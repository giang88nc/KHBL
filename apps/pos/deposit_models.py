"""Dữ liệu vận hành ĐẶT-CỌC của KHBL; mọi khóa đều tách KK/bản thử."""
import uuid
from django.db import models


class DepositOrderState(models.Model):
    target = models.CharField(max_length=10)
    trn_id = models.CharField(max_length=20)
    version = models.PositiveIntegerField(default=0)
    original_promise = models.DateField(null=True, blank=True)
    promise = models.DateField(null=True, blank=True)
    employee_id = models.CharField(max_length=20, blank=True)
    employee_name = models.CharField(max_length=200, blank=True)
    fulfilment = models.CharField(max_length=20, blank=True)
    items = models.JSONField(default=list)
    item_signature = models.CharField(max_length=64, blank=True)
    next_contact = models.DateTimeField(null=True, blank=True, db_index=True)
    last_contact = models.DateTimeField(null=True, blank=True)
    contacted = models.BooleanField(null=True)
    note = models.CharField(max_length=1000, blank=True)
    ready_at = models.DateTimeField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    quote_status = models.CharField(max_length=12, default='estimate')
    quote_amount = models.DecimalField(max_digits=18, decimal_places=3, null=True, blank=True)
    quote_terms = models.CharField(max_length=1000, blank=True)
    pricing = models.JSONField(default=dict)
    payment_plan = models.JSONField(default=dict)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['target','trn_id'], name='dc_state_target_order')]


class DepositEvent(models.Model):
    target = models.CharField(max_length=10)
    trn_id = models.CharField(max_length=20, db_index=True)
    action = models.CharField(max_length=30)
    username = models.CharField(max_length=150)
    note = models.TextField(blank=True)
    data = models.JSONField(default=dict)
    token = models.CharField(max_length=64, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    @property
    def action_name(self):
        return {'contact':'Liên hệ / nhắc hẹn','progress':'Tiến độ giao hàng','hold':'Giữ hàng','release':'Giải phóng hàng',
            'quote':'Thỏa thuận giá','cancel':'Hủy đặt hàng','money_receive':'Thu cọc','money_refund':'Hoàn cọc',
            'money_apply':'Cấn cọc hóa đơn','message_edit':'Sửa thông báo','message_delete':'Xóa khỏi lịch gửi',
            'message_schedule':'Lên lịch thông báo','message_result':'Kết quả gửi thông báo'}.get(self.action,self.action)

    def save(self, *args, **kwargs):
        if self.pk: raise ValueError('Nhật ký chỉ được ghi thêm.')
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError('Không xóa nhật ký đặt hàng.')


class DepositStockHold(models.Model):
    target = models.CharField(max_length=10)
    trn_id = models.CharField(max_length=20, db_index=True)
    product_code = models.CharField(max_length=80)
    active_key = models.CharField(max_length=100, null=True, unique=True)
    location = models.CharField(max_length=200)
    username = models.CharField(max_length=150)
    created_at = models.DateTimeField(auto_now_add=True)
    released_at = models.DateTimeField(null=True)
    released_by = models.CharField(max_length=150, blank=True)


class DepositMoneyOperation(models.Model):
    target = models.CharField(max_length=10)
    trn_id = models.CharField(max_length=20, db_index=True)
    kind = models.CharField(max_length=20)
    amount = models.DecimalField(max_digits=18, decimal_places=3)
    cash = models.DecimalField(max_digits=18, decimal_places=3, default=0)
    bank = models.DecimalField(max_digits=18, decimal_places=3, default=0)
    invoice_id = models.CharField(max_length=20, blank=True)
    bank_key = models.CharField(max_length=60, null=True, unique=True)
    active_key = models.CharField(max_length=60, null=True, unique=True)
    status = models.CharField(max_length=20, default='queued', db_index=True)
    token = models.CharField(max_length=64, unique=True)
    user_id = models.CharField(max_length=20)
    till_id = models.CharField(max_length=20)
    username = models.CharField(max_length=150)
    evidence = models.JSONField(default=dict)
    message = models.CharField(max_length=1000, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True)


class DepositMessageTemplate(models.Model):
    name = models.CharField(max_length=120)
    provider_id = models.CharField(max_length=80, blank=True)
    body = models.TextField()
    approved_body_hash = models.CharField(max_length=64, blank=True)
    parameter_map = models.JSONField(default=dict)
    status = models.CharField(max_length=20, default='draft')
    version = models.PositiveIntegerField(default=1)
    updated_at = models.DateTimeField(auto_now=True)


class DepositMessage(models.Model):
    target = models.CharField(max_length=10)
    trn_id = models.CharField(max_length=20, db_index=True)
    customer_name = models.CharField(max_length=200)
    phone = models.CharField(max_length=30)
    template = models.ForeignKey(DepositMessageTemplate, on_delete=models.PROTECT)
    template_version = models.PositiveIntegerField()
    body = models.TextField()
    parameters = models.JSONField(default=dict)
    purpose = models.CharField(max_length=20, default='ready')
    planned_day = models.DateField(db_index=True)
    scheduled_at = models.DateTimeField(null=True, db_index=True)
    status = models.CharField(max_length=20, default='draft', db_index=True)
    version = models.PositiveIntegerField(default=0)
    tracking_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    dedupe_key = models.CharField(max_length=64, null=True, unique=True)
    username = models.CharField(max_length=150)
    provider_message_id = models.CharField(max_length=150, blank=True)
    error = models.CharField(max_length=1000, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(null=True)
    claimed_at = models.DateTimeField(null=True)
