from django.contrib import admin

from .models import PmvAudit, PmvBehavior, PmvSnapshot, PmvState, PmvUser


@admin.register(PmvUser)
class PmvUserAdmin(admin.ModelAdmin):
    list_display = ("user_name", "user_id", "full_name", "emp_id", "till_id", "till_code", "active", "django_user", "synced_at")
    readonly_fields = ("password",)


@admin.register(PmvBehavior)
class PmvBehaviorAdmin(admin.ModelAdmin):
    list_display = ("event_time", "source", "category", "action", "proc_name", "host", "duration_ms", "exec_delta")
    list_filter = ("source", "category", "action")
    search_fields = ("proc_name", "text", "host")


@admin.register(PmvSnapshot)
class PmvSnapshotAdmin(admin.ModelAdmin):
    list_display = ("id", "label", "source", "mode", "table_count", "created_at")
    list_filter = ("source", "mode")


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
