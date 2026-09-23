"""CHECK BILL V2 (GĐ chốt 21/09/2026) — sổ kiểm hóa đơn giấy cuối ngày.

Thay bảng `gold_check_bill` của BANLE_V5 (pmv_report @3306): KHBL giữ sổ riêng ở khj_bl.
Mỗi hóa đơn KK chỉ được kiểm MỘT lần (unique trn_id). Số tiền là BẢN CHỤP lúc quét để đối chiếu,
nguồn thật vẫn là TRN_RT_BUYSELL trên máy KK.
"""
from django.db import models


class CheckBill(models.Model):
    TM, CK, TMCK, DB = 'TM', 'CK', 'TMCK', 'DB'
    KIEU = [(TM, 'Tiền mặt'), (CK, 'Chuyển khoản'), (TMCK, 'Tiền mặt + CK'), (DB, 'Đổi bù')]

    trn_id = models.CharField(max_length=20, unique=True)
    bill_code = models.CharField(max_length=30, db_index=True)
    ngay = models.DateField('Ngày làm việc (CreatedDate KK)', db_index=True)
    lap_luc = models.DateTimeField('Giờ lập HĐ trên KK', null=True, blank=True)
    kieu = models.CharField(max_length=4, choices=KIEU)
    tong = models.DecimalField(max_digits=18, decimal_places=0)
    tien_mat = models.DecimalField(max_digits=18, decimal_places=0)
    chuyen_khoan = models.DecimalField(max_digits=18, decimal_places=0)
    ck_nhan = models.DecimalField('CK đã nhận (bank_notifications)', max_digits=18, decimal_places=0, default=0)
    ck_so_lan = models.PositiveSmallIntegerField(default=0)
    nhan_vien = models.CharField(max_length=200, blank=True, default='')
    khach = models.CharField(max_length=200, blank=True, default='')
    nguoi_kiem = models.CharField(max_length=150)
    kiem_luc = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'check_bill'
        ordering = ['-kiem_luc']
        verbose_name = 'Check bill'
