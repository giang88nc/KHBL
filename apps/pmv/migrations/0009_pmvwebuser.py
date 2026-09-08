from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def copy_legacy_pmv_links(apps, schema_editor):
    PmvUser = apps.get_model("pmv", "PmvUser")
    PmvWebUser = apps.get_model("pmv", "PmvWebUser")
    for pmv_user in PmvUser.objects.exclude(django_user_id=None).iterator():
        PmvWebUser.objects.get_or_create(web_user_id=pmv_user.django_user_id, defaults={"pmv_user_id": pmv_user.pk})


class Migration(migrations.Migration):
    dependencies = [
        ("pmv", "0008_auth_user_passcode"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="PmvWebUser",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("pmv_user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="web_mappings", to="pmv.pmvuser")),
                ("updated_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="+", to=settings.AUTH_USER_MODEL)),
                ("web_user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="pmv_mappings", to=settings.AUTH_USER_MODEL)),
            ],
            options={"verbose_name": "Liên kết User web — PMV", "verbose_name_plural": "Liên kết User web — PMV", "db_table": "pmv_web_users"},
        ),
        migrations.AddConstraint(
            model_name="pmvwebuser",
            constraint=models.UniqueConstraint(fields=("web_user",), name="uq_pmv_web_user"),
        ),
        migrations.RunPython(copy_legacy_pmv_links, migrations.RunPython.noop),
    ]
