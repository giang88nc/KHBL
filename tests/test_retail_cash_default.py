from decimal import Decimal as D
from unittest.mock import MagicMock, patch
from django.test import SimpleTestCase
from apps.pmv import gateway as G
from apps.pos import bill as B


class RetailCashGatewayTests(SimpleTestCase):
    def setUp(self):
        self.cn = MagicMock()
        self.cur = self.cn.cursor.return_value
        self.cur.rowcount = 1
        self.before = dict(TrnID='TRB260900000001', BillCode='26-09-18-000001',
                           Status='W', IsDel='0', PayAmount=D(1000), CashPay=D(0), CardPay=D(0))
        for p in (patch.object(G, '_connect_dich', return_value=self.cn), patch.object(G, '_audit'),
                  patch('apps.pmv.models.PmvState.get', return_value='0')):
            p.start(); self.addCleanup(p.stop)

    def setup_rows(self, **changes):
        self.before.update(changes)
        self.after = {**self.before, 'CashPay': self.before['PayAmount'] - D(self.before['CardPay'] or 0)}
        self.cur.fetchall.return_value = [tuple(self.before.values())]
        self.cur.fetchone.side_effect = [None, tuple(self.after.values())]

    def write(self, **kwargs):
        return G.pmv_retail_cash_default(self.before['TrnID'], target=kwargs.pop('target', 'sandbox'), **kwargs)

    def test_new_invoice_defaults_to_full_cash(self):
        self.setup_rows()
        result = self.write()
        self.assertEqual((result['CashPay'], result['CardPay']), (D(1000), D(0)))
        writes = [c.args for c in self.cur.execute.call_args_list if c.args[0].startswith('UPDATE')]
        self.assertEqual(len(writes), 1)
        self.assertIn('SET CashPay=? WHERE', writes[0][0])
        self.assertEqual(writes[0][1][0], D(1000))
        self.cn.commit.assert_called_once()

    def test_existing_bank_is_preserved_using_current_locked_row(self):
        self.setup_rows(PayAmount=D(1500), CardPay=D(600), CashPay=D(100))
        result = self.write()
        self.assertEqual((result['CashPay'], result['CardPay']), (D(900), D(600)))
        self.assertTrue(any('UPDLOCK,HOLDLOCK' in c.args[0] for c in self.cur.execute.call_args_list))

    def test_repeat_is_noop_and_closed_invoice_can_resume(self):
        self.setup_rows(Status='C', CashPay=D(700), CardPay=D(300))
        self.write()
        self.assertFalse(any(c.args[0].startswith('UPDATE') for c in self.cur.execute.call_args_list))

    def test_zero_full_bank_and_signed_payments_follow_formula(self):
        for pay, card in ((0, 0), (1000, 1000), (-500, 0), (500, 700)):
            self.setup_rows(PayAmount=D(pay), CardPay=D(card))
            self.assertEqual(self.write()['CashPay'], D(pay-card))

    def test_dry_run_is_rollback_and_kk_cannot_use_it(self):
        self.setup_rows()
        self.write(verify_only=True)
        self.cn.rollback.assert_called_once(); self.cn.commit.assert_not_called()
        with self.assertRaises(ValueError): self.write(target='kk', verify_only=True)

    def test_locked_or_wrong_target_never_connects(self):
        with patch('apps.pmv.models.PmvState.get', return_value='1'):
            with self.assertRaises(G.PmvBlocked): self.write()
        with patch.object(G, 'duoc_ghi_kk', return_value=False):
            with self.assertRaises(G.PmvBlocked): self.write(target='kk')
        with self.assertRaises(ValueError): self.write(target='hist')
        G._connect_dich.assert_not_called()

    def test_deleted_missing_and_triggered_invoice_never_write(self):
        for override in ({'IsDel': '1'}, {'Status': 'D'}):
            self.setup_rows(**override)
            with self.assertRaises(ValueError): self.write()
        self.setup_rows()
        self.cur.fetchall.return_value = []
        with self.assertRaises(ValueError): self.write()
        self.cur.fetchone.side_effect = [('trigger',)]
        with self.assertRaises(ValueError): self.write()
        self.cn.commit.assert_not_called()
        self.assertFalse(any(c.args[0].startswith('UPDATE') for c in self.cur.execute.call_args_list))

    def test_readback_change_in_bank_rolls_back(self):
        self.setup_rows(CardPay=D(300))
        self.cur.fetchone.side_effect = [None, tuple({**self.after, 'CardPay': D(400)}.values())]
        with self.assertRaisesRegex(ValueError, 'Đọc lại'): self.write()
        self.cn.rollback.assert_called_once(); self.cn.commit.assert_not_called()


class RetailCashCheckoutTests(SimpleTestCase):
    def setUp(self):
        self.c = MagicMock(target='sandbox')
        self.events = []
        self.c.query.side_effect = self.query
        self.c.call.side_effect = lambda proc, **kw: self.events.append(proc)
        self.c.sync_retail_cash.side_effect = lambda _: self.events.append('cash')
        for p in (patch('apps.pos.deposit_models.DepositMoneyOperation.objects'),
                  patch('apps.pos.deposit_application.linked_ids', return_value=[]),
                  patch('apps.pos.deposit_operations.check_holds'),
                  patch('apps.oa.xep_hang.xep_hang_hoa_don')):
            obj = p.start(); self.addCleanup(p.stop)
            if hasattr(obj, 'filter'): obj.filter.return_value.exists.return_value = False

    def query(self, sql, params):
        if 'SELECT Status FROM TRN_RT_BUYSELL' in sql: return [{'Status': self.status}]
        if 'SELECT Status, PayAmount, CashPay, CardPay' in sql:
            return [dict(Status='C', PayAmount=D(1000), CashPay=self.cash, CardPay=D(400))]
        if 'T_TILL_TXN' in sql:
            return [{'co': 1}] if self.status == 'C' or 'TillID IS NOT NULL' in sql else []
        return []

    def test_cash_verified_before_completion(self):
        self.status, self.cash = 'W', D(600)
        B.chot('TRB_TEST', till_id='T1', user_id='U1', c=self.c)
        self.assertEqual(self.events, ['cash', 'TRN_RT_BUYSELL_Complete', 'T_TILL_TXN_Proc'])

    def test_resume_does_not_complete_or_post_till_again(self):
        self.status, self.cash = 'C', D(600)
        B.chot('TRB_TEST', till_id='T1', user_id='U1', c=self.c)
        self.assertEqual(self.events, ['cash'])

    def test_cash_failure_stops_before_financial_procs(self):
        self.c.sync_retail_cash.side_effect = ValueError('ghi tiền mặt lỗi')
        with self.assertRaisesRegex(ValueError, 'ghi tiền mặt'):
            B.chot('TRB_TEST', till_id='T1', user_id='U1', c=self.c)
        self.c.call.assert_not_called()

    def test_bad_final_split_not_reported_as_done(self):
        self.status, self.cash = 'C', D(0)
        with self.assertRaises(B.PmvProcError): B.chot('TRB_TEST', till_id='T1', user_id='U1', c=self.c)
