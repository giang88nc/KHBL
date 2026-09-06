from django.urls import path

from . import views

app_name = "pos"

urlpatterns = [
    # Tổng quan
    path("", views.dashboard, name="dashboard"),
    path("banle/", views.dashboard, name="dashboard_banle"),

    # Bán hàng
    path("banle/ban-hang/", views.ban, name="ban"),
    path("banle/ban-hang/quet/", views.ban_quet, name="ban_quet"),
    path("banle/ban-hang/xoa/", views.ban_xoa, name="ban_xoa"),
    path("banle/ban-hang/vang-doi/", views.ban_doi_them, name="ban_doi_them"),
    path("banle/ban-hang/vang-doi/xoa/", views.ban_doi_xoa, name="ban_doi_xoa"),
    path("banle/ban-hang/vang-doi/tinh-lai/", views.ban_doi_tinh_lai, name="ban_doi_tinh_lai"),
    path("banle/ban-hang/dat/", views.ban_dat, name="ban_dat"),
    path("banle/ban-hang/qr/", views.ban_qr, name="ban_qr"),
    path("banle/ban-hang/bot-le/", views.ban_bot_le, name="ban_bot_le"),
    path("banle/ban-hang/moi/", views.ban_moi, name="ban_moi"),
    path("banle/ban-hang/tim-khach/", views.ban_tim_khach, name="ban_tim_khach"),
    path("banle/ban-hang/tim-nv/", views.ban_tim_nv, name="ban_tim_nv"),
    path("banle/ban-hang/tim-hang/", views.ban_tim_hang, name="ban_tim_hang"),
    # hóa đơn đã lưu
    path("banle/ban-hang/danh-sach/", views.ban_ds, name="ban_ds"),
    path("banle/ban-hang/mo/", views.ban_mo, name="ban_mo"),
    path("banle/ban-hang/mo-lai/", views.ban_mo_lai, name="ban_mo_lai"),
    path("banle/ban-hang/thanh-toan/", views.ban_thanh_toan, name="ban_thanh_toan"),
    path("banle/ban-hang/huy/", views.ban_huy, name="ban_huy"),
    path("banle/ban-hang/in/", views.ban_in, name="ban_in"),

    # Thâu vào
    path("banle/thau-vao/", views.thau, name="thau"),
    path("banle/thau-vao/tinh/", views.thau_tinh, name="thau_tinh"),

    # Bảng giá
    path("banle/bang-gia/", views.bang_gia, name="bang_gia"),
    path("banle/bang-gia/cap-nhat/", views.gia_cap_nhat, name="gia_cap_nhat"),
    path("banle/bang-gia/xem/", views.gia_xem_bang, name="gia_xem_bang"),
    path("banle/bang-gia/luu-png/", views.gia_luu_png, name="gia_luu_png"),
    path("banle/bang-gia/anh/<uuid:export_id>/", views.gia_tai_png, name="gia_tai_png"),
    path("banle/bang-gia/dong-bo/<uuid:batch_id>/", views.gia_dong_bo_lai, name="gia_dong_bo_lai"),
    path("banle/bang-gia/nhip/", views.gia_nhip, name="gia_nhip"),

    # Khách hàng
    path("banle/khach-hang/", views.khach_hang, name="khach_hang"),
    path("banle/khach-hang/them/", views.khach_form, name="khach_them"),
    path("banle/khach-hang/luu/", views.khach_luu, name="khach_luu"),
    path("banle/khach-hang/<str:cust_id>/anh/<str:kind>/", views.khach_anh, name="khach_anh"),
    path("banle/khach-hang/<str:cust_id>/", views.khach_chi_tiet, name="khach_chi_tiet"),
    path("banle/khach-hang/<str:cust_id>/sua/", views.khach_form, name="khach_sua"),

    # Hóa đơn
    path("banle/hoa-don/", views.hoa_don, name="hoa_don"),
    path("banle/hoa-don/xem/", views.hoa_don_chi_tiet, name="hoa_don_chi_tiet"),
    path("banle/hoa-don/xac-nhan/", views.hoa_don_xac_nhan, name="hoa_don_xac_nhan"),
    path("banle/hoa-don/huy/", views.hoa_don_huy, name="hoa_don_huy"),
]
