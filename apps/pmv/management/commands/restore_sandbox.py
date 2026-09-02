"""
Restore sandbox PMV_SANDBOX từ file .bak (mặc định: bản MỚI NHẤT trong
PMV_BACKUP_DIR_LOCAL; hoặc --file <đường dẫn>). GĐ chốt 02/09/2026: KHÔNG share
mạng PC KK — .bak chép tay về máy Mr Giang khi cần refresh sandbox.

Chạy: manage.py restore_sandbox [--file D:\\duong\\dan.bak]
"""
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.pmv.models import PmvState
from apps.pmv.sandbox import restore_sandbox_from, sandbox_query


class Command(BaseCommand):
    help = "Restore PMV_SANDBOX trên SQL Express local từ file .bak"

    def add_arguments(self, parser):
        parser.add_argument("--file", help="File .bak cụ thể (mặc định: bản mới nhất trong PMV_BACKUP_DIR_LOCAL)")

    def handle(self, *args, **opts):
        if not settings.PMV_LOCAL_MSSQL:
            raise CommandError("Chưa khai PMV_LOCAL_MSSQL trong .env (vd localhost\\SQL2014).")
        if opts["file"]:
            bak = Path(opts["file"])
        else:
            local_dir = Path(settings.PMV_BACKUP_DIR_LOCAL)
            baks = sorted(local_dir.glob("*.bak"), key=lambda p: p.stat().st_mtime, reverse=True)
            if not baks:
                raise CommandError(f"Không có file .bak nào trong {local_dir} — chép bản backup từ PC KK về đó trước.")
            bak = baks[0]
        self.stdout.write(f"Restore [{settings.PMV_SANDBOX_DB}] trên {settings.PMV_LOCAL_MSSQL} từ: {bak}")
        restore_sandbox_from(bak)

        rows = sandbox_query(
            "SELECT (SELECT COUNT(*) FROM T_EMPLOYEE) AS nv, "
            "(SELECT COUNT(*) FROM TRN_RT_BUYSELL) AS hd, "
            "(SELECT COUNT(*) FROM T_PRODUCT) AS sp"
        )[0]
        now = timezone.localtime()
        PmvState.set(
            "pmv_last_sandbox",
            f"{now:%d/%m/%Y %H:%M} | {bak.name} | NV={rows['nv']} HĐ={rows['hd']} SP={rows['sp']}",
        )
        self.stdout.write(self.style.SUCCESS(
            f"SANDBOX OK — T_EMPLOYEE={rows['nv']}, TRN_RT_BUYSELL={rows['hd']}, T_PRODUCT={rows['sp']}"
        ))
