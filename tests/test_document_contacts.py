import uuid
from unittest.mock import patch
from django.test import TestCase
from django.db import transaction
from apps.pos import document_contacts as D
from apps.pos.models import DocumentContact, DocumentContactWrite
from apps.oa import xep_hang


class ContactTests(TestCase):
    def save(self,kind,pk,phone='0900000002',cust='CU1'):
        intent=D.begin(kind,uuid.uuid4().hex,D.payload(cust,'Khách QA',phone,'qa'))
        D.bind(intent,pk);D.finish(intent,'26-09-21-000007')
        return intent

    def test_same_customer_different_documents(self):
        self.save('KHBL_DEPOSIT','TDC260900000045')
        self.save('KHBL_BUYSELL','TRB1','0900000003')
        for kind,pk,phone in [('KHBL_DEPOSIT','TDC260900000045','0900000002'),('KHBL_BUYSELL','TRB1','0900000003')]:
            row=D.overlay(kind,dict(TrnID=pk,CustID='CU1',Phone='0900000099',CustName='Đã đổi'))
            self.assertEqual(row['Phone'],phone)
            self.assertEqual(row['CustName'],'Khách QA')
        self.assertEqual(DocumentContact.objects.count(),2)

    def test_namespace_and_update_do_not_duplicate(self):
        self.save('KHBL_DEPOSIT','same')
        self.save('KHBL_BUYSELL','same')
        self.save('KHBL_DEPOSIT','same','0900000004')
        self.assertEqual(DocumentContact.objects.count(),2)
        self.assertEqual(D.read('KHBL_DEPOSIT','same').phone,'0900000004')

    def test_unknown_result_never_replays(self):
        token=uuid.uuid4().hex;contact=D.payload('CU1','QA','0900000001')
        intent=D.begin('KHBL_BUYSELL',token,contact)
        with self.assertRaises(ValueError): D.begin('KHBL_BUYSELL',token,contact)
        self.assertEqual(DocumentContactWrite.objects.get(pk=intent.pk).snapshot,contact)
        self.assertFalse(DocumentContact.objects.exists())

    def test_atomic_finish_and_idempotence(self):
        intent=D.begin('KHBL_BUYSELL',uuid.uuid4().hex,D.payload('CU1','QA','0900000001'))
        D.bind(intent,'TRB1')
        with self.assertRaises(RuntimeError):
            with transaction.atomic(): D.finish(intent);raise RuntimeError('rollback')
        self.assertFalse(DocumentContact.objects.exists())
        intent.refresh_from_db();self.assertEqual(intent.status,'pending')
        D.finish(intent);D.finish(intent)
        self.assertEqual(DocumentContact.objects.count(),1)

    def test_different_sessions_cannot_write_same_document(self):
        contact=D.payload('CU1','QA','0900000001')
        one=D.begin('KHBL_BUYSELL',uuid.uuid4().hex,contact,source_id='TRB1')
        with self.assertRaises(ValueError): D.begin('KHBL_BUYSELL',uuid.uuid4().hex,contact,source_id='TRB1')
        D.finish(one)
        two=D.begin('KHBL_BUYSELL',uuid.uuid4().hex,contact,source_id='TRB1')
        self.assertNotEqual(one.pk,two.pk)

    def test_cust_mismatch_blocks_print_or_sms(self):
        self.save('KHBL_BUYSELL','TRB1')
        with self.assertRaises(ValueError): D.overlay('KHBL_BUYSELL',dict(TrnID='TRB1',CustID='CU2'))

    def test_sandbox_never_reads_or_writes_live_snapshot(self):
        self.save('KHBL_BUYSELL','TRB1')
        row=dict(TrnID='TRB1',CustID='CU2',Phone='0900000009')
        self.assertEqual(D.overlay('KHBL_BUYSELL',row,'sandbox'),row)
        intent=D.begin('KHBL_BUYSELL',uuid.uuid4().hex,D.payload('CU2','QA','0900000009'),'sandbox','TRB1')
        D.finish(intent)
        self.assertEqual(D.read('KHBL_BUYSELL','TRB1').cust_id,'CU1')

    def test_sms_uses_same_overlay(self):
        self.save('KHBL_BUYSELL','TRB1')
        with patch('apps.pmv.gateway.pmv_read',return_value=[dict(TrnID='TRB1',CustID='CU1',Phone='0900000001')]),patch.object(xep_hang,'_dich_hien_tai',return_value='kk'):
            self.assertEqual(xep_hang.doc_hoa_don('TRB1')['Phone'],'0900000002')

    def test_normalization_and_no_multiple_numbers(self):
        self.assertEqual(D.normalize_phone('+84 900.000.002'),'0900000002')
        for bad in ['abc0900000001','0900000001/0900000002','123']:
            with self.assertRaises(ValueError): D.normalize_phone(bad)


class SaveBoundaryTests(TestCase):
    def test_retail_binds_contact_after_customer_readback(self):
        from unittest.mock import MagicMock
        from apps.pos import bill as B
        c=MagicMock(target='kk');c.money.side_effect=str
        c.call.return_value=(0,[[{'TrnID':'TRB_QA'}]])
        c.query.side_effect=[[dict(BillCode='QA',CustID='CU1',PayAmount=0,TienCoc=0)],[]]
        intent={'token':uuid.uuid4().hex,'contact':D.payload('CU1','QA','0900000002')}
        with patch.object(B,'_tham_so',side_effect=lambda c,proc,**kw:kw),patch('apps.pos.deposit_operations.check_holds'):
            result=B.luu(trn_id='',ban=[],doi=[],ngay='21/09/2026',gio='12:00',cust_id='CU1',emp_id='E',till_id='T',shop_id='S',user_id='U',c=c,contact_intent=intent)
        self.assertEqual(result['trn_id'],'TRB_QA')
        self.assertEqual(D.read('KHBL_BUYSELL','TRB_QA').phone,'0900000002')
        self.assertEqual(c.call.call_count,1)

    def test_thau_wrong_customer_keeps_pending_and_blocks_print(self):
        from unittest.mock import MagicMock
        from apps.pos import bill as B
        c=MagicMock(target='kk');c.call.return_value=(0,[[{'TrnID':'TBG_QA'}]])
        intent=D.begin('KHBL_BUYGOLD',uuid.uuid4().hex,D.payload('CU1','QA','0900000002'))
        with patch.object(B.M,'buy_amount_standalone',return_value=1000000),patch.object(B,'phieu_thau',return_value=dict(TrnID='TBG_QA',TotalAmount=1000000,CustID='CU2')):
            with self.assertRaisesRegex(ValueError,'CustID'):
                B.luu_thau(cust_id='CU1',emp_id='E',till_id='T',shop_id='S',user_id='U',gold_code='D9999',gw=100,rate=10000,c=c,contact_intent=intent)
        intent.refresh_from_db();self.assertEqual(intent.source_id,'TBG_QA');self.assertEqual(intent.status,'pending')
        with self.assertRaises(ValueError): D.read('KHBL_BUYGOLD','TBG_QA')
        self.assertFalse(DocumentContact.objects.exists())

    def test_deposit_uses_trnid_not_billcode(self):
        from unittest.mock import MagicMock
        from types import SimpleNamespace
        from decimal import Decimal
        from apps.pos import deposits as P
        c=MagicMock(target='kk')
        c.query.return_value=[dict(CustID='CU1',CustName='QA',Phone='0900000001')]
        c.call.return_value=(0,[[dict(ErrCode=0,TrnID='TDC260900000045')]])
        data=dict(CustID='CU1',EmpID='E',TienCoc=Decimal(1000),Description='{}',DocumentPhone='0900000002')
        actual=dict(data,TrnID='TDC260900000045',BillCode='26-09-21-000007')
        with patch.object(P,'header',return_value=actual),patch.object(P,'lines',return_value=[]),patch.object(P.O,'_json_description',return_value={}):
            pk=P.save(c,data,[],SimpleNamespace(user_id='U',shop_id='S'),contact_intent={'token':uuid.uuid4().hex,'actor':'qa'})
        self.assertEqual(pk,'TDC260900000045')
        contact=D.read('KHBL_DEPOSIT',pk)
        self.assertEqual(contact.document_code,'26-09-21-000007')
        self.assertEqual(contact.phone,'0900000002')
