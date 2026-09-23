"""CHECK GOLD V4 (23/09/2026): nhận mã · quét thêm/gỡ · chặn hàng đã bán · còn thiếu · trang không cần đăng nhập."""
import datetime as dt
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from django.db import connection
from django.test import TestCase

from apps.pos import check_gold as G
from apps.pos.models import GoldCheckEntry

NGAY = dt.date(2026, 9, 23)
KHO = {'GoldCode': 'N9999', 'ProductCode': '9N60017816', 'ProductDesc': 'Nhẫn 1 chỉ',
       'TotalWeight': Decimal(100), 'Status': 'I', 'SellOutDate': None}


def kk(rows_product=(KHO,), rows_ton=()):
    """Máy KK giả: câu nào cũng trả theo bảng đang hỏi."""
    def query(sql, params=()):
        if 'TOP 1 GoldCode' in sql:
            return list(rows_product)
        if "Status='I'" in sql:
            return list(rows_ton)
        if 'OUTER APPLY' in sql:
            return [{'ProductCode': KHO['ProductCode'], 'TT': 'I', 'NgayXuat': None, 'CustName': '', 'EmpName': ''}]
        return []
    return SimpleNamespace(query=query)


class CheckGoldTests(TestCase):
    def setUp(self):
        with connection.cursor() as c:      # bảng CK chỉ dùng để tính chữ ký của ngày
            c.execute('CREATE TABLE IF NOT EXISTS bank_notifications (id integer primary key, transaction_time text)')

    def tearDown(self):
        with connection.cursor() as c:
            c.execute('DROP TABLE IF EXISTS bank_notifications')

    def test_nhan_dien_ma(self):
        self.assertEqual(G.loai_ma('9n60017816'), ('sp', '9N60017816'))
        self.assertEqual(G.loai_ma('1C60004504'), ('sp', '1C60004504'))
        self.assertEqual(G.loai_ma('260923000096'), ('hd', '26-09-23-000096'))
        self.assertEqual(G.loai_ma('26-09-23-000096'), ('hd', '26-09-23-000096'))
        self.assertEqual(G.loai_ma('abc')[0], None)

    def test_quet_them_roi_go_va_van_truy_vet(self):
        kieu, _, tin, muc = G.quet(kk(), '9n60017816', 'Chị Tuyền', NGAY)
        self.assertEqual((kieu, muc), ('sp', 'ok'))
        self.assertIn('1.0 chỉ', tin)
        row = GoldCheckEntry.objects.get(ngay=NGAY, product_code='9N60017816')
        self.assertEqual((row.gold_code, row.nhan_vien, row.go_luc), ('N9999', 'Chị Tuyền', None))
        G.quet(kk(), '9N60017816', 'Chị Tuyền', NGAY)                       # quét lần 2 = gỡ
        row.refresh_from_db()
        self.assertIsNotNone(row.go_luc)                                     # vẫn còn dòng để truy vết
        self.assertEqual(G.danh_sach(kk(), NGAY), [])
        G.quet(kk(), '9N60017816', 'Chị Tuyền', NGAY)                        # quét lại lần 3 = vào lại danh sách
        row.refresh_from_db()
        self.assertIsNone(row.go_luc)
        self.assertEqual(GoldCheckEntry.objects.filter(ngay=NGAY).count(), 1)   # unique (ngày, mã) — không sinh dòng 2

    def test_chan_hang_da_ban_va_canh_bao_ngoai_9999(self):
        ban = dict(KHO, Status='S', SellOutDate=dt.datetime(2026, 9, 20))
        with self.assertRaises(G.LoiQuet) as e:
            G.quet(kk([ban]), '9N60017816', '', NGAY)
        self.assertIn('Đã bán', str(e.exception))
        self.assertFalse(GoldCheckEntry.objects.exists())
        _, _, tin, muc = G.quet(kk([dict(KHO, GoldCode='18K')]), '9N60017816', '', NGAY)
        self.assertEqual(muc, 'canh_bao')
        self.assertIn('KHÔNG thuộc vàng 9999', tin)

    def test_khong_tim_thay_san_pham(self):
        with self.assertRaises(G.LoiQuet):
            G.quet(kk([]), '9N60017816', '', NGAY)

    def test_dong_giao_dich_doc_money_flow(self):
        """Bảng GIAO DỊCH lấy từ money_flow; mỗi dòng mang CHỮ KÝ gồm đúng các cột GĐ yêu cầu theo dõi."""
        from apps.pos.models import MoneyFlow
        f = MoneyFlow.objects.create(direction='IN', service='RETAIL', source_system='KHBL',
                                     source_type='gold_bill_retail', source_id='T9', source_bill_code='26-09-23-000009',
                                     customer_name='Chị Dung', business_date=NGAY, expected_amount=1000,
                                     cash_amount=600, bank_amount=400, cus_cash=700, cus_change=100,
                                     payment_status='confirmed')
        g = G.giao_dich(NGAY)[0]
        self.assertEqual((g['ma'], g['khach'], g['tong'], g['khach_dua'], g['thoi_lai'], g['tt_nhan']),
                         ('26-09-23-000009', 'Chị Dung', Decimal(1000), Decimal(700), Decimal(100), 'Đủ tiền'))
        ky_cu = g['ky']
        MoneyFlow.objects.filter(pk=f.pk).update(cash_amount=1000, bank_amount=0)
        self.assertNotEqual(G.giao_dich(NGAY)[0]['ky'], ky_cu)          # đổi tiền → dòng sẽ nháy trên trang
        self.assertEqual(G.giao_dich(NGAY, tu_id=f.pk), [])              # lấy dòng mới hơn: không còn gì

    def test_dong_het_gio_tu_roi_bang(self):
        """GĐ chốt 23/09/2026: dòng TIỀN VÀO sống 3 phút, TIỀN RA sống 10 phút."""
        from django.utils import timezone
        from apps.pos.models import MoneyFlow
        hom_nay = timezone.localdate()
        cu = timezone.now() - dt.timedelta(minutes=5)
        vao = MoneyFlow.objects.create(direction='IN', service='RETAIL', source_system='KHBL',
                                       source_type='gold_bill_retail', source_id='T1', source_bill_code='B1',
                                       business_date=hom_nay, expected_amount=100)
        ra = MoneyFlow.objects.create(direction='OUT', service='GOLD_BUY', source_system='KHBL',
                                      source_type='gold_bill_ungrouped', source_id='T2', source_bill_code='B2',
                                      business_date=hom_nay, expected_amount=200)
        MoneyFlow.objects.filter(pk__in=[vao.pk, ra.pk]).update(created_at=cu)
        ds = G.giao_dich(hom_nay)
        self.assertEqual([g['ma'] for g in ds], ['B2'])                  # IN quá 3 phút đã rời bảng, OUT còn
        self.assertEqual((ds[0]['nhom'], ds[0]['ra_tien']), ('ra', True))  # thâu vàng = nhóm ĐỎ

    def test_trang_khong_can_dang_nhap_va_khong_ghi_kk(self):
        with patch('apps.pos.services.client', return_value=kk()):
            r = self.client.get('/banle/bao-cao/check-gold/', HTTP_HOST='127.0.0.1')
        self.assertEqual(r.status_code, 200)                                  # không bị đẩy sang trang đăng nhập
        h = r.content.decode()
        self.assertIn('CHECK', h)
        self.assertIn('GIAO DỊCH', h)
        self.assertNotIn('cg-the-so', h)                                      # GĐ chốt: bỏ khối thống kê

    def test_sua_hoa_don_rang_buoc_tien_va_goi_dung_cong(self):
        hd = {'TrnID': 'TRB1', 'BillCode': '26-09-23-000009', 'PayAmount': Decimal(1000), 'CashPay': Decimal(1000),
              'CardPay': Decimal(0), 'CustID': 'CU1', 'EmpID': 'E1', 'Desc4': '', 'Desc5': ''}
        with patch('apps.pos.check_gold.hoa_don', return_value=hd),                 patch('apps.pmv.gateway.pmv_billsell_edit') as ghi:
            with self.assertRaises(G.LoiQuet):                                 # lệch tổng → không ghi
                G.sua_hoa_don(None, 'TRB1', hd['BillCode'], {'cash_pay': '300', 'card_pay': '300'})
            ghi.assert_not_called()
            tin = G.sua_hoa_don(None, 'TRB1', hd['BillCode'],
                                {'cash_pay': '400', 'card_pay': '600', 'desc4': '50361307',
                                 'cust_id': 'CU1', 'emp_id': 'E1', 'desc5': ''})
        self.assertIn('Đã lưu', tin)
        self.assertEqual(ghi.call_args[0][1], {'Desc4': '50361307', 'CashPay': Decimal(400), 'CardPay': Decimal(600)})
