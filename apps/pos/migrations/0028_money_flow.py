# Generated for the read-only unified money-flow ledger (phase 1).
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('pos', '0027_customer_bridge_receipt')]

    operations = [
        migrations.CreateModel(
            name='MoneyFlow',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('direction', models.CharField(choices=[('IN', 'Khách trả'), ('OUT', 'Trả khách')], db_index=True, max_length=3)),
                ('service', models.CharField(db_index=True, max_length=20)),
                ('source_system', models.CharField(default='KHBL', max_length=12)),
                ('source_type', models.CharField(max_length=30)),
                ('source_id', models.CharField(max_length=64)),
                ('source_group_id', models.CharField(blank=True, default='', max_length=64)),
                ('source_bill_code', models.CharField(blank=True, db_index=True, default='', max_length=40)),
                ('source_trn_ids', models.JSONField(blank=True, default=list)),
                ('flow_role', models.CharField(default='settlement', max_length=20)),
                ('customer_id', models.CharField(blank=True, default='', max_length=30)),
                ('customer_name', models.CharField(blank=True, default='', max_length=200)),
                ('expected_amount', models.DecimalField(decimal_places=3, default=0, max_digits=18)),
                ('cash_amount', models.DecimalField(decimal_places=3, default=0, max_digits=18)),
                ('bank_amount', models.DecimalField(decimal_places=3, default=0, max_digits=18)),
                ('business_date', models.DateField(db_index=True)),
                ('source_status', models.CharField(blank=True, default='', max_length=20)),
                ('payment_status', models.CharField(choices=[('waiting', 'Chờ ghi nhận'), ('recorded', 'Nguồn đã ghi'), ('partial', 'Khớp một phần'), ('confirmed', 'Đã xác minh'), ('review', 'Cần kiểm tra'), ('void', 'Không còn hiệu lực')], db_index=True, default='waiting', max_length=20)),
                ('is_void', models.BooleanField(default=False)),
                ('source_snapshot', models.JSONField(blank=True, default=dict)),
                ('synced_at', models.DateTimeField(auto_now=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
            ],
            options={'db_table': 'money_flow', 'ordering': ['-business_date', '-id']},
        ),
        migrations.AddConstraint(
            model_name='moneyflow',
            constraint=models.UniqueConstraint(fields=('source_system', 'service', 'source_type', 'source_id', 'flow_role'), name='money_flow_source_role'),
        ),
        migrations.AddIndex(model_name='moneyflow', index=models.Index(fields=['direction', 'business_date'], name='money_flow_dir_day')),
        migrations.AddIndex(model_name='moneyflow', index=models.Index(fields=['service', 'business_date'], name='money_flow_svc_day')),
    ]
