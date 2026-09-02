"""
Thu thập hành vi PMVGoldRT vào bảng pmv_behavior_logs (scheduler 2 phút/lần).

Lớp THỐNG KÊ (luôn chạy, chỉ đọc): đọc sys.dm_exec_query_stats → proc nào tăng số lần
  thực thi so với lần đọc trước → 1 dòng STATS (exec_delta, last_execution_time).
  Cache SQL bị dọn (đếm giảm) → lấy lại mốc, không ghi.
Lớp TRACE (khi trace đang bật — manage.py pmv_trace start): đọc fn_trace_gettable từ
  EventSequence đã đọc lần trước → mỗi lời gọi 1 dòng TRACE có tham số thật; bỏ NOISE
  (proc poll liên tục — lớp STATS vẫn đếm) và bỏ login của KHBL.
"""
import json

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.pmv.classify import NOISE, classify, proc_name_from_text
from apps.pmv.gateway import pmv_read
from apps.pmv.models import PmvBehavior, PmvState

_TRACE_BATCH = 5000


def _aware(dt):
    if dt is None:
        return timezone.now()
    if timezone.is_naive(dt):
        return timezone.make_aware(dt)
    return dt


class Command(BaseCommand):
    help = "Thu thập hành vi PMVGoldRT (bộ đếm SQL + trace) vào pmv_behavior_logs"

    def handle(self, *args, **opts):
        n_stats = self._collect_stats()
        n_trace = self._collect_trace()
        PmvState.set("pmv_behavior_last", f"{timezone.localtime():%d/%m/%Y %H:%M} | STATS +{n_stats} | TRACE +{n_trace}")
        self.stdout.write(f"collect_pmv_behavior: STATS +{n_stats}, TRACE +{n_trace}")

    # ---------- lớp THỐNG KÊ ----------
    def _collect_stats(self):
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
        return len(new_rows)

    # ---------- lớp TRACE ----------
    def _collect_trace(self):
        path = PmvState.get("pmv_trace_path")
        if not path or not PmvState.get("pmv_trace_id"):
            return 0
        last_seq = int(PmvState.get("pmv_trace_last_seq") or 0)
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
            self.stderr.write(f"Đọc trace lỗi: {exc}")
            return 0
        if not rows:
            return 0
        max_seq = max(int(r["EventSequence"]) for r in rows)
        if max_seq < last_seq:  # SQL restart → sequence reset
            last_seq = 0
        new_rows = []
        for r in rows:
            text = r["TextData"] or ""
            name = (r["ObjectName"] or "").strip("[]") or proc_name_from_text(text)
            if (r["LoginName"] or "") == settings.PMV_MSSQL_USER or name in NOISE:
                continue
            if not name and text.lstrip()[:6].lower() == "select" and len(text) < 200:
                continue  # SELECT lặt vặt của app
            cat, act = classify(name, text)
            dur = r["Duration"]
            new_rows.append(PmvBehavior(
                event_time=_aware(r["StartTime"]),
                source=PmvBehavior.Source.TRACE, category=cat, action=act,
                proc_name=name[:128], text=text[:4000],
                login=(r["LoginName"] or "")[:64], host=(r["HostName"] or "")[:64],
                app=(r["ApplicationName"] or "")[:128], spid=r["SPID"],
                duration_ms=int(dur / 1000) if dur is not None else None,
                reads=r["Reads"], writes=r["Writes"], row_count=r["RowCounts"],
                event_seq=int(r["EventSequence"]),
            ))
        if new_rows:
            PmvBehavior.objects.bulk_create(new_rows, batch_size=500)
        PmvState.set("pmv_trace_last_seq", str(max_seq))
        return len(new_rows)
