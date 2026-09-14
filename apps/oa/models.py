# -*- coding: utf-8 -*-
"""ẢNH CHIẾU 3 BẢNG ZALO ZNS TRONG DB ``khj_bl`` — Django KHÔNG SỞ HỮU SCHEMA.

⚠⚠ ĐỌC TRƯỚC KHI SỬA BẤT KỲ DÒNG NÀO Ở ĐÂY ⚠⚠

1. BA BẢNG NÀY LÀ BẢN CHÉP NGUYÊN TỪ CARE360.
   Giám đốc tự tay chép ``zalo_templates`` (3 dòng) · ``zalo_send_rules`` (2 dòng) ·
   ``zalo_messages`` (**16.657 dòng LỊCH SỬ GỬI THẬT**) từ DB ``pmv_report`` của CARE360
   (``D:\\PYTHON\\KIMHANH\\KH_GATEWAY\\ZALO``) sang ``khj_bl``. Tên cột, kiểu cột, khóa duy nhất
   đều là của CARE360 — **DDL THẬT trong MySQL là NGUỒN SỰ THẬT**, tệp này chỉ soi chiếu lại.

2. ``managed = False`` TRÊN CẢ BA MODEL — KHÔNG BAO GIỜ ĐỔI.
   Django không được sinh DDL, không ``CREATE TABLE``, không ``ALTER``. ``django_migrations``
   chưa hề có dòng nào của app ``oa``; migration ``0001_initial`` chỉ ghi TRẠNG THÁI (CreateModel
   với ``managed=False``) nên ``manage.py migrate`` là lệnh RỖNG với app này.
   Muốn đổi cấu trúc bảng ⇒ đổi ở MySQL bằng tay, rồi sửa tệp này cho khớp.

3. TUYỆT ĐỐI KHÔNG XÓA / SỬA ĐÈ 16.657 DÒNG LỊCH SỬ.
   Chúng là tin ĐÃ GỬI THẬT cho khách (``sent`` 15.015 · ``cancelled`` 959 ·
   ``failed_permanent`` 683). Mọi đường ghi của KHBL chỉ được chạm dòng
   ``source_type='khbl_invoice'`` — xem :mod:`apps.oa.xep_hang`.

4. KHJ ĐỌC 3 BẢNG NÀY QUA USER CHỈ-ĐỌC.
   ``D:\\PYTHON\\KHJ\\apps\\oa_messages`` là ảnh chiếu chỉ-đọc của đúng 3 bảng này. Đổi tên cột
   ở MySQL mà quên sửa bên KHJ ⇒ trang "OA TIN NHẮN" nổ MySQL 1054, hoặc tệ hơn: mở được mà số
   ra 0 không báo lỗi.

GIỜ GIẤC — BẪY ĐÃ TRẢ GIÁ: mọi cột ``datetime`` trong bảng là **UTC NAIVE** (CARE360 ghi bằng
``_mssql_local_to_utc()``; dữ liệu thật: hóa đơn 12:03:43 giờ VN ⇒ ``eligible_at = 05:03:43``).
KHBL chạy ``USE_TZ=True`` nên Django tự quy đổi: gán datetime **AWARE** vào field là ghi ra UTC
naive đúng chuẩn CARE360. **KHÔNG BAO GIỜ gán datetime naive** (Django diễn giải ngầm + cảnh báo)
và **KHÔNG BAO GIỜ trừ tay 7 giờ**.
"""
from django.db import models


# ───────────────────────────── zalo_templates ─────────────────────────────
class ZaloTemplate(models.Model):
    """Mẫu ZNS đã được Zalo duyệt. Gửi được = ``active`` VÀ ``status == 'ENABLE'``.

    Dữ liệu thật (14/09/2026): 13 = 606576 "Đánh giá chất lượng 2026" tag CUSTOMER_CARE ·
    14 = 606575 "Xác nhận đơn hàng 2026" tag TRANSACTION · 15 = 630604 "Xét duyệt nghỉ phép"
    (chưa quy tắc nào dùng). Cả ba ``price_sdt = 300.00``.
    """

    id = models.BigAutoField(primary_key=True)
    oa_account_id = models.BigIntegerField()
    # ⚠ Đây là MÃ MẪU BÊN ZALO (chuỗi, vd "606575") — KHÔNG phải khóa nội bộ.
    # Khóa nội bộ của mẫu là cột `id`; `zalo_messages.template_id` trỏ vào `id` đó.
    template_id = models.CharField(max_length=100)
    template_name = models.CharField(max_length=250)
    template_type = models.CharField(max_length=50, null=True, blank=True)
    template_tag = models.CharField(max_length=50, null=True, blank=True)
    status = models.CharField(max_length=40)
    quality = models.CharField(max_length=30)
    price_sdt = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    price_uid = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    preview_url = models.CharField(max_length=1000, null=True, blank=True)
    # JSON dạng TEXT (CARE360 tự json.dumps, không dùng kiểu JSON của MySQL).
    params_json = models.TextField(null=True, blank=True)
    layout_json = models.TextField(null=True, blank=True)
    raw_json = models.TextField(null=True, blank=True)
    active = models.BooleanField(default=False)
    zalo_created_at = models.DateTimeField(null=True, blank=True)
    last_synced_at = models.DateTimeField()
    created_at = models.DateTimeField()
    updated_at = models.DateTimeField()

    class Meta:
        managed = False
        db_table = "zalo_templates"
        verbose_name = "Mẫu ZNS"
        verbose_name_plural = "Mẫu ZNS"

    def __str__(self):
        return f"{self.template_id} · {self.template_name}"

    @property
    def gui_duoc(self):
        """Mẫu còn dùng được để gửi (hai điều kiện AND, thiếu một là không)."""
        return bool(self.active) and str(self.status or "").upper() == "ENABLE"


# ──────────────────────────── zalo_send_rules ─────────────────────────────
class ZaloSendRule(models.Model):
    """Quy tắc gửi tự động theo NHÓM TIN. Dữ liệu thật (14/09/2026):

    · id=1 ``auto_send_affter_2m`` · CUSTOMER_CARE · mẫu 13 (606576) · delay 3' · cooldown 60'
    · id=2 ``auto_send_affter_5m`` · TRANSACTION   · mẫu 14 (606575) · delay 5' · cooldown 0
    Cả hai ``is_active=1``, ``effective_from = 2026-08-11 10:59:10``, và ``last_scan_at`` VẪN NHẢY
    liên tục — tức **CARE360 đang gửi thật cho chính những hóa đơn này** (xem cảnh báo GỬI ĐÔI ở
    :mod:`apps.oa.xep_hang`).

    ⚠ ``cooldown_scope`` ('PHONE_GROUP') KHÔNG được đọc ở đâu cả — phạm vi "số điện thoại + nhóm
    tin" là CỨNG trong truy vấn khoảng lặng. Đừng viết nhánh theo cột này.
    ⚠ ``daily_message_limit`` / ``daily_budget`` / ``send_time_from`` / ``send_time_to`` /
    ``max_attempts`` / ``retry_base_minutes`` thuộc LỚP GỬI (bước 2, chưa làm). Bước xếp hàng chỉ
    chụp chúng vào ``rule_snapshot_json``.
    """

    id = models.BigAutoField(primary_key=True)
    rule_name = models.CharField(max_length=99, null=True, blank=True)
    oa_account_id = models.BigIntegerField()
    # db_constraint=False: DDL thật KHÔNG có FOREIGN KEY, chỉ có index. Không được để Django
    # sinh ràng buộc mới trên bảng mà nó không sở hữu.
    template = models.ForeignKey(
        ZaloTemplate, on_delete=models.DO_NOTHING, db_constraint=False,
        db_column="template_id", null=True, blank=True, related_name="send_rules",
    )
    message_group = models.CharField(max_length=30)
    is_active = models.BooleanField(default=False)
    delay_minutes = models.IntegerField(default=0)
    cooldown_minutes = models.IntegerField(default=0)
    cooldown_scope = models.CharField(max_length=30)
    apply_to_manual_messages = models.BooleanField(default=False)
    scan_lookback_minutes = models.IntegerField(default=0)
    daily_message_limit = models.IntegerField(null=True, blank=True)
    daily_budget = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    max_attempts = models.IntegerField(default=0)
    retry_base_minutes = models.IntegerField(default=0)
    send_time_from = models.TimeField(null=True, blank=True)
    send_time_to = models.TimeField(null=True, blank=True)
    effective_from = models.DateTimeField(null=True, blank=True)
    last_scan_at = models.DateTimeField(null=True, blank=True)
    options_json = models.TextField(null=True, blank=True)
    created_by = models.BigIntegerField(null=True, blank=True)
    created_at = models.DateTimeField()
    updated_at = models.DateTimeField()

    class Meta:
        managed = False
        db_table = "zalo_send_rules"
        verbose_name = "Quy tắc gửi ZNS"
        verbose_name_plural = "Quy tắc gửi ZNS"

    def __str__(self):
        return f"#{self.id} {self.rule_name or ''} · {self.message_group}"


# ───────────────────────────── zalo_messages ──────────────────────────────
class ZaloMessage(models.Model):
    """Sổ TỪNG TIN — vừa là lịch sử, vừa là HÀNG ĐỢI (``status='queued'`` + ``scheduled_at``).

    ⚠ BẢNG NÀY CHỨA 16.657 DÒNG THẬT CỦA CARE360 (``source_type='auto_rule'``).
    KHBL chỉ được ghi/sửa dòng ``source_type='khbl_invoice'``. Mọi truy vấn ghi PHẢI có bộ lọc đó.

    BA KHÓA DUY NHẤT phải tôn trọng (DDL thật):
      · ``uq_zalo_message_auto_rule_bill (oa_account_id, send_rule_id, trn_id)`` — NGUY HIỂM NHẤT:
        **không lọc source_type**, nên nhãn riêng của KHBL KHÔNG cứu được. Hóa đơn nào CARE360 đã
        ghi thì ô khóa đó đã bị chiếm.
      · ``ix_zalo_messages_dedupe_key (dedupe_key)`` UNIQUE.
      · ``uq_zalo_tracking_id (tracking_id)`` UNIQUE.
    """

    # Bộ trạng thái chuẩn của CARE360 (zbs_message_log.MESSAGE_LOG_STATUSES).
    TRANG_THAI_CUOI = ("sent", "delivered", "seen")
    TRANG_THAI_CON_SONG = ("queued", "retry", "sending", "sent", "delivered", "seen")

    id = models.BigAutoField(primary_key=True)
    campaign_id = models.BigIntegerField(null=True, blank=True)      # KHBL không dùng chiến dịch
    oa_account_id = models.BigIntegerField()
    template = models.ForeignKey(
        ZaloTemplate, on_delete=models.DO_NOTHING, db_constraint=False,
        db_column="template_id", null=True, blank=True, related_name="messages",
    )
    send_rule = models.ForeignKey(
        ZaloSendRule, on_delete=models.DO_NOTHING, db_constraint=False,
        db_column="send_rule_id", null=True, blank=True, related_name="messages",
    )
    external_template_id = models.CharField(max_length=100)          # mã mẫu bên Zalo, vd "606575"
    channel = models.CharField(max_length=20)                        # 'phone' — CHỮ THƯỜNG
    source_type = models.CharField(max_length=30)                    # 'auto_rule' | 'khbl_invoice'
    source_ref = models.CharField(max_length=100, null=True, blank=True)
    trn_id = models.CharField(max_length=30, null=True, blank=True)
    bill_code = models.CharField(max_length=100, null=True, blank=True)
    cust_id = models.CharField(max_length=50, null=True, blank=True)
    customer_name = models.CharField(max_length=200, null=True, blank=True)
    # ⚠ SỐ ĐIỆN THOẠI DẠNG RÕ, 10 số nội địa '0xxxxxxxxx'. Đây là cách CARE360 vốn đã lưu; lớp gửi
    # của CARE360 lấy số từ ĐÂY chứ không giải mã recipient_ciphertext.
    customer_phone = models.CharField(max_length=11, null=True, blank=True)
    emp_id = models.CharField(max_length=30, null=True, blank=True)
    employee_name = models.CharField(max_length=200, null=True, blank=True)
    shop_id = models.CharField(max_length=30, null=True, blank=True)
    till_id = models.CharField(max_length=30, null=True, blank=True)
    transaction_at = models.DateTimeField(null=True, blank=True)
    eligible_at = models.DateTimeField(null=True, blank=True)
    source_status = models.CharField(max_length=10, null=True, blank=True)
    source_updated_at = models.DateTimeField(null=True, blank=True)
    source_eligible = models.BooleanField(default=True)
    pay_amount = models.DecimalField(max_digits=18, decimal_places=3, null=True, blank=True)
    # NOT NULL. CARE360 ghi token Fernet 'gAAAAAB…'; KHBL KHÔNG giữ khóa của KH_GATEWAY nên ghi
    # hằng 'khbl_no_cipher' (xem apps/oa/xep_hang.KHONG_CO_MA_HOA).
    recipient_ciphertext = models.TextField()
    recipient_masked = models.CharField(max_length=40)
    recipient_hash = models.CharField(max_length=64)
    template_data_json = models.TextField()
    tracking_id = models.CharField(max_length=48, unique=True)
    dedupe_key = models.CharField(max_length=64, unique=True)
    msg_id = models.CharField(max_length=100, null=True, blank=True)  # có sau khi API trả (bước 2)
    status = models.CharField(max_length=30)
    scheduled_at = models.DateTimeField(null=True, blank=True)
    lease_token = models.CharField(max_length=40, null=True, blank=True)
    locked_until = models.DateTimeField(null=True, blank=True)
    attempt_count = models.IntegerField(default=0)
    next_retry_at = models.DateTimeField(null=True, blank=True)
    attempted_at = models.DateTimeField(null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    seen_at = models.DateTimeField(null=True, blank=True)
    sending_mode = models.CharField(max_length=10, null=True, blank=True)
    estimated_cost = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    # ⚠ DDL thật để varchar(11) (không phải số) và NULL 100% trên dòng thật — giữ nguyên kiểu.
    paid_cost = models.CharField(max_length=11, null=True, blank=True)
    error_code = models.CharField(max_length=50, null=True, blank=True)
    error_message = models.TextField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancel_reason = models.CharField(max_length=100, null=True, blank=True)
    rule_snapshot_json = models.TextField(null=True, blank=True)
    response_json = models.TextField(null=True, blank=True)
    created_by = models.BigIntegerField(null=True, blank=True)
    # ⚠ KHÔNG có DEFAULT ở DB và KHÔNG dùng auto_now_add/auto_now (bảng không thuộc Django,
    # và auto_now sẽ âm thầm đè khi save(update_fields=…) quên liệt kê). Đường ghi tự cấp giá trị.
    created_at = models.DateTimeField()
    updated_at = models.DateTimeField()

    class Meta:
        managed = False
        db_table = "zalo_messages"
        verbose_name = "Tin ZNS"
        verbose_name_plural = "Tin ZNS"

    def __str__(self):
        return f"#{self.id} {self.status} · {self.trn_id or ''}"
