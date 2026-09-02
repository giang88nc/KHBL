"""
Backup DB PMV_BANLE_KH2 trên PC KK (GĐ chốt 02/09/2026: "máy KK tự backup").

Dùng WITH COPY_ONLY để KHÔNG phá chuỗi backup sẵn có của vendor (họ backup tay
vào D:\\PHANMEMVANG\\BACKUP — bản gần nhất 04/06/2026).

Các bước (tolerant — bước sau lỗi không hủy kết quả bước trước):
  1) Tạo thư mục PMV_BACKUP_DIR_KK trên Ổ ĐĨA PC KK (xp_create_subdir, idempotent)
  2) BACKUP DATABASE ... WITH COPY_ONLY, CHECKSUM, INIT
  3) RESTORE VERIFYONLY kiểm tra bản backup đọc lại được
  4) Dọn file .bak cũ quá PMV_BACKUP_RETENTION_KK ngày (xp_delete_file)
  5) PMV_BACKUP_SHARE có khai báo → copy .bak về PMV_BACKUP_DIR_LOCAL (giữ N bản)
  6) PMV_LOCAL_MSSQL có khai báo → RESTORE vào SQL Express local thành PMV_SANDBOX

Job đêm 02:00 (config/scheduler.py). Chạy tay: manage.py backup_pmv [--no-prune]
"""
import shutil
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.pmv.gateway import pmv_admin, pmv_read
from apps.pmv.models import PmvState


class Command(BaseCommand):
    help = "Backup COPY_ONLY DB PMV trên PC KK + verify + dọn bản cũ (+ kéo về/restore sandbox nếu cấu hình)"

    def add_arguments(self, parser):
        parser.add_argument("--no-prune", action="store_true", help="Không dọn file .bak cũ")

    def handle(self, *args, **opts):
        now = timezone.localtime()
        kk_dir = settings.PMV_BACKUP_DIR_KK.rstrip("\\")
        fname = f"PMV_BANLE_KH2_{now:%Y%m%d_%H%M}.bak"
        kk_path = f"{kk_dir}\\{fname}"

        # 1) thư mục trên PC KK (idempotent — có sẵn thì thôi)
        pmv_admin(f"EXEC master.dbo.xp_create_subdir N'{kk_dir}'", tag="backup_pmv")
        self.stdout.write(f"[1/6] Thư mục PC KK sẵn sàng: {kk_dir}")

        # 2) backup COPY_ONLY (online, không chặn bán hàng; ~vài chục giây với DB 428MB)
        pmv_admin(
            f"BACKUP DATABASE [PMV_BANLE_KH2] TO DISK = N'{kk_path}' "
            "WITH COPY_ONLY, INIT, CHECKSUM, STATS = 25",
            tag="backup_pmv", timeout=1800,
        )
        self.stdout.write(f"[2/6] Backup xong: {kk_path}")

        # 3) verify
        pmv_admin(
            f"RESTORE VERIFYONLY FROM DISK = N'{kk_path}' WITH CHECKSUM",
            tag="backup_pmv", timeout=1800,
        )
        rows = pmv_read(
            "SELECT TOP 1 CAST(backup_size/1024/1024 AS INT) AS mb, "
            "CONVERT(VARCHAR(19), backup_finish_date, 120) AS fin "
            "FROM msdb.dbo.backupset WHERE database_name = 'PMV_BANLE_KH2' "
            "ORDER BY backup_finish_date DESC",
            tag="backup_pmv", database="msdb",
        )
        mb = rows[0]["mb"] if rows else "?"
        self.stdout.write(f"[3/6] VERIFY OK — {mb}MB")
        PmvState.set("pmv_last_backup", f"{now:%d/%m/%Y %H:%M} | {kk_path} | {mb}MB | VERIFY OK")

        # 4) dọn bản cũ trên PC KK
        if not opts["no_prune"]:
            cutoff = (now - timezone.timedelta(days=settings.PMV_BACKUP_RETENTION_KK)).strftime(
                "%Y-%m-%dT%H:%M:%S"
            )
            pmv_admin(
                f"EXEC master.dbo.xp_delete_file 0, N'{kk_dir}', N'bak', N'{cutoff}'",
                tag="backup_pmv",
            )
            self.stdout.write(f"[4/6] Đã dọn .bak cũ hơn {settings.PMV_BACKUP_RETENTION_KK} ngày trên PC KK")
        else:
            self.stdout.write("[4/6] Bỏ qua dọn dẹp (--no-prune)")

        # 5) kéo về máy Mr Giang (cần share trên PC KK — .env PMV_BACKUP_SHARE)
        local_bak = None
        if settings.PMV_BACKUP_SHARE:
            try:
                src = Path(settings.PMV_BACKUP_SHARE) / fname
                dst_dir = Path(settings.PMV_BACKUP_DIR_LOCAL)
                dst_dir.mkdir(parents=True, exist_ok=True)
                local_bak = dst_dir / fname
                shutil.copy2(src, local_bak)
                keep = settings.PMV_BACKUP_RETENTION_LOCAL
                baks = sorted(dst_dir.glob("*.bak"), key=lambda p: p.stat().st_mtime, reverse=True)
                for old in baks[keep:]:
                    old.unlink()
                self.stdout.write(f"[5/6] Đã kéo về {local_bak} (giữ {keep} bản)")
                PmvState.set("pmv_last_pull", f"{now:%d/%m/%Y %H:%M} | {local_bak}")
            except Exception as exc:
                local_bak = None
                self.stderr.write(f"[5/6] KHÔNG kéo được file về máy Mr Giang: {exc}")
        else:
            self.stdout.write(
                "[5/6] Chưa khai PMV_BACKUP_SHARE trong .env — file .bak nằm trên PC KK. "
                "Muốn kéo về: tạo share thư mục backup trên PC KK rồi khai UNC vào .env."
            )

        # 6) restore sandbox local (cần SQL Express local — .env PMV_LOCAL_MSSQL)
        if settings.PMV_LOCAL_MSSQL and local_bak:
            try:
                self._restore_sandbox(local_bak)
                self.stdout.write(f"[6/6] Restore sandbox [{settings.PMV_SANDBOX_DB}] OK trên {settings.PMV_LOCAL_MSSQL}")
                PmvState.set("pmv_last_sandbox", f"{now:%d/%m/%Y %H:%M} | {settings.PMV_SANDBOX_DB}")
            except Exception as exc:
                self.stderr.write(f"[6/6] Restore sandbox LỖI: {exc}")
        else:
            self.stdout.write("[6/6] Chưa restore sandbox (thiếu PMV_LOCAL_MSSQL hoặc chưa kéo được .bak về)")

        self.stdout.write(self.style.SUCCESS("backup_pmv HOÀN TẤT."))

    def _restore_sandbox(self, local_bak):
        """Restore .bak vào SQL Express TRÊN MÁY NÀY (không phải PMV — không đi gateway)."""
        import pyodbc  # noqa: PLC0415 — ngoại lệ duy nhất: server LOCAL, không phải PMV

        data_dir = Path(settings.PMV_BACKUP_DIR_LOCAL) / "sandbox"
        data_dir.mkdir(parents=True, exist_ok=True)
        db = settings.PMV_SANDBOX_DB
        cn = pyodbc.connect(
            f"DRIVER={{{settings.PMV_MSSQL_ODBC_DRIVER}}};SERVER={settings.PMV_LOCAL_MSSQL};"
            "DATABASE=master;Trusted_Connection=yes;Encrypt=no;TrustServerCertificate=yes;",
            timeout=30, autocommit=True,
        )
        try:
            cur = cn.cursor()
            # tên file logic của PMV_BANLE_KH2 (đo 02/09/2026): GOLDRTDB + GOLDRTDB_log
            cur.execute(
                f"RESTORE DATABASE [{db}] FROM DISK = N'{local_bak}' WITH REPLACE, STATS = 25, "
                f"MOVE N'GOLDRTDB' TO N'{data_dir}\\{db}.mdf', "
                f"MOVE N'GOLDRTDB_log' TO N'{data_dir}\\{db}_log.ldf'"
            )
            while cur.nextset():
                pass
        finally:
            cn.close()
