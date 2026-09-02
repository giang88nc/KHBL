from django.urls import path

from . import views

app_name = "pos"

urlpatterns = [
    path("", views.ban, name="ban"),
    path("ban/quet/", views.ban_quet, name="ban_quet"),
    path("ban/xoa/", views.ban_xoa, name="ban_xoa"),
    path("ban/vang-cu/", views.ban_mua_them, name="ban_mua_them"),
    path("ban/vang-cu/xoa/", views.ban_mua_xoa, name="ban_mua_xoa"),
    path("ban/dat/", views.ban_dat, name="ban_dat"),
    path("ban/bot-le/", views.ban_bot_le, name="ban_bot_le"),
    path("ban/moi/", views.ban_moi, name="ban_moi"),
    path("ban/tim-khach/", views.ban_tim_khach, name="ban_tim_khach"),
    path("ban/tim-hang/", views.ban_tim_hang, name="ban_tim_hang"),
    path("ban/phieu/", views.ban_phieu, name="ban_phieu"),
    path("ban/in/", views.ban_in, name="ban_in"),

    path("bang-gia/", views.bang_gia, name="bang_gia"),
    path("gia/nhip/", views.gia_nhip, name="gia_nhip"),

    path("khach-hang/", views.khach_hang, name="khach_hang"),
    path("khach-hang/<str:cust_id>/", views.khach_chi_tiet, name="khach_chi_tiet"),

    path("thau/", views.thau, name="thau"),
    path("thau/tinh/", views.thau_tinh, name="thau_tinh"),

    path("hoa-don/", views.hoa_don, name="hoa_don"),
    path("hoa-don/nhip/", views.hoa_don_nhip, name="hoa_don_nhip"),
]
