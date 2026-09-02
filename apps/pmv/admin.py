from django.contrib import admin

from .models import PmvAudit, PmvState


@admin.register(PmvAudit)
class PmvAuditAdmin(admin.ModelAdmin):
    list_display = ("created_at", "kind", "tag", "summary", "ok", "duration_ms")
    list_filter = ("kind", "ok")
    search_fields = ("tag", "summary", "error")
    readonly_fields = [f.name for f in PmvAudit._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(PmvState)
class PmvStateAdmin(admin.ModelAdmin):
    list_display = ("key", "value", "updated_at")
