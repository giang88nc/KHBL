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


class DepositValidationTests(TestCase):
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

    def test_stock_code_required_and_metadata_round_trip(self):
        data = dict(Mode='stock', ProductDesc='Nhẫn', GoldCode='18K', SL=1,
                    TotalWeight='100', DiamondWeight='0', TaskPrice='0', Notes='Ni 12 | khắc tên')
        form = D.OrderLineForm(data)
        form.fields['GoldCode'].choices = [('18K', '18K')]
        self.assertFalse(form.is_valid())
        self.assertIn('ProductCode', form.errors)
        form = D.OrderLineForm({**data, 'ProductCode': 'SP001'})
        form.fields['GoldCode'].choices = [('18K', '18K')]
        self.assertTrue(form.is_valid(), form.errors)
        decoded = D.O.item_info(form.cleaned_data)
        self.assertEqual(decoded['ProductCode'], 'SP001')
        self.assertEqual(decoded['Notes'], data['Notes'])
        self.assertEqual(decoded['Mode'], 'stock')

    def test_order_requires_item_and_explicit_deposit(self):
        formset = D.LineSet({'items-TOTAL_FORMS': '0', 'items-INITIAL_FORMS': '0'}, prefix='items')
        self.assertFalse(formset.is_valid())
        form = D.DepositForm({'CustID': 'K1', 'EmpID': 'E1'})
        self.assertFalse(form.is_valid())
        self.assertIn('TienCoc', form.errors)

    def test_unavailable_stock_is_rejected_before_write(self):
        c = MagicMock()
        c.query.side_effect = [[{'CustID': 'K1'}], []]
        with self.assertRaisesRegex(ValueError, 'không còn sẵn'):
            D.save(c, {'CustID': 'K1'}, [{'Mode': 'stock', 'ProductCode': 'SP001'}], None)
        c.call.assert_not_called()

    def test_listing_batches_item_summary_without_multiplying_deposit(self):
        c = MagicMock(target='sandbox')
        c.query.side_effect = [[{'TrnID': 'TRC1', 'Status': 'W', 'TienCoc': 100}],
                              [{'TrnID': 'TRC1', 'ProductDesc': 'Nhẫn', 'Notes': 'SP:A1 | Ni 12'},
                               {'TrnID': 'TRC1', 'ProductDesc': 'Dây', 'Notes': 'Đặt riêng'}], [], []]
        result = D.listing(c, {'tab': 'all', 'refresh': True}, 1)
        self.assertEqual(result['stats']['amount'], 100)
        self.assertEqual(result['rows'][0]['item_summary'], 'Nhẫn · Dây')
        self.assertEqual(result['rows'][0]['stock_count'], 1)
        self.assertEqual(result['rows'][0]['new_count'], 1)
        self.assertEqual(c.query.call_count, 4)

    def test_xml_preserves_unicode_and_escapes_and_excludes_deleted(self):
        row = dict(ProductDesc='Nhẫn <A> & B', GoldCode='18K', SL=2, TotalWeight=Decimal('100.2'),
                   DiamondWeight=Decimal('0.2'), GoldWeight=Decimal('100'), TaskPrice=Decimal('250000'), Size='', Notes='')
        root = ElementTree.fromstring(D.xml_lines([row, {**row, 'DELETE': True}], 'TDC1'))
        self.assertEqual(len(root), 1)
        self.assertEqual(root[0].findtext('ProductDesc'), row['ProductDesc'])
        self.assertEqual(root[0].findtext('TaskPrice'), '250000')
        self.assertEqual(root[0].findtext('TrnID'), 'TDC1')

    def test_legacy_order_keeps_estimated_gold_weight_without_total(self):
        f = D.OrderLineForm({'ProductDesc': 'Nhẫn đặt', 'GoldCode': '18K', 'SL': '1',
                            'TotalWeight': '0', 'DiamondWeight': '0', 'GoldWeight': '2', 'TaskPrice': '0'})
        f.fields['GoldCode'].choices = [('18K', '18K')]
        self.assertTrue(f.is_valid(), f.errors)
        self.assertEqual(f.cleaned_data['GoldWeight'], Decimal('2'))

    def test_order_filter_does_not_multiply_header_totals(self):
        c = MagicMock(target='sandbox')
        c.query.side_effect = [[{'TrnID': 'TRC1', 'Status': 'W', 'TienCoc': 100, 'CustName': "a%'_"}], [], [], []]
        result = D.listing(c, {'tab': 'all', 'q': "a%'_", 'refresh': True}, '999')
        self.assertEqual(result['stats']['amount'], 100)
        self.assertEqual(result['pager'].number, 1)
        self.assertNotIn("a%'_", c.query.call_args_list[0].args[0])

    def test_reference_description_round_trip_with_pipes(self):
        parsed = D.O.parse_description('HEN:2026-09-25 | Đã gọi | khách hẹn chiều | 3500000.000')
        self.assertEqual(parsed['promise_date'], dt.date(2026, 9, 25))
        self.assertEqual(parsed['note'], 'Đã gọi | khách hẹn chiều')
        self.assertEqual(parsed['estimate'], Decimal('3500000'))
        self.assertEqual(D.O.parse_description(D.O.pack_description(parsed['promise_date'], parsed['note'], parsed['estimate'])), parsed)
        malformed = 'HEN:2026-02-31 | ghi chú'
        self.assertEqual(D.O.parse_description(malformed)['note'], malformed)

    def test_order_status_and_deadline_do_not_mark_finished_orders_overdue(self):
        row = {'TrnID': 'TRC001', 'Status': 'R', 'TrnDate': dt.datetime(2026, 9, 1), 'Description': 'HEN:2026-09-10 | Đã gọi | 0'}
        result = D.O.enrich(row.copy(), today=dt.date(2026, 9, 11))
        self.assertEqual(result['state_name'], 'Hàng sẵn sàng')
        self.assertEqual(result['due_text'], 'Quá hẹn 1 ngày')
        self.assertEqual(result['age_days'], 10)
        for state in ['C', 'D']:
            self.assertEqual(D.O.enrich({**row, 'Status': state}, today=dt.date(2026, 9, 11))['due_state'], 'done')
        result = D.O.enrich({**row, 'Description': '', 'UserID_Upd': '2026-09-11'}, today=dt.date(2026, 9, 11))
        self.assertEqual(result['due_text'], 'Hẹn hôm nay')

    def test_promise_and_estimate_pack_without_losing_note(self):
        form = D.DepositForm({'CustID': 'K1', 'EmpID': 'E1', 'TienCoc': '500000', 'Description': 'Khách gọi lại',
                              'PromiseDate': '2026-09-25', 'Estimate': '1200000'})
        form.fields['EmpID'].choices = [('E1', 'Nhân viên')]
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['Description'], 'HEN:2026-09-25 | Khách gọi lại | 1200000')

    def test_legacy_weights_are_read_without_vendor_unit_conversion(self):
        c = MagicMock()
        D.lines(c, 'TRC001')
        self.assertIn("ISNULL(h.ShopID,'')=''", c.query.call_args.args[0])
        self.assertIn('dbo.fun_GetHS()', c.query.call_args.args[0])
        self.assertTrue(D.O.is_mobile_order({'TrnID': 'TRC001', 'ShopID': None}))
        self.assertFalse(D.O.is_mobile_order({'TrnID': 'TRC001', 'ShopID': 'S1'}))


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
        for name, args in [('dat_coc', []), ('dat_coc_report', []), ('dat_coc_add', []), ('dat_coc_customers', []), ('dat_coc_products', []),
                           ('dat_coc_popup', ['TDC001', 'view']), ('dat_coc_popup', ['TDC001', 'delete'])]:
            self.assertEqual(self.client.get(reverse('pos:' + name, args=args)).status_code, 403)
        UserModuleAccess.objects.create(user=self.user, module='DAT_COC', can_view=True)
        self.assertEqual(self.client.get(reverse('pos:dat_coc_add')).status_code, 403)

    def test_stock_lookup_filters_inventory_and_scales_mobile_weights(self):
        self.c.query.return_value = [dict(ProductCode='SP001', ProductDesc='Nhẫn', GoldCode='18K',
                                         TotalWeight=Decimal('125'), DiamondWeight=Decimal('25'),
                                         RingSize='12', WeightUnit='L')]
        response = self.client.get(reverse('pos:dat_coc_products'), {'q': 'SP', 'units': 'mobile'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Decimal(response.json()['rows'][0]['GoldWeight']), Decimal('1'))
        self.assertIn("p.Status='I'", self.c.query.call_args.args[0])

    def test_list_and_tabs_render_without_fake_totals(self):
        with patch.object(D, 'listing', return_value={'rows': [], 'stats': {'total': 0}, 'pager': D.Paginator([], 30).get_page(1)}):
            response = self.client.get(reverse('pos:dat_coc'))
            self.assertContains(response, 'ĐẶT-CỌC')
            for _, label in D.O.TABS:
                self.assertContains(response, label)
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

    def test_recorded_payment_blocks_hard_delete_and_locks_deposit_field(self):
        h = {**self.h, 'CashPay': self.h['TienCoc']}
        with patch.object(D, 'header', return_value=h), patch.object(D, 'lines', return_value=[]):
            response = self.client.post(reverse('pos:dat_coc_popup', args=['TDC001','delete']), {})
            self.assertContains(response, 'không xóa phiếu')
            response = self.client.get(reverse('pos:dat_coc_popup', args=['TDC001','edit']))
            self.assertTrue(response.context['form'].fields['TienCoc'].widget.attrs['readonly'])
        self.c.call.assert_not_called()

    def test_csv_is_scoped_to_creation_dates_and_escapes_formula(self):
        snapshot = {'headers':[{**self.h,'CustName':'=HYPERLINK("bad")'}, {**self.h,'TrnID':'OLD','TrnDate':dt.date(2026,1,1)}], 'items':{}, 'as_of':D.timezone.now()}
        with patch.object(D.W,'read_snapshot',return_value=snapshot):
            response = self.client.get(reverse('pos:dat_coc_report'), {'export':'csv','start':'2026-09-01'})
            text = response.content.decode('utf-8-sig')
            self.assertIn("'=HYPERLINK",text)
            self.assertNotIn('2026-01-01',text)
            self.assertIn('Cọc ghi trên phiếu',text)

    def test_edit_initial_decimals_fit_form_precision(self):
        row = dict(ProductDesc='Nhẫn', GoldCode='18K', SL=None, TotalWeight=Decimal('0E-25'),
                   DiamondWeight=Decimal('0E-25'), GoldWeight=Decimal('2.0000000000000000000000000'),
                   TaskPrice=None, Size='', Notes='')
        with patch.object(D, 'header', return_value={**self.h, 'TienCoc': Decimal('1000000.000')}), patch.object(D, 'lines', return_value=[row]):
            response = self.client.get(reverse('pos:dat_coc_popup', args=['TDC001', 'edit']))
            self.assertEqual(str(response.context['form'].initial['TienCoc']), '1000000.000')
            item = response.context['line_set'].initial[0]
            self.assertEqual(item['SL'], 1)
            self.assertEqual(str(item['GoldWeight']), '2.00000000')

    def test_create_form_and_idempotency(self):
        self.c.query.side_effect = lambda sql, *a: ([{'EmpID': 'NV1', 'EmpName': 'Nhân viên'}] if 'T_EMPLOYEE' in sql
                                                   else [{'GoldCode': '18K'}] if 'I_GOLD' in sql else [])
        response = self.client.get(reverse('pos:dat_coc_add'))
        self.assertContains(response, 'items-__prefix__-GoldCode')
        token = response.context['token']
        payload = {'token': token, 'CustID': 'KH001', 'EmpID': 'NV1', 'TienCoc': '1000000',
                   'Description': '', 'items-TOTAL_FORMS': '1', 'items-INITIAL_FORMS': '0',
                   'items-0-Mode': 'new', 'items-0-ProductDesc': 'Nhẫn đặt', 'items-0-GoldCode': '18K',
                   'items-0-SL': '1', 'items-0-TotalWeight': '0', 'items-0-DiamondWeight': '0', 'items-0-TaskPrice': '0'}
        with patch.object(D, 'pmv_user_for_web_user', return_value=SimpleNamespace(user_id='U1', shop_id='S1')), patch.object(D, 'save', return_value='TDC001') as save:
            response = self.client.post(reverse('pos:dat_coc_add'), payload)
            self.assertIn('depositSaved', response.headers['HX-Trigger'])
            response = self.client.post(reverse('pos:dat_coc_add'), payload)
            self.assertContains(response, 'đã được xử lý')
            self.assertEqual(save.call_count, 1)
            self.assertEqual(DepositSubmission.objects.filter(completed=True).count(), 1)

    def test_order_tab_controls_status_and_renders_compact_rows(self):
        row = D.decorate({**self.h, 'TrnID': 'TRC001', 'Status': 'R', 'line_count': 2,
                          'EmpName': 'Nhân viên thử', 'Description': 'HEN:2026-09-25 | Đã gọi khách | 1200000'}, order=True)
        with patch.object(D, 'listing', return_value={'rows': [row], 'stats': {'total': 1, 'ready': 1}, 'pager': D.Paginator([row], 30).get_page(1)}) as listing:
            response = self.client.get(reverse('pos:dat_coc'), {'tab': 'ready', 'status': 'W'})
            self.assertEqual(listing.call_args.args[1]['status'], 'R')
            self.assertEqual(listing.call_args.args[1]['sort'], 'priority')
            self.assertContains(response, 'Hàng sẵn sàng')
            self.assertContains(response, 'Đã gọi khách')
            self.assertContains(response, 'Nhân viên thử')
            self.assertContains(response, '25/09/2026')
            self.assertContains(response, 'XEM')
            self.assertContains(response, 'SỬA')
            self.assertNotContains(response, 'HEN:2026')
