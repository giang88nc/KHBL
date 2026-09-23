from types import SimpleNamespace
from unittest.mock import patch
from django.test import SimpleTestCase
from django.template.loader import render_to_string
from apps.pos.mobile_customers import hydrate_customers


class MobileIdentityTests(SimpleTestCase):
    def test_bill_search_normalization(self):
        from apps.pos.money_flow_views import normalize_mobile_bill_key
        for key in ('26-09-17-000040', '260917000040', '260917040'):
            self.assertEqual(normalize_mobile_bill_key(key),'26-09-17-000040')
        for key in ('2609-974-3435','26090097403435','260917','269917040'):
            self.assertEqual(normalize_mobile_bill_key(key),key)

    def test_staff_toggle_and_removed_hints(self):
        html=render_to_string('pos/_money_flow_mobile_in.html', {'rows':[], 'mine':True})
        self.assertIn('aria-pressed="true"',html)
        self.assertIn('name="mine" value="1"',html)
        self.assertNotIn('giờ gần nhất ·',html)
        self.assertNotIn('Chưa có giao dịch IN',html)

    @patch('apps.pos.mobile_customers.services.client')
    def test_customer_pair_comes_from_matching_id(self, client):
        client.return_value.query.return_value = [
            {'CustID': 'C1', 'CustName': 'Anh Duy', 'Phone': '0357215151'}]
        flow = SimpleNamespace(customer_id='C1', customer_name='', phone_label='')
        hydrate_customers([flow])
        self.assertEqual((flow.customer_name, flow.phone_label), ('Anh Duy', '0357215151'))

    def test_created_qr_is_not_paid_tick(self):
        row = SimpleNamespace(pk=1, source_id='S1', source_bill_code='P1', customer_name='Test',
            phone_label='0357215151', bank_instruction=True, qr_paid=False)
        html = render_to_string('pos/_money_flow_mobile_in.html', {'rows': [row]})
        self.assertNotIn('<span>✓</span>', html)
        self.assertNotIn('tel:', html)
        self.assertNotIn('disabled title=', html)
        row.qr_paid = True
        html = render_to_string('pos/_money_flow_mobile_in.html', {'rows': [row]})
        self.assertIn('<span>✓</span>', html)
        self.assertIn('disabled title=', html)
        self.assertIn('qr-ticket__trakhach',html)
        self.assertIn('Đã nhận đủ',html)
        self.assertNotIn('Chưa xác nhận',html)

    def test_pawn_ticket_has_invoice_button(self):
        row=SimpleNamespace(pk=7,source_system='KHCD',service='REDEEM',mobile_group='PAWN',mobile_label='Cầm đồ',
            source_id='3435',source_bill_code='26090097703435',customer_name='Khách',phone_label='0901234567',
            expected_amount=1000,cash_amount=1000,transfer_amounts=[],time_label='18:00',staff_label='NV',
            customer_change=None,customer_received_exact=False,qr_paid=False)
        html=render_to_string('pos/_money_flow_mobile_in.html',{'rows':[row]})
        self.assertIn('aria-label="HĐ phiếu 26090097703435"',html)
        self.assertIn('/banle/mobile/invoice/7/',html)
