"""Đồng bộ ảnh hồ sơ khách từ PMV KK sang kho local KHBL (chỉ đọc PMV)."""
from django.core.management.base import BaseCommand

from apps.pmv.client import PmvClient
from apps.pos import customer as C


class Command(BaseCommand):
    help = "Đồng bộ CCCD/ảnh đại diện PMV KK vào media/cccd với tên CustID_ten_MT|MS|DD.jpg"

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=0, help="Chỉ xử lý tối đa N ảnh")
        parser.add_argument("--force", action="store_true", help="Nén/ghi lại cả tệp local đang hợp lệ")

    def handle(self, *args, **options):
        client = PmvClient("kk", tag="khbl_image_sync")
        rows = client.query(
            "SELECT CustID, CustName, ImagePath, ImagePathMatTruoc, ImagePathMatSau "
            "FROM I_CUSTOMER WITH (NOLOCK) "
            "WHERE ISNULL(ImagePath,'')<>'' OR ISNULL(ImagePathMatTruoc,'')<>'' "
            "OR ISNULL(ImagePathMatSau,'')<>'' ORDER BY CustID")
        slots = (("dai-dien", "ImagePath"), ("mat-truoc", "ImagePathMatTruoc"),
                 ("mat-sau", "ImagePathMatSau"))
        done = skipped = failed = 0
        limit = max(0, options["limit"])
        for row in rows:
            for kind, field in slots:
                path = row.get(field)
                if not path:
                    continue
                if limit and done + skipped + failed >= limit:
                    self.stdout.write(f"Dừng ở giới hạn {limit} ảnh.")
                    self.stdout.write(self.style.SUCCESS(f"Xong: {done} ghi, {skipped} đã có, {failed} lỗi."))
                    return
                if not options["force"] and C._archive_current(row, kind):
                    skipped += 1
                    continue
                try:
                    data, _ = C._validated_image(client, path)
                    C._archive_one(row, kind, data)
                    done += 1
                except Exception as exc:
                    failed += 1
                    self.stderr.write(f"{row.get('CustID')} {kind}: {C.error_message(exc)}")
                if (done + skipped + failed) % 25 == 0:
                    self.stdout.write(f"Đã xử lý {done + skipped + failed} ảnh: {done} ghi, {skipped} đã có, {failed} lỗi.")
        self.stdout.write(self.style.SUCCESS(f"Xong: {done} ghi, {skipped} đã có, {failed} lỗi."))
