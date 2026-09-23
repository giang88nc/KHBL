# -*- coding: utf-8 -*-
"""TIỆM CHUYỂN KHOẢN CHO KHÁCH khi bán-đổi dư (GĐ chốt 19/09/2026).

Chạy: manage.py test tests.test_ck_tra_khach --settings=config.settings.test_price_save
(SQLite riêng tên test_ — KHÔNG chạy bằng unittest, xem tests/__init__.py).
Máy KK và gateway đều được giả lập: không một lệnh nào tới PMV thật.
"""
import datetime as dt
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.sessions.backends.db import SessionStore
from django.db import connection
from django.test import RequestFactory, TransactionTestCase

from apps.pos import cart, ck_tra_khach as CK, money_flow as MF, thau_payments as P
from apps.pos.models import MoneyFlow, ThauNhom, ThauPaymentLink

DAY = '2026-09-09'
TK = '666141168'
TRB = 'TRB260900000220'       # hóa đơn bán-đổi — nội dung CK theo 4 số cuối SỐ HĐ (0058), không theo TrnID (22/09/2026)
TBG = 'TBG260900000220'       # phiếu thâu TRÙNG đuôi 0220 (tình huống tranh chấp)


def jpeg():
    from io import BytesIO
    from PIL import Image
    out = BytesIO()
    Image.new('RGB', (40, 30), (200, 180, 90)).save(out, 'JPEG')
    return out.getvalue()


def qr_png(chuoi):
    from io import BytesIO
    import segno
    out = BytesIO()
    segno.make(chuoi, error='m').save(out, kind='png', scale=8, border=4)
    return out.getvalue()


def g_gio(ban, doi, pay_method='bank', tien_mat=''):
    g = cart._rong()
    g['ban'] = [{'tien': str(ban), 'row': {}}] if ban else []
    g['doi'] = [{'tien': str(doi), 'row': {}}] if doi else []
    g['pay_method'], g['tien_mat'] = pay_method, tien_mat
    return g


class TongCuaTraKhachTests(TransactionTestCase):
    """cart.tong_cua: chia tiền mặt + CK khi tiệm trả khách; chiều khách trả giữ y cũ."""

    def test_tra_khach_mac_dinh_ck_toan_bo(self):
        t = cart.tong_cua(g_gio(10_000_000, 20_000_000))
        self.assertEqual((t['khach_tra'], t['tien_mat'], t['tien_ck']), (-10_000_000, 0, -10_000_000))

    def test_tra_khach_chia_tien_mat_va_ck(self):
        t = cart.tong_cua(g_gio(10_000_000, 20_000_000, tien_mat='4000000'))
        self.assertEqual((t['tien_mat'], t['tien_ck']), (-4_000_000, -6_000_000))
        t = cart.tong_cua(g_gio(10_000_000, 20_000_000, tien_mat='-4000000'))     # gõ kèm dấu trừ vẫn hiểu
        self.assertEqual((t['tien_mat'], t['tien_ck']), (-4_000_000, -6_000_000))
        t = cart.tong_cua(g_gio(10_000_000, 20_000_000, tien_mat='99000000'))     # quá số trả → kẹp lại
        self.assertEqual((t['tien_mat'], t['tien_ck']), (-10_000_000, 0))

    def test_tra_khach_tien_mat(self):
        t = cart.tong_cua(g_gio(10_000_000, 20_000_000, pay_method='cash'))
        self.assertEqual((t['tien_mat'], t['tien_ck']), (-10_000_000, 0))

    def test_khach_tra_duong_khong_doi(self):
        t = cart.tong_cua(g_gio(20_000_000, 5_000_000, tien_mat='3000000'))
        self.assertEqual((t['tien_mat'], t['tien_ck']), (3_000_000, 12_000_000))
        t = cart.tong_cua(g_gio(20_000_000, 5_000_000, pay_method='cash'))
        self.assertEqual((t['tien_mat'], t['tien_ck']), (15_000_000, 0))

    def test_chan_quet_the_khi_tra_khach(self):
        self.assertTrue(CK.kiem_truoc_chot(g_gio(10_000_000, 20_000_000, pay_method='card')))
        self.assertTrue(CK.kiem_truoc_chot(g_gio(20_000_000, 10_000_000, pay_method='card')))   # quẹt thẻ chưa áp dụng
        self.assertEqual(CK.kiem_truoc_chot({'trn_id': 'x'}), '')        # giỏ thiếu khóa vẫn đi qua như cũ


class NhomSauChotTests(TransactionTestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('ban-test', password='x')
        self.req = RequestFactory().post('/')
        self.req.user = self.user
        self.req.session = SessionStore()
        self.req.session.create()

    def anh_cho(self, slot, data=b'JPEG'):
        from apps.pos.models import ThauAnhTam
        ThauAnhTam.objects.create(session_key=self.req.session.session_key, slot=slot, data=data)

    def test_anh_cho_ghi_vao_nhom_ke_ca_khach_tra_duong(self):
        self.anh_cho('bhinh1', jpeg())
        g = g_gio(20_000_000, 10_000_000)                  # khách trả DƯƠNG, không CK — chỉ có hình vàng đổi
        g['trn_id'] = TRB
        n = CK.ghi_nhom_sau_chot(self.req, g, cart.tong_cua(g), 'x')
        self.assertEqual((n.tien_ck, n.tien_mat, n.pay_method), (0, 0, 'cash'))
        self.assertTrue(n.anh_hinh1 and not n.anh_hinh2)
        from apps.pos.models import ThauAnhTam
        self.assertFalse(ThauAnhTam.objects.filter(session_key=self.req.session.session_key).exists())

    def test_ban_lai_chep_anh_nhom_cu_qr_chi_khi_dung_tk(self):
        self.anh_cho('bhinh2', jpeg())
        self.anh_cho('bqr', jpeg())
        g = self.gio()
        CK.ghi_nhom_sau_chot(self.req, g, cart.tong_cua(g), 'x')
        n2 = CK.ghi_nhom_sau_chot(self.req, self.gio(), cart.tong_cua(self.gio()), 'x')       # SỬA → bán lại
        self.assertTrue(n2.anh_hinh2 and n2.anh_qr)
        g = self.gio(); g['ck_stk'] = '999'
        n3 = CK.ghi_nhom_sau_chot(self.req, g, cart.tong_cua(g), 'x')                        # đổi TK → QR cũ sai
        self.assertTrue(n3.anh_hinh2)
        self.assertFalse(n3.anh_qr)

    def test_o_qr_chi_nhan_dung_ma_ck(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        from django.http import HttpResponse
        from apps.pos import views as V, vietqr as QR
        from apps.pos.models import ThauAnhTam
        try:
            import cv2  # noqa: F401
        except ImportError:
            self.skipTest('máy không có OpenCV')
        g = self.gio(); g['ck_bank'] = g['ck_stk'] = g['ck_ten'] = ''
        cart.save(self.req, g)
        with patch.object(V, '_pos_oob', side_effect=lambda r, e=None: HttpResponse((e or {}).get('tin', 'ok'))), \
             patch.object(V, '_loi', side_effect=lambda r, m, e=None: HttpResponse('LOI ' + m, status=409)):
            self.req.FILES['anh_bqr'] = SimpleUploadedFile('a.jpg', jpeg(), 'image/jpeg')
            self.assertEqual(CK.ban_anh_len(self.req).status_code, 409)             # ảnh thường → từ chối
            self.assertFalse(ThauAnhTam.objects.filter(slot='bqr').exists())
            self.req.FILES['anh_bqr'] = SimpleUploadedFile('q.png', qr_png(QR.payload('ACB', '555666', 0, '', 'LE VAN C')), 'image/png')
            r = CK.ban_anh_len(self.req)
            self.assertEqual(r.status_code, 200, r.content)
        g = cart.get(self.req)
        self.assertEqual((g['ck_bank'], g['ck_stk'], g['ck_ten']), ('ACB', '555666', 'LE VAN C'))
        self.assertTrue(ThauAnhTam.objects.filter(slot='bqr').exists())
        self.assertTrue(CK.anh_o(self.req, g)['bqr']['tam'])

    def gio(self, **kw):
        g = g_gio(10_000_000, 20_000_000, **kw)
        g.update(trn_id=TRB, bill_code='26-09-19-000058', cust={'id': 'KH1', 'name': 'Nguyễn Thị Lan'}, emp='NV1',
                 ck_bank='ACB', ck_stk='123456', ck_ten='NGUYEN THI LAN')
        return g

    def test_doi_phuong_thuc_sau_chot_chieu_out_ghi_nhom_khi_khac(self):
        from apps.pos import gold_bill as GB
        g = self.gio(); g['status'] = 'C'
        CK.ghi_nhom_sau_chot(self.req, g, cart.tong_cua(g), 'x')
        with patch.object(GB, 'upsert_tu_gio') as ghi_gb:
            CK.sau_chot_ghi_lai(self.req, g)                                   # không đổi gì → không thêm nhóm
            self.assertEqual(ThauNhom.objects.filter(nghiep_vu='doi').count(), 1)
            g['tien_mat'] = '3000000'                                          # chia lại 3tr mặt + 7tr CK
            CK.sau_chot_ghi_lai(self.req, g)
            self.assertEqual(ghi_gb.call_count, 2)                             # gold_bill ghi hình thức trả mỗi lần
        n = CK.nhom_doi(TRB)
        self.assertEqual((ThauNhom.objects.filter(nghiep_vu='doi').count(), n.tien_mat, n.tien_ck), (2, 3_000_000, 7_000_000))

    def test_ghi_nhom_doi_so_duong(self):
        g = self.gio(tien_mat='4000000')
        n = CK.ghi_nhom_sau_chot(self.req, g, cart.tong_cua(g), '26-09-19-000058')
        self.assertEqual((n.nghiep_vu, n.trn_ids, n.tien_mat, n.tien_ck, n.pay_method),
                         ('doi', [TRB], 4_000_000, 6_000_000, 'mixed'))
        self.assertEqual(n.ck_nd, 'THANH TOAN TIEN VANG 0058')
        self.assertEqual(CK.nhom_doi(TRB).pk, n.pk)

    def test_khong_ghi_khi_khach_tra_hoac_tien_mat(self):
        g = self.gio(pay_method='cash')
        self.assertIsNone(CK.ghi_nhom_sau_chot(self.req, g, cart.tong_cua(g), 'x'))
        g = g_gio(20_000_000, 10_000_000)
        g['trn_id'] = TRB
        self.assertIsNone(CK.ghi_nhom_sau_chot(self.req, g, cart.tong_cua(g), 'x'))
        self.assertEqual(ThauNhom.objects.count(), 0)

    def test_sua_sang_tien_mat_ghi_nhom_ck_0(self):
        g = self.gio()
        CK.ghi_nhom_sau_chot(self.req, g, cart.tong_cua(g), 'x')
        g = self.gio(pay_method='cash')
        n = CK.ghi_nhom_sau_chot(self.req, g, cart.tong_cua(g), 'x')
        self.assertEqual((n.tien_ck, n.ck_stk), (0, ''))
        self.assertEqual(CK.nhom_doi(TRB).pk, n.pk)          # nhóm MỚI NHẤT là bản đúng

    def test_quet_qr_dien_tai_khoan_khach(self):
        from apps.pos import vietqr as QR
        chuoi = QR.payload('ACB', '987654321', 0, '', 'TRAN VAN B')
        g = self.gio()
        tin, loi = CK.quet_qr(g, chuoi)
        self.assertEqual(loi, '')
        self.assertEqual((g['ck_bank'], g['ck_stk'], g['pay_method']), ('ACB', '987654321', 'bank'))
        self.assertEqual(g['ck_nd'], 'THANH TOAN TIEN VANG 0058')


class DoiSoatDoiTests(TransactionTestCase):
    """Đối soát: hóa đơn đổi vào chung bộ luật, tranh chấp xét theo LOẠI, CK ghi lên KK sau khi khớp."""

    def setUp(self):
        with connection.cursor() as c:
            c.execute('CREATE TABLE bank_notifications (id integer primary key, provider text, ref_code text, bank_number text, bank_name text, trans_amount decimal, transaction_time text, direction text, description text, bill_code_raw text, ma_chung_tu text, loai_chung_tu text)')
            c.execute('CREATE TABLE gold_bank (bank_number text, Active integer)')
            c.execute("INSERT INTO gold_bank VALUES ('666141168',1)")
        self.user = get_user_model().objects.create_superuser('doi-test', password='test')
        self.thau_raw, self.thau_groups = [], []
        self.kk_doi = [self.hd_doi()]
        for p in (patch.object(P.S, 'hoa_don_loc', side_effect=lambda *a, **kw: (self.thau_raw, True)),
                  patch.object(P, 'groups_for', side_effect=lambda ids: self.thau_groups),
                  patch.object(P, 'history_for', side_effect=lambda ids: [
                      l for l in ThauPaymentLink.objects.order_by('-pk') if set(l.trn_ids) & set(ids)]),
                  patch('apps.pmv.client.PmvClient', side_effect=lambda *a, **kw: SimpleNamespace(
                      query=lambda sql, params=(): [r for r in self.kk_doi if r['TrnID'] in params]))):
            p.start()
            self.addCleanup(p.stop)
        self.nhom = ThauNhom.objects.create(nghiep_vu='doi', trn_ids=[TRB], bill_codes=['26-09-09-000058'],
                                            cust_id='KH1', cust_name='Lan', pay_method='mixed', tien_mat=4_000_000,
                                            tien_ck=6_000_000, ck_bank='ACB', ck_stk='123', ck_ten='LAN',
                                            ck_nd='THANH TOAN TIEN VANG 0058')

    def tearDown(self):
        with connection.cursor() as c:
            c.execute('DROP TABLE bank_notifications')
            c.execute('DROP TABLE gold_bank')

    def hd_doi(self, card=0):
        return dict(TrnID=TRB, BillCode='26-09-09-000058', TrnDate=dt.datetime(2026, 9, 9), TrnTime='11:40:00',
                    Status='C', IsDel='0', SoTien=Decimal(-10_000_000), CardPay=Decimal(card),
                    CreatedDate=dt.datetime(2026, 9, 9, 11, 40), CustID='KH1', CustName='Lan', Phone='09', CMND='0',
                    EmpName='NV')

    def bank(self, id=1, amount=6_000_000, duoi='0058'):   # 4 số cuối SỐ HĐ 26-09-09-000058 (22/09/2026)
        with connection.cursor() as c:
            c.execute('INSERT INTO bank_notifications (id,provider,ref_code,bank_number,bank_name,trans_amount,transaction_time,direction,description,bill_code_raw) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                      [id, 'sepay', f'REF{id}', TK, 'ACB', amount, DAY + ' 12:00:00', 'out',
                       f'THANH TOAN TIEN VANG {duoi}-090926-12:00:00 6253ASCB', ''])

    def orders(self):
        return {o['ids'][0]: o for o in P.inspect(DAY, DAY)[0]}

    def test_hoa_don_doi_khop_va_tu_noi_duoc(self):
        self.bank()
        od = self.orders()[TRB]
        self.assertEqual((od['nghiep_vu'], od['required'], od['SoTien']), ('doi', 6_000_000, 10_000_000))
        item = od['candidates'][0]
        self.assertTrue(item['can_link'] and item['exact'] and not item['ambiguous'])
        P.create_link(od, item, reason='test')           # đường tự nối (mode auto) như job doi_soat_ck
        od = self.orders()[TRB]
        self.assertEqual((od['payment_status'], od['paid']), ('confirmed', 6_000_000))

    def test_lech_tien_khong_noi(self):
        self.bank(amount=5_000_000)
        self.assertFalse(self.orders()[TRB]['candidates'][0]['can_link'])

    def test_trung_duoi_voi_phieu_thau_khac_tien_khong_tranh_chap(self):
        self.thau_raw = [dict(TrnID=TBG, BillCode='26-09-08-000058', CreatedDate=dt.datetime(2026, 9, 9, 11),
                              Status='C', IsDel='0', CardPay=-Decimal(2_000_000), SoTien=Decimal(2_000_000),
                              TienMua=Decimal(2_000_000), loai='THAU', TrnDate=dt.datetime(2026, 9, 9), TrnTime='11:00:00')]
        self.bank()                                       # 6tr — đúng hóa đơn đổi, lệch phiếu thâu
        ds = self.orders()
        self.assertFalse(ds[TRB]['candidates'][0]['ambiguous'])       # khác loại + khác tiền → không tranh chấp
        self.assertTrue(ds[TBG]['candidates'][0]['ambiguous'])        # phía thâu vẫn thận trọng như cũ
        self.assertFalse(ds[TBG]['candidates'][0]['can_link'])

    def test_trung_duoi_cung_tien_la_tranh_chap(self):
        self.thau_raw = [dict(TrnID=TBG, BillCode='26-09-08-000058', CreatedDate=dt.datetime(2026, 9, 9, 11),
                              Status='C', IsDel='0', CardPay=-Decimal(6_000_000), SoTien=Decimal(6_000_000),
                              TienMua=Decimal(6_000_000), loai='THAU', TrnDate=dt.datetime(2026, 9, 9), TrnTime='11:00:00')]
        self.bank()
        ds = self.orders()
        self.assertTrue(ds[TRB]['candidates'][0]['ambiguous'])
        self.assertTrue(ds[TBG]['candidates'][0]['ambiguous'])
        with self.assertRaises(ValueError):
            P.create_link(ds[TRB], ds[TRB]['candidates'][0])          # không tự nối — phải người xác nhận

    def test_tranh_chap_chi_thau_y_luat_cu(self):
        od = {'order_key': 'a', 'nghiep_vu': 'thau'}
        item = {'id': 1, 'trans_amount': 100}
        self.assertTrue(P.tranh_chap(od, item, {1: [('thau', 999, 'a'), ('thau', 1, 'b')]}))
        self.assertFalse(P.tranh_chap(od, item, {1: [('thau', 999, 'a')]}))

    def test_ghi_ck_len_kk_sau_khop_va_tra_ve_khi_go(self):
        self.bank()
        od = self.orders()[TRB]
        link = P.create_link(od, od['candidates'][0], reason='test')
        with patch('apps.pmv.gateway.pmv_out_snapshot_retail', return_value={'CardPay': '0', 'PayAmount': '-10000000'}), \
             patch('apps.pmv.gateway.pmv_out_allocate_retail') as ghi:
            self.assertEqual(CK.dong_bo_ck_kk(P.inspect(DAY, DAY)[0]), (1, []))
            self.assertEqual(ghi.call_args[0][1], Decimal(-6_000_000))
        # KK đã mang CardPay −6tr → không còn "cần kiểm tra", không ghi lại
        self.kk_doi = [self.hd_doi(card=-6_000_000)]
        od = self.orders()[TRB]
        self.assertEqual((od['payment_status'], od['problems']), ('confirmed', []))
        # gỡ liên kết → CK trên KK phải về 0
        ThauPaymentLink.objects.filter(pk=link.pk).update(active_notification_id=None)
        with patch('apps.pmv.gateway.pmv_out_snapshot_retail', return_value={'CardPay': '-6000000', 'PayAmount': '-10000000'}), \
             patch('apps.pmv.gateway.pmv_out_allocate_retail') as ghi:
            self.assertEqual(CK.dong_bo_ck_kk(P.inspect(DAY, DAY)[0]), (1, []))
            self.assertEqual(ghi.call_args[0][1], Decimal(0))

    def test_ck_la_tren_kk_thi_dung(self):
        self.kk_doi = [self.hd_doi(card=-1_234_000)]
        od = self.orders()[TRB]
        self.assertIn('CK trên KK khác số đã đối soát — cần kiểm tra.', od['problems'])
        with patch('apps.pmv.gateway.pmv_out_allocate_retail') as ghi:
            self.assertEqual(CK.dong_bo_ck_kk([od]), (0, []))           # có vấn đề → không đụng KK
            ghi.assert_not_called()
        od['problems'] = []                                               # kể cả khi bỏ qua bước kiểm
        with patch('apps.pmv.gateway.pmv_out_snapshot_retail', return_value={'CardPay': '-1234000', 'PayAmount': '-10000000'}), \
             patch('apps.pmv.gateway.pmv_out_allocate_retail') as ghi:
            xong, loi = CK.dong_bo_ck_kk([od])
            self.assertEqual((xong, len(loi)), (0, 1))
            ghi.assert_not_called()

    def test_nhom_chi_co_anh_khach_tra_duong_khong_vao_doi_soat(self):
        ThauNhom.objects.filter(pk=self.nhom.pk).update(tien_ck=0, tien_mat=0)
        self.kk_doi = [dict(self.hd_doi(), SoTien=Decimal(5_000_000))]
        self.assertNotIn(TRB, self.orders())

    def test_hoa_don_huy_can_kiem_tra(self):
        self.kk_doi = [dict(self.hd_doi(), Status='W')]
        self.assertIn('Phiếu chưa chốt hoặc đã hủy.', self.orders()[TRB]['problems'])

    def test_so_dong_tien_khong_dem_nhom_doi_thanh_thau(self):
        ThauNhom.objects.create(trn_ids=[TBG], bill_codes=['x'], tien_ck=1, pay_method='bank')
        MF.sync_thau_groups()
        ids = [f.source_id for f in MoneyFlow.objects.filter(source_type='thau_nhom')]
        self.assertNotIn(str(self.nhom.pk), ids)
        self.assertEqual(len(ids), 1)


class GatewayOutTests(TransactionTestCase):
    """Kiểm điều kiện đầu vào của pmv_out_allocate_retail — dừng TRƯỚC khi mở kết nối."""

    def snap(self, **kw):
        s = dict(TrnID=TRB, BillCode='b', TrnDate='d', TrnTime='t', TrnDateTime_Upd='u', Status='C', IsDel='0',
                 CashPay='-10000000', CardPay='0', PayAmount='-10000000')
        s.update(kw)
        return s

    def test_chan_so_sai(self):
        from apps.pmv import gateway as G
        with patch.object(G, '_connect_dich') as mo:
            for snap, bank in ((self.snap(), 1), (self.snap(), -10_000_001), (self.snap(PayAmount='5'), -1),
                               ({k: v for k, v in self.snap().items() if k != 'IsDel'}, -1)):
                with self.assertRaises(ValueError):
                    G.pmv_out_allocate_retail(snap, bank, target='sandbox', verify_only=True)
            mo.assert_not_called()


class FormInTests(TransactionTestCase):
    """FORM IN (GĐ chốt 19/09/2026 tối): khách chuyển khoản vào TK tiệm — TẠO QR ghi money_flow_payment qua
    đúng money_flow_payment.save_instruction; chỉ khi đã THANH TOÁN; nội dung = mã hóa đơn bỏ gạch."""
    BANKS = [{'id': 1, 'bank_bin': 'ACB', 'bank_number': '127606', 'bank_name': 'ACB', 'bank_user': 'Tiem', 'type': 'pawn'},
             {'id': 15, 'bank_bin': 'ACB', 'bank_number': '666141168', 'bank_name': 'ACB', 'bank_user': 'Cty', 'type': 'cty'}]

    def setUp(self):
        from django.contrib.auth.models import AnonymousUser
        from apps.pos.models import MoneyFlow
        self.req = RequestFactory().post('/')
        self.req.user = get_user_model().objects.create_superuser('in-test', password='x')
        self.req.session = SessionStore(); self.req.session.create()
        self.flow = MoneyFlow.objects.create(direction='IN', service='RETAIL', source_system='KHBL',
            source_type='gold_bill_retail', source_id='TRB260900000195', source_bill_code='26-09-19-000195',
            business_date=dt.date.today(), expected_amount=10_000_000, cash_amount=10_000_000)
        self.kk_rows = []                                   # máy KK giả: TRN_RT_BUYSELL PayAmount/CashPay/CardPay
        for p in (patch('apps.pos.vietqr.active_banks', return_value=self.BANKS),
                  patch('apps.pos.money_in.check_source'), patch('apps.pos.quyen.chan'),
                  patch('apps.pos.services.client', side_effect=lambda *a, **k: SimpleNamespace(
                      query=lambda sql, params=(): list(self.kk_rows)))):
            p.start(); self.addCleanup(p.stop)

    def gio(self, status='C', **kw):
        g = g_gio(10_000_000, 0, **kw)
        g.update(trn_id='TRB260900000195', bill_code='26-09-19-000195', status=status)
        cart.save(self.req, g)
        return g

    def goi(self, qr_tien='10.000.000'):
        from django.http import HttpResponse, QueryDict
        from apps.pos import views as V
        self.req.POST = QueryDict('', mutable=True)
        self.req.POST['qr_tien'] = qr_tien
        with patch.object(V, '_pos_oob', side_effect=lambda r, e=None: HttpResponse((e or {}).get('tin', ''))), \
             patch.object(V, '_loi', side_effect=lambda r, m, e=None: HttpResponse(m, status=409)):
            return CK.ban_in_qr(self.req)

    def test_mac_dinh_tk_cty_va_noi_dung_co_dinh(self):
        self.assertEqual(CK.tk_tiem_mac_dinh(), 15)
        self.assertEqual(CK.noi_dung_in({'bill_code': '26-09-19-000195'}), '260919000195')

    def test_chua_thanh_toan_khong_tao_qr(self):
        from apps.pos.models import MoneyFlowPayment
        self.gio(status='W')
        self.assertEqual(self.goi().status_code, 409)
        self.assertEqual(MoneyFlowPayment.objects.count(), 0)

    def test_tao_qr_vo_han_khong_vuot_con_lai(self):
        """GĐ chốt 19/09 tối: TẠO QR theo số nhập, bao nhiêu lần cũng được, không vượt phần còn phải thanh toán."""
        from apps.pos.models import MoneyFlowPayment
        self.gio(tien_mat='4000000')
        r = self.goi('6.000.000')
        self.assertEqual(r.status_code, 200, r.content)
        lenh = MoneyFlowPayment.objects.get()
        self.assertEqual((lenh.method, lenh.status, lenh.amount, lenh.bank_account, lenh.transfer_content, lenh.flow_id),
                         ('BANK', 'ready', 6_000_000, '666141168', '260919000195', self.flow.pk))
        self.assertIn('260919000195', lenh.qr_payload)
        self.assertEqual(self.goi('6.000.000').status_code, 200)          # cùng số → dùng lại, không đẻ thêm dòng
        self.assertEqual(MoneyFlowPayment.objects.count(), 1)
        self.assertEqual(self.goi('2.500.000').status_code, 200)          # số khác → QR mới, QR cũ vẫn giữ để nhận tiền
        self.assertEqual(MoneyFlowPayment.objects.count(), 2)
        r = self.goi('10.000.001')
        self.assertEqual(r.status_code, 409)
        self.assertIn('vượt phần còn phải thanh toán', r.content.decode())
        self.assertEqual(self.goi('0').status_code, 409)
        lenh2, anh = CK.qr_in_hien(cart.get(self.req))
        self.assertEqual(lenh2.amount, 2_500_000)
        self.assertTrue(anh.startswith('data:image/png;base64,'))
        self.flow.refresh_from_db()                                       # QR không đổi hóa đơn / dòng tiền
        self.assertEqual((self.flow.cash_amount, self.flow.bank_amount), (10_000_000, 0))

    def test_ds_qr_theo_noi_dung_va_dau_check(self):
        from apps.pos.models import MoneyFlowBankReceipt
        self.gio()
        self.goi('6.000.000'); self.goi('4.000.000')
        MoneyFlowBankReceipt.objects.create(flow=self.flow, notification_id=1, bank_identity='x1', amount=6_000_000,
                                            bank_account='666141168', status='applied')
        tt = CK.qr_in_tinh_trang(cart.get(self.req))
        self.assertEqual([(q['amount'], q['checked']) for q in tt['ds']], [(4_000_000, False), (6_000_000, True)])
        self.assertEqual((tt['da_nhan'], tt['con_lai']), (6_000_000, 4_000_000))
        self.assertEqual(self.goi('4.000.001').status_code, 409)          # còn lại chỉ 4tr

    def test_khach_tra_am_khong_tao_qr_in(self):
        g = g_gio(0, 10_000_000)
        g.update(trn_id='TRB260900000195', bill_code='26-09-19-000195', status='C')
        cart.save(self.req, g)
        self.assertEqual(self.goi().status_code, 409)
        self.assertTrue(CK.kiem_truoc_chot({'pay_method': 'card'}))

    def test_doi_phuong_thuc_sau_chot_chi_o_form_thanh_toan(self):
        from django.http import QueryDict
        self.gio()
        def co(**kw):
            self.req.POST = QueryDict('', mutable=True)
            for k, v in kw.items():
                self.req.POST[k] = v
            return CK.sua_tt_sau_chot(self.req)
        g = cart.get(self.req); g['ngay'] = dt.date.today().isoformat(); cart.save(self.req, g)
        self.assertTrue(co(pay_method='bank', tien_mat='', bank_id='15', csrfmiddlewaretoken='x'))
        self.assertFalse(co(pay_method='bank', bot='100000'))                 # ô khác vẫn khóa
        self.assertFalse(co(csrfmiddlewaretoken='x'))
        g['ngay'] = '2026-01-01'; cart.save(self.req, g)
        self.assertFalse(co(pay_method='bank'))                               # đơn ngày cũ: chỉ xem
        g['ngay'] = dt.date.today().isoformat(); g['status'] = 'W'; cart.save(self.req, g)
        self.assertFalse(co(pay_method='bank'))                               # đơn nháp đi đường thường

    def test_dong_tien_cu_chieu_out_sau_khi_sua_van_tao_duoc(self):
        """Sự cố 19/09/2026 20:17: hóa đơn sửa từ tiệm-trả (OUT) sang khách-trả, dòng tiền trên sổ còn bản OUT cũ."""
        from apps.pos.models import GoldBill, MoneyFlowPayment
        self.flow.direction, self.flow.expected_amount, self.flow.cash_amount = 'OUT', 2_832_000, 2_832_000
        self.flow.save()
        GoldBill.all_objects.create(target='kk', trn_id='TRB260900000195', bill_code='26-09-19-000195',
                                    trn_date=dt.date.today(), status='C', tong=10_000_000, tien_mat=0,
                                    tien_ck=10_000_000, pay_method='bank')
        self.gio()
        r = self.goi()
        self.assertEqual(r.status_code, 200, r.content)
        self.flow.refresh_from_db()
        self.assertEqual((self.flow.direction, self.flow.expected_amount), ('IN', 10_000_000))
        self.assertEqual(MoneyFlowPayment.objects.get().bank_account, '666141168')

    def test_so_chua_khop_thi_bao_ro(self):
        from apps.pos.models import MoneyFlowPayment
        self.flow.direction = 'OUT'; self.flow.save()          # không có gold_bill để chiếu lại
        self.gio()
        r = self.goi()
        self.assertEqual(r.status_code, 409)
        self.assertIn('Sổ IN/OUT chưa khớp', r.content.decode())
        self.assertEqual(MoneyFlowPayment.objects.count(), 0)

    def test_doc_lai_kk_cashpay_la_so_con_phai_thu(self):
        """GĐ chốt 19/09 tối: bấm CHUYỂN KHOẢN → đọc lại CashPay/CardPay trên KK; CashPay = số thực tế còn phải thu."""
        from apps.pos.models import MoneyFlowPayment
        g = self.gio()
        self.kk_rows = [{'PayAmount': Decimal(10_000_000), 'CashPay': Decimal(3_000_000), 'CardPay': Decimal(7_000_000)}]
        CK.doc_tien_kk(g)
        kk = CK.tien_kk(g)
        self.assertEqual((kk['con'], kk['xong']), (3_000_000, False))
        cart.save(self.req, g)
        self.assertEqual(self.goi('4.000.000').status_code, 409)        # vượt CashPay 3tr → chặn
        self.assertEqual(self.goi('3.000.000').status_code, 200)
        self.assertEqual(MoneyFlowPayment.objects.get().amount, 3_000_000)

    def test_cashpay_0_la_xac_nhan_xong(self):
        from apps.pos.models import MoneyFlowBankReceipt
        g = self.gio()
        self.kk_rows = [{'PayAmount': Decimal(10_000_000), 'CashPay': Decimal(0), 'CardPay': Decimal(10_000_000)}]
        CK.doc_tien_kk(g); cart.save(self.req, g)
        self.assertTrue(CK.tien_kk(g)['xong'])
        r = self.goi('1.000.000')
        self.assertEqual(r.status_code, 409)
        self.assertIn('XÁC NHẬN XONG', r.content.decode())
        MoneyFlowBankReceipt.objects.create(flow=self.flow, notification_id=7, bank_identity='b7', amount=10_000_000,
                                            bank_account='666141168', bank_ref='FT123', status='applied',
                                            evidence={'transaction_time': '2026-09-19 20:31:05', 'description': '260919000195'})
        bc = CK.bang_chung(g)
        self.assertEqual([(b['gio'], b['amount'], b['ref']) for b in bc], [('20:31', 10_000_000, 'FT123')])

    def test_doc_kk_chi_khi_da_chot_va_loi_khong_chan(self):
        g = self.gio(status='W')
        self.assertIsNone(CK.doc_tien_kk(g))
        g = self.gio()
        self.kk_rows = []                                                    # KK không thấy hóa đơn
        CK.doc_tien_kk(g)
        self.assertTrue(CK.tien_kk(g)['loi'])
        g['trn_id'] = 'KHAC'
        self.assertIsNone(CK.tien_kk(g))                                     # số của hóa đơn khác không dùng lại

    def test_popup_xem_qr_chi_cua_hoa_don_dang_mo(self):
        from apps.pos.models import MoneyFlowPayment
        self.gio()
        self.goi('6.000.000')
        q = MoneyFlowPayment.objects.get()
        tt = CK.qr_in_tinh_trang(cart.get(self.req))
        self.assertEqual(tt['ds'][0]['pk'], q.pk)
        r = CK.ban_in_qr_xem(self.req, q.pk).content.decode()
        self.assertIn('data:image/png;base64,', r)
        self.assertIn('260919000195', r)
        g = cart.get(self.req); g['bill_code'] = '26-09-19-000999'; cart.save(self.req, g)
        r = CK.ban_in_qr_xem(self.req, q.pk).content.decode()
        self.assertNotIn('data:image/png;base64,', r)                     # QR của hóa đơn khác → không mở
