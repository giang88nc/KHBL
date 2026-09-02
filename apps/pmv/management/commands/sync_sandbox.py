"""
SYNC 1 CHIỀU: KK (PMV thật) → Mr Giang (sandbox). Sau khi chạy, 2 SQL = nhau
(tính tại thời điểm backup — PMV thật vẫn bán tiếp nên chênh lệch sau đó = đúng
số giao dịch phát sinh trong lúc sync, thường 0–vài dòng).

Cơ chế KHÔNG CẦN SHARE (chốt 02/09/2026 sau khi \\AA\PUBLIC từ chối SQL KK —
service NetworkService ra mạng bằng tài khoản máy KK$, workgroup không nhận):
  1) SQL KK BACKUP ra đĩa LOCAL của nó: PMV_BACKUP_DIR_KK\KHBL_PMV_SYNC.bak (COPY_ONLY, INIT)
  2) RESTORE VERIFYONLY trên KK
  3) Mr Giang HÚT file về qua kết nối SQL: SELECT BulkColumn FROM OPENROWSET(BULK ..., SINGLE_BLOB)
     (đo thực tế 428MB ≈ 40s; nếu .env có PMV_SYNC_BAK_READ = UNC đọc được thì copy thay vì hút)
  4) RESTORE đè lên PMV_SANDBOX từ file local
  5) đếm đối chiếu + ghi trạng thái

Gọi: manage.py sync_sandbox   (hoặc nút ⟳ SYNC trên /so-sanh/ và trang trạng thái)
"""
import shutil
import time
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.pmv.gateway import pmv_admin
from apps.pmv.models import PmvState
from apps.pmv.sandbox import restore_sandbox_from, sandbox_query

SYNC_NAME = "KHBL_PMV_SYNC.bak"


class Command(BaseCommand):
    help = "SYNC 1 chiều KK→Mr Giang: backup PMV thật, hút về, restore đè sandbox (2 SQL = nhau)"

    def handle(self, *args, **opts):
        if not settings.PMV_LOCAL_MSSQL:
            raise CommandError("Chưa khai PMV_LOCAL_MSSQL trong .env (vd localhost\\SQL2014).")
        kk_bak = f"{settings.PMV_BACKUP_DIR_KK.rstrip(chr(92))}\\{SYNC_NAME}"
        local_dir = Path(settings.PMV_BACKUP_DIR_LOCAL)
        local_dir.mkdir(parents=True, exist_ok=True)
        local_bak = local_dir / SYNC_NAME

        t_start = time.monotonic()
        now = timezone.localtime()
        PmvState.set("pmv_last_sync", f"{now:%d/%m/%Y %H:%M} | ĐANG CHẠY...")

        # 1) SQL KK backup ra đĩa local của nó
        pmv_admin(f"EXEC master.dbo.xp_create_subdir N'{settings.PMV_BACKUP_DIR_KK}'", tag="sync_sandbox")
        pmv_admin(
            f"BACKUP DATABASE [PMV_BANLE_KH2] TO DISK = N'{kk_bak}' "
            "WITH COPY_ONLY, INIT, CHECKSUM, STATS = 25",
            tag="sync_sandbox", timeout=1800,
        )
        self.stdout.write(f"[1/5] SQL KK đã backup: {kk_bak}")

        # 2) verify trên KK
        pmv_admin(f"RESTORE VERIFYONLY FROM DISK = N'{kk_bak}' WITH CHECKSUM",
                  tag="sync_sandbox", timeout=1800)
        self.stdout.write("[2/5] VERIFY OK")

        # 3) đưa file về máy Mr Giang
        t0 = time.monotonic()
        if settings.PMV_SYNC_BAK_READ:
            shutil.copy2(settings.PMV_SYNC_BAK_READ, local_bak)
            how = f"copy từ {settings.PMV_SYNC_BAK_READ}"
        else:
            rows = pmv_admin(
                f"SELECT BulkColumn FROM OPENROWSET(BULK N'{kk_bak}', SINGLE_BLOB) AS x",
                tag="sync_sandbox", timeout=1800, fetch=True,
            )
            blob = rows[0]["BulkColumn"]
            local_bak.write_bytes(blob)
            del blob, rows
            how = "hút qua kết nối SQL"
        mb = local_bak.stat().st_size / 1024 / 1024
        self.stdout.write(f"[3/5] {mb:.0f}MB về máy Mr Giang ({how}) trong {time.monotonic() - t0:.0f}s")

        # 4) restore đè sandbox
        restore_sandbox_from(local_bak)
        self.stdout.write(f"[4/5] Restore đè [{settings.PMV_SANDBOX_DB}] xong")

        # 5) đối chiếu + trạng thái
        r = sandbox_query(
            "SELECT (SELECT COUNT(*) FROM T_EMPLOYEE) AS nv, "
            "(SELECT COUNT(*) FROM TRN_RT_BUYSELL) AS hd, "
            "(SELECT COUNT(*) FROM T_PRODUCT) AS sp"
        )[0]
        done = timezone.localtime()
        tong = time.monotonic() - t_start
        PmvState.set(
            "pmv_last_sync",
            f"{done:%d/%m/%Y %H:%M} | XONG {tong:.0f}s | {mb:.0f}MB | NV={r['nv']} HĐ={r['hd']} SP={r['sp']}",
        )
        PmvState.set("pmv_last_sandbox", f"{done:%d/%m/%Y %H:%M} | SYNC từ PMV thật | "
                     f"NV={r['nv']} HĐ={r['hd']} SP={r['sp']}")
        self.stdout.write(self.style.SUCCESS(
            f"SYNC HOÀN TẤT trong {tong:.0f}s — sandbox = PMV thật tại {done:%H:%M:%S}. "
            f"T_EMPLOYEE={r['nv']}, TRN_RT_BUYSELL={r['hd']}, T_PRODUCT={r['sp']}"
        ))
