"""
Đọc chuỗi QR trên thẻ CCCD gắn chip (máy quét hoạt động như bàn phím, gõ thẳng vào ô nhập).

Khuôn 7 trường ngăn bằng dấu |:
    số CCCD | số CMND cũ | họ tên | ngày sinh ddmmyyyy | giới tính | địa chỉ | ngày cấp ddmmyyyy
Ví dụ: 012345678901|123456789|Nguyễn Văn A|07041988|Nam|Khóm 4, TT. Năm Căn, Cà Mau|15092023

Bản Python này dùng cho phía máy chủ; bản JS trong static/js/khbl.js điền tức thì lúc quét.
"""
import datetime
import re

from .vn_text import chuan_hoa, hoa_dau_tu


def phan_tich(raw):
    """Tách trường trước khi sửa chữ; không biến đổi CCCD, CMND cũ và ngày tháng."""
    loi, canh_bao, thieu = [], [], []
    raw = str(raw or "")
    parts = [p.strip() for p in raw.strip().split("|")]
    result = {"data": None, "loi": loi, "canh_bao": canh_bao, "thieu": thieu}
    if len(raw) > 8192 or len(parts) != 7:
        loi.append("Chuỗi quét cần đúng 7 trường ngăn bằng dấu |. Hãy quét lại đầy đủ.")
        return result
    cccd, cmnd, ten, sinh, gt, dia_chi, cap = parts
    if not re.fullmatch(r"[0-9]{12}", cccd):
        loi.append("Số CCCD trong QR phải gồm đúng 12 chữ số.")
    if cmnd and not re.fullmatch(r"[0-9]{9}", cmnd):
        loi.append("Số CMND cũ phải gồm 9 chữ số hoặc để trống.")

    def text(value, label):
        value, warnings = chuan_hoa(value)
        canh_bao.extend(f"{label}: {w}" for w in warnings)
        if re.search(r"[\x00-\x1f\x7f<>|]", value):
            loi.append(f"{label} chứa ký tự không hợp lệ.")
        if re.search(r"�|\?|&#|%[0-9a-f]{2}|0(?:19[5-9]|2[0-4][0-9])", value, re.I):
            canh_bao.append(f"{label}: còn nội dung cần kiểm tra lại với thẻ.")
        return value

    ten = hoa_dau_tu(text(ten, "Họ tên"))
    dia_chi = text(dia_chi, "Địa chỉ")
    if not ten:
        loi.append("QR thiếu họ tên khách.")
    ngay_sinh, ngay_cap = _ngay(sinh), _ngay(cap)
    if sinh and not ngay_sinh:
        loi.append("Ngày sinh không hợp lệ (cần ddmmyyyy và ngày có thật).")
    if cap and not ngay_cap:
        loi.append("Ngày cấp không hợp lệ (cần ddmmyyyy và ngày có thật).")
    if ngay_sinh and ngay_cap and ngay_cap < ngay_sinh:
        loi.append("Ngày cấp không được trước ngày sinh.")
    gt = text(gt, "Giới tính").lower()
    gioi_tinh = "1" if gt == "nam" else ("0" if gt in ("nữ", "nu") else "")
    for label, value in (("Ngày sinh", ngay_sinh), ("Ngày cấp", ngay_cap),
                         ("Địa chỉ", dia_chi), ("Giới tính", gioi_tinh)):
        if not value:
            thieu.append(label)
    if thieu:
        canh_bao.append("Chưa xác định: " + ", ".join(thieu) + ". Kiểm tra và bổ sung từ thẻ.")
    if not loi:
        result["data"] = {"cmnd": cccd, "cmnd_cu": cmnd, "ho_ten": ten,
                          "ngay_sinh": ngay_sinh, "gioi_tinh": gioi_tinh,
                          "dia_chi": dia_chi, "ngay_cap": ngay_cap, "canh_bao": canh_bao}
    return result


def parse(raw):
    """Giữ giao diện cũ: dữ liệu đã kiểm tra, hoặc None nếu chuỗi không hợp lệ."""
    return phan_tich(raw)["data"]


def _ngay(s):
    """ddmmyyyy → yyyy-mm-dd (rỗng nếu không hợp lệ)."""
    if not re.fullmatch(r"[0-9]{8}", s or ""):
        return ""
    try:
        return datetime.date(int(s[4:]), int(s[2:4]), int(s[:2])).isoformat()
    except ValueError:
        return ""

