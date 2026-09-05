"""
Tiện ích nhật ký hành vi PMV ra file (Bước 1, GĐ duyệt 05/09/2026).

  manage.py pmv_log xuat-lai [YYYY-MM-DD]   dựng lại toàn bộ file .log của 1 ngày từ MySQL
                                             (mặc định hôm nay) — dùng khi file mất/hỏng
  manage.py pmv_log don                      dọn ngay: file >90 ngày, DB >30 ngày
  manage.py pmv_log thu-muc                  in đường dẫn thư mục log
"""
from datetime import date

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.pmv import behavior_log as BL


class Command(BaseCommand):
    help = "Nhật ký hành vi PMV ra file: xuat-lai [ngày] | don | thu-muc"

    def add_arguments(self, parser):
        parser.add_argument("action", choices=["xuat-lai", "don", "thu-muc"])
        parser.add_argument("ngay", nargs="?", help="YYYY-MM-DD (cho xuat-lai)")

    def handle(self, *args, **opts):
        if opts["action"] == "thu-muc":
            self.stdout.write(str(BL.LOG_DIR))
            return
        if opts["action"] == "don":
            xoa = BL.don_file()
            n1, n2 = BL.don_db()
            self.stdout.write(self.style.SUCCESS(
                f"Đã dọn: {len(xoa)} thư mục ngày ({', '.join(xoa) or 'không có'}); "
                f"DB xóa {n1} hành vi + {n2} thay đổi cũ hơn {BL.GIU_DB_NGAY} ngày"))
            return
        try:
            ngay = date.fromisoformat(opts["ngay"]) if opts["ngay"] else timezone.localdate()
        except ValueError:
            raise CommandError("Ngày phải dạng YYYY-MM-DD")
        n, m = BL.xuat_lai(ngay)
        self.stdout.write(self.style.SUCCESS(
            f"Đã dựng lại {BL.thu_muc_ngay(ngay)}: {n} dòng hành vi + {m} dòng thay đổi"))
