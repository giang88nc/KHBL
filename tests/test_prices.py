"""Kiểm tra dữ liệu giá, lịch sử, chống lặp/lạc hậu và đồng bộ lỗi trên DB riêng."""
from contextlib import nullcontext
from datetime import datetime
from decimal import Decimal
from unittest import TestCase as UnitTestCase
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import connection
from django.http import QueryDict
from django.test import TransactionTestCase, override_settings

from apps.pos import prices, price_sync
from apps.pos.models import PriceBatch, PriceDisplay
from apps.pmv import gateway


class ValidationTests(UnitTestCase):
    def test_amount_format_and_limits(self):
        self.assertEqual(prices.clean_amount('8.350.000', 'Giá'), 8350000)
        self.assertEqual(prices.clean_amount('8350000', 'Giá'), 8350000)
        for raw in ('', '-1', '0', '12.34', '1e9', '8,350,000', 'NaN', '1000000000000'):
            with self.subTest(raw=raw), self.assertRaises(prices.PriceError):
                prices.clean_amount(raw, 'Giá')

    def test_mssql_mapping_preserves_other_rates(self):
        original = [self.rate('18K', 'G'), self.rate('D18K', 'D'), self.rate('VND', 'C')]
        result = price_sync.build_rates(original, [{'gold_type': '610', 'buy': 8350000, 'sell': 8850000}])
        self.assertEqual(result[0]['BuyRate'], Decimal('8350'))
        self.assertEqual(result[1]['SellRate'], Decimal('8850'))
        self.assertEqual(result[2], original[2])
        self.assertNotEqual(original[0]['BuyRate'], result[0]['BuyRate'])
        xml = price_sync.xml_rates(result)
        self.assertIn('<TB_GIAVANG>', xml)
        self.assertIn('<TB_GIADE>', xml)
        self.assertIn('<TB_NGOAITE>', xml)
        self.assertIn('<BuyRate>8350.000</BuyRate>', xml)

    def test_unit_conversion_requires_explicit_bk_vt_unit(self):
        with self.assertRaises(prices.PriceError):
            price_sync.mssql_rate({'gold_type': 'BK', 'buy': 3750000}, 'buy')
        self.assertEqual(price_sync.mssql_rate({'gold_type': 'BK', 'unit': 'chỉ', 'buy': 3750000}, 'buy'), Decimal('1000.000'))
        self.assertEqual(price_sync.mssql_rate({'gold_type': 'VT', 'unit': 'gram', 'buy': 500000}, 'buy'), Decimal('500.000'))
        self.assertEqual(price_sync.mssql_rate({'gold_type': 'VT', 'unit': 'chỉ', 'buy': 1000000}, 'buy'), Decimal('266.667'))

    @staticmethod
    def rate(code, kind):
        return {'GoldCcy': code, 'ShopID': '', 'Type': kind, 'RateDate': '', 'RateTime': '',
                **{key: Decimal(1 if key in ('BuyRate', 'SellRate') else 0) for key in price_sync.RATE_FIELDS}}

    def test_missing_mapping_and_unsafe_source_fail_closed(self):
        with self.assertRaises(prices.PriceError):
            price_sync.build_rates([self.rate('18K', 'G')], [{'gold_type': '610', 'buy': 1, 'sell': 2}])
        row = self.rate('VND', 'C')
        row['ShopID'] = 'OTHER'
        with self.assertRaises(prices.PriceError):
            price_sync.build_rates([row], [])
        row = self.rate('D18K', 'D')
        row['POSellRate'] = 1
        with self.assertRaises(prices.PriceError):
            price_sync.build_rates([row], [])

    @override_settings(PMV_GHI_KK=False)
    def test_gateway_still_blocks_kk_before_connect(self):
        with patch('apps.pmv.gateway._audit'), patch('apps.pmv.gateway._connect_dich') as connect:
            with self.assertRaises(gateway.PmvBlocked):
                gateway.pmv_call('I_XRATE_Ins', {}, write=True, tag='test_price', target='kk')
            connect.assert_not_called()


class PriceSaveTests(TransactionTestCase):
    def test_png_export_preserves_bytes_and_downloads_as_attachment(self):
        from io import BytesIO
        from tempfile import TemporaryDirectory
        from PIL import Image
        png = BytesIO()
        Image.new('RGB', (20, 10), (152, 9, 9)).save(png, format='PNG')
        raw = png.getvalue()
        with TemporaryDirectory() as directory, override_settings(BASE_DIR=directory):
            response = self.client.post('/banle/bang-gia/luu-png/', raw, content_type='image/png')
            self.assertEqual(response.status_code, 200)
            download = self.client.get(response.json()['url'])
            self.assertEqual(b''.join(download.streaming_content), raw)
            self.assertIn('attachment;', download['Content-Disposition'])
            self.assertEqual(self.client.post('/banle/bang-gia/luu-png/', b'not a png', content_type='image/png').status_code, 400)

    def setUp(self):
        if connection.vendor != 'sqlite':
            self.skipTest('Chạy với --settings=config.settings.test_prices để cách ly dữ liệu thật')
        with connection.cursor() as c:
            c.execute('CREATE TABLE IF NOT EXISTS gold_prices (id INTEGER PRIMARY KEY AUTOINCREMENT, '
                      'gold_type VARCHAR(50), gold_name VARCHAR(50), buy DECIMAL, sell DECIMAL, '
                      'effective_at DATETIME, source VARCHAR(100), is_current INTEGER)')
            c.execute('DELETE FROM gold_prices')
            c.execute("INSERT INTO gold_prices VALUES (1,'610','Vàng 610',8350000,8850000,'2026-09-05 13:53:57','fixture',1)")
        self.user = get_user_model().objects.create(username='price_test')
        self.client.force_login(self.user)
        self.original_reader = prices.current_rows
        self.enter = self.patch('apps.pos.prices.save_lock', return_value=nullcontext())
        self.patch('apps.pos.prices.current_rows', side_effect=lambda lock=False: self.original_reader(False))
        self.patch('apps.pos.prices.gateway.dich_hien_tai', return_value='sandbox')
        self.sync = self.patch('apps.pos.price_sync.sync_prices')

    def patch(self, *args, **kwargs):
        mock = patch(*args, **kwargs)
        self.addCleanup(mock.stop)
        return mock.start()

    def form(self):
        rows = prices.decorate(prices.current_rows())
        key = str(rows[0]['id'])
        data = QueryDict(mutable=True)
        data.setlist('row_id', [key])
        data.update({'edit_token': prices.edit_token(rows, self.user.pk, 'sandbox'),
                     'buy_' + key: '8.400.000', 'sell_' + key: '8.900.000',
                     'position_' + key: '2', 'pinned_' + key: 'on'})
        return data

    def test_insert_history_pin_order_and_no_duplicate_post(self):
        form = self.form()
        batch = prices.save_prices(form, self.user)
        self.assertEqual(batch.status, 'synced')
        rows = prices.current_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(int(rows[0]['buy']), 8400000)
        self.assertNotEqual(rows[0]['id'], 1)
        with connection.cursor() as c:
            c.execute('SELECT buy,is_current FROM gold_prices WHERE id=1')
            self.assertEqual(c.fetchone(), (8350000, 0))
        display = PriceDisplay.objects.get(gold_type='610')
        self.assertTrue(display.pinned)
        self.assertEqual(display.position, 2)
        self.assertEqual(prices.save_prices(form, self.user).pk, batch.pk)
        self.assertEqual(PriceBatch.objects.count(), 1)
        self.sync.assert_called_once()

    def test_mssql_failure_keeps_mysql_and_retry_does_not_insert_again(self):
        self.sync.side_effect = RuntimeError('offline')
        with self.assertLogs('apps.pos.prices', level='ERROR'):
            batch = prices.save_prices(self.form(), self.user)
        self.assertEqual(batch.status, 'sync_failed')
        self.assertEqual(int(prices.current_rows()[0]['buy']), 8400000)
        self.sync.side_effect = None
        self.assertEqual(prices.retry_sync(batch.pk).status, 'synced')
        with connection.cursor() as c:
            c.execute('SELECT COUNT(*) FROM gold_prices')
            self.assertEqual(c.fetchone()[0], 2)

    def test_pmv_report_sync_creates_local_history_without_mssql_write(self):
        source = [{
            'gold_type': '610', 'gold_name': 'Vàng 610', 'buy': 8200000, 'sell': 8700000,
            'effective_at': datetime(2026, 9, 7, 8, 49, 24), 'source': 'PUBLIC_GOLD:ketoan',
        }]
        with patch('apps.pos.prices._pmv_report_source_rows', return_value=source):
            preview = prices.pmv_report_sync_preview()
            self.assertEqual(len(preview['changes']), 1)
            token = prices.pmv_report_sync_token(preview, self.user.pk)
            self.assertEqual(prices.apply_pmv_report_sync(token, self.user), 1)
        current = prices.current_rows()[0]
        self.assertEqual(int(current['buy']), 8200000)
        self.assertEqual(int(current['sell']), 8700000)
        with connection.cursor() as c:
            c.execute('SELECT source,is_current FROM gold_prices ORDER BY id')
            self.assertEqual(c.fetchall(), [('fixture', 0), ('pmv_report:PUBLIC_GOLD:ketoan', 1)])
        self.sync.assert_not_called()

    def test_stale_form_and_stale_retry_are_rejected(self):
        stale_form = self.form()
        self.sync.side_effect = RuntimeError('offline')
        with self.assertLogs('apps.pos.prices', level='ERROR'):
            old = prices.save_prices(self.form(), self.user)
        self.sync.side_effect = None
        with self.assertRaises(prices.PriceError):
            prices.save_prices(stale_form, self.user)
        prices.save_prices(self.form(), self.user)
        with self.assertRaises(prices.PriceError):
            prices.retry_sync(old.pk)

    def test_validation_rolls_back_all_rows(self):
        form = self.form()
        form['buy_1'] = '9.999.999'
        with self.assertRaises(prices.PriceError):
            prices.save_prices(form, self.user)
        self.assertEqual(prices.current_rows()[0]['id'], 1)
        self.assertFalse(PriceBatch.objects.exists())
        self.sync.assert_not_called()

    def test_target_change_rejected(self):
        form = self.form()
        with patch('apps.pos.prices.gateway.dich_hien_tai', return_value='kk'):
            with self.assertRaises(prices.PriceError):
                prices.save_prices(form, self.user)
        self.assertFalse(PriceBatch.objects.exists())

    def test_mysql_failure_rolls_back_old_price(self):
        with patch('apps.pos.prices.PriceBatch.objects.create', side_effect=RuntimeError('mysql failed')):
            with self.assertRaises(RuntimeError):
                prices.save_prices(self.form(), self.user)
        self.assertEqual(prices.current_rows()[0]['id'], 1)
        self.assertFalse(PriceDisplay.objects.exists())
        self.sync.assert_not_called()

    def test_board_only_shows_pinned_saved_rows(self):
        context = {'pmv_dich': 'sandbox', 'tiem': {}, 'pmv_user': None, 'write_lock': False}
        self.patch('apps.pos.context_processors.khbl', return_value=context)
        response = self.client.get('/banle/bang-gia/xem/')
        self.assertContains(response, 'Chưa ghim loại vàng nào')
        prices.save_prices(self.form(), self.user)
        response = self.client.get('/banle/bang-gia/xem/')
        self.assertContains(response, 'VÀNG 610')
        self.assertContains(response, 'gold-trend up')
        self.assertEqual(response['X-Frame-Options'], 'SAMEORIGIN')
        self.assertContains(response, '8.400.000')
        self.assertNotContains(response, 'Chưa ghim loại vàng nào')

    def test_post_endpoint_uses_prg_and_get_does_not_write(self):
        response = self.client.post('/banle/bang-gia/cap-nhat/', self.form())
        self.assertEqual(response.status_code, 302)
        self.assertEqual(PriceBatch.objects.count(), 1)
        self.assertEqual(self.client.get('/banle/bang-gia/cap-nhat/').status_code, 405)
