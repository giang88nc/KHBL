# Chay bang: D:\PYTHON\KHBL\venv\Scripts\python.exe khao_sat_cau_truc.py  (CHI DOC - xem skill pmv-proc-map)
# -*- coding: utf-8 -*-
"""Khao sat CHI DOC cau truc DB PMV_BANLE_KH2 (phuc vu phan tich webapp song song).
Toan bo SELECT catalog/metadata. KHONG in mat khau, KHONG dump du lieu ca nhan."""
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

cn = pyodbc.connect(
    f"DRIVER={{{ENV['PMV_MSSQL_ODBC_DRIVER']}}};SERVER={ENV['PMV_MSSQL_HOST']};"
    f"DATABASE={ENV['PMV_MSSQL_DB']};UID={ENV['PMV_MSSQL_USER']};PWD={ENV['PMV_MSSQL_PASSWORD']};"
    "Encrypt=no;TrustServerCertificate=yes;Connection Timeout=10;",
    timeout=15,
)
cur = cn.cursor()

def q(sql, params=()):
    cur.execute(sql, params)
    return cur.fetchall()

print("== 1. TONG QUAN OBJECT ==")
for r in q("SELECT type_desc, COUNT(*) n FROM sys.objects WHERE is_ms_shipped=0 GROUP BY type_desc ORDER BY n DESC"):
    print(f"  {r.type_desc:<30} {r.n}")

print("\n== 2. TAT CA BANG + SO DONG (giam dan) ==")
rows = q(
    "SELECT t.name, SUM(CASE WHEN p.index_id IN (0,1) THEN p.rows ELSE 0 END) rows "
    "FROM sys.tables t JOIN sys.partitions p ON p.object_id=t.object_id "
    "GROUP BY t.name ORDER BY rows DESC, t.name"
)
print(f"  Tong so bang: {len(rows)}")
for r in rows:
    print(f"  {r.name:<40} {r.rows}")

print("\n== 3. TRIGGER (DML + DDL) ==")
trs = q(
    "SELECT tr.name, OBJECT_NAME(tr.parent_id) AS tbl, tr.is_disabled "
    "FROM sys.triggers tr ORDER BY tbl, tr.name"
)
if not trs:
    print("  (khong co trigger nao)")
for r in trs:
    print(f"  {r.name:<40} tren {r.tbl}  disabled={r.is_disabled}")

print("\n== 4. STORED PROCEDURE / FUNCTION (ten) ==")
ps = q("SELECT name FROM sys.procedures ORDER BY name")
print(f"  Tong proc: {len(ps)}")
for r in ps[:60]:
    print("  ", r.name)
fs = q("SELECT name, type_desc FROM sys.objects WHERE type IN ('FN','IF','TF') AND is_ms_shipped=0 ORDER BY name")
print(f"  Tong function: {len(fs)}")
for r in fs[:30]:
    print(f"   {r.name} ({r.type_desc})")

print("\n== 5. VIEW ==")
vs = q("SELECT name FROM sys.views ORDER BY name")
print(f"  Tong view: {len(vs)}")
for r in vs[:40]:
    print("  ", r.name)

print("\n== 6. KHOA NGOAI (FK) ==")
fks = q(
    "SELECT fk.name, OBJECT_NAME(fk.parent_object_id) child, OBJECT_NAME(fk.referenced_object_id) parent "
    "FROM sys.foreign_keys fk ORDER BY child"
)
print(f"  Tong FK: {len(fks)}")
for r in fks[:40]:
    print(f"  {r.child} -> {r.parent}  ({r.name})")

print("\n== 7. CONSTRAINT/IDENTITY ==")
ids = q(
    "SELECT OBJECT_NAME(object_id) tbl, name FROM sys.identity_columns ORDER BY tbl"
)
print(f"  Cot IDENTITY: {len(ids)}")
for r in ids[:30]:
    print(f"  {r.tbl}.{r.name}")
pks = q("SELECT COUNT(*) n FROM sys.key_constraints WHERE type='PK'")
print(f"  So bang co PRIMARY KEY: {pks[0].n} / {len(rows)} bang")

print("\n== 8. CO CHE SYNC (cot IsSync & bang lien quan) ==")
syncs = q(
    "SELECT OBJECT_NAME(c.object_id) tbl, c.name FROM sys.columns c "
    "JOIN sys.tables t ON t.object_id=c.object_id "
    "WHERE c.name LIKE '%Sync%' OR c.name LIKE '%sync%' ORDER BY tbl"
)
for r in syncs:
    print(f"  {r.tbl}.{r.name}")
stabs = q("SELECT name FROM sys.tables WHERE name LIKE '%SYNC%' OR name LIKE '%Sync%'")
for r in stabs:
    print("  BANG:", r.name)

print("\n== 9. DINH DANG MA GIAO DICH (TrnID) — 5 dong moi nhat, chi ma+ngay ==")
for r in q("SELECT TOP 5 TrnID, BillCode, CONVERT(VARCHAR(19), CreatedDate, 120) d, Status, IsDel FROM TRN_RT_BUYSELL ORDER BY CreatedDate DESC"):
    print(f"  TrnID={r.TrnID}  Bill={r.BillCode}  {r.d}  Status={r.Status} IsDel={r.IsDel}")
print("  Cac gia tri Status phan biet:", [x.Status for x in q("SELECT DISTINCT Status FROM TRN_RT_BUYSELL")])

print("\n== 10. BANG CAU HINH / VERSION APP (ten goi y) ==")
cfg = q(
    "SELECT name FROM sys.tables WHERE name LIKE '%CONFIG%' OR name LIKE '%VERSION%' "
    "OR name LIKE '%SYS%' OR name LIKE '%PARAM%' OR name LIKE '%SETTING%' ORDER BY name"
)
for r in cfg:
    print("  ", r.name)

print("\n== 11. T_EMPLOYEE: ServerName/DatabaseName (an mat khau) ==")
for r in q("SELECT DISTINCT ServerName, DatabaseName, ShopID FROM T_EMPLOYEE"):
    print(f"  Server={r.ServerName}  DB={r.DatabaseName}  Shop={r.ShopID}")

print("\n== 12. USER TRONG DB + LOGIN SERVER ==")
for r in q("SELECT name, type_desc FROM sys.database_principals WHERE type IN ('S','U') AND name NOT LIKE '##%' ORDER BY name"):
    print(f"  DB user: {r.name} ({r.type_desc})")
for r in q("SELECT name, type_desc, is_disabled FROM sys.server_principals WHERE type IN ('S','U') AND name NOT LIKE '##%' ORDER BY name"):
    print(f"  LOGIN  : {r.name} ({r.type_desc}) disabled={r.is_disabled}")

print("\n== 13. KET NOI DANG MO TOI SERVER (ai dang dung) ==")
try:
    for r in q(
        "SELECT s.login_name, s.host_name, s.program_name, COUNT(*) n "
        "FROM sys.dm_exec_sessions s WHERE s.is_user_process=1 "
        "GROUP BY s.login_name, s.host_name, s.program_name ORDER BY n DESC"
    ):
        print(f"  {r.login_name:<15} tu may {r.host_name:<18} app='{r.program_name}'  ({r.n} phien)")
except Exception as e:
    print("  loi:", e)

cn.close()
print("\nXONG — toan bo chi doc.")
