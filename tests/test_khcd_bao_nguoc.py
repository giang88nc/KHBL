"""BÁO NGƯỢC ĐỐI SOÁT CHI CẦM ĐỒ SANG KHCD (GĐ chốt 29/09/2026) — chạy trên sqlite, khj_cd là DB gắn thêm trong bộ nhớ."""
import datetime as dt
import json
from decimal import Decimal
from types import SimpleNamespace

from django.db import connection
from django.test import TransactionTestCase
from django.utils import timezone

from apps.pos import khcd_bao_nguoc as KB

SKU, LOG = 'KH2260900000123', 4567


class KhcdBaoNguocTests(TransactionTestCase):
    def setUp(self):
        with connection.cursor() as c:
            c.execute("ATTACH DATABASE ':memory:' AS khj_cd")
            c.execute('CREATE TABLE khj_cd.cd_loans (id integer primary key, sku text)')
            c.execute('CREATE TABLE khj_cd.cd_loan_logs (id integer primary key, loan_id integer)')
            c.execute('CREATE TABLE khj_cd.cd_payments (id integer primary key, log_id integer, channel text, direction text, '
                      'amount decimal, cashPay decimal, cardPay decimal, bank_snapshot text, reconciliation_state text)')
            c.execute('INSERT INTO khj_cd.cd_loans VALUES (1, %s)', [SKU])
            c.execute('INSERT INTO khj_cd.cd_loan_logs VALUES (%s, 1)', [LOG])
            c.execute("INSERT INTO khj_cd.cd_payments VALUES (1, %s, NULL, 'OUT', 100, 100, 0, %s, 'RECORDED')",
                      [LOG, json.dumps({'transfer_status': 'PREPARED', 'qr_image': 'x'})])

    def tearDown(self):
        with connection.cursor() as c:
            c.execute('DETACH DATABASE khj_cd')

    def order(self, paid, required=100, ngay=dt.date(2026, 9, 29), problems=()):
        tao = timezone.make_aware(dt.datetime.combine(ngay, dt.time(10)))
        links = [SimpleNamespace(pk=1, active_notification_id=9, amount=paid, bank_snapshot={'ref_code': 'FT1'})] if paid else []
        return dict(nghiep_vu='camdo', ids=[SKU], bill_codes=[f'26-09-29-{LOG:06d}'], paid=Decimal(paid),
                    required=Decimal(required), problems=list(problems), links=links, bank=SimpleNamespace(created_at=tao))

    def dong(self):
        with connection.cursor() as c:
            c.execute('SELECT cashPay, cardPay, reconciliation_state, bank_snapshot FROM khj_cd.cd_payments WHERE id=1')
            cash, card, st, snap = c.fetchone()
        return Decimal(cash), Decimal(card), st, json.loads(snap)

    def test_log_id_tu_ma_phien(self):
        self.assertEqual(KB.log_id_cua({'bill_codes': ['26-09-29-004567']}), 4567)
        self.assertIsNone(KB.log_id_cua({'bill_codes': ['']}))

    def test_du_tien_thanh_reconciled(self):
        self.assertTrue(KB.ghi_mot(self.order(100)))
        cash, card, st, snap = self.dong()
        self.assertEqual((cash, card, st), (0, 100, 'RECONCILED'))
        self.assertEqual((snap['transfer_status'], snap['reconciled_refs'], snap['reconciled_by']), ('RECONCILED', ['FT1'], 'KHBL'))
        self.assertEqual(snap['qr_image'], 'x')                   # JSON_SET không làm mất ảnh QR
        self.assertFalse(KB.ghi_mot(self.order(100)))             # chạy lại: đã đúng → không ghi

    def test_mot_phan_roi_go_het(self):
        self.assertTrue(KB.ghi_mot(self.order(40)))
        self.assertEqual(self.dong()[:3], (60, 40, 'PARTIAL'))
        self.assertTrue(KB.ghi_mot(self.order(0)))                # gỡ liên kết → về tiền mặt
        self.assertEqual(self.dong()[:3], (100, 0, 'RECORDED'))

    def test_khong_dung_dong_cua_luong_khac_va_ngay_cu(self):
        self.assertFalse(KB.ghi_mot(self.order(100, ngay=dt.date(2026, 9, 28))))   # trước ngày áp dụng: giữ nguyên
        self.assertFalse(KB.ghi_mot(self.order(100, problems=['x'])))
        with connection.cursor() as c:
            c.execute("UPDATE khj_cd.cd_payments SET reconciliation_state='MATCHED'")
        self.assertFalse(KB.ghi_mot(self.order(100)))
        self.assertEqual(self.dong()[2], 'MATCHED')

    def test_vuot_tien_phien_bao_loi(self):
        with self.assertRaises(ValueError):
            KB.ghi_mot(self.order(150, required=150))
        xong, loi = KB.dong_bo([self.order(150, required=150)])
        self.assertEqual((xong, len(loi)), (0, 1))

    def test_trang_thai_cho_danh_sach(self):
        KB.ghi_mot(self.order(40))
        self.assertEqual(KB.trang_thai([self.order(40)]), {SKU: ('PARTIAL', Decimal(40))})
