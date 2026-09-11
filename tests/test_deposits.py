import datetime as dt
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch, MagicMock
from xml.etree import ElementTree

from django.contrib.auth import get_user_model
from django.core import signing
from django.test import TestCase, SimpleTestCase
from django.urls import reverse

from apps.pos import deposits as D
from apps.pos.models import DepositSubmission
from apps.pmv.models import UserModuleAccess


class DepositValidationTests(SimpleTestCase):
    def test_bad_money_and_dates(self):
        self.assertFalse(D.DepositForm({'TienCoc': '-1'}).is_valid())
        self.assertFalse(D.FilterForm({'tab': 'money', 'start': '2026-09-11', 'end': '2026-09-01'}).is_valid())
        self.assertFalse(D.FilterForm({'tab': 'orders', 'end': '9999-12-31'}).is_valid())

    def test_diamond_cannot_exceed_total(self):
        f = D.OrderLineForm({'ProductDesc': 'Nhẫn', 'GoldCode': '18K', 'SL': 1,
                            'TotalWeight': '1', 'DiamondWeight': '2', 'TaskPrice': '1000'})
        f.fields['GoldCode'].choices = [('18K', '18K')]
        self.assertFalse(f.is_valid())
        self.assertIn('Trọng lượng hột', str(f.errors))

    def test_xml_preserves_unicode_and_escapes_and_excludes_deleted(self):
        row = dict(ProductDesc='Nhẫn <A> & B', GoldCode='18K', SL=2, TotalWeight=Decimal('100.2'),
                   DiamondWeight=Decimal('0.2'), GoldWeight=Decimal('100'), TaskPrice=Decimal('250000'), Size='', Notes='')
        root = ElementTree.fromstring(D.xml_lines([row, {**row, 'DELETE': True}], 'TDC1'))
        self.assertEqual(len(root), 1)
        self.assertEqual(root[0].findtext('ProductDesc'), row['ProductDesc'])
        self.assertEqual(root[0].findtext('TaskPrice'), '250000')
        self.assertEqual(root[0].findtext('TrnID'), 'TDC1')

    def test_order_filter_does_not_multiply_header_totals(self):
        c = MagicMock()
        c.query.side_effect = [[{'total': 1, 'amount': 100, 'drafts': 1, 'available': 0}], []]
        D.listing(c, {'tab': 'orders', 'q': "a%'_"}, '999')
        query, params = c.query.call_args_list[0].args
        self.assertIn('EXISTS (SELECT', query)
        self.assertNotIn("a%'_", query)
        self.assertIn('[%]', params[0])
        self.assertEqual(c.query.call_args_list[1].args[1][-2:], [1, 1])


class DepositViewTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_superuser('dc-admin', password='test')
        self.user = get_user_model().objects.create_user('dc-user', password='test')
        self.client.force_login(self.admin)
        self.c = MagicMock(target='sandbox')
        self.c.sys_param.return_value = 'Ly'
        self.c.query.return_value = []
        self.h = dict(TrnID='TDC001', BillCode='DC-001', TrnDate=dt.datetime(2026, 9, 11),
                      TrnDateTime_Upd=dt.datetime(2026, 9, 11, 10), Status='W', CustID='KH001',
                      EmpID='NV1', TienCoc=Decimal('1000000'), Description='Ghi chú <script>', CustName='Khách thử', Phone='')
        self.patchers = [patch.object(D, 'PmvClient', return_value=self.c),
                         patch('apps.pos.context_processors.S.thong_tin_tiem', return_value={}),
                         patch('apps.pos.context_processors.gateway.mo_ta_dich', return_value='Bản thử')]
        for p in self.patchers: p.start(); self.addCleanup(p.stop)

    def test_permissions_are_enforced_on_all_routes(self):
        self.client.force_login(self.user)
        for name, args in [('dat_coc', []), ('dat_coc_add', []), ('dat_coc_customers', []),
                           ('dat_coc_popup', ['TDC001', 'view']), ('dat_coc_popup', ['TDC001', 'delete'])]:
            self.assertEqual(self.client.get(reverse('pos:' + name, args=args)).status_code, 403)
        UserModuleAccess.objects.create(user=self.user, module='DAT_COC', can_view=True)
        self.assertEqual(self.client.get(reverse('pos:dat_coc_add')).status_code, 403)

    def test_list_and_tabs_render_without_fake_totals(self):
        with patch.object(D, 'listing', return_value={'rows': [], 'stats': {'total': 0}, 'pager': D.Paginator([], 30).get_page(1)}):
            response = self.client.get(reverse('pos:dat_coc'))
            self.assertContains(response, 'ĐẶT-CỌC')
            self.assertContains(response, 'Tiền cọc')
            self.assertContains(response, 'Đặt hàng')
            self.assertContains(response, 'datcoc.svg')
        with patch.object(D, 'listing', side_effect=RuntimeError('offline')):
            response = self.client.get(reverse('pos:dat_coc'), HTTP_HX_REQUEST='true')
            self.assertContains(response, 'Không kết nối')
            self.assertNotContains(response, 'Tổng tiền cọc trên phiếu')

    def test_detail_escapes_notes_and_locked_delete_has_no_submit(self):
        with patch.object(D, 'header', return_value={**self.h, 'Status': 'C'}), patch.object(D, 'lines', return_value=[]):
            response = self.client.get(reverse('pos:dat_coc_popup', args=['TDC001', 'view']))
            self.assertContains(response, 'Ghi chú &lt;script&gt;')
            self.assertNotContains(response, 'Ghi chú <script>')
            response = self.client.get(reverse('pos:dat_coc_popup', args=['TDC001', 'delete']))
            self.assertContains(response, 'Chỉ phiếu Lưu tạm')
            self.assertNotContains(response, 'Xác nhận xóa phiếu')

    def test_stale_version_and_changed_target_block_delete(self):
        for token_data in [{**D.version(self.c, self.h), 'stamp': 'old'}, {**D.version(self.c, self.h), 'target': 'kk'}]:
            with patch.object(D, 'header', return_value=self.h), patch.object(D, 'lines', return_value=[]):
                response = self.client.post(reverse('pos:dat_coc_popup', args=['TDC001', 'delete']),
                                            {'token': signing.dumps(token_data, salt='datcoc')})
                self.assertContains(response, 'đã thay đổi')
        self.c.call.assert_not_called()

    def test_create_form_and_idempotency(self):
        self.c.query.side_effect = lambda sql, *a: ([{'EmpID': 'NV1', 'EmpName': 'Nhân viên'}] if 'T_EMPLOYEE' in sql
                                                   else [{'GoldCode': '18K'}] if 'I_GOLD' in sql else [])
        response = self.client.get(reverse('pos:dat_coc_add'))
        self.assertContains(response, 'items-__prefix__-GoldCode')
        token = response.context['token']
        payload = {'token': token, 'CustID': 'KH001', 'EmpID': 'NV1', 'TienCoc': '1000000',
                   'Description': '', 'items-TOTAL_FORMS': '0', 'items-INITIAL_FORMS': '0'}
        with patch.object(D, 'pmv_user_for_web_user', return_value=SimpleNamespace(user_id='U1', shop_id='S1')), patch.object(D, 'save', return_value='TDC001') as save:
            response = self.client.post(reverse('pos:dat_coc_add'), payload)
            self.assertIn('depositSaved', response.headers['HX-Trigger'])
            response = self.client.post(reverse('pos:dat_coc_add'), payload)
            self.assertContains(response, 'đã được xử lý')
            self.assertEqual(save.call_count, 1)
            self.assertEqual(DepositSubmission.objects.filter(completed=True).count(), 1)
