# -*- coding: utf-8 -*-
"""Danh mục CHUYỂN KHOẢN mới thêm vào ma trận (13/09/2026) — không ai được mất quyền đang có.

Trang CHUYỂN KHOẢN trước nay chỉ cần đăng nhập. Từ lúc nó chịu ma trận, tài khoản chưa có dòng quyền
sẽ mất luôn mục trên menu. Bản vá này chép nguyên trạng quyền HÓA ĐƠN của từng tài khoản sang
CHUYỂN KHOẢN (cùng bộ 4 ô), nên quầy vẫn dùng y như hôm qua; GĐ siết hay nới sau trong trang Người dùng.
"""
from django.db import migrations


def chep_quyen(apps, schema_editor):
    Access = apps.get_model("pmv", "UserModuleAccess")
    dang_co = set(Access.objects.filter(module="CHUYEN_KHOAN").values_list("user_id", flat=True))
    moi = [Access(user_id=a.user_id, module="CHUYEN_KHOAN", can_view=a.can_view, can_edit=a.can_edit,
                  can_delete=a.can_delete, can_approve=a.can_approve, updated_by_id=a.updated_by_id)
           for a in Access.objects.filter(module="HOA_DON") if a.user_id not in dang_co]
    Access.objects.bulk_create(moi)


def go_lai(apps, schema_editor):
    apps.get_model("pmv", "UserModuleAccess").objects.filter(module="CHUYEN_KHOAN").delete()


class Migration(migrations.Migration):
    dependencies = [("pmv", "0012_chuyen_khoan_quyen")]
    operations = [migrations.RunPython(chep_quyen, go_lai)]
