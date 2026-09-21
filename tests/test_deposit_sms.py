# -*- coding: utf-8 -*-
"""📱 GỬI SMS phiếu đặt cọc → DS CHỜ zalo_messages (GĐ chốt 19/09/2026 · lịch nhắc + Xem/XÓA 20/09/2026).

Chạy: manage.py test tests.test_deposit_sms --settings=config.settings.test_price_save
(SQLite riêng tên test_; 3 bảng Zalo managed=False nên bộ kiểm tự dựng bảng tạm. Máy KK được giả lập.)
"""
import datetime as dt
import json
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from django.db import connection
from django.test import RequestFactory, TransactionTestCase
from django.utils import timezone

from apps.oa.models import ZaloMessage, ZaloSendRule, ZaloTemplate
from apps.pos import deposit_sms as S

HOM_NAY = timezone.localdate()


def phieu(trn='TRC1', status='R', phone='0974472163', ten='Chị Hân', bill='', tien_do='ready', qua_han=0):
    return {'TrnID': trn, 'BillCode': bill, 'fulfilment': tien_do, 'TrnDate': dt.datetime(2026, 9, 5), 'TrnTime': '10:15:00',
            'CustID': 'CU1', 'CustName': ten, 'Phone': phone, 'Status': status, 'state_name': 'Hàng sẵn sàng', 'EmpID': 'E1',
            'EmpName': 'Thư', 'TienCoc': Decimal(500000), 'promise_date': HOM_NAY - dt.timedelta(days=qua_han)}


class DepositSmsTests(TransactionTestCase):
    def setUp(self):
        with connection.schema_editor() as ed:
            for m in (ZaloTemplate, ZaloSendRule, ZaloMessage):
                ed.create_model(m)
        now = timezone.now()
        self.mau = ZaloTemplate.objects.create(oa_account_id=1, template_id='635720', template_name='Thông báo hàng sẵn sàng',
                                               template_tag='TRANSACTION', status='DISABLE', quality='', price_sdt=Decimal('300'),
                                               active=False, last_synced_at=now, created_at=now, updated_at=now)
        self.rows = [phieu('TRC1', qua_han=-3),                                  # chưa tới hẹn → vẫn báo SẴN SÀNG
                     phieu('TRC2', ten='Nguyễn Văn Minh', bill='26-09-05-000012', qua_han=12),
                     phieu('TRC3', phone='12345'), phieu('TRC4', tien_do='delivered'),
                     phieu('TRW5', status='W', qua_han=25), phieu('TRC6', qua_han=35)]
        self.gio = timezone.now() + dt.timedelta(hours=1)
        self.user = SimpleNamespace(pk=7, username='gd')
        for p in (patch.object(S, 'phieu_coc', side_effect=lambda: list(self.rows)),
                  patch.object(S, '_gioi_tinh', return_value={}),
                  patch.object(S, 'duoc_ghi', return_value=(True, ''))):
            p.start()
            self.addCleanup(p.stop)

    def tearDown(self):
        with connection.schema_editor() as ed:
            for m in (ZaloMessage, ZaloSendRule, ZaloTemplate):
                ed.delete_model(m)

    def lui(self, trn, ngay):
        """Đẩy giờ tạo tin của phiếu về quá khứ (giả lập đã nhắc cách đây N ngày)."""
        ZaloMessage.objects.filter(trn_id=trn).update(created_at=timezone.now() - dt.timedelta(days=ngay))

    def ds(self, **kw):
        return {r['trn']: r for r in S.ung_vien({'tt': 'ready', 'tin': 'can', **kw})}

    def test_xung_ho_ten_ma(self):
        self.assertEqual(S.anh_chi_cua(None, 'Chị Hân'), 'Chị')
        self.assertEqual(S.anh_chi_cua(None, 'Nguyễn Văn Minh'), 'Anh')
        self.assertEqual(S.anh_chi_cua(None, 'Minh'), 'Anh/Chị')
        self.assertEqual(S.ten_khong_tien_to('Chị Hân'), 'Hân')
        self.assertEqual(S.che_ma('26-09-05-000012'), '26********00012')

    def test_muc_toi_han_theo_qua_hen(self):
        d = self.ds()
        self.assertEqual({k: v['muc'] for k, v in d.items()},
                         {'TRC1': 'san_sang', 'TRC2': 'nhac1', 'TRW5': 'nhac2', 'TRC6': 'nhac3', 'TRC3': 'san_sang'})
        self.assertFalse(d['TRC3']['chon_duoc'])                              # không SĐT: hiện nhưng không tick được
        self.assertNotIn('TRC4', d)                                          # "Hoàn thành - đặt" không vào

    def test_tao_dung_muc_va_hop_dong_dong(self):
        them, mo, bo = S.tao(['TRC1', 'TRC2', 'TRC3'], self.gio, self.user)
        self.assertEqual((them, mo, len(bo)), (2, 0, 1))
        m = ZaloMessage.objects.get(trn_id='TRC2')
        self.assertEqual((m.source_type, m.status, m.source_status, m.external_template_id, m.template_id, m.send_rule_id,
                          m.recipient_ciphertext, m.recipient_masked, m.bill_code, m.created_by),
                         ('khbl_deposit', 'queued', 'nhac1', '635720', self.mau.pk, None, 'khbl_no_cipher', '097***163',
                          '26-09-05-000012', 7))
        self.assertEqual(json.loads(m.template_data_json),
                         {'bill_code': '26********00012', 'customer_name': 'Nguyễn Văn Minh', 'anh_chi': 'Anh'})
        self.assertEqual(m.dedupe_key, S.dedupe('TRC2', 'nhac1'))
        self.assertEqual(ZaloMessage.objects.get(trn_id='TRC1').dedupe_key, S.dedupe('TRC1'))

    def test_an_10_ngay_roi_nhac_muc_ke(self):
        S.tao(['TRC2'], self.gio, self.user)                                  # nhắc lần 1 hôm nay
        self.assertNotIn('TRC2', self.ds())                                   # ẩn 10 ngày
        tat_ca = {r['trn']: r for r in S.ung_vien({'tt': 'ready', 'tin': ''})}
        self.assertIn('ẩn tới', tat_ca['TRC2']['ly_do'])
        self.lui('TRC2', 11)                                                  # 11 ngày sau, vẫn quá hẹn 12 → đã xử lý nhắc 1
        self.assertNotIn('TRC2', self.ds())
        self.assertIn('Đã xử lý Nhắc lần 1', {r['trn']: r for r in S.ung_vien({'tt': 'ready', 'tin': ''})}['TRC2']['ly_do'])
        self.rows[1] = phieu('TRC2', ten='Nguyễn Văn Minh', bill='26-09-05-000012', qua_han=22)
        self.assertEqual(self.ds()['TRC2']['muc'], 'nhac2')                   # quá hẹn 22 → tới nhắc lần 2
        S.tao(['TRC2'], self.gio, self.user)
        self.assertEqual(sorted(ZaloMessage.objects.filter(trn_id='TRC2').values_list('source_status', flat=True)),
                         ['nhac1', 'nhac2'])

    def test_xoa_tro_lai_ds_chon_da_gui_khong_xoa(self):
        S.tao(['TRC1'], self.gio, self.user)
        m = ZaloMessage.objects.get(trn_id='TRC1')
        self.assertNotIn('TRC1', self.ds())
        S.xoa(m.pk, self.user)
        self.assertFalse(ZaloMessage.objects.filter(pk=m.pk).exists())
        self.assertEqual(self.ds()['TRC1']['muc'], 'san_sang')                # xóa → trở lại DS CHỌN
        S.tao(['TRC1'], self.gio, self.user)
        m = ZaloMessage.objects.get(trn_id='TRC1')
        ZaloMessage.objects.filter(pk=m.pk).update(status='sent')
        with self.assertRaises(ValueError):
            S.xoa(m.pk, self.user)                                          # đã gửi: giữ dấu vết
        khac = ZaloMessage.objects.get(pk=m.pk)
        ZaloMessage.objects.filter(pk=khac.pk).update(source_type='khcd_pawn', status='queued')
        with self.assertRaises(ValueError):
            S.xoa(khac.pk, self.user)                                       # dòng nguồn khác: không đụng
        self.assertTrue(ZaloMessage.objects.filter(pk=khac.pk).exists())

    def test_huy_mo_lai_cung_dong(self):
        S.tao(['TRC6'], self.gio, self.user)
        m = ZaloMessage.objects.get(trn_id='TRC6')
        ZaloMessage.objects.filter(pk=m.pk).update(status='cancelled')        # tin hủy không tính là đã nhắc
        them, mo, _ = S.tao(['TRC6'], self.gio, self.user)
        m.refresh_from_db()
        self.assertEqual((them, mo, m.status, ZaloMessage.objects.count()), (0, 1, 'queued', 1))

    def test_tin_cu_truoc_20_09_la_muc_san_sang(self):
        S.tao(['TRC1'], self.gio, self.user)
        ZaloMessage.objects.filter(trn_id='TRC1').update(source_status='R')   # dòng cũ mang Status phiếu
        self.lui('TRC1', 11)
        self.assertNotIn('TRC1', self.ds())                                   # đã xử lý mức sẵn sàng, chưa quá hẹn

    def test_xem_the_tin_va_mau(self):
        S.tao(['TRC2'], self.gio, self.user)
        m = ZaloMessage.objects.get(trn_id='TRC2')
        from django.contrib.auth import get_user_model
        req = RequestFactory().get('/')
        req.user = get_user_model().objects.create_superuser('sms-xem', password='x')
        html = S.xem_view(req, m.pk).content.decode()
        self.assertIn('26********00012', html)
        self.assertIn('<b>Anh</b> <b>Nguyễn Văn Minh</b>', html)
        self.assertIn('Nhắc lần 1', html)
        self.assertIn('&lt;customer_name&gt;', S.xem_view(req, 0).content.decode())

    def test_cong_tac_va_gio(self):
        with patch.object(S, 'duoc_ghi', return_value=(False, 'Đang DỪNG KHẨN')):
            with self.assertRaises(ValueError):
                S.tao(['TRC2'], self.gio, self.user)
        self.assertEqual(ZaloMessage.objects.count(), 0)
        with self.assertRaises(ValueError):
            S.doc_gio('2030-01-01T21:30')
        with self.assertRaises(ValueError):
            S.doc_gio('2020-01-01T10:00')
        self.assertTrue(S.GIO_TU <= S.gio_mac_dinh().hour < S.GIO_DEN)
