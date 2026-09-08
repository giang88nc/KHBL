from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [("pos", "0006_thau_nhom")]

    operations = [
        migrations.AddField(model_name="goldbill", name="anh_cccd1", field=models.BinaryField(blank=True, editable=False, null=True, verbose_name="CCCD mặt trước")),
        migrations.AddField(model_name="goldbill", name="anh_cccd2", field=models.BinaryField(blank=True, editable=False, null=True, verbose_name="CCCD mặt sau")),
        migrations.AddField(model_name="goldbill", name="anh_hinh1", field=models.BinaryField(blank=True, editable=False, null=True, verbose_name="Hình 1")),
        migrations.AddField(model_name="goldbill", name="anh_hinh2", field=models.BinaryField(blank=True, editable=False, null=True, verbose_name="Hình 2")),
        migrations.AddField(model_name="goldbill", name="anh_qr", field=models.BinaryField(blank=True, editable=False, null=True, verbose_name="QR chuyển khoản")),
        migrations.CreateModel(
            name="ThauAnhTam",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("session_key", models.CharField(db_index=True, max_length=40)),
                ("slot", models.CharField(max_length=10)),
                ("data", models.BinaryField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={"db_table": "thau_anh_tam", "unique_together": {("session_key", "slot")}},
        ),
    ]
