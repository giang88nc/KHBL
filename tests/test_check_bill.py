"""CHECK BILL V2 (21/09/2026) — chuẩn mã, phân loại, quét/trùng/không có, số liệu ngày, quyền, render trang."""
import datetime as dt
from decimal import Decimal
from unittest.mock import patch
from types import SimpleNamespace
from django.db import connection

from django.contrib.auth import get_user_model
from django.test import TestCase, RequestFactory

from apps.pos import check_bill as CB
from apps.pos.models import CheckBill

NGAY = dt.date(2026, 9, 21)


def dong(trn, ma, tong, ck, gio=10):
    return dict(TrnID=trn, BillCode=ma, PayAmount=tong, CashPay=tong - ck,   # HĐ tiệm trả khách: CashPay/CardPay ÂM như KK
                CardPay=ck,
                CreatedDate=dt.datetime(2026, 9, 21, gio, 5), TrnTime='10:05:00', EmpName='Thu', CustName='Khách A')


class GiaKK:
    def __init__(self, rows): self.rows = rows
    def query(self, sql, params=()):
        if 'b.BillCode=?' in sql:
            return [r for r in self.rows if r['BillCode'] == params[0]]
        return list(self.rows)


@patch('apps.pos.check_bill.ck_trong_ngay', return_value=[])
@patch('apps.pos.check_bill.ck_nhan', return_value={'260921000002': (Decimal(500), 1)})
class CheckBillTests(TestCase):
    def setUp(self):
        self.u = get_user_model().objects.create_superuser('cb-admin', password='x')
        self.kk = GiaKK([dong('T1', '26-09-21-000001', 1000, 0), dong('T2', '26-09-21-000002', 800, 500),
                         dong('T3', '26-09-21-000003', 700, 700), dong('T4', '26-09-21-000004', -200, 0)])

    def test_chuan_ma_va_phan_loai(self, *_):
        self.assertEqual(CB.chuan_ma('260921000123'), '26-09-21-000123')
        self.assertEqual(CB.chuan_ma('2609211'), '26-09-21-000001')
        self.assertEqual(CB.chuan_ma('26-09-21-000123'), '26-09-21-000123')
        for sai in ('123', '261321000001', 'abc'):
            with self.assertRaises(CB.LoiKiem): CB.chuan_ma(sai)
        self.assertEqual([CB.phan_loai(Decimal(a), Decimal(b)) for a, b in ((1000, 0), (800, 500), (700, 700), (-200, 0))],
                         ['TM', 'TMCK', 'CK', 'DB'])

    def test_quet_ghi_so_va_chan_trung(self, *_):
        cb = CB.kiem(self.kk, '260921000002', 'cb-admin')
        self.assertEqual((cb.kieu, cb.tong, cb.chuyen_khoan, cb.ck_nhan, cb.ngay), ('TMCK', 800, 500, 500, NGAY))
        with self.assertRaisesRegex(CB.LoiKiem, 'ĐÃ KIỂM'):
            CB.kiem(self.kk, '26-09-21-000002', 'cb-admin')
        with self.assertRaisesRegex(CB.LoiKiem, 'Không tìm thấy'):
            CB.kiem(self.kk, '260921000099', 'cb-admin')

    def test_bang_ngay_thieu_nhom_va_huy(self, *_):
        CB.kiem(self.kk, '260921000001', 'cb-admin')
        CB.kiem(self.kk, '260921000004', 'cb-admin')
        CheckBill.objects.create(trn_id='TX', bill_code='26-09-21-000009', ngay=NGAY, kieu='TM', tong=5, tien_mat=5,
                                 chuyen_khoan=0, nguoi_kiem='x')           # đã kiểm nhưng KK không còn
        b = CB.bang_ngay(self.kk, NGAY)
        self.assertEqual((b['kpi']['so_kk'], b['kpi']['so_da'], b['kpi']['so_thieu']), (4, 2, 2))
        self.assertEqual([r['trn_id'] for r in b['thieu']], ['T2', 'T3'])
        self.assertEqual({g['ma']: g['so'] for g in b['nhom']}, {'TM': 2, 'CK': 0, 'TMCK': 0, 'DB': 1})
        self.assertEqual([x.trn_id for x in b['huy']], ['TX'])

    def test_do_phieu_cot_lech_va_bang_ck(self, *_):
        # HĐ T2: Pay 800 = Cash 300 + Card 500 → lệch 0; thêm HĐ lệch: Pay 900, Cash 100, Card 500 → lệch 300
        self.kk.rows.append(dict(dong('T5', '26-09-21-000005', 900, 500), CashPay=100))
        b = CB.bang_ngay(self.kk, NGAY)
        self.assertNotIn('ck', b)                                          # chưa bấm Dò phiếu → không dựng bảng CK
        b = CB.bang_ngay(self.kk, NGAY, do=True)
        lech = {r['trn_id']: r['lech'] for r in b['thieu']}
        self.assertEqual((lech['T2'], lech['T5']), (0, 300))
        self.assertEqual(b['kpi']['thieu_lech'], 1)
        from django.test import Client
        with patch('apps.pos.services.client', return_value=self.kk):
            html = Client().get('/banle/bao-cao/check-bill/?d=2026-09-21&do=1').content.decode()
        self.assertIn('C.LỆCH', html); self.assertIn('CK VÀO TRONG NGÀY', html); self.assertIn('⚠ 300', html)

    def test_khong_can_dang_nhap_qua_middleware_that(self, *_):
        # GĐ chốt 21/09/2026: trang đứng riêng, KHÔNG đăng nhập, KHÔNG menu — đi qua LoginRequiredMiddleware thật.
        from django.test import Client
        c = Client()
        with patch('apps.pos.services.client', return_value=self.kk):
            r = c.get('/banle/bao-cao/check-bill/?d=2026-09-21')
            self.assertEqual(r.status_code, 200)
            html = r.content.decode()
            self.assertIn('CHECK BILL', html); self.assertIn('CÒN THIẾU', html)
            self.assertNotIn('khbl-topbar', html); self.assertNotIn('dang-xuat', html)     # không menu / topbar
            r = c.post('/banle/bao-cao/check-bill/quet/', {'ma': '260921000003', 'd': '2026-09-21'}, HTTP_HX_REQUEST='true')
            self.assertEqual(r.status_code, 200); self.assertIn('Chuyển khoản', r.content.decode())
            cb = CheckBill.objects.get(trn_id='T3')
            self.assertTrue(cb.nguoi_kiem.startswith('máy '))                               # không tài khoản → ghi IP
            c.post(f'/banle/bao-cao/check-bill/{cb.pk}/xoa/', HTTP_HX_REQUEST='true')
            self.assertFalse(CheckBill.objects.filter(trn_id='T3').exists())
            CB.kiem(self.kk, '260921000001', 'x')
            c.post('/banle/bao-cao/check-bill/kiem-lai/', {'d': '2026-09-21'}, HTTP_HX_REQUEST='true')
            self.assertFalse(CheckBill.objects.filter(ngay=NGAY).exists())
            self.assertEqual(c.get('/banle/bao-cao/check-bill/quet/').status_code, 405)   # quét chỉ nhận POST


class CkTrongNgayTests(TestCase):
    """CK VÀO (22/09/2026) = mọi dòng bank_notifications IN trong ngày · Mã HĐ = ma_chung_tu · checked = đã xác nhận vào phiếu."""

    def test_ds_tu_bank_notifications_va_checked(self):
        from apps.pos.models import MoneyFlow
        a = MoneyFlow.objects.create(direction='IN', service='RETAIL', source_type='gold_bill_retail', source_id='T1',
                                     source_bill_code='26-09-21-000001', business_date=NGAY, expected_amount=700)
        cot = ('id', 'transaction_time', 'trans_amount', 'bank_name', 'acc_name', 'description', 'ma_chung_tu', 'loai_chung_tu')
        bn = [(11, '2026-09-21 10:06:00', 700, 'VCB', 'NGUYEN A', 'CK HD', '260921000001', 'HD'),     # đã xác nhận T1
              (12, '2026-09-21 11:00:00', 300, 'ACB', 'TRAN B', 'COC 260921000002', '260921000002', 'HD'),  # có mã, chưa xác nhận
              (13, '2026-09-21 12:00:00', 999, 'TCB', 'LE C', 'chuyen tien', None, None)]              # không mã
        with patch('apps.pos.mobile_receipts.receipts_by_flow', return_value={a.pk: {'11': Decimal(700)}}),                 patch('apps.pos.check_bill._ck_ngan_hang', return_value=[dict(zip(cot, r)) for r in bn]):
            ra = CB.ck_trong_ngay(NGAY)
        self.assertEqual([(r['gio'], r['ma'], r['loai_ma'], r['tien_ck'], r['checked']) for r in ra],
                         [('10:06', '260921000001', 'HD', 700, True), ('11:00', '260921000002', 'HD', 300, False),
                          ('12:00', '', '', 999, False)])


class CkSuaMaTests(TestCase):
    """Popup ✎ (23/09/2026): sửa ma_chung_tu của 1 khoản CK → ghi CardPay/CashPay đúng HĐ đó trên KK."""

    def setUp(self):
        with connection.cursor() as c:
            c.execute('CREATE TABLE bank_notifications (id integer primary key, direction text, trans_amount decimal, '
                      'transaction_time text, bank_name text, bank_number text, acc_name text, description text, '
                      'ma_chung_tu text, loai_chung_tu text, nhan_dien_luc text)')
            c.execute("INSERT INTO bank_notifications VALUES (5,'in',2090000,'2026-09-23 08:44','ACB','666141168','',"
                      "'TRAN THI BAO VI chuyen tien',NULL,NULL,NULL)")

    def tearDown(self):
        with connection.cursor() as c:
            c.execute('DROP TABLE bank_notifications')

    def kk(self, pay=2090000):
        return SimpleNamespace(query=lambda sql, p=(): [{'TrnID': 'TRB1', 'PayAmount': Decimal(pay)}])

    def test_ghi_dung_hoa_don_va_so_tien(self):
        with patch('apps.pmv.gateway.pmv_in_snapshot', return_value={'BillCode': '26-09-23-000096'}) as snap, \
                patch('apps.pmv.gateway.pmv_in_allocate') as alloc:
            tin = CB.sua_ma_va_ghi_kk(self.kk(), 5, '26-09-23-000096', 'test')
        self.assertIn('26-09-23-000096', tin)
        self.assertEqual(snap.call_args[0][:3], ('RETAIL', '26-09-23-000096', 'TRB1'))
        self.assertEqual(alloc.call_args[0][2], Decimal(2090000))          # CardPay = tổng CK mang mã này
        with connection.cursor() as c:
            c.execute('SELECT ma_chung_tu, loai_chung_tu FROM bank_notifications WHERE id=5')
            self.assertEqual(c.fetchone(), ('260923000096', 'HD'))

    def test_chan_ma_sai_va_tien_vuot_hoa_don(self):
        with self.assertRaises(CB.LoiKiem):
            CB.sua_ma_va_ghi_kk(self.kk(), 5, '123', 'test')                # không đủ 12 số
        with patch('apps.pmv.gateway.pmv_in_allocate') as alloc, self.assertRaises(CB.LoiKiem):
            CB.sua_ma_va_ghi_kk(self.kk(pay=1000000), 5, '26-09-23-000096', 'test')   # CK > PayAmount
        alloc.assert_not_called()
