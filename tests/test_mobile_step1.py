import datetime as dt
from unittest.mock import patch
from django.test import TestCase, RequestFactory
from django.utils import timezone
from django.contrib.auth import get_user_model
from apps.pos.models import MoneyFlow, MoneyFlowPayment
from apps.pos.money_flow_views import mobile_check


class Step1Tests(TestCase):
    def setUp(self):
        self.flow=MoneyFlow.objects.create(direction='IN',service='RETAIL',source_system='KHBL',
            source_type='gold_bill_retail',source_id='T1',source_bill_code='26-09-18-000001',
            business_date=timezone.localdate(),expected_amount=100)
        self.request=RequestFactory().post('/banle/mobile/money-in/1/check/')
        self.request.user=get_user_model().objects.create_superuser('step1',password='test')

    def qr(self,**kw):
        return MoneyFlowPayment.objects.create(flow=self.flow,method='BANK',amount=100,
            transfer_content=kw.pop('transfer_content','260918000001'),status='ready',**kw)

    @patch('apps.pos.money_in.reconcile',return_value={'status':'waiting'})
    def test_no_qr_wrong_code_and_old_qr_are_silent(self,reconcile):
        import json
        self.assertEqual(json.loads(mobile_check(self.request,self.flow.pk).content)['status'],'idle')
        self.qr(transfer_content='OTHER')
        qr=self.qr();MoneyFlowPayment.objects.filter(pk=qr.pk).update(created_at=timezone.now()-dt.timedelta(hours=2))
        self.assertEqual(json.loads(mobile_check(self.request,self.flow.pk).content)['status'],'idle')
        reconcile.assert_not_called()

    @patch('apps.pos.money_in.reconcile',return_value={'status':'waiting'})
    def test_recent_matching_qr_checks_once(self,reconcile):
        qr=self.qr()
        response=mobile_check(self.request,self.flow.pk)
        self.assertEqual(response.status_code,200)
        reconcile.assert_called_once_with(self.flow.pk,qr.pk)

    def test_ambiguous_evidence_is_waiting_not_terminal(self):
        import json
        from apps.pos.money_in import AmbiguousReceipt
        from apps.pos.money_flow_views import reconcile_pawn
        qr=self.qr()
        with patch('apps.pos.money_in.reconcile',side_effect=AmbiguousReceipt('multiple codes')):
            result=json.loads(mobile_check(self.request,self.flow.pk).content)
            self.assertEqual(result['status'],'waiting')
            self.assertNotIn('message',result)
            request=RequestFactory().post('/reconcile/',{'version':qr.updated_at.isoformat()})
            request.user=self.request.user
            result=json.loads(reconcile_pawn(request,qr.pk).content)
            self.assertEqual(result['status'],'waiting')
            self.assertEqual(result['code'],'ambiguous_receipt')

    @patch('apps.pos.money_in.reconcile',return_value={'status':'pending','received':'0','message':'Đang chờ chứng từ ngân hàng'})
    def test_reconcile_does_not_block_on_missing_or_stale_popup_version(self,reconcile):
        import json
        from apps.pos.money_flow_views import reconcile_pawn
        qr=self.qr()
        for posted in ({}, {'version':'stale-popup-version'}):
            request=RequestFactory().post('/reconcile/',posted)
            request.user=self.request.user
            response=reconcile_pawn(request,qr.pk)
            self.assertEqual(response.status_code,200)
            self.assertEqual(json.loads(response.content)['status'],'pending')
        self.assertEqual(reconcile.call_count,2)
