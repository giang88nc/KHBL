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
            c.execute('CREATE TABLE bank_notifications (id integer primary key, provider text, ref_code text, bank_number text, bank_name text, trans_amount decimal, transaction_time text, direction text, description text, bill_code_raw text, ma_chung_tu text, loai_chung_tu text)')
            c.execute('CREATE TABLE gold_bank (bank_number text, Active integer)')
            c.execute("INSERT INTO gold_bank VALUES ('666141168',1)")
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

    # CHUẨN MỚI (GĐ chốt 11/09/2026): chỉ giao dịch OUT từ tài khoản trả tiền của tiệm, nội dung mang
    # "THANH TOAN TIEN VANG {4 số cuối mã phiếu}" mới được xét. Mặc định của helper dựng đúng khuôn đó.
    TK = '666141168'

    def nd_chuan(self, trn='TBG260900000001'):
        return 'THANH TOAN TIEN VANG ' + trn[-4:] + '-090926-12:00:00 6253ASCB'

    def bank(self, id=1, description=None, amount=100, ref=None, tk=None):
        if description is None:
            description = self.nd_chuan()
        with connection.cursor() as c:
            c.execute('INSERT INTO bank_notifications (id,provider,ref_code,bank_number,bank_name,trans_amount,transaction_time,direction,description,bill_code_raw) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                      [id,'sepay',ref or f'REF{id}',tk or self.TK,'ACB',amount,self.day+' 12:00:00','out',description,''])

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

    def test_amount_time_only_no_longer_candidate(self):
        """CHUẨN MỚI: đúng số tiền, đúng giờ nhưng nội dung không mang mã phiếu → KHÔNG còn là ứng viên."""
        self.bank(description='CHUYEN TIEN')          # cùng số tiền, cùng ngày giờ với phiếu
        order = self.inspect()[0]
        self.assertEqual(order['candidates'], [])
        self.assertEqual(order['payment_status'], 'unconfirmed')
        # ghi đúng khuôn thì bắt được ngay
        self.bank(id=2, ref='REF2')
        order = self.inspect()[0]
        self.assertTrue(order['candidates'][0]['can_link'] and order['candidates'][0]['exact'])

    def test_ambiguous_groups_cannot_auto_but_allow_review(self):
        # hai phiếu KHÁC THÁNG nhưng trùng 4 số cuối → một giao dịch khớp cả hai, không được tự nối
        self.raw.append(self.bill('TBG260800000001'))
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

    def test_new_standard_rules(self):
        """CHUẨN MỚI: chỉ nội dung mang đúng 4 số cuối mã phiếu mới khớp; các đường cũ đã bỏ."""
        self.bank(description='transfer')
        order = self.inspect()[0]
        khop = ['THANH TOAN TIEN VANG 0001-090926-12:00:00 6253ASCB',
                'THANH TOAN TIEN VANG 0001',
                'thanh toan tien vang 0001']
        for nd in khop:
            self.assertTrue(P.code_match(order, dict(description=nd)), nd)
        lech = ['THANH TOAN TIEN VANG 1-090926-12:00:00',      # khoản chi khác
                'THANH TOAN TIEN VANG-090926-12:00:00',        # không mang mã
                'THANH TOAN TIEN VANG 0002-090926',            # phiếu khác
                'THANH TOAN TIEN VANG 090926',                 # 6 số là ngày
                'TBG260900000001']                             # mã đầy đủ: chuẩn CŨ, nay bỏ
        for nd in lech:
            self.assertFalse(P.code_match(order, dict(description=nd)), nd)
        # mã đầy đủ nằm ở ô mã hóa đơn cũng không còn được đọc
        self.assertFalse(P.code_match(order, dict(description='THANH TOAN TIEN VANG-090926',
                                                  bill_code_raw='TBG260900000001')))

    def test_new_standard_eligibility(self):
        """Bốn điều kiện lọc giao dịch của chuẩn mới."""
        goc = dict(direction='out', trans_amount=100, bank_number=P.TAI_KHOAN_TRA[0],
                   description='THANH TOAN TIEN VANG 0001')
        self.assertTrue(P.eligible(goc))
        self.assertFalse(P.eligible(dict(goc, direction='in')))
        self.assertFalse(P.eligible(dict(goc, bank_number='999999999')))
        self.assertFalse(P.eligible(dict(goc, trans_amount=0)))
        self.assertFalse(P.eligible(dict(goc, description='THANH TOAN TIEN VANG 1-090926')))

    def test_other_account_never_matches(self):
        """Tiền ra từ tài khoản KHÁC của tiệm không được nhận, dù nội dung đúng khuôn."""
        self.bank(tk='123456789')
        self.assertEqual(self.inspect()[0]['candidates'], [])

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
        # GĐ chốt 22/09/2026 (ĐẢO 10/09): "THANH TOAN TIEN VANG {4 số cuối SỐ HĐ đầu}" — không còn theo TrnID
        from apps.pos import thau_cart as TC
        g = {'bill_codes': ['26-09-10-000058', '26-09-10-000059'], 'trn_ids': ['TBG260900001445', 'TBG260900001446']}
        self.assertEqual(TC.noi_dung_ck(g), 'THANH TOAN TIEN VANG 0058')          # nhóm → lấy HĐ ĐẦU
        self.assertEqual(TC.noi_dung_ck({'trn_ids': ['TBG260900001445']}), 'THANH TOAN TIEN VANG')   # chưa có số HĐ
        self.assertEqual(TC.noi_dung_ck({}), 'THANH TOAN TIEN VANG')
        self.assertLessEqual(len(TC.noi_dung_ck(g)), 25)                           # maxlength của ô nhập
        # chuỗi sinh ra phải khớp lại được đúng phiếu đó — và KHÔNG khớp kiểu TrnID cũ nữa
        order = dict(ids=['TBG260900001445'], members=[dict(BillCode='26-09-10-000058')])
        self.assertTrue(P.code_match(order, dict(description=TC.noi_dung_ck(g) + '-100926-10:47:17 6253ASCB')))
        self.assertFalse(P.code_match(order, dict(description='THANH TOAN TIEN VANG 1445')))

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


    # ── Cầm đồ (22/09/2026): Cầm đồ (22/09/2026): nhóm KHCD ghi → hiện ở Thâu vào 2, khớp 6 số log phiên, chỉ TK cầm đồ (type pawn), không KK ──
    def cam_do(self):
        self.raw = []
        with connection.cursor() as c:
            c.execute('ALTER TABLE gold_bank ADD COLUMN type text')
            c.execute("INSERT INTO gold_bank VALUES ('127606',1,'pawn')")
        from apps.pos.models import ThauNhom
        self.g = ThauNhom.objects.create(nghiep_vu=ThauNhom.CAMDO, trn_ids=['KH22609020928'],
                                         bill_codes=['26-09-09-006830'], kieu=[], cust_name='Trương Ngọc Giang',
                                         pay_method='bank', tien_mat=0, tien_ck=5000000,
                                         ck_nd='THANH TOAN TIEN VANG 006830', ck_ten='TRUONG NGOC GIANG')
        ThauNhom.objects.filter(pk=self.g.pk).update(created_at=timezone.make_aware(dt.datetime(2026, 9, 9, 7, 55)))

    def test_cam_do_hien_va_khop_tk_cam_do(self):
        self.cam_do()
        self.bank(id=5, description='THANH TOAN TIEN VANG 006830-090926-08:00', amount=5000000, tk='127606')
        self.bank(id=6, description='THANH TOAN TIEN VANG 006830-090926-08:00', amount=5000000)   # TK thâu: không nhận
        o = [x for x in self.inspect() if x['nghiep_vu'] == 'camdo'][0]
        self.assertEqual((o['TrnID'], o['required'], o['SoTien']), ('KH22609020928', Decimal(5000000), Decimal(5000000)))
        self.assertEqual([c['id'] for c in o['candidates']], [5])
        self.assertTrue(o['candidates'][0]['can_link'])

    def test_cam_do_4_so_khong_nham(self):
        self.cam_do()
        self.bank(id=7, description='THANH TOAN TIEN VANG 6830-090926', amount=5000000, tk='127606')
        o = [x for x in self.inspect() if x['nghiep_vu'] == 'camdo'][0]
        self.assertEqual(o['candidates'], [])

    # ── 22/09/2026: đối soát = ma_phieu(ck_nd) == bank_notifications.ma_chung_tu ──
    def test_ma_chung_tu_cot_skill_quyet_dinh(self):
        self.bank(description='CK KHONG CO MA')        # nội dung không mang mã, nhưng cột skill đã ghi đúng mã
        with connection.cursor() as c:
            c.execute("UPDATE bank_notifications SET ma_chung_tu='260909000001', loai_chung_tu='TV' WHERE id=1")
        cand = self.inspect()[0]['candidates']
        self.assertEqual(len(cand), 1)
        self.assertTrue(cand[0]['exact'])

    def test_ck_khac_ngay_chi_xac_nhan_tay(self):
        self.bank()
        with connection.cursor() as c:
            c.execute("UPDATE bank_notifications SET transaction_time='2026-09-10 08:00:00' WHERE id=1")
        o = P.inspect(self.day, '2026-09-10')[0][0]
        self.assertEqual(len(o['candidates']), 1)
        self.assertFalse(o['candidates'][0]['exact'])    # đuôi khớp, khác ngày → không tự nối

    def test_tu_dong_khong_can_dang_nhap(self):
        from django.test import Client
        self.bank()
        with patch.object(P.timezone, 'localdate', return_value=dt.date(2026, 9, 9)), patch.object(P, 'single_run', return_value=nullcontext(True)):
            r = Client().post('/banle/thau-vao-2/doi-soat/', {'d1': self.day, 'd2': self.day, 'action': 'scan', 'automatic': '1'})
            self.assertEqual(r.status_code, 200)
            self.assertEqual(r.json()['changed'], 1)
            r = Client().post('/banle/thau-vao-2/doi-soat/', {'d1': self.day, 'd2': self.day, 'action': 'scan'})
            self.assertEqual(r.status_code, 403)    # bấm tay vẫn cần quyền
