"""Biên nhận sync một chiều; không sao chép CCCD hoặc tài khoản nguồn."""
from django.db import models


class CustomerSyncReceipt(models.Model):
    target = models.CharField(max_length=12)
    source_id = models.PositiveBigIntegerField()
    phone = models.CharField(max_length=30)
    fingerprint = models.CharField(max_length=64)
    cust_id = models.CharField(max_length=15, blank=True)
    status = models.CharField(max_length=16, default="writing")
    message = models.TextField(blank=True)
    payload = models.JSONField(default=dict)
    user_id = models.PositiveBigIntegerField(null=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "customer_sync_receipt"
        constraints = [models.UniqueConstraint(fields=["target", "source_id"], name="customer_sync_source_unique")]
