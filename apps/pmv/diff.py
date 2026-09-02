"""
So sánh 2 SQL Server (PMV thật ↔ SANDBOX ↔ snapshot đã lưu).

Vân tay mỗi bảng = (số dòng, checksum). 2 mức:
- FAST : chỉ số dòng, lấy từ sys.partitions (1 query metadata — KHÔNG quét bảng,
         nhẹ, an toàn chạy trên PMV THẬT dù tiệm đang bán).
- FULL : thêm CHECKSUM_AGG(BINARY_CHECKSUM(*)) từng bảng → phát hiện cả UPDATE
         (đổi giá trị mà số dòng không đổi). CHỈ chạy trên SANDBOX (local) —
         KHÔNG chạy full trên PMV thật (251 lần quét toàn bảng nện máy KK).

Dùng cho GĐ1 (đối chiếu sandbox vs thật) và GĐ3 (chụp sandbox TRƯỚC/SAU khi gọi
1 stored proc → biết chính xác proc đụng bảng nào).
"""
from .gateway import pmv_read
from .sandbox import sandbox_query

_COUNTS_SQL = (
    "SELECT t.name AS tbl, SUM(p.rows) AS rows "
    "FROM sys.tables t JOIN sys.partitions p "
    "ON p.object_id = t.object_id AND p.index_id IN (0, 1) "
    "WHERE t.is_ms_shipped = 0 GROUP BY t.name"
)


def _runner(source):
    if source == "pmv":
        return lambda sql: pmv_read(sql, tag="diff")
    if source == "sandbox":
        return sandbox_query
    raise ValueError(f"Nguồn không hợp lệ: {source}")


def snapshot(source, mode="fast"):
    """Trả {tbl: {'rows': int, 'cs': int|None}} cho toàn bộ bảng của 1 nguồn."""
    if mode == "full" and source == "pmv":
        # An toàn: không quét toàn bộ PMV thật. Hạ về fast.
        mode = "fast"
    run = _runner(source)
    rows = run(_COUNTS_SQL)
    data = {r["tbl"]: {"rows": int(r["rows"] or 0), "cs": None} for r in rows}
    if mode == "full":
        for tbl in data:
            try:
                r = run(f"SELECT CHECKSUM_AGG(BINARY_CHECKSUM(*)) AS cs FROM [{tbl}] WITH (NOLOCK)")
                data[tbl]["cs"] = r[0]["cs"] if r else None
            except Exception:
                data[tbl]["cs"] = "ERR"
    return data


def diff(snap_a, snap_b):
    """So 2 snapshot → list dòng (đã sắp: khác lên trên). status: only_a/only_b/diff/same."""
    tables = sorted(set(snap_a) | set(snap_b))
    out = []
    for t in tables:
        a = snap_a.get(t)
        b = snap_b.get(t)
        if a is None:
            status, ra, rb, dcs = "only_b", None, b["rows"], None
        elif b is None:
            status, ra, rb, dcs = "only_a", a["rows"], None, None
        else:
            ra, rb = a["rows"], b["rows"]
            has_cs = a["cs"] is not None and b["cs"] is not None
            dcs = has_cs and (a["cs"] != b["cs"])
            same = (ra == rb) and (not dcs if has_cs else True)
            status = "same" if same else "diff"
        out.append({
            "tbl": t,
            "rows_a": ra,
            "rows_b": rb,
            "delta": (rb - ra) if (ra is not None and rb is not None) else None,
            "cs_changed": bool(dcs),
            "status": status,
        })
    order = {"diff": 0, "only_a": 1, "only_b": 2, "same": 3}
    out.sort(key=lambda r: (order[r["status"]], r["tbl"]))
    return out
