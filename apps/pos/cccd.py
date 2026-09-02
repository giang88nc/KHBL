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


def parse(raw):
    """Trả dict các trường đọc được (+ 'canh_bao'), hoặc None nếu không phải chuỗi CCCD.
    Chuỗi được CHUẨN HÓA trước — máy quét đi qua clipboard/Excel hay trả về chữ hỏng mã."""
    if not raw:
        return None
    sach, canh_bao = chuan_hoa(raw)
    parts = [p.strip() for p in sach.split("|")]
    if len(parts) < 6:
        return None
    cccd, cmnd, ten, sinh, gt, dia_chi = parts[0], parts[1], parts[2], parts[3], parts[4], parts[5]
    cap = parts[6] if len(parts) > 6 else ""
    if not re.fullmatch(r"\d{9,12}", cccd or ""):
        return None
    return {
        "cmnd": cccd,                      # CMND = CCCD (GĐ chốt): ô CMND lưu số CCCD 12 số
        "cmnd_cu": cmnd if re.fullmatch(r"\d{9}", cmnd or "") else "",
        "ho_ten": hoa_dau_tu(ten),
        "ngay_sinh": _ngay(sinh),
        "gioi_tinh": "1" if (gt or "").strip().lower().startswith("nam") else "0",
        "dia_chi": dia_chi,
        "ngay_cap": _ngay(cap),
        "canh_bao": canh_bao,
    }


def _ngay(s):
    """ddmmyyyy → yyyy-mm-dd (rỗng nếu không hợp lệ)."""
    s = re.sub(r"\D", "", s or "")
    if len(s) != 8:
        return ""
    try:
        return datetime.date(int(s[4:]), int(s[2:4]), int(s[:2])).isoformat()
    except ValueError:
        return ""


