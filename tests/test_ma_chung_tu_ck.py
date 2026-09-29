"""Nhận diện mã chứng từ trong nội dung CK — đặc tả GĐ chốt 22/09/2026 (skill nhan-dien-ma-chung-tu-ck):
chỉ trong ngày · tách IN/OUT · chỉ dạng mới."""
import datetime as dt

from django.test import SimpleTestCase, TestCase

from apps.pos import ma_chung_tu_ck as MC

NGAY = dt.date(2026, 9, 21)


def loai_ma(mo_ta, huong='in'):
    return [(x.loai, x.ma) for x in MC.nhan_dien(mo_ta, huong)]


class NhanDienInTests(SimpleTestCase):
    def test_hd_12_so_va_cam_do_14_so(self):
        self.assertEqual(loai_ma('NGUYEN VAN A CK HD 260921000232 083012'), [('HD', '260921000232')])
        self.assertEqual(loai_ma('thanh toan 26-09-21-000232'), [('HD', '260921000232')])
        self.assertEqual(loai_ma('chuoc do 26090178206824'), [('CD', '26090178206824')])

    def test_qr_moi_khbl_10_so(self):
        self.assertEqual(loai_ma('KHBL1790054215 thanh toan'), [('QR', 'KHBL1790054215')])
        # 10 số của QR KHÔNG bị nhận nhầm thành mã HĐ 12 số / cầm đồ 14 số
        self.assertEqual(loai_ma('MBVCB.123.KHBL1790054215.NGUYEN A'), [('QR', 'KHBL1790054215')])

    def test_ma_giao_dich_ngan_hang_khong_bi_nham(self):
        self.assertEqual(loai_ma('MBVCB.16158852044.6264BFTVGLBXZUT8 chuyen tien'), [])
        self.assertEqual(loai_ma('FT26264117829075 TRAN B'), [])

    def test_dang_cu_khong_con_nhan_dien(self):
        for cu in ('BHABCDEFGHJK23', 'KH2QABCDEFGHIJKLMNOPABCD', 'TDC260900000123CT 101530',
                   'IB 2609151033CD 103303 Tra bot', '2609151200CT 120501'):
            self.assertEqual(loai_ma(cu), [], cu)

    def test_chieu_in_khong_doc_mau_out(self):
        self.assertEqual(loai_ma('THANH TOAN TIEN VANG 0234', 'in'), [])

    def test_mo_ho(self):
        self.assertIsNone(MC.ma_duy_nhat('260921000001 va 260921000002'))
        self.assertEqual(MC.ma_duy_nhat('260921000001').ma, '260921000001')


class NhanDienOutTests(SimpleTestCase):
    def test_thau_doi_du_4_so_stt(self):
        self.assertEqual(loai_ma('THANH TOAN TIEN VANG 0234', 'out'), [('TV', '0234')])
        self.assertEqual(loai_ma('THANH TOAN TIEN VANG 1234-220926-10:47', 'out'), [('TV', '1234')])   # STT bắt đầu 1 vẫn là thâu

    def test_cam_do_1_cong_5_so_phien(self):
        self.assertEqual(loai_ma('THANH TOAN TIEN VANG 101234', 'out'), [('CD', '101234')])

    def test_out_co_ngay_ghep_du_12_so(self):
        # GĐ chốt 22/09/2026: yymmdd (ngày giao dịch) + 6 số sau "TIEN VANG"
        ds = MC.nhan_dien('THANH TOAN TIEN VANG 1014-220926', 'out', NGAY)
        self.assertEqual([(x.loai, x.ma) for x in ds], [('TV', '260921001014')])
        ds = MC.nhan_dien('THANH TOAN TIEN VANG 101234-220926', 'out', NGAY)
        self.assertEqual([(x.loai, x.ma) for x in ds], [('CD', '260921101234')])

    def test_dang_cu_va_thieu_so(self):
        self.assertEqual(loai_ma('THANH TOAN TIEN VANG 1-KH22609020837', 'out'), [])
        self.assertEqual(loai_ma('THANH TOAN TIEN VANG-100926-11:03:21', 'out'), [])
        self.assertEqual(loai_ma('THANH TOAN TIEN VANG 12345', 'out'), [])                             # 5 số: không khớp dạng nào


class TraSoTests(TestCase):
    def _flow(self, **kw):
        from apps.pos.models import MoneyFlow
        d = dict(direction='IN', service='RETAIL', source_type='gold_bill_retail', source_id='T', business_date=NGAY,
                 expected_amount=5)
        d.update(kw)
        return MoneyFlow.objects.create(**d)

    def test_hd_trong_ngay_phan_giai_ban_hay_coc(self):
        self._flow(service='DEPOSIT', source_type='gold_bill_deposit', source_id='TDC1', source_bill_code='26-09-21-000007')
        ma = MC.ma_duy_nhat('ck 260921000007')
        self.assertEqual(MC.loai_that(ma, MC.tra_so(ma, NGAY)), 'Cọc')
        self.assertEqual(MC.tra_so(ma, NGAY + dt.timedelta(days=1)), [])                         # chỉ trong ngày

    def test_out_stt_thau_va_doi_du(self):
        self._flow(direction='OUT', service='GOLD_BUY', source_type='thau_nhom', source_id='1', source_bill_code='26-09-21-000233 (+1)')
        self._flow(direction='OUT', service='RETAIL', source_id='TRB9', source_bill_code='26-09-21-000150')
        ma = MC.ma_duy_nhat('THANH TOAN TIEN VANG 0233', 'out')
        self.assertEqual(MC.loai_that(ma, MC.tra_so(ma, NGAY)), 'Thâu vàng')
        ma = MC.ma_duy_nhat('THANH TOAN TIEN VANG 0150', 'out')
        self.assertEqual(MC.loai_that(ma, MC.tra_so(ma, NGAY)), 'Đổi dư')


class SinhNoiDungThauTests(SimpleTestCase):
    """Sinh nội dung ↔ đối soát phải cùng khuôn: 4 số cuối SỐ HĐ (không còn TrnID)."""

    def test_sinh_va_doi_soat_khop(self):
        from apps.pos import thau_cart as TC, thau_payments as TP
        nd = TC.noi_dung_ck({'bill_codes': ['26-09-21-000234', '26-09-21-000235'], 'trn_ids': ['TBG260900001445']})
        self.assertEqual(nd, 'THANH TOAN TIEN VANG 0234')
        order = {'ids': ['TBG260900001445'], 'members': [{'BillCode': '26-09-21-000234'}]}
        self.assertTrue(TP.code_match(order, {'description': nd + '-220926-10:47:17'}))
        self.assertFalse(TP.code_match(order, {'description': 'THANH TOAN TIEN VANG 1445'}))          # kiểu TrnID cũ: cắt ngay


class GanMaTests(TestCase):
    """Ghi kết quả vào 3 cột riêng KHBL — không đụng bill_code_raw; STT đổi ra số HĐ đầy đủ trong ngày."""

    def setUp(self):
        from django.db import connection
        with connection.cursor() as c:
            c.execute('CREATE TABLE bank_notifications (id INTEGER PRIMARY KEY, direction VARCHAR(3), description TEXT, '
                      'transaction_time VARCHAR(19), bill_code_raw VARCHAR(100), ma_chung_tu VARCHAR(20), '
                      'loai_chung_tu VARCHAR(8), nhan_dien_luc DATETIME)')
            for r in [(1, 'in', 'CK HD 260921000232 083012', '2026-09-21 08:30:12', 'giu nguyen'),
                      (2, 'in', 'chuoc do 26090178206824', '2026-09-21 09:00:00', ''),
                      (3, 'in', 'KHBL1790054215', '2026-09-21 09:10:00', ''),
                      (4, 'in', '260921000001 va 260921000002', '2026-09-21 09:20:00', ''),
                      (5, 'in', 'chuyen tien', '2026-09-21 09:30:00', ''),
                      (6, 'out', 'THANH TOAN TIEN VANG 0233-210926', '2026-09-21 10:00:00', 'chi CĐ'),
                      (7, 'out', 'THANH TOAN TIEN VANG 101234', '2026-09-21 10:05:00', '')]:
                c.execute('INSERT INTO bank_notifications (id,direction,description,transaction_time,bill_code_raw) '
                          'VALUES (%s,%s,%s,%s,%s)', list(r))

    def tearDown(self):
        from django.db import connection
        with connection.cursor() as c:
            c.execute('DROP TABLE bank_notifications')

    def test_gan_ma_va_chay_lai_khong_doi(self):
        from django.db import connection
        from apps.pos.models import MoneyFlow
        MoneyFlow.objects.create(direction='OUT', service='GOLD_BUY', source_type='thau_nhom', source_id='1',
                                 source_bill_code='26-09-21-000233 (+1)', business_date=NGAY, expected_amount=5)
        with connection.cursor() as c:
            dem = MC.gan_ma(c)
            c.execute('SELECT id, ma_chung_tu, loai_chung_tu, bill_code_raw FROM bank_notifications ORDER BY id')
            kq = {r[0]: r[1:] for r in c.fetchall()}
            self.assertEqual(kq[1], ('260921000232', 'HD', 'giu nguyen'))           # bill_code_raw KHÔNG bị đụng
            self.assertEqual(kq[2][:2], ('26090178206824', 'CD'))
            self.assertEqual(kq[3][:2], ('KHBL1790054215', 'QR'))
            self.assertEqual(kq[4][:2], (None, 'NHIEU'))
            self.assertEqual(kq[5][:2], (None, ''))
            self.assertEqual(kq[6], ('260921000233', 'TV', 'chi CĐ'))               # yymmdd + 4 số đệm 0 (không cần tra sổ)
            self.assertEqual(kq[7][:2], ('260921101234', 'CD'))
            self.assertEqual(dem['_so_dong'], 7)
            self.assertEqual(MC.gan_ma(c)['_so_dong'], 0)                             # đã xử lý → không làm lại


    def test_vot_lai_dong_chieu_unknown(self):
        # 29/09/2026: webhook chèn dòng lúc SePay chưa báo chiều tiền → gan_ma đóng dấu rỗng; chiều cập nhật sau → vớt lại
        from django.db import connection
        with connection.cursor() as c:
            c.execute("INSERT INTO bank_notifications (id,direction,description,transaction_time) "
                      "VALUES (8,'unknown','CK HD 260921000162','2026-09-21 18:15:37')")
            MC.gan_ma(c)
            c.execute('SELECT ma_chung_tu, loai_chung_tu FROM bank_notifications WHERE id=8')
            self.assertEqual(c.fetchone(), (None, ''))
            self.assertEqual(MC.vot_lai(c, so_ngay=100000), 0)                      # còn 'unknown' → chưa vớt
            c.execute("UPDATE bank_notifications SET direction='in' WHERE id=8")
            self.assertEqual(MC.vot_lai(c, so_ngay=100000), 1)
            c.execute('SELECT id, ma_chung_tu, loai_chung_tu FROM bank_notifications WHERE id IN (4,5,8) ORDER BY id')
            self.assertEqual(c.fetchall(), [(4, None, 'NHIEU'), (5, None, ''), (8, '260921000162', 'HD')])
            self.assertEqual(MC.vot_lai(c, so_ngay=100000), 0)                      # đã có mã → không làm lại

    def test_api_noi_bo_sau_upsert(self):
        from django.db import connection
        from django.test import Client, override_settings
        c = Client()
        url = '/banle/api/gan-ma-ck/'
        with override_settings(KHBL_GAN_MA_TOKEN='bi-mat'):
            self.assertEqual(c.post(url, {'ids': '1'}).status_code, 403)                              # thiếu token
            self.assertEqual(c.post(url, {'ids': '1'}, HTTP_X_KHBL_TOKEN='sai').status_code, 403)
            self.assertEqual(c.post(url, {'ids': '1'}, HTTP_X_KHBL_TOKEN='bi-mat',
                                    HTTP_X_FORWARDED_FOR='192.168.1.9').status_code, 403)             # đi vòng qua Caddy
            self.assertEqual(c.post(url, {'ids': '1'}, HTTP_X_KHBL_TOKEN='bi-mat',
                                    HTTP_X_FORWARDED_PROTO='https').status_code, 403)             # Caddy KHBL chỉ thêm X-Forwarded-Proto
            r = c.post(url, {'ids': '1,7'}, HTTP_X_KHBL_TOKEN='bi-mat')
            self.assertEqual((r.status_code, r.json()['so_dong']), (200, 2))
        with connection.cursor() as cur:
            cur.execute('SELECT id, loai_chung_tu FROM bank_notifications WHERE nhan_dien_luc IS NOT NULL ORDER BY id')
            self.assertEqual(cur.fetchall(), [(1, 'HD'), (7, 'CD')])                                 # CHỈ đúng 2 dòng được báo


class SoDinhChuTests(SimpleTestCase):
    """Mã giao dịch ngân hàng/ví dính sau chữ (FT, ZP, MOMO, TRC…) KHÔNG phải mã chứng từ (thực đo 22/09/2026)."""

    def test_ft_zp_momo_khong_bi_nham(self):
        self.assertEqual(loai_ma('260403000010 080618 FT26093852644649 GD 6093IBT1k1ZN7C23 030426-08:06:25'),
                         [('HD', '260403000010')])                                       # FT… ngày 093 KHÔNG thành mã cầm đồ
        self.assertEqual(loai_ma('ZP260930371614 260403000135 165942'), [('HD', '260403000135')])
        self.assertEqual(loai_ma('MOMO133117572688MOMO GD 61'), [])
        self.assertEqual(loai_ma('2717 TRC260524000003 DAT'), [])

    def test_tien_to_khach_go_ck_hd_van_nhan(self):
        self.assertEqual(loai_ma('CK260611000354 210022 GD'), [('HD', '260611000354')])
        self.assertEqual(loai_ma('HD260921000232'), [('HD', '260921000232')])

    def test_nam_vo_ly_khong_phai_chung_tu(self):
        from unittest.mock import patch
        with patch('django.utils.timezone.localdate', return_value=dt.date(2026, 9, 22)):
            self.assertEqual(loai_ma('130125245197-01257397864-260521000125 180123'), [('HD', '260521000125')])
            self.assertEqual(loai_ma('HD 250921000001'), [('HD', '250921000001')])      # năm trước vẫn nhận
