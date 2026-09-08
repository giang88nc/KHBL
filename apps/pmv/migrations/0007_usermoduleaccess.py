# Generated manually for KHBL user access matrix.
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("pmv", "0006_pmvprocess"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="UserModuleAccess",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("module", models.CharField(choices=[
                    ("DASHBOARD", "Tổng quan"), ("BAN_HANG", "Bán hàng"), ("THAU_VAO", "Thâu vào"),
                    ("BANG_GIA", "Bảng giá"), ("KHACH_HANG", "Khách hàng"), ("HOA_DON", "Hóa đơn"),
                    ("HE_THONG", "Hệ thống")], max_length=20)),
                ("can_view", models.BooleanField(default=False, verbose_name="Xem")),
                ("can_edit", models.BooleanField(default=False, verbose_name="Tạo / sửa")),
                ("can_delete", models.BooleanField(default=False, verbose_name="Hủy / xóa")),
                ("can_approve", models.BooleanField(default=False, verbose_name="Duyệt / chốt")),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("updated_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="+", to=settings.AUTH_USER_MODEL)),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="module_accesses", to=settings.AUTH_USER_MODEL)),
            ],
            options={"verbose_name": "Quyền người dùng theo danh mục", "verbose_name_plural": "Quyền người dùng theo danh mục", "db_table": "user_module_access", "ordering": ["module"]},
        ),
        migrations.AddConstraint(
            model_name="usermoduleaccess",
            constraint=models.UniqueConstraint(fields=("user", "module"), name="uq_user_module_access"),
        ),
    ]
