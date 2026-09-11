import uuid

from django.conf import settings
from django.db import models


class DepositSubmission(models.Model):
    """Giữ token một lần trước khi gửi lệnh PMV; không tự gửi lại khi mất phản hồi."""
    token = models.CharField(max_length=64, unique=True)
    completed = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)


class BankReconcileState(models.Model):
    """Dấu vết đối soát và giữ chỗ TrnID qua các lần UPSERT ngân hàng."""
    notification_id = models.BigIntegerField(primary_key=True)
    trn_id = models.CharField(max_length=15, unique=True, null=True, blank=True)
    fingerprint = models.CharField(max_length=64, default="")
    status = models.CharField(max_length=24, default="pending")
    attempts = models.PositiveIntegerField(default=0)
    next_attempt = models.DateTimeField(null=True, blank=True)
    message = models.CharField(max_length=255, default="")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "bank_reconcile_state"


class ThauPaymentLink(models.Model):
    """Bank evidence, retained after revocation; unique active bank transaction."""
    order_key = models.CharField(max_length=64, db_index=True)
    trn_ids = models.JSONField(default=list)
    notification_id = models.BigIntegerField(db_index=True)
    active_notification_id = models.BigIntegerField(unique=True, null=True)
    amount = models.DecimalField(max_digits=18, decimal_places=2)
    snapshot = models.JSONField(default=dict)
    bank_snapshot = models.JSONField(default=dict)
    mode = models.CharField(max_length=10)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name='+')
    username = models.CharField(max_length=150, default='')
    reason = models.CharField(max_length=500, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    revoked_at = models.DateTimeField(null=True)
    revoked_by = models.CharField(max_length=150, default='')
    revoke_reason = models.CharField(max_length=500, default='')

    class Meta:
        db_table = 'thau_payment_link'


class PriceDisplay(models.Model):
    gold_type = models.CharField(max_length=50, unique=True)
    pinned = models.BooleanField(default=False)
    position = models.PositiveIntegerField(default=1)
    unit = models.CharField(max_length=10, blank=True, default="")

    class Meta:
        db_table = "gold_price_display"
        ordering = ["position", "gold_type"]


class UnlockPasscode(models.Model):
    """Kho passcode cũ, chỉ giữ để migration sang ``auth_user.passcode``.

    Từ PMV 0008 passcode mặc định nằm ngay trên tài khoản web (auth_user), nên
    runtime không còn đọc hoặc ghi bảng này. Bảng được giữ nguyên để không mất dữ
    liệu cũ và để các bản sao DB có thể nâng cấp an toàn.
    """

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="unlock_passcode")
    hash = models.CharField("Passcode (băm)", max_length=128)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name="+")

    class Meta:
        db_table = "unlock_passcodes"

    def kiem(self, ma):
        from django.contrib.auth.hashers import check_password
        return bool(ma) and check_password(ma, self.hash)

    def dat(self, ma, boi=None):
        from django.contrib.auth.hashers import make_password
        self.hash = make_password(ma)
        self.updated_by = boi
        self.save()


class BillAudit(models.Model):
    """NHẬT KÝ hóa đơn — APPEND-ONLY (GĐ chốt 07/09/2026): mọi hành động lên đơn ĐÃ CHỐT (sửa đơn /
    hủy thanh toán / hủy hóa đơn) + chốt + in đều ghi 1 dòng kèm ẢNH CHỤP đơn TRƯỚC khi đổi.
    Model TỪ CHỐI update/delete (save chỉ khi chưa có pk; delete raise) — sửa chỉ được bằng tay trong DB,
    không có đường nào từ web."""

    CHOT, SUA, HUY_TT, HUY_HD, IN = "CHOT", "SUA", "HUY_TT", "HUY_HD", "IN"
    TEN = {CHOT: "Thanh toán & chốt", SUA: "Sửa đơn (về nháp)", HUY_TT: "Hủy thanh toán",
           HUY_HD: "Hủy hóa đơn", IN: "In giấy đảm bảo"}

    trn_id = models.CharField(max_length=20, db_index=True)
    bill_code = models.CharField(max_length=30, blank=True, default="")
    action = models.CharField(max_length=10)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    username = models.CharField(max_length=150, blank=True, default="")   # giữ tên dù user bị xóa
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    before = models.JSONField(default=dict, blank=True)     # ảnh chụp đơn TRƯỚC hành động
    note = models.CharField(max_length=300, blank=True, default="")
    version = models.PositiveIntegerField(default=0)        # lần in thứ mấy (action=IN)

    class Meta:
        db_table = "bill_audit"
        ordering = ["-created_at", "-id"]

    def save(self, *args, **kwargs):
        if self.pk:
            raise PermissionError("bill_audit là nhật ký chỉ ghi thêm — không sửa được")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise PermissionError("bill_audit là nhật ký chỉ ghi thêm — không xóa được")

    @property
    def ten(self):
        return self.TEN.get(self.action, self.action)


class GoldBill(models.Model):
    """`gold_bill` — 1 dòng = 1 ĐƠN BÁN (khóa TrnID), GĐ chốt 08/09/2026. KHÔNG phải bản sao KK (HIST đã có):
    vai trò = (1) dữ liệu KK KHÔNG có chỗ: NV HỖ TRỢ, tách tiền mặt/CK/thẻ, TK ngân hàng, số lần in, user web;
    (2) tra cứu/thống kê nhanh không đụng KK; (3) `items`/`doi` = ẢNH CHỤP dòng hàng để hiển thị, không phải sự thật.
    KK vẫn là sự thật: ghi SAU khi KK OK (write-through), MySQL lỗi → log, không chặn bán; làm tươi khi xem +
    job đối soát 60'. Chỉ áp dụng từ ngày bật (không backfill). Đơn KK xóa → is_del=1 (không xóa dòng)."""

    trn_id = models.CharField(max_length=20, unique=True)
    bill_code = models.CharField(max_length=30, blank=True, default="", db_index=True)
    trn_date = models.DateField(null=True, blank=True, db_index=True)
    trn_time = models.CharField(max_length=8, blank=True, default="")
    nguon = models.CharField(max_length=8, default="KHBL")           # KHBL (web) / PMV (đối soát thấy)
    emp_id = models.CharField(max_length=20, blank=True, default="", db_index=True)
    emp_sup_id = models.CharField("NV hỗ trợ", max_length=20, blank=True, default="", db_index=True)
    cust_id = models.CharField(max_length=20, blank=True, default="")
    cust_name = models.CharField(max_length=200, blank=True, default="")
    cust_phone = models.CharField(max_length=30, blank=True, default="")
    user_web = models.CharField(max_length=150, blank=True, default="")
    tien_vang_moi = models.DecimalField(max_digits=18, decimal_places=0, default=0)
    tien_vang_cu = models.DecimalField(max_digits=18, decimal_places=0, default=0)
    tien_vang_them = models.DecimalField(max_digits=18, decimal_places=0, default=0)
    tien_cong_them = models.DecimalField(max_digits=18, decimal_places=0, default=0)
    tien_bot = models.DecimalField(max_digits=18, decimal_places=0, default=0)
    tien_coc = models.DecimalField(max_digits=18, decimal_places=0, default=0)
    tong = models.DecimalField("Khách trả (PayAmount)", max_digits=18, decimal_places=0, default=0)
    pay_method = models.CharField(max_length=8, default="cash")     # cash / bank / card
    tien_mat = models.DecimalField(max_digits=18, decimal_places=0, default=0)
    tien_ck = models.DecimalField(max_digits=18, decimal_places=0, default=0)
    tien_the = models.DecimalField(max_digits=18, decimal_places=0, default=0)
    bank_id = models.CharField(max_length=20, blank=True, default="")
    items = models.JSONField(default=list, blank=True)               # ảnh chụp món bán
    doi = models.JSONField(default=list, blank=True)                 # ảnh chụp dẻ (ngang/thâu)
    ma_sp = models.TextField(blank=True, default="")                 # "MA1 MA2 …" để LIKE
    status = models.CharField(max_length=2, default="W", db_index=True)   # W / C
    is_del = models.BooleanField(default=False)
    kk_upd = models.CharField("TrnDateTime_Upd lúc đồng bộ", max_length=40, blank=True, default="")
    so_lan_in = models.PositiveIntegerField(default=0)
    synced_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    # ẢNH CHUYỂN KHOẢN phiếu THÂU (08/09/2026 tối, GĐ chốt lưu ở gold_bill): JPEG đã nén, gắn vào dòng TrnID ĐẦU của nhóm
    anh_cccd1 = models.BinaryField("CCCD mặt trước", null=True, blank=True, editable=False)
    anh_cccd2 = models.BinaryField("CCCD mặt sau", null=True, blank=True, editable=False)
    anh_hinh1 = models.BinaryField("Hình 1", null=True, blank=True, editable=False)
    anh_hinh2 = models.BinaryField("Hình 2", null=True, blank=True, editable=False)
    anh_qr = models.BinaryField("QR chuyển khoản", null=True, blank=True, editable=False)

    class Meta:
        db_table = "gold_bill"
        ordering = ["-trn_date", "-trn_time"]


class PriceBatch(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    target = models.CharField(max_length=10)
    payload = models.JSONField(default=list)
    status = models.CharField(max_length=20, default="pending")
    error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    synced_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "gold_price_batches"
        ordering = ["-created_at"]


class ThauAnhTam(models.Model):
    """Ảnh phiếu thâu ĐANG NHẬP (chưa thanh toán) — giữ theo session, mỗi slot 1 dòng; THANH TOÁN xong chuyển sang
    gold_bill.anh_* rồi xóa. Dọn dòng quá 2 ngày khi mở phiếu mới."""

    session_key = models.CharField(max_length=40, db_index=True)
    slot = models.CharField(max_length=10)                 # cccd1 · cccd2 · hinh1 · hinh2 · qr
    data = models.BinaryField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "thau_anh_tam"
        unique_together = [("session_key", "slot")]


class ThauNhom(models.Model):
    """NHÓM phiếu thâu web (08/09/2026 tối, GĐ chốt): PMVGoldRT chỉ Ins 1 dòng/1 phiếu → web cho nhiều dòng cùng 1 khách,
    mỗi dòng = 1 TRN_RT_BUYGOLD, cả nhóm chốt chung CompleteMore 'A@B@'. Bảng này giữ danh sách TrnID của nhóm để mở lại /
    in 1 tờ 110mm + phần app-only KK không giữ (kiểu giá từng dòng, cách thanh toán)."""

    trn_ids = models.JSONField("Các TrnID TRN_RT_BUYGOLD", default=list)
    bill_codes = models.JSONField("Số phiếu KK", default=list)
    kieu = models.JSONField("Kiểu giá từng dòng (thau/ban)", default=list)
    cust_id = models.CharField(max_length=15, blank=True, default="")
    cust_name = models.CharField(max_length=200, blank=True, default="")
    emp_id = models.CharField(max_length=15, blank=True, default="")
    pay_method = models.CharField(max_length=10, default="cash")
    tien_mat = models.DecimalField(max_digits=18, decimal_places=3, default=0)
    tien_ck = models.DecimalField(max_digits=18, decimal_places=3, default=0)
    bu = models.DecimalField(max_digits=18, decimal_places=3, default=0)
    bot = models.DecimalField(max_digits=18, decimal_places=3, default=0)
    ghi_chu = models.CharField(max_length=300, blank=True, default="")
    # thông tin CHUYỂN KHOẢN cho khách (08/09 tối): ngân hàng (mã/BIN) · số TK · nội dung (mặc định = mã phiếu)
    ck_bank = models.CharField(max_length=12, blank=True, default="")
    ck_stk = models.CharField(max_length=40, blank=True, default="")
    ck_nd = models.CharField(max_length=60, blank=True, default="")
    ck_ten = models.CharField("Tên chủ TK", max_length=100, blank=True, default="")
    # ẢNH CỦA NHÓM ĐƠN (GĐ chốt 09/09/2026 — "dọn sang nhà mới" từ gold_bill.anh_*): Hình 1 · Hình 2 · QR chuyển khoản.
    # CCCD KHÔNG ở đây — thuộc hồ sơ KHÁCH (I_CUSTOMER trên máy KK + file media/cccd/ trên máy chủ).
    anh_hinh1 = models.BinaryField("Hình 1", null=True, blank=True, editable=False)
    anh_hinh2 = models.BinaryField("Hình 2", null=True, blank=True, editable=False)
    anh_qr = models.BinaryField("QR chuyển khoản", null=True, blank=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)

    class Meta:
        db_table = "thau_nhom"
        ordering = ["-id"]
        verbose_name = "Nhóm phiếu thâu"
        verbose_name_plural = "Nhóm phiếu thâu"

    def __str__(self):
        return f"Thâu #{self.pk} {', '.join(self.bill_codes or self.trn_ids)}"
