from types import SimpleNamespace
from unittest.mock import patch

from django.test import TestCase, RequestFactory
from django.template.loader import render_to_string

from apps.pos.invoice_display import compact
from apps.pos import views


def invoice(trn, amount=100, **extra):
    return dict(TrnID=trn, BillCode=trn, loai='THAU', SoTien=amount,
                TienMua=amount, CardPay=-20, GoldDesc='Dẻ 9999', IsDel='0', Status='C', **extra)


class InvoiceDisplayTests(TestCase):
    def test_group_sum_and_no_heuristic_or_mutation(self):
        rows = [invoice('A'), invoice('B', 200), invoice('C')]
        result = compact(rows, [SimpleNamespace(pk=1, trn_ids=['A', 'B'])])
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]['SoTien'], 300)
        self.assertEqual(result[0]['tien_ck'], 40)
        self.assertEqual(len(result[0]['thau_lines']), 2)
        self.assertEqual(rows[0]['SoTien'], 100)
        self.assertFalse(result[0]['partial_group'])

    def test_partial_group_does_not_reintroduce_filtered_money(self):
        result = compact([invoice('A')], [SimpleNamespace(pk=1, trn_ids=['A', 'B'])])
        self.assertTrue(result[0]['partial_group'])
        self.assertEqual(result[0]['SoTien'], 100)

    def test_reopened_invoice_belongs_only_to_latest_group(self):
        groups = [SimpleNamespace(pk=2, trn_ids=['A', 'C']), SimpleNamespace(pk=1, trn_ids=['A', 'B'])]
        result = compact([invoice('A'), invoice('B'), invoice('C')], groups)
        self.assertEqual([len(r['members']) for r in result], [2, 1])
        self.assertFalse(result[1]['partial_group'])

    def test_mixed_status_and_sale_support(self):
        b = invoice('B')
        b['IsDel'] = '1'
        sale = invoice('S')
        sale.update(loai='BAN_DOI', CardPay=50)
        result = compact([invoice('A'), b, sale], [SimpleNamespace(pk=1, trn_ids=['A', 'B'])], {'S': 'Hỗ trợ'})
        self.assertTrue(result[0]['mixed_status'])
        self.assertEqual(result[1]['EmpSupName'], 'Hỗ trợ')
        self.assertEqual(result[1]['tien_ck'], 50)

    def test_template_group_actions_and_red_payout(self):
        rows = compact([invoice('A'), invoice('B')], [SimpleNamespace(pk=1, trn_ids=['A', 'B'])])
        html = render_to_string('pos/_hoa_don_bang.html', {'rows': rows, 'nguon_kk': True})
        self.assertNotIn('Nhóm thâu ·', html)
        self.assertIn('hd-negative', html)
        self.assertIn('⚡40', html)
        self.assertNotIn('Từng phiếu', html)
        self.assertIn('nguon=kk', html)

    def test_buyback_preview_uses_group_and_requested_source(self):
        request = RequestFactory().get('/', {'trn_id': 'A', 'loai': 'THAU', 'nguon': 'hist'})
        group = SimpleNamespace(pk=1, trn_ids=['A', 'B'])
        with patch('apps.pos.invoice_display.groups_for', return_value=[group]), \
             patch('apps.pos.views.PmvClient') as client, \
             patch('apps.pos.views.B.phieu_thau', side_effect=[invoice('A'), invoice('B')]) as read, \
             patch('apps.pos.views_thau._phieu_ctx', return_value={'so_dong': 2}), \
             patch('apps.pos.views.S.thong_tin_tiem', return_value={}):
            response = views.hoa_don_chi_tiet(request)
        self.assertEqual(response.status_code, 200)
        client.assert_called_once_with('hist', tag='hd_thau_preview')
        self.assertEqual([c.args[0] for c in read.call_args_list], ['A', 'B'])
        self.assertIn('Bill 110mm', response.content.decode())
