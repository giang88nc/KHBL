from django.apps import AppConfig


class PosConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.pos"
    verbose_name = "Bán lẻ"

    def ready(self):
        # Nạp chốt chặn chú thích template nhiều dòng → thành check của Django (mã khbl.E001)
        from . import kiem_template  # noqa: F401
