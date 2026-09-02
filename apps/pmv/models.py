from django.db import models


class PmvAudit(models.Model):
    """Nhật ký MỌI lời gọi sang SQL Server PMV — kể cả lệnh bị CHẶN (vượt quyền)."""

    class Kind(models.TextChoices):
        READ = "READ", "Đọc"
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
