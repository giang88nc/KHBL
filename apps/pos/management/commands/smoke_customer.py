"""Kiểm thử UPSERT khách và ảnh trên sandbox; tuyệt đối không ghi máy KK."""
import datetime
from io import BytesIO
from pathlib import Path

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management.base import BaseCommand, CommandError
from PIL import Image, ImageDraw

from apps.pmv.client import PmvClient
from apps.pmv import sandbox
from apps.pos import customer


class Command(BaseCommand):
    help = "Tạo/sửa khách + điểm + ảnh trên sandbox, kiểm chứng rồi tự dọn"

    def handle(self, *args, **options):
        now = datetime.datetime.now()
        stamp = now.strftime("%H%M%S")
        phone = "098" + stamp + "7"
        cust_id, paths = "", []
        client = PmvClient("sandbox", tag="smoke_customer")
        data = {
            "cust_id": "", "name": f"KIỂM THỬ UPSERT KHBL {stamp}", "phone": phone,
            "cmnd": "", "address": "Bản thử", "birth": "01/01/1900", "gender": "1",
            "issued": "", "issued_by": "", "email": "", "notes": "tự dọn",
            "cust_type": "", "active": "1",
        }
        try:
            upload = self._image()
            images, info = customer.prepare_images({"anh_dai_dien": upload})
            with customer.SAVE_LOCK:
                result = customer.upsert(data, images, client=client)
            self.stdout.write(f"  warnings={result.get('warnings') or []}")
            cust_id = result["cust_id"]
            row = client.query(
                "SELECT CustName, Phone, Address, ImagePath FROM I_CUSTOMER WITH (NOLOCK) WHERE CustID=?",
                (cust_id,))[0]
            paths = [row.get("ImagePath")]
            self.stdout.write(f"  image={row.get('ImagePath') or '(trống)'}")
            self._ok("tạo khách + đọc lại", row["Phone"] == phone and row["Address"] == "Bản thử")
            self._ok("mở sổ điểm", bool(client.query(
                "SELECT CustID FROM I_DIEMTICHLUY WITH (NOLOCK) WHERE CustID=?", (cust_id,))))
            self._ok("tên tệp ảnh theo tên khách",
                     bool(row.get("ImagePath")) and Path(row["ImagePath"]).name.startswith("KIEM_THU_UPSERT_KHBL"))
            self._ok("ảnh JPEG đã được ghi", bool(row.get("ImagePath")) and Path(row["ImagePath"]).is_file())
            self._ok("ảnh JPEG nằm trong ngưỡng dung lượng", info[0]["bytes"] <= 650_000)
            self._ok("phát hiện trùng điện thoại", bool(customer.duplicate_errors(client, data)))

            data["cust_id"] = cust_id
            data["address"] = "Bản thử đã sửa"
            with customer.SAVE_LOCK:
                updated = customer.upsert(data, {}, client=client)
            check = client.query("SELECT Address FROM I_CUSTOMER WITH (NOLOCK) WHERE CustID=?",
                                 (cust_id,))[0]
            self._ok("cập nhật đúng khách, không tạo dòng mới",
                     not updated["created"] and check["Address"] == "Bản thử đã sửa")
            self.stdout.write(self.style.SUCCESS("SMOKE CUSTOMER: PASS"))
        finally:
            for raw in paths:
                if raw:
                    path = Path(raw)
                    try:
                        path.resolve().relative_to(Path(r"D:\PHANMEMVANG\HINHANHKH").resolve())
                        path.unlink(missing_ok=True)
                    except (ValueError, OSError):
                        pass
            if cust_id:
                for table in ("I_LICHSUTICHLUYDIEM", "T_CUSTOMER_DEBT", "I_DIEMTICHLUY", "I_CUSTOMER"):
                    sandbox.sandbox_don_dep(f"DELETE FROM {table} WHERE CustID = ?", (cust_id,))

    @staticmethod
    def _image():
        out = BytesIO()
        image = Image.new("RGB", (2400, 1600), "white")
        draw = ImageDraw.Draw(image)
        for y in range(0, 1600, 80):
            draw.line((0, y, 2400, 1600 - y), fill=(90, 60, 30), width=5)
        image.save(out, "PNG")
        raw = out.getvalue()
        return SimpleUploadedFile("smoke.png", raw, content_type="image/png")

    @staticmethod
    def _ok(label, condition):
        if not condition:
            raise CommandError(f"FAIL: {label}")
