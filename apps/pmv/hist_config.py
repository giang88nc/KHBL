"""Danh mục bảng đồng bộ sang KHO LỊCH SỬ (PMV_KH2_HIST) — DÒ TỪ metadata KK, không hardcode.

Nhóm bảng (GĐ chốt 06/09/2026): I_CUSTOMER · TRN_RT_BUYSELL(+con) · TRN_RT_BUYGOLD(+con) ·
T_PRODUCT(+liên quan) · mọi bảng *_LOG. Chiến lược suy theo cấu trúc thật:

  append    : bảng *_LOG (chỉ thêm) — watermark = cột định danh tăng dần (identity/PK 1 cột)
  upsert    : bảng CHA có TrnDateTime_Upd + PK 1 cột — MERGE theo PK, watermark = TrnDateTime_Upd
  child     : bảng CON (không watermark) — làm mới theo khóa CHA (TrnID) khi cha đổi
  snapshot  : bảng lớn hay đổi, không watermark (I_CUSTOMER, T_PRODUCT) — đối soát bằng checksum

Phase 1 chỉ dùng cột/PK để DỰNG SCHEMA + BACKFILL + RECONCILE. strategy/watermark khai sẵn
để Phase 2 (incremental) dùng lại, không phải dò lại.
"""
from apps.pmv import gateway as G

# Điều kiện bảng thuộc phạm vi đồng bộ (khớp câu dò ở dưới)
# Bảng THAM CHIẾU nhỏ (Phase 4, 06/09/2026): các màn đọc quá khứ JOIN tới đây (tên NV, quầy, nhóm vàng,
# tồn kho, bảng giá, két, user) — có ở HIST thì đọc quá khứ + failover tự đủ, không phải hỏi KK.
_EXTRA_REF = ("T_EMPLOYEE", "T_SECTION", "T_MAINSECTION", "I_GOLD", "I_GOLD_BAL", "T_SHOP",
              "I_XRATE", "T_TILL", "SYS_USERS", "I_DIEMTICHLUY")
_INCLUDE = (
    "TABLE_NAME='I_CUSTOMER' OR TABLE_NAME LIKE 'TRN_RT_BUYSELL%' "
    "OR TABLE_NAME LIKE 'TRN_RT_BUYGOLD%' OR TABLE_NAME LIKE 'T_PRODUCT%' "
    "OR TABLE_NAME LIKE '%[_]LOG' "
    "OR TABLE_NAME IN (" + ", ".join(f"'{t}'" for t in _EXTRA_REF) + ")"
)
_SNAPSHOT = {"I_CUSTOMER", "T_PRODUCT"}
# Cha của các bảng con (để Phase 2 làm mới con theo cha); key nối luôn là TrnID.
_PARENT = {
    "TRN_RT_BUYSELL_SELL": "TRN_RT_BUYSELL", "TRN_RT_BUYSELL_BUYGOLD": "TRN_RT_BUYSELL",
    "TRN_RT_BUYSELL_OLDGOLD": "TRN_RT_BUYSELL", "TRN_RT_BUYSELL_OLDGOLD_BUY": "TRN_RT_BUYSELL",
    "TRN_RT_BUYSELL_OLDGOLD_SELL": "TRN_RT_BUYSELL", "TRN_RT_BUYSELL_PAYMENT": "TRN_RT_BUYSELL",
    "TRN_RT_BUYSELL_CardPay": "TRN_RT_BUYSELL", "TRN_RT_BUYSELL_DATCOC": "TRN_RT_BUYSELL",
    "TRN_RT_BUYGOLD_DT": "TRN_RT_BUYGOLD", "TRN_RT_BUYGOLD_CardPay": "TRN_RT_BUYGOLD",
}
# text/ntext/image không đưa vào BINARY_CHECKSUM được → loại khỏi cột đối soát
_NO_CHECKSUM_TYPES = {"text", "ntext", "image", "xml"}


def _q(sql):
    return G.pmv_read(sql, tag="hist_config", target="kk", audit=False)


def discover():
    """Trả list meta từng bảng trong phạm vi, đọc THẲNG metadata KK (chỉ đọc).
    meta = {table, columns:[{name,ddl,checksummable}], pk:[...], strategy, watermark, parent}"""
    tables = [r["TABLE_NAME"] for r in _q(
        f"SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES "
        f"WHERE TABLE_TYPE='BASE TABLE' AND ({_INCLUDE}) ORDER BY TABLE_NAME")]

    # cột (dùng sys để biết identity/computed/timestamp + độ dài chuẩn)
    cols_raw = _q("""
        SELECT o.name AS t, c.name AS col, c.column_id, ty.name AS typ,
               c.max_length AS mlen, c.precision AS prec, c.scale AS scale,
               c.is_nullable AS nullable, c.is_identity AS ident, c.is_computed AS computed
        FROM sys.objects o
        JOIN sys.columns c ON c.object_id=o.object_id
        JOIN sys.types ty ON ty.user_type_id=c.user_type_id
        WHERE o.type='U' ORDER BY o.name, c.column_id""")
    pk_raw = {r["t"]: r["cols"] for r in _q("""
        SELECT tc.TABLE_NAME AS t, STUFF((SELECT ','+k.COLUMN_NAME
             FROM INFORMATION_SCHEMA.KEY_COLUMN_USAGE k WHERE k.CONSTRAINT_NAME=tc.CONSTRAINT_NAME
             ORDER BY k.ORDINAL_POSITION FOR XML PATH('')),1,1,'') AS cols
        FROM INFORMATION_SCHEMA.TABLE_CONSTRAINTS tc WHERE tc.CONSTRAINT_TYPE='PRIMARY KEY'""")}
    upd_raw = {r["t"] for r in _q(
        "SELECT DISTINCT TABLE_NAME AS t FROM INFORMATION_SCHEMA.COLUMNS WHERE COLUMN_NAME='TrnDateTime_Upd'")}

    by_table = {}
    for r in cols_raw:
        by_table.setdefault(r["t"], []).append(r)

    metas = []
    for t in tables:
        cols = [_col_meta(c) for c in by_table.get(t, [])]
        if not cols:
            continue
        pk = [c.strip() for c in (pk_raw.get(t) or "").split(",") if c.strip()]
        strategy, watermark = _strategy(t, pk, t in upd_raw, cols)
        metas.append({
            "table": t, "columns": cols, "pk": pk,
            "strategy": strategy, "watermark": watermark, "parent": _PARENT.get(t),
        })
    return metas


def _col_meta(c):
    """1 cột → {name, ddl (kiểu SQL để CREATE ở HIST), checksummable}.
    identity/computed/timestamp đều hạ về cột THƯỜNG (kho lịch sử chỉ giữ giá trị)."""
    typ = c["typ"].lower()
    null = "NULL" if c["nullable"] else "NOT NULL"
    if typ in ("timestamp", "rowversion"):
        return {"name": c["col"], "ddl": f"[{c['col']}] binary(8) NULL", "checksummable": True}
    if typ in ("varchar", "char", "varbinary", "binary"):
        n = "max" if c["mlen"] == -1 else c["mlen"]
        t = f"{typ}({n})"
    elif typ in ("nvarchar", "nchar"):
        n = "max" if c["mlen"] == -1 else c["mlen"] // 2
        t = f"{typ}({n})"
    elif typ in ("decimal", "numeric"):
        t = f"{typ}({c['prec']},{c['scale']})"
    elif typ in ("datetime2", "time", "datetimeoffset"):
        t = f"{typ}({c['scale']})"
    else:
        t = typ
    return {"name": c["col"], "ddl": f"[{c['col']}] {t} {null}",
            "checksummable": typ not in _NO_CHECKSUM_TYPES}


def _strategy(table, pk, has_upd, cols):
    up = table.upper()
    if up.endswith("_LOG"):
        # watermark = PK 1 cột (LogID/IDLog/PromotionLogID…) hoặc cột identity
        wm = pk[0] if len(pk) == 1 else next(
            (c["name"] for c in cols if c["name"].lower() in ("id", "logid", "idlog")), None)
        return "append", wm
    if table in _SNAPSHOT:
        return "snapshot", None
    if has_upd and len(pk) == 1:
        return "upsert", "TrnDateTime_Upd"
    if table in _PARENT:
        return "child", None
    # còn lại: có PK thì snapshot theo PK, không thì child (an toàn cho backfill)
    return ("snapshot" if pk else "child"), None
