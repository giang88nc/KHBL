from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [("pos", "0010_thau_nhom_ck")]

    operations = [
        migrations.AddField(model_name="thaunhom", name="ck_ten", field=models.CharField(blank=True, default="", max_length=100, verbose_name="Tên chủ TK")),
    ]
