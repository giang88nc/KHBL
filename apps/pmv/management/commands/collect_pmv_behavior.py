r"""
Thu thập hành vi PMVGoldRT vào bảng pmv_behavior_logs + FILE logs\pmv\<ngày>\*.log
(scheduler 2 phút/lần). Thiết kế GĐ duyệt 05/09/2026 (Bước 1 + Bước 2).

Lớp THỐNG KÊ (luôn chạy, chỉ đọc): đọc sys.dm_exec_query_stats → proc nào tăng số lần
  thực thi so với lần đọc trước → 1 dòng STATS (exec_delta, last_execution_time).
  Cache SQL bị dọn (đếm giảm) → lấy lại mốc, không ghi.
Lớp TRACE (khi trace đang bật — manage.py pmv_trace start): đọc fn_trace_gettable từ FILE
  ĐẦU của chuỗi trace hiện hành (đường lấy sống từ sys.traces + xp_dirtree, không ghim path
  cũ) với DEFAULT để đi hết các file xoay vòng; lọc EventSequence > mốc đã đọc.
  Bỏ NOISE (proc app poll liên tục — lớp STATS vẫn đếm). Lời gọi của CHÍNH KHBL (login
  kimhanh2) GIỮ LẠI khi là proc (gắn nhãn máy KHBL), bỏ các SELECT trần của web (poll
  bảng giá/hóa đơn, sys.parameters...) để không ngập log.
Bước 2: chụp FULL KK → so với lần trước → thay_doi.log + pmv_change_logs (behavior_log.py).
Dọn 1 lần/ngày: file 90 ngày, DB 30 ngày.
"""
import json

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.pmv import behavior_log as BL
from apps.pmv.classify import NOISE, SP_WRAPPERS, classify, proc_name_from_text
from apps.pmv.gateway import pmv_read
from apps.pmv.management.commands.pmv_trace import trace_first_file
from apps.pmv.models import PmvBehavior, PmvState

_TRACE_BATCH = 5000
_TRACE_MAX_LOOP = 6      # tối đa 30.000 sự kiện/chu kỳ — dư sức so tiệm ~700 giao dịch/ngày


def _aware(dt):
    if dt is None:
        return timezone.now()
    if timezone.is_naive(dt):
        return timezone.make_aware(dt)
    return dt


class Command(BaseCommand):
    help = "Thu thập hành vi PMVGoldRT (bộ đếm SQL + trace) → MySQL + file log; dò thay đổi SQL KK"

    def add_arguments(self, parser):
        parser.add_argument("--khong-do-thay-doi", action="store_true",
                            help="Chỉ thu hành vi, không chụp FULL KK (Bước 2)")

    def handle(self, *args, **opts):
        rows = []
        rows += thu_thap_stats()
        rows += thu_thap_trace(self.stderr)
        n_file = BL.ghi(rows)
        n_stats = sum(1 for r in rows if r.source == PmvBehavior.Source.STATS)
        n_trace = len(rows) - n_stats
        tin = f"{timezone.localtime():%d/%m/%Y %H:%M} | STATS +{n_stats} | TRACE +{n_trace} | file +{n_file}"
        n_doi = "-"
        if not opts["khong_do_thay_doi"]:
            try:
                n_doi = len(BL.do_thay_doi(rows))
            except Exception as exc:
                n_doi = "LỖI"
                self.stderr.write(f"Dò thay đổi KK lỗi: {exc}")
        PmvState.set("pmv_behavior_last", f"{tin} | bảng đổi {n_doi}")
        try:
            kq = BL.don_hang_ngay()
            if kq:
                self.stdout.write(f"Dọn: file {kq[0]} | DB hành vi/thay đổi {kq[1]}")
        except Exception as exc:
            self.stderr.write(f"Dọn nhật ký lỗi: {exc}")
        self.stdout.write(f"collect_pmv_behavior: STATS +{n_stats}, TRACE +{n_trace}, file +{n_file}, bảng đổi {n_doi}")


# ---------- lớp THỐNG KÊ ----------
def thu_thap_stats():
    rows = pmv_read(
        "SELECT OBJECT_NAME(st.objectid, st.dbid) AS obj, SUM(qs.execution_count) AS n, "
        "MAX(qs.last_execution_time) AS last_exec "
        "FROM sys.dm_exec_query_stats qs CROSS APPLY sys.dm_exec_sql_text(qs.sql_handle) st "
        "WHERE st.objectid IS NOT NULL AND st.dbid = DB_ID(?) "
        "GROUP BY st.objectid, st.dbid",
        (settings.PMV_MSSQL_DB,), tag="collect_behavior", audit=False,
    )
    cur = {r["obj"]: [int(r["n"]), r["last_exec"].isoformat() if r["last_exec"] else None]
           for r in rows if r["obj"]}
    try:
        prev = json.loads(PmvState.get("pmv_stats_prev") or "{}")
    except Exception:
        prev = {}
    new_rows = []
    if prev:  # lần đầu chỉ lấy mốc
        for obj, (n, last) in cur.items():
            p = prev.get(obj)
            if p is None:
                delta = n            # proc mới xuất hiện trong cache
            elif n > p[0]:
                delta = n - p[0]
            else:
                continue             # không đổi hoặc cache reset
            if delta <= 0 or obj in NOISE:
                continue
            cat, act = classify(obj)
            new_rows.append(PmvBehavior(
                event_time=_aware(timezone.datetime.fromisoformat(last)) if last else timezone.now(),
                source=PmvBehavior.Source.STATS, category=cat, action=act,
                proc_name=obj, exec_delta=delta,
            ))
    if new_rows:
        PmvBehavior.objects.bulk_create(new_rows, batch_size=500)
    PmvState.set("pmv_stats_prev", json.dumps(cur))
    return new_rows


# ---------- lớp TRACE ----------
def thu_thap_trace(err=None):
    """Đọc sự kiện trace mới (EventSequence > mốc) → PmvBehavior. Trả list đã ghi DB."""
    try:
        path = trace_first_file()
    except Exception as exc:
        if err:
            err.write(f"Không xác định được file trace: {exc}")
        path = PmvState.get("pmv_trace_path")
    if not path or not PmvState.get("pmv_trace_id"):
        return []
    PmvState.set("pmv_trace_path", path)
    last_seq = int(PmvState.get("pmv_trace_last_seq") or 0)
    tat_ca = []
    for _ in range(_TRACE_MAX_LOOP):
        try:
            rows = pmv_read(
                f"SELECT TOP {_TRACE_BATCH} EventSequence, EventClass, StartTime, Duration, Reads, Writes, "
                "RowCounts, SPID, HostName, ApplicationName, LoginName, ObjectName, "
                "CAST(TextData AS NVARCHAR(4000)) AS TextData "
                f"FROM fn_trace_gettable(N'{path}', DEFAULT) "
                "WHERE EventSequence > ? AND EventClass IN (10, 12) ORDER BY EventSequence",
                (last_seq,), tag="collect_behavior", audit=False, timeout=120,
            )
        except Exception as exc:
            if err:
                err.write(f"Đọc trace lỗi: {exc}")
            break
        if not rows:
            break
        max_seq = max(int(r["EventSequence"]) for r in rows)
        moi = _dong_trace(rows)
        if moi:
            PmvBehavior.objects.bulk_create(moi, batch_size=500)
            tat_ca += moi
        last_seq = max_seq
        PmvState.set("pmv_trace_last_seq", str(max_seq))
        if len(rows) < _TRACE_BATCH:
            break
    return tat_ca


def _dong_trace(rows):
    web_login = settings.PMV_MSSQL_USER
    out = []
    for r in rows:
        text = r["TextData"] or ""
        name = (r["ObjectName"] or "").strip("[]")
        # RPC:Completed điền sẵn ObjectName = tên lớp bọc (sp_prepexec…) → phải bóc proc thật từ TextData
        if not name or name.lower() in SP_WRAPPERS:
            name = proc_name_from_text(text)
        if name in NOISE:
            continue
        la_khbl = (r["LoginName"] or "") == web_login
        if la_khbl and not name:
            continue  # SELECT trần của web (poll bảng giá/hóa đơn, sys.parameters, trace...) — không phải hành vi nghiệp vụ
        if not name and text.lstrip()[:6].lower() == "select" and len(text) < 200:
            continue  # SELECT lặt vặt của app
        cat, act = classify(name, text)
        dur = r["Duration"]
        out.append(PmvBehavior(
            event_time=_aware(r["StartTime"]),
            source=PmvBehavior.Source.TRACE, category=cat, action=act,
            proc_name=name[:128], text=text[:4000],
            login=(r["LoginName"] or "")[:64], host=("KHBL" if la_khbl else (r["HostName"] or ""))[:64],
            app=(r["ApplicationName"] or "")[:128], spid=r["SPID"],
            duration_ms=int(dur / 1000) if dur is not None else None,
            reads=r["Reads"], writes=r["Writes"], row_count=r["RowCounts"],
            event_seq=int(r["EventSequence"]),
        ))
    return out
