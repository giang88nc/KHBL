"""
Bật/tắt TRACE hành vi PMVGoldRT (server-side trace — cơ chế nền của SQL Profiler).

Ghi từng lời gọi proc (RPC:Completed) + batch SQL (SQL:BatchCompleted) KÈM THAM SỐ THẬT
của app desktop vào file trên đĩa KK: D:\\KHJ_PMV_BACKUP\\trace\\pmv_behavior*.trc
(xoay vòng TRACE_MAX_MB × TRACE_FILES — SQL tự xóa file cũ nhất → dung lượng có trần).
Lọc ngay tại SQL: chỉ DB PMV_BANLE_KH2, bỏ login của KHBL (kimhanh2).
Overhead: không đáng kể (2 event, tiệm ~700 giao dịch/ngày).

Chạy: manage.py pmv_trace start | stop | status
Đọc log: collect_pmv_behavior (scheduler 2 phút/lần) đọc qua fn_trace_gettable.
"""
import re

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.pmv.gateway import pmv_admin, pmv_read, pmv_trace_batch
from apps.pmv.models import PmvState

TRACE_DIR = r"D:\KHJ_PMV_BACKUP\trace"
# Tên file gốc gắn MỐC GIỜ lúc bật (05/09/2026): sp_trace_create từ chối khi file cùng tên còn
# trên đĩa → mỗi lần bật là 1 chuỗi file mới, file cũ giữ lại cho tới khi dọn tay.
TRACE_BASE = f"{TRACE_DIR}\\pmv_behavior"
TRACE_MAX_MB = 20
# GĐ duyệt 05/09/2026 gom cả lời gọi của KHBL vào trace (bỏ lọc login) → thêm ~20-40k
# sự kiện/ngày nên nâng 5 → 10 file (200MB trên ổ D KK, còn 178GB).
TRACE_FILES = 10
# cột trace: 1 TextData 3 DatabaseID 8 HostName 10 ApplicationName 11 LoginName 12 SPID
# 13 Duration 14 StartTime 15 EndTime 16 Reads 17 Writes 18 CPU 34 ObjectName 35 DatabaseName
# 48 RowCounts 51 EventSequence
_COLS_RPC = [1, 3, 8, 10, 11, 12, 13, 14, 15, 16, 17, 18, 34, 35, 48, 51]
_COLS_BATCH = [1, 3, 8, 10, 11, 12, 13, 14, 15, 16, 17, 18, 35, 48, 51]


def trace_status():
    rows = pmv_read(
        "SELECT id, status, path, max_size, max_files, event_count, dropped_event_count, "
        "CONVERT(VARCHAR(19), start_time, 120) AS start_time, "
        "CONVERT(VARCHAR(19), last_event_time, 120) AS last_event_time "
        "FROM sys.traces WHERE path LIKE '%pmv_behavior%'",
        tag="pmv_trace", audit=False,
    )
    return rows[0] if rows else None


def _chon_file_dau(path_hien_tai, ten_file):
    """Từ path file trace ĐANG GHI (sys.traces) + danh sách tên file trong thư mục → file
    ĐẦU của chuỗi (số hậu tố nhỏ nhất). fn_trace_gettable(file_đầu, DEFAULT) đọc nối tiếp
    các file xoay vòng sau nó; SQL xóa file cũ nhất khi vượt TRACE_FILES nên file đầu
    có thể là _3.trc chứ không phải .trc. Hàm thuần để smoke test."""
    thu_muc, _, hien_tai = path_hien_tai.rpartition("\\")
    base = re.sub(r"(?i)(_\d+)?\.trc$", "", hien_tai)
    ung_vien = []
    for ten in ten_file:
        m = re.fullmatch(rf"(?i){re.escape(base)}(?:_(\d+))?\.trc", ten)
        if m:
            ung_vien.append((int(m.group(1)) if m.group(1) else 0, ten))
    if not ung_vien:
        return path_hien_tai
    return f"{thu_muc}\\{min(ung_vien)[1]}"


def trace_first_file():
    """Đường dẫn file ĐẦU của chuỗi trace đang chạy trên KK; None khi trace không chạy."""
    cur = trace_status()
    if not cur or cur["status"] != 1:
        return None
    files = pmv_admin(f"EXEC master.dbo.xp_dirtree N'{TRACE_DIR}', 1, 1", tag="pmv_trace", fetch=True)
    ten = [f["subdirectory"] for f in files if f.get("file") in (1, True, "1")]
    return _chon_file_dau(cur["path"], ten)


class Command(BaseCommand):
    help = "Bật/tắt/xem trace hành vi PMVGoldRT (start | stop | status)"

    def add_arguments(self, parser):
        parser.add_argument("action", choices=["start", "stop", "status"])

    def handle(self, *args, **opts):
        action = opts["action"]
        cur = trace_status()

        if action == "status":
            if not cur:
                self.stdout.write("Trace: KHÔNG chạy.")
            else:
                self.stdout.write(
                    f"Trace #{cur['id']} status={cur['status']} (1=đang chạy) file={cur['path']} "
                    f"events={cur['event_count']} dropped={cur['dropped_event_count']} "
                    f"từ {cur['start_time']} · sự kiện cuối {cur['last_event_time']}"
                )
            return

        if action == "start":
            if cur and cur["status"] == 1:
                self.stdout.write(f"Trace #{cur['id']} ĐÃ chạy sẵn — bỏ qua.")
                return
            if cur:  # có định nghĩa cũ đang dừng → đóng hẳn rồi tạo mới
                pmv_trace_batch(f"EXEC sp_trace_setstatus {cur['id']}, 2", tag="pmv_trace")
            pmv_admin(f"EXEC master.dbo.xp_create_subdir N'{TRACE_DIR}'", tag="pmv_trace")
            now = timezone.localtime()
            # ⚠ SQL Server (lỗi 19069) từ chối tên gốc kết thúc bằng "_số" khi bật xoay vòng
            # (tưởng là số thứ tự file) → dùng 'T' nối ngày-giờ, KHÔNG dùng dấu gạch dưới.
            base = f"{TRACE_BASE}_{now:%Y%m%dT%H%M}"
            stmts = ["DECLARE @tid INT", "DECLARE @maxsize BIGINT", f"SET @maxsize = {TRACE_MAX_MB}",
                     "DECLARE @on BIT", "SET @on = 1",
                     f"EXEC sp_trace_create @tid OUTPUT, 2, N'{base}', @maxsize, NULL, {TRACE_FILES}"]
            stmts += [f"EXEC sp_trace_setevent @tid, 10, {c}, @on" for c in _COLS_RPC]
            stmts += [f"EXEC sp_trace_setevent @tid, 12, {c}, @on" for c in _COLS_BATCH]
            stmts += [
                # Chỉ lọc DB. KHÔNG lọc login nữa (GĐ duyệt 05/09/2026): lời gọi của KHBL
                # đi chung dòng thời gian, collect gắn nhãn máy "KHBL" và bỏ SELECT trần của web.
                f"EXEC sp_trace_setfilter @tid, 35, 0, 0, N'{settings.PMV_MSSQL_DB}'",   # DatabaseName =
                "EXEC sp_trace_setstatus @tid, 1",
                "SELECT @tid AS tid",
            ]
            rows = pmv_trace_batch(";\n".join(stmts), tag="pmv_trace")
            tid = rows[0]["tid"] if rows else None
            live = trace_status()  # lấy đường thật SQL vừa mở
            PmvState.set("pmv_trace_id", tid or "")
            PmvState.set("pmv_trace_path", (live or {}).get("path") or f"{base}.trc")
            PmvState.set("pmv_trace_started", f"{now:%d/%m/%Y %H:%M}")
            # EventSequence là bộ đếm toàn instance (không reset khi mở trace mới) — GIỮ mốc
            # đã đọc để không đọc lại sự kiện cũ; chỉ về 0 khi chưa có mốc.
            if not PmvState.get("pmv_trace_last_seq"):
                PmvState.set("pmv_trace_last_seq", "0")
            self.stdout.write(self.style.SUCCESS(
                f"Trace #{tid} ĐÃ BẬT → {base}*.trc (xoay vòng {TRACE_FILES}×{TRACE_MAX_MB}MB, gồm cả KHBL)"
            ))
            return

        if action == "stop":
            if not cur:
                self.stdout.write("Trace không chạy — không có gì để tắt.")
            else:
                pmv_trace_batch(f"EXEC sp_trace_setstatus {cur['id']}, 0", tag="pmv_trace")
                pmv_trace_batch(f"EXEC sp_trace_setstatus {cur['id']}, 2", tag="pmv_trace")
                self.stdout.write(self.style.SUCCESS(f"Trace #{cur['id']} ĐÃ TẮT (file .trc còn trên KK, tối đa {TRACE_FILES}×{TRACE_MAX_MB}MB)."))
            PmvState.set("pmv_trace_id", "")
            PmvState.set("pmv_trace_stopped", f"{timezone.localtime():%d/%m/%Y %H:%M}")
