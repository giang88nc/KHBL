from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("pos", "0005_gold_bill"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="ThauNhom",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("trn_ids", models.JSONField(default=list, verbose_name="Các TrnID TRN_RT_BUYGOLD")),
                ("bill_codes", models.JSONField(default=list, verbose_name="Số phiếu KK")),
                ("kieu", models.JSONField(default=list, verbose_name="Kiểu giá từng dòng (thau/ban)")),
                ("cust_id", models.CharField(blank=True, default="", max_length=15)),
                ("cust_name", models.CharField(blank=True, default="", max_length=200)),
                ("emp_id", models.CharField(blank=True, default="", max_length=15)),
                ("pay_method", models.CharField(default="cash", max_length=10)),
                ("tien_mat", models.DecimalField(decimal_places=3, default=0, max_digits=18)),
                ("tien_ck", models.DecimalField(decimal_places=3, default=0, max_digits=18)),
                ("bu", models.DecimalField(decimal_places=3, default=0, max_digits=18)),
                ("bot", models.DecimalField(decimal_places=3, default=0, max_digits=18)),
                ("ghi_chu", models.CharField(blank=True, default="", max_length=300)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("user", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL)),
            ],
            options={"db_table": "thau_nhom", "verbose_name": "Nhóm phiếu thâu", "verbose_name_plural": "Nhóm phiếu thâu",
                     "ordering": ["-id"]},
        ),
    ]
