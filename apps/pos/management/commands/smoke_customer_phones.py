"""Kiểm ba SĐT trên sandbox, gồm hai worker tranh cùng một số phụ; tự dọn khách thử."""
import datetime
import re
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from django.core.management.base import BaseCommand, CommandError
from django.db import close_old_connections
from apps.pmv.client import PmvClient
from apps.pmv import sandbox
from apps.pos import customer as C, customer_phones as P, services as S


class Command(BaseCommand):
    help = "Kiểm ghi/tìm ba SĐT và chống trùng đồng thời trên sandbox; không ghi KK"

    def handle(self, *args, **options):
        stamp = datetime.datetime.now().strftime("%H%M%S")
        numbers = ["09" + str(i) + stamp + "6" for i in range(1, 8)]
        client = PmvClient("sandbox", tag="smoke_customer_phones")
        tables = ("I_CUSTOMER", "I_DIEMTICHLUY", "SHOP_CUSTOMER", "I_GIAODICH_KHACHHANG")
        ids = []

        def counts():
            return {t: client.query("SELECT COUNT(*) n FROM " + t + " WITH (NOLOCK)")[0]["n"] for t in tables}

        before = counts()
        for phone in numbers:
            if client.query("SELECT CustID FROM I_CUSTOMER WITH (NOLOCK) WHERE " + P.exact_sql(), (phone,) * 3):
                raise CommandError("SĐT thử đã có, chạy lại vào giây khác")
        data, errors, _ = C.clean_form({"CustName": "Kiểm Ba Số " + stamp, "Gender": "1", "Phone": numbers[0],
                                       "GhiChu2": numbers[1], "GhiChu3": numbers[2], "BirthDate": "1900-01-01"})
        self.ok("Dữ liệu thử hợp lệ", not errors)
        try:
            result = C.upsert(data, {}, client=client, on_created=ids.append)
            cid = result["cust_id"]
            saved = C._current(client, cid)
            self.ok("INSERT lưu đủ ba cột + sổ điểm", [saved[k] for k in P.COLUMNS] == numbers[:3] and C._has_points(client, cid))
            with patch.object(S, "client", return_value=client):
                for phone in numbers[:3]:
                    rows, total, pages = S.khach_loc(key=phone)
                    self.ok("Tìm số chính/phụ trả một khách", total == 1 and len(rows) == 1 and rows[0]["CustID"] == cid)
                    self.ok("Gợi ý bán/thâu tìm được số phụ", S.tim_khach(phone)[0]["CustID"] == cid)
            for key in P.KEYS:
                for phone in numbers[:3]:
                    attempt = {**data, "phone": "", "phone2": "", "phone3": "", key: phone}
                    try:
                        C.upsert(attempt, {}, client=client, on_created=ids.append)
                    except C.CustomerSaveError as exc:
                        self.ok("Chặn trùng chéo cột", phone in str(exc))
                    else:
                        raise CommandError("Lọt SĐT trùng")
            updated = {**data, "cust_id": cid, "phone2": numbers[3]}
            C.upsert(updated, {}, client=client)
            self.ok("UPDATE đúng ô, giữ hai số khác", [C._current(client, cid)[k] for k in P.COLUMNS] == [numbers[0], numbers[3], numbers[2]])
            C.upsert({**updated, "phone2": ""}, {}, client=client)
            self.ok("Xóa số phụ có chủ đích", C._current(client, cid)["GhiChu2"] == "")

            def compete(i):
                close_old_connections()
                try:
                    candidate = {**data, "name": "Kiểm Đồng Thời " + stamp + str(i), "phone": numbers[4 + i],
                                 "phone2": numbers[6], "phone3": ""}
                    try:
                        C.upsert(candidate, {}, client=PmvClient("sandbox", tag="smoke_phone_race"), on_created=ids.append)
                        return "created"
                    except C.CustomerSaveError as exc:
                        if numbers[6] not in str(exc): raise
                        return "blocked"
                finally:
                    close_old_connections()
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(compete, (0, 1)))
            self.ok("Hai worker cùng SĐT phụ: một thành công, một bị chặn", sorted(results) == ["blocked", "created"])
            after = counts()
            self.ok("Chỉ thêm hai hồ sơ và hai sổ điểm", after["I_CUSTOMER"] == before["I_CUSTOMER"] + 2 and after["I_DIEMTICHLUY"] == before["I_DIEMTICHLUY"] + 2)
            self.stdout.write("Diff: " + str({t: after[t] - before[t] for t in tables}))
        finally:
            for cid in ids:
                try:
                    C.delete(cid, client=client)
                except Exception:
                    if not re.fullmatch(r"CU[0-9]+", cid): raise
                    for table in ("I_GIAODICH_KHACHHANG", "SHOP_CUSTOMER"):
                        sql = "DELETE FROM " + table + " WHERE CustID='" + cid + "'"
                        sandbox.sandbox_exec_khuon(sql, re.compile(re.escape(sql) + r"\Z"))
                    for table in ("I_LICHSUTICHLUYDIEM", "T_CUSTOMER_DEBT", "I_DIEMTICHLUY", "I_CUSTOMER"):
                        sandbox.sandbox_don_dep("DELETE FROM " + table + " WHERE CustID = ?", (cid,))
            self.ok("Đã dọn khách thử, số dòng trở lại ban đầu", counts() == before)
        self.stdout.write(self.style.SUCCESS("SMOKE CUSTOMER PHONES: PASS"))

    def ok(self, label, condition):
        if not condition: raise CommandError("FAIL: " + label)
        self.stdout.write("PASS: " + label)
