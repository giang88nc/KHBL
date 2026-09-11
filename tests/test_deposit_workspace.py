import datetime as dt
from decimal import Decimal
from unittest.mock import MagicMock

from django.core.cache import cache
from django.test import SimpleTestCase
from django.utils import timezone

from apps.pos import deposit_workspace as W


class WorkspaceTests(SimpleTestCase):
    def rows(self, *headers, items=None):
        return W.classify(W.prepare({'headers': list(headers), 'items': items or {}}, today=dt.date(2026,9,11)),
                          today=dt.date(2026,9,11))

    def test_vendor_paid_or_used_is_not_delivery_confirmation(self):
        rows = self.rows({'TrnID':'TRC1','Status':'C','ShopID':'S1'}, {'TrnID':'TRC2','Status':'C','ShopID':''})
        self.assertEqual(rows[0]['fulfilment'], 'unknown')
        self.assertTrue(rows[0]['active'])
        self.assertEqual(rows[1]['fulfilment'], 'delivered')
        self.assertEqual(len(W.paginate(rows, {'tab':'delivered'})['rows']), 1)

    def test_late_finished_order_never_becomes_open_task(self):
        row = self.rows({'TrnID':'TRC1','Status':'C','Description':'HEN:2026-08-01 | x | 0'})[0]
        self.assertFalse(row['overdue'])
        self.assertFalse(row['needs_call'])
        self.assertFalse(row['callback_due'])
        self.assertIsNone(row['estimate'])
        self.assertIsNone(row['delivered_at'])

    def test_matching_cash_is_not_proof_of_reconciliation(self):
        row = self.rows({'TrnID':'TRC1','Status':'W','TienCoc':500,'CashPay':500})[0]
        self.assertTrue(row['needs_money'])
        self.assertIsNone(row['money_balance'])

    def test_pending_priority_puts_old_overdue_first(self):
        rows = self.rows({'TrnID':'TRC1','Status':'R','Description':'HEN:2026-09-25 | x | 0'},
                         {'TrnID':'TRC2','Status':'W','Description':'HEN:2026-09-01 | x | 0'},
                         {'TrnID':'TRC3','Status':'W','Description':'HEN:2026-09-08 | x | 0'})
        self.assertEqual([r['TrnID'] for r in W.sorted_rows(rows,'priority','pending')], ['TRC2','TRC3','TRC1'])

    def test_stock_conflict_ignores_closed_orders(self):
        items = {key:[{'Mode':'stock','ProductCode':'A1','ProductDesc':'Nhẫn'}] for key in ['TRC1','TRC2','TRC3']}
        rows = self.rows(*[{'TrnID':key,'Status':state} for key,state in [('TRC1','W'),('TRC2','R'),('TRC3','C')]], items=items)
        self.assertTrue(rows[0]['hold_conflict'])
        self.assertTrue(rows[1]['hold_conflict'])
        self.assertFalse(rows[2]['hold_conflict'])

    def test_mine_without_mapping_returns_empty_not_all(self):
        rows = self.rows({'TrnID':'TRC1','Status':'W','EmpID':''})
        self.assertEqual(W.paginate(rows, {'tab':'all','mine':True},emp_id='')['stats']['total'],0)

    def test_aging_only_counts_open_and_preserves_recorded_amount(self):
        rows = self.rows({'TrnID':'TRC1','Status':'W','TrnDate':dt.date(2026,5,1),'TienCoc':Decimal('100.001')},
                         {'TrnID':'TRC2','Status':'C','TrnDate':dt.date(2026,5,1),'TienCoc':500})
        report = W.operations_report(rows)
        self.assertEqual(report['bands'][3]['count'],1)
        self.assertEqual(report['bands'][3]['amount'],Decimal('100.001'))

    def test_cache_is_target_scoped_and_never_returns_mutable_shared_rows(self):
        cache.clear()
        c = MagicMock(target='sandbox')
        c.query.side_effect = [[{'TrnID':'TRC1'}],[],[],[]]
        W.read_snapshot(c)['headers'][0]['TrnID'] = 'changed'
        self.assertEqual(W.read_snapshot(c)['headers'][0]['TrnID'],'TRC1')
        self.assertEqual(c.query.call_count,4)
        other = MagicMock(target='kk'); other.query.side_effect = [[],[],[],[]]
        self.assertEqual(W.read_snapshot(other)['headers'],[])
        cache.clear()
