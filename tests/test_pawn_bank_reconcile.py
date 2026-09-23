import datetime as dt
from decimal import Decimal
from django.test import SimpleTestCase, TestCase
from django.db import connection
from django.utils import timezone
from unittest.mock import patch
from apps.pos import pawn_bank_reconcile as R
from apps.pos.payment_reference import pawn_reference, contains_reference
from apps.pos.models import MoneyFlow, MoneyFlowPayment, MoneyFlowBankReceipt


class ReferenceTests(SimpleTestCase):
    def test_non_transactional_bank_table_blocks_writes(self):
        with patch.object(R,'query',return_value=[{'ENGINE':'MyISAM'}]),self.assertRaises(ValueError):
            R.ensure_transactional()
    def test_reference_and_boundaries(self):
        ref=pawn_reference(dt.date(2026,9,17),977,3435)
        self.assertEqual(ref,'26090097703435')
        for text in [ref,'CK 2609-00977-03435 KH','CT 2609 00977 03435']:
            self.assertTrue(contains_reference(text,ref))
        for text in ['126090097703435',ref+'1','26090097703436','KH22609020839']:
            self.assertFalse(contains_reference(text,ref))
        with self.assertRaises(ValueError):pawn_reference(dt.date.today(),1,100000)

    def test_totals(self):
        self.assertEqual(R.checked_total(3500000,[1000000,2000000,500000]),(3500000,0))
        self.assertEqual(R.checked_total(3500000,[1000000]),(1000000,2500000))
        for values in [[4000000],[-1],['NaN'],['1.5']]:
            with self.assertRaises(ValueError):R.checked_total(3500000,values)

    def test_recipient_direction_time(self):
        start=dt.datetime(2026,9,17,8)
        row=dict(bank_number='123',direction='in',transaction_time='2026-09-17 08:00:00',description='26090097703435')
        self.assertTrue(R.candidate(row,'26090097703435','123','IN',start,start))
        for fields in [dict(bank_number='456'),dict(direction='out'),dict(transaction_time='invalid'),dict(transaction_time='2026-09-16 08:00:00')]:
            self.assertFalse(R.candidate({**row,**fields},'26090097703435','123','IN',start,start))


class ReconcileTransactionTests(TestCase):
    """Actual transaction/rollback using disposable SQLite source tables, no live DB."""
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        with connection.cursor() as c:
            c.execute("ATTACH DATABASE ':memory:' AS khj_cd")
            c.execute('CREATE TABLE khj_cd.cd_loans(id INTEGER)')
            c.execute('CREATE TABLE khj_cd.cd_loan_logs(id INTEGER,loan_id INTEGER,happened_at TIMESTAMP,operation_id INTEGER,request_key TEXT)')
            c.execute('CREATE TABLE khj_cd.cd_payments(id INTEGER,log_id INTEGER,direction TEXT,amount DECIMAL,channel TEXT,cashPay DECIMAL,cardPay DECIMAL,payment_ref TEXT,reconciliation_state TEXT)')
            c.execute('CREATE TABLE bank_notifications(id INTEGER,ref_code TEXT,bank_number TEXT,bank_name TEXT,transaction_time TEXT,trans_amount DECIMAL,direction TEXT,description TEXT,is_check INTEGER)')

    def setUp(self):
        self.start=timezone.localtime().replace(tzinfo=None,microsecond=0)-dt.timedelta(minutes=5)
        self.ref=pawn_reference(self.start,977,3435)
        self.flow=MoneyFlow.objects.create(source_system='KHCD',source_type='cd_loan_log',source_id='3435',source_group_id='977',
            direction='IN',service='REDEEM',expected_amount=1000,cash_amount=1000,business_date=self.start.date())
        self.qr=MoneyFlowPayment.objects.create(flow=self.flow,method='BANK',amount=1000,bank_account='123',bank_code='ACB',transfer_content=self.ref)
        with connection.cursor() as c:
            c.execute('INSERT INTO khj_cd.cd_loans VALUES (977)')
            c.execute('INSERT INTO khj_cd.cd_loan_logs VALUES (3435,977,%s,4,%s)',[self.start,'test-only'])
            c.execute("INSERT INTO khj_cd.cd_payments VALUES (1,3435,'IN',1000,'CASH',NULL,NULL,NULL,'RECORDED')")
        original=R.query
        self.vendor=patch.object(connection,'vendor','mysql');self.vendor.start();self.addCleanup(self.vendor.stop)
        self.query=patch.object(R,'query',side_effect=lambda sql,params=():original(sql.replace(' FOR UPDATE',''),params))
        self.query.start();self.addCleanup(self.query.stop)
        self.engines=patch.object(R,'ensure_transactional');self.engines.start();self.addCleanup(self.engines.stop)

    def notification(self,pk,amount,ref=None):
        with connection.cursor() as c:
            c.execute('INSERT INTO bank_notifications VALUES (%s,%s,%s,%s,%s,%s,%s,%s,0)',
                      [pk,ref or f'bank-{pk}','123','ACB',str(self.start+dt.timedelta(minutes=1)),amount,'in',self.ref])

    def test_partial_cumulative_and_idempotent(self):
        self.notification(1,300)
        self.assertEqual(R.reconcile(self.flow.pk)['status'],'partial')
        self.assertEqual(R.reconcile(self.flow.pk)['received'],'300.00')
        self.notification(2,700)
        self.assertEqual(R.reconcile(self.flow.pk)['status'],'success')
        self.assertEqual(MoneyFlowBankReceipt.objects.count(),2)
        self.assertEqual(R.query('SELECT cashPay,cardPay FROM khj_cd.cd_payments')[0],dict(cashPay=0,cardPay=1000))

    def test_duplicate_bank_reference_counted_once(self):
        self.notification(1,300,'same');self.notification(2,300,'same')
        R.reconcile(self.flow.pk)
        self.assertEqual(MoneyFlowBankReceipt.objects.count(),1)

    def test_overpayment_rolls_back_all(self):
        self.notification(1,1001)
        with self.assertRaises(ValueError):R.reconcile(self.flow.pk)
        self.assertFalse(MoneyFlowBankReceipt.objects.exists())
        self.assertEqual(R.query('SELECT is_check FROM bank_notifications')[0]['is_check'],0)

    def test_ambiguous_codes_and_existing_ownership_block(self):
        self.notification(1,300)
        with connection.cursor() as c:
            c.execute('UPDATE bank_notifications SET description=%s',[self.ref+' 26090097803436'])
        with self.assertRaises(ValueError):R.reconcile(self.flow.pk)
        with connection.cursor() as c:
            c.execute('UPDATE bank_notifications SET description=%s,is_check=1',[self.ref])
        with self.assertRaises(ValueError):R.reconcile(self.flow.pk)
        self.assertFalse(MoneyFlowBankReceipt.objects.exists())

    def test_duplicate_identity_conflicting_amount_blocked(self):
        self.notification(1,300,'same');self.notification(2,400,'same')
        with self.assertRaises(ValueError):R.reconcile(self.flow.pk)
        self.assertFalse(MoneyFlowBankReceipt.objects.exists())

    def test_existing_unverified_bank_is_not_overwritten(self):
        self.notification(1,300)
        with connection.cursor() as c:
            c.execute('UPDATE khj_cd.cd_payments SET cashPay=800,cardPay=200')
        with self.assertRaises(ValueError):R.reconcile(self.flow.pk)

    def test_mixed_payment_success_is_not_full_bank_payment(self):
        self.qr.amount=300;self.qr.save()
        self.notification(1,300)
        result=R.reconcile(self.flow.pk)
        self.assertEqual(result['status'],'success')
        self.assertFalse(result['fully_bank_paid'])
        self.assertEqual(Decimal(result['cash']),700)

    def test_source_verify_failure_rolls_back_evidence_and_money(self):
        self.notification(1,1000)
        original=R.query
        def invalid(sql,params=()):
            rows=original(sql,params)
            if sql.startswith('SELECT amount,cashPay,cardPay'):rows[0]['cashPay']=123
            return rows
        with patch.object(R,'query',side_effect=invalid),self.assertRaises(ValueError):R.reconcile(self.flow.pk)
        self.assertFalse(MoneyFlowBankReceipt.objects.exists())
        self.assertEqual(R.query('SELECT cardPay FROM khj_cd.cd_payments')[0]['cardPay'],None)
        self.assertEqual(R.query('SELECT is_check FROM bank_notifications')[0]['is_check'],0)

    def test_missing_source_and_stale_qr_block(self):
        with self.assertRaises(ValueError):R.reconcile(self.flow.pk,'old-version')
        with connection.cursor() as c:c.execute('DELETE FROM khj_cd.cd_loan_logs')
        with self.assertRaises(ValueError):R.reconcile(self.flow.pk)

    def test_three_hour_boundary_and_old_account_qr(self):
        self.notification(1,300)
        with connection.cursor() as c:
            c.execute('UPDATE bank_notifications SET transaction_time=%s',[str(self.start+dt.timedelta(hours=3,seconds=1))])
        R.reconcile(self.flow.pk)
        self.assertFalse(MoneyFlowBankReceipt.objects.exists())
        self.qr.status='superseded';self.qr.save()
        MoneyFlowPayment.objects.create(flow=self.flow,method='BANK',amount=1000,bank_code='ACB',bank_account='999',transfer_content=self.ref)
        with connection.cursor() as c:
            c.execute('UPDATE bank_notifications SET transaction_time=%s',[str(self.start+dt.timedelta(seconds=10))])
        self.assertEqual(R.reconcile(self.flow.pk)['status'],'partial')
        self.assertEqual(MoneyFlowBankReceipt.objects.get().bank_account,'123')

    def test_pmv_uncertain_commit_has_durable_retry_without_double_count(self):
        from contextlib import nullcontext
        from apps.pos import money_in as MI
        from apps.pos.models import GoldBill,MoneyFlowSourceWrite
        self.flow.source_system='KHBL';self.flow.source_type='gold_bill_retail';self.flow.service='RETAIL'
        self.flow.source_id='TRBTEST';self.flow.source_bill_code='26-09-17-000014';self.flow.save()
        self.ref='260917000014';self.qr.transfer_content=self.ref;self.qr.save()
        GoldBill.all_objects.create(target='kk',trn_id='TRBTEST',bill_code='26-09-17-000014',
            trn_date=self.start.date(),trn_time=self.start.strftime('%H:%M:%S'),status='W',tong=1000,tien_mat=1000)
        self.notification(1,1000)
        with patch.object(MI,'flow_lock',side_effect=lambda pk:nullcontext()),patch.object(MI,'check_source',return_value=(self.start,Decimal(1000),{})),patch.object(MI.gateway,'pmv_in_allocate',side_effect=[TimeoutError('lost commit response'),None]) as remote:
            result=MI.reconcile(self.flow.pk,self.qr.pk)
            self.assertEqual(result['status'],'pending')
            self.assertEqual(MoneyFlowBankReceipt.objects.get().status,'pending')
            self.assertEqual(MoneyFlowSourceWrite.objects.get().status,'pending')
            result=MI.reconcile(self.flow.pk,self.qr.pk)
            self.assertEqual(result['status'],'success')
            self.assertEqual(MoneyFlowBankReceipt.objects.count(),1)
            self.assertEqual(remote.call_count,2)
            self.assertEqual(remote.call_args_list[0],remote.call_args_list[1])
        bill=GoldBill.all_objects.get(trn_id='TRBTEST')
        self.assertEqual((bill.tien_mat,bill.tien_ck,bill.status),(0,1000,'W'))
        self.qr.refresh_from_db();self.assertEqual(self.qr.status,'success')
