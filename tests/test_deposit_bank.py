import datetime as dt
from decimal import Decimal as Q
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from apps.pos import deposit_bank as R, deposit_money as F
from apps.pos.deposit_models import DepositOrderState, DepositMoneyOperation as Operation


class AllocationTests(SimpleTestCase):
    def row(self, pk, amount):
        return {'id': pk, 'direction': 'in', 'trans_amount': Q(amount)}

    def test_partial_multiple_duplicate_and_initial_bank(self):
        first, second = self.row(1, '300'), self.row(2, '200')
        self.assertEqual(R.allocation(Q(1000), 0, [first], [first, second]), (Q(500), Q(500)))
        self.assertEqual(R.allocation(Q(1000), 600, [], [first]), (Q(400), Q(600)))
        with self.assertRaisesRegex(ValueError, 'vượt tổng'):
            R.allocation(Q(1000), 0, [first], [self.row(2, '701')])

    def test_description_contains_code_without_word_boundary(self):
        self.assertTrue(R.contains_code({'description': 'FT123tdc260900000001CHUYENTIEN'}, 'TDC260900000001'))
        self.assertFalse(R.contains_code({'description': 'FT123', 'bill_code_raw': 'TDC260900000001'}, 'TDC260900000001'))

    def test_past_day_or_applied_receipt_never_changes_pmv(self):
        c = MagicMock(); c.sys_param.return_value = '0'
        f = {'valid': True, 'h': {'Status': 'P', 'TrnDate': timezone.localdate()-dt.timedelta(days=1)},
             'links': [], 'changes': [], 'amount': Q(1000)}
        with self.assertRaisesRegex(ValueError, 'ngày trước'): R.validate_reallocation(c, f, Q(700), Q(300))
        f['h']['TrnDate'] = timezone.localdate(); f['links'] = [{'TrnID': 'INV'}]
        with self.assertRaisesRegex(ValueError, 'liên kết'): R.validate_reallocation(c, f, Q(700), Q(300))
        c.call.assert_not_called()

    def test_stock_map_reads_product_description_json_without_custom_placeholder(self):
        from apps.pos import ban_coc as BC
        c = MagicMock(target='sandbox')
        base = {'TrnID':'TDC260900000001','CustID':'C1','TrnDate':timezone.localdate(),'TienCoc':Q(1000),'CustName':'Khách','Notes':''}
        c.query.return_value = [{**base,'ProductDesc':'{"SP001":"Nhẫn"}'}, {**base,'ProductDesc':'{"Khách đặt":"Vòng"}'}]
        with patch.object(BC, '_da_huy', return_value=set()): result = BC.sp_dang_coc(c)
        self.assertEqual(set(result), {'SP001'})
        self.assertIn('t.ProductDesc', c.query.call_args.args[0])


class ReconcileTests(TestCase):
    def setUp(self):
        self.code = 'TDC260900000001'
        self.user = get_user_model().objects.create_superuser('cashier', password='test')
        self.c = MagicMock(target='kk'); self.c.sys_param.return_value = '0'
        self.h = {'TrnID': self.code, 'BillCode': '26-09-12-000001', 'TrnDate': timezone.localdate(),
                  'TrnDateTime_Upd': dt.datetime(2026, 9, 12, 10), 'Status': 'P', 'CustID': 'C1',
                  'ShopID': 'S1', 'TienCoc': Q(1000), 'CashPay': Q(1000), 'CardPay': Q(0)}
        self.f = {'h': self.h, 'amount': Q(1000), 'cash': Q(1000), 'bank': Q(0), 'valid': True,
                  'tx': [{'TillID': 'T1'}], 'links': [], 'changes': []}
        source = Operation.objects.create(target='kk', trn_id=self.code, kind='receive', status='done',
            amount=1000, cash=1000, evidence={'money_policy':R.POLICY}, token='source', username=self.user.username, user_id='U1', till_id='T1')
        self.state = DepositOrderState.objects.create(target='kk', trn_id=self.code)
        R.enable_reconcile(source); self.state.refresh_from_db()
        # Bảng thử riêng của Django SQLite; không chạm DB khj_bl.
        with connection.cursor() as cur:
            cur.execute('CREATE TABLE bank_notifications (id INTEGER PRIMARY KEY, bank_number TEXT, transaction_time TEXT, '
                'trans_amount NUMERIC, direction TEXT, bill_code_raw TEXT, is_check INTEGER, description TEXT)')
        for p in [patch.object(R, 'PmvClient', return_value=self.c),
                  patch.object(R, 'pmv_user_for_web_user', return_value=SimpleNamespace(user_id='U1', till_id='T1')),
                  patch.object(F, 'financial', side_effect=lambda *a: self.f)]:
            p.start(); self.addCleanup(p.stop)

    def add_bank(self, pk, amount, description=None, checked=0):
        with connection.cursor() as cur:
            cur.execute('INSERT INTO bank_notifications VALUES (%s,%s,%s,%s,%s,%s,%s,%s)',
                [pk, '1234', timezone.localtime().replace(tzinfo=None).isoformat(sep=' '), amount, 'in', '', checked, description or 'FT' + self.code + 'CHUYEN'])

    def allocated(self, c, op):
        self.h = {**self.h, 'CashPay': op.cash, 'CardPay': op.bank, 'TienCoc':op.amount}
        self.f = {**self.f, 'cash': op.cash, 'bank': op.bank, 'h': self.h, 'amount':op.amount}
        return self.f

    def test_multiple_transfers_update_once_each_and_repeat_does_not_write(self):
        self.add_bank(1, 300); self.add_bank(2, 200)
        with patch.object(R, 'reallocate', side_effect=self.allocated) as write:
            self.assertEqual(R.reconcile(self.state)[0], 'matched')
            self.state.refresh_from_db()
            self.assertEqual(R.reconcile(self.state)[0], 'matched')
            self.assertEqual(write.call_count, 2)
        self.assertEqual(self.state.payment_plan['cash'], '500')
        self.assertEqual(self.state.payment_plan['bank'], '500')
        self.assertEqual(Operation.objects.filter(kind='bank_reconcile', status='done').count(), 2)
        self.assertFalse(Operation.objects.filter(active_key__isnull=False).exists())
        with connection.cursor() as cur:
            cur.execute('SELECT SUM(is_check) FROM bank_notifications'); self.assertEqual(cur.fetchone()[0], 2)

    def test_overpayment_or_multiple_receipt_codes_not_claimed(self):
        self.add_bank(1, 1200)
        with self.assertRaisesRegex(ValueError, 'vượt tổng'): R.reconcile(self.state)
        with connection.cursor() as cur:
            cur.execute('UPDATE bank_notifications SET trans_amount=100,description=%s', [self.code + ' TDC260900000002'])
        with self.assertRaisesRegex(ValueError, 'nhiều mã'): R.reconcile(self.state)
        self.assertFalse(Operation.objects.filter(kind='bank_reconcile').exists())
        self.c.call.assert_not_called()

    def test_partial_failure_retains_bank_claim_and_never_retries(self):
        self.add_bank(1, 300)
        with patch.object(R, 'reallocate', side_effect=TimeoutError()) as write:
            with self.assertRaisesRegex(ValueError, 'không phát lại'): R.reconcile(self.state)
            with self.assertRaisesRegex(ValueError, 'chưa hoàn tất'): R.reconcile(self.state)
            self.assertEqual(write.call_count, 1)
        op = Operation.objects.get(kind='bank_reconcile')
        self.assertEqual(op.status, 'uncertain'); self.assertEqual(op.bank_key, 'bank:1')
        self.assertIsNotNone(op.active_key)

    def test_modified_bank_evidence_is_alerted_without_reversal(self):
        self.add_bank(1, 300)
        with patch.object(R, 'reallocate', side_effect=self.allocated): R.reconcile(self.state)
        self.state.refresh_from_db()
        with connection.cursor() as cur: cur.execute('UPDATE bank_notifications SET trans_amount=350 WHERE id=1')
        with patch.object(R, 'reallocate') as write:
            with self.assertRaisesRegex(ValueError, 'sửa/xóa'): R.reconcile(self.state)
            write.assert_not_called()

    def test_bank_used_by_other_receipt_is_not_reused_even_if_flag_reset(self):
        self.add_bank(1, 300)
        Operation.objects.create(target='kk', trn_id='OTHER', kind='receive', status='done', amount=300,
            token='other', bank_key='bank:1', username='cashier', user_id='U1', till_id='T1')
        from django.db import IntegrityError
        with self.assertRaises(IntegrityError): R.reconcile(self.state)
        self.c.call.assert_not_called()

    def test_applied_receipt_does_not_claim_new_bank(self):
        self.add_bank(1, 300)
        self.f['links'] = [{'TrnID':'INV'}]
        with self.assertRaisesRegex(ValueError, 'liên kết'): R.reconcile(self.state)
        self.assertFalse(Operation.objects.filter(kind='bank_reconcile').exists())
