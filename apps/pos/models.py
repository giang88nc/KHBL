import uuid

from django.conf import settings
from django.db import models


class PriceDisplay(models.Model):
    gold_type = models.CharField(max_length=50, unique=True)
    pinned = models.BooleanField(default=False)
    position = models.PositiveIntegerField(default=1)
    unit = models.CharField(max_length=10, blank=True, default="")

    class Meta:
        db_table = "gold_price_display"
        ordering = ["position", "gold_type"]


class UnlockPasscode(models.Model):
    """PASSCODE mở khóa hóa đơn đã chốt — MỖI USER 1 passcode (GĐ chốt 07/09/2026), lưu MÃ BĂM Django
    (không plain text). User tự đổi (phải nhập passcode hiện tại / mật khẩu web nếu chưa có) hoặc
    superuser đặt cho bất kỳ ai. Kiểm ở views._passcode_dung: bản ghi này > .env > mật khẩu web."""

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
