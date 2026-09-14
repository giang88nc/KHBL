import datetime as dt
import json
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from django.contrib.auth import get_user_model
from django.core import signing
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from apps.pos import deposits as D, deposit_operations as O, deposit_messages as M, deposit_money as F
from apps.pos.deposit_models import DepositMessage, DepositMessageTemplate, DepositOrderState, DepositStockHold, DepositEvent, DepositMoneyOperation


class DepositOperationsTests(TestCase):
    def setUp(self):
        self.user=get_user_model().objects.create_superuser('dc-ops',password='test')
        self.client.force_login(self.user)
        self.c=MagicMock(target='sandbox'); self.c.query.return_value=[]
        self.h={'TrnID':'TRC1','BillCode':'DC1','Status':'W','CustID':'C1','CustName':'Khách','Phone':'0901234567',
                'TrnDateTime_Upd':dt.datetime(2026,9,11,10),'TrnDate':dt.date(2026,9,11),'Description':'HEN:2026-09-12 | thử | 0',
                'TienCoc':Decimal(1000),'CashPay':0,'CardPay':0,'ShopID':'','EmpID':'','EmpName':''}
        self.items=[{'ProductDesc':'Nhẫn','GoldCode':'18K','SL':2,'Size':'12','Notes':'SP:A1 | thử'}]
        for p in [patch.object(O,'PmvClient',return_value=self.c),patch.object(M,'PmvClient',return_value=self.c),patch.object(F,'PmvClient',return_value=self.c),
                  patch('apps.pmv.gateway.pmv_deposit_zalo'),
                  patch.object(D,'header',side_effect=lambda *a:dict(self.h)),patch.object(D,'lines',side_effect=lambda *a:[dict(i) for i in self.items]),
                  patch('apps.pos.context_processors.S.thong_tin_tiem',return_value={}),patch('apps.pos.context_processors.gateway.mo_ta_dich',return_value='Bản thử')]:
            p.start(); self.addCleanup(p.stop)

    def op(self,kind,data):
        url=reverse('pos:dat_coc_operation',args=['TRC1',kind])
        get=self.client.get(url)
        self.assertEqual(get.status_code,200)
        return self.client.post(url,{'token':get.context['token'],**data})

    def test_progress_rejects_delivered_more_than_ready(self):
        response=self.op('progress',{'ready_0':1,'delivered_0':2})
        self.assertContains(response,'Số đã giao không được lớn hơn')
        self.assertFalse(DepositOrderState.objects.exists())

    def test_partial_then_delivered_preserves_actual_time_and_releases_hold(self):
        DepositStockHold.objects.create(target='sandbox',trn_id='TRC1',product_code='A1',active_key='sandbox:a1',location='Khay 1',username='test')
        self.op('progress',{'ready_0':2,'delivered_0':1})
        self.assertEqual(DepositOrderState.objects.get().fulfilment,'partial')
        self.assertTrue(DepositStockHold.objects.filter(active_key__isnull=False).exists())
        self.op('progress',{'ready_0':2,'delivered_0':2})
        state=DepositOrderState.objects.get()
        self.assertEqual(state.fulfilment,'delivered'); self.assertIsNotNone(state.delivered_at)
        self.assertFalse(DepositStockHold.objects.filter(active_key__isnull=False).exists())
        self.assertEqual(DepositEvent.objects.count(),2)

    def test_stale_local_version_rejects_contact(self):
        url=reverse('pos:dat_coc_operation',args=['TRC1','contact'])
        get=self.client.get(url)
        state=O.base_state(self.c,self.h,[D.O.item_info(i) for i in self.items]); state.version=1; state.save()
        response=self.client.post(url,{'token':get.context['token'],'result':'no_answer','note':'Gọi lại','employee':''})
        self.assertContains(response,'vừa thay đổi')
        self.assertFalse(DepositEvent.objects.exists())

    def test_hold_is_target_scoped_and_cannot_duplicate(self):
        DepositStockHold.objects.create(target='sandbox',trn_id='TRC1',product_code='A1',active_key='sandbox:a1',location='Khay',username='test')
        O.check_holds(['a1'],'kk')
        with self.assertRaisesRegex(ValueError,'TRC1'): O.check_holds(['A1'],'sandbox')
        with self.assertRaises(IntegrityError),transaction.atomic():
            DepositStockHold.objects.create(target='sandbox',trn_id='TRC2',product_code='A1',active_key='sandbox:a1',location='Khay',username='test')

    def test_cancel_stops_reminders_and_preserves_money(self):
        response=self.op('cancel',{'reason':'Khách đổi kế hoạch','confirm':'on'})
        self.assertIn('HX-Trigger',response)
        self.assertEqual(DepositOrderState.objects.get().fulfilment,'cancelled')
        self.c.call.assert_not_called()

    def test_cancel_after_completion_requires_reason_and_records_policy(self):
        self.op('progress',{'ready_0':2,'delivered_0':2})
        response=self.op('cancel',{'reason':'','policy':'refund','confirm':'on'})
        self.assertNotIn('HX-Trigger',response)
        response=self.op('cancel',{'reason':'Khách hủy đặt','policy':'keep','confirm':'on'})
        self.assertIn('HX-Trigger',response)
        state=DepositOrderState.objects.get();self.assertEqual(state.cancellation_policy,'keep')
        self.assertEqual(state.extra_notes[-1]['text'],'Khách hủy đặt')
        self.h['Status']='D'
        self.op('progress',{'ready_0':2,'delivered_0':0})
        rows=D.W.prepare({'headers':[self.h],'items':{'TRC1':[D.O.item_info(i) for i in self.items]}})
        row=O.overlay(rows,'sandbox')[0]
        self.assertEqual(row['fulfilment'],'ready')
        self.c.call.assert_not_called()

    def test_changed_items_do_not_reuse_old_progress(self):
        self.op('progress',{'ready_0':2,'delivered_0':1})
        items=[D.O.item_info({**self.items[0],'ProductDesc':'Dây mới'})]
        rows=D.W.prepare({'headers':[self.h],'items':{'TRC1':items}})
        row=O.overlay(rows,'sandbox')[0]
        self.assertTrue(row['items_changed']); self.assertTrue(row['hold_conflict'])
        self.assertNotEqual(row['fulfilment'],'partial')

    def test_external_closed_order_cannot_resume_reminders_from_old_local_state(self):
        self.op('progress',{'ready_0':2,'delivered_0':1})
        self.h['Status']='C'
        rows=D.W.prepare({'headers':[self.h],'items':{'TRC1':[D.O.item_info(i) for i in self.items]}})
        row=D.W.classify(O.overlay(rows,'sandbox'))[0]
        self.assertEqual(row['fulfilment'],'delivered'); self.assertFalse(row['active'])

    def template(self,status='draft'):
        return DepositMessageTemplate.objects.create(name='Báo hẹn',body='{customer}: {bill}, hẹn {promise}.',provider_id='1',status=status)

    def message(self,**extra):
        params={'customer':'Khách','bill':'DC1','promise':'12/09/2026','deposit':'1,000','store':'Tiệm'}
        return DepositMessage.objects.create(target='sandbox',trn_id='TRC1',customer_name='Khách',phone='84901234567',template=self.template(),
            template_version=1,body='Nội dung',parameters=params,planned_day=timezone.localdate()+dt.timedelta(days=1),username=self.user.username,**extra)

    def test_notification_page_renders_day_filter_and_draft_templates(self):
        response=self.client.get(reverse('pos:dat_coc'),{'tab':'notifications'},HTTP_HX_REQUEST='true')
        self.assertContains(response,'THÔNG BÁO'); self.assertContains(response,'Chưa kết nối OA')
        self.assertContains(response,'dc-message-filter')

    def test_compose_deduplicates_per_order_day_template_and_waits_for_approval(self):
        self.h['Status']='R'; t=self.template()
        snapshot={'headers':[self.h],'items':{},'as_of':timezone.now()}
        url=reverse('pos:dat_coc_compose')
        with patch.object(D.W,'read_snapshot',return_value=snapshot):
            token=self.client.get(url).context['token']
            data={'token':token,'day':timezone.localdate().isoformat(),'template':t.pk,'orders':['TRC1']}
            preview=self.client.post(url,data)
            self.assertEqual(DepositMessage.objects.count(),0)
            data['confirmation']=preview.context['confirmation']
            for _ in range(2): self.client.post(url,data)
        self.assertEqual(DepositMessage.objects.count(),1)
        self.assertEqual(DepositMessage.objects.get().status,'waiting_template')

    def test_unapproved_template_schedule_stays_waiting(self):
        m=self.message(); url=reverse('pos:dat_coc_message',args=[m.pk,'schedule'])
        token=self.client.get(url).context['token']
        response=self.client.post(url,{'token':token,'phone':m.phone,'planned_day':m.planned_day.isoformat(),
            'scheduled_at':m.planned_day.isoformat()+'T10:00',**m.parameters})
        self.assertIn('HX-Trigger',response)
        m.refresh_from_db(); self.assertEqual(m.status,'waiting_template'); self.assertIsNotNone(m.scheduled_at)

    def test_bulk_schedule_previews_both_receipts_then_waits_for_oa(self):
        self.h['Status']='R'; template=self.template()
        second={**self.h,'TrnID':'TRC2','CustName':'Khách hai'}
        snapshot={'headers':[self.h,second],'items':{},'as_of':timezone.now()}
        day=timezone.localdate()+dt.timedelta(days=1)
        url=reverse('pos:dat_coc_compose')
        with patch.object(D.W,'read_snapshot',return_value=snapshot),patch.object(M,'oa_request') as api:
            token=self.client.get(url,{'orders':['TRC1','TRC2'],'day':str(day)}).context['token']
            data={'token':token,'orders':['TRC1','TRC2'],'day':str(day),'template':template.pk,'scheduled_at':str(day)+'T10:00'}
            preview=self.client.post(url,data)
            self.assertEqual(len(preview.context['previews']),2)
            self.assertEqual(DepositMessage.objects.count(),0)
            data['confirmation']=preview.context['confirmation']
            response=self.client.post(url,data)
            self.assertIn('HX-Trigger',response)
            self.assertEqual(DepositMessage.objects.filter(status='waiting_template',scheduled_at__isnull=False).count(),2)
            api.assert_not_called()

    def test_bulk_preview_invalidated_by_changed_phone_and_closed_order(self):
        self.h['Status']='R'; template=self.template()
        snapshot={'headers':[self.h],'items':{},'as_of':timezone.now()};url=reverse('pos:dat_coc_compose')
        with patch.object(D.W,'read_snapshot',side_effect=lambda *a,**kw:snapshot):
            data={'token':self.client.get(url).context['token'],'orders':['TRC1'],'day':str(timezone.localdate()),'template':template.pk}
            data['confirmation']=self.client.post(url,data).context['confirmation']
            self.h['Phone']='0907654321'
            changed=self.client.post(url,data)
            self.assertEqual(DepositMessage.objects.count(),0)
            self.assertIn('confirmation',changed.context)
            self.h['Status']='D'
            response=self.client.post(url,data)
            self.assertTrue(response.context['form'].errors)
            self.assertEqual(DepositMessage.objects.count(),0)

    def test_suggestion_remove_and_add_change_only_reminder_journal(self):
        self.h['Status']='R';snapshot={'headers':[self.h],'items':{},'as_of':timezone.now()}
        with patch.object(D.W,'read_snapshot',return_value=snapshot):
            for kind in ('remove','add'):
                url=reverse('pos:dat_coc_suggestion',args=[kind])
                token=self.client.get(url,{'receipt':'TRC1','day':str(timezone.localdate())}).context['token']
                result=self.client.post(url,{'token':token,'orders':['TRC1'],'day':str(timezone.localdate())})
                self.assertIn('HX-Trigger',result)
        self.assertEqual(DepositEvent.objects.filter(action__startswith='reminder_').count(),2)
        self.c.call.assert_not_called()
        self.assertFalse(DepositMessage.objects.exists())

    def test_delete_draft_keeps_audit_and_releases_dedupe(self):
        m=self.message(dedupe_key='test'); url=reverse('pos:dat_coc_message',args=[m.pk,'delete'])
        token=self.client.get(url).context['token']; self.client.post(url,{'token':token})
        m.refresh_from_db(); self.assertEqual(m.status,'cancelled'); self.assertIsNone(m.dedupe_key)
        self.assertEqual(DepositEvent.objects.get().action,'message_delete')

    def test_sent_message_is_immutable(self):
        m=self.message(status='sent'); url=reverse('pos:dat_coc_message',args=[m.pk,'delete'])
        token=self.client.get(url).context['token']; response=self.client.post(url,{'token':token})
        self.assertContains(response,'Không sửa/xóa')
        m.refresh_from_db(); self.assertEqual(m.status,'sent')

    def test_template_rejects_code_fields_and_bad_mapping(self):
        for body in ['{customer.__class__}','{customer!r}','{customer:>100}']:
            form=M.TemplateForm({'name':'Mẫu','body':body,'parameter_map':'{}'})
            self.assertFalse(form.is_valid())
        form=M.TemplateForm({'name':'Mẫu','body':'{customer}','parameter_map':'{"name":[]}'})
        self.assertFalse(form.is_valid())

    def test_phone_normalization_and_validation(self):
        self.assertEqual(M.phone_number('+84 901 234 567'),'84901234567')
        with self.assertRaises(ValueError): M.phone_number('0281234')

    @override_settings(DATCOC_OA_ACCESS_TOKEN='')
    def test_missing_oa_connection_never_sends(self):
        self.message(status='scheduled',scheduled_at=timezone.now()-dt.timedelta(minutes=1))
        with patch.object(M,'oa_request') as api:
            self.assertEqual(M.send_due('kk'),0); api.assert_not_called()

    def test_stale_inflight_message_becomes_uncertain_not_retried(self):
        m=self.message(status='sending',claimed_at=timezone.now()-dt.timedelta(minutes=6))
        with patch.object(M,'oa_request') as api: M.send_due('sandbox'); api.assert_not_called()
        m.refresh_from_db(); self.assertEqual(m.status,'uncertain')

    @override_settings(DATCOC_OA_ACCESS_TOKEN='test-only-token')
    def test_due_approved_message_sent_once_and_network_timeout_is_not_retried(self):
        now=timezone.make_aware(dt.datetime(2026,9,11,10))
        self.h['Status']='R'
        for outcome,expected in [({'error':0,'data':{'msg_id':'provider-1'}},'sent'),(TimeoutError(),'uncertain')]:
            m=self.message(status='scheduled',scheduled_at=now-dt.timedelta(minutes=1))
            m.target='kk'; m.save()
            t=m.template; t.status='approved'; t.approved_body_hash=M.digest(t.body); t.save()
            with patch.object(M.timezone,'now',return_value=now),patch.object(M,'provider_info',return_value={'listParams':[]}),patch.object(M,'oa_request') as api:
                if isinstance(outcome,Exception): api.side_effect=outcome
                else: api.return_value=outcome
                M.send_due('kk'); M.send_due('kk')
                self.assertEqual(api.call_count,1)
            m.refresh_from_db(); self.assertEqual(m.status,expected)

    def test_money_legacy_and_partial_are_not_silently_posted(self):
        f={'h':self.h,'amount':Decimal(1000),'cash':0,'bank':0,'tx':[],'links':[],'changes':[]}
        op=SimpleNamespace(kind='receive',amount=Decimal(1000),cash=Decimal(1000),bank=0)
        with self.assertRaisesRegex(ValueError,'appMobile'): F.validate_operation(op,f)
        op.amount=500
        with self.assertRaisesRegex(ValueError,'bằng tiền cọc'): F.validate_operation(op,f)

    def test_bank_match_needs_exact_code_amount_and_direction(self):
        op=SimpleNamespace(kind='receive',bank=Decimal(1000))
        row={'direction':'in','trans_amount':1000,'description':'Cọc TDC001 cho nhẫn'}
        self.assertTrue(F.matches_bank(row,op,['TDC001']))
        self.assertFalse(F.matches_bank(row,op,['TDC00']))
        self.assertFalse(F.matches_bank({**row,'direction':'out'},op,['TDC001']))
        self.assertFalse(F.matches_bank({**row,'trans_amount':999},op,['TDC001']))

    def test_cash_columns_without_posted_till_are_not_confirmation(self):
        self.h.update(CashPay=1000,Status='P',ShopID='S1')
        self.assertFalse(F.financial(self.c,'TRC1')['confirmed'])

    def test_null_till_detail_is_rejected(self):
        self.h.update(CashPay=1000,Status='P',ShopID='S1')
        self.c.query.side_effect=[[{'TillTxnID':'TX','TillID':'T1','Status':'P','TrnTotalAmount':1000}],[],[],[{'Amount':None,'CrDr':'+','GoldCcy':'VND'}]]
        self.assertFalse(F.financial(self.c,'TRC1')['valid'])

    def test_unique_active_money_operation(self):
        base={'target':'sandbox','trn_id':'TRC1','kind':'receive','amount':1000,'cash':1000,'user_id':'U','till_id':'T','username':'test','active_key':'sandbox:TRC1'}
        DepositMoneyOperation.objects.create(**base,token='one')
        with self.assertRaises(IntegrityError),transaction.atomic(): DepositMoneyOperation.objects.create(**base,token='two')

    def test_partial_proc_failure_retains_lock_and_never_retries_money(self):
        self.h.update(ShopID='S1')
        f={'h':self.h,'amount':Decimal(1000),'cash':Decimal(0),'bank':Decimal(0),'tx':[],'links':[],'changes':[]}
        op=DepositMoneyOperation.objects.create(target='sandbox',trn_id='TRC1',kind='receive',amount=1000,cash=1000,
            user_id='U',till_id='T',username=self.user.username,active_key='sandbox:TRC1',token='execute-test',evidence={'stamp':F.money_stamp(self.c,self.h)})
        self.c.query.side_effect=[[{'TillTxnID':'TX','Status':'U','TrnTotalAmount':1000}],
                                 [{'Amount':None,'CrDr':'+','GoldCcy':'VND'}]]
        with patch.object(F,'financial',return_value=f):
            F.execute(op)
        op.refresh_from_db(); self.assertEqual(op.status,'uncertain'); self.assertIsNotNone(op.active_key)
        called=[call.args[0] for call in self.c.call.call_args_list]
        self.assertEqual(called,['TRN_DATCOC_Complete'])
        self.c.update_deposit_money.assert_called_once()
        F.execute(op)
        self.assertEqual(self.c.call.call_count,1)
        self.c.update_deposit_money.assert_called_once()

    def test_successful_receive_is_posted_once_and_audited(self):
        self.h.update(ShopID='S1')
        before={'h':self.h,'amount':Decimal(1000),'cash':Decimal(0),'bank':Decimal(0),'tx':[],'links':[],'changes':[]}
        after={**before,'h':{**self.h,'Status':'P','CashPay':1000},'valid':True,'cash':1000,'tx':[{'TillID':'T'}]}
        op=DepositMoneyOperation.objects.create(target='sandbox',trn_id='TRC1',kind='receive',amount=1000,cash=1000,
            user_id='U',till_id='T',username=self.user.username,active_key='sandbox:TRC1',token='execute-ok',evidence={'stamp':F.money_stamp(self.c,self.h)})
        self.c.query.side_effect=[[{'TillTxnID':'TX','Status':'U','TrnTotalAmount':1000}],
                                 [{'Amount':1000,'CrDr':'+','GoldCcy':'VND'}]]
        with patch.object(F,'financial',side_effect=[before,before,after]): F.execute(op)
        op.refresh_from_db(); self.assertEqual(op.status,'done'); self.assertIsNone(op.active_key)
        self.assertEqual(DepositEvent.objects.get().action,'money_receive')
        F.execute(op); self.assertEqual(self.c.call.call_count,2)
        self.c.update_deposit_money.assert_called_once()

    def test_money_stamp_detects_status_change_without_updated_timestamp(self):
        self.assertNotEqual(F.money_stamp(self.c,self.h),F.money_stamp(self.c,{**self.h,'Status':'P'}))
        self.assertEqual(F.money_stamp(self.c,self.h),F.money_stamp(self.c,F.safe_data(self.h)))

    def test_new_routes_require_deposit_permission(self):
        self.client.force_login(get_user_model().objects.create_user('dc-no-right',password='test'))
        for url in [reverse('pos:dat_coc_compose'),reverse('pos:dat_coc_money',args=['TRC1','receive']),
                    reverse('pos:dat_coc_apply',args=['TRC1']),reverse('pos:dat_coc_operation',args=['TRC1','progress'])]:
            self.assertEqual(self.client.get(url).status_code,403)
