"""Durable receipts for KHCD requests; separate from one-way import receipts."""
from django.db import models


class CustomerBridgeReceipt(models.Model):
    token = models.CharField(max_length=80, primary_key=True)
    target = models.CharField(max_length=12)
    user_id = models.PositiveBigIntegerField()
    fingerprint = models.CharField(max_length=64)
    cust_id = models.CharField(max_length=15, blank=True)
    status = models.CharField(max_length=16, default="writing")
    result = models.JSONField(default=dict)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "customer_bridge_receipt"
