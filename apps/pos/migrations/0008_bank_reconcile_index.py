from django.db import migrations


def add_index(apps, schema_editor):
    if schema_editor.connection.vendor == 'mysql':
        # Bảng webhook có sẵn, chỉ thêm index phục vụ tập out/chưa kiểm/ngày.
        with schema_editor.connection.cursor() as cur:
            cur.execute("CREATE INDEX khbl_bank_reconcile_lookup ON bank_notifications "
                        "(direction, is_check, transaction_time)")


def remove_index(apps, schema_editor):
    if schema_editor.connection.vendor == 'mysql':
        with schema_editor.connection.cursor() as cur:
            cur.execute('DROP INDEX khbl_bank_reconcile_lookup ON bank_notifications')


class Migration(migrations.Migration):
    dependencies = [('pos', '0007_bank_reconcile_state')]
    operations = [migrations.RunPython(add_index, remove_index)]
