"""
GATEWAY DUY NHẤT sang SQL Server PMV (PC KK — dữ liệu THẬT của phần mềm bán vàng).

RULE 1 (GĐ chốt 02/09/2026): tài khoản kimhanh2 là SYSADMIN nên SQL Server KHÔNG
tự bảo vệ được gì — hàng rào duy nhất là module này. Mọi lệnh sang PMV BẮT BUỘC
đi qua đây; KHÔNG import pyodbc ở bất kỳ file nào khác trong dự án.

HAI ĐÍCH (chốt 03/09/2026) — settings.PMV_TARGET:
- "sandbox": bản sao trên máy Mr Giang (mặc định). Tha hồ ghi, sai thì bấm SYNC làm lại.
- "kk"     : máy KK, DỮ LIỆU THẬT của tiệm.
CHỐT AN TOÀN settings.PMV_GHI_KK (mặc định False): khi TẮT, gateway TỪ CHỐI mọi lệnh
GHI có đích là KK — dù proc nằm trong allowlist, dù người gọi truyền write=True. Chốt
đặt Ở ĐÂY chứ không ở người gọi, để không ai quên được. Backup / kiểm tra / trace /
đồng bộ vẫn luôn nói chuyện với KK (chỉ đọc + allowlist quản trị), không theo cờ này.

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
from decimal import Decimal

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
    # NGOẠI LỆ RULE 2 — GĐ duyệt 05/09/2026: dọn ngày sinh khách (app PMVGoldRT ghi mặc định = ngày tạo hồ sơ,
    # 56.392/56.401 khách sai). ĐÚNG 1 CỘT, ĐÚNG 1 KHUÔN, chỉ dòng > 2010-01-01 → '1900-01-01' (giá trị default của
    # cột). Chạy bằng manage.py sua_ngay_sinh_khach (không có nút web), theo lô TOP(n). Không sửa khuôn này để
    # "cho tiện" — mọi UPDATE khác vẫn bị CHẶN.
    re.compile(r"(?is)^\s*UPDATE\s+(?:TOP\s*\(\s*\d{1,5}\s*\)\s+)?I_CUSTOMER\s+SET\s+BirthDate\s*=\s*'1900-01-01'\s+"
               r"WHERE\s+BirthDate\s*>\s*CAST\('2010-01-01'\s+AS\s+datetime\)\s*;?\s*$"),
)
SUA_NGAY_SINH_SQL = "UPDATE TOP ({lo}) I_CUSTOMER SET BirthDate = '1900-01-01' WHERE BirthDate > CAST('2010-01-01' AS datetime)"


def _audit(kind, tag, summary, ok=True, ms=0, error=""):
    try:
        from .models import PmvAudit

        PmvAudit.objects.create(
            kind=kind, tag=tag, summary=summary[:500], ok=ok,
            duration_ms=ms, error=str(error)[:2000],
        )
    except Exception:
        pass  # audit hỏng (MySQL sập...) không được làm chết nghiệp vụ chính


DICH_KEY = "pmv_target"          # công tắc chạy-nóng, đặt trên trang Hệ thống
_DICH_TTL = 2.0                  # giây — gateway bị gọi liên tục, không hỏi DB mỗi lần
_dich_cache = {"gia": None, "luc": 0.0}


def _chuan_dich(d):
    d = str(d or "").strip().lower()
    return "kk" if d in ("kk", "pmv", "that", "thật", "real") else "sandbox"


def dich_hien_tai():
    """Đích dữ liệu nghiệp vụ đang chọn: "sandbox" hay "kk".

    Thứ tự ưu tiên: CÔNG TẮC trên trang Hệ thống (PmvState) > .env PMV_TARGET.
    Công tắc để đổi được ngay lúc đang chạy, không phải sửa file rồi RESET; .env là
    giá trị mặc định lúc khởi động sạch."""
    now = time.monotonic()
    if _dich_cache["gia"] is not None and now - _dich_cache["luc"] < _DICH_TTL:
        return _dich_cache["gia"]
    cong_tac = ""
    try:
        from .models import PmvState

        cong_tac = PmvState.get(DICH_KEY, "")
    except Exception:
        cong_tac = ""      # MySQL sập thì rơi về .env, không làm chết nghiệp vụ
    d = _chuan_dich(cong_tac or getattr(settings, "PMV_TARGET", "sandbox"))
    _dich_cache.update(gia=d, luc=now)
    return d


def dat_dich(d):
    """Bật công tắc sang đích mới (ghi PmvState + xóa cache). Trả đích đã chuẩn hóa."""
    from .models import PmvState

    d = _chuan_dich(d)
    PmvState.set(DICH_KEY, d)
    _dich_cache.update(gia=d, luc=time.monotonic())
    return d


def xoa_cong_tac():
    """Bỏ công tắc, trả quyền quyết định về .env."""
    from .models import PmvState

    PmvState.objects.filter(key=DICH_KEY).delete()
    _dich_cache.update(gia=None, luc=0.0)
    return dich_hien_tai()


def duoc_ghi_kk():
    """Chốt an toàn: có được phép GHI vào máy KK không (mặc định KHÔNG)."""
    return bool(getattr(settings, "PMV_GHI_KK", False))


def mo_ta_dich(target=None):
    """Chuỗi ngắn cho băng cảnh báo + nhật ký."""
    t = target or dich_hien_tai()
    if t == "hist":
        return "kho lịch sử (PMV_KH2_HIST)"
    return "máy KK (DỮ LIỆU THẬT)" if t == "kk" else "máy Mr Giang (bản thử)"


def _connect_dich(target, database=None, autocommit=False, timeout=15):
    """Mở kết nối tới ĐÍCH chỉ định. KK: tài khoản SQL. Sandbox/HIST: tài khoản Windows.
    "hist" = kho lịch sử PMV_KH2_HIST (Phase 4: đọc quá khứ + failover) — CHỈ ĐỌC qua đường này."""
    if target == "hist":
        return _hist_connect(database=database, autocommit=autocommit, timeout=timeout)
    if target == "sandbox":
        if not settings.PMV_LOCAL_MSSQL:
            raise PmvBlocked("PMV_TARGET=sandbox nhưng PMV_LOCAL_MSSQL đang trống trong .env")
        conn_str = (
            f"DRIVER={{{settings.PMV_MSSQL_ODBC_DRIVER}}};"
            f"SERVER={settings.PMV_LOCAL_MSSQL};"
            f"DATABASE={database or settings.PMV_SANDBOX_DB};"
            "Trusted_Connection=yes;Encrypt=no;TrustServerCertificate=yes;Connection Timeout=10;"
        )
        return pyodbc.connect(conn_str, timeout=timeout, autocommit=autocommit)
    return _connect(database=database, autocommit=autocommit, timeout=timeout)


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


def pmv_read(sql, params=(), *, tag, database=None, timeout=30, audit=True, target=None, query_timeout=None):
    """Chạy 1 câu SELECT, trả list[dict]. Lệnh khác SELECT → PmvBlocked.
    target: None = MÁY KK (mặc định — hạ tầng backup/kiểm tra/trace/đồng bộ luôn đọc KK);
            "sandbox"/"kk" = chỉ định rõ (PmvClient truyền theo PMV_TARGET).
    audit=False: job lặp (collect 2 phút/lần) không ghi dòng READ thành công — lỗi vẫn ghi."""
    target = target or "kk"
    s = sql.strip()
    if not s.lower().startswith("select") or _WRITE_WORDS.search(s):
        _audit("BLOCKED", tag, f"VƯỢT QUYỀN (kênh đọc): {s[:400]}", ok=False)
        raise PmvBlocked(f"Gateway CHẶN (kênh đọc chỉ nhận SELECT): {s[:120]}")
    t0 = time.monotonic()
    try:
        with _connect_dich(target, database=database, timeout=timeout) as cn:
            if query_timeout is not None:
                cn.timeout = query_timeout
            cur = cn.cursor()
            cur.execute(s, params)
            cols = [c[0] for c in cur.description] if cur.description else []
            rows = [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []
        if audit:
            _audit("READ", tag, f"[{target}] {s}", ms=int((time.monotonic() - t0) * 1000))
        return rows
    except PmvBlocked:
        raise
    except Exception as exc:
        _audit("READ", tag, f"[{target}] {s}", ok=False,
               ms=int((time.monotonic() - t0) * 1000), error=exc)
        raise


_CUSTOMER_IMAGE_PATH = re.compile(
    r"(?i)^D:\\PHANMEMVANG\\HINHANHKH\\[A-Za-z0-9][A-Za-z0-9_. -]{0,180}\.(?:jpe?g|png)$"
)


def pmv_image_read(path, *, tag, target=None, timeout=30):
    """Đọc một ảnh khách từ đĩa local của SQL Server qua kênh chỉ đọc riêng.

    ``OPENROWSET(BULK)`` không nhận tham số cho tên tệp. Vì vậy đường dẫn phải khớp
    tuyệt đối thư mục ảnh PMV và bộ ký tự tên tệp an toàn trước khi ghép vào SQL.
    Hàm không nhận thư mục khác, dấu nháy, đường dẫn tương đối hoặc tên có ``..``.
    """
    target = target or "kk"
    path = str(path or "").strip()
    if ".." in path or not _CUSTOMER_IMAGE_PATH.fullmatch(path):
        _audit("BLOCKED", tag, "Đường dẫn ảnh khách không hợp lệ", ok=False)
        raise PmvBlocked("Đường dẫn ảnh khách nằm ngoài thư mục PMV được phép đọc")
    sql = f"SELECT BulkColumn FROM OPENROWSET(BULK N'{path}', SINGLE_BLOB) AS img"
    t0 = time.monotonic()
    try:
        with _connect_dich(target, timeout=timeout) as cn:
            cur = cn.cursor()
            cur.execute(sql)
            row = cur.fetchone()
        data = bytes(row[0]) if row and row[0] is not None else b""
        _audit("READ", tag, f"[{target}] ảnh khách {path}",
               ms=int((time.monotonic() - t0) * 1000))
        return data
    except Exception as exc:
        _audit("READ", tag, f"[{target}] ảnh khách {path}", ok=False,
               ms=int((time.monotonic() - t0) * 1000), error=exc)
        raise


def pmv_admin(sql, params=(), *, tag, database="master", timeout=1800, fetch=False, rowcount=False):
    """Kênh quản trị (backup/verify/tiện ích) — chỉ nhận lệnh khớp _ADMIN_ALLOW.
    rowcount=True: trả số dòng bị ảnh hưởng (cho khuôn UPDATE ngoại lệ) thay cho list rows."""
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
            n = cur.rowcount
            rows = []
            if fetch and cur.description:
                cols = [c[0] for c in cur.description]
                rows = [dict(zip(cols, r)) for r in cur.fetchall()]
            # BACKUP/RESTORE trả nhiều result set thông báo — phải rút hết mới xong lệnh
            while cur.nextset():
                pass
        finally:
            cn.close()
        _audit("ADMIN", tag, s + (f" → {n} dòng" if rowcount else ""), ms=int((time.monotonic() - t0) * 1000))
        return n if rowcount else rows
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
# Mỗi dòng GHI ghi rõ ngày kiểm chứng. ĐÃ chạy thật trên sandbox 03/09/2026 (tạo khách →
# lập hóa đơn → chốt → hủy sạch, dấu vết trùng khớp 11 bảng mà app desktop tạo).
PROC_WRITE_ALLOW = frozenset({
    # 11/09/2026: CRUD phiếu cọc W theo yêu cầu trang ĐẶT-CỌC; kiểm chứng sandbox.
    "TRN_DATCOC_Ins", "TRN_DATCOC_Upd", "TRN_DATCOC_Del",
    # 11/09/2026 GĐ duyệt thu/hoàn/cấn cọc: smoke_datcoc_money PASS, source KK=bản thử,
    # COPY_ONLY + VERIFY DATCOC_BEFORE_20260911_100542 trước mở. Vẫn qua chốt ghi/khóa hiện hữu.
    "TRN_DATCOC_Complete", "TRN_RT_BUYSELL_DatCoc_Ins",
    "I_XRATE_Ins",                                         # 06/09/2026 — cập nhật giá MySQL → MSSQL, smoke_price_sync
    "I_CUSTOMER_Ins", "I_CUSTOMER_Upd",                       # 03/09/2026
    "I_DiemTichLuy_InsFromGT",                                 # 05/09/2026
    "I_CUSTOMER_Del",                                          # 08/09/2026 — xóa khách CHƯA giao dịch, guard bán+thâu ở customer.delete, kiểm sandbox 08/09
    "TRN_RT_BUYSELL_Ins", "TRN_RT_BUYSELL_Upd",               # 03/09/2026
    "TRN_RT_BUYSELL_Complete",                                # 03/09/2026
    "TRN_RT_BUYSELL_Del", "TRN_RT_BUYGOLD_Del", "T_TILL_TXN_Proc", "T_TILL_TXN_Del",# 03/09/2026
    # THAU_VANG (08/09/2026, GĐ chốt 3 điểm): lập/sửa/chốt phiếu thâu độc lập; CARDPAY_Ins chỉ với DS thẻ RỖNG
    # (ghi CashPay/CardPay + TRN_TILL_TXN_Upd sửa dòng VND két) — kiểm sandbox smoke_thau 08/09
    "TRN_RT_BUYGOLD_Ins", "TRN_RT_BUYGOLD_Upd", "TRN_RT_BUYGOLD_CompleteMore", "CARDPAY_Ins",
})
_PROC_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def pmv_call(proc, params=None, *, tag, write=False, timeout=120, target=None):
    """Gọi 1 stored proc vendor với tham số ĐẶT TÊN.
    Trả (rc, result_sets): rc = giá trị RETURN của proc (0 = OK theo quy ước vendor),
    result_sets = list[list[dict]]. Proc phải nằm trong allowlist; write=True mới được
    gọi proc ghi (và bị khóa khi pmv_write_lock=1).
    target: None = theo settings.PMV_TARGET. Ghi vào KK còn phải qua CHỐT AN TOÀN."""
    params = dict(params or {})
    target = target or dich_hien_tai()
    if target == "hist":   # kho lịch sử không có proc vendor và không bao giờ nhận lệnh ghi nghiệp vụ
        _audit("BLOCKED", tag, f"VƯỢT QUYỀN (gọi proc trên kho lịch sử): {proc}", ok=False)
        raise PmvBlocked("Kho lịch sử chỉ đọc bằng SELECT — không gọi proc")
    if not _PROC_NAME_RE.match(proc or ""):
        _audit("BLOCKED", tag, f"VƯỢT QUYỀN (tên proc lạ): {proc!r}", ok=False)
        raise PmvBlocked(f"Tên proc không hợp lệ: {proc!r}")
    allowed = PROC_WRITE_ALLOW if write else (PROC_READ_ALLOW | PROC_WRITE_ALLOW)
    if proc not in allowed:
        _audit("BLOCKED", tag, f"VƯỢT QUYỀN (proc ngoài allowlist {'GHI' if write else 'ĐỌC'}): {proc} {list(params)}", ok=False)
        raise PmvBlocked(f"Gateway CHẶN: proc {proc} chưa được duyệt cho kênh {'GHI' if write else 'ĐỌC'}")
    if write:
        # ══ CHỐT AN TOÀN — KHÔNG ĐƯỢC GỠ ══
        # Ghi vào máy KK là chạm dữ liệu THẬT của tiệm. Chốt nằm ở đây (không ở người
        # gọi) nên dù view/lệnh/test nào quên kiểm tra thì vẫn không lọt được.
        if target == "kk" and not duoc_ghi_kk():
            _audit("BLOCKED", tag,
                   f"CHẶN GHI VÀO MÁY KK (PMV_GHI_KK=False): {proc} {list(params)}", ok=False)
            raise PmvBlocked(
                f"Gateway CHẶN: cấm GHI vào máy KK. Proc {proc} bị từ chối vì PMV_GHI_KK=False. "
                "Muốn ghi thật phải được GĐ duyệt, đổi .env rồi RESET_KHBL.bat."
            )
        from .models import PmvState

        if PmvState.get("pmv_write_lock") == "1":
            _audit("BLOCKED", tag, f"KHÓA GHI đang bật (vendor đổi version) — từ chối {proc}", ok=False)
            raise PmvBlocked("pmv_write_lock=1 — vendor vừa đổi version DB, kiểm tra lại bản đồ proc trước khi ghi")
    for k in params:
        if not _PROC_NAME_RE.match(k):
            _audit("BLOCKED", tag, f"VƯỢT QUYỀN (tên tham số lạ): {proc} {k!r}", ok=False)
            raise PmvBlocked(f"Tên tham số không hợp lệ: {k!r}")
    sql, sql_values = _proc_sql(proc, params)
    summary = f"[{target}] EXEC {proc} " + ", ".join(f"@{k}={_short(v)}" for k, v in params.items())
    t0 = time.monotonic()
    try:
        cn = _connect_dich(target, autocommit=True, timeout=timeout)
        try:
            cur = cn.cursor()
            cur.execute(sql, sql_values)
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


def pmv_deposit_money(expected, cash, bank, *, till_id, tag, target):
    """12/09/2026 GĐ duyệt ghi trực tiếp 3 cột tiền cọc, không qua CARDPAY_Ins.

    Không nhận SQL tùy ý. Khóa/đối chiếu phiếu, liên kết và quỹ trong cùng transaction.
    Chỉ ghi khi quỹ mới U bằng tổng cọc, chưa có phân bổ. Giữ tổng cọc cố định.
    Gọi đúng nhánh TDC của proc quỹ để phần tiền mặt khớp trước khi chốt két.
    """
    from .models import PmvState
    cash, bank = Decimal(str(cash)), Decimal(str(bank))
    pk = expected.get('TrnID', '')
    summary = f'[{target}] DATCOC money {pk}: TM={cash}, CK={bank}'
    if target not in ('kk', 'sandbox') or (target == 'kk' and not duoc_ghi_kk()) or PmvState.get('pmv_write_lock', '0') == '1':
        _audit('BLOCKED', tag, summary, ok=False)
        raise PmvBlocked('Đích hoặc chốt ghi PMV không cho phép cập nhật tiền cọc.')
    if not re.fullmatch(r'TDC\d{12}', pk) or not till_id:
        raise ValueError('Cần mã TDC và két thu hợp lệ.')
    if any(not n.is_finite() or n < 0 or n >= Decimal('1000000000000000') for n in (cash, bank, cash + bank)):
        raise ValueError('Số tiền cọc ngoài giới hạn.')
    if cash + bank <= 0 or cash + bank != Decimal(expected['TienCoc']):
        raise ValueError('Tiền mặt + CK phải bằng tổng cọc cố định.')
    keys = ('TrnID', 'BillCode', 'TrnDate', 'TrnDateTime_Upd', 'CustID', 'ShopID', 'Status', 'TienCoc', 'CashPay', 'CardPay')
    t0 = time.monotonic()
    cn = None
    try:
        cn = _connect_dich(target, autocommit=False)
        cur = cn.cursor()
        cur.execute('SELECT ' + ','.join(keys) + ' FROM TRN_DATCOC WITH (UPDLOCK,HOLDLOCK) WHERE TrnID=?', (pk,))
        rows = cur.fetchall()
        if len(rows) != 1 or dict(zip(keys, rows[0])) != {k: expected[k] for k in keys}:
            raise ValueError('Phiếu vừa thay đổi; chưa cập nhật tiền cọc.')
        h = dict(zip(keys, rows[0]))
        if h['Status'] != 'P' or not h['ShopID']:
            raise ValueError('Chỉ cập nhật phiếu đã thu, có cửa hàng PMV.')
        for table in ('TRN_RT_BUYSELL_DatCoc', 'TRN_RT_CHANGE_DatCoc'):
            cur.execute(f'SELECT TrnID FROM {table} WITH (UPDLOCK,HOLDLOCK) WHERE DatCocID=?', (pk,))
            if cur.fetchone():
                raise ValueError('Phiếu đã liên kết hóa đơn; không tự đổi phân bổ cọc.')
        if (h['CashPay'] or 0) != 0 or (h['CardPay'] or 0) != 0:
            raise ValueError('Phiếu đã có phân bổ tiền; không ghi lại.')
        cur.execute('SELECT TillTxnID,TillID,Status,TrnTotalAmount FROM T_TILL_TXN WITH (UPDLOCK,HOLDLOCK) WHERE TrnRefID=?', (pk,))
        tx = cur.fetchall()
        if len(tx) != 1 or tx[0][2] != 'U' or tx[0][3] != cash + bank:
            raise ValueError('Quỹ cọc không khớp tiền mặt/két thu.')
        cur.execute('SELECT Amount,CrDr,GoldCcy FROM T_TILL_TXN_DETAIL WITH (UPDLOCK,HOLDLOCK) WHERE TillTxnID=?', (tx[0][0],))
        detail = cur.fetchall()
        if len(detail) != 1 or tuple(detail[0]) != (cash + bank, '+', 'VND'):
            raise ValueError('Chi tiết quỹ cọc không khớp.')
        cur.execute('UPDATE TRN_DATCOC SET CashPay=?,CardPay=?,TienCoc=?,TrnDateTime_Upd=GETDATE() WHERE TrnID=?',
                    (cash, bank, cash + bank, pk))
        if cur.rowcount != 1:
            raise ValueError('Chưa cập nhật đúng một phiếu cọc.')
        cur.execute('SELECT CashPay,CardPay,TienCoc FROM TRN_DATCOC WHERE TrnID=?', (pk,))
        if tuple(cur.fetchone()) != (cash, bank, cash + bank):
            raise ValueError('Đọc lại tiền cọc không khớp.')
        if bank:
            # Chỉ nhánh TDC, trong cùng transaction/khóa. Không mở proc này cho SQL tùy ý.
            cur.execute("DECLARE @rc int, @err varchar(100); EXEC @rc=TRN_TILL_TXN_Upd "
                        "@p_TrnID=?,@p_TrnType='TDC',@p_TrnCode='TDC',@p_ErrCode=@err OUTPUT; "
                        "SELECT @rc AS __rc, @err AS err", (pk,))
            rc, _ = _drain(cur)
            if rc != 0: raise ValueError('PMV không cập nhật được phần tiền mặt của quỹ cọc.')
        cur.execute('SELECT TrnTotalAmount,Status FROM T_TILL_TXN WHERE TillTxnID=?', (tx[0][0],))
        if tuple(cur.fetchone()) != (cash, 'U'):
            raise ValueError('Quỹ chưa khớp tiền mặt sau phân bổ.')
        cur.execute('SELECT Amount,CrDr,GoldCcy FROM T_TILL_TXN_DETAIL WHERE TillTxnID=?', (tx[0][0],))
        if tuple(cur.fetchone()) != (cash, '+', 'VND'):
            raise ValueError('Chi tiết quỹ chưa khớp tiền mặt sau phân bổ.')
        cn.commit()
        _audit('EXEC', tag, summary, ms=int((time.monotonic() - t0) * 1000))
    except Exception as exc:
        if cn is not None: cn.rollback()
        _audit('EXEC', tag, summary, ok=False, error=exc, ms=int((time.monotonic() - t0) * 1000))
        raise
    finally:
        if cn is not None: cn.close()


def pmv_deposit_zalo(trn_id, next_day, number, *, target, tag='datcoc-zalo'):
    """Mốc nhắc được duyệt: chỉ Description + dấu phiên bản; không gọi proc tài chính."""
    import datetime as dt
    from .models import PmvState
    from apps.pos.deposit_orders import merge_zalo
    if target not in ('kk','sandbox') or (target=='kk' and not duoc_ghi_kk()) or PmvState.get('pmv_write_lock','0')=='1':
        raise PmvBlocked('Chốt ghi PMV đang đóng; mốc nhắc được giữ tại MySQL.')
    if not isinstance(trn_id,str) or not re.fullmatch(r'[A-Za-z0-9-]{1,20}',trn_id): raise ValueError('Mã phiếu không hợp lệ.')
    day=dt.date.fromisoformat(next_day).isoformat()
    if not isinstance(number,int) or not 1<=number<=99999: raise ValueError('Số lần nhắc không hợp lệ.')
    cn=None
    try:
        cn=_connect_dich(target,autocommit=False); cur=cn.cursor()
        cur.execute('SELECT Description FROM TRN_DATCOC WITH (UPDLOCK,HOLDLOCK) WHERE TrnID=?',(trn_id,))
        row=cur.fetchone()
        if row is None: raise ValueError('Phiếu không còn tồn tại.')
        description=merge_zalo(row[0],{day:f'nhắc lần {number}'})
        if description!=row[0]:
            cur.execute('UPDATE TRN_DATCOC SET Description=?,TrnDateTime_Upd=GETDATE() WHERE TrnID=?',(description,trn_id))
            if cur.rowcount!=1: raise ValueError('Chưa cập nhật đúng một phiếu.')
            cur.execute('SELECT Description FROM TRN_DATCOC WHERE TrnID=?',(trn_id,))
            if cur.fetchone()[0]!=description: raise ValueError('Đọc lại mốc Zalo không khớp.')
        cn.commit()
        _audit('EXEC',tag,f'[{target}] DATCOC Zalo {trn_id}: {day} lần {number}')
    except Exception as exc:
        if cn is not None: cn.rollback()
        _audit('EXEC',tag,f'[{target}] DATCOC Zalo {trn_id}',ok=False,error=exc)
        raise
    finally:
        if cn is not None: cn.close()


def _proc_sql(proc, params):
    # pyodbc có thể mô tả một marker mang giá trị None là varchar. SQL Server 2005
    # trên máy KK không cho đổi ngầm varchar -> varbinary(max), dù cùng lời gọi trên
    # SQL Express sandbox vẫn chấp nhận. Ép kiểu tại marker để NULL và bytes đều có
    # đúng kiểu trước khi vào proc SaveImageToFile.
    binary_names = {"p_imagedata", "p_imagedatamattruoc", "p_imagedatamatsau"}
    binary = [(k, v) for k, v in params.items() if k.lower() in binary_names]
    regular = [(k, v) for k, v in params.items() if k.lower() not in binary_names]
    declarations = []
    binary_vars = {}
    binary_values = []
    for index, (name, value) in enumerate(binary):
        var = f"@__bin{index}"
        binary_vars[name] = var
        # SQL Server 2005 không hỗ trợ DECLARE @x type = value.
        declarations.append(f"DECLARE {var} VARBINARY(MAX)")
        if value is None:
            # Không dùng marker cho NULL: pyodbc mô tả None là varchar trên máy KK,
            # và SQL Server từ chối cả phép gán varchar NULL -> varbinary(max).
            declarations.append(f"SET {var}=NULL")
        else:
            declarations.append(f"SET {var}=?")
            binary_values.append(value)
    named = ", ".join(
        f"@{k}={binary_vars[k]}" if k in binary_vars else f"@{k}=?" for k in params
    )
    statements = declarations + ["DECLARE @__rc INT", f"EXEC @__rc = [{proc}] {named}",
                                  "SELECT @__rc AS __rc"]
    values = binary_values + [v for _, v in regular]
    return "; ".join(statements), values


def _dedupe_cols(cols):
    """Proc vendor có result set TRÙNG TÊN CỘT (T_PRODUCT_GetByCodeForSell: GoldReal,
    WeightUnit, InPrice mỗi cái 2 lần) — dict() thường để lần sau ĐÈ lần đầu, mà lần sau
    hay là hằng 0 → mất giá trị thật. Giữ lần đầu tên trần, lần sau thêm __2, __3."""
    seen, out = {}, []
    for c in cols:
        seen[c] = seen.get(c, 0) + 1
        out.append(c if seen[c] == 1 else f"{c}__{seen[c]}")
    return out


def _drain(cur):
    """Rút mọi result set; set cuối là RETURN value."""
    sets = []
    while True:
        if cur.description:
            cols = _dedupe_cols([c[0] for c in cur.description])
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


# ═══════════════ KHO LỊCH SỬ (GIANG MSSQL) — kênh RIÊNG, KHÔNG chạm KK ═══════════════
# DB của CHÍNH MÌNH trên localhost\SQL2014 (settings.PMV_HIST_*). Vì là kho backup do ta
# sở hữu nên được DDL/DML tự do — nhưng các hàm dưới CHỈ kết nối tới PMV_HIST_*, tuyệt đối
# không bao giờ tới máy KK (206). Đọc KK vẫn qua pmv_read(target="kk") chỉ-đọc như cũ.

def _hist_connect(database=None, autocommit=False, timeout=30):
    """Kết nối instance kho lịch sử. USER trống → Trusted (Windows, như sandbox)."""
    host = settings.PMV_HIST_MSSQL
    if not host:
        raise PmvBlocked("PMV_HIST_MSSQL trống trong .env — chưa cấu hình kho lịch sử")
    auth = (f"UID={settings.PMV_HIST_USER};PWD={settings.PMV_HIST_PASSWORD};"
            if settings.PMV_HIST_USER else "Trusted_Connection=yes;")
    conn_str = (
        f"DRIVER={{{settings.PMV_MSSQL_ODBC_DRIVER}}};SERVER={host};"
        f"DATABASE={database or settings.PMV_HIST_DB};{auth}"
        "Encrypt=no;TrustServerCertificate=yes;Connection Timeout=10;"
    )
    return pyodbc.connect(conn_str, timeout=timeout, autocommit=autocommit)


def hist_query(sql, params=(), *, tag="hist", database=None, timeout=60):
    """SELECT trên kho lịch sử → list[dict]."""
    with _hist_connect(database=database, timeout=timeout) as cn:
        cur = cn.cursor()
        cur.execute(sql, params)
        if not cur.description:
            return []
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


def hist_exec(sql, params=(), *, tag="hist", database=None, timeout=1800, audit=True):
    """Chạy 1 lệnh DDL/DML trên kho lịch sử (autocommit). Trả số dòng ảnh hưởng."""
    t0 = time.monotonic()
    try:
        with _hist_connect(database=database, autocommit=True, timeout=timeout) as cn:
            cur = cn.cursor()
            cur.execute(sql, params)
            n = cur.rowcount
            while cur.nextset():
                pass
        if audit:
            _audit("HIST", tag, sql[:400], ms=int((time.monotonic() - t0) * 1000))
        return n
    except Exception as exc:
        _audit("HIST", tag, sql[:400], ok=False, ms=int((time.monotonic() - t0) * 1000), error=exc)
        raise


def hist_executemany(sql, seq_rows, *, tag="hist", database=None, timeout=1800, fast=True):
    """INSERT hàng loạt vào kho lịch sử (1 giao dịch). Trả số dòng đã gửi."""
    seq_rows = list(seq_rows)
    if not seq_rows:
        return 0
    t0 = time.monotonic()

    def _do(fast_mode):
        with _hist_connect(database=database, autocommit=False, timeout=timeout) as cn:
            cur = cn.cursor()
            if fast_mode:
                try:
                    cur.fast_executemany = True
                except Exception:
                    pass
            cur.executemany(sql, seq_rows)
            cn.commit()

    try:
        try:
            _do(fast)
        except Exception as exc:
            # fast_executemany hỏng với '' (HY090) / Decimal lệch scale → thử lại đường chậm
            if not fast:
                raise
            _audit("HIST", tag, f"{sql[:80]} fast lỗi ({str(exc)[:60]}) → thử lại chậm", ok=False)
            _do(False)
        _audit("HIST", tag, f"{sql[:120]} × {len(seq_rows)} dòng", ms=int((time.monotonic() - t0) * 1000))
        return len(seq_rows)
    except Exception as exc:
        _audit("HIST", tag, f"{sql[:120]} × {len(seq_rows)}", ok=False,
               ms=int((time.monotonic() - t0) * 1000), error=exc)
        raise


def pmv_read_stream(sql, params=(), *, tag, target="kk", chunk=5000, timeout=600):
    """Đọc KK theo LÔ (generator) cho backfill kho lịch sử — tránh nạp cả bảng vào RAM.
    Trả lần lượt list[tuple] (dạng thô để executemany). Vẫn CHỈ nhận SELECT như pmv_read."""
    s = sql.strip()
    if not s.lower().startswith("select") or _WRITE_WORDS.search(s):
        _audit("BLOCKED", tag, f"VƯỢT QUYỀN (stream chỉ SELECT): {s[:200]}", ok=False)
        raise PmvBlocked("Gateway CHẶN (stream chỉ nhận SELECT)")
    cn = _connect_dich(target, timeout=timeout)
    try:
        cur = cn.cursor()
        cur.execute(s, params)
        while True:
            rows = cur.fetchmany(chunk)
            if not rows:
                break
            yield [tuple(r) for r in rows]
    finally:
        cn.close()


def hist_bulk_merge(target, data_cols, pk_cols, rows, *, fast=True, reset_deleted=True,
                    tag="hist"):
    """UPSERT hàng loạt vào kho lịch sử theo PK (staging #stg + MERGE, 1 kết nối).
    Trả (them_moi, cap_nhat). KHÔNG xóa dòng nào (kho backup = superset)."""
    rows = list(rows)
    if not rows:
        return (0, 0)
    q = lambda c: f"[{c}]"
    on = " AND ".join(f"t.{q(c)}=s.{q(c)}" for c in pk_cols)
    setc = ", ".join(f"t.{q(c)}=s.{q(c)}" for c in data_cols if c not in pk_cols)
    setc += ", t.[_sync_seen_at]=GETDATE()" + (", t.[_sync_deleted]=0" if reset_deleted else "")
    inscols = ", ".join(q(c) for c in data_cols) + ", [_sync_seen_at]"
    insvals = ", ".join(f"s.{q(c)}" for c in data_cols) + ", GETDATE()"
    ins_stg = (f"INSERT INTO #stg ({', '.join(q(c) for c in data_cols)}) "
               f"VALUES ({', '.join(['?'] * len(data_cols))})")
    merge = (f"MERGE [{target}] AS t USING #stg AS s ON ({on}) "
             f"WHEN MATCHED THEN UPDATE SET {setc} "
             f"WHEN NOT MATCHED BY TARGET THEN INSERT ({inscols}) VALUES ({insvals}) "
             f"OUTPUT $action;")
    t0 = time.monotonic()

    def _do(fast_mode):
        with _hist_connect(autocommit=False, timeout=1800) as cn:
            cur = cn.cursor()
            cur.execute(f"SELECT TOP 0 {', '.join(q(c) for c in data_cols)} INTO #stg FROM [{target}]")
            if fast_mode:
                try:
                    cur.fast_executemany = True
                except Exception:
                    pass
            step = 5000 if fast_mode else 500
            for i in range(0, len(rows), step):
                cur.executemany(ins_stg, rows[i:i + step])
            cur.execute(merge)
            acts = [r[0] for r in cur.fetchall()]
            cur.execute("DROP TABLE #stg")
            cn.commit()
        return acts

    try:
        try:
            acts = _do(fast)
        except Exception as exc:
            # ⚠ fast_executemany hỏng với chuỗi RỖNG '' (HY090 buffer length 0) và Decimal lệch scale
            # ("loses precision") — thử lại đường CHẬM (bind từng giá trị), luôn đúng.
            if not fast:
                raise
            _audit("HIST", tag, f"MERGE [{target}] fast lỗi ({str(exc)[:60]}) → thử lại chậm", ok=False)
            acts = _do(False)
        _audit("HIST", tag, f"MERGE [{target}] × {len(rows)}", ms=int((time.monotonic() - t0) * 1000))
        return (acts.count("INSERT"), acts.count("UPDATE"))
    except Exception as exc:
        _audit("HIST", tag, f"MERGE [{target}] × {len(rows)}", ok=False,
               ms=int((time.monotonic() - t0) * 1000), error=exc)
        raise
