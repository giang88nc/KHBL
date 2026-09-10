import datetime as dt
from contextlib import nullcontext
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import connection, IntegrityError, transaction
from django.test import TransactionTestCase
from django.utils import timezone

from apps.pos import thau_payments as P
from apps.pos.models import ThauPaymentLink
from apps.pos.transfers import query


class ThauPaymentsTests(TransactionTestCase):
    day = '2026-09-09'

    def setUp(self):
        with connection.cursor() as c:
            c.execute('CREATE TABLE bank_notifications (id integer primary key, provider text, ref_code text, bank_number text, bank_name text, trans_amount decimal, transaction_time text, direction text, description text, bill_code_raw text)')
            c.execute('CREATE TABLE gold_bank (bank_number text, Active integer)')
            c.execute("INSERT INTO gold_bank VALUES ('001',1)")
        self.user = get_user_model().objects.create_superuser('thau-test', password='test')
        self.client.force_login(self.user)
        self.raw = [self.bill()]
        self.groups = []
        self.start_patch = patch.object(P.S, 'hoa_don_loc', side_effect=lambda *a, **kw: (self.raw, True))
        self.start_patch.start()
        self.group_patch = patch.object(P, 'groups_for', side_effect=lambda ids: self.groups)
        self.group_patch.start()
        self.history_patch = patch.object(P, 'history_for', side_effect=lambda ids: [l for l in ThauPaymentLink.objects.order_by('-pk') if set(l.trn_ids) & set(ids)])
        self.history_patch.start()
        self.addCleanup(self.start_patch.stop)
        self.addCleanup(self.group_patch.stop)
        self.addCleanup(self.history_patch.stop)

    def tearDown(self):
        with connection.cursor() as c:
            c.execute('DROP TABLE bank_notifications')
            c.execute('DROP TABLE gold_bank')

    def bill(self, trn='TBG260900000001', amount=100):
        return dict(TrnID=trn, BillCode='26-09-09-'+trn[-6:], CreatedDate=dt.datetime(2026,9,9,11,45),
                    Status='C', IsDel='0', CardPay=-Decimal(amount), SoTien=Decimal(amount), TienMua=Decimal(amount), loai='THAU')

    def bank(self, id=1, description='TBG260900000001', amount=100, ref=None):
        with connection.cursor() as c:
            c.execute('INSERT INTO bank_notifications VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                      [id,'sepay',ref or f'REF{id}','001','ACB',amount,self.day+' 12:00:00','out',description,''])

    def inspect(self):
        return P.inspect(self.day, self.day)[0]

    def link(self):
        order = self.inspect()[0]
        return P.create_link(order, order['candidates'][0], self.user, 'Reviewed', 'manual')

    def test_group_sums_cardpay_and_confirms_once(self):
        self.raw.append(self.bill('TBG260900000002', 50))
        self.groups = [SimpleNamespace(pk=1,trn_ids=[r['TrnID'] for r in self.raw],tien_ck=150,ck_bank='ACB',ck_stk='recipient',ck_ten='Customer',ck_nd='memo')]
        self.bank(amount=150)
        order = self.inspect()[0]
        self.assertEqual(order['required'], 150)
        self.assertTrue(order['candidates'][0]['can_link'])
        self.link()
        self.assertEqual(self.inspect()[0]['payment_status'], 'confirmed')
        self.assertEqual(self.inspect()[0]['candidates'], [])

    def test_amount_time_only_requires_manual(self):
        self.bank(description='transfer')
        order = self.inspect()[0]
        self.assertTrue(order['candidates'][0]['can_link'])
        with self.assertRaises(ValueError): P.create_link(order, order['candidates'][0])
        self.link()
        self.assertEqual(self.inspect()[0]['payment_status'], 'confirmed')

    def test_ambiguous_groups_cannot_auto_but_allow_review(self):
        self.raw.append(self.bill('TBG260900000002'))
        self.bank()
        order = self.inspect()[0]
        self.assertTrue(order['candidates'][0]['ambiguous'])
        with self.assertRaises(ValueError): P.create_link(order, order['candidates'][0])
        self.link()
        self.assertEqual(len(self.inspect()[1]['candidates']), 0)

    def test_bank_change_after_link_requires_review(self):
        self.bank(); self.link()
        with connection.cursor() as c: c.execute('UPDATE bank_notifications SET trans_amount=90')
        self.assertEqual(self.inspect()[0]['payment_status'], 'review')

    def test_invoice_cancel_or_amount_change_requires_review(self):
        self.bank(); self.link()
        self.raw[0]['Status'] = 'W'
        self.assertEqual(self.inspect()[0]['payment_status'], 'review')
        self.raw[0]['Status'] = 'C'; self.raw[0]['CardPay'] = -90
        self.assertEqual(self.inspect()[0]['payment_status'], 'review')

    def test_bank_change_before_save_rejected(self):
        self.bank(); order = self.inspect()[0]
        with connection.cursor() as c: c.execute('UPDATE bank_notifications SET trans_amount=90')
        with self.assertRaises(ValueError): P.create_link(order, order['candidates'][0])
        self.assertEqual(ThauPaymentLink.objects.count(), 0)

    def test_duplicate_reference_rejected(self):
        self.bank(ref='DUP'); self.bank(id=2, ref='DUP')
        order = self.inspect()[0]
        self.assertFalse(order['candidates'][0]['can_link'])

    def test_late_duplicate_reference_rejected(self):
        self.bank(ref='DUP'); order = self.inspect()[0]
        self.bank(id=2, ref='DUP')
        with self.assertRaises(ValueError): P.create_link(order, order['candidates'][0])

    def test_unique_active_notification(self):
        self.bank(); link = self.link()
        with self.assertRaises(IntegrityError), transaction.atomic():
            ThauPaymentLink.objects.create(order_key='other', notification_id=1, active_notification_id=1, amount=100)

    def test_revoked_link_never_auto_reappears(self):
        self.bank(); link = self.link()
        link.active_notification_id = None; link.revoked_at = timezone.now(); link.save()
        order = self.inspect()[0]
        self.assertTrue(order['auto_blocked'])
        with self.assertRaises(ValueError): P.create_link(order, order['candidates'][0])

    def test_excludes_cd_inactive_and_incoming(self):
        self.bank(description='THANH TOAN TIEN VANG 1 TBG260900000001')
        self.assertEqual(self.inspect()[0]['candidates'], [])
        with connection.cursor() as c:
            c.execute("UPDATE bank_notifications SET description='TBG260900000001',direction='in'")
        self.assertEqual(self.inspect()[0]['candidates'], [])
        with connection.cursor() as c:
            c.execute("UPDATE bank_notifications SET direction='out'")
            c.execute('UPDATE gold_bank SET Active=0')
        self.assertEqual(self.inspect()[0]['candidates'], [])

    def test_amount_difference_and_partial_group_blocked(self):
        self.bank(amount=99)
        self.assertFalse(self.inspect()[0]['candidates'][0]['can_link'])
        self.groups = [SimpleNamespace(pk=1,trn_ids=[self.raw[0]['TrnID'],'MISSING'],tien_ck=100,ck_bank='',ck_stk='',ck_ten='',ck_nd='')]
        self.assertEqual(self.inspect()[0]['payment_status'], 'review')

    def test_time_window_and_code_boundaries(self):
        self.bank(description='transfer')
        order = self.inspect()[0]; bank = order['candidates'][0]
        for value, expected in [('11:30:00',True),('12:00:00',True),('11:29:59',False),('12:00:01',False)]:
            order['members'][0]['CreatedDate'] = self.day+' '+value
            self.assertEqual(P.near(order,bank),expected)
        self.assertFalse(P.code_match(order,dict(description='TBG2609000000012')))

    def test_code_match_reads_bill_code_and_standard_content(self):
        # GĐ chốt 10/09/2026: nội dung chuẩn "THANH TOAN TIEN VANG {6 số cuối}"; ngân hàng nối thêm đuôi ngày giờ.
        self.bank(description='transfer')
        order = self.inspect()[0]
        khop = [
            dict(description='THANH TOAN TIEN VANG-100926-11:03:21 6253ASCB', bill_code_raw='TBG260900000001'),
            dict(description='THANH TOAN TIEN VANG 0001-100926-10:47:17 6253ASCB'),
            dict(description='thanh toan tien vang 0001'),
            dict(description='CONG TY TNHH TRANG SUC KIM HANH 2 THANH TOAN TIEN VANG', bill_code_raw='TBG260900000001'),
        ]
        for bank in khop:
            self.assertTrue(P.code_match(order, bank), bank)
        lech = [
            # nội dung KHÔNG mang mã: 100926 là NGÀY, tuyệt đối không được nhận nhầm thành mã phiếu
            dict(description='THANH TOAN TIEN VANG-100926-11:03:21 6253ASCB'),
            dict(description='THANH TOAN TIEN VANG 1-100926-10:47:17 6253ASCB'),
            dict(description='THANH TOAN TIEN VANG 0002-100926'),        # phiếu khác
            dict(description='THANH TOAN TIEN VANG 100926-10:47:17'),     # 6 số là NGÀY, không phải mã
            dict(description='THANH TOAN TIEN VANG 00001-100926'),        # 5 số, không đúng form
            dict(description='TT TIEN VANG', bill_code_raw='TBG260900000002'),
            dict(description='', bill_code_raw='chi CĐ'),
        ]
        for bank in lech:
            self.assertFalse(P.code_match(order, bank), bank)

    def test_group_confirms_when_content_names_first_bill(self):
        # Nhóm 2 phiếu, nội dung chỉ nhắc phiếu ĐẦU (người đại diện nhận tiền) → xác nhận cho cả nhóm, đủ tổng tiền.
        self.raw = [self.bill(), self.bill('TBG260900000002', 150)]
        self.groups = [SimpleNamespace(pk=1, trn_ids=['TBG260900000001','TBG260900000002'], ck_bank='', ck_stk='',
                                       ck_ten='', ck_nd='', tien_ck=Decimal(250), pay_method='bank')]
        self.bank(description='THANH TOAN TIEN VANG 0001-100926-10:47:17 6253ASCB', amount=250)
        order = self.inspect()[0]
        self.assertEqual(order['required'], Decimal(250))
        self.assertTrue(order['candidates'][0]['exact'])
        self.assertTrue(order['candidates'][0]['can_link'])

    def test_default_transfer_note_uses_bill_suffix(self):
        # Ô nội dung CK trên trang thâu mặc định "THANH TOAN TIEN VANG {4 số cuối mã phiếu}" (GĐ chốt 10/09/2026)
        from apps.pos import thau_cart as TC
        self.assertEqual(TC.noi_dung_ck({'trn_ids': ['TBG260900001445']}), 'THANH TOAN TIEN VANG 1445')
        self.assertEqual(TC.noi_dung_ck({'trn_ids': ['TBG260900000495', 'TBG260900000496']}),
                         'THANH TOAN TIEN VANG 0495')          # nhóm → lấy phiếu ĐẦU
        self.assertEqual(TC.noi_dung_ck({}), 'THANH TOAN TIEN VANG')   # chưa chốt, chưa có mã
        self.assertLessEqual(len(TC.noi_dung_ck({'trn_ids': ['TBG260900001445']})), 25)   # maxlength của ô nhập
        # chuỗi sinh ra phải khớp lại được đúng phiếu đó
        order = dict(ids=['TBG260900001445'], members=[dict(BillCode='26-09-10-000058')])
        self.assertTrue(P.code_match(order, dict(description=TC.noi_dung_ck({'trn_ids': ['TBG260900001445']})
                                                 + '-100926-10:47:17 6253ASCB')))

    def test_api_permission_and_today_guard(self):
        self.bank()
        response = self.client.post('/banle/thau-vao-2/doi-soat/',dict(d1='2000-01-01',d2='2000-01-01',automatic='1'))
        self.assertEqual(response.json()['changed'],0)
        self.user.is_superuser = False; self.user.save()
        self.assertEqual(self.client.post('/banle/thau-vao-2/doi-soat/').status_code,403)

    def test_api_scan_and_revoke_audit(self):
        self.bank()
        data = dict(d1=self.day,d2=self.day,action='scan')
        with patch.object(P,'single_run',return_value=nullcontext(True)):
            response = self.client.post('/banle/thau-vao-2/doi-soat/',data)
        self.assertEqual(response.json()['changed'],1)
        link = ThauPaymentLink.objects.get()
        data.update(action='unlink',link_id=link.pk,reason='Wrong recipient',passcode='test')
        with patch.object(P,'single_run',return_value=nullcontext(True)), patch('apps.pos.views._passcode_dung',return_value=True):
            response = self.client.post('/banle/thau-vao-2/doi-soat/',data)
        self.assertEqual(response.json()['changed'],1)
        link.refresh_from_db()
        self.assertIsNone(link.active_notification_id)
        self.assertEqual(link.revoked_by,self.user.username)
        self.assertEqual(link.revoke_reason,'Wrong recipient')

    def test_api_stale_token_rejected(self):
        self.bank(); order = self.inspect()[0]
        data = dict(d1=self.day,d2=self.day,action='link',order_key=order['order_key'],bank_id=1,token=order['candidates'][0]['token'],reason='Checked',passcode='test')
        self.raw[0]['BillCode']='CHANGED'
        with patch.object(P,'single_run',return_value=nullcontext(True)), patch('apps.pos.views._passcode_dung',return_value=True):
            response = self.client.post('/banle/thau-vao-2/doi-soat/',data)
        self.assertEqual(response.status_code,409)
        self.assertFalse(ThauPaymentLink.objects.exists())
