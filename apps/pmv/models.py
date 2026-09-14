from django.conf import settings
from django.db import models


class PmvAudit(models.Model):
    """Nhật ký MỌI lời gọi sang SQL Server PMV — kể cả lệnh bị CHẶN (vượt quyền)."""

    class Kind(models.TextChoices):
        READ = "READ", "Đọc"
        EXEC = "EXEC", "Gọi proc"
        ADMIN = "ADMIN", "Quản trị (backup/tiện ích)"
        BLOCKED = "BLOCKED", "CHẶN — vượt quyền"
        CANHBAO = "CANHBAO", "Cảnh báo"

    created_at = models.DateTimeField(auto_now_add=True, db_index=True, verbose_name="Thời điểm")
    kind = models.CharField(max_length=10, choices=Kind.choices, db_index=True, verbose_name="Loại")
    tag = models.CharField(max_length=100, verbose_name="Nghiệp vụ gọi")
    summary = models.CharField(max_length=500, verbose_name="Lệnh/tóm tắt")
    ok = models.BooleanField(default=True, verbose_name="Thành công")
    duration_ms = models.IntegerField(default=0, verbose_name="Thời lượng (ms)")
    error = models.TextField(blank=True, default="", verbose_name="Lỗi")

    class Meta:
        db_table = "pmv_audit_logs"
        ordering = ["-id"]
        verbose_name = "Nhật ký PMV"
        verbose_name_plural = "Nhật ký PMV"

    def __str__(self):
        return f"[{self.kind}] {self.tag}: {self.summary[:60]}"


class PmvState(models.Model):
    """Trạng thái key-value: lần backup cuối, vân tay version DB vendor, khóa ghi..."""

    key = models.CharField(max_length=100, unique=True)
    value = models.TextField(blank=True, default="")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "pmv_state"
        verbose_name = "Trạng thái PMV"
        verbose_name_plural = "Trạng thái PMV"

    def __str__(self):
        return f"{self.key} = {self.value[:60]}"

    @classmethod
    def get(cls, key, default=""):
        row = cls.objects.filter(key=key).first()
        return row.value if row else default

    @classmethod
    def set(cls, key, value):
        cls.objects.update_or_create(key=key, defaults={"value": str(value)})


class PmvBehavior(models.Model):
    """1 dòng = 1 hành vi của PMVGoldRT quan sát được (lớp THỐNG KÊ hoặc lớp TRACE)."""

    class Source(models.TextChoices):
        STATS = "STATS", "Thống kê (bộ đếm SQL)"
        TRACE = "TRACE", "Trace (từng lời gọi + tham số)"

    event_time = models.DateTimeField(db_index=True, verbose_name="Thời điểm (giờ KK)")
    collected_at = models.DateTimeField(auto_now_add=True)
    source = models.CharField(max_length=6, choices=Source.choices, db_index=True)
    category = models.CharField(max_length=30, db_index=True, verbose_name="Nhóm")
    action = models.CharField(max_length=10, verbose_name="Hành động")  # ghi | đọc | hệ thống
    proc_name = models.CharField(max_length=128, db_index=True, blank=True, default="")
    text = models.TextField(blank=True, default="", verbose_name="Lệnh + tham số")
    login = models.CharField(max_length=64, blank=True, default="")
    host = models.CharField(max_length=64, blank=True, default="")
    app = models.CharField(max_length=128, blank=True, default="")
    spid = models.IntegerField(null=True, blank=True)
    duration_ms = models.IntegerField(null=True, blank=True)
    reads = models.IntegerField(null=True, blank=True)
    writes = models.IntegerField(null=True, blank=True)
    row_count = models.IntegerField(null=True, blank=True)
    exec_delta = models.IntegerField(null=True, blank=True, verbose_name="Số lần gọi thêm")
    event_seq = models.BigIntegerField(null=True, blank=True)

    class Meta:
        db_table = "pmv_behavior_logs"
        ordering = ["-event_time", "-id"]
        verbose_name = "Hành vi PMV"
        verbose_name_plural = "Hành vi PMV"

    def __str__(self):
        return f"[{self.source}] {self.event_time:%d/%m %H:%M:%S} {self.proc_name or self.text[:40]}"


class PmvChange(models.Model):
    """1 dòng = 1 BẢNG của SQL KK đổi trong 1 khung thời gian (Bước 2 — dò thay đổi, 05/09/2026).
    mark rỗng = dò liên tục 2 phút; 'danhdau:<id>' = chế độ ĐÁNH DẤU TRƯỚC/SAU thủ công."""

    window_start = models.DateTimeField(db_index=True, verbose_name="Từ")
    window_end = models.DateTimeField(verbose_name="Đến")
    table = models.CharField(max_length=128, db_index=True, verbose_name="Bảng")
    rows_before = models.IntegerField(null=True, blank=True)
    rows_after = models.IntegerField(null=True, blank=True)
    delta = models.IntegerField(null=True, blank=True)
    cs_changed = models.BooleanField(default=False, verbose_name="Checksum đổi")
    procs = models.TextField(blank=True, default="", verbose_name="Proc ghi trong khung")
    mark = models.CharField(max_length=64, blank=True, default="", db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "pmv_change_logs"
        ordering = ["-id"]
        verbose_name = "Thay đổi SQL KK"
        verbose_name_plural = "Thay đổi SQL KK"

    def __str__(self):
        return f"[{self.window_end:%d/%m %H:%M}] {self.table} {self.delta:+d}" if self.delta else f"[{self.window_end:%d/%m %H:%M}] {self.table}"


class PmvProcess(models.Model):
    """QUY TRÌNH nghiệp vụ của PMVGoldRT đã HỌC được từ thao tác thật (GĐ chốt 05/09/2026):
    cây PHA → BƯỚC theo thứ tự từ 0 đến hoàn thành. Mỗi lần đổi tự LƯU ra
    docs/quy_trinh/<code>.md + .json (apps/pmv/quy_trinh.py)."""

    class Status(models.TextChoices):
        DRAFT = "draft", "Bản nháp (mới học)"
        APPROVED = "approved", "GĐ đã duyệt"

    code = models.CharField(max_length=40, unique=True, verbose_name="Mã")          # BAN_HANG
    name = models.CharField(max_length=150, verbose_name="Tên quy trình")
    description = models.TextField(blank=True, default="", verbose_name="Mô tả")
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT)
    version = models.IntegerField(default=1)
    source = models.TextField(blank=True, default="", verbose_name="Nguồn học (hóa đơn/khung giờ đo)")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "pmv_process"
        ordering = ["code"]
        verbose_name = "Quy trình PMV"
        verbose_name_plural = "Quy trình PMV"

    def __str__(self):
        return f"{self.code} v{self.version}"


class PmvProcessStep(models.Model):
    """1 nút trong cây quy trình: PHA (parent=None, không proc) hoặc BƯỚC (parent=pha)."""

    class Khbl(models.TextChoices):
        LAM = "lam", "KHBL thực hiện"
        KIEM = "kiem", "Cần kiểm trên sandbox"
        RIENG = "rieng", "KHBL làm cách riêng"
        BO = "bo", "Ngoài phạm vi — bỏ"

    class DoiChieu(models.TextChoices):
        CHUA = "chua", "Chưa đối chiếu"
        KHOP = "khop", "Khớp app"
        LECH = "lech", "Lệch app"

    process = models.ForeignKey(PmvProcess, on_delete=models.CASCADE, related_name="steps")
    parent = models.ForeignKey("self", null=True, blank=True, on_delete=models.CASCADE, related_name="children")
    order = models.IntegerField(default=0)
    title = models.CharField(max_length=150, verbose_name="Tên pha / bước")
    proc_name = models.CharField(max_length=128, blank=True, default="", verbose_name="Proc")
    action = models.CharField(max_length=10, blank=True, default="", verbose_name="ghi/đọc")
    repeat = models.BooleanField(default=False, verbose_name="Lặp nhiều lần (×N)")
    params = models.TextField(blank=True, default="", verbose_name="Tham số quan trọng")
    tables = models.TextField(blank=True, default="", verbose_name="Bảng đổi")
    khbl = models.CharField(max_length=10, choices=Khbl.choices, default=Khbl.LAM)
    doi_chieu = models.CharField(max_length=10, choices=DoiChieu.choices, default=DoiChieu.CHUA)
    note = models.TextField(blank=True, default="", verbose_name="Ghi chú")

    class Meta:
        db_table = "pmv_process_step"
        ordering = ["process", "parent_id", "order", "id"]
        verbose_name = "Bước quy trình"
        verbose_name_plural = "Bước quy trình"

    def __str__(self):
        return f"{self.process.code} · {self.title}"

    @property
    def la_pha(self):
        return self.parent_id is None


class PmvUser(models.Model):
    """Bản sao SYS_USERS của app KK cho 2 tài khoản web dùng (GĐ chốt 03/09/2026: admin,
    kimhanh2). Web stamp UserID/TillID/EmpID này khi gọi proc → app KK hiện đúng tên/két.
    Password = MÃ BĂM Django (khởi tạo từ mật khẩu app), không chép plain text."""

    user_id = models.CharField("UserID", max_length=15, primary_key=True)
    user_name = models.CharField("UserName", max_length=50, unique=True)
    password = models.CharField("Password (băm)", max_length=128, blank=True, default="")
    first_name = models.CharField("FirstName", max_length=100, blank=True, default="")
    last_name = models.CharField("LastName", max_length=100, blank=True, default="")
    full_name = models.CharField("FullName", max_length=200, blank=True, default="")
    is_admin = models.CharField("IsAdmin", max_length=1, default="0")
    active = models.CharField("Active", max_length=1, default="1")
    shop_id = models.CharField("ShopID", max_length=15, blank=True, default="")
    emp_id = models.CharField("EmpID", max_length=15, blank=True, default="")
    till_id = models.CharField("TillID (két của user — proc bán cần)", max_length=15, blank=True, default="")
    till_code = models.CharField("TillCode", max_length=50, blank=True, default="")
    django_user = models.OneToOneField(
        "auth.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="pmv_user",
    )
    synced_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "sys_users"
        verbose_name = "Tài khoản PMV (bản sao)"
        verbose_name_plural = "Tài khoản PMV (bản sao)"

    def __str__(self):
        return f"{self.user_name} ({self.user_id})"


class PmvWebUser(models.Model):
    """Phân vai Web User → hồ sơ PMV/KK.

    Mỗi tài khoản web chọn một hồ sơ PMV để lấy UserID, EmpID và TillID khi gọi
    KK. Nhiều Web User có thể cùng chọn một PmvUser, phù hợp khi nhân viên cùng
    thao tác trên một tài khoản/két KK. ``PmvUser.django_user`` được giữ như liên
    kết cũ/canonical cho lệnh đồng bộ, còn runtime ưu tiên bảng này.
    """
    web_user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="pmv_mappings")
    pmv_user = models.ForeignKey(PmvUser, on_delete=models.CASCADE, related_name="web_mappings")
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name="+")

    class Meta:
        db_table = "pmv_web_users"
        constraints = [models.UniqueConstraint(fields=("web_user",), name="uq_pmv_web_user")]
        verbose_name = "Liên kết User web — PMV"
        verbose_name_plural = "Liên kết User web — PMV"

    def __str__(self):
        return f"{self.web_user.username} → {self.pmv_user.user_name}"


def pmv_user_for_web_user(web_user):
    """Hồ sơ PMV hiệu lực của Web User, giữ fallback cho liên kết một-một cũ."""
    if not web_user or not web_user.is_authenticated:
        return None
    mapping = PmvWebUser.objects.select_related("pmv_user").filter(web_user=web_user).first()
    return mapping.pmv_user if mapping else PmvUser.objects.filter(django_user=web_user).first()


class UserModuleAccess(models.Model):
    """Quyền vận hành KHBL theo danh mục, tách khỏi quyền kỹ thuật của Django.

    Mỗi user có tối đa một dòng cho mỗi danh mục. Trang quản trị dùng
    ``update_or_create`` nên lưu ma trận luôn là UPSERT, không sinh bản ghi trùng.
    Superuser luôn bỏ qua giới hạn của ma trận quyền.
    """
    class Module(models.TextChoices):
        DASHBOARD = "DASHBOARD", "Tổng quan"
        BAN_HANG = "BAN_HANG", "Bán hàng"
        THAU_VAO = "THAU_VAO", "Thâu vào"
        BANG_GIA = "BANG_GIA", "Bảng giá"
        KHACH_HANG = "KHACH_HANG", "Khách hàng"
        HOA_DON = "HOA_DON", "Hóa đơn"
        DAT_COC = "DAT_COC", "Đặt cọc"
        BAO_CAO = "BAO_CAO", "Báo cáo"
        CHUYEN_KHOAN = "CHUYEN_KHOAN", "Chuyển khoản"
        HE_THONG = "HE_THONG", "Hệ thống"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="module_accesses")
    module = models.CharField(max_length=20, choices=Module.choices)
    can_view = models.BooleanField(default=False, verbose_name="Xem")
    can_edit = models.BooleanField(default=False, verbose_name="Tạo / sửa")
    can_delete = models.BooleanField(default=False, verbose_name="Hủy / xóa")
    can_approve = models.BooleanField(default=False, verbose_name="Duyệt / chốt")
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name="+")

    class Meta:
        db_table = "user_module_access"
        verbose_name = "Quyền người dùng theo danh mục"
        verbose_name_plural = "Quyền người dùng theo danh mục"
        constraints = [models.UniqueConstraint(fields=("user", "module"), name="uq_user_module_access")]
        ordering = ["module"]

    def __str__(self):
        return f"{self.user.username} · {self.get_module_display()}"


class PmvSnapshot(models.Model):
    """Vân tay toàn bộ bảng của 1 nguồn tại 1 thời điểm — để so TRƯỚC/SAU (diff.py)."""

    class Source(models.TextChoices):
        PMV = "pmv", "PMV thật"
        SANDBOX = "sandbox", "Sandbox"

    label = models.CharField(max_length=150, verbose_name="Nhãn")
    source = models.CharField(max_length=10, choices=Source.choices, verbose_name="Nguồn")
    mode = models.CharField(max_length=8, default="fast", verbose_name="Mức")  # fast | full
    created_at = models.DateTimeField(auto_now_add=True)
    table_count = models.IntegerField(default=0)
    payload = models.JSONField(default=dict)  # {tbl: {rows, cs}}

    class Meta:
        db_table = "pmv_snapshots"
        ordering = ["-id"]
        verbose_name = "Snapshot PMV"
        verbose_name_plural = "Snapshot PMV"

    def __str__(self):
        return f"#{self.pk} {self.label} ({self.get_source_display()}/{self.mode})"
