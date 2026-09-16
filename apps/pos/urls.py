from django.urls import path

from . import views
from . import deposits
from . import deposit_operations
from . import deposit_messages
from . import deposit_money
from . import deposit_photos
from . import deposit_editing
from . import bao_cao
from . import qr_learning_views
from . import customer_camera
from . import customer_sync
from . import thau_list, thau_payments, thau_xuat
from . import views_thau as VT, transfers, dashboard_alerts, bank_reconcile
from . import money_flow_views

app_name = "pos"

urlpatterns = [
    path("banle/check-money-flow/", money_flow_views.listing, name="money_flow"),
    path('banle/bao-cao/', bao_cao.trang, name='bao_cao'),        # BÁO CÁO (12/09/2026)
    path('banle/dat-coc/', deposits.index, name='dat_coc'),
    path('banle/dat-coc/bao-cao/', deposits.report, name='dat_coc_report'),
    path('banle/dat-coc/thong-bao/soan/', deposit_messages.compose, name='dat_coc_compose'),
    path('banle/dat-coc/thong-bao/goi-y/<str:kind>/', deposit_messages.suggestion_edit, name='dat_coc_suggestion'),
    path('banle/dat-coc/thong-bao/mau/them/', deposit_messages.template_edit, name='dat_coc_template_add'),
    path('banle/dat-coc/thong-bao/mau/<int:pk>/', deposit_messages.template_edit, name='dat_coc_template'),
    path('banle/dat-coc/thong-bao/mau/<int:pk>/kiem-tra/', deposit_messages.approve_template, name='dat_coc_template_approve'),
    path('banle/dat-coc/thong-bao/<int:pk>/<str:kind>/', deposit_messages.edit, name='dat_coc_message'),
    path('banle/dat-coc/khach/', deposits.customers, name='dat_coc_customers'),
    path('banle/dat-coc/hang-trong-kho/', deposits.products, name='dat_coc_products'),
    path('banle/dat-coc/them/', deposits.popup, {'action': 'add'}, name='dat_coc_add'),
    path('banle/dat-coc/<str:pk>/anh/<str:slot>/', deposit_photos.photo, name='dat_coc_photo'),
    path('banle/dat-coc/<str:pk>/hinh-anh/', deposit_photos.edit, name='dat_coc_photos_edit'),
    path('banle/dat-coc/<str:pk>/mo-sua/', deposit_editing.unlock, name='dat_coc_unlock'),
    path('banle/dat-coc/<str:pk>/van-hanh/<str:kind>/', deposit_operations.action, name='dat_coc_operation'),
    path('banle/dat-coc/<str:pk>/tien/<str:kind>/', deposit_money.action, name='dat_coc_money'),
    path('banle/dat-coc/<str:pk>/can-coc/', deposit_money.apply_to_invoice, name='dat_coc_apply'),
    path('banle/dat-coc/<str:pk>/xoa/', deposits.xoa, name='dat_coc_xoa'),        # 🗑 XÓA + Passcode (11/09)
    path('banle/dat-coc/<str:pk>/<str:action>/', deposits.popup, name='dat_coc_popup'),
    path("banle/khach-hang/qr/cong-cu/", qr_learning_views.tool, name="qr_learning"),
    path("banle/khach-hang/qr/hoc/dich/", qr_learning_views.translate, name="qr_learning_translate"),
    path("banle/khach-hang/qr/hoc/loc/", qr_learning_views.filter_errors, name="qr_learning_filter"),
    path("banle/khach-hang/qr/hoc/luu-nhap/", qr_learning_views.save_draft, name="qr_learning_save_draft"),
    path("banle/khach-hang/qr/hoc/xem-thu/", qr_learning_views.preview, name="qr_learning_preview"),
    path("banle/khach-hang/qr/hoc/ap-dung/", qr_learning_views.save, name="qr_learning_save"),
    path("banle/thau-vao-2/", thau_list.listing, name="thau_vao_2"),
    path("banle/thau-vao-2/xem/", thau_list.detail, name="thau_vao_2_xem"),
    path("banle/thau-vao-2/doi-soat/", thau_payments.action, name="thau_vao_2_payment"),
    path("banle/thau-vao-2/xuat-ncc/", thau_xuat.xuat_ncc, name="thau_vao_2_xuat_ncc"),      # ⬇ Excel danh mục NCC (10/09)
    path("banle/thau-vao-2/in-cccd/", thau_xuat.in_cccd, name="thau_vao_2_in_cccd"),         # 🪪 Word ảnh CCCD cỡ thật
    path("banle/chuyen-khoan/doi-soat/", bank_reconcile.reconcile_view, name="chuyen_khoan_doi_soat"),
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
    path("banle/ban-hang/coc/", views.ban_coc, name="ban_coc"),          # 💸 áp phiếu ĐẶT-CỌC (11/09/2026)
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
    path("banle/thau-vao/anh/len/", VT.thau_anh_len, name="thau_anh_len"),
    path("banle/thau-vao/anh/xoa/", VT.thau_anh_xoa, name="thau_anh_xoa"),
    path("banle/thau-vao/anh/", VT.thau_anh, name="thau_anh"),
    path("banle/thau-vao/qr/quet/", VT.thau_qr_quet, name="thau_qr_quet"),
    path("banle/thau-vao/qr/tao/", VT.thau_qr_tao, name="thau_qr_tao"),
    path("banle/thau-vao/qr/luu/", VT.thau_qr_luu, name="thau_qr_luu"),
    path("banle/thau-vao/anh/cat/", VT.thau_anh_cat, name="thau_anh_cat"),
    path("banle/thau-vao/anh/cat/luu/", VT.thau_anh_cat_luu, name="thau_anh_cat_luu"),

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
    path("banle/khach-hang/sync/", customer_sync.listing, name="customer_sync_list"),
    path("banle/khach-hang/sync/them/", customer_sync.insert, name="customer_sync_insert"),
    path("banle/khach-hang/chup-hinh/tach-cccd/", customer_camera.split_card, name="customer_camera_card"),
    path("banle/khach-hang/them/", views.khach_form, name="khach_them"),
    path("banle/khach-hang/luu/", views.khach_luu, name="khach_luu"),
    path("banle/khach-hang/kiem-sdt/", views.khach_kiem_sdt, name="khach_kiem_sdt"),
    path("banle/khach-hang/qr/phan-tich/", views.khach_qr_phan_tich, name="khach_qr_phan_tich"),
    path("banle/khach-hang/anh/cat/", VT.khach_anh_cat, name="khach_anh_cat"),            # ✂ popup khách (10/09) — tái dùng AC.cat_cccd
    path("banle/khach-hang/anh/cat/luu/", VT.khach_anh_cat_luu, name="khach_anh_cat_luu"),
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
