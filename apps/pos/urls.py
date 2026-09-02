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
    path("banle/ban-hang/vang-cu/", views.ban_mua_them, name="ban_mua_them"),
    path("banle/ban-hang/vang-cu/xoa/", views.ban_mua_xoa, name="ban_mua_xoa"),
    path("banle/ban-hang/dat/", views.ban_dat, name="ban_dat"),
    path("banle/ban-hang/bot-le/", views.ban_bot_le, name="ban_bot_le"),
    path("banle/ban-hang/moi/", views.ban_moi, name="ban_moi"),
    path("banle/ban-hang/tim-khach/", views.ban_tim_khach, name="ban_tim_khach"),
    path("banle/ban-hang/tim-hang/", views.ban_tim_hang, name="ban_tim_hang"),
    path("banle/ban-hang/phieu/", views.ban_phieu, name="ban_phieu"),
    path("banle/ban-hang/in/", views.ban_in, name="ban_in"),

    # Thâu vào
    path("banle/thau-vao/", views.thau, name="thau"),
    path("banle/thau-vao/tinh/", views.thau_tinh, name="thau_tinh"),

    # Bảng giá
    path("banle/bang-gia/", views.bang_gia, name="bang_gia"),
    path("banle/bang-gia/nhip/", views.gia_nhip, name="gia_nhip"),

    # Khách hàng
    path("banle/khach-hang/", views.khach_hang, name="khach_hang"),
    path("banle/khach-hang/them/", views.khach_form, name="khach_them"),
    path("banle/khach-hang/luu/", views.khach_luu, name="khach_luu"),
    path("banle/khach-hang/<str:cust_id>/", views.khach_chi_tiet, name="khach_chi_tiet"),
    path("banle/khach-hang/<str:cust_id>/sua/", views.khach_form, name="khach_sua"),

    # Hóa đơn
    path("banle/hoa-don/", views.hoa_don, name="hoa_don"),
    path("banle/hoa-don/nhip/", views.hoa_don_nhip, name="hoa_don_nhip"),
]
