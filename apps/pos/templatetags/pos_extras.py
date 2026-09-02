"""Bộ lọc/thẻ template của KHBL. Glyph vẽ bằng SVG 1 nét — KHÔNG dùng emoji hệ điều hành
(emoji render khác nhau mỗi máy, màu sặc, phá bảng màu đã đo tương phản)."""
from django import template
from django.utils.safestring import mark_safe

from apps.pmv import money as M

register = template.Library()

_ICONS = {
    # mua bán: tem hàng
    "ban": '<path d="M20.6 13.4 12 4.8H4.8V12l8.6 8.6a2 2 0 0 0 2.8 0l4.4-4.4a2 2 0 0 0 0-2.8Z"/><circle cx="8.5" cy="8.5" r="1.3"/>',
    # thâu vào: mũi tên vào két
    "thau": '<path d="M12 3v10"/><path d="m8 9 4 4 4-4"/><path d="M4 15v4a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-4"/>',
    # bảng giá: bảng điện
    "gia": '<rect x="3" y="4" width="18" height="14" rx="2"/><path d="M7 9h4M7 13h7M16 9h1"/>',
    # khách hàng
    "khach": '<circle cx="12" cy="8" r="3.4"/><path d="M4.5 20a7.5 7.5 0 0 1 15 0"/>',
    # hóa đơn
    "hoadon": '<path d="M6 3h12v18l-3-2-3 2-3-2-3 2Z"/><path d="M9 8h6M9 12h6"/>',
    # hệ thống
    "hethong": '<circle cx="12" cy="12" r="3"/><path d="M12 2v3M12 19v3M2 12h3M19 12h3M4.9 4.9l2.1 2.1M17 17l2.1 2.1M19.1 4.9 17 7M7 17l-2.1 2.1"/>',
    "tem": '<path d="M20.6 13.4 12 4.8H4.8V12l8.6 8.6a2 2 0 0 0 2.8 0l4.4-4.4a2 2 0 0 0 0-2.8Z"/><circle cx="8.5" cy="8.5" r="1.3"/>',
}


@register.simple_tag
def rail_icon(key, size=20):
    body = _ICONS.get(key, _ICONS["hethong"])
    return mark_safe(
        f'<svg class="khbl-ico" width="{size}" height="{size}" viewBox="0 0 24 24" fill="none" '
        f'stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" '
        f'aria-hidden="true">{body}</svg>')


@register.simple_tag
def icon(key, size=18):
    return rail_icon(key, size)


@register.filter
def tien(x, suffix=" ₫"):
    """1.234.567 ₫ — số âm dùng dấu trừ thật U+2212."""
    return M.money_vn(x, suffix)


@register.filter
def tien_tran(x):
    return M.money_vn(x, "")


@register.filter
def tl_man(w, unit="L"):
    """Trọng lượng cho màn hình: '1,05 chỉ' · '5 phân' · '8,11 g'."""
    return M.weight_screen(w, unit)


@register.filter
def tl_giay(w, unit="L"):
    """Trọng lượng cho giấy in: '1L0C3P7Ly'."""
    return M.weight_bill(w, unit)


@register.filter
def nhan(a, b):
    return M.dec(a) * M.dec(b)


@register.filter
def sokhong(x):
    """Số trần để nhúng vào data-*/value= (chống localize)."""
    d = M.dec(x)
    return str(int(d)) if d == d.to_integral_value() else format(d.normalize(), "f")
