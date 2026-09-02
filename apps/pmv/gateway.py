"""
GATEWAY DUY NHẤT sang SQL Server PMV (PC KK — dữ liệu THẬT của phần mềm bán vàng).

RULE 1 (GĐ chốt 02/09/2026): tài khoản kimhanh2 là SYSADMIN nên SQL Server KHÔNG
tự bảo vệ được gì — hàng rào duy nhất là module này. Mọi lệnh sang PMV BẮT BUỘC
đi qua đây; KHÔNG import pyodbc ở bất kỳ file nào khác trong dự án.

3 kênh:
- pmv_read(sql)  : CHỈ SELECT. Bắt gặp từ khóa ghi/DDL/EXEC → PmvBlocked + audit
                   kind=BLOCKED (= "CẢNH BÁO VƯỢT QUYỀN" hiện đỏ trên trang trạng thái).
                   Đọc bảng dữ liệu PHẢI kèm WITH (NOLOCK) — 2 máy trạm đang bán hàng.
- pmv_admin(sql) : chỉ cho backup/verify/tiện ích thư mục theo _ADMIN_ALLOW.
- GHI nghiệp vụ (GĐ3+): sẽ mở kênh pmv_exec() với allowlist STORED PROC vendor
  (TRN_RT_BUYSELL_Ins, I_CUSTOMER_Ins...) — CHƯA MỞ, đừng tự thêm khi chưa có
  lệnh GĐ + sandbox đã kiểm chứng. Xem skill .claude/skills/pmv-proc-map/.

Cú pháp SQL phải tương thích SQL Server 2005 (compat 90): không MERGE, không kiểu
DATE — tham số ngày truyền CHUỖI ISO 'YYYY-MM-DD'.
"""
import re
import time

import pyodbc
from django.conf import settings


class PmvBlocked(Exception):
    """Lệnh bị gateway CHẶN vì ngoài allowlist. Sửa code gọi — KHÔNG nới gateway bừa."""


# Từ khóa không bao giờ được xuất hiện trong kênh ĐỌC
_WRITE_WORDS = re.compile(
    r"(?is)\b(INSERT|UPDATE|DELETE|MERGE|DROP|ALTER|CREATE|TRUNCATE|GRANT|REVOKE|DENY|"
    r"EXEC|EXECUTE|BACKUP|RESTORE|SHUTDOWN|RECONFIGURE|OPENROWSET|OPENQUERY|OPENDATASOURCE)\b"
    r"|\b(xp_|sp_OA)"
)

# Kênh QUẢN TRỊ: chỉ đúng các dạng lệnh phục vụ backup/khảo sát — không gì khác
_ADMIN_ALLOW = (
    re.compile(r"(?is)^\s*BACKUP\s+DATABASE\s+\[?PMV_BANLE_KH2\]?\s+TO\s+DISK"),
    re.compile(r"(?is)^\s*RESTORE\s+VERIFYONLY\s+FROM\s+DISK"),
    re.compile(r"(?is)^\s*EXEC\s+master\.(sys|dbo)\.xp_create_subdir\s"),
    re.compile(r"(?is)^\s*EXEC\s+master\.(sys|dbo)\.xp_dirtree\s"),
    re.compile(r"(?is)^\s*EXEC\s+master\.\.xp_fixeddrives"),
    re.compile(r"(?is)^\s*EXEC\s+master\.(sys|dbo)\.xp_delete_file\s"),
    # Nút SYNC: hút file .bak trong THƯ MỤC BACKUP của mình về qua kết nối SQL
    # (SQL KK đọc đĩa local của nó — không cần share, không cần đổi service account)
    re.compile(r"(?is)^\s*SELECT\s+BulkColumn\s+FROM\s+OPENROWSET\(\s*BULK\s+N'D:\\KHJ_PMV_BACKUP\\[^']+\.bak'\s*,\s*SINGLE_BLOB\s*\)"),
    re.compile(r"(?is)^\s*SELECT\b(?!.*OPENROWSET)"),
)


def _audit(kind, tag, summary, ok=True, ms=0, error=""):
    try:
        from .models import PmvAudit

        PmvAudit.objects.create(
            kind=kind, tag=tag, summary=summary[:500], ok=ok,
            duration_ms=ms, error=str(error)[:2000],
        )
    except Exception:
        pass  # audit hỏng (MySQL sập...) không được làm chết nghiệp vụ chính


def _connect(database=None, autocommit=False, timeout=15):
    conn_str = (
        f"DRIVER={{{settings.PMV_MSSQL_ODBC_DRIVER}}};"
        f"SERVER={settings.PMV_MSSQL_HOST};"
        f"DATABASE={database or settings.PMV_MSSQL_DB};"
        f"UID={settings.PMV_MSSQL_USER};"
        f"PWD={settings.PMV_MSSQL_PASSWORD};"
        "Encrypt=no;TrustServerCertificate=yes;Connection Timeout=10;"
    )
    return pyodbc.connect(conn_str, timeout=timeout, autocommit=autocommit)


def pmv_read(sql, params=(), *, tag, database=None, timeout=30, audit=True):
    """Chạy 1 câu SELECT trên PMV, trả list[dict]. Lệnh khác SELECT → PmvBlocked.
    audit=False: job lặp (collect 2 phút/lần) không ghi dòng READ thành công — lỗi vẫn ghi."""
    s = sql.strip()
    if not s.lower().startswith("select") or _WRITE_WORDS.search(s):
        _audit("BLOCKED", tag, f"VƯỢT QUYỀN (kênh đọc): {s[:400]}", ok=False)
        raise PmvBlocked(f"Gateway CHẶN (kênh đọc chỉ nhận SELECT): {s[:120]}")
    t0 = time.monotonic()
    try:
        with _connect(database=database, timeout=timeout) as cn:
            cur = cn.cursor()
            cur.execute(s, params)
            cols = [c[0] for c in cur.description] if cur.description else []
            rows = [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []
        if audit:
            _audit("READ", tag, s, ms=int((time.monotonic() - t0) * 1000))
        return rows
    except PmvBlocked:
        raise
    except Exception as exc:
        _audit("READ", tag, s, ok=False, ms=int((time.monotonic() - t0) * 1000), error=exc)
        raise


def pmv_admin(sql, params=(), *, tag, database="master", timeout=1800, fetch=False):
    """Kênh quản trị (backup/verify/tiện ích) — chỉ nhận lệnh khớp _ADMIN_ALLOW."""
    s = sql.strip()
    if not any(rx.match(s) for rx in _ADMIN_ALLOW):
        _audit("BLOCKED", tag, f"VƯỢT QUYỀN (kênh quản trị): {s[:400]}", ok=False)
        raise PmvBlocked(f"Gateway CHẶN (ngoài allowlist quản trị): {s[:120]}")
    t0 = time.monotonic()
    try:
        cn = _connect(database=database, autocommit=True, timeout=timeout)
        try:
            cur = cn.cursor()
            cur.execute(s, params)
            rows = []
            if fetch and cur.description:
                cols = [c[0] for c in cur.description]
                rows = [dict(zip(cols, r)) for r in cur.fetchall()]
            # BACKUP/RESTORE trả nhiều result set thông báo — phải rút hết mới xong lệnh
            while cur.nextset():
                pass
        finally:
            cn.close()
        _audit("ADMIN", tag, s, ms=int((time.monotonic() - t0) * 1000))
        return rows
    except PmvBlocked:
        raise
    except Exception as exc:
        _audit("ADMIN", tag, s, ok=False, ms=int((time.monotonic() - t0) * 1000), error=exc)
        raise


# ---------------------------------------------------------------------------
# Kênh GỌI PROC (Track A3, 03/09/2026). 2 danh sách:
#   PROC_READ_ALLOW : proc CHỈ ĐỌC của vendor (Get/Lst) — được gọi bất cứ lúc nào.
#   PROC_WRITE_ALLOW: proc GHI — mở dần theo Track B, mỗi dòng ghi ngày GĐ duyệt.
# Proc ngoài 2 danh sách → BLOCKED. Ghi khi pmv_write_lock=1 → BLOCKED + cảnh báo.
# ---------------------------------------------------------------------------
PROC_READ_ALLOW = frozenset({
    "TRN_RT_BUYSELL_Get", "TRN_RT_BUYSELL_Lst", "TRN_RT_BUYSELL_SELL_Lst",
    "TRN_RT_BUYGOLD_Get", "TRN_RT_BUYGOLD_Lst",
    "T_PRODUCT_GetByCodeForSell", "T_PRODUCT_GetByCodeForOut",
    "I_CUSTOMER_Lst", "I_XRATE_GetAll", "I_XRATE_Lst", "I_XRATE_HIST_Lst", "I_GOLD_GetAll", "I_GOLDCCY_GetAll",
    "T_TILL_TXN_DETAIL_GetByTrnID", "T_CUSTOMER_DEBT_Lst", "SYS_LOADCOMBO", "T_TILL_GetBySYS_USERS",
})
PROC_WRITE_ALLOW = frozenset({
    # (chưa mở — B1 sẽ thêm "I_XRATE_Ins" sau khi sandbox + GĐ duyệt)
})
_PROC_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def pmv_call(proc, params=None, *, tag, write=False, timeout=120):
    """Gọi 1 stored proc vendor trên PMV THẬT với tham số ĐẶT TÊN.
    Trả (rc, result_sets): rc = giá trị RETURN của proc (0 = OK theo quy ước vendor),
    result_sets = list[list[dict]]. Proc phải nằm trong allowlist; write=True mới được
    gọi proc ghi (và bị khóa khi pmv_write_lock=1)."""
    params = dict(params or {})
    if not _PROC_NAME_RE.match(proc or ""):
        _audit("BLOCKED", tag, f"VƯỢT QUYỀN (tên proc lạ): {proc!r}", ok=False)
        raise PmvBlocked(f"Tên proc không hợp lệ: {proc!r}")
    allowed = PROC_WRITE_ALLOW if write else (PROC_READ_ALLOW | PROC_WRITE_ALLOW)
    if proc not in allowed:
        _audit("BLOCKED", tag, f"VƯỢT QUYỀN (proc ngoài allowlist {'GHI' if write else 'ĐỌC'}): {proc} {list(params)}", ok=False)
        raise PmvBlocked(f"Gateway CHẶN: proc {proc} chưa được duyệt cho kênh {'GHI' if write else 'ĐỌC'}")
    if write:
        from .models import PmvState

        if PmvState.get("pmv_write_lock") == "1":
            _audit("BLOCKED", tag, f"KHÓA GHI đang bật (vendor đổi version) — từ chối {proc}", ok=False)
            raise PmvBlocked("pmv_write_lock=1 — vendor vừa đổi version DB, kiểm tra lại bản đồ proc trước khi ghi")
    for k in params:
        if not _PROC_NAME_RE.match(k):
            _audit("BLOCKED", tag, f"VƯỢT QUYỀN (tên tham số lạ): {proc} {k!r}", ok=False)
            raise PmvBlocked(f"Tên tham số không hợp lệ: {k!r}")
    sql = _proc_sql(proc, params)
    summary = f"EXEC {proc} " + ", ".join(f"@{k}={_short(v)}" for k, v in params.items())
    t0 = time.monotonic()
    try:
        cn = _connect(autocommit=True, timeout=timeout)
        try:
            cur = cn.cursor()
            cur.execute(sql, list(params.values()))
            rc, sets = _drain(cur)
        finally:
            cn.close()
        _audit("EXEC", tag, summary + f" → rc={rc}", ok=(rc == 0), ms=int((time.monotonic() - t0) * 1000))
        return rc, sets
    except PmvBlocked:
        raise
    except Exception as exc:
        _audit("EXEC", tag, summary, ok=False, ms=int((time.monotonic() - t0) * 1000), error=exc)
        raise


def _proc_sql(proc, params):
    named = ", ".join(f"@{k}=?" for k in params)
    return f"DECLARE @__rc INT; EXEC @__rc = [{proc}] {named}; SELECT @__rc AS __rc"


def _drain(cur):
    """Rút mọi result set; set cuối là RETURN value."""
    sets = []
    while True:
        if cur.description:
            cols = [c[0] for c in cur.description]
            sets.append([dict(zip(cols, r)) for r in cur.fetchall()])
        if not cur.nextset():
            break
    rc = None
    if sets and sets[-1] and "__rc" in sets[-1][0]:
        rc = sets.pop()[0]["__rc"]
    return rc, sets


def _short(v, n=60):
    s = str(v)
    return (s[:n] + "…") if len(s) > n else s


# Kênh TRACE (theo dõi hành vi PMVGoldRT): server-side trace của SQL Server — CHỈ các
# lệnh sp_trace_* đúng khuôn, file trace khóa trong D:\KHJ_PMV_BACKUP\trace\.
_TRACE_STMT_ALLOW = (
    re.compile(r"(?is)^DECLARE\s+@tid\s+INT$"),
    re.compile(r"(?is)^DECLARE\s+@maxsize\s+BIGINT$"),
    re.compile(r"(?is)^SET\s+@maxsize\s*=\s*\d{1,3}$"),
    re.compile(r"(?is)^EXEC\s+sp_trace_create\s+@tid\s+OUTPUT\s*,\s*2\s*,\s*N'D:\\KHJ_PMV_BACKUP\\trace\\[A-Za-z0-9_]+'\s*,\s*@maxsize\s*,\s*NULL\s*,\s*\d{1,2}$"),
    re.compile(r"(?is)^DECLARE\s+@on\s+BIT$"),
    re.compile(r"(?is)^SET\s+@on\s*=\s*1$"),
    re.compile(r"(?is)^EXEC\s+sp_trace_setevent\s+@tid\s*,\s*\d{1,3}\s*,\s*\d{1,3}\s*,\s*@on$"),
    re.compile(r"(?is)^EXEC\s+sp_trace_setfilter\s+@tid\s*,\s*\d{1,3}\s*,\s*[01]\s*,\s*\d\s*,\s*N'[A-Za-z0-9_%]*'$"),
    re.compile(r"(?is)^EXEC\s+sp_trace_setstatus\s+(?:@tid|\d{1,4})\s*,\s*[012]$"),
    re.compile(r"(?is)^SELECT\s+@tid\s+AS\s+tid$"),
)


def pmv_trace_batch(sql, *, tag):
    """Chạy 1 batch sp_trace_* (bật/tắt trace hành vi). Từng câu lệnh phải khớp khuôn."""
    stmts = [x.strip() for x in sql.split(";") if x.strip()]
    for st in stmts:
        if not any(rx.match(st) for rx in _TRACE_STMT_ALLOW):
            _audit("BLOCKED", tag, f"VƯỢT QUYỀN (kênh trace): {st[:400]}", ok=False)
            raise PmvBlocked(f"Gateway CHẶN (ngoài khuôn trace): {st[:120]}")
    t0 = time.monotonic()
    try:
        cn = _connect(database="master", autocommit=True, timeout=60)
        try:
            cur = cn.cursor()
            cur.execute(";\n".join(stmts))
            rows = []
            while True:
                if cur.description:
                    cols = [c[0] for c in cur.description]
                    rows = [dict(zip(cols, r)) for r in cur.fetchall()]
                if not cur.nextset():
                    break
        finally:
            cn.close()
        _audit("ADMIN", tag, sql, ms=int((time.monotonic() - t0) * 1000))
        return rows
    except PmvBlocked:
        raise
    except Exception as exc:
        _audit("ADMIN", tag, sql, ok=False, ms=int((time.monotonic() - t0) * 1000), error=exc)
        raise


def canh_bao(tag, message):
    """Ghi 1 dòng cảnh báo vàng vào nhật ký (hiện trên trang trạng thái)."""
    _audit("CANHBAO", tag, message, ok=False)
