import json
from decimal import Decimal
from xml.etree import ElementTree
from unittest.mock import MagicMock

from django.test import SimpleTestCase, TestCase

from apps.pos import deposits as D, ban_coc as B
from apps.pos.models import GoldBill, DepositOrderState


class ProductDescriptionTests(SimpleTestCase):
    def test_json_roundtrip_preserves_special_characters_and_plain_notes(self):
        for code, mode in [('9N60017716', 'stock'), ('Khách đặt', 'new')]:
            row = dict(Mode=mode, ProductCode=code, ProductDesc='Nhẫn "A" & <B> 😀',
                       Notes='SP: đây là ghi chú, không phải mã', GoldCode='18K', SL=1,
                       TotalWeight=1, DiamondWeight=0, GoldWeight=1, TaskPrice=0, Size='12')
            xml = ElementTree.fromstring(D.xml_lines([row], 'TDC260900000001'))
            raw = {child.tag: child.text for child in xml[0]}
            self.assertEqual(json.loads(raw['ProductDesc']), {code: row['ProductDesc']})
            self.assertEqual(raw['Notes'], row['Notes'])
            decoded = D.O.item_info(raw)
            self.assertEqual((decoded['ProductCode'], decoded['ProductDesc'], decoded['Mode']),
                             (code, row['ProductDesc'], mode))
            self.assertEqual(D.O.item_info(decoded), decoded)

    def test_legacy_stock_is_converted_without_losing_note(self):
        old = {'ProductDesc': 'Nhẫn', 'Notes': 'SP: SP001 | Ni 12'}
        decoded = D.O.item_info(old)
        self.assertEqual(json.loads(D.O.product_description(old)), {'SP001': 'Nhẫn'})
        self.assertEqual(decoded['Notes'], 'Ni 12')
        self.assertEqual(D.O.item_info({'ProductDesc': '{không phải JSON', 'Notes': ''})['Mode'], 'new')

    def test_form_checks_json_capacity_before_vendor_write(self):
        row = dict(Mode='new', ProductDesc='😀' * 250, GoldCode='18K', SL=1,
                   TotalWeight=1, DiamondWeight=0, GoldWeight=1, TaskPrice=0)
        form = D.OrderLineForm(row)
        form.fields['GoldCode'].choices = [('18K', '18K')]
        self.assertFalse(form.is_valid())
        self.assertIn('500', str(form.errors['ProductDesc']))

    def test_fresh_invoice_match_uses_json_code_and_exempts_custom_order(self):
        c = MagicMock()
        c.query.return_value = [{'TrnID': 'P1', 'ProductDesc': '{"SP001":"Nhẫn"}', 'Notes': ''}]
        self.assertIn('SP001 chưa có', B.kiem_san_pham(c, ['P1'], []))
        self.assertEqual(B.kiem_san_pham(c, ['P1'], [{'row': {'ProductCode': 'SP001'}}]), '')
        c.query.return_value = [{'TrnID': 'P1', 'ProductDesc': '{"Khách đặt":"Nhẫn đặt"}', 'Notes': ''}]
        self.assertEqual(B.kiem_san_pham(c, ['P1'], []), '')


class DepositGoldBillTests(TestCase):
    def test_operational_data_lives_in_gold_bill_and_is_excluded_from_retail(self):
        retail = GoldBill.objects.create(trn_id='TRB1')
        deposit = DepositOrderState.objects.create(
            target='kk', trn_id='TDC260900000001', fulfilment='making',
            items=[{'key': 'item1', 'state': 'making'}], pricing={'total': '12000000'},
            payment_plan={'cash': '5000000', 'bank': '0'}, quote_amount=Decimal('12000000'))
        self.assertEqual(list(GoldBill.objects.values_list('pk', flat=True)), [retail.pk])
        stored = GoldBill.all_objects.get(pk=deposit.pk)
        self.assertEqual(stored.bill_kind, 'deposit')
        self.assertEqual(stored.items, deposit.items)
        self.assertEqual(stored.payment_plan, deposit.payment_plan)
        GoldBill.objects.update(is_del=True)
        deposit.refresh_from_db()
        self.assertFalse(deposit.is_del)

    def test_same_receipt_code_in_sandbox_and_kk_keeps_independent_metadata(self):
        for target in ('kk', 'sandbox'):
            DepositOrderState.objects.create(target=target, trn_id='TDC260900000001', note=target)
        self.assertEqual(DepositOrderState.objects.count(), 2)
        self.assertEqual(DepositOrderState.objects.get(target='kk', trn_id='TDC260900000001').note, 'kk')
