"""
Backup KHO LỊCH SỬ PMV_KH2_HIST (instance nội bộ localhost\\SQL2014) — GĐ chốt 06/09/2026.

Quy tắc ổ đĩa: DB đang nằm ổ C (default data path) → .bak phải sang Ổ KHÁC (mặc định D:\\KHBL_BACKUP\\hist).
Lệnh tự từ chối nếu thư mục backup cùng ổ với file .mdf — GIANG hỏng 1 ổ vẫn còn bản kia.

Các bước: 1) tạo thư mục · 2) BACKUP DATABASE WITH COPY_ONLY, CHECKSUM, INIT · 3) RESTORE VERIFYONLY ·
4) giữ PMV_HIST_BACKUP_RETENTION bản mới nhất. Chạy trong job 09:30/21:30 ngay sau backup_pmv.
Chạy tay: manage.py backup_hist [--no-prune]
"""
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.pmv import gateway as G
from apps.pmv.models import PmvState


class Command(BaseCommand):
    help = "Backup COPY_ONLY kho lịch sử PMV_KH2_HIST sang ổ khác + verify + giữ N bản"

    def add_arguments(self, parser):
        parser.add_argument("--no-prune", action="store_true", help="Không dọn bản cũ")

    def handle(self, *args, **opts):
        now = timezone.localtime()
        db = settings.PMV_HIST_DB
        out_dir = Path(settings.PMV_HIST_BACKUP_DIR)

        # 0) an toàn ổ đĩa: thư mục backup PHẢI khác ổ với file dữ liệu của DB
        files = G.hist_query(
            "SELECT physical_name FROM sys.master_files WHERE database_id = DB_ID(?)", [db], database="master")
        o_db = {str(r["physical_name"])[:1].upper() for r in files}
        o_bak = str(out_dir.resolve())[:1].upper()
        if o_bak in o_db:
            raise CommandError(f"Thư mục backup {out_dir} cùng ổ {o_bak}: với DB (ổ {', '.join(sorted(o_db))}) "
                               "— đổi PMV_HIST_BACKUP_DIR sang ổ khác.")
        out_dir.mkdir(parents=True, exist_ok=True)
        fname = f"{db}_{now:%Y%m%d_%H%M}.bak"
        path = out_dir / fname
        self.stdout.write(f"[1/4] DB ở ổ {', '.join(sorted(o_db))} → backup sang ổ {o_bak}: {path}")

        # 2) backup online (kho chỉ đọc/ghi bởi sync; COPY_ONLY không phá chuỗi backup nào)
        G.hist_exec(f"BACKUP DATABASE [{db}] TO DISK = N'{path}' WITH COPY_ONLY, INIT, CHECKSUM, STATS = 25",
                    database="master", tag="backup_hist", timeout=1800)
        self.stdout.write("[2/4] Backup xong")

        # 3) verify
        G.hist_exec(f"RESTORE VERIFYONLY FROM DISK = N'{path}' WITH CHECKSUM",
                    database="master", tag="backup_hist", timeout=1800)
        rows = G.hist_query(
            "SELECT TOP 1 CAST(backup_size/1024/1024 AS INT) AS mb FROM msdb.dbo.backupset "
            "WHERE database_name = ? ORDER BY backup_finish_date DESC", [db], database="msdb")
        mb = rows[0]["mb"] if rows else "?"
        self.stdout.write(f"[3/4] VERIFY OK — {mb}MB")
        PmvState.set("hist_last_backup", f"{now:%d/%m/%Y %H:%M} | {path} | {mb}MB | VERIFY OK")

        # 4) giữ N bản mới nhất (file nằm trên chính máy này → xóa thẳng)
        if not opts["no_prune"]:
            keep = settings.PMV_HIST_BACKUP_RETENTION
            baks = sorted(out_dir.glob(f"{db}_*.bak"), key=lambda p: p.stat().st_mtime, reverse=True)
            for old in baks[keep:]:
                old.unlink()
            self.stdout.write(f"[4/4] Giữ {min(len(baks), keep)}/{keep} bản mới nhất")
        else:
            self.stdout.write("[4/4] Bỏ qua dọn dẹp (--no-prune)")
        self.stdout.write(self.style.SUCCESS("backup_hist HOÀN TẤT."))
