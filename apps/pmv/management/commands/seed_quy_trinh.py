"""
Dựng quy trình chuẩn BÁN HÀNG từ thao tác thật 05/09/2026 (apps/pmv/quy_trinh.BAN_HANG).
  manage.py seed_quy_trinh            có rồi thì thôi
  manage.py seed_quy_trinh --ghi-de   dựng lại từ đầu (xóa pha/bước hiện có, +1 version)
"""
from django.core.management.base import BaseCommand

from apps.pmv import quy_trinh as QT


class Command(BaseCommand):
    help = "Seed quy trình chuẩn BAN_HANG (idempotent)"

    def add_arguments(self, parser):
        parser.add_argument("--ghi-de", action="store_true")

    def handle(self, *args, **opts):
        p, moi = QT.seed_ban_hang(ghi_de=opts["ghi_de"])
        tk = QT.thong_ke(p)
        self.stdout.write(self.style.SUCCESS(
            f"{'ĐÃ DỰNG' if moi else 'ĐÃ CÓ'} {p.code} v{p.version}: {tk['pha']} pha · {tk['buoc']} bước "
            f"({tk['ghi']} ghi, KHBL làm {tk['lam']}, cần kiểm {tk['kiem']}, bỏ {tk['bo']}) → {QT.QUY_TRINH_DIR}\\{p.code}.md"))
