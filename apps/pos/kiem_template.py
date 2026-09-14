# -*- coding: utf-8 -*-
"""CHỐT CHẶN CHÚ THÍCH TEMPLATE — chạy như một "system check" của Django.

GĐ chốt 11/09/2026: chú thích gom ở ĐẦU tệp bằng ``{% comment %}…{% endcomment %}``. Lý do:
``{# … #}`` chỉ là chú thích khi gọn trong **một dòng**; viết nhiều dòng thì Django in NGUYÊN VĂN ra
trang cho khách đọc. Lỗi này đã tái đi tái lại (trang Nghỉ phép KHJ hiện suốt 28/08→11/09; khối
TÍNH TỔNG màn bán 11/09) vì bộ dò cũ nằm lẫn trong `smoke_thau` — ai sửa màn khác thì không chạy tới.

Nay nó là **check của Django**: mọi `manage.py <lệnh>` đều chạy qua, không cần nhớ, không né được.
Muốn dò tay: ``manage.py check``. Mã lỗi ``khbl.E001``.
"""
from pathlib import Path

from django.conf import settings
from django.core.checks import Error, register

MO, DONG = "{#", "#}"


def _thu_muc():
    """Thư mục template của dự án (bỏ qua template trong thư viện ngoài)."""
    goc = Path(settings.BASE_DIR)
    for cau_hinh in settings.TEMPLATES:
        for d in cau_hinh.get("DIRS", []):
            d = Path(d)
            if d.is_dir():
                yield goc, d


def cho_hong(noi_dung):
    """Trả về danh sách SỐ DÒNG có `{#` mà không đóng ngay trong dòng đó."""
    ra = []
    for i, dong in enumerate(noi_dung.splitlines(), 1):
        tim = 0
        while True:
            mo = dong.find(MO, tim)
            if mo < 0:
                break
            dong_dong = dong.find(DONG, mo)
            if dong_dong < 0:            # mở ở dòng này, đóng ở dòng khác → Django in ra trang
                ra.append(i)
                break
            tim = dong_dong + len(DONG)
    return ra


@register()
def kiem_chu_thich_template(app_configs, **kwargs):
    loi = []
    for goc, thu_muc in _thu_muc():
        for tep in sorted(thu_muc.rglob("*.html")):
            try:
                noi = tep.read_text(encoding="utf-8")
            except OSError:
                continue
            for dong in cho_hong(noi):
                try:
                    ten = tep.relative_to(goc)
                except ValueError:
                    ten = tep
                loi.append(Error(
                    f"{ten}:{dong} — chú thích {{# #}} bắc qua nhiều dòng: Django sẽ IN NGUYÊN VĂN "
                    f"ra trang cho khách thấy.",
                    hint="Gom chú thích về đầu tệp trong {% comment %}…{% endcomment %}, "
                         "hoặc viết gọn {# … #} trong đúng một dòng.",
                    id="khbl.E001"))
    return loi
