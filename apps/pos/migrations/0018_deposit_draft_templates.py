from django.db import migrations


def seed(apps, schema_editor):
    Template=apps.get_model('pos','DepositMessageTemplate')
    for name,body in [
        ('Hàng đã sẵn sàng · mẫu dự thảo','{store} thông báo: Đơn {bill} của {customer} đã sẵn sàng. Quý khách vui lòng liên hệ tiệm để xác nhận thời gian nhận hàng. Ngày hẹn: {promise}.'),
        ('Nhắc lịch hẹn · mẫu dự thảo','{store} xin nhắc {customer} về lịch hẹn của đơn {bill} ngày {promise}. Quý khách vui lòng liên hệ tiệm nếu cần đổi lịch.')]:
        Template.objects.get_or_create(name=name,defaults={'body':body,'status':'draft','parameter_map':{}})


class Migration(migrations.Migration):
    dependencies=[('pos','0017_deposit_operations_messages')]
    operations=[migrations.RunPython(seed,migrations.RunPython.noop)]
