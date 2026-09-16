# -*- coding: utf-8 -*-
"""
Trang cấu hình MẪU IN GIẤY CẦM ĐỒ (GCD) — /he-thong/mau-in-gcd/ (GĐ chốt 15/09/2026).

Đây là NƠI GHI DUY NHẤT của 3 khoá trong `khj_bl.pmv_state`: `gcd_layout` (bố cục tờ giấy),
`may_in_ds` (sổ máy in khai tay) và `gcd_may_in` (MÁY IN THẬT đang chọn — tìm/chọn/bỏ chọn ở 2
endpoint riêng cuối tệp, KHÔNG đi chung POST bố cục; xem lý do tách khoá trong gcd_may_in.py).
Việc IN THẬT nằm bên KHCD (https://localhost:8200/camdo/lap-phieu) — KHCD chỉ SELECT chéo DB,
không bao giờ ghi ngược. Trang này KHÔNG truy vấn `khj_cd` một dòng nào: bản xem trước dùng
**DỮ LIỆU MẪU CỨNG** bên dưới (giữ đúng một chiều KHBL → KHCD).

Vì sao xem trước NGAY TẠI KHBL chứ không nhúng iframe trang in KHCD (biến thể CỌC): KHCD đặt
`X-Frame-Options: DENY` + `frame-ancestors 'none'` cho MỌI phản hồi. Muốn nhúng phải hạ hai header
bảo mật của một endpoint KHCD cho origin khác — bề mặt tấn công mới, đổi lấy một tiện ích xem trước.
Không đáng. ⇒ dùng biến thể GĐB (include thẳng partial `pos/_gcd_a5.html`).

QUYỀN: theo khuôn trang mẫu-in CỌC — GET đòi `can_view` HE_THONG, POST đòi `can_edit`.
Tài khoản chỉ có XEM (máy quầy `kimhanh2` hiện đúng như vậy) vẫn MỞ ĐƯỢC trang, nhưng nút LƯU /
MẶC ĐỊNH bị ẩn và có dải báo — máy quầy chạy Edge `--app --kiosk-printing` KHÔNG CÓ NÚT BACK,
gặp 403 là phải tắt cửa sổ.
"""
import datetime as dt
import json
import logging

from django.core.exceptions import PermissionDenied
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_http_methods

from . import gcd_layout as L
from . import gcd_may_in as MI
from . import ma_vach as MV
from . import quyen as Q

logger = logging.getLogger(__name__)

# ── DỮ LIỆU MẪU CỨNG ──────────────────────────────────────────────────────────────────────────
# Cột trái: phiếu bình thường. Cột phải (?dai=1): TRƯỜNG HỢP DÀI NHẤT độ dài mô phỏng theo phiếu thật, nhưng MỌI giá trị đều là số giả
# (địa chỉ 65 ký tự, nhiều món gộp một dòng, 550.000.000) — để GĐ nhìn thấy cảnh tràn TRƯỚC khi in,
# bản xem trước dùng dữ liệu ngắn thì không bao giờ lộ.
MAU_THUONG = {
    "sku": "CD26090100012",
    "khach_ten": "Nguyễn Thị Kim Anh",
    "khach_diachi": "1276 Kha Vạn Cân, P. Linh Trung, TP. Thủ Đức",
    "mon_hang": "[N9999] Lắc tay trơn 3.25c + [18K] Nhẫn đá 1.10c",
    "so_tien": 25_000_000,
    "so_tien_chu": "Hai mươi lăm triệu đồng",
    "ky_han": 30,
    "opened": dt.date(2026, 9, 1),
    "due": dt.date(2026, 10, 1),
    "nhan_vien": "Nguyễn Văn Mẫu",
    "trang_thai": "ĐANG CẦM",
    "giay_to": "CCCD 079188001234",
    "lai": "3,0 %/tháng",
    "tu": "Tủ 24K",
    "phone": "0901 234 567",
    "ghi_chu": "Khách quen, hẹn đóng lời hằng tháng.",
}
MAU_DAI = {
    **MAU_THUONG,
    "khach_ten": "Nguyễn Thị Hoàng Bảo Trâm Anh",
    "khach_diachi": "Số 1276/45B Kha Vạn Cân, Khu phố 3, P. Linh Trung, TP. Thủ Đức, TP. HCM",
    "mon_hang": ("[N9999] Lắc tay trơn 3.25c · [18K] Nhẫn đá xanh 1.10c · [N9999] Dây chuyền 5.40c"
                 " · [610] Bông tai 0.85c · [SJC] Nhẫn tròn trơn 2.00c"),
    "so_tien": 550_000_000,
    "so_tien_chu": "Năm trăm năm mươi triệu đồng",
}


def _tien_vn(n):
    return "{:,.0f}".format(int(n)).replace(",", ".") + " ₫"


def _ngay(d):
    return d.strftime("%d/%m/%Y")


def _du_lieu_mau(dai=False):
    """Bản đồ key khối → chuỗi in ra + class co chữ, dựng y như KHCD sẽ dựng lúc in thật."""
    m = MAU_DAI if dai else MAU_THUONG
    gia_tri = {
        "so_cuong_1": m["sku"],
        "so_cuong_2": m["sku"],
        "cuong_chi_tiet": "\n".join([m["mon_hang"], "Lãi: " + m["lai"], m["tu"],
                                     "ĐT: " + m["phone"], m["ghi_chu"]]),
        "ma_phieu": m["sku"],
        "khach_ten": m["khach_ten"],
        "khach_diachi": m["khach_diachi"],
        "mon_hang": m["mon_hang"],
        "so_tien_so": _tien_vn(m["so_tien"]),
        "so_tien_chu": m["so_tien_chu"],
        "ky_han": str(m["ky_han"]),
        "ngay_lap": _ngay(m["opened"]),
        "ngay_hen": _ngay(m["due"]),
        "nam": str(m["due"].year),
        "nhan_vien": m["nhan_vien"],
        "khach_ky": m["khach_ten"],
        "trang_thai": m["trang_thai"],
        "giay_to": m["giay_to"],
        # MÃ VẠCH: text = ĐÚNG chuỗi số máy quét sẽ đọc ra (CD26090100012 → 26090100012), dùng luôn
        # làm `alt` để người xem đối chiếu được. Dựng bằng cùng hàm KHCD dùng lúc in ⇒ xem trước
        # không nói dối.
        "ma_phieu_vach": MV.so_ma_vach(m["sku"]),
    }
    o = []
    for b in L.BLOCKS:
        v = gia_tri.get(b["key"], "")
        o.append({"key": b["key"], "ten": b["ten"], "text": v, "cls": b["sel"].lstrip("."),
                  "dong": v.split("\n") if b["key"] == "cuong_chi_tiet" else [],
                  # ẢNH THẬT, không phải ô trống: có vẽ ra vạch thì GĐ mới thấy được mã vạch có bị
                  # bóp quá hẹp / đè lên chữ in sẵn hay không TRƯỚC khi đem in.
                  "anh": MV.anh_ma_vach(v) if b["key"] == "ma_phieu_vach" else "",
                  "co": L.lop_co(b["key"], v), "dai": L.qua_dai(b["key"], v)})
    return o


@require_http_methods(["GET", "POST"])
def config(request):
    muc = "can_edit" if request.method == "POST" else "can_view"
    Q.chan(request, "HE_THONG", muc)

    if request.method == "POST":
        try:
            data = json.loads(request.body or b"{}")
            if not isinstance(data, dict):
                raise ValueError("JSON không hợp lệ.")
        except (ValueError, TypeError) as exc:
            return JsonResponse({"ok": False, "loi": str(exc)}, status=400)
        dat_lai = bool(data.get("reset"))
        # ⚠ POST thiếu khoá "layout" mà vẫn ghi = XOÁ SẠCH BỐ CỤC KHÔNG HỎI: save({}) ghi chuỗi
        # rỗng vào pmv_state, hệ quay về mặc định, công căn chỉnh từng milimet mất trắng.
        # Chỉ "reset": true mới được phép xoá; mọi trường hợp khác PHẢI có layout là dict.
        if not dat_lai and not isinstance(data.get("layout"), dict):
            return JsonResponse({"ok": False, "loi": "Thiếu bố cục. Muốn về mặc định thì gửi reset=true."},
                                status=400)
        layout = L.save(None) if dat_lai else L.save(data["layout"])
        may_in = L.may_in_save(None) if dat_lai else (
            L.may_in_save(data["may_in"]) if isinstance(data.get("may_in"), dict) else L.may_in_load())
        logger.info("gcd_layout: %s %s", request.user.username, "reset" if dat_lai else "save")
        return JsonResponse({"ok": True, "layout": layout, "may_in": may_in, "css": L.css(layout)})

    layout = L.load()
    dai = request.GET.get("dai") == "1"
    sua = Q.duoc(request.user, "HE_THONG", "can_edit")
    # CẢNH BÁO BỀ RỘNG MÃ VẠCH ngay tại ĐÂY — nơi Giám đốc kéo-thả căn chỉnh. Trước đây cảnh báo
    # chỉ có ở trang in bên KHCD, tức là chỉ thấy SAU khi đã in ra giấy: căn hẹp quá thì mãi tới
    # lúc cầm tờ giấy quét không ra mới biết. Đo theo ĐÚNG mã mẫu đang hiện trên bản xem trước.
    _v = (layout.get("ma_phieu_vach") or L.mac_dinh()["ma_phieu_vach"])
    # Bề ngang tờ THẬT (mm) — không lấy hằng 210: GĐ đo lại tờ in sẵn rồi sửa kho_w thì vạch hẹp đi
    # theo, cảnh báo phải chạy trên số đang dùng chứ không phải số mặc định.
    _giay_w = (layout.get(L.IN_KEY) or {}).get("kho_w") or L.PAPER_W_MM
    _ma_mau = next((k["text"] for k in _du_lieu_mau(dai) if k["key"] == "ma_phieu_vach"), "")
    _muc, _cau = MV.canh_bao_vach(_ma_mau, _v.get("w") or 0, _giay_w)
    return render(request, "pos/gcd_mau.html", {
        "nav_active": "hethong",
        "vach_muc": _muc, "vach_cau": _cau,
        # JS tính lại NGAY khi kéo-thả / gõ số (chưa Lưu đã thấy) — đưa sang 3 tham số dạng CHUỖI.
        # ⚠ Số thực để Django render thẳng sẽ bị bản địa hoá thành "0,19" rồi parseFloat cắt còn 0.
        "vach_so": _ma_mau,
        "vach_thu": "%g" % MV.VACH_HEP_CAN_THU_MM,
        "vach_min": "%g" % MV.VACH_HEP_TOI_THIEU_MM,
        "blocks": L.BLOCKS,
        "khoi_mau": _du_lieu_mau(dai),
        "dai": dai,
        "duoc_sua": sua,
        # json_script tự tuần tự hoá — truyền OBJECT, đừng truyền chuỗi JSON (sẽ bị bọc 2 lớp)
        "layout": layout,
        "mac_dinh": L.mac_dinh(),
        "may_in": L.may_in_load(),
        "may_in_mac_dinh": L.may_in_mac_dinh(),
        # MÁY IN THẬT (khoá riêng gcd_may_in) — tách hẳn khỏi bố cục, xem gcd_may_in.py
        "may_in_that": MI.load(),
        "may_in_tinh_trang": MI.tinh_trang(),
        "sel": {b["key"]: b["sel"] for b in L.BLOCKS},
        "kho": L.IN_KHO,
        "kho_to": L.KHO_TO,
        "cach_in": L.MAY_IN_CACH,
        "cach_chay_that": list(L.MAY_IN_CHAY_THAT),
        "gcd_css": L.css(layout),
    })


# ── MÁY IN THẬT: TÌM · CHỌN · BỎ CHỌN ─────────────────────────────────────────────────────────
# Hai endpoint RIÊNG, KHÔNG đi chung POST của bố cục: chọn máy in không bao giờ được có cơ hội ghi
# vào khoá `gcd_layout` (công căn chỉnh từng milimet của GĐ nằm ở đó). Cả hai đòi quyền SỬA —
# `tim` có chạy lệnh hệ điều hành nên tài khoản máy quầy chỉ-XEM không được phép gọi.
@require_http_methods(["POST"])
def may_in_tim(request):
    """Hỏi Windows danh sách máy in. Không có máy in nào = danh sách rỗng + câu giải thích, KHÔNG lỗi."""
    Q.chan(request, "HE_THONG", "can_edit")
    kq = MI.tim()
    kq["dang_chon"] = MI.load()
    kq["tinh_trang"] = MI.tinh_trang()
    logger.info("gcd_may_in: %s tim -> %d máy in", request.user.username, len(kq["ds"]))
    return JsonResponse(kq)


@require_http_methods(["POST"])
def may_in_luu(request):
    """Lưu máy in đang chọn. `bo_chon: true` hoặc ten rỗng = BỎ CHỌN (luôn dùng hộp thoại trình duyệt).

    ⚠ POST thiếu hẳn khoá `may_in` mà vẫn ghi = âm thầm xoá lựa chọn — cùng cái bẫy đã xử ở POST bố
    cục. Muốn bỏ chọn thì phải nói thẳng `bo_chon: true`.
    """
    Q.chan(request, "HE_THONG", "can_edit")
    try:
        data = json.loads(request.body or b"{}")
        if not isinstance(data, dict):
            raise ValueError("JSON không hợp lệ.")
    except (ValueError, TypeError) as exc:
        return JsonResponse({"ok": False, "loi": str(exc)}, status=400)
    bo = bool(data.get("bo_chon"))
    if not bo and not isinstance(data.get("may_in"), dict):
        return JsonResponse({"ok": False, "loi": "Thiếu máy in. Muốn bỏ chọn thì gửi bo_chon=true."},
                            status=400)
    chon = MI.save(None if bo else data["may_in"], boi=request.user.username)
    logger.info("gcd_may_in: %s %s %r", request.user.username, "bo_chon" if bo else "chon",
                chon.get("ten"))
    return JsonResponse({"ok": True, "may_in": chon, "tinh_trang": MI.tinh_trang()})
