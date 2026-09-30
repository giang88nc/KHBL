"""GIÁ VÀNG MIẾNG SJC khi quét mã (GĐ chốt 29/09/2026): mã 2S…/9S… (GoldCode N9999 trên KK) bán theo giá SJC."""
from decimal import Decimal
from unittest.mock import patch

from django.test import SimpleTestCase

from apps.pos import services as S
from apps.pos.prices import PriceError

GIA = {'N9999': {'SellRate': Decimal('13750'), 'BuyRate': Decimal('13250')},
       'SJC': {'SellRate': Decimal('14600'), 'BuyRate': Decimal('13800')}}


class GiaSjcTests(SimpleTestCase):
    def test_nhan_dien_ma(self):
        for ma in ('9S600036', '2S000001', '9s1', ' 2S9 '):
            self.assertTrue(S.la_ma_sjc(ma), ma)
        for ma in ('9B600005', 'S9000', '19S000', '', None, '29S1'):
            self.assertFalse(S.la_ma_sjc(ma), ma)

    def test_ap_gia_sjc_giu_goldcode(self):
        row = {'ProductCode': '9S600036', 'GoldCode': 'N9999'}
        with patch.object(S, 'gia_mysql', return_value=GIA):
            S.ap_gia_mysql(row)
            self.assertEqual(row['SellRate'], Decimal('13750'))
            S.ap_gia_sjc(row)
        self.assertEqual((row['GoldCode'], row['SellRate'], row['BuyRate']), ('N9999', Decimal('14600'), Decimal('13800')))

    def test_thieu_gia_sjc_thi_chan(self):
        with patch.object(S, 'gia_mysql', return_value={'N9999': GIA['N9999']}):
            with self.assertRaises(PriceError):
                S.ap_gia_sjc({'ProductCode': '9S1', 'GoldCode': 'N9999'})

    def test_quet_ma_sjc_va_ma_thuong(self):
        class C:
            def __init__(self, ma):
                self.ma = ma

            def call(self, *a, **kw):
                return 0, [[{'ErrorCode': '0', 'ProductCode': kw['p_ProductCode'], 'GoldCode': 'N9999'}]]
        with patch.object(S, 'gia_mysql', return_value=GIA), patch.object(S, 'client', side_effect=lambda tag='': C(tag)):
            sjc, err = S._quet('9S600036')
            thuong, err2 = S._quet('9B600005')
        self.assertIsNone(err)
        self.assertIsNone(err2)
        self.assertEqual(sjc['SellRate'], Decimal('14600'))
        self.assertEqual(thuong['SellRate'], Decimal('13750'))
        self.assertNotIn('gia_sjc', thuong)
