from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import TestCase
from django.test import RequestFactory
from django.http import HttpResponse

from apps.pos import deposit_application as A, deposit_money as F, ban_coc as BC, bill as B
from apps.pos.deposit_models import DepositMoneyOperation as Operation, DepositOrderState


class DepositApplicationTests(TestCase):
    def setUp(self):
        self.c=MagicMock(target='sandbox')
        self.f={'h':{'TrnID':'D1','CustID':'C1','Status':'P','TienCoc':1000,'CashPay':1000,'CardPay':0},
                'valid':True,'amount':Decimal(1000),'cash':Decimal(1000),'bank':Decimal(0),
                'tx':[{'Status':'P'}],'links':[],'changes':[]}
        self.user=SimpleNamespace(pk=None,user_id='U1',till_id='T1',shop_id='S1',username='test')

    def test_unpaid_and_legacy_cannot_bypass_pmv_status(self):
        for money in (0,1000):
            f={**self.f,'h':{**self.f['h'],'Status':'W'},'valid':False,'cash':Decimal(money),'tx':[]}
            self.assertTrue(A.reason(f,'C1'))
        self.assertEqual(A.reason(self.f,'C1'),'')
        self.assertTrue(A.reason({**self.f,'h':{**self.f['h'],'Status':'R'}},'C1'))

    def test_ownership_other_invoice_change_and_duplicate_links_rejected(self):
        self.assertTrue(A.reason(self.f,'C2'))
        self.assertTrue(A.reason({**self.f,'changes':[{'TrnID':'CHANGE'}]},'C1'))
        self.assertTrue(A.reason({**self.f,'links':[{'TrnID':'OTHER'}]},'C1','INV'))
        self.assertTrue(A.reason({**self.f,'links':[{'TrnID':'INV'}]*2},'C1','INV'))

    def test_resume_completed_invoice_accepts_c_only_for_same_invoice(self):
        f={**self.f,'h':{**self.f['h'],'Status':'C'},'links':[{'TrnID':'INV'}]}
        self.assertEqual(A.reason(f,'C1','INV','C'),'')
        self.assertTrue(A.reason(f,'C1','INV','W'))
        self.assertTrue(A.reason(f,'C1','NEW','C'))

    def test_cancelled_and_pending_refund_rejected(self):
        with patch.object(F,'financial',return_value=self.f):
            DepositOrderState.objects.create(target='sandbox',trn_id='D1',fulfilment='cancelled')
            with self.assertRaisesMessage(ValueError,'hủy'): A.check(self.c,['D1'],'C1')
            DepositOrderState.objects.all().delete()
            Operation.objects.create(target='sandbox',trn_id='D1',kind='refund',status='uncertain',amount=1000,token='refund')
            with self.assertRaisesMessage(ValueError,'chưa xác định'): A.check(self.c,['D1'],'C1')

    def test_sum_and_duplicate_selection_checked_before_write(self):
        DepositOrderState.objects.create(target='sandbox',trn_id='D1',fulfilment='ready')
        with patch.object(F,'financial',return_value=self.f):
            with self.assertRaisesMessage(ValueError,'khớp'): A.check(self.c,['D1'],'C1',amount=2000)
            with self.assertRaisesMessage(ValueError,'trùng'): A.check(self.c,['D1','D1'],'C1',amount=2000)
        self.c.call.assert_not_called()

    def test_only_ready_or_closed_order_can_be_applied(self):
        with patch.object(F,'financial',return_value=self.f):
            with self.assertRaisesMessage(ValueError,'Hàng sẵn sàng'): A.check(self.c,['D1'],'C1')
            state=DepositOrderState.objects.create(target='sandbox',trn_id='D1')
            for progress in ('new','ordering','crafting','shipping','partial','cancelled','applied'):
                state.fulfilment=progress;state.save()
                with self.subTest(progress=progress),self.assertRaises(ValueError): A.check(self.c,['D1'],'C1')
            for progress in ('ready','delivered'):
                state.fulfilment=progress;state.save()
                self.assertEqual(A.check(self.c,['D1'],'C1'),[self.f])
            state.fulfilment='crafting';state.save()
            with self.assertRaisesMessage(ValueError,'Hàng sẵn sàng'):
                A.check(self.c,['D1'],'C1','INV',invoice_status='W')
        self.c.call.assert_not_called()

    def test_completed_invoice_readback_recovers_progress_without_reapplying(self):
        state=DepositOrderState.objects.create(target='sandbox',trn_id='D1',fulfilment='cancelled')
        f={**self.f,'h':{**self.f['h'],'Status':'C'},'links':[{'TrnID':'INV'}]}
        with patch.object(F,'financial',return_value=f):
            self.assertEqual(A.check(self.c,['D1'],'C1','INV',invoice_status='C'),[f])
            with self.assertRaisesMessage(ValueError,'khác'): A.check(self.c,['D1'],'C1','OTHER',invoice_status='C')
        state.refresh_from_db();self.assertEqual(state.fulfilment,'cancelled')
        self.c.call.assert_not_called()

    def test_closed_order_stops_reserving_stock_but_ready_order_still_reserves(self):
        self.c.query.return_value=[{'TrnID':'D1','CustID':'C1','CustName':'Khách','TrnDate':None,'TienCoc':1000,
                                   'ProductDesc':'{"SP1":"Nhẫn"}','Notes':''}]
        state=DepositOrderState.objects.create(target='sandbox',trn_id='D1',fulfilment='ready')
        self.assertIn('SP1',BC.sp_dang_coc(self.c))
        state.fulfilment='delivered';state.save()
        self.assertEqual(BC.sp_dang_coc(self.c),{})

    def test_finish_waits_for_posted_invoice_till(self):
        op=Operation.objects.create(target='sandbox',trn_id='D1',invoice_id='INV',kind='apply',
            status='linked',amount=1000,active_key='sandbox:D1',token='apply')
        with patch.object(A,'invoice',return_value={'Status':'C','CustID':'C1','TienCoc':1000}),patch.object(A,'linked_ids',return_value=['D1']),patch.object(A,'check'):
            self.c.query.return_value=[{'Status':'U','TillID':None}]
            with self.assertRaisesMessage(ValueError,'vào két'): A.finish(self.c,'INV')
            op.refresh_from_db();self.assertEqual(op.status,'linked')
            self.c.query.return_value=[{'Status':'P','TillID':'T1'}]
            A.finish(self.c,'INV');A.finish(self.c,'INV')
        op.refresh_from_db();self.assertEqual(op.status,'done');self.assertIsNone(op.active_key)
        self.c.call.assert_not_called()

    def test_release_rejects_paid_invoice_or_stale_snapshot_without_writes(self):
        with patch.object(A,'invoice',return_value={'Status':'C'}):
            with self.assertRaises(ValueError): A.release_snapshot(self.c,'INV')
        with patch.object(A,'release_snapshot',return_value={'ids':['D1'],'invoice':{}}):
            with self.assertRaisesMessage(ValueError,'đã đổi'): A.release(self.c,'INV',self.user,{})
        self.c.call.assert_not_called();self.assertFalse(Operation.objects.exists())

    def test_interrupted_release_prevents_checkout(self):
        Operation.objects.create(target='sandbox',trn_id='D1',invoice_id='INV',kind='unlink',
            status='uncertain',amount=1000,token='release')
        with self.assertRaisesMessage(ValueError,'phục hồi'): B.chot('INV',till_id='T1',user_id='U1',c=self.c)
        self.c.call.assert_not_called()

    def test_link_timeout_readback_does_not_replay_proc(self):
        self.c.query.return_value=[]
        inv={'Status':'W','CustID':'C1','TienCoc':1000}
        with patch.object(BC,'kiem_truoc_khi_gan',return_value=(self.f['h'],'')),patch.object(BC,'kiem_san_pham',return_value=''),patch.object(A,'invoice',return_value=inv),patch.object(A,'check'),patch.object(A,'linked_ids',return_value=['D1']):
            self.assertEqual(BC.lien_ket(self.c,'INV',['D1'],'C1',self.user),(['D1'],''))
        self.c.call.assert_not_called();self.assertFalse(Operation.objects.exists())

    def test_release_interruption_keeps_evidence_and_blocks_completion(self):
        before={'ids':['D1'],'invoice':{'Status':'W','TienCoc':'1000','ShopID':'S1'}}
        doc={'ngay':'11/09/2026','gio':'10:00','ban':[],'doi':[],'cust_id':'C1','emp_id':'E1','ghi_chu':'',
             'tong':{'bot':0,'cong_them':0,'vang_them':2000}}
        with patch.object(A,'release_snapshot',side_effect=[before,before,{**before,'ids':[]}]),patch.object(B,'doc',return_value=doc),patch.object(A,'linked_ids',return_value=[]),patch.object(B,'luu',side_effect=TimeoutError('lost')):
            with self.assertRaises(TimeoutError): A.release(self.c,'INV',self.user,before)
        op=Operation.objects.get(kind='unlink')
        self.assertEqual(op.status,'uncertain');self.assertEqual(op.evidence['before'],before)
        self.assertEqual(self.c.call.call_count,1)
        with self.assertRaisesMessage(ValueError,'phục hồi'): B.chot('INV',till_id='T1',user_id='U1',c=self.c)

    def test_reconcile_deleted_draft_only_corrects_web_journal(self):
        op=Operation.objects.create(target='sandbox',trn_id='D1',invoice_id='INV',kind='apply',
            status='done',amount=1000,token='old')
        self.c.query.return_value=[]
        with patch.object(F,'financial',return_value={**self.f,'applied':False}):
            A.reconcile(self.c,op)
        op.refresh_from_db();self.assertEqual(op.status,'cancelled');self.c.call.assert_not_called()

    def test_reconcile_retains_uncertain_when_removed_link_has_posted_invoice(self):
        op=Operation.objects.create(target='sandbox',trn_id='D1',invoice_id='INV',kind='apply',
            status='linked',amount=1000,token='ambiguous')
        self.c.query.side_effect=[[{'Status':'C','TienCoc':1000}],[{'TillTxnID':'TX1'}]]
        with patch.object(F,'financial',return_value={**self.f,'applied':False}): A.reconcile(self.c,op)
        op.refresh_from_db();self.assertEqual(op.status,'uncertain');self.c.call.assert_not_called()

    def test_unknown_invoice_creation_is_not_replayed(self):
        op=A.reserve_invoice(self.c,'session-1',['D1'],'C1',1000,'test')
        op.status='uncertain';op.save()
        with self.assertRaisesMessage(ValueError,'không tạo lại'):
            A.reserve_invoice(self.c,'session-1',['D1'],'C1',1000,'test')
        self.assertEqual(Operation.objects.filter(kind='invoice').count(),1)

    def test_money_popup_parses_vietnamese_amounts(self):
        form=F.MoneyForm({'cash':'500.000','bank':'1.500.000','money_format':'vi','note':'Đã nhận','confirm':'on'})
        self.assertTrue(form.is_valid(),form.errors)
        self.assertEqual(form.cleaned_data['cash']+form.cleaned_data['bank'],Decimal('2000000'))

    def test_select_rechecks_money_even_when_cached_list_allows_it(self):
        from apps.pos import views as V
        request=RequestFactory().post('/banle/ban-hang/coc/',{'id':'D1'})
        g={'cust':{'id':'C1'},'coc_ids':[],'ban':[]}
        with patch.object(V,'_dang_khoa',return_value=False),patch.object(V.cart,'get',return_value=g),patch.object(V,'_coc_ctx',return_value={'coc_ds':[{'id':'D1'}]}),patch.object(V.S,'client',return_value=self.c),patch.object(BC,'kiem_san_pham',return_value=''),patch.object(BC,'kiem_truoc_khi_gan',return_value=(None,'Chưa xác nhận thu cọc.')),patch.object(V.cart,'save') as save,patch.object(V,'_loi',side_effect=lambda req,msg,extra=None:HttpResponse(msg)):
            response=V.ban_coc(request)
        self.assertIn('Chưa xác nhận thu cọc',response.content.decode());save.assert_not_called()
