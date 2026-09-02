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
