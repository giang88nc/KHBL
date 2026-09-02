"""
Restore file .bak PMV vào SQL Express LOCAL (PMV_LOCAL_MSSQL) thành DB PMV_SANDBOX.

Đây là server TRÊN MÁY MR GIANG — không phải PMV thật, nên là ngoại lệ duy nhất
(cùng backup_pmv) được dùng pyodbc ngoài gateway. Sandbox dùng cho GĐ1/GĐ3:
thử nghiệm ghi + diff kết quả với app PMVGoldRT thật trước khi go-live.
"""
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
