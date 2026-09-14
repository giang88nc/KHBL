from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('pos', '0021_deposit_order_photos')]
    operations = [
        migrations.AddField(model_name='depositorderstate', name='extra_notes', field=models.JSONField(default=list)),
        migrations.AddField(model_name='depositorderstate', name='cancellation_policy', field=models.CharField(max_length=10, blank=True)),
    ]
