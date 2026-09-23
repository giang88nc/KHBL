import uuid
from unittest.mock import patch
from django.test import TestCase,RequestFactory
from django.core import signing
from django.contrib.auth import get_user_model
from django.db import connection
from django.utils import timezone
from apps.pos import mobile_qr as Q
from apps.pos.models import MoneyFlow,MoneyFlowPayment,MoneyFlowBankReceipt

BANK={'id':1,'bank_bin':'ACB','bank_number':'123456','bank_user':'TEST','bank_name':'ACB'}
class StandaloneQrTests(TestCase):
    def setUp(self):
        self.user=get_user_model().objects.create_superuser('qr-test',password='test')
        self.token=signing.dumps({'user':self.user.pk,'nonce':uuid.uuid4().hex},salt='standalone-qr')
        with connection.cursor() as cur:cur.execute('CREATE TABLE IF NOT EXISTS bank_notifications (id INTEGER PRIMARY KEY,is_check INTEGER NOT NULL DEFAULT 0)')
    def make(self,amount='1000'):
        return Q.create(self.user,self.token,BANK,amount,'Ghi chú')
    def req(self):
        r=RequestFactory().get('/banle/mobile/qr/');r.user=self.user;r.session={};return r
    def receipt(self,qr,pk,amount=1000,**values):
        with connection.cursor() as cur:cur.execute('INSERT INTO bank_notifications(id,is_check) VALUES(%s,0)',[pk])
        return dict(id=pk,ref_code='REF'+str(pk),bank_number='123456',bank_name='ACB',transaction_time=str(timezone.localtime().replace(tzinfo=None)),trans_amount=amount,direction='in',description='IBFT '+qr.transfer_content,is_check=0,**values)
    def test_creation_idempotent_without_flow(self):
        qr=self.make();self.assertEqual(qr.pk,self.make().pk)
        self.assertIsNone(qr.flow_id);self.assertEqual(MoneyFlow.objects.count(),0)
        self.assertRegex(qr.transfer_content,r'^KH2Q[A-P]{20}$')
        with self.assertRaises(ValueError):self.make('2000')
        for amount in ['0','-1','NaN','1.5']:
            with self.assertRaises(ValueError):self.make(amount)
    def test_short_codes_categories_and_matching(self):
        for i,category in enumerate(Q.CATEGORIES):
            token=Q.new_token(self.user)
            qr=Q.create(self.user,token,BANK,'1000','',category)
            self.assertEqual(len(qr.transfer_content),14)
            self.assertRegex(qr.transfer_content, r'^KHBL\d{10}$')      # 22/09/2026: KHBL + 10 số giây, loại vẫn lưu ở qr_category
            self.assertEqual(qr.qr_category,category)
            self.assertEqual(qr.standalone_reference,qr.transfer_content)
            receipt=self.receipt(qr,100+i)
            with patch.object(Q.P,'query',return_value=[receipt]):
                self.assertEqual(Q.reconcile(qr.pk)['status'],'success')
    def test_partial_then_success_idempotent(self):
        qr=self.make();a=self.receipt(qr,1,400);b=self.receipt(qr,2,600)
        with patch.object(Q.P,'query',return_value=[a]):self.assertEqual(Q.reconcile(qr.pk)['status'],'partial')
        with patch.object(Q.P,'query',return_value=[a,b]):self.assertEqual(Q.reconcile(qr.pk)['status'],'success')
        with patch.object(Q.P,'query') as read:
            self.assertEqual(Q.reconcile(qr.pk)['status'],'success');read.assert_not_called()
        self.assertEqual(MoneyFlowBankReceipt.objects.filter(payment=qr).count(),2)
        self.assertEqual(MoneyFlow.objects.count(),0)
    def test_wrong_account_direction_ambiguous_used_or_overpayment(self):
        qr=self.make();base=self.receipt(qr,1)
        for delta in [{'bank_number':'other'},{'direction':'out'},{'bank_name':'VCB'},
                      {'description':qr.transfer_content+' 260919000001'},{'is_check':1}]:
            with patch.object(Q.P,'query',return_value=[{**base,**delta}]):
                self.assertEqual(Q.reconcile(qr.pk)['status'],'waiting')
        with patch.object(Q.P,'query',return_value=[{**base,'trans_amount':1001}]):
            self.assertEqual(Q.reconcile(qr.pk)['status'],'review')
        self.assertEqual(MoneyFlowBankReceipt.objects.count(),0)
    def test_views_and_ownership(self):
        qr=self.make();self.assertContains(Q.index(self.req()),'QR tự tạo')
        self.assertContains(Q.detail(self.req(),qr.pk),'data-pawn-poll')
        with patch.object(Q.vietqr,'active_banks',return_value=[BANK]):
            self.assertContains(Q.new(self.req()),'standalone-qr-form')
        user=get_user_model().objects.create_user('other');r=self.req();r.user=user
        self.assertFalse(Q.own(r).exists())
    def test_today_only_and_creator_full_name(self):
        qr=self.make()
        self.user.first_name='Trương Ngọc';self.user.last_name='Giang';self.user.save()
        response=Q.index(self.req())
        self.assertContains(response,'Trương Ngọc Giang')
        self.assertNotContains(response,'Tiếp ›')
        MoneyFlowPayment.objects.filter(pk=qr.pk).update(created_at=timezone.now()-timezone.timedelta(days=1))
        self.assertContains(Q.index(self.req()),'Chưa có QR tự tạo.')
    def test_window_and_foreign_token(self):
        qr=self.make()
        MoneyFlowPayment.objects.filter(pk=qr.pk).update(created_at=timezone.now()-timezone.timedelta(hours=4))
        with patch.object(Q.P,'query',return_value=[]):self.assertEqual(Q.reconcile(qr.pk)['status'],'review')
        other=get_user_model().objects.create_user('different')
        with self.assertRaises(ValueError):Q.create(other,self.token,BANK,'1000','')
