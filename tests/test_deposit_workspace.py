import datetime as dt
from decimal import Decimal
from unittest.mock import MagicMock

from django.core.cache import cache
from django.test import SimpleTestCase
from django.utils import timezone

from apps.pos import deposit_workspace as W


class WorkspaceTests(SimpleTestCase):
    def test_all_receipt_tabs_match_progress_not_creation_date(self):
        states=('waiting','new','ordering','crafting','ready','shipping','partial','unknown','delivered','applied','cancelled')
        rows=[]
        for state in states:
            row=self.appointment_row(dt.date(2026,9,15),created=dt.date(2025,1,1),state=state)
            row['TrnID']=state
            rows.append(row)
        rows=W.classify(rows)
        expected={'all':set(states)-{'delivered','applied','cancelled'},
                  'new':{'waiting','new'},'ordering':{'ordering','crafting'},'ready':{'ready'}}
        for tab,wanted in expected.items():
            with self.subTest(tab=tab):
                self.assertEqual({r['TrnID'] for r in W.paginate(rows,{'tab':tab})['rows']},wanted)
                self.assertEqual({r['TrnID'] for r in W.paginate(rows,{'tab':tab,'start':dt.date(2026,9,1),'end':dt.date(2026,9,30)})['rows']},wanted)
                self.assertEqual(W.paginate(rows,{'tab':tab,'start':dt.date(2026,10,1)})['rows'],[])

    def appointment_row(self, promise, created=None, state='ready'):
        row=self.rows({'TrnID':'TRC1','Status':'W','TrnDate':created or dt.date(2026,8,1)})[0]
        row.update(promise_date=promise,fulfilment=state)
        return row

    def test_date_range_filters_promise_not_creation_inclusively(self):
        data={'tab':'all','start':dt.date(2026,9,1),'end':dt.date(2026,9,30)}
        for date,expected in [(dt.date(2026,9,1),True),(dt.date(2026,9,30),True),
                              (dt.date(2026,8,31),False),(dt.date(2026,10,1),False),(None,False)]:
            with self.subTest(promise=date):
                self.assertEqual(W.matches(self.appointment_row(date),data),expected)
        self.assertFalse(W.matches(self.appointment_row(dt.date(2026,10,1),created=dt.date(2026,9,15)),data))

    def test_blank_and_one_sided_appointment_dates(self):
        row=self.appointment_row(None)
        self.assertTrue(W.matches(row,{'tab':'all','start':None,'end':None}))
        row['promise_date']=dt.date(2026,9,15)
        self.assertTrue(W.matches(row,{'start':dt.date(2026,9,1)}))
        self.assertTrue(W.matches(row,{'end':dt.date(2026,9,30)}))
        self.assertFalse(W.matches(row,{'start':dt.date(2026,9,16)}))
        self.assertFalse(W.matches(row,{'end':dt.date(2026,9,14)}))

    def test_appointment_range_applies_to_all_four_receipt_tabs(self):
        for tab,state in [('all','waiting'),('new','waiting'),('ordering','crafting'),('ready','ready')]:
            row=self.appointment_row(dt.date(2026,9,15),created=timezone.localdate(),state=state)
            data={'tab':tab,'start':dt.date(2026,9,1),'end':dt.date(2026,9,30)}
            self.assertEqual(len(W.paginate([row],data)['rows']),1)
            row['promise_date']=dt.date(2026,10,1)
            self.assertEqual(len(W.paginate([row],data)['rows']),0)

    def test_report_can_still_filter_creation_date_explicitly(self):
        row=self.appointment_row(dt.date(2026,10,1),created=dt.date(2026,9,15))
        self.assertTrue(W.matches(row,{'tab':'scope','date_field':'created','start':dt.date(2026,9,1),'end':dt.date(2026,9,30)}))

    def test_many_deposits_keep_original_receipts_and_show_same_sales_invoice(self):
        headers=[{'TrnID':pk,'BillCode':code,'CustID':'K1','ShopID':'S1','Status':'C',
                  'TienCoc':amount,'CashPay':amount,'CardPay':0,'TrnDate':dt.date(2026,8,1)}
                 for pk,code,amount in [('TDC1','26-08-01-000001',500),('TDC2','26-08-01-000002',300)]]
        funds=[{'TrnRefID':h['TrnID'],'Status':'P','TillID':'T1','TrnTotalAmount':h['TienCoc'],
                'detail_count':1,'amount_count':1,'cash_amount':h['TienCoc'],'bad_detail':0} for h in headers]
        links=[{'DatCocID':h['TrnID'],'TrnID':'TRB1','CustID':'K1','TienCoc':800,'Status':'C','kind':'sell',
                'invoice_bill_code':'26-09-10-000048','invoice_tx_count':1,'invoice_posted':1} for h in headers]
        rows=W.prepare({'headers':headers,'items':{},'funds':funds,'links':links})
        self.assertEqual([r['BillCode'] for r in rows],[h['BillCode'] for h in headers])
        self.assertTrue(all(r['money_balance']==0 and r['money_confirmed'] for r in rows))
        self.assertTrue(all(r['application_codes']=='26-09-10-000048' for r in rows))
        self.assertTrue(all(r['TrnDate']==dt.date(2026,8,1) for r in rows))
        self.assertTrue(W.matches(W.classify(rows)[0],{'tab':'scope','q':'26-09-10-000048'}))

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
