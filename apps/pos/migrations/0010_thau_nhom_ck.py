from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [("pos", "0009_merge_0007_thau_anh_0008_bank_reconcile_index")]

    operations = [
        migrations.AddField(model_name="thaunhom", name="ck_bank", field=models.CharField(blank=True, default="", max_length=12)),
        migrations.AddField(model_name="thaunhom", name="ck_stk", field=models.CharField(blank=True, default="", max_length=40)),
        migrations.AddField(model_name="thaunhom", name="ck_nd", field=models.CharField(blank=True, default="", max_length=60)),
    ]
