from decimal import Decimal as Q
from unittest.mock import MagicMock, patch
from django.test import SimpleTestCase, override_settings
from apps.pmv import gateway as G


class DepositGatewayTests(SimpleTestCase):
    def setUp(self):
        self.h = dict(TrnID='TDC260900000001', BillCode='26-09-12-000001', TrnDate='2026-09-12',
            TrnDateTime_Upd='stamp', CustID='C1', ShopID='S1', Status='P', TienCoc=Q(1000), CashPay=Q(0), CardPay=Q(0))
        self.cn = MagicMock(); self.cur = self.cn.cursor.return_value
        self.cur.rowcount = 1
        self.cur.fetchall.side_effect = [[tuple(self.h.values())], [('TX', None, 'U', Q(1000))], [(Q(1000), '+', 'VND')]]
        self.cur.fetchone.side_effect = [None, None, (Q(700), Q(300), Q(1000)), (Q(700), 'U'), (Q(700), '+', 'VND')]
        for p in [patch.object(G, '_connect_dich', return_value=self.cn), patch.object(G, '_audit'),
                  patch('apps.pmv.models.PmvState.get', return_value='0'), patch.object(G, '_drain', return_value=(0, []))]:
            p.start(); self.addCleanup(p.stop)

    def write(self, **kw):
        return G.pmv_deposit_money(self.h, Q(700), Q(300), till_id='T1', tag='test', target=kw.get('target', 'sandbox'))

    def test_fixed_total_write_and_vendor_till_adjustment_commit_together(self):
        self.write()
        sql = [a.args[0] for a in self.cur.execute.call_args_list]
        self.assertEqual(sum(q.startswith('UPDATE TRN_DATCOC') for q in sql), 1)
        self.assertFalse(any('CARDPAY_Ins' in q for q in sql))
        self.assertTrue(any('EXEC @rc=TRN_TILL_TXN_Upd' in q for q in sql))
        self.cn.commit.assert_called_once(); self.cn.rollback.assert_not_called()

    def test_stale_snapshot_rolls_back_without_update(self):
        self.cur.fetchall.side_effect = [[tuple({**self.h, 'CardPay':Q(50)}.values())]]
        with self.assertRaisesRegex(ValueError, 'vừa thay đổi'): self.write()
        self.cn.rollback.assert_called_once(); self.cn.commit.assert_not_called()
        self.assertFalse(any(a.args[0].startswith('UPDATE') for a in self.cur.execute.call_args_list))

    def test_linked_receipt_rejected_inside_transaction(self):
        self.cur.fetchone.side_effect = [('INV',)]
        with self.assertRaisesRegex(ValueError, 'liên kết'): self.write()
        self.cn.commit.assert_not_called()

    def test_already_posted_till_never_adjusted_again(self):
        self.cur.fetchall.side_effect = [[tuple(self.h.values())], [('TX','T1','P',Q(1000))]]
        with self.assertRaisesRegex(ValueError, 'Quỹ cọc'): self.write()
        self.cn.commit.assert_not_called()

    def test_vendor_failure_rolls_back_header_and_till(self):
        with patch.object(G, '_drain', return_value=(-1, [])):
            with self.assertRaisesRegex(ValueError, 'không cập nhật'): self.write()
        self.cn.rollback.assert_called_once(); self.cn.commit.assert_not_called()

    @override_settings(PMV_GHI_KK=False)
    def test_kk_safety_switch_blocks_before_connection(self):
        with self.assertRaises(G.PmvBlocked): self.write(target='kk')
        G._connect_dich.assert_not_called()

    def test_cannot_increase_total(self):
        with self.assertRaisesRegex(ValueError, 'cố định'):
            G.pmv_deposit_money(self.h, Q(1000), Q(300), till_id='T1', tag='test', target='sandbox')
        G._connect_dich.assert_not_called()
