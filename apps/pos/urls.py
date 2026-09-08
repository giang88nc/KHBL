from django.urls import path

from . import views
from . import views_thau as VT, transfers, dashboard_alerts

app_name = "pos"

urlpatterns = [
    path("banle/canh-bao/<str:key>/", dashboard_alerts.detail_view, name="dashboard_alert_detail"),
    path("banle/chuyen-khoan/", transfers.transfers, name="chuyen_khoan"),
    path("banle/ngan-hang/", transfers.bank_list, name="bank_list"),
    path("banle/ngan-hang/them/", transfers.bank_edit, name="bank_add"),
    path("banle/ngan-hang/<int:bank_id>/sua/", transfers.bank_edit, name="bank_edit"),
    path("banle/ngan-hang/<int:bank_id>/xoa/", transfers.bank_delete, name="bank_delete"),
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
    path("banle/ban-hang/moi/", views.ban_moi, name="ban_moi"),
    path("banle/ban-hang/tim-khach/", views.ban_tim_khach, name="ban_tim_khach"),
    path("banle/ban-hang/tim-nv/", views.ban_tim_nv, name="ban_tim_nv"),
    path("banle/ban-hang/tim-hang/", views.ban_tim_hang, name="ban_tim_hang"),
    # hóa đơn đã lưu
    path("banle/ban-hang/danh-sach/", views.ban_ds, name="ban_ds"),
    path("banle/ban-hang/mo/", views.ban_mo, name="ban_mo"),
    # đơn ĐÃ CHỐT: popup xác nhận chung + passcode (sua | huy_tt | huy_hd) — 07/09/2026
    path("banle/ban-hang/xac-nhan/<str:hanh_dong>/", views.ban_xac_nhan, name="ban_xac_nhan"),
    path("banle/ban-hang/thuc-hien/<str:hanh_dong>/", views.ban_thuc_hien, name="ban_thuc_hien"),
    path("tai-khoan/passcode/", views.passcode_form, name="passcode_form"),
    path("tai-khoan/passcode/luu/", views.passcode_save, name="passcode_save"),
    path("banle/ban-hang/thanh-toan/", views.ban_thanh_toan, name="ban_thanh_toan"),
    # 08/09 chiều dọn code chết: bỏ bot-le · mo-lai · mo-khoa · in/ (in_phieu.html); XÓA nháp đi qua thuc-hien/xoa_nhap
    path("banle/ban-hang/huy/", views.ban_huy, name="ban_huy"),
    path("banle/ban-hang/in/dem/", views.ban_in_dem, name="ban_in_dem"),
    # 🖨 IN trong popup GĐB: in thẳng từ cửa sổ bán + đóng popup + phiếu trắng (08/09/2026 chiều)
    path("banle/ban-hang/in/thang/", views.ban_in_thang, name="ban_in_thang"),

    # Thâu vào
    # THÂU VÀO — cùng khung màn bán (08/09/2026 tối, views_thau.py): giỏ session nhiều dòng · popup DS · passcode
    path("banle/thau-vao/", VT.thau, name="thau"),
    path("banle/thau-vao/moi/", VT.thau_moi, name="thau_moi"),
    path("banle/thau-vao/dat/", VT.thau_dat, name="thau_dat"),
    path("banle/thau-vao/them/", VT.thau_them, name="thau_them"),
    path("banle/thau-vao/xoa-dong/", VT.thau_xoa_dong, name="thau_xoa_dong"),
    path("banle/thau-vao/tinh-lai/", VT.thau_tinh_lai, name="thau_tinh_lai"),
    path("banle/thau-vao/tim/", VT.thau_tim, name="thau_tim"),
    path("banle/thau-vao/danh-sach/", VT.thau_ds, name="thau_ds"),
    path("banle/thau-vao/mo/", VT.thau_mo, name="thau_mo"),
    path("banle/thau-vao/thanh-toan/", VT.thau_thanh_toan, name="thau_thanh_toan"),
    path("banle/thau-vao/xac-nhan/<str:hanh_dong>/", VT.thau_xac_nhan, name="thau_xac_nhan"),
    path("banle/thau-vao/thuc-hien/<str:hanh_dong>/", VT.thau_thuc_hien, name="thau_thuc_hien"),
    path("banle/thau-vao/in/", VT.thau_in, name="thau_in"),

    # Bảng giá
    path("banle/bang-gia/", views.bang_gia, name="bang_gia"),
    path("banle/bang-gia/cap-nhat/", views.gia_cap_nhat, name="gia_cap_nhat"),
    path("banle/bang-gia/xem/", views.gia_xem_bang, name="gia_xem_bang"),
    path("banle/bang-gia/luu-png/", views.gia_luu_png, name="gia_luu_png"),
    path("banle/bang-gia/anh/<uuid:export_id>/", views.gia_tai_png, name="gia_tai_png"),
    path("banle/bang-gia/dong-bo/<uuid:batch_id>/", views.gia_dong_bo_lai, name="gia_dong_bo_lai"),
    path("banle/bang-gia/sync-pmv-report/", views.gia_sync_pmv_report_xem, name="gia_sync_pmv_report_xem"),
    path("banle/bang-gia/sync-pmv-report/ap-dung/", views.gia_sync_pmv_report_ap_dung, name="gia_sync_pmv_report_ap_dung"),
    path("banle/bang-gia/pmv-report-trang-thai/", views.gia_pmv_report_trang_thai, name="gia_pmv_report_trang_thai"),
    path("banle/bang-gia/sync-kk/", views.gia_sync_kk_xem, name="gia_sync_kk_xem"),
    path("banle/bang-gia/sync-kk/ap-dung/", views.gia_sync_kk_ap_dung, name="gia_sync_kk_ap_dung"),
    path("banle/bang-gia/kk-trang-thai/", views.gia_kk_trang_thai, name="gia_kk_trang_thai"),
    path("banle/bang-gia/pmv-report-canh-bao/", views.gia_pmv_report_canh_bao, name="gia_pmv_report_canh_bao"),
    path("banle/bang-gia/nhip/", views.gia_nhip, name="gia_nhip"),

    # Khách hàng
    path("banle/khach-hang/", views.khach_hang, name="khach_hang"),
    path("banle/khach-hang/them/", views.khach_form, name="khach_them"),
    path("banle/khach-hang/luu/", views.khach_luu, name="khach_luu"),
    path("banle/khach-hang/<str:cust_id>/anh/<str:kind>/", views.khach_anh, name="khach_anh"),
    path("banle/khach-hang/<str:cust_id>/xoa/", views.khach_xoa_xac_nhan, name="khach_xoa_xn"),
    path("banle/khach-hang/<str:cust_id>/xoa/thuc-hien/", views.khach_xoa, name="khach_xoa"),
    path("banle/khach-hang/<str:cust_id>/", views.khach_chi_tiet, name="khach_chi_tiet"),
    path("banle/khach-hang/<str:cust_id>/sua/", views.khach_form, name="khach_sua"),

    # Hóa đơn
    path("banle/hoa-don/", views.hoa_don, name="hoa_don"),
    path("banle/hoa-don/xem/", views.hoa_don_chi_tiet, name="hoa_don_chi_tiet"),
    path("banle/hoa-don/xac-nhan/", views.hoa_don_xac_nhan, name="hoa_don_xac_nhan"),
    path("banle/hoa-don/huy/", views.hoa_don_huy, name="hoa_don_huy"),
    # Mẫu in GIẤY ĐẢM BẢO tùy chỉnh (kéo-thả khối, cỡ chữ pt) — 08/09/2026
    path("banle/giay-dam-bao/mau/", views.gdb_mau, name="gdb_mau"),
]
