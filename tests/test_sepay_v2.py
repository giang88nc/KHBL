"""Webhook SePay V2 (22/09/2026): xác thực 2 kiểu · UPSERT chống trùng theo id SePay · tách mã ngay · giữ nghĩa cột V1."""
import json

from django.db import connection
from django.test import Client, TestCase, override_settings

URL = '/banle/webhook/sepay2/'
MAU = {'id': 9123456, 'gateway': 'ACB', 'transactionDate': '2026-09-21 08:52:36', 'accountNumber': '666141168',
       'content': 'MBVCB.16156663777.6264BFTV.260921000025 085236 NGUYEN A', 'transferType': 'in',
       'transferAmount': 4790000, 'accumulated': 123456789, 'referenceCode': 'FT26264X'}


@override_settings(KHBL_GAN_MA_TOKEN='noi-bo', SEPAY_API_KEY='khoa-sepay')
class SepayV2Tests(TestCase):
    def setUp(self):
        with connection.cursor() as c:
            c.execute('CREATE TABLE bank_notifications (id INTEGER PRIMARY KEY AUTOINCREMENT, provider VARCHAR(20), '
                      'ref_code VARCHAR(100) NOT NULL, bank_number VARCHAR(100), bank_name VARCHAR(50), acc_name VARCHAR(255), '
                      'trans_amount DECIMAL(18,2), balance_after DECIMAL(18,2), description TEXT, full_code VARCHAR(255), '
                      'bill_code_raw VARCHAR(100), bill_code_norm VARCHAR(100), transaction_time VARCHAR(50), '
                      'direction VARCHAR(10), match_status VARCHAR(50), match_message VARCHAR(255), raw_payload TEXT, '
                      'is_check INT, created_at DATETIME, updated_at DATETIME, ma_chung_tu VARCHAR(20), '
                      'loai_chung_tu VARCHAR(8), nhan_dien_luc DATETIME, '
                      'UNIQUE (ref_code, bank_number, trans_amount, direction, transaction_time))')

    def tearDown(self):
        with connection.cursor() as c:
            c.execute('DROP TABLE bank_notifications')

    def goi(self, body=None, **h):
        return Client().post(URL, data=json.dumps(body or MAU), content_type='application/json', **h)

    def dong(self):
        with connection.cursor() as c:
            c.execute('SELECT ref_code, bill_code_raw, bill_code_norm, full_code, match_status, transaction_time, '
                      'ma_chung_tu, loai_chung_tu, is_check FROM bank_notifications')
            return c.fetchall()

    def test_xac_thuc(self):
        self.assertEqual(self.goi().status_code, 401)                                              # không gì cả
        self.assertEqual(self.goi(HTTP_X_KHBL_TOKEN='sai').status_code, 401)
        self.assertEqual(self.goi(HTTP_X_KHBL_TOKEN='noi-bo', REMOTE_ADDR='192.168.1.9').status_code, 401)      # token nhưng không từ máy chủ
        self.assertEqual(self.goi(HTTP_X_KHBL_TOKEN='noi-bo', HTTP_X_FORWARDED_PROTO='https').status_code, 200)  # V1 → Caddy 8100 (cùng máy)
        self.assertEqual(self.goi(HTTP_AUTHORIZATION='Apikey sai').status_code, 401)
        self.assertEqual(self.goi(HTTP_AUTHORIZATION='Apikey khoa-sepay').status_code, 200)       # SePay gọi thẳng (giai đoạn 2)
        self.assertEqual(self.goi(HTTP_X_KHBL_TOKEN='noi-bo').status_code, 200)                    # V1 chuyển tiếp

    def test_upsert_chong_trung_va_tach_ma(self):
        r1 = self.goi(HTTP_X_KHBL_TOKEN='noi-bo')
        r2 = self.goi(HTTP_X_KHBL_TOKEN='noi-bo')                                                  # SePay/V1 gửi lại
        self.assertEqual((r1.json()['message'], r2.json()['message']), ('Webhook saved', 'Duplicate webhook'))
        self.assertEqual(r1.json()['id'], r2.json()['id'])
        rows = self.dong()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0], ('9123456', '260921000025', '085236', '260921000025085236', 'matched',
                                   '2026-09-21 08:52:36', '260921000025', 'HD', 0))                 # cột V1 + cột skill

    def test_tien_ra_va_json_loi(self):
        out = dict(MAU, id=77, transferType='out', content='THANH TOAN TIEN VANG 1014-210926-20:19:57')
        self.assertEqual(self.goi(out, HTTP_X_KHBL_TOKEN='noi-bo').status_code, 200)
        r = [x for x in self.dong() if x[0] == '77'][0]
        self.assertEqual((r[4], r[6], r[7]), ('ignored', '260921001014', 'TV'))
        bad = Client().post(URL, data='khong-phai-json', content_type='application/json', HTTP_X_KHBL_TOKEN='noi-bo')
        self.assertEqual(bad.status_code, 400)
        self.assertEqual(self.goi(dict(MAU, id=None, referenceCode=None), HTTP_X_KHBL_TOKEN='noi-bo').status_code, 400)
