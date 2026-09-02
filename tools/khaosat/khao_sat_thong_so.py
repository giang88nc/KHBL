# Chay bang: D:\PYTHON\KHBL\venv\Scripts\python.exe khao_sat_thong_so.py  (CHI DOC - xem skill pmv-proc-map)
# -*- coding: utf-8 -*-
"""Kiem tra CHI DOC thong so SQL Server @ PC KK (192.168.1.206:1430).
Doc thong so tu D:\\PYTHON\\KHJ\\.env, khong dung Django, khong ghi gi len server."""
import sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import pyodbc

ENV = {}
with open(r"D:\PYTHON\KHBL\.env", encoding="utf-8") as f:
    for line in f:
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            ENV[k.strip()] = v.strip()

conn_str = (
    f"DRIVER={{{ENV['PMV_MSSQL_ODBC_DRIVER']}}};"
    f"SERVER={ENV['PMV_MSSQL_HOST']};"
    f"DATABASE={ENV['PMV_MSSQL_DB']};"
    f"UID={ENV['PMV_MSSQL_USER']};"
    f"PWD={ENV['PMV_MSSQL_PASSWORD']};"
    "Encrypt=no;TrustServerCertificate=yes;Connection Timeout=10;"
)
print(f"== KET NOI: {ENV['PMV_MSSQL_HOST']} / DB {ENV['PMV_MSSQL_DB']} / user {ENV['PMV_MSSQL_USER']} / driver {ENV['PMV_MSSQL_ODBC_DRIVER']}")
cn = pyodbc.connect(conn_str, timeout=10)
cur = cn.cursor()

def q(sql, params=()):
    cur.execute(sql, params)
    return cur.fetchall()

def one(sql, params=()):
    cur.execute(sql, params)
    r = cur.fetchone()
    return r[0] if r else None

print("\n== SERVER ==")
print("@@VERSION      :", one("SELECT @@VERSION").replace("\n", " | ").replace("\t", " "))
for p in ("ProductVersion", "ProductLevel", "Edition", "MachineName", "InstanceName", "Collation", "IsIntegratedSecurityOnly"):
    print(f"{p:<26}:", one(f"SELECT CONVERT(NVARCHAR(200), SERVERPROPERTY('{p}'))"))
print("Gio server (GETDATE)      :", one("SELECT CONVERT(VARCHAR(30), GETDATE(), 120)"))
import datetime
print("Gio may nay               :", datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

print("\n== DANH SACH DATABASE TREN SERVER (nhin thay duoc) ==")
try:
    for r in q("SELECT name, state_desc, compatibility_level, recovery_model_desc FROM sys.databases ORDER BY name"):
        print(f"  {r.name:<30} {r.state_desc:<10} compat={r.compatibility_level} recovery={r.recovery_model_desc}")
except Exception as e:
    print("  (khong du quyen xem sys.databases):", e)

print("\n== DB HIEN TAI ==")
print("DB_NAME()                 :", one("SELECT DB_NAME()"))
try:
    r = q("SELECT compatibility_level, collation_name, state_desc, recovery_model_desc, create_date FROM sys.databases WHERE name = DB_NAME()")[0]
    print(f"compat={r.compatibility_level} collation={r.collation_name} state={r.state_desc} recovery={r.recovery_model_desc} created={r.create_date}")
except Exception as e:
    print("  loi:", e)
try:
    for r in q("SELECT name, size*8/1024 AS mb, physical_name FROM sys.database_files"):
        print(f"  file {r.name:<20} {r.mb:>8} MB  {r.physical_name}")
except Exception as e:
    print("  (khong xem duoc file DB):", e)

print("\n== QUYEN CUA TAI KHOAN ==")
print("SUSER_SNAME()             :", one("SELECT SUSER_SNAME()"))
print("USER_NAME() trong DB      :", one("SELECT USER_NAME()"))
print("sysadmin?                 :", one("SELECT IS_SRVROLEMEMBER('sysadmin')"))
print("db_datareader?            :", one("SELECT IS_ROLEMEMBER('db_datareader')"))
print("db_datawriter?            :", one("SELECT IS_ROLEMEMBER('db_datawriter')"))
print("db_owner?                 :", one("SELECT IS_ROLEMEMBER('db_owner')"))
try:
    perms = q("SELECT DISTINCT permission_name FROM fn_my_permissions(NULL, 'DATABASE') ORDER BY permission_name")
    print("Quyen cap DATABASE        :", ", ".join(p.permission_name for p in perms))
except Exception as e:
    print("  (fn_my_permissions loi):", e)

print("\n== MA HOA KET NOI ==")
try:
    r = q("SELECT encrypt_option, auth_scheme, protocol_type, net_transport FROM sys.dm_exec_connections WHERE session_id = @@SPID")
    if r:
        print(f"encrypt={r[0].encrypt_option} auth={r[0].auth_scheme} proto={r[0].protocol_type} transport={r[0].net_transport}")
except Exception as e:
    print("  (can VIEW SERVER STATE, bo qua):", e)

print("\n== 2 BANG WEBAPP DANG DUNG ==")
for tbl in ("T_EMPLOYEE", "TRN_RT_BUYSELL"):
    oid = one("SELECT OBJECT_ID(?)", (tbl,))
    if oid is None:
        print(f"  {tbl}: KHONG TIM THAY!")
        continue
    rows = one(
        "SELECT SUM(p.rows) FROM sys.partitions p WHERE p.object_id = OBJECT_ID(?) AND p.index_id IN (0,1)",
        (tbl,),
    )
    print(f"  {tbl}: ton tai, ~{rows} dong")
    cols = q(
        "SELECT c.name, t.name AS typ, c.max_length, c.is_nullable FROM sys.columns c "
        "JOIN sys.types t ON t.user_type_id = c.user_type_id "
        "WHERE c.object_id = OBJECT_ID(?) ORDER BY c.column_id",
        (tbl,),
    )
    need = {"T_EMPLOYEE": {"EmpID", "EmpName"}, "TRN_RT_BUYSELL": {"EmpID", "PayAmount", "Status", "CreatedDate"}}[tbl]
    have = {c.name for c in cols}
    thieu = need - have
    print(f"    cot webapp can: {sorted(need)} -> {'DU' if not thieu else 'THIEU ' + str(sorted(thieu))}")
    for c in cols:
        mark = " *" if c.name in need else ""
        print(f"    {c.name:<24} {c.typ}({c.max_length}) null={c.is_nullable}{mark}")

print("\n== THU CHAY 2 QUERY WEBAPP (chi doc) ==")
try:
    n = one("SELECT COUNT(*) FROM T_EMPLOYEE")
    print(f"  KK_EMPLOYEE_QUERY: T_EMPLOYEE co {n} nhan vien")
except Exception as e:
    print("  KK_EMPLOYEE_QUERY loi:", e)
try:
    r = q(
        "SELECT COUNT(*) AS n, SUM(PayAmount) AS total FROM TRN_RT_BUYSELL "
        "WHERE PayAmount > 0 AND Status = 'C' AND CreatedDate >= ? AND CreatedDate < ?",
        ("2026-08-01", "2026-09-01"),
    )[0]
    print(f"  KK_BUYSELL_QUERY thang 8/2026: {r.n} giao dich, tong PayAmount = {r.total:,.0f}")
except Exception as e:
    print("  KK_BUYSELL_QUERY loi:", e)

cn.close()
print("\nXONG — khong ghi gi len server (toan bo SELECT).")
