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
        from django.core.cache import cache
        cache.clear()                       # mốc "HĐ XONG" của CHECK GOLD nằm trong bộ nhớ đệm tiến trình
        with connection.cursor() as c:      # sổ ngân hàng giả: đủ cột cho đối soát tiền RA của phiếu thâu
            c.execute('CREATE TABLE IF NOT EXISTS bank_notifications (id integer primary key, '
                      'transaction_time text, direction text, ma_chung_tu text, trans_amount numeric)')

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
        """Hết nhịp chờ thì thẻ rời bảng: phiếu VÀO đã xong và cũ hơn 30 phút không còn hiện lại,
        phiếu RA thuần tiền mặt 5 phút tuổi vẫn nằm trong nhịp 10 phút của nó."""
        from django.utils import timezone
        from apps.pos.models import MoneyFlow
        hom_nay = timezone.localdate()
        vao = MoneyFlow.objects.create(direction='IN', service='RETAIL', source_system='KHBL',
                                       source_type='gold_bill_retail', source_id='T1', source_bill_code='B1',
                                       business_date=hom_nay, expected_amount=100)
        ra = MoneyFlow.objects.create(direction='OUT', service='GOLD_BUY', source_system='KHBL',
                                      source_type='gold_bill_ungrouped', source_id='T2', source_bill_code='B2',
                                      business_date=hom_nay, expected_amount=200, cash_amount=200)
        MoneyFlow.objects.filter(pk=vao.pk).update(created_at=timezone.now() - dt.timedelta(minutes=35))
        MoneyFlow.objects.filter(pk=ra.pk).update(created_at=timezone.now() - dt.timedelta(minutes=5))
        ds = G.giao_dich(hom_nay)
        self.assertEqual([g['ma'] for g in ds], ['B2'])                  # phiếu VÀO xong + quá cũ đã đi hẳn
        # ⚠ nhịp SAU cũng không được bày lại: mốc "xong" của phiếu cũ phải ghi là CHÍNH LÚC GIAO DỊCH,
        # nếu ghi là "bây giờ" thì mỗi lần RESET web bảng sẽ dội lên vài chục thẻ đã xong.
        self.assertEqual([g['ma'] for g in G.giao_dich(hom_nay)], ['B2'])
        self.assertEqual((ds[0]['nhom'], ds[0]['ra_tien']), ('ra', True))  # thâu vàng = nhóm ĐỎ

    def test_phieu_cho_xac_nhan_tien_mat_giu_30_phut(self):
        """Bảng nhịp GĐ chốt 26/09/2026: phiếu còn tiền mặt chưa ai xác nhận (cus_cash + cus_change = 0)
        giữ 30 phút; phiếu chuyển khoản chưa đối soát cũng 30 phút; nhận đủ rồi thì 30 giây là đi."""
        from django.utils import timezone
        from apps.pos.models import MoneyFlow
        hom_nay = timezone.localdate()
        cu = timezone.now() - dt.timedelta(minutes=5)
        cho = MoneyFlow.objects.create(direction='IN', service='RETAIL', source_system='KHBL',
                                       source_type='gold_bill_retail', source_id='T3', source_bill_code='B3',
                                       business_date=hom_nay, expected_amount=500, cash_amount=500)
        xong = MoneyFlow.objects.create(direction='IN', service='RETAIL', source_system='KHBL',
                                        source_type='gold_bill_retail', source_id='T4', source_bill_code='B4',
                                        business_date=hom_nay, expected_amount=500, cash_amount=500,
                                        cus_cash=500, cus_change=0)
        ck = MoneyFlow.objects.create(direction='IN', service='RETAIL', source_system='KHBL',
                                      source_type='gold_bill_retail', source_id='T5', source_bill_code='B5',
                                      business_date=hom_nay, expected_amount=500, bank_amount=500)
        MoneyFlow.objects.filter(pk__in=[cho.pk, ck.pk]).update(created_at=cu)
        MoneyFlow.objects.filter(pk=xong.pk).update(created_at=timezone.now() - dt.timedelta(minutes=35))
        ds = {g['ma']: g for g in G.giao_dich(hom_nay)}
        self.assertEqual(sorted(ds), ['B3', 'B5'])            # B4 nhận đủ → đã đi; B3 chờ TM, B5 chờ đối soát CK
        self.assertTrue(ds['B3']['cho_tm'])
        self.assertEqual(ds['B3']['song'], G.SONG_CHO)        # 30 phút
        self.assertEqual(ds['B5']['song'], G.SONG_CHO)        # CK chưa khớp cũng 30 phút
        self.assertGreater(ds['B3']['con'], 0)                # vạch thời gian còn lại của thẻ

    def test_thoi_lai_lon_hon_0_giu_3_phut(self):
        """Bảng nhịp GĐ chốt 26/09/2026: nhận đủ (THỐI = 0) → 30 giây; còn nợ khách tiền thối → 3 phút."""
        from django.utils import timezone
        from apps.pos.models import MoneyFlow
        hom_nay = timezone.localdate()
        cu = timezone.now() - dt.timedelta(minutes=2)
        du = MoneyFlow.objects.create(direction='IN', service='RETAIL', source_system='KHBL',
                                      source_type='gold_bill_retail', source_id='T6', source_bill_code='B6',
                                      business_date=hom_nay, expected_amount=500, cash_amount=500,
                                      cus_cash=600, cus_change=100)
        du_dung = MoneyFlow.objects.create(direction='IN', service='RETAIL', source_system='KHBL',
                                           source_type='gold_bill_retail', source_id='T7', source_bill_code='B7',
                                           business_date=hom_nay, expected_amount=500, cash_amount=500,
                                           cus_cash=500, cus_change=0)
        MoneyFlow.objects.filter(pk=du.pk).update(created_at=cu)
        MoneyFlow.objects.filter(pk=du_dung.pk).update(created_at=timezone.now() - dt.timedelta(minutes=35))
        ds = G.giao_dich(hom_nay)
        self.assertEqual([g['ma'] for g in ds], ['B6'])       # còn thối cho khách → vẫn giữ; nhận đủ → đã ẩn
        self.assertEqual(ds[0]['song'], G.SONG_THOI)

    def test_tien_ra_tra_bang_ck_cho_doi_soat_roi_moi_an(self):
        """GĐ chốt 25/09/2026: phiếu THÂU trả hết bằng chuyển khoản (TM 0 · CK 40.958.000) thì "Đưa khách" = 0,
        nhưng vẫn nằm lại 10 phút CHỜ ĐỐI SOÁT; đối soát khớp (confirmed) là HĐ XONG → 15 giây rồi ẩn.
        Phiếu còn tiền mặt phải đưa thì "Đưa khách" > 0 và nổi bật y như "Thối tiền khách"."""
        from django.utils import timezone
        from apps.pos.models import MoneyFlow
        hom_nay = timezone.localdate()
        ck = MoneyFlow.objects.create(direction='OUT', service='GOLD_BUY', source_system='KHBL',
                                      source_type='thau_nhom', source_id='9', source_bill_code='B8',
                                      business_date=hom_nay, expected_amount=Decimal('40958000'),
                                      cash_amount=0, bank_amount=Decimal('40958000'), payment_status='waiting')
        tien_mat = MoneyFlow.objects.create(direction='OUT', service='GOLD_BUY', source_system='KHBL',
                                            source_type='thau_nhom', source_id='10', source_bill_code='B9',
                                            business_date=hom_nay, expected_amount=Decimal('40958000'),
                                            cash_amount=Decimal('958000'), bank_amount=Decimal('40000000'))
        cam_do = MoneyFlow.objects.create(direction='OUT', service='PAWN', source_system='KHCD',
                                          source_type='cd_loan_log', source_id='77', source_bill_code='B10',
                                          business_date=hom_nay, expected_amount=Decimal('26860000'),
                                          cash_amount=0, bank_amount=Decimal('26860000'), payment_status='waiting')
        ds = {g['ma']: g for g in G.giao_dich(hom_nay)}
        self.assertEqual(ds['B8']['dua_khach'], Decimal(0))          # trả hết bằng CK → không đưa tay đồng nào
        self.assertEqual(ds['B8']['nhan_ck'], 'Chờ chuyển khoản')    # dòng 4 nói việc còn lại, không ghi "0"
        self.assertEqual(ds['B8']['nhan_ck_lop'], 'cg-gd__do cg-gd__cho')   # chữ đỏ + nhấp nhô từng chữ
        self.assertEqual(ds['B8']['song'], G.SONG_CHO)               # chưa đối soát → 30 phút
        self.assertEqual(ds['B8']['noi_bat'], 0)                     # không phải đưa tiền → không nhấp nháy
        self.assertEqual(ds['B9']['dua_khach'], Decimal('958000'))   # còn tiền mặt phải đưa tận tay
        self.assertEqual(ds['B9']['nhan_ck'], '')                    # có tiền mặt → vẫn là dòng "Đưa khách"
        self.assertEqual(ds['B9']['song'], G.SONG_CHO)               # còn chờ đối soát CK → 30 phút
        self.assertGreater(ds['B9']['noi_bat'], 0)                   # nổi bật y như "Thối tiền khách"
        # Cầm đồ (KHCD) đi cùng một đường với thâu vàng
        self.assertEqual((ds['B10']['nhom'], ds['B10']['ra_tien']), ('ra', True))
        self.assertEqual(ds['B10']['nhan_ck'], 'Chờ chuyển khoản')
        self.assertEqual(ds['B10']['song'], G.SONG_CHO)
        MoneyFlow.objects.filter(pk=cam_do.pk).update(payment_status='confirmed')
        xong = {g['ma']: g for g in G.giao_dich(hom_nay)}['B10']      # còn non 3 phút nên vẫn thấy trên bảng
        self.assertEqual((xong['nhan_ck'], xong['nhan_ck_lop']), ('Đã chuyển khoản', 'cg-gd__nhan'))
        self.assertEqual(xong['song'], G.SONG_XONG)                   # đối soát khớp → HĐ XONG, 15 giây

        cu = timezone.now() - dt.timedelta(minutes=5)
        MoneyFlow.objects.filter(pk__in=[ck.pk, tien_mat.pk]).update(created_at=cu)
        con_lai = {g['ma'] for g in G.giao_dich(hom_nay)}
        self.assertTrue({'B8', 'B9'} <= con_lai)                     # quá 5 phút mà chưa đối soát: cả hai còn
        MoneyFlow.objects.filter(pk=ck.pk).update(payment_status='confirmed')           # CK đối soát KHỚP
        ds = {g['ma']: g for g in G.giao_dich(hom_nay)}
        self.assertEqual(ds['B8']['song'], G.SONG_XONG)              # khớp rồi → còn đúng 30 giây báo XONG
        self.assertEqual(ds['B8']['tt_nhan'], 'HĐ XONG')
        self.assertIn('B9', ds)                                      # còn chờ đối soát CK → vẫn nằm lại
        from apps.pos.models import GoldCheckMoc
        GoldCheckMoc.objects.filter(flow_id=ck.pk).update(
            xong_luc=timezone.now() - dt.timedelta(seconds=G.SONG_XONG + 10))
        self.assertNotIn('B8', {g['ma'] for g in G.giao_dich(hom_nay)})     # quá 30 giây thì đi hẳn

    def test_hd_xong_an_sau_30_giay(self):
        """GĐ chốt 25/09/2026: phiếu ĐÃ HỦY và phiếu có CK đối soát khớp (cả tiền vào lẫn tiền ra) là HĐ XONG
        — chỉ còn 15 giây trên bảng, không đợi hết nhịp 3/10 phút; phiếu còn việc ở quầy thì chưa xong."""
        from django.utils import timezone
        from apps.pos.models import MoneyFlow
        hom_nay = timezone.localdate()
        huy = MoneyFlow.objects.create(direction='IN', service='RETAIL', source_system='KHBL',
                                       source_type='gold_bill_retail', source_id='H1', source_bill_code='H1',
                                       business_date=hom_nay, expected_amount=1000, cash_amount=1000,
                                       cus_cash=1000, is_void=True, payment_status='void')
        ck_vao = MoneyFlow.objects.create(direction='IN', service='RETAIL', source_system='KHBL',
                                          source_type='gold_bill_retail', source_id='V1', source_bill_code='V1',
                                          business_date=hom_nay, expected_amount=2000, bank_amount=2000,
                                          payment_status='confirmed')
        ck_ra = MoneyFlow.objects.create(direction='OUT', service='GOLD_BUY', source_system='KHBL',
                                         source_type='thau_nhom', source_id='R1', source_bill_code='R1',
                                         business_date=hom_nay, expected_amount=3000, bank_amount=3000,
                                         payment_status='confirmed')
        con_viec = MoneyFlow.objects.create(direction='IN', service='RETAIL', source_system='KHBL',
                                            source_type='gold_bill_retail', source_id='V2', source_bill_code='V2',
                                            business_date=hom_nay, expected_amount=2000, cash_amount=500,
                                            bank_amount=1500, payment_status='confirmed')
        ds = {g['ma']: g for g in G.giao_dich(hom_nay)}
        for ma in ('H1', 'V1', 'R1'):
            self.assertEqual(ds[ma]['song'], G.SONG_XONG, ma)        # 30 giây
            self.assertLessEqual(ds[ma]['con'], G.SONG_XONG, ma)
        self.assertEqual(ds['H1']['huy'], True)                      # thẻ vẫn ghi "Đã hủy", không phải HĐ XONG
        self.assertEqual(ds['V1']['tt_nhan'], 'HĐ XONG')
        self.assertEqual(ds['R1']['nhan_ck'], 'Đã chuyển khoản')
        self.assertEqual(ds['V2']['song'], G.SONG_CHO)               # CK khớp nhưng CHƯA xác nhận tiền mặt
        self.assertEqual(ds['V2']['tt_nhan'], 'Đủ tiền')

        # lùi mốc "đã xong" quá 30 giây → cả ba rời bảng, phiếu còn việc ở quầy thì vẫn nằm lại
        from apps.pos.models import GoldCheckMoc
        GoldCheckMoc.objects.filter(flow_id__in=[huy.pk, ck_vao.pk, ck_ra.pk]).update(
            xong_luc=timezone.now() - dt.timedelta(seconds=G.SONG_XONG + 10))
        self.assertEqual([g['ma'] for g in G.giao_dich(hom_nay)], ['V2'])
        self.assertEqual(con_viec.pk, MoneyFlow.objects.get(source_bill_code='V2').pk)

    def test_bang_nhip_song_theo_bang_gd_chot(self):
        """Đúng BẢNG NHỊP GĐ chốt 26/09/2026 — mỗi dòng dưới đây là một ô trong bảng đó.

        Tiền VÀO: chờ xác nhận tiền mặt 30p · chờ đối soát CK 30p (kể cả khi tiền mặt đã xong) ·
        còn nợ khách tiền thối 3p · hết việc 30s.
        Tiền RA: thuần tiền mặt 10p · chờ đối soát CK 30p · đối soát xong 30s.  Phiếu hủy 30s.
        """
        from django.utils import timezone
        from apps.pos.models import MoneyFlow
        hom_nay = timezone.localdate()
        RA = {'direction': 'OUT', 'service': 'GOLD_BUY', 'source_type': 'thau_nhom'}
        bang = [
            ('V1', 'VÀO · TM = tổng, chưa ai xác nhận',
             dict(expected_amount=1000, cash_amount=1000), G.SONG_CHO),
            ('V2', 'VÀO · CK cả phiếu, chưa đối soát',
             dict(expected_amount=1000, bank_amount=1000), G.SONG_CHO),
            ('V3', 'VÀO · CK cả phiếu, đối soát khớp',
             dict(expected_amount=1000, bank_amount=1000, payment_status='confirmed'), G.SONG_XONG),
            ('V4', 'VÀO · TM + CK, chưa xác nhận gì',
             dict(expected_amount=1000, cash_amount=400, bank_amount=600), G.SONG_CHO),
            ('V5', 'VÀO · TM đã xác nhận nhưng CK chưa khớp',
             dict(expected_amount=1000, cash_amount=400, bank_amount=600, cus_cash=400), G.SONG_CHO),
            ('V6', 'VÀO · TM đã xác nhận và CK đã khớp',
             dict(expected_amount=1000, cash_amount=400, bank_amount=600, cus_cash=400,
                  payment_status='confirmed'), G.SONG_XONG),
            ('V7', 'VÀO · nhận dư, còn nợ khách tiền thối',
             dict(expected_amount=1000, cash_amount=1000, cus_cash=1200, cus_change=200), G.SONG_THOI),
            ('V8', 'VÀO · nhận đủ tiền mặt, thối = 0',
             dict(expected_amount=1000, cash_amount=1000, cus_cash=1000), G.SONG_XONG),
            ('R1', 'RA · thuần tiền mặt, còn phải đưa tận tay khách',
             dict(expected_amount=1000, cash_amount=1000, **RA), G.SONG_DUA),
            ('R2', 'RA · CK cả phiếu, chưa đối soát',
             dict(expected_amount=1000, bank_amount=1000, **RA), G.SONG_CHO),
            ('R3', 'RA · CK cả phiếu, đối soát khớp',
             dict(expected_amount=1000, bank_amount=1000, payment_status='confirmed', **RA), G.SONG_XONG),
            ('R4', 'RA · TM + CK chưa khớp → lấy nhịp DÀI hơn',
             dict(expected_amount=1000, cash_amount=300, bank_amount=700, **RA), G.SONG_CHO),
            ('H1', 'Phiếu đã hủy',
             dict(expected_amount=1000, cash_amount=1000, is_void=True), G.SONG_XONG),
            # GĐ chốt 26/09 tối (phiếu chuộc đồ 26090170307034): còn nợ khách tiền thối là việc GẤP,
            # xét TRƯỚC cả chờ đối soát CK — phiếu hỗn hợp TM + CK còn thối vẫn về nhịp 3 phút.
            ('V9', 'VÀO · TM + CK chưa khớp NHƯNG còn thối cho khách',
             dict(expected_amount=4784000, cash_amount=284000, bank_amount=4500000,
                  cus_cash=294000, cus_change=10000, payment_status='partial'), G.SONG_THOI),
        ]
        for ma, _mo_ta, kw, _nhip in bang:
            tham_so = {'direction': 'IN', 'service': 'RETAIL', 'source_type': 'gold_bill_retail'}
            tham_so.update(kw)
            MoneyFlow.objects.create(source_system='KHBL', source_id=ma, source_bill_code=ma,
                                     business_date=hom_nay, **tham_so)
        ds = {g['ma']: g for g in G.giao_dich(hom_nay)}
        for ma, mo_ta, _kw, nhip in bang:
            self.assertIn(ma, ds, mo_ta)
            self.assertEqual(ds[ma]['song'], nhip, ma + ' - ' + mo_ta)
        self.assertEqual((G.SONG_CHO, G.SONG_DUA, G.SONG_THOI, G.SONG_XONG), (1800, 600, 180, 30))
        # Chữ "việc còn lại" của thẻ THU GỌN, mỗi trạng thái một câu ngắn
        self.assertEqual([ds[m]['viec'] for m in ('V1', 'V2', 'V5', 'V7', 'V8', 'V9', 'R1', 'R3', 'H1')],
                         ['Chưa xác nhận TM', 'Chờ CK', 'Chờ CK', 'Thối 200', 'Xong', 'Thối 10.000',
                          'Đưa khách 1.000', 'Xong', 'Đã hủy'])

    def test_khong_tien_mat_ghi_cho_ck_va_cho_lau_thi_thu_gon(self):
        """GĐ chốt 26/09/2026: (1) phiếu KHÔNG có đồng tiền mặt nào — cả VÀO lẫn RA — ghi thẳng
        "Chờ chuyển khoản" / "Đã chuyển khoản" thay cho "Nhận 0 · Thối 0" vô nghĩa;
        (2) thẻ chờ quá 15 phút thì thu gọn còn hai dòng, thẻ vừa xong thì không thu gọn."""
        from django.utils import timezone
        from apps.pos.models import MoneyFlow
        hom_nay = timezone.localdate()
        ck_vao = MoneyFlow.objects.create(direction='IN', service='RETAIL', source_system='KHBL',
                                          source_type='gold_bill_retail', source_id='C1', source_bill_code='C1',
                                          business_date=hom_nay, expected_amount=2000, bank_amount=2000)
        co_tm = MoneyFlow.objects.create(direction='IN', service='RETAIL', source_system='KHBL',
                                         source_type='gold_bill_retail', source_id='C2', source_bill_code='C2',
                                         business_date=hom_nay, expected_amount=500, cash_amount=500)
        ds = {g['ma']: g for g in G.giao_dich(hom_nay)}
        self.assertEqual(ds['C1']['nhan_ck'], 'Chờ chuyển khoản')          # phiếu BÁN trả hết bằng CK
        self.assertEqual(ds['C1']['nhan_ck_lop'], 'cg-gd__do cg-gd__cho')  # chữ đỏ + nhấp nhô từng chữ
        self.assertEqual(ds['C2']['nhan_ck'], '')                          # có tiền mặt → vẫn "Nhận / Thối"
        self.assertFalse(ds['C1']['gon'])                                  # còn mới → hiện đủ 4 dòng

        MoneyFlow.objects.filter(pk__in=[ck_vao.pk, co_tm.pk]).update(
            created_at=timezone.now() - dt.timedelta(minutes=10))
        ds = {g['ma']: g for g in G.giao_dich(hom_nay)}
        self.assertFalse(ds['C1']['gon'])                                  # 10 phút: chưa tới ngưỡng
        MoneyFlow.objects.filter(pk__in=[ck_vao.pk, co_tm.pk]).update(
            created_at=timezone.now() - dt.timedelta(minutes=16))
        ds = {g['ma']: g for g in G.giao_dich(hom_nay)}
        self.assertTrue(ds['C1']['gon'])                                   # quá 15 phút → gọn 2 dòng
        self.assertTrue(ds['C2']['gon'])
        self.assertEqual(ds['C1']['viec'], 'Chờ CK')                       # việc còn lại gói trong vài chữ
        self.assertEqual(ds['C2']['viec'], 'Chưa xác nhận TM')

        MoneyFlow.objects.filter(pk=ck_vao.pk).update(payment_status='confirmed')
        xong = {g['ma']: g for g in G.giao_dich(hom_nay)}['C1']
        self.assertEqual(xong['nhan_ck'], 'Đã chuyển khoản')
        self.assertEqual(xong['nhan_ck_lop'], 'cg-gd__nhan')               # xong → chữ xám, hết nhấp nhô
        self.assertFalse(xong['gon'])                                      # thẻ vừa xong hiện lại đủ 30 giây
        self.assertEqual(xong['song'], G.SONG_XONG)

    def test_doi_soat_tien_ra_thau_theo_ma_chung_tu(self):
        """GĐ chốt 26/09/2026 — đối soát TẠM THỜI tiền RA của phiếu thâu: money_flow chưa ghi nhận khoản chi,
        nên CHECK GOLD tự đọc sổ ngân hàng. Khớp khi: chi tiền · ma_chung_tu = bill_code bỏ gạch · đúng số
        tiền · trong vòng 1 giờ kể từ lúc lập phiếu. Lệch bất kỳ điều kiện nào thì vẫn là "Chờ chuyển khoản".
        """
        from django.db import connection as conn
        from django.utils import timezone
        from apps.pos.models import MoneyFlow
        hom_nay = timezone.localdate()

        def phieu(ma, tien):
            return MoneyFlow.objects.create(direction='OUT', service='GOLD_BUY', source_system='KHBL',
                                            source_type='thau_nhom', source_id=ma, source_bill_code=ma,
                                            business_date=hom_nay, expected_amount=tien, bank_amount=tien)

        def bank(pk, ma, tien, luc, huong='out'):
            with conn.cursor() as c:
                c.execute('INSERT INTO bank_notifications (id, transaction_time, direction, ma_chung_tu, '
                          'trans_amount) VALUES (%s,%s,%s,%s,%s)',
                          [pk, luc.strftime('%Y-%m-%d %H:%M:%S'), huong, ma, tien])

        gio = timezone.localtime(timezone.now())
        dung = phieu('26-09-26-000207', 66750000)          # khớp đủ 4 điều kiện
        lech_tien = phieu('26-09-26-000208', 10000000)     # ngân hàng chi thiếu
        qua_gio = phieu('26-09-26-000209', 20000000)       # chi sau 1 giờ
        khac_ngay = phieu('26-09-26-000210', 30000000)     # mã của NGÀY KHÁC, chỉ trùng đuôi 0210
        bank(1, '260926000207', 66750000, gio + dt.timedelta(minutes=5))
        bank(2, '260926000208', 9000000, gio + dt.timedelta(minutes=5))
        bank(3, '260926000209', 20000000, gio + dt.timedelta(minutes=75))
        bank(4, '260924000210', 30000000, gio + dt.timedelta(minutes=5))

        ds = {g['ma']: g for g in G.giao_dich(hom_nay)}
        self.assertEqual(ds['26-09-26-000207']['nhan_ck'], 'Đã chuyển khoản')
        self.assertEqual(ds['26-09-26-000207']['song'], G.SONG_XONG)     # hết việc → 30 giây rồi ẩn
        for ma in ('26-09-26-000208', '26-09-26-000209', '26-09-26-000210'):
            self.assertEqual(ds[ma]['nhan_ck'], 'Chờ chuyển khoản', ma)
            self.assertEqual(ds[ma]['song'], G.SONG_CHO, ma)

        # tiền VÀO cùng mã KHÔNG được tính là đã chi
        with conn.cursor() as c:
            c.execute('UPDATE bank_notifications SET direction=%s WHERE id=1', ['in'])
        ds = {g['ma']: g for g in G.giao_dich(hom_nay)}
        self.assertEqual(ds['26-09-26-000207']['nhan_ck'], 'Chờ chuyển khoản')

    def test_ma_tu_bill_code(self):
        """Mã chứng từ suy từ bill_code: bỏ gạch, nhóm nhiều phiếu lấy phiếu đầu, mã lạ thì bỏ qua."""
        self.assertEqual(G._ma_tu_bill('26-09-26-000207'), '260926000207')
        self.assertEqual(G._ma_tu_bill('26-09-26-000171 (+1)'), '260926000171')
        self.assertEqual(G._ma_tu_bill('TBG260900001270'), '')
        self.assertEqual(G._ma_tu_bill(''), '')

    def test_thong_ke_vang_ban_va_vang_thau_theo_loai(self):
        """GĐ chốt 27/09/2026: hai popup thống kê VÀNG theo loại. Trọng lượng máy KK lưu ×100 nên phải đổi
        ra CHI; "Vàng thâu" gom HAI nguồn: phiếu thâu độc lập và vàng cũ khách đưa trong hóa đơn bán."""
        ban = [{'GoldCode': 'N9999', 'GoldDesc': '99.99', 'SoMon': 3, 'TL': 12345, 'Tien': Decimal(900)},
               {'GoldCode': '18K', 'GoldDesc': '18K', 'SoMon': 2, 'TL': 5000, 'Tien': Decimal(100)}]
        thau = [{'GoldCode': 'D9999', 'GoldDesc': 'Dẻ 99.99', 'SoMon': 4, 'TL': 2000, 'TLVang': 2000, 'TLHot': 0,
                 'Tien': Decimal(500)}]
        doi = [{'GoldCode': 'D18K', 'GoldDesc': 'DẺ 18K', 'Kieu': 'thau', 'SoMon': 1, 'TL': 1000, 'TLVang': 700,
                'TLHot': 300, 'Tien': Decimal(70)},
               {'GoldCode': 'D18K', 'GoldDesc': 'DẺ 18K', 'Kieu': 'ngang', 'SoMon': 2, 'TL': 500, 'TLVang': 500,
                'TLHot': 0, 'Tien': Decimal(43)},
               {'GoldCode': 'D24K', 'GoldDesc': 'DẺ 24K', 'Kieu': 'ngang', 'SoMon': 1, 'TL': 100, 'TLVang': 100,
                'TLHot': 0, 'Tien': Decimal(14)}]

        def may(**bang):
            def query(sql, params=()):
                if 'TRN_RT_BUYSELL_SELL' in sql:
                    return list(bang.get('ban', []))
                if 'TRN_RT_BUYGOLD t' in sql:
                    return list(bang.get('thau', []))
                if 'TRN_RT_BUYSELL_BUYGOLD' in sql:
                    return list(bang.get('doi', []))
                return []
            return SimpleNamespace(query=query)

        rows, tong = G.ds_vang_ban(may(ban=ban), NGAY)
        self.assertEqual([r['ma'] for r in rows], ['N9999', '18K'])
        self.assertEqual(rows[0]['chi'], 123.45)                       # 12345 ÷ 100 = 123,45 chỉ
        self.assertEqual((tong['loai'], tong['so_mon'], tong['chi'], tong['tien']),
                         (2, 5, 173.45, Decimal(1000)))

        rows, tong = G.ds_vang_thau(may(thau=thau, doi=doi), NGAY)
        self.assertEqual([r['ma'] for r in rows['thau']], ['D9999'])
        # ĐỔI tách 2 bảng theo cột Kieu (SQL so giá dòng với giá BÁN RA hiệu lực — GĐ chốt 27/09/2026)
        self.assertEqual([r['ma'] for r in rows['doi_thau']], ['D18K'])
        self.assertEqual([r['ma'] for r in rows['doi_ngang']], ['D18K', 'D24K'])
        self.assertEqual((tong['thau']['tien'], tong['doi_thau']['tien'], tong['doi_ngang']['tien']),
                         (Decimal(500), Decimal(70), Decimal(57)))
        self.assertEqual((tong['so_mon'], tong['chi'], tong['tien']), (8, 36.0, Decimal(627)))
        # vàng đổi 10 chỉ = 7 chỉ vàng + 3 chỉ hột; ĐƠN GIÁ chia cho TL VÀNG: 70 ÷ 7 = 10 đồng/chỉ
        d = rows['doi_thau'][0]
        self.assertEqual((d['chi_vang'], d['chi_hot'], d['don_gia']), (7.0, 3.0, Decimal(10)))
        self.assertEqual(tong['thau']['don_gia'], Decimal(25))
        # header theo nhóm tuổi: 610 = 18K + D18K, 980 = 24K + D24K, 9999 = N9999 + D9999 (tổng TL / TL vàng)
        self.assertEqual(tong['nhom'], [{'ten': '610', 'chi': 15.0, 'chi_vang': 12.0},
                                        {'ten': '980', 'chi': 1.0, 'chi_vang': 1.0},
                                        {'ten': '9999', 'chi': 20.0, 'chi_vang': 20.0}])

        with patch('apps.pos.services.client', return_value=may(ban=ban, thau=thau, doi=doi)):
            for kieu, tieu_de in (('vang_ban', 'VÀNG BÁN THEO LOẠI'), ('vang_thau', 'VÀNG THÂU')):
                r = self.client.get(f'/banle/bao-cao/check-gold/ds/{kieu}/', HTTP_HOST='127.0.0.1')
                self.assertEqual(r.status_code, 200, kieu)
                self.assertIn(tieu_de, r.content.decode(), kieu)
                self.assertEqual('TL HỘT' in r.content.decode(), kieu == 'vang_thau', kieu)   # chỉ bảng thâu/đổi
        h = r.content.decode()
        self.assertIn('PHIẾU THÂU · khách bán vàng', h)                # popup vàng thâu có đủ hai khối
        self.assertIn('ĐỔI - THÂU VÀO', h)
        self.assertIn('ĐỔI - NGANG VÀNG', h)
        # header 3 dòng: Tổng thâu · Tổng đổi · TỔNG; mỗi nhóm "TL vàng - tổng TL"
        self.assertIn('Tổng thâu:', h)
        self.assertIn('Tổng đổi:', h)
        self.assertIn('<b>610</b> 12,00 - 15,00', h)     # đổi 610: vàng 7 + 5 = 12 · tổng 10 + 5 = 15
        self.assertIn('<b>9999</b> 20,00 - 20,00', h)    # thâu 9999

    def test_so_moc_ben_qua_reset_va_nhap_nhay_dung_2_phut(self):
        """GĐ chốt 27/09/2026: mốc "đã xong" và mốc nhấp nháy nằm trong bảng gold_check_moc (bền qua RESET),
        không còn ở bộ nhớ tiến trình / money_flow.synced_at."""
        from django.core.cache import cache
        from django.utils import timezone
        from apps.pos.models import GoldCheckMoc, MoneyFlow
        hom_nay = timezone.localdate()
        thoi = MoneyFlow.objects.create(direction='IN', service='RETAIL', source_system='KHBL',
                                        source_type='gold_bill_retail', source_id='N1', source_bill_code='N1',
                                        business_date=hom_nay, expected_amount=1000, cash_amount=1000,
                                        cus_cash=1200, cus_change=200)
        g = {x['ma']: x for x in G.giao_dich(hom_nay)}['N1']
        self.assertGreater(g['noi_bat'], G.NOI_BAT - 5)                 # vừa có tiền thối → nhấp nháy ~2 phút
        moc = GoldCheckMoc.objects.get(flow_id=thoi.pk)
        self.assertIsNotNone(moc.noi_bat_luc)
        # mốc bắt đầu nhấp nháy đã qua 3 phút → thôi nháy, DÙ synced_at vẫn "vừa xong" (job chiếu lại liên tục)
        GoldCheckMoc.objects.filter(flow_id=thoi.pk).update(noi_bat_luc=timezone.now() - dt.timedelta(minutes=3))
        MoneyFlow.objects.filter(pk=thoi.pk).update(synced_at=timezone.now())
        g = {x['ma']: x for x in G.giao_dich(hom_nay)}['N1']
        self.assertEqual(g['noi_bat'], 0)
        # "RESET web" = xoá sạch bộ nhớ tiến trình: phiếu đã xong vẫn giữ đúng mốc cũ, không hiện lại
        MoneyFlow.objects.filter(pk=thoi.pk).update(cus_change=0, cus_cash=1000)
        self.assertIn('N1', {x['ma'] for x in G.giao_dich(hom_nay)})    # vừa xong → còn 30 giây
        GoldCheckMoc.objects.filter(flow_id=thoi.pk).update(xong_luc=timezone.now() - dt.timedelta(minutes=1))
        cache.clear()
        self.assertNotIn('N1', {x['ma'] for x in G.giao_dich(hom_nay)})

    def test_theo_doi_chi_gui_the_vua_doi(self):
        """Nhịp 2 giây chỉ gửi TRỌN nội dung thẻ mà trang chưa có hoặc có dấu vân tay khác (27/09/2026)."""
        from django.utils import timezone
        from apps.pos.models import MoneyFlow
        hom_nay = timezone.localdate()
        for ma in ('D1', 'D2'):
            MoneyFlow.objects.create(direction='IN', service='RETAIL', source_system='KHBL',
                                     source_type='gold_bill_retail', source_id=ma, source_bill_code=ma,
                                     business_date=hom_nay, expected_amount=1000, cash_amount=1000)
        url = '/banle/bao-cao/check-gold/theo-doi/?d=' + hom_nay.isoformat()
        r0 = self.client.get(url, HTTP_HOST='127.0.0.1')
        self.assertEqual(r0.status_code, 200, r0.content.decode()[:400])
        j = r0.json()
        self.assertEqual(len(j['ds']), 2)
        self.assertEqual(len(j['moi']), 2)                              # trang trống → nhận trọn cả hai
        biet = ','.join(f'{i}:{h}' for i, h in j['ds'])
        j2 = self.client.get(url + '&biet=' + biet, HTTP_HOST='127.0.0.1').json()
        self.assertEqual(j2['moi'], [])                                  # không gì đổi → không gửi lại thẻ nào
        MoneyFlow.objects.filter(source_id='D1').update(cus_cash=1000)   # quầy xác nhận tiền mặt phiếu D1
        j3 = self.client.get(url + '&biet=' + biet, HTTP_HOST='127.0.0.1').json()
        self.assertEqual([g['ma'] for g in j3['moi']], ['D1'])           # chỉ đúng thẻ vừa đổi

    def test_popup_ve_toi_da_200_dong_nhung_tong_tinh_du(self):
        """Popup Phiếu bán vẽ tối đa 200 dòng mới nhất, tổng tiền vẫn tính trên TẤT CẢ (27/09/2026)."""
        hd = [{'TrnID': f'T{i}', 'BillCode': f'26-09-27-{i:06d}', 'TrnTime': '10:00', 'CustName': '',
               'EmpName': '', 'PayAmount': Decimal(1), 'CashPay': Decimal(1), 'CardPay': Decimal(0),
               'Status': 'C', 'IsDel': '0'} for i in range(250)]
        may = SimpleNamespace(query=lambda sql, params=(): hd if 'TRN_RT_BUYSELL' in sql else [])
        with patch('apps.pos.services.client', return_value=may):
            r = self.client.get('/banle/bao-cao/check-gold/ds/ban/', HTTP_HOST='127.0.0.1')
        h = r.content.decode()
        self.assertIn('Đang hiện 200/250 dòng mới nhất', h)
        self.assertIn('Tổng 250', h)                                     # tổng tiền = 250 × 1 ₫
        self.assertEqual(h.count('class="cg-xem-hd"'), 200)

    def test_the_cam_do_ghi_ro_ten_nghiep_vu(self):
        """GĐ chốt 27/09/2026: thẻ phiên cầm đồ ghi ĐÚNG nghiệp vụ (Gia hạn · Cầm thêm…), không gộp theo chiều tiền."""
        from django.utils import timezone
        from apps.pos.models import MoneyFlow
        hom_nay = timezone.localdate()
        for ma, huong, dv, nv in (('P1', 'OUT', 'PAWN', 'Cầm mới'), ('P2', 'OUT', 'PAWN', 'Cầm thêm'),
                                  ('P3', 'IN', 'REDEEM', 'Gia hạn'), ('P4', 'IN', 'REDEEM', 'Chuộc đồ')):
            MoneyFlow.objects.create(direction=huong, service=dv, source_system='KHCD', source_type='cd_loan_log',
                                     source_id=ma, source_bill_code=ma, business_date=hom_nay,
                                     expected_amount=1000, cash_amount=1000, source_status=nv)
        ds = {g['ma']: g['dich_vu'] for g in G.giao_dich(hom_nay)}
        self.assertEqual(ds, {'P1': 'Cầm đồ · Cầm mới', 'P2': 'Cầm đồ · Cầm thêm',
                              'P3': 'Cầm đồ · Gia hạn', 'P4': 'Cầm đồ · Chuộc đồ'})

    def test_desc3_thong_tin_them(self):
        """Desc3 (JSON app ghi) hiện thành các dòng thông tin thêm, chỉ mục có nội dung (29/09/2026)."""
        d = ('{"channel":"store","social":null,"customer_hold":false,"wedding":true,"wedding_quantity":"4",'
             '"wedding_bill_total":"39540000.000","type":["Đơn cưới"],"NgayCuoi":"2026-11-11","note":""}')
        self.assertEqual(G._desc3(d), [('Kênh', 'Tại tiệm'), ('Loại đơn', 'Đơn cưới'), ('Ngày cưới', '11/11/2026'),
                                       ('Số món cưới', '4'), ('Tổng đơn cưới', '39.540.000 ₫')])
        self.assertEqual(G._desc3('{"channel":"online","social":"Zalo","customer_hold":true,"note":"giao chiều"}'),
                         [('Kênh', 'Online · Zalo'), ('Khách gửi hàng', 'Đang gửi tại tiệm'), ('Ghi chú', 'giao chiều')])
        self.assertEqual(G._desc3('khách quen'), [('Ghi chú', 'khách quen')])
        self.assertEqual(G._desc3(None), [])

    def test_phieu_ban_quet_hien_3_cot(self):
        """GĐ chốt 29/09/2026: phiếu bán vừa quét chia 3 cột — Hóa đơn · Nhân viên + tiền · Sản phẩm."""
        hd = {'TrnID': 'T1', 'BillCode': '26-09-29-000100', 'TrnDate': dt.date(2026, 9, 29), 'TrnTime': '10:05:00',
              'CustName': 'Chị Hoa', 'Phone': '0909', 'CMND': '', 'EmpName': 'Lý Thới', 'nv_ho_tro': 'Bích Tuyền',
              'SellTotalAmount': Decimal(10000), 'BuyTotalAmount': Decimal(4000), 'Discount': Decimal(0),
              'TaskPriceAdd': Decimal(0), 'PayAmount': Decimal(6000), 'pay_abs': Decimal(6000),
              'CashPay': Decimal(1000), 'CardPay': Decimal(5000), 'Desc4': '', 'TienKhachTraThuc': Decimal(2000),
              'TienTraLai': Decimal(1000), 'da_huy': False, 'trang_thai': 'Hoàn thành', 'tl_ban': 1.0,
              'ban': [{'ProductDesc': 'Nhẫn 1 chỉ', 'ProductCode': '9N6001', 'GoldCode': 'N9999', 'chi': 1.0,
                       'TaskPrice': Decimal(0), 'SellAmount': Decimal(10000)}],
              'thu': [1, 2],
              'doi_ngang': [{'GoldDesc': 'Dẻ 99.99', 'GoldCode': 'D9999', 'chi': 0.2, 'chi_vang': 0.2, 'chi_hot': 0,
                             'BuyRate': Decimal(14000), 'BuyAmount': Decimal(2800)}],
              'doi_thau': [{'GoldDesc': 'DẺ 18K', 'GoldCode': 'D18K', 'chi': 0.2, 'chi_vang': 0.15, 'chi_hot': 0.05,
                            'BuyRate': Decimal(8100), 'BuyAmount': Decimal(1200)}]}
        with patch('apps.pos.check_gold.hoa_don', return_value=hd), patch('apps.pos.services.client', return_value=kk()):
            h = self.client.get('/banle/bao-cao/check-gold/hd/26-09-29-000100/?d=2026-09-29', HTTP_HOST='127.0.0.1',
                                HTTP_HX_REQUEST='true').content.decode()
        hd['phuong_thuc'] = 'Tiền mặt + CK'
        with patch('apps.pos.check_gold.hoa_don', return_value=hd), patch('apps.pos.services.client', return_value=kk()):
            h = self.client.get('/banle/bao-cao/check-gold/hd/26-09-29-000100/?d=2026-09-29', HTTP_HOST='127.0.0.1',
                                HTTP_HX_REQUEST='true').content.decode()
        for chu in ('PHIẾU BÁN', 'Hóa đơn', 'Khách hàng', 'Nhân viên', 'Thanh toán', 'Phương thức', 'Tiền mặt + CK',
                    'Tiền khách', 'Sản phẩm', 'Bích Tuyền', 'Nhận của khách',
                    'Trả lại khách', 'Đổi ngang vàng', 'Đổi thâu vào', 'Nhẫn 1 chỉ', 'Khách phải trả'):
            self.assertIn(chu, h, chu)

    def test_trang_khong_can_dang_nhap_va_khong_ghi_kk(self):
        with patch('apps.pos.services.client', return_value=kk()):
            r = self.client.get('/banle/bao-cao/check-gold/', HTTP_HOST='127.0.0.1')
        self.assertEqual(r.status_code, 200)                                  # không bị đẩy sang trang đăng nhập
        h = r.content.decode()
        self.assertIn('CHECK', h)
        self.assertIn('GIAO DỊCH', h)
        self.assertNotIn('cg-the-so', h)                                      # GĐ chốt: bỏ khối thống kê
        self.assertIn('id="cg-nv-dem"', h)                                    # dòng badge NV dưới tiêu đề MÓN HÀNG

    def test_popup_cam_do_gom_dung_chieu_tien_va_doi_soat(self):
        """Popup CẦM ĐỒ (26/09/2026): một dòng cd_loan_logs = một phiên; OUT là tiệm CHI ra, IN là THU vào;
        phiên hủy không được tính vào thống kê; CK chưa khớp thì đếm vào "chờ đối soát"."""
        def phien(**kw):
            mac_dinh = {'id': 1, 'happened_at': dt.datetime(2026, 9, 26, 9, 5), 'operation_id': 1,
                        'employee_name': 'Cô Vân', 'interest': 0, 'principal_change': 0, 'principal_after': 0,
                        'note': '', 'sku': 'KH226', 'phone': '0900', 'customer_snapshot': '{"name": "Chị Hoa"}',
                        'loan_state': 'OPEN', 'tong': 0, 'tien_mat': 0, 'ck': 0, 'chieu': 'OUT', 'doi_soat': None}
            return dict(mac_dinh, **kw)

        rows, tong = G._gom_cam_do([
            phien(id=1, tong=5000000, tien_mat=5000000, chieu='OUT'),                       # cầm mới, trả tiền mặt
            phien(id=2, operation_id=5, tong=2000000, ck=2000000, chieu='IN', interest=150000),   # chuộc, CK chưa khớp
            phien(id=3, operation_id=4, tong=300000, ck=300000, chieu='IN', doi_soat='MATCHED', interest=300000),
            phien(id=4, operation_id=0, tong=9000000, tien_mat=9000000, chieu='OUT'),        # HỦY phiên
        ])
        self.assertEqual([r['ma'] for r in rows], ['KH226'] * 4)
        self.assertEqual((rows[0]['nghiep_vu'], rows[0]['ra_tien'], rows[0]['doi_soat']),
                         ('Cầm mới', True, 'Tiền mặt'))
        self.assertEqual((rows[1]['nghiep_vu'], rows[1]['ra_tien'], rows[1]['doi_soat']),
                         ('Chuộc đồ', False, 'Chờ đối soát'))
        self.assertEqual(rows[2]['doi_soat'], 'Đã khớp')
        self.assertEqual(rows[0]['khach'], 'Chị Hoa')                       # đọc từ customer_snapshot JSON
        self.assertTrue(rows[3]['huy'])
        self.assertEqual(tong['so'], 3)                                     # phiên hủy không vào thống kê
        self.assertEqual(tong['chi'], Decimal(5000000))
        self.assertEqual(tong['thu'], Decimal(2300000))
        self.assertEqual((tong['tien_mat'], tong['ck']), (Decimal(5000000), Decimal(2300000)))
        self.assertEqual(tong['lai'], Decimal(450000))
        self.assertEqual(tong['cho_ck'], 1)                                 # chỉ phiên CK chưa khớp

    def test_popup_cam_do_mo_duoc_va_khong_no_500(self):
        """Không đọc được khj_cd (máy kiểm dùng SQLite) thì popup vẫn mở và báo lỗi tử tế, không 500."""
        r = self.client.get('/banle/bao-cao/check-gold/ds/cam/', HTTP_HOST='127.0.0.1')
        self.assertEqual(r.status_code, 200)
        self.assertIn('CẦM ĐỒ', r.content.decode())

    def test_sua_hoa_don_desc3_tien_khach_va_nv_ho_tro(self):
        """GĐ chốt 29/09/2026: form SỬA ghi thêm Desc3 + tiền khách (cổng pmv_invoice_update, đúng thuật toán
        mobile_invoice) và NV hỗ trợ (sổ KHBL); ô tiền dạng 39.540.000 đọc đúng; kiểm hết rồi mới ghi."""
        from apps.pos.models import GoldBill
        hd = {'TrnID': 'TRB9', 'BillCode': '26-09-29-000009', 'PayAmount': Decimal(1000000), 'CashPay': Decimal(1000000),
              'CardPay': Decimal(0), 'CustID': 'CU1', 'EmpID': 'E1', 'Desc4': '', 'Desc5': '',
              'emp_sup_id': '', 'co_so_khbl': True}
        goc = {'TrnID': 'TRB9', 'BillCode': '26-09-29-000009', 'CustID': 'CU1', 'EmpID': 'E1', 'Status': 'C',
               'CashPay': Decimal(1000000), 'CardPay': Decimal(0), 'PayAmount': Decimal(1000000), 'IsDel': '0',
               'TienKhachTraThuc': Decimal(0), 'TienTraLai': Decimal(0),
               'Desc3': '{"channel":"store","wedding":false,"type":[],"NgayCuoi":null,"note":"","customer_pickups":[1]}'}
        posted = {'cash_pay': '400.000', 'card_pay': '600.000', 'cust_id': 'CU1', 'emp_id': 'E1', 'desc4': '',
                  'desc5': '', 'emp_sup_id': 'E2', 'channel': 'store', 'is_wedding': '1', 'wedding': '2026-11-11',
                  'note': 'giao chiều', 'tender': '500.000'}
        with patch('apps.pos.check_gold.hoa_don', return_value=hd),                 patch('apps.pos.mobile_invoice.load_retail', return_value=goc),                 patch('apps.pos.mobile_invoice.retail_quantity', return_value=3),                 patch('apps.pmv.gateway.pmv_billsell_edit') as ghi_tien,                 patch('apps.pmv.gateway.pmv_invoice_update') as ghi_d3,                 patch.object(GoldBill.all_objects, 'filter') as loc:
            tin = G.sua_hoa_don(None, 'TRB9', hd['BillCode'], posted)
        self.assertIn('thông tin đơn', tin)
        self.assertIn('NV hỗ trợ', tin)
        self.assertEqual(ghi_tien.call_args[0][1], {'CashPay': Decimal(400000), 'CardPay': Decimal(600000)})
        gui = ghi_d3.call_args[0][4]                                      # đúng bộ khóa mobile_invoice
        self.assertEqual((gui['channel'], gui['is_wedding'], gui['wedding'], gui['tender']),
                         ('store', '1', '2026-11-11', '500000'))
        loc.return_value.update.assert_called_once_with(emp_sup_id='E2')

        # lệch tổng → báo lỗi, KHÔNG ghi gì
        with patch('apps.pos.check_gold.hoa_don', return_value=hd),                 patch('apps.pmv.gateway.pmv_billsell_edit') as ghi_tien,                 patch('apps.pmv.gateway.pmv_invoice_update') as ghi_d3:
            with self.assertRaises(G.LoiQuet):
                G.sua_hoa_don(None, 'TRB9', hd['BillCode'], dict(posted, card_pay='500.000'))
        ghi_tien.assert_not_called(); ghi_d3.assert_not_called()

    def test_form_sua_giu_tk_va_nv_dang_ghi(self):
        """TK nhận CK / NV bán / NV hỗ trợ đang ghi trên hóa đơn mà không còn trong danh sách đang bật vẫn phải có
        trong ô chọn — không thì bấm LƯU là âm thầm xóa hoặc đổi sang người khác (thực đo 29/09/2026)."""
        may = SimpleNamespace(query=lambda sql, params=(): (
            [{'NumberBank': '111', 'AccName': 'A', 'BankName': 'ACB'}] if 'I_BankCard' in sql
            else [{'EmpID': 'E1', 'EmpName': 'Liên'}]))
        hd = {'Desc4': '50361307', 'EmpID': 'E9', 'EmpName': 'Hạnh', 'emp_sup_id': 'E1', 'nv_ho_tro': 'Liên'}
        self.assertEqual([b['NumberBank'] for b in G._ds_tk(may, hd)], ['50361307', '111'])
        self.assertEqual([e['EmpID'] for e in G._ds_nv(may, hd)], ['E9', 'E1'])       # E1 có sẵn, không nhân đôi
        self.assertEqual(G._ds_nv(may, hd)[0]['EmpName'], 'Hạnh (đã nghỉ)')
        self.assertEqual([b['NumberBank'] for b in G._ds_tk(may, {'Desc4': '111'})], ['111'])

    def test_moi_view_check_gold_khong_can_dang_nhap(self):
        """Trang đứng riêng, KHÔNG đăng nhập: chèn hàm mới ngay trên một view từng làm TRÔI decorator
        (@login_not_required) sang hàm mới — 2 lần trong tháng 9/2026. Soi thẳng cờ trên từng view."""
        for ten in ('trang', 'quet_view', 'go_view', 'theo_doi', 'sua_form', 'sua_luu', 'tim_khach',
                    'hoa_don_view', 'manifest', 'popup'):
            self.assertFalse(getattr(getattr(G, ten), 'login_required', True), ten)
        for ten in ('_ds_tk', '_ds_nv', '_the_json', 'giao_dich'):
            self.assertFalse(hasattr(getattr(G, ten), 'login_required'), ten + ' không phải view')

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
