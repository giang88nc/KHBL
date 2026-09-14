import datetime as dt
import json
from decimal import Decimal as Q
from types import SimpleNamespace
from unittest.mock import patch
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from apps.pos import deposits as D, deposit_editing as X, deposit_editor as E, deposit_operations as OP
from apps.pos.deposit_models import DepositOrderState, DepositEvent, DepositStockHold, DepositMoneyOperation
from tests import test_deposits


class RestrictedEditorTests(TestCase):
    def setUp(self):
        test_deposits.DepositViewTests.setUp(self)
        self.h.update(ShopID='S1',CashPay=Q(0),CardPay=Q(0),Description=D.O.pack_description(dt.date(2026,9,20),[{'date':'2026-09-10','text':'Ghi chú đã lưu'}],Q('8000000')))
        self.items=[dict(ProductDesc='Nhẫn',ProductCode='',GoldCode='18K',SL=1,TotalWeight=Q('80.012345'),GoldWeight=Q('80.012345'),DiamondWeight=Q(0),TaskPrice=Q('100000'),Size='12',Notes='MA_DAT:Khách đặt | ')]
        self.c.query.side_effect=lambda sql,*args: [{'EmpID':'NV1','EmpName':'NV 1'},{'EmpID':'NV2','EmpName':'NV 2'}] if 'T_EMPLOYEE' in sql else [{'GoldCode':'18K','WeightUnit':'L','PriceUnit':'L'}] if 'I_GOLD' in sql else [{'factor':1}] if 'fun_GetHS' in sql else [{'GoldCcy':'18K','SellRate':8000}] if 'I_XRATE' in sql else []
        for p in [patch.object(D,'header',side_effect=lambda *a:dict(self.h)),patch.object(D,'lines',side_effect=lambda *a:[dict(i) for i in self.items]),
                  patch('apps.pmv.client.PmvClient',return_value=self.c),patch('apps.pmv.models.pmv_user_for_web_user',return_value=SimpleNamespace(user_id='U1',shop_id='S1'))]:
            p.start();self.addCleanup(p.stop)
        self.url=reverse('pos:dat_coc_popup',args=['TDC001','edit'])

    def payload(self):
        response=self.client.get(self.url)
        self.assertEqual(response.status_code,200)
        self.assertIsNotNone(response.context['form'],response.content.decode())
        return {'token':response.context['token'],'editor_version':'2','description_format':'json','row_version':'3','money_format':'vi',
                'EmpID':'NV1','PromiseDate':'2026-09-20','NoteEntries':json.dumps(response.context['form'].initial['NoteEntries']),
                'Progress':response.context['form'].initial['Progress']},response

    def test_print_preview_uses_receipt_data_and_requires_explicit_print(self):
        url=reverse('pos:dat_coc_popup',args=['TDC001','view'])
        normal=self.client.get(url)
        self.assertContains(normal,'?print=1')
        response=self.client.get(url,{'print':'1'})
        self.assertTemplateUsed(response,'pos/dat_coc_print.html')
        self.assertContains(response,'Xem trước · Giấy đảm bảo')
        self.assertContains(response,'PHIẾU ĐẶT HÀNG')
        self.assertContains(response,'TDC001')
        self.assertNotContains(response,'Ghi chú đã lưu')
        self.assertContains(response,'Vui lòng giữ phiếu để đối chiếu khi nhận hàng.')
        self.assertContains(response,'data:image/svg+xml')
        self.assertContains(response,'0,800')
        self.assertNotContains(response,'khbl-modal')
        self.assertNotContains(response,'onload=')
        self.assertEqual(response['Cache-Control'],'private, no-store')
        self.assertEqual(response['X-Frame-Options'],'SAMEORIGIN')
        self.assertContains(response,'data-store-copy',count=2)
        self.assertContains(response,'Nền giấy mẫu')
        self.assertContains(response,'dat_coc_print.js')
        self.assertContains(response,'size:auto;margin:0')
        self.c.call.assert_not_called()

    def test_print_json_product_labels_differ_for_customer_and_store(self):
        products={'2C600638':'Cara nam hxanh rồng','Khách đặt':'cặp nhẫn như mẫu + đặc lòng'}
        self.items=[dict(self.items[0],ProductDesc=json.dumps({code:name},ensure_ascii=False),Notes='')
                    for code,name in products.items()]
        response=self.client.get(reverse('pos:dat_coc_popup',args=['TDC001','view']),{'print':'1'})
        self.assertEqual(response.status_code,200)
        customer,store=response.content.decode().split('<section class="store-copies"',1)
        for code,name in products.items():
            self.assertIn(f'<span>[{code}]</span> <b>{name}</b>',customer)
            self.assertEqual(store.count(name),2)
            self.assertNotIn(code,store)
        self.c.call.assert_not_called()

    def test_direct_edit_requires_promise_and_active_employee(self):
        for changes in ({'PromiseDate':''},{'EmpID':'INACTIVE'}):
            data,_=self.payload(); data.update(changes)
            with patch.object(D,'save') as save:
                response=self.client.post(self.url,data)
                self.assertNotIn('depositSaved',response.headers.get('HX-Trigger',''))
                self.assertTrue(response.context['form'].errors)
                save.assert_not_called()
        self.c.call.assert_not_called()

    def test_applied_receipt_can_edit_metadata_and_has_stamp(self):
        from apps.pos import deposit_money as F
        self.h.update(Status='C',CashPay=self.h['TienCoc'])
        state=DepositOrderState.objects.create(target='sandbox',trn_id='TDC001',fulfilment='applied',
            promise=dt.date(2026,9,20),employee_id='NV1',employee_name='NV 1')
        fund={'h':self.h,'applied':True,'valid':True,'links':[{'TrnID':'INV','Status':'C'}],
              'changes':[],'refunded':False}
        with patch.object(F,'financial',return_value=fund):
            data,opened=self.payload()
            self.assertContains(opened,'ĐÃ ÁP DỤNG')
            self.assertNotContains(opened,'dc-document-identity')
            self.assertNotContains(opened,'dc-edit-access')
            data.update(NoteText='Khách hẹn nhận thêm giấy',EmpID='NV2')
            response=self.client.post(self.url,data)
            self.assertIn('depositSaved',response.headers.get('HX-Trigger',''),response.content.decode())
            state.refresh_from_db();self.assertEqual(state.fulfilment,'applied');self.assertEqual(state.employee_id,'NV2')
            view=self.client.get(reverse('pos:dat_coc_popup',args=['TDC001','view']))
            self.assertContains(view,'dc-view-table');self.assertContains(view,'ĐÃ ÁP DỤNG')
            self.assertContains(view,'Sửa thông tin');self.assertNotContains(view,'Cấn cọc vào hóa đơn')
        self.c.call.assert_not_called()

    def test_w_direct_edit_preserves_every_product_digit_and_money(self):
        data,_=self.payload();data.update(NoteText='Khách hẹn lại',PromiseDate='2026-09-30',EmpID='NV2',Progress='crafting')
        with patch.object(D,'save',return_value='TDC001') as save:
            response=self.client.post(self.url,data)
        self.assertIn('depositSaved',response.headers.get('HX-Trigger',''),response.content.decode())
        save.assert_not_called();self.c.call.assert_not_called()
        state=DepositOrderState.objects.get();self.assertEqual((state.fulfilment,state.employee_id),('crafting','NV2'))
        self.assertEqual(state.extra_notes,[{'date':timezone.localdate().isoformat(),'text':'Khách hẹn lại'}])

    def test_paid_direct_edit_is_local_and_appears_on_reopen_and_view(self):
        self.h.update(Status='P',CashPay=self.h['TienCoc'])
        data,opened=self.payload();self.assertTrue(opened.context['edit_locked']);self.assertFalse(opened.context['can_unlock'])
        data.update(NoteText='Thợ nhận làm',Progress='crafting',EmpID='NV2')
        response=self.client.post(self.url,data)
        self.assertIn('depositSaved',response.headers.get('HX-Trigger',''),response.content.decode())
        self.c.call.assert_not_called()
        state=DepositOrderState.objects.get();self.assertEqual(state.extra_notes[-1]['text'],'Thợ nhận làm')
        _,reopened=self.payload();self.assertEqual(len(reopened.context['form'].initial['NoteEntries']),2)
        view=self.client.get(reverse('pos:dat_coc_popup',args=['TDC001','view']));self.assertContains(view,'Thợ nhận làm')

    def test_old_notes_cannot_be_removed_or_modified(self):
        for notes in ([],[{'date':'2026-09-10','text':'Đã sửa'}]):
            data,_=self.payload();data['NoteEntries']=json.dumps(notes)
            response=self.client.post(self.url,data)
            self.assertContains(response,'Không được xóa hoặc sửa ghi chú đã lưu')
        self.c.call.assert_not_called();self.assertFalse(DepositOrderState.objects.exists())

    def test_protected_payload_requires_passcode(self):
        for changes in ({'CustID':'OTHER'},{'CashDeposit':'100'},{'items-0-GoldWeight':'5'}):
            data,_=self.payload();data.update(changes)
            response=self.client.post(self.url,data)
            self.assertContains(response,'cần xác nhận Passcode')
        self.c.call.assert_not_called()

    def test_invalid_progress_and_stale_local_version_are_rejected(self):
        data,_=self.payload();data['Progress']='fake'
        self.assertContains(self.client.post(self.url,data),'Vui lòng kiểm tra')
        data,_=self.payload();DepositOrderState.objects.create(target='sandbox',trn_id='TDC001',version=1)
        self.assertContains(self.client.post(self.url,data),'Phiếu vừa thay đổi')
        self.c.call.assert_not_called()

    def test_passcode_grant_bound_to_form_and_denied_for_paid_order(self):
        data,_=self.payload();url=reverse('pos:dat_coc_unlock',args=['TDC001'])
        with patch('apps.pos.views._passcode_dung',return_value=False):
            result=self.client.post(url,{'token':data['token'],'passcode':'wrong'})
            self.assertEqual(result.status_code,400)
        with patch('apps.pos.views._passcode_dung',return_value=True):
            result=self.client.post(url,{'token':data['token'],'passcode':'correct'})
            self.assertEqual(result.status_code,200,result.content)
        fresh,_=self.payload();fresh['edit_grant']=result.json()['grant']
        self.assertContains(self.client.post(self.url,fresh),'không thuộc phiếu này')
        self.h['Status']='P';data,_=self.payload()
        self.assertEqual(self.client.post(url,{'token':data['token'],'passcode':'correct'}).status_code,400)

    def test_cancel_and_completion_update_holds_without_moving_money(self):
        self.h.update(Status='P',CashPay=self.h['TienCoc'])
        for progress in ('cancel_refund','delivered'):
            DepositOrderState.objects.all().delete();DepositEvent.objects.all()._raw_delete('default')
            hold=DepositStockHold.objects.create(target='sandbox',trn_id='TDC001',product_code='SP1',active_key='sandbox:sp1',location='Khay',username='test')
            data,_=self.payload();data.update(Progress=progress,NoteText='Khách đổi kế hoạch')
            result=self.client.post(self.url,data)
            self.assertIn('depositSaved',result.headers.get('HX-Trigger',''),result.content.decode())
            state=DepositOrderState.objects.get();hold.refresh_from_db();self.assertIsNone(hold.active_key)
            if progress=='cancel_refund':
                self.assertEqual(state.cancellation_policy,'refund');self.assertNotIn('open_url',result.headers['HX-Trigger'])
            else: self.assertEqual(state.items[0]['delivered'],0)
        self.c.call.assert_not_called()

    def test_precision_display_keeps_source_when_other_fields_change(self):
        self.assertEqual(D.WeightInput().format_value(Q('0.80000000')),'0,800')
        old=[{'Mode':'new','GoldCode':'18K','GoldWeight':Q('.80012345')}]
        data={'items-0-Mode':'new','items-0-GoldCode':'18K','items-0-GoldWeight':'0,800'}
        self.assertEqual(E.keep_weight_precision(data,old)['items-0-GoldWeight'],'0.80012345')
        data['items-0-GoldWeight']='0,900'
        self.assertEqual(E.keep_weight_precision(data,old)['items-0-GoldWeight'],'0,900')

    def test_cancel_policy_change_requires_new_reason_and_can_reopen(self):
        self.h.update(Status='P',CashPay=self.h['TienCoc'])
        for progress in ('cancel_refund','cancel_keep'):
            data,_=self.payload();data['Progress']=progress
            self.assertContains(self.client.post(self.url,data),'phải có lý do trong ghi chú mới')
            data['NoteText']='Lý do mới '+progress
            result=self.client.post(self.url,data)
            self.assertIn('depositSaved',result.headers.get('HX-Trigger',''))
            self.assertNotIn('open_url',result.headers['HX-Trigger'])
        state=DepositOrderState.objects.get()
        self.assertEqual(state.cancellation_policy,'keep')
        self.assertEqual([n['text'] for n in state.extra_notes],['Lý do mới cancel_refund','Lý do mới cancel_keep'])
        view=self.client.get(reverse('pos:dat_coc_popup',args=['TDC001','view']))
        self.assertContains(view,'Đã hủy - không hoàn cọc');self.assertNotContains(view,'chờ hoàn cọc')
        for progress in ('ready','delivered','crafting','ready'):
            data,_=self.payload();data['Progress']=progress
            response=self.client.post(self.url,data)
            self.assertIn('depositSaved',response.headers.get('HX-Trigger',''),response.content.decode())
            state.refresh_from_db();self.assertEqual(state.fulfilment,progress)
            self.assertEqual(state.cancellation_policy,'')
        self.assertFalse(DepositMoneyOperation.objects.exists())
        self.c.call.assert_not_called()

    def test_status_change_ignores_pending_banking_and_does_not_collect_again(self):
        from apps.pos.deposit_models import DepositMoneyOperation
        self.h.update(Status='P',CashPay=self.h['TienCoc'])
        operation=DepositMoneyOperation.objects.create(target='sandbox',trn_id='TDC001',kind='bank_reconcile',
            status='uncertain',amount=self.h['TienCoc'],active_key='sandbox:TDC001',token='pending-bank')
        data,_=self.payload();data.update(Progress='cancel_keep',NoteText='Khách bỏ cọc')
        response=self.client.post(self.url,data)
        self.assertIn('depositSaved',response.headers.get('HX-Trigger',''),response.content.decode())
        operation.refresh_from_db();self.assertEqual(operation.status,'uncertain')
        self.assertEqual(DepositMoneyOperation.objects.count(),1)
        self.c.call.assert_not_called()

    def test_passcode_allows_product_edit_but_never_removes_saved_notes(self):
        data,opened=self.payload()
        with patch('apps.pos.views._passcode_dung',return_value=True):
            grant=self.client.post(reverse('pos:dat_coc_unlock',args=['TDC001']),{'token':data['token'],'passcode':'correct'}).json()['grant']
        data.update(edit_grant=grant,pricing_token=opened.context['pricing_token'],CustID='KH001',CashDeposit='1.000.000',BankDeposit='0',
                    **{'items-TOTAL_FORMS':'1','items-INITIAL_FORMS':'1','items-0-Mode':'new','items-0-ProductCode':'Khách đặt',
                       'items-0-ProductDesc':'Nhẫn sửa size','items-0-GoldCode':'18K','items-0-GoldWeight':'0,800',
                       'items-0-DiamondWeight':'0','items-0-TotalWeight':'0.80012345','items-0-SL':'1',
                       'items-0-TaskPrice':'100.000','items-0-Size':'13','items-0-DisplayUnits':'True'})
        with patch.object(D,'pmv_user_for_web_user',return_value=SimpleNamespace(user_id='U1',shop_id='S1')),patch.object(D,'save',return_value='TDC001') as save:
            data['NoteEntries']='[]'
            self.assertContains(self.client.post(self.url,data),'Không được xóa hoặc sửa ghi chú đã lưu')
            save.assert_not_called()
            data['NoteEntries']=json.dumps(D.O.note_entries(self.h['Description']))
            result=self.client.post(self.url,data)
        self.assertIn('depositSaved',result.headers.get('HX-Trigger',''),result.content.decode())
        row=save.call_args.args[2][0]
        self.assertEqual(row['Size'],'13');self.assertEqual(row['GoldWeight'],Q('80.012345'))
        self.assertTrue(DepositEvent.objects.get().data['passcode_confirmed'])
