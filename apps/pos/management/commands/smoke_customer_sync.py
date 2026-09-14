"""Kiểm sync hai yêu cầu đồng thời trên sandbox; tự dọn đúng khách vừa tạo."""
import datetime
import re
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from django.core.management.base import BaseCommand, CommandError
from django.db import close_old_connections

from apps.pmv.client import PmvClient
from apps.pmv import sandbox
from apps.pos import customer_sync as S, customer as C
from apps.pos.customer_sync_models import CustomerSyncReceipt


class Command(BaseCommand):
    help = "Kiểm tạo khách, NULL giới tính, sổ điểm và gửi đồng thời trên sandbox; không ghi KK"

    def add_arguments(self, parser):
        parser.add_argument("--without-cccd", action="store_true", help="Kiểm nguồn không có CCCD")

    def handle(self, *args, **options):
        stamp = datetime.datetime.now().strftime("%H%M%S")
        source = {"id": 900000000000 + int(stamp), "name": "KIỂM SYNC KHBL " + stamp,
                  "phone": "099" + stamp + "8", "addr": "BẢN THỬ SYNC MỘT CHIỀU",
                  "cccd": "" if options["without_cccd"] else "999" + stamp + "888"}
        client = PmvClient("sandbox", tag="smoke_customer_sync")
        shop = client.query("SELECT TOP 1 ShopID FROM T_SHOP WITH (NOLOCK)")[0]["ShopID"]
        tables = ("I_CUSTOMER", "I_DIEMTICHLUY", "I_GIAODICH_KHACHHANG", "SHOP_CUSTOMER")

        def counts():
            return {t: client.query("SELECT COUNT(*) n FROM " + t + " WITH (NOLOCK)")[0]["n"] for t in tables}

        if S.matching_phone(client, source["phone"]) or CustomerSyncReceipt.objects.filter(target="sandbox", source_id=source["id"]).exists():
            raise CommandError("Mã kiểm thử đã có; chạy lại vào giây khác")
        before = counts()
        cust_id = ""

        def submit():
            close_old_connections()
            try:
                return S.insert_one(source["id"], S.fingerprint(source), "sandbox", None, shop)
            finally:
                close_old_connections()

        try:
            # Chỉ đổi hàm trong tiến trình kiểm thử; không đổi công tắc dùng chung máy quầy.
            with patch.object(S.gateway, "dich_hien_tai", return_value="sandbox"), \
                    patch.object(S, "source_rows", return_value=[source]), \
                    patch.object(S, "source_phone_count", return_value=1):
                with ThreadPoolExecutor(max_workers=2) as pool:
                    results = list(pool.map(lambda _: submit(), range(2)))
                cust_id = results[0]["cust_id"]
                self.ok("2 yêu cầu đồng thời = 1 khách", sum(r["created"] for r in results) == 1 and results[1]["cust_id"] == cust_id)
                saved = C._current(client, cust_id)
                self.ok("Gender NULL", saved["Gender"] is None)
                self.ok("BirthDate 1900", saved["BirthDate"] == datetime.datetime(1900, 1, 1))
                self.ok("PHONE đúng, CMND theo CCCD nguồn, hoạt động", saved["Phone"] == source["phone"] and saved["CMND"] == source["cccd"] and saved["Active"] == "1")
                self.ok("NoiCap đúng mặc định", saved["NoiCap"] == S.ISSUED_BY)
                self.ok("Sổ điểm đã có", C._has_points(client, cust_id))
                after = counts()
                self.ok("Đúng 1 I_CUSTOMER và 1 I_DIEMTICHLUY", after["I_CUSTOMER"] == before["I_CUSTOMER"] + 1 and after["I_DIEMTICHLUY"] == before["I_DIEMTICHLUY"] + 1)
                repeated = submit()
                self.ok("Gửi lại sau hoàn tất không ghi thêm", not repeated["created"] and counts() == after)
                self.stdout.write("Diff: " + str({t: after[t] - before[t] for t in tables}))
            self.stdout.write(self.style.SUCCESS("SMOKE CUSTOMER SYNC: PASS"))
        finally:
            receipt = CustomerSyncReceipt.objects.filter(target="sandbox", source_id=source["id"]).first()
            cust_id = cust_id or (receipt.cust_id if receipt else "")
            if cust_id:
                try:
                    C.delete(cust_id, client=client)
                except Exception:
                    # Sandbox có thể tắt OLE trong proc xóa ảnh: dùng khuôn dọn sandbox hiện có.
                    if not re.fullmatch(r"CU[0-9]+", cust_id):
                        raise CommandError("CustID thử không đúng khuôn; dừng dọn")
                    for table in ("I_GIAODICH_KHACHHANG", "SHOP_CUSTOMER"):
                        sql = "DELETE FROM " + table + " WHERE CustID='" + cust_id + "'"
                        sandbox.sandbox_exec_khuon(sql, re.compile(re.escape(sql) + r"\Z"))
                    for table in ("I_LICHSUTICHLUYDIEM", "T_CUSTOMER_DEBT", "I_DIEMTICHLUY", "I_CUSTOMER"):
                        sandbox.sandbox_don_dep("DELETE FROM " + table + " WHERE CustID = ?", (cust_id,))
                self.ok("Đã dọn khách thử", C._current(client, cust_id) is None)
            CustomerSyncReceipt.objects.filter(target="sandbox", source_id=source["id"]).delete()
            self.ok("Số dòng 4 bảng trở lại trước kiểm", counts() == before)

    def ok(self, label, condition):
        if not condition:
            raise CommandError("FAIL: " + label)
        self.stdout.write("PASS: " + label)
