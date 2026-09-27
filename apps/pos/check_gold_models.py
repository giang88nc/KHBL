"""CHECK GOLD V4 (23/09/2026) — nhật ký KIỂM VÀNG mỗi ngày, gom về MySQL 8 (khj_bl).

Thay bảng pmv_report.gold_check_n9999 của bản V3 bên BANLE_V5. Khác 3 điểm (sửa điểm yếu của V3):
  · có cột NGÀY KIỂM riêng + unique (mã SP, ngày) → 2 máy quét cùng lúc không tạo 2 dòng;
  · gỡ mã khỏi danh sách = ĐÁNH DẤU (go_luc/go_boi) chứ không xóa cứng — còn truy vết ai gỡ;
  · giữ nguyên tên/loại vàng/trọng lượng chụp từ KK lúc quét để xem lại không phải hỏi KK.
"""
from django.db import models


class GoldCheckEntry(models.Model):
    ngay = models.DateField('Ngày kiểm', db_index=True)
    product_code = models.CharField('Mã sản phẩm', max_length=30, db_index=True)
    gold_code = models.CharField('Loại vàng', max_length=20, blank=True, default='')
    product_desc = models.CharField('Tên sản phẩm', max_length=255, blank=True, default='')
    total_weight = models.DecimalField('Trọng lượng (KK, chi × 100)', max_digits=18, decimal_places=3, default=0)
    nhan_vien = models.CharField('NV kiểm', max_length=150, blank=True, default='')
    quet_luc = models.DateTimeField('Giờ quét', auto_now_add=True)
    go_luc = models.DateTimeField('Giờ gỡ khỏi danh sách', null=True, blank=True)
    go_boi = models.CharField(max_length=150, blank=True, default='')

    class Meta:
        db_table = 'gold_check_entry'
        ordering = ['-id']
        constraints = [models.UniqueConstraint(fields=['ngay', 'product_code'], name='uq_gold_check_ngay_ma')]
        verbose_name = 'Kiểm vàng'
        verbose_name_plural = 'Kiểm vàng'

    def __str__(self):
        return f'{self.product_code} · {self.ngay:%d/%m/%Y}'


class GoldCheckMoc(models.Model):
    """MỐC THỜI GIAN của thẻ GIAO DỊCH trên CHECK GOLD (GĐ chốt 27/09/2026). CHỈ CHECK GOLD đọc/ghi.

    Thay cho bộ nhớ đệm tiến trình web (mất sạch mỗi lần RESET) và cho money_flow.synced_at (job chiếu lại MỌI
    dòng vài giây một lần nên cột đó luôn là "vừa xong"). Bảng riêng, khóa = id dòng money_flow nhưng CỐ Ý không
    đặt khóa ngoại: không khóa bảng money_flow đang ghi liên tục, và không đụng 4 chỗ ghi trạng thái tiền.

        · xong_luc    — lúc phiếu TRỞ THÀNH xong (hủy / đối soát khớp) → thẻ còn 30 giây rồi rời bảng;
        · noi_bat_luc — lúc phiếu BẮT ĐẦU có tiền phải thối / phải đưa khách → nhấp nháy đúng 2 phút.

    Có dòng = CHECK GOLD đã từng nhìn thấy phiếu này (dùng để phân biệt phiếu vừa xong với phiếu xong từ lâu).
    """
    flow_id = models.PositiveBigIntegerField('Dòng money_flow', primary_key=True)
    xong_luc = models.DateTimeField('Lúc xong', null=True, blank=True)
    noi_bat_luc = models.DateTimeField('Lúc bắt đầu nổi bật', null=True, blank=True)
    tao_luc = models.DateTimeField('Lần đầu thấy', auto_now_add=True)

    class Meta:
        db_table = 'gold_check_moc'
        verbose_name = 'Mốc thẻ CHECK GOLD'
        verbose_name_plural = 'Mốc thẻ CHECK GOLD'
