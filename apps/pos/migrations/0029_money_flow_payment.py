# Generated for standardized IN/OUT payment instructions (phase 2).
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("pos", "0028_money_flow")]

    operations = [
        migrations.CreateModel(
            name="MoneyFlowPayment",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("method", models.CharField(choices=[("CASH", "Tiền mặt"), ("BANK", "Chuyển khoản")], max_length=8)),
                ("amount", models.DecimalField(decimal_places=3, default=0, max_digits=18)),
                ("bank_code", models.CharField(blank=True, default="", max_length=20)),
                ("bank_name", models.CharField(blank=True, default="", max_length=120)),
                ("bank_account", models.CharField(blank=True, default="", max_length=40)),
                ("bank_owner", models.CharField(blank=True, default="", max_length=160)),
                ("transfer_content", models.CharField(blank=True, default="", max_length=100)),
                ("qr_payload", models.TextField(blank=True, default="")),
                ("status", models.CharField(choices=[("ready", "Sẵn sàng thanh toán"), ("cancelled", "Đã hủy")], db_index=True, default="ready", max_length=12)),
                ("created_by", models.CharField(blank=True, default="", max_length=150)),
                ("updated_by", models.CharField(blank=True, default="", max_length=150)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("flow", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="payment_instructions", to="pos.moneyflow")),
            ],
            options={"db_table": "money_flow_payment", "ordering": ["-updated_at", "-id"]},
        ),
        migrations.AddConstraint(
            model_name="moneyflowpayment",
            constraint=models.UniqueConstraint(fields=("flow", "method"), name="money_flow_payment_method"),
        ),
    ]
