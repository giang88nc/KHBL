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
