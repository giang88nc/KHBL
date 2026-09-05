"""
Restore file .bak PMV vào SQL Express LOCAL (PMV_LOCAL_MSSQL) thành DB PMV_SANDBOX.

Đây là server TRÊN MÁY MR GIANG — không phải PMV thật, nên là ngoại lệ duy nhất
(cùng backup_pmv) được dùng pyodbc ngoài gateway. Sandbox dùng cho GĐ1/GĐ3:
thử nghiệm ghi + diff kết quả với app PMVGoldRT thật trước khi go-live.
"""
import re
from pathlib import Path

import pyodbc
from django.conf import settings


def restore_sandbox_from(bak_path):
    """Restore bak_path vào [PMV_SANDBOX] (WITH REPLACE). Trả đường dẫn data dir."""
    bak_path = Path(bak_path)
    if not bak_path.exists():
        raise FileNotFoundError(f"Không thấy file backup: {bak_path}")
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
            f"RESTORE DATABASE [{db}] FROM DISK = N'{bak_path}' WITH REPLACE, STATS = 25, "
            f"MOVE N'GOLDRTDB' TO N'{data_dir}\\{db}.mdf', "
            f"MOVE N'GOLDRTDB_log' TO N'{data_dir}\\{db}_log.ldf'"
        )
        while cur.nextset():
            pass
    finally:
        cn.close()
    return data_dir



# CHÚ Ý: sandbox_query chỉ phục vụ HẠ TẦNG (restore / đối chiếu / khôi phục).
# Nghiệp vụ bán lẻ KHÔNG gọi thẳng vào đây — phải qua PmvClient → gateway,
# để bản thử đi đúng con đường của lúc chạy thật (RULE 1).
def sandbox_query(sql, params=()):
    """SELECT trên sandbox local (không đi gateway — không phải PMV thật)."""
    cn = pyodbc.connect(
        f"DRIVER={{{settings.PMV_MSSQL_ODBC_DRIVER}}};SERVER={settings.PMV_LOCAL_MSSQL};"
        f"DATABASE={settings.PMV_SANDBOX_DB};Trusted_Connection=yes;Encrypt=no;TrustServerCertificate=yes;",
        timeout=30,
    )
    try:
        cur = cn.cursor()
        cur.execute(sql, params)
        cols = [c[0] for c in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []
    finally:
        cn.close()


# ---------------------------------------------------------------------------
# DỌN DẸP SAU KIỂM THỬ — CHỈ chạy trên bản thử máy Mr Giang, không bao giờ chạm KK.
# Cần vì proc I_CUSTOMER_Del của vendor TỪ CHỐI xóa khách đã phát sinh giao dịch
# (trả Result=-1), nên bộ hồi quy phải tự nhặt rác của chính nó.
# Chỉ nhận đúng khuôn DELETE dưới đây, khóa theo mã khách mà test vừa tạo.
# ---------------------------------------------------------------------------
_DON_DEP_ALLOW = tuple(
    re.compile(rf"(?is)^\s*DELETE\s+FROM\s+{b}\s+WHERE\s+CustID\s*=\s*\?\s*$")
    for b in ("I_CUSTOMER", "I_DIEMTICHLUY", "I_LICHSUTICHLUYDIEM", "T_CUSTOMER_DEBT")
)


def sandbox_exec_khuon(sql, khuon_rx):
    """Chạy 1 lệnh GHI trên BẢN THỬ nếu khớp đúng khuôn regex truyền vào (dùng cho việc TẬP DƯỢT
    khuôn ngoại lệ của gateway trước khi chạy thật). Trả số dòng ảnh hưởng."""
    s = sql.strip()
    if not khuon_rx.match(s):
        raise RuntimeError(f"Sandbox từ chối (ngoài khuôn): {s[:120]}")
    if not settings.PMV_LOCAL_MSSQL:
        raise RuntimeError("Chưa cấu hình PMV_LOCAL_MSSQL — không có bản thử")
    cn = pyodbc.connect(
        f"DRIVER={{{settings.PMV_MSSQL_ODBC_DRIVER}}};SERVER={settings.PMV_LOCAL_MSSQL};"
        f"DATABASE={settings.PMV_SANDBOX_DB};Trusted_Connection=yes;Encrypt=no;TrustServerCertificate=yes;",
        timeout=120, autocommit=True,
    )
    try:
        cur = cn.cursor()
        cur.execute(s)
        return cur.rowcount
    finally:
        cn.close()


def sandbox_don_dep(sql, params=()):
    """Xóa vài dòng rác của bộ hồi quy trên BẢN THỬ. Ngoài khuôn → từ chối."""
    s = sql.strip()
    if not any(rx.match(s) for rx in _DON_DEP_ALLOW):
        raise RuntimeError(f"Dọn dẹp bị chặn (ngoài khuôn cho phép): {s[:120]}")
    if not settings.PMV_LOCAL_MSSQL:
        raise RuntimeError("Chưa cấu hình PMV_LOCAL_MSSQL — không có bản thử để dọn")
    cn = pyodbc.connect(
        f"DRIVER={{{settings.PMV_MSSQL_ODBC_DRIVER}}};SERVER={settings.PMV_LOCAL_MSSQL};"
        f"DATABASE={settings.PMV_SANDBOX_DB};Trusted_Connection=yes;Encrypt=no;TrustServerCertificate=yes;",
        timeout=60, autocommit=True,
    )
    try:
        cur = cn.cursor()
        cur.execute(s, params)
        return cur.rowcount
    finally:
        cn.close()
