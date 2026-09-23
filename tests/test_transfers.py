from unittest.mock import patch
from datetime import date

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TransactionTestCase

from apps.pos.transfers import TransferFilter, listing, query


class TransferTests(TransactionTestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('bank-test')
        self.client.force_login(self.user)
        self.patches = [patch('apps.pos.context_processors.khbl', return_value={}), patch('apps.pmv.hist_read.nguon', return_value={})]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)
        with connection.cursor() as c:
            c.execute('CREATE TABLE bank_notifications (id integer primary key, transaction_time varchar(50), bank_number varchar(100), bank_name varchar(50), direction varchar(10), trans_amount decimal, description text, ref_code varchar(100), bill_code_raw varchar(100), ma_chung_tu varchar(20), loai_chung_tu varchar(8))')
            c.execute('CREATE TABLE gold_bank (id integer primary key, bank_bin varchar(22), bank_number varchar(33), bank_name varchar(33), bank_user varchar(255), bank_addr varchar(255), type varchar(11), Active integer)')
            for i, day, direction, amount, text in [(1, '08', 'in', 100, 'ABC'), (2, '08', 'out', 200, 'XYZ'), (3, '09', 'in', 100, 'ABC')]:
                c.execute('INSERT INTO bank_notifications VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)', [i, f'2026-09-{day} 12:00:00', '001', 'ACB', direction, amount, text, str(i), "HD001", '2609' + str(i).zfill(8), 'HD'])

    def tearDown(self):
        with connection.cursor() as c:
            c.execute('DROP TABLE bank_notifications')
            c.execute('DROP TABLE gold_bank')

    def test_filters_boundaries_and_signature_changes(self):
        data = dict(d1=date(2026, 9, 8), d2=date(2026, 9, 8), direction='in', account='001', amount1=100, amount2=100, key='ABC')
        first = listing(data, 1)
        self.assertEqual([r['id'] for r in first['rows']], [1])
        with connection.cursor() as c:
            c.execute("UPDATE bank_notifications SET description='ABC changed' WHERE id=1")
        self.assertNotEqual(first['signature'], listing(data, 1)['signature'])
        with connection.cursor() as c:
            c.execute('DELETE FROM bank_notifications WHERE id=1')
        self.assertEqual(listing(data, 1)['total'], 0)

    def test_page_default_and_unchanged_poll(self):
        with patch('apps.pos.transfers.timezone.localdate', return_value=date(2026,9,8)):
            response = self.client.get('/banle/chuyen-khoan/')
            self.assertContains(response, 'CHUYỂN KHOẢN')
            self.assertEqual(response.context['total'], 2)
            sig = response.context['signature']
            poll = self.client.get('/banle/chuyen-khoan/', {'signature':sig}, HTTP_HX_REQUEST='true')
            self.assertEqual(poll.status_code, 204)

    def test_invalid_ranges_and_injection(self):
        self.assertFalse(TransferFilter({'d1':'2026-09-09', 'd2':'2026-09-08'}).is_valid())
        self.assertFalse(TransferFilter({'d1':'2026-09-08', 'd2':'2026-09-08','amount1':200,'amount2':100}).is_valid())
        response = self.client.get('/banle/chuyen-khoan/', {'d1':'2026-09-08','d2':'2026-09-08','account':"' OR 1=1 --"})
        self.assertEqual(response.context['total'], 0)

    def test_bank_popup_crud_and_get_does_not_delete(self):
        payload = dict(bank_bin='ACB',bank_number='000123',bank_name='ACB',bank_user='Tài khoản thử',bank_addr='',type='',Active='on')
        self.assertContains(self.client.post('/banle/ngan-hang/them/', payload), 'Đã lưu')
        bank = query('SELECT * FROM gold_bank')[0]
        self.assertEqual(bank['bank_number'], '000123')
        payload['bank_user'] = 'Đã sửa'
        self.assertContains(self.client.post(f"/banle/ngan-hang/{bank['id']}/sua/", payload), 'Đã sửa')
        self.client.get(f"/banle/ngan-hang/{bank['id']}/xoa/")
        self.assertEqual(len(query('SELECT * FROM gold_bank')), 1)
        self.assertContains(self.client.post(f"/banle/ngan-hang/{bank['id']}/xoa/"), 'Đã xóa')
        self.assertEqual(query('SELECT * FROM gold_bank'), [])
        self.assertEqual(len(query('SELECT * FROM bank_notifications')), 3)

    def test_auth_and_post_validation(self):
        response = self.client.post('/banle/ngan-hang/them/', {'bank_number':'bad'})
        self.assertTrue(response.context['form'].errors)
        self.assertEqual(query('SELECT * FROM gold_bank'), [])
        self.client.logout()
        self.assertEqual(self.client.get('/banle/chuyen-khoan/').status_code, 302)
        self.assertEqual(self.client.post('/banle/ngan-hang/them/', {}).status_code, 302)
