"""Bộ lọc/thẻ template của KHBL. Glyph vẽ bằng SVG 1 nét — KHÔNG dùng emoji hệ điều hành
(emoji render khác nhau mỗi máy, màu sặc, phá bảng màu đã đo tương phản)."""
from django import template
from django.templatetags.static import static
from django.utils.safestring import mark_safe

from apps.pmv import money as M

register = template.Library()


@register.filter
def nhan_vang_thau(value):
    code = str(value or '').strip().upper()
    if code.startswith('DẺ '):
        code = code[3:].strip()
    return M.tuoi(code).replace('99.99', '9999') or '—'

_ICONS = {
    # tổng quan: 4 ô số liệu
    "tong": '<rect x="3" y="3" width="7.5" height="7.5" rx="1.5"/><rect x="13.5" y="3" width="7.5" height="7.5" rx="1.5"/><rect x="3" y="13.5" width="7.5" height="7.5" rx="1.5"/><rect x="13.5" y="13.5" width="7.5" height="7.5" rx="1.5"/>',
    # bán hàng: tem hàng
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


# Icon ẢNH cho topbar (bộ tranh vàng GĐ đưa 03/09/2026). Bản 96px trong static/img/ico/
# sinh từ ảnh gốc 1254px bằng System.Drawing — gốc GIỮ NGUYÊN trong static/img/, đừng xóa.
# Muốn sinh lại sau khi đổi ảnh: xem CLAUDE.md mục "Icon topbar".
# {% icon %} (SVG nét, ăn theo currentColor) vẫn dùng cho mọi chỗ khác trong app.
_ICON_ANH = {"tong", "ban", "thau", "gia", "khach", "hoadon", "hethong", "chuyenkhoan"}


@register.simple_tag
def icon_anh(key, size=26):
    if key not in _ICON_ANH:
        return rail_icon(key, size)
    return mark_safe(
        f'<img class="khbl-ico-anh" src="{static(f"img/ico/{key}.png")}" '
        f'width="{size}" height="{size}" alt="" aria-hidden="true" loading="lazy" decoding="async">')


@register.filter
def tien(x, suffix=" ₫"):
    """1.234.567 ₫ — số âm dùng dấu trừ thật U+2212."""
    return M.money_vn(x, suffix)


@register.filter
def tien_tran(x):
    return M.money_vn(x, "")


@register.filter
def tien_gon(x):
    """Tiền rút gọn cho biểu đồ/thẻ số: 2.155.000.000 → '2,16 tỷ' · 155.400.000 → '155 tr'."""
    d = M.dec(x)
    if d == 0:
        return ""
    am = "−" if d < 0 else ""
    d = abs(d)
    if d >= 1_000_000_000:
        return f"{am}{M._vn(d / 1_000_000_000, 2)} tỷ"
    if d >= 1_000_000:
        return f"{am}{M._vn(d / 1_000_000, 0)} tr"
    if d >= 1000:
        return f"{am}{M._vn(d / 1000, 0)} ng"
    return am + M._vn(d, 0)


@register.filter
def tl_man(w, unit="L"):
    """Trọng lượng cho màn hình: '1,05 chỉ' · '5 phân' · '8,11 g'."""
    return M.weight_screen(w, unit)


@register.filter
def tl_chi(w, unit="L"):
    """TL quy về CHỈ (L/M) hoặc GRAM (G), KHÔNG kèm chữ đơn vị — cho bảng bán hàng."""
    return M.weight_chi(w, unit)


@register.filter
def tuoi(gold_code):
    """Nhãn tuổi vàng: 18K→610, 24K→980, N9999→99.99, BK, VT."""
    return M.tuoi(gold_code)


@register.filter
def tl_giay(w, unit="L"):
    """Trọng lượng cho giấy in: '1L0C3P7Ly'."""
    return M.weight_bill(w, unit)


@register.filter
def don_gia(x):
    """Giá vàng / tiền công trong PMV lưu dạng NGHÌN (8750 = 8.750.000 ₫/chỉ).
    Nhân RATE_SCALE rồi format — đừng hiện số thô ra màn hình."""
    return M.money_vn(M.dec(x) * M.RATE_SCALE, "")


@register.filter
def nhan(a, b):
    return M.dec(a) * M.dec(b)


@register.filter
def sokhong(x):
    """Số trần để nhúng vào data-*/value= (chống localize)."""
    d = M.dec(x)
    return str(int(d)) if d == d.to_integral_value() else format(d.normalize(), "f")


@register.simple_tag
def gdb_css():
    """<style> bố cục GIẤY ĐẢM BẢO tùy chỉnh (apps/pos/gdb_layout, GĐ 08/09/2026) — nhúng trong _gdb_a5.html
    nên popup xem, trang in thẳng và trang chỉnh mẫu đều cùng một bố cục đã lưu."""
    from apps.pos import gdb_layout as GL
    return mark_safe('<style id="gdb-layout-css">' + GL.css() + "</style>")
