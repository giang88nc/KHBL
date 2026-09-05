"""Đồng bộ KK → KHO LỊCH SỬ (PMV_KH2_HIST). Phase 1: dựng DB + schema + BACKFILL + RECONCILE.

An toàn tuyệt đối với KK: chỉ ĐỌC (SELECT/metadata) từ KK; mọi CREATE/INSERT chỉ vào kho lịch sử
qua gateway.hist_* (kết nối riêng localhost\\SQL2014, không bao giờ chạm 206). Backfill = DROP+CREATE
bảng ở HIST rồi chép trọn theo lô. Reconcile = so COUNT + CHECKSUM_AGG(BINARY_CHECKSUM) từng bảng.
"""
import time

from django.conf import settings

from apps.pmv import gateway as G
from apps.pmv import hist_config as HC

CHUNK = 5000
CONTROL = "_hist_sync_state"


# ─────────────────────────── hạ tầng kho ───────────────────────────
def ensure_database():
    """Tạo PMV_KH2_HIST nếu chưa có (chạy trên 'master' của instance HIST)."""
    db = settings.PMV_HIST_DB
    rows = G.hist_query("SELECT name FROM sys.databases WHERE name=?", [db], database="master")
    if not rows:
        G.hist_exec(f"CREATE DATABASE [{db}]", database="master")
    return db


def ensure_control():
    G.hist_exec(f"""
        IF OBJECT_ID('{CONTROL}') IS NULL
        CREATE TABLE [{CONTROL}] (
            table_name  sysname NOT NULL PRIMARY KEY,
            strategy    varchar(20) NULL,
            watermark   nvarchar(60) NULL,
            last_full_at datetime NULL,
            last_run    datetime NULL,
            rows_kk     bigint NULL,
            rows_hist   bigint NULL,
            checksum_kk bigint NULL,
            checksum_hist bigint NULL,
            is_match    bit NULL,
            last_ms     int NULL,
            note        nvarchar(400) NULL
        )""")


# ─────────────────────────── schema + backfill ───────────────────────────
def mirror_schema(meta):
    """DROP + CREATE bảng ở HIST theo đúng cột KK + 2 cột kỹ thuật _sync_seen_at/_sync_deleted."""
    t = meta["table"]
    pkset = {c.lower() for c in meta["pk"]}
    ddls = []
    for c in meta["columns"]:
        d = c["ddl"]
        if c["name"].lower() in pkset:               # cột khóa chính phải NOT NULL
            d = d.rsplit(" ", 1)[0] + " NOT NULL" if d.rstrip().endswith("NULL") else d
            d = d.replace(" NOT NOT NULL", " NOT NULL")
        ddls.append(d)
    ddls.append("[_sync_seen_at] datetime NULL")
    ddls.append("[_sync_deleted] bit NOT NULL CONSTRAINT [DF_%s_del] DEFAULT (0)" % _safe(t))
    if meta["pk"]:
        cols = ", ".join(f"[{c}]" for c in meta["pk"])
        ddls.append(f"CONSTRAINT [PK_{_safe(t)}] PRIMARY KEY ({cols})")
    G.hist_exec(f"IF OBJECT_ID('[{t}]') IS NOT NULL DROP TABLE [{t}]")
    G.hist_exec(f"CREATE TABLE [{t}] (\n  " + ",\n  ".join(ddls) + "\n)")


def backfill(meta):
    """Chép trọn KK→HIST theo lô. Trả số dòng đã chép."""
    t = meta["table"]
    cols = [c["name"] for c in meta["columns"]]
    collist = ", ".join(f"[{c}]" for c in cols)
    sel = f"SELECT {collist} FROM [{t}] WITH (NOLOCK)"
    ins = f"INSERT INTO [{t}] ({collist}) VALUES ({', '.join(['?'] * len(cols))})"
    # ⚠ fast_executemany cấp phát bộ đệm theo cột LỚN NHẤT → cột (max) làm nổ RAM.
    # Bảng có cột (max) → tắt fast + lô nhỏ (chậm hơn nhưng an toàn).
    has_max = any("(max)" in c["ddl"].lower() for c in meta["columns"])
    fast = not has_max
    chunk_size = CHUNK if fast else 500
    total = 0
    for chunk in G.pmv_read_stream(sel, tag="hist_backfill", target="kk", chunk=chunk_size):
        G.hist_executemany(ins, chunk, tag="hist_backfill", fast=fast)
        total += len(chunk)
    G.hist_exec(f"UPDATE [{t}] SET [_sync_seen_at]=GETDATE()", audit=False)
    return total


# ─────────────────────────── đối soát ───────────────────────────
def _checksum_sql(meta, where=""):
    cs = [c["name"] for c in meta["columns"] if c["checksummable"]]
    args = ", ".join(f"[{c}]" for c in cs) or "1"
    return f"SELECT COUNT_BIG(*) AS n, CHECKSUM_AGG(BINARY_CHECKSUM({args})) AS s"


def reconcile(meta):
    t = meta["table"]
    base = _checksum_sql(meta)
    kk = G.pmv_read(f"{base} FROM [{t}] WITH (NOLOCK)", tag="hist_reconcile", target="kk", audit=False)[0]
    hs = G.hist_query(f"{base} FROM [{t}] WHERE [_sync_deleted]=0")[0]
    n_kk, s_kk = int(kk["n"] or 0), kk["s"]
    n_hs, s_hs = int(hs["n"] or 0), hs["s"]
    match = (n_kk == n_hs) and (s_kk == s_hs)
    return {"rows_kk": n_kk, "rows_hist": n_hs, "checksum_kk": s_kk,
            "checksum_hist": s_hs, "is_match": match}


# ─────────────────────────── ghi trạng thái ───────────────────────────
def _save_state(meta, rec, ms, note=""):
    t = meta["table"]
    G.hist_exec(f"DELETE FROM [{CONTROL}] WHERE table_name=?", [t], audit=False)
    G.hist_exec(
        f"INSERT INTO [{CONTROL}] (table_name, strategy, watermark, last_full_at, last_run, "
        f"rows_kk, rows_hist, checksum_kk, checksum_hist, is_match, last_ms, note) "
        f"VALUES (?,?,?,GETDATE(),GETDATE(),?,?,?,?,?,?,?)",
        [t, meta["strategy"], meta["watermark"],
         rec.get("rows_kk"), rec.get("rows_hist"), rec.get("checksum_kk"),
         rec.get("checksum_hist"), 1 if rec.get("is_match") else 0, ms, note[:400]],
        audit=False)


# ─────────────────────────── điều phối ───────────────────────────
def run(tables=None, *, do_backfill=True, do_reconcile=True, log=lambda s: None):
    """Chạy backfill+reconcile cho các bảng (None = tất cả trong danh mục). Trả list report."""
    ensure_database()
    ensure_control()
    metas = HC.discover()
    if tables:
        want = {x.lower() for x in tables}
        metas = [m for m in metas if m["table"].lower() in want]
    report = []
    for m in metas:
        t = m["table"]
        t0 = time.monotonic()
        rows = None
        try:
            if do_backfill:
                mirror_schema(m)
                rows = backfill(m)
            rec = reconcile(m) if do_reconcile else {}
            ms = int((time.monotonic() - t0) * 1000)
            _save_state(m, rec, ms, note="")
            ok = rec.get("is_match", None)
            flag = "OK " if ok else ("≠  " if ok is False else "·  ")
            log(f"  {flag}{t:<32} chép={rows if rows is not None else '-':>8}  "
                f"KK={rec.get('rows_kk','-')} HIST={rec.get('rows_hist','-')} "
                f"{'KHỚP' if ok else ('LỆCH' if ok is False else '')}")
            report.append({"table": t, "rows": rows, **rec, "ms": ms, "strategy": m["strategy"]})
        except Exception as exc:
            ms = int((time.monotonic() - t0) * 1000)
            err = str(exc) or exc.__class__.__name__      # MemoryError… str rỗng
            _save_state(m, {}, ms, note=f"LỖI: {err}")
            log(f"  ✗  {t:<32} LỖI: {err[:160]}")
            report.append({"table": t, "error": err, "ms": ms})
    return report


def status():
    """Đọc bảng điều khiển cho trang/report."""
    ensure_database()
    ensure_control()
    return G.hist_query(
        f"SELECT table_name, strategy, rows_kk, rows_hist, is_match, last_run, last_ms, note "
        f"FROM [{CONTROL}] ORDER BY table_name")


def _safe(name):
    return "".join(ch if ch.isalnum() else "_" for ch in name)
