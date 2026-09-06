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
def _create_table(meta):
    """CREATE bảng ở HIST theo đúng cột KK + 2 cột kỹ thuật _sync_seen_at/_sync_deleted (không DROP)."""
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
    G.hist_exec(f"CREATE TABLE [{t}] (\n  " + ",\n  ".join(ddls) + "\n)")


def mirror_schema(meta):
    """DROP + CREATE (dùng cho backfill Phase 1 — làm lại schema sạch)."""
    G.hist_exec(f"IF OBJECT_ID('[{meta['table']}]') IS NOT NULL DROP TABLE [{meta['table']}]")
    _create_table(meta)


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


# ─────────────────────────── ĐỒNG BỘ INCREMENTAL (Phase 2) ───────────────────────────
# Bảng có cột CreatedDate → dò VOID (đơn bị xóa cứng ở KK) trong cửa sổ PMV_HIST_VOID_DAYS.
VOID_TABLES = ("TRN_RT_BUYSELL", "TRN_RT_BUYGOLD")
CHILD_KEY = "TrnID"


def _has_max_col(meta):
    return any("(max)" in c["ddl"].lower() for c in meta["columns"])


def ensure_schema(meta):
    """Tạo bảng ở HIST nếu CHƯA có (không DROP — giữ dữ liệu đã backfill)."""
    t = meta["table"]
    if G.hist_query(f"SELECT 1 AS x FROM sys.objects WHERE object_id=OBJECT_ID('[{t}]') AND type='U'"):
        return
    _create_table(meta)


def _hist_scalar(sql):
    r = G.hist_query(sql)
    return list(r[0].values())[0] if r else None


def sync_one(meta):
    """Đồng bộ 1 bảng theo chiến lược. Trả dict {ins, upd, changed:set(TrnID)}."""
    t, cols = meta["table"], [c["name"] for c in meta["columns"]]
    collist = ", ".join(f"[{c}]" for c in cols)
    fast = not _has_max_col(meta)
    strat = meta["strategy"]

    if strat == "append":
        wm = meta["watermark"]
        if not wm:                       # log không có khóa → bỏ qua incremental (đã backfill)
            return {"ins": 0, "upd": 0, "changed": set()}
        last = _hist_scalar(f"SELECT MAX([{wm}]) AS m FROM [{t}]")
        where = f" WHERE [{wm}] > ?" if last is not None else ""
        params = [last] if last is not None else []
        ins = f"INSERT INTO [{t}] ({collist}) VALUES ({', '.join(['?'] * len(cols))})"
        n = 0
        for chunk in G.pmv_read_stream(f"SELECT {collist} FROM [{t}] WITH (NOLOCK){where}",
                                       params, tag="hist_sync", target="kk",
                                       chunk=(CHUNK if fast else 500)):
            G.hist_executemany(ins, chunk, tag="hist_sync", fast=fast)
            n += len(chunk)
        if n:
            G.hist_exec(f"UPDATE [{t}] SET [_sync_seen_at]=GETDATE() WHERE [_sync_seen_at] IS NULL",
                        audit=False)
        return {"ins": n, "upd": 0, "changed": set()}

    if strat == "upsert":
        wm = meta["watermark"]
        last = _hist_scalar(f"SELECT MAX([{wm}]) AS m FROM [{t}]")
        where = f" WHERE [{wm}] > ?" if last is not None else ""
        params = [last] if last is not None else []
        rows = G.pmv_read(f"SELECT {collist} FROM [{t}] WITH (NOLOCK){where}", params,
                          tag="hist_sync", target="kk", audit=False)
        pki = cols.index(meta["pk"][0])
        changed = {r[meta["pk"][0]] for r in rows}
        tuples = [tuple(r[c] for c in cols) for r in rows]
        ins, upd = G.hist_bulk_merge(t, cols, meta["pk"], tuples, fast=fast)
        return {"ins": ins, "upd": upd, "changed": changed}

    if strat == "snapshot" and meta["pk"]:
        rows = G.pmv_read(f"SELECT {collist} FROM [{t}] WITH (NOLOCK)", tag="hist_sync",
                          target="kk", audit=False)
        tuples = [tuple(r[c] for c in cols) for r in rows]
        ins, upd = G.hist_bulk_merge(t, cols, meta["pk"], tuples, fast=fast)
        return {"ins": ins, "upd": upd, "changed": set()}

    return {"ins": 0, "upd": 0, "changed": set()}   # child xử lý riêng theo cha


def refresh_children(meta, changed_keys):
    """Làm mới bảng CON theo TrnID cha vừa đổi: xóa dòng cũ + chép lại từ KK."""
    if not changed_keys:
        return 0
    t, cols = meta["table"], [c["name"] for c in meta["columns"]]
    if CHILD_KEY not in cols:
        return 0
    collist = ", ".join(f"[{c}]" for c in cols)
    fast = not _has_max_col(meta)
    keys = list(changed_keys)
    ins = f"INSERT INTO [{t}] ({collist}) VALUES ({', '.join(['?'] * len(cols))})"
    total = 0
    for i in range(0, len(keys), 500):
        batch = keys[i:i + 500]
        ph = ", ".join(["?"] * len(batch))
        G.hist_exec(f"DELETE FROM [{t}] WHERE [{CHILD_KEY}] IN ({ph})", batch, audit=False)
        rows = G.pmv_read(f"SELECT {collist} FROM [{t}] WITH (NOLOCK) WHERE [{CHILD_KEY}] IN ({ph})",
                          batch, tag="hist_sync", target="kk", audit=False)
        if rows:
            G.hist_executemany(ins, [tuple(r[c] for c in cols) for r in rows], tag="hist_sync", fast=fast)
            total += len(rows)
    G.hist_exec(f"UPDATE [{t}] SET [_sync_seen_at]=GETDATE() WHERE [_sync_seen_at] IS NULL", audit=False)
    return total


def detect_void(meta):
    """Đánh _sync_deleted=1 cho dòng trong cửa sổ VOID_DAYS còn ở HIST nhưng ĐÃ MẤT ở KK
    (đơn hủy = xóa cứng). Ngoài cửa sổ coi là KK prune → giữ nguyên (là hợp lệ)."""
    t = meta["table"]
    days = int(getattr(settings, "PMV_HIST_VOID_DAYS", 60))
    if "CreatedDate" not in {c["name"] for c in meta["columns"]} or not meta["pk"]:
        return 0
    pk = meta["pk"][0]
    # PK các dòng KK trong cửa sổ
    kk = G.pmv_read(
        f"SELECT [{pk}] AS k FROM [{t}] WITH (NOLOCK) WHERE CreatedDate >= DATEADD(day, -?, GETDATE())",
        [days], tag="hist_sync", target="kk", audit=False)
    kk_keys = [r["k"] for r in kk]
    # nạp vào #void rồi UPDATE NOT EXISTS
    with G._hist_connect(autocommit=False) as cn:  # noqa: SLF001 - cùng gói
        cur = cn.cursor()
        cur.execute("CREATE TABLE #void (k nvarchar(64) PRIMARY KEY)")
        if kk_keys:
            cur.fast_executemany = True
            for i in range(0, len(kk_keys), 5000):
                cur.executemany("INSERT INTO #void (k) VALUES (?)",
                                [(str(x),) for x in kk_keys[i:i + 5000]])
        cur.execute(
            f"UPDATE h SET h.[_sync_deleted]=1 FROM [{t}] h "
            f"WHERE h.[_sync_deleted]=0 AND h.CreatedDate >= DATEADD(day, -{days}, GETDATE()) "
            f"AND NOT EXISTS (SELECT 1 FROM #void v WHERE v.k = CAST(h.[{pk}] AS nvarchar(64)))")
        n = cur.rowcount
        cur.execute("DROP TABLE #void")
        cn.commit()
    return n


def run_sync(tables=None, *, log=lambda s: None):
    """Đồng bộ INCREMENTAL toàn bộ (append+upsert+snapshot → con → void) + reconcile + ghi state."""
    ensure_database(); ensure_control()
    metas = HC.discover()
    if tables:
        want = {x.lower() for x in tables}
        metas = [m for m in metas if m["table"].lower() in want]
    for m in metas:
        ensure_schema(m)
    changed_map = {}
    report = []
    # 1) cha/log/snapshot
    for m in metas:
        if m["strategy"] == "child":
            continue
        t0 = time.monotonic()
        try:
            res = sync_one(m)
            if m["strategy"] == "upsert":
                changed_map[m["table"]] = res["changed"]
            vd = detect_void(m) if m["table"] in VOID_TABLES else 0
            rec = reconcile(m)
            ms = int((time.monotonic() - t0) * 1000)
            _save_state(m, rec, ms, note=(f"void {vd}" if vd else ""))
            report.append({"table": m["table"], **res, "void": vd, **rec, "ms": ms})
            _log_row(log, m["table"], res, rec, vd)
        except Exception as exc:
            _save_state(m, {}, int((time.monotonic() - t0) * 1000), note=f"LỖI: {exc}")
            log(f"  ✗  {m['table']:<32} LỖI: {str(exc)[:150]}")
            report.append({"table": m["table"], "error": str(exc)})
    # 2) con theo cha đổi
    for m in metas:
        if m["strategy"] != "child":
            continue
        t0 = time.monotonic()
        try:
            n = refresh_children(m, changed_map.get(m["parent"], set()))
            rec = reconcile(m)
            note = ""
            # TỰ CHỮA: con thiếu dòng (HIST < KK) — do lần chạy trước nhỡ cha mà không có con,
            # hoặc lỗi giữa chừng → chép lại trọn bảng con cho khớp KK hiện tại.
            if rec.get("rows_hist", 0) < rec.get("rows_kk", 0):
                mirror_schema(m)
                n = backfill(m)
                rec = reconcile(m)
                note = "tự chữa (backfill)"
            _save_state(m, rec, int((time.monotonic() - t0) * 1000), note=note)
            report.append({"table": m["table"], "child_refresh": n, **rec})
            _log_row(log, m["table"], {"ins": n, "upd": 0}, rec, 0, child=True)
        except Exception as exc:
            _save_state(m, {}, int((time.monotonic() - t0) * 1000), note=f"LỖI: {exc}")
            log(f"  ✗  {m['table']:<32} LỖI: {str(exc)[:150]}")
            report.append({"table": m["table"], "error": str(exc)})
    return report


def _log_row(log, t, res, rec, vd, child=False):
    healthy = rec.get("rows_hist", 0) >= rec.get("rows_kk", 0)   # HIST là superset → ≥ KK là lành
    flag = "OK " if healthy else "≠  "
    extra = f" +{res.get('ins',0)}/~{res.get('upd',0)}" + (f" void{vd}" if vd else "")
    log(f"  {flag}{t:<32} KK={rec.get('rows_kk','-')} HIST={rec.get('rows_hist','-')}{extra}")


def status():
    """Đọc bảng điều khiển cho trang/report."""
    ensure_database()
    ensure_control()
    return G.hist_query(
        f"SELECT table_name, strategy, rows_kk, rows_hist, is_match, last_run, last_ms, note "
        f"FROM [{CONTROL}] ORDER BY table_name")


def _safe(name):
    return "".join(ch if ch.isalnum() else "_" for ch in name)
