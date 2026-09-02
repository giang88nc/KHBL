# Chay bang: D:\PYTHON\KHBL\venv\Scripts\python.exe khao_sat_hanh_vi.py  (CHI DOC - xem skill pmv-proc-map)
# -*- coding: utf-8 -*-
"""Theo doi HANH VI cua PMVGoldRT — toan bo CHI DOC (SELECT + NOLOCK), khong ghi gi.
A. App dang thuc thi gi (cache ke hoach thuc thi cua SQL Server)
B. Dau vet 1 hoa don ban THAT (TrnID moi nhat Status=C) tren cac bang
C. Bang nao co dong moi phat sinh HOM NAY (bang "song")
D. Ban do proc <-> bang cho 4 nghiep vu: hoa don ban/thau/doi, product, customer, bang gia
"""
import sys, re
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
    timeout=60,
)
cur = cn.cursor()

def q(sql, params=()):
    cur.execute(sql, params)
    return cur.fetchall()

# ============ A. APP DANG THUC THI GI ============
print("== A. TOP CAU LENH TRONG CACHE THUC THI (app that dang chay gi) ==")
rows = q("""
SELECT TOP 60
  qs.execution_count,
  CONVERT(VARCHAR(19), qs.last_execution_time, 120) AS last_exec,
  st.objectid,
  OBJECT_NAME(st.objectid, st.dbid) AS obj_name,
  LEFT(REPLACE(REPLACE(st.text, CHAR(13), ' '), CHAR(10), ' '), 160) AS snippet
FROM sys.dm_exec_query_stats qs
CROSS APPLY sys.dm_exec_sql_text(qs.sql_handle) st
ORDER BY qs.execution_count DESC
""")
n_proc = n_adhoc = 0
for r in rows:
    if r.obj_name:
        n_proc += 1
        print(f"  [{r.execution_count:>7}x  last {r.last_exec}]  PROC {r.obj_name}")
    else:
        n_adhoc += 1
        print(f"  [{r.execution_count:>7}x  last {r.last_exec}]  ADHOC: {r.snippet}")
print(f"  -> trong top 60: {n_proc} proc / {n_adhoc} cau ad-hoc")

# ============ B. DAU VET 1 HOA DON THAT ============
print("\n== B. DAU VET HOA DON BAN MOI NHAT (Status=C) ==")
trn = q("SELECT TOP 1 TrnID, BillCode, CONVERT(VARCHAR(19),CreatedDate,120) d FROM TRN_RT_BUYSELL WITH (NOLOCK) WHERE Status='C' AND IsDel='0' ORDER BY CreatedDate DESC")[0]
print(f"  Hoa don: TrnID={trn.TrnID}  Bill={trn.BillCode}  luc {trn.d}")
tid = trn.TrnID
# moi bang co cot TrnID -> dem dong khop
tabs_trnid = q("""
SELECT t.name FROM sys.tables t JOIN sys.columns c ON c.object_id=t.object_id
WHERE c.name = 'TrnID' ORDER BY t.name""")
hits = []
for r in tabs_trnid:
    try:
        n = q(f"SELECT COUNT(*) FROM [{r.name}] WITH (NOLOCK) WHERE TrnID = ?", (tid,))[0][0]
        if n:
            hits.append((r.name, n))
    except Exception:
        pass
print(f"  Bang co cot TrnID: {len(tabs_trnid)} — bang chua dong cua hoa don nay:")
for name, n in hits:
    print(f"    {name:<35} {n} dong")
# cac cot tham chieu khac (RefTrnID, RefID...)
refcols = q("""
SELECT t.name tbl, c.name col FROM sys.tables t JOIN sys.columns c ON c.object_id=t.object_id
WHERE c.name IN ('RefTrnID','RefID','TrnRefID','SellTrnID','BuyTrnID') ORDER BY t.name""")
for r in refcols:
    try:
        n = q(f"SELECT COUNT(*) FROM [{r.tbl}] WITH (NOLOCK) WHERE [{r.col}] = ?", (tid,))[0][0]
        if n:
            print(f"    {r.tbl:<35} {n} dong (qua cot {r.col})")
    except Exception:
        pass

# ============ C. BANG "SONG" HOM NAY ============
print("\n== C. BANG CO DONG TAO HOM NAY (CreatedDate >= hom nay) ==")
tabs_cd = q("""
SELECT t.name FROM sys.tables t JOIN sys.columns c ON c.object_id=t.object_id
JOIN sys.types ty ON ty.user_type_id=c.user_type_id
WHERE c.name='CreatedDate' AND ty.name IN ('datetime','smalldatetime')
ORDER BY t.name""")
live = []
for r in tabs_cd:
    try:
        n = q(f"SELECT COUNT(*) FROM [{r.name}] WITH (NOLOCK) WHERE CreatedDate >= ?", ("2026-09-02",))[0][0]
        if n:
            live.append((r.name, n))
    except Exception:
        pass
for name, n in sorted(live, key=lambda x: -x[1]):
    print(f"    {name:<35} {n} dong moi hom nay")

# ============ D. BAN DO PROC <-> BANG ============
print("\n== D. BAN DO PROC GHI VAO CAC BANG MUC TIEU ==")
defs = q("SELECT o.name, m.definition FROM sys.sql_modules m JOIN sys.objects o ON o.object_id=m.object_id WHERE o.type='P'")
proc_def = {r.name: (r.definition or "") for r in defs}
print(f"  Da doc source {len(proc_def)} proc.")

all_tables = {r.name for r in q("SELECT name FROM sys.tables")}

WRITE_RE = re.compile(
    r"\b(INSERT\s+INTO|UPDATE|DELETE\s+FROM|DELETE)\s+(?:\[?dbo\]?\s*\.\s*)?\[?([A-Za-z0-9_]+)\]?",
    re.IGNORECASE,
)
EXEC_RE = re.compile(r"\bEXEC(?:UTE)?\s+(?:\[?dbo\]?\s*\.\s*)?\[?([A-Za-z0-9_]+)\]?", re.IGNORECASE)

def writes_of(name):
    """tra {(action, table)} ma proc ghi truc tiep"""
    out = set()
    for m in WRITE_RE.finditer(proc_def.get(name, "")):
        act, obj = m.group(1).upper().split()[0], m.group(2)
        if obj in all_tables:
            out.add((act, obj))
    return out

def execs_of(name):
    return {p for p in EXEC_RE.findall(proc_def.get(name, "")) if p in proc_def and p != name}

TARGETS = {
    "HOA DON BAN (TRN_RT_BUYSELL)": "TRN_RT_BUYSELL",
    "HOA DON THAU (TRN_RT_BUYGOLD)": "TRN_RT_BUYGOLD",
    "HOA DON DOI (TRN_RT_CHANGE)": "TRN_RT_CHANGE",
    "PRODUCT (T_PRODUCT)": "T_PRODUCT",
    "CUSTOMER (I_CUSTOMER)": "I_CUSTOMER",
    "BANG GIA (I_XRATE)": "I_XRATE",
}
for label, tbl in TARGETS.items():
    ins, upd, dele = [], [], []
    for pname in proc_def:
        for act, t in writes_of(pname):
            if t == tbl:
                (ins if act == "INSERT" else upd if act == "UPDATE" else dele).append(pname)
    print(f"\n  --- {label} ---")
    print(f"    Proc INSERT : {sorted(set(ins))}")
    print(f"    Proc UPDATE : {sorted(set(upd))[:12]}{' ...' if len(set(upd))>12 else ''}")
    print(f"    Proc DELETE : {sorted(set(dele))}")

# Voi proc Ins chinh cua tung nghiep vu: liet ke TOAN BO bang no dung (truc tiep + goi long nhau 1 cap)
print("\n== D2. PROC CHINH — TOAN BO BANG BI GHI (ke ca proc goi long nhau) ==")
MAIN = [
    "TRN_RT_BUYSELL_Ins", "INS_TRN_RT_BUYSELL", "TRN_RT_BUYGOLD_Ins", "INS_TRN_RT_BUYGOLD",
    "TRN_RT_CHANGE_Ins", "INS_TRN_RT_CHANGE",
    "DEL_TRN_RT_BUYSELL", "DEL_TRN_RT_BUYGOLD", "DEL_TRN_RT_CHANGE",
    "I_CUSTOMER_Ins", "I_CUSTOMER_Upd", "T_PRODUCT_Ins", "T_PRODUCT_Upd",
    "I_XRATE_Ins", "I_XRATE_Upd", "CARDPAY_Ins",
]
exist = [p for p in MAIN if p in proc_def]
missing = [p for p in MAIN if p not in proc_def]
if missing:
    # tim ten gan dung
    for miss in missing:
        key = miss.replace("_Ins", "").replace("INS_", "").replace("_Upd", "").replace("DEL_", "")
        cands = [p for p in proc_def if key.lower() in p.lower()][:8]
        print(f"  (khong co proc '{miss}' — ten gan giong: {cands})")
for pname in exist:
    seen_w = set(writes_of(pname))
    for sub in execs_of(pname):
        for w in writes_of(sub):
            seen_w.add((w[0], w[1] + f"  (qua {sub})"))
    print(f"\n  {pname}:")
    for act, t in sorted(seen_w):
        print(f"    {act:<7} {t}")
    subs = execs_of(pname)
    if subs:
        print(f"    goi proc con: {sorted(subs)}")

cn.close()
print("\nXONG — toan bo chi doc (SELECT + NOLOCK).")
