"""Giữ nguyên dữ liệu giá; bảo đảm lưu phiên bản và nhật ký có thể rollback."""
from django.db import migrations


def make_transactional(apps, schema_editor):
    if schema_editor.connection.vendor != 'mysql':
        return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("SELECT ENGINE FROM information_schema.TABLES "
                       "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='gold_prices'")
        row = cursor.fetchone()
        if row and row[0].lower() != 'innodb':
            cursor.execute('ALTER TABLE gold_prices ENGINE=InnoDB')


class Migration(migrations.Migration):
    atomic = False
    dependencies = [('pos', '0022_deposit_edit_progress')]
    operations = [migrations.RunPython(make_transactional, migrations.RunPython.noop)]
