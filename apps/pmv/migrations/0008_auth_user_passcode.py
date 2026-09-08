from django.db import migrations


def copy_legacy_passcodes(apps, schema_editor):
    """Chuyển mã băm từ bảng tạm unlock_passcodes vào auth_user.passcode."""
    User = apps.get_model("auth", "User")
    UnlockPasscode = apps.get_model("pos", "UnlockPasscode")
    table = schema_editor.connection.ops.quote_name(User._meta.db_table)
    with schema_editor.connection.cursor() as cursor:
        for item in UnlockPasscode.objects.exclude(hash="").iterator():
            cursor.execute(f"UPDATE {table} SET passcode = %s WHERE id = %s", [item.hash, item.user_id])


class Migration(migrations.Migration):
    dependencies = [
        ("pmv", "0007_usermoduleaccess"),
        ("pos", "0005_gold_bill"),
    ]

    operations = [
        migrations.RunSQL(
            sql="ALTER TABLE auth_user ADD COLUMN passcode VARCHAR(128) NOT NULL DEFAULT ''",
            reverse_sql="ALTER TABLE auth_user DROP COLUMN passcode",
        ),
        migrations.RunPython(copy_legacy_passcodes, migrations.RunPython.noop),
    ]
