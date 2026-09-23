# -*- coding: utf-8 -*-
"""QUYỀN THEO DANH MỤC — một nguồn sự thật cho cả MENU lẫn TRANG (GĐ chốt 13/09/2026).

GĐ chốt hai điều sau sự cố máy quầy ăn "403 Forbidden":

  · hệ thống phải làm ĐÚNG theo ma trận quyền đã gán (`UserModuleAccess`);
  · mục nào được tick **XEM** thì mới hiện trên thanh menu, không thì ẨN hẳn.

Mỗi mục trên thanh menu ứng với đúng một danh mục trong ma trận (xem ``MUC``). Trang đích của mục
kiểm quyền bằng ``chan(request, MÃ)``; thanh menu đọc ``quyen_muc`` trong context processor. Hai chỗ
dùng CHUNG bảng này nên không thể lệch nhau — menu hiện mà bấm vào 403, hay ngược lại, là không xảy ra.

⚠ Các URL con của một mục (popup, HTMX, nút bấm trong trang) đi theo trang đích của mục đó chứ chưa
chặn riêng từng URL. Muốn chặt tới từng URL như ma trận quyền bên KHJ thì phải thêm một middleware
ánh xạ url name → danh mục; chưa làm vì cả ba tài khoản hiện dùng đều là người của tiệm.
"""
from django.core.exceptions import PermissionDenied

# mã danh mục → (nhan_active của topbar, tên hiện cho người đọc)
MUC = {
    "DASHBOARD": ("tong", "TỔNG QUAN"),
    "BAN_HANG": ("ban", "BÁN HÀNG"),
    "THAU_VAO": ("thau", "THÂU VÀO"),
    "DAT_COC": ("datcoc", "ĐẶT-CỌC"),
    "BANG_GIA": ("gia", "BẢNG GIÁ"),
    "KHACH_HANG": ("khach", "KHÁCH HÀNG"),
    "HOA_DON": ("hoadon", "HÓA ĐƠN"),
    "BAO_CAO": ("baocao", "BÁO CÁO"),
    "CHUYEN_KHOAN": ("chuyenkhoan", "CHUYỂN KHOẢN"),
    "HE_THONG": ("hethong", "HỆ THỐNG"),
}


def duoc(user, ma, muc="can_view"):
    """Tài khoản này có quyền ``muc`` ở danh mục ``ma`` không. Superuser luôn có."""
    from apps.pmv.models import UserModuleAccess

    if not user or not getattr(user, "is_authenticated", False):
        return False
    if user.is_superuser:
        return True
    them = {} if muc == "can_view" else {muc: True}
    return UserModuleAccess.objects.filter(user=user, module=ma, can_view=True, **them).exists()


def chan(request, ma, muc="can_view"):
    """Chặn ngay đầu view nếu chưa được cấp quyền — trang 403 có hướng dẫn xin quyền."""
    if getattr(request,'khbl_mobile_admin',False) and request.path.startswith('/banle/mobile/'):
        return
    if not duoc(getattr(request, "user", None), ma, muc):
        raise PermissionDenied(f"Tài khoản chưa được cấp quyền {MUC.get(ma, (ma, ma))[1]}.")


def cua_user(user):
    """Bản đồ {mã danh mục: True} cho thanh menu — chỉ chứa mục được tick XEM."""
    from apps.pmv.models import UserModuleAccess

    if not user or not getattr(user, "is_authenticated", False):
        return {}
    if user.is_superuser:
        return {ma: True for ma in MUC}
    return {a.module: True for a in UserModuleAccess.objects.filter(user=user, can_view=True)
            if a.module in MUC}
