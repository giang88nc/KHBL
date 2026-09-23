"""Nhật ký hóa đơn (bill_audit) — CHỈ XEM trong Django admin: không thêm/sửa/xóa (append-only)."""
from django.contrib import admin

from .models import BillAudit, MobileEmployeeIdentity


@admin.register(MobileEmployeeIdentity)
class MobileEmployeeIdentityAdmin(admin.ModelAdmin):
    list_display = ('employee_id','user','employee_pmv','mobile_only','enabled')
    search_fields = ('user__username',)
    list_filter = ('enabled',)


@admin.register(BillAudit)
class BillAuditAdmin(admin.ModelAdmin):
    list_display = ("created_at", "action", "bill_code", "trn_id", "username", "version", "note")
    list_filter = ("action",)
    search_fields = ("trn_id", "bill_code", "username")
    readonly_fields = [f.name for f in BillAudit._meta.fields]
    ordering = ("-created_at",)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
