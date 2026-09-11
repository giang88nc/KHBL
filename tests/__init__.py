# -*- coding: utf-8 -*-
"""CHỐT CHẶN AN TOÀN cho bộ kiểm KHBL (dựng 11/09/2026 sau sự cố mất dữ liệu).

Sự cố: chạy `python -m unittest discover -s tests` (hoặc bất cứ cách nào KHÔNG qua `manage.py test`)
làm Django không dựng DB kiểm riêng, nên tới bước dọn dẹp `TransactionTestCase` chạy `DELETE FROM`
**thẳng trên DB THẬT** — 11/09/2026 xóa sạch 355.918 dòng của `khj_bl` (phiếu thâu + ảnh, tài khoản,
ma trận quyền…). Khôi phục được nhờ nhật ký nhị phân ROW, nhưng đừng để tái diễn.

Chốt chặn: trước khi một `TransactionTestCase` dọn dẹp, kiểm tên DB đang nối. Không phải DB kiểm
(`test_…`) thì DỪNG NGAY, không cho lệnh xóa chạy. Cách chạy đúng: `manage.py test <đường dẫn>`.
"""
try:                                    # đặt chốt chặn khi nào Django nạp được, không thì thôi
    from django.db import connections
    from django.test import TransactionTestCase
except Exception:                       # pragma: no cover - môi trường chưa có Django
    pass
else:
    _don_dep_goc = TransactionTestCase._fixture_teardown

    def _don_dep_co_chot(self):
        for ten_noi in (getattr(self, "databases", None) or ["default"]):
            ten_db = str(connections[ten_noi].settings_dict.get("NAME") or "")
            if not ten_db.startswith("test_"):
                raise RuntimeError(
                    f"DỪNG: {type(self).__name__} sắp XÓA SẠCH bảng của DB THẬT '{ten_db}'. "
                    "Bộ kiểm phải chạy qua `manage.py test`, để Django dựng DB kiểm riêng.")
        return _don_dep_goc(self)

    TransactionTestCase._fixture_teardown = _don_dep_co_chot
