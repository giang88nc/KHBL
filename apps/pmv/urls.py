from django.urls import path

from . import views
from apps.pos.views import gdb_mau
from apps.pos.deposit_print_config import config as deposit_print_config

app_name = "pmv"

urlpatterns = [
    path("mau-in-gdb/", gdb_mau, name="gdb_mau"),
    path("mau-in-coc/", deposit_print_config, name="deposit_print_config"),
    path("", views.status, name="status"),
    path("nguoi-dung/", views.user_list, name="user_list"),
    path("nguoi-dung/them/", views.user_form, name="user_create"),
    path("nguoi-dung/<int:user_id>/", views.user_form, name="user_edit"),
    path("nguoi-dung/<int:user_id>/xoa/", views.user_delete, name="user_delete"),
    path("doi-dich/", views.doi_dich, name="doi_dich"),
    # Kho lịch sử PMV_KH2_HIST (Phase 3, 06/09/2026)
    path("kho-lich-su/", views.hist_view, name="hist"),
    path("kho-lich-su/bang/", views.hist_bang, name="hist_bang"),
    path("kho-lich-su/chay/", views.hist_action, name="hist_action"),
    path("so-sanh/", views.diff_view, name="diff"),
    path("so-sanh/chup/", views.snapshot_create, name="snapshot_create"),
    path("so-sanh/sync/", views.sync_now, name="sync_now"),
    path("so-sanh/danh-dau/truoc/", views.danh_dau_truoc, name="danh_dau_truoc"),
    path("so-sanh/danh-dau/sau/", views.danh_dau_sau, name="danh_dau_sau"),
    path("hanh-vi/", views.behavior_view, name="behavior"),
    path("hanh-vi/trace/<str:action>/", views.trace_toggle, name="trace_toggle"),
    path("hanh-vi/thu-thap/", views.collect_now, name="collect_now"),
    path("so-sanh/xoa/<int:pk>/", views.snapshot_delete, name="snapshot_delete"),
    # Quy trình — LƯU và HỌC (05/09/2026)
    path("quy-trinh/", views.quy_trinh_list, name="quy_trinh"),
    path("quy-trinh/tao/", views.qt_tao, name="qt_tao"),
    path("quy-trinh/hoc/", views.qt_hoc, name="qt_hoc"),
    path("quy-trinh/buoc/<int:pk>/sua/", views.buoc_sua, name="buoc_sua"),
    path("quy-trinh/buoc/<int:pk>/xoa/", views.buoc_xoa, name="buoc_xoa"),
    path("quy-trinh/buoc/<int:pk>/<str:huong>/", views.buoc_doi_cho, name="buoc_doi_cho"),
    path("quy-trinh/<str:code>/", views.quy_trinh_detail, name="quy_trinh_detail"),
    path("quy-trinh/<str:code>/sua/", views.qt_sua, name="qt_sua"),
    path("quy-trinh/<str:code>/xoa/", views.qt_xoa, name="qt_xoa"),
    path("quy-trinh/<str:code>/luu/", views.qt_luu, name="qt_luu"),
    path("quy-trinh/<str:code>/them-buoc/", views.buoc_them, name="buoc_them"),
]
