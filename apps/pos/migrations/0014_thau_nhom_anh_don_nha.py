"""Dọn ảnh Hình 1 · Hình 2 · QR từ gold_bill(TrnID đầu của nhóm).anh_* sang "nhà mới" thau_nhom.anh_* (GĐ chốt 09/09/2026:
giữ nguyên cột ở gold_bill, chỉ chép dữ liệu). CCCD KHÔNG chép — thuộc hồ sơ khách trên máy KK."""
from django.db import migrations


def don_sang_nhom(apps, schema_editor):
    ThauNhom = apps.get_model("pos", "ThauNhom")
    GoldBill = apps.get_model("pos", "GoldBill")
    n = 0
    for nhom in ThauNhom.objects.all().only("id", "trn_ids"):
        ids = list(nhom.trn_ids or [])
        if not ids:
            continue
        gb = GoldBill.objects.filter(trn_id__in=ids).exclude(anh_hinh1=None, anh_hinh2=None, anh_qr=None).first()
        if not gb:
            continue
        ThauNhom.objects.filter(pk=nhom.pk).update(anh_hinh1=gb.anh_hinh1, anh_hinh2=gb.anh_hinh2, anh_qr=gb.anh_qr)
        n += 1
    print(f" thau_nhom: đã chép ảnh cho {n} nhóm")


class Migration(migrations.Migration):
    dependencies = [("pos", "0013_thau_nhom_anh")]
    operations = [migrations.RunPython(don_sang_nhom, migrations.RunPython.noop)]
