"""
Lớp ĐỌC dữ liệu bán lẻ từ PMV (chỉ đọc, qua gateway). Mọi câu SQL ở đây:
- SQL Server 2005: dùng TOP, không OFFSET/FETCH, ngày truyền chuỗi ISO
- WITH (NOLOCK) trên mọi bảng dữ liệu — 2 máy trạm đang bán hàng thật
Số liệu/cột đã kiểm chứng bằng khảo sát 03/09/2026 (xem docs/PHAN_TICH_HOAT_DONG_PMVGOLDRT.md).
"""
import hashlib

from django.core.cache import cache

from apps.pmv import money as M
from apps.pmv.client import PmvClient

# Bảng giá dùng ShopID = CHUỖI RỖNG (khác ShopID hóa đơn 'TSP141100000001')
XRATE_SHOP = ""
WALK_IN = "CU0000000000000"

# 4 loại lên bảng giá đầu màn, theo số dòng bán thật (N9999 16.033 · 18K 8.250 · 24K 4.339 · BK 56)
GIA_NOI_BAT = ["N9999", "18K", "24K", "BK"]

AGE_CLASS = {"N9999": "9999", "D9999": "9999", "SJC": "9999", "DSJC": "9999",
             "24K": "24k", "D24K": "24k", "18K": "18k", "D18K": "18k",
             "BK": "bk", "DBK": "bk", "DBk": "bk", "DT": "bk", "VT": "vt", "VBK": "vt"}


def client(tag="pos"):
    return PmvClient("pmv", tag=tag)


def age_class(gold_code):
    return AGE_CLASS.get((gold_code or "").strip(), "")


# ─────────────────────────── BẢNG GIÁ ───────────────────────────

def bang_gia(force=False):
    """15 dòng bảng giá + tên tiếng Việt + đơn vị. Cache 5 giây (N tab chỉ tốn 1 truy vấn/5s)."""
    key = "khbl:xrate"
    rows = None if force else cache.get(key)
    if rows is None:
        rows = client("bang_gia").query(
            "SELECT x.GoldCcy, x.Type, x.SellRate, x.BuyRate, x.POSellRate, x.POBuyRate, "
            "x.RateDate, x.RateTime, ISNULL(g.GoldDesc, x.GoldCcy) AS GoldDesc, "
            "ISNULL(g.PriceUnit,'L') AS PriceUnit, ISNULL(g.Active,'1') AS Active "
            "FROM I_XRATE x WITH (NOLOCK) LEFT JOIN I_GOLD g WITH (NOLOCK) ON g.GoldCode = x.GoldCcy "
            "WHERE x.ShopID = ? ORDER BY x.Type DESC, x.GoldCcy", (XRATE_SHOP,))
        for r in rows:
            r["don_vi"] = "₫/gram" if (r["PriceUnit"] or "L").upper() == "G" else "₫/chỉ"
            r["ban_dong"] = M.dec(r["SellRate"]) * M.RATE_SCALE
            r["mua_dong"] = M.dec(r["BuyRate"]) * M.RATE_SCALE
            r["age"] = age_class(r["GoldCcy"])
        cache.set(key, rows, 5)
    return rows


def gia_map():
    """{GoldCode: dòng giá} để tra nhanh khi quét hàng."""
    return {r["GoldCcy"]: r for r in bang_gia()}


def gia_noi_bat():
    m = gia_map()
    return [m[g] for g in GIA_NOI_BAT if g in m]


def gia_chu_ky(rows=None):
    """Chữ ký bảng giá — client gửi lại, giống thì trả 204 (không swap DOM)."""
    rows = rows if rows is not None else bang_gia()
    raw = "|".join(f"{r['GoldCcy']}:{r['SellRate']}:{r['BuyRate']}:{r['RateDate']}:{r['RateTime']}"
                   for r in rows)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def gia_moc():
    rows = bang_gia()
    t = max((str(r["RateTime"] or "") for r in rows), default="")
    d = max((str(r["RateDate"] or "")[:10] for r in rows), default="")
    return {"gio": t, "ngay": d}


def lich_su_gia(limit=12):
    """Các LÔ đổi giá gần nhất (I_XRATE_HIST là ảnh chụp trạng thái, không phải nhật ký thao tác)."""
    return client("lich_su_gia").query(
        f"SELECT TOP {int(limit)} RateDate, Serial, COUNT(*) AS so_dong, MAX(RateTime) AS gio "
        "FROM I_XRATE_HIST WITH (NOLOCK) GROUP BY RateDate, Serial ORDER BY RateDate DESC, Serial DESC")


# ─────────────────────────── QUÉT MÃ HÀNG ───────────────────────────

SCAN_ERR_STYLE = {"P-002": "do", "P-017": "nau", "P-012": "hoi"}


def quet_ma(ma, till_id="", cust_id=WALK_IN):
    """Gọi đúng proc vendor → dict(ok, item|err). Câu lỗi lấy NGUYÊN VĂN của vendor."""
    ma = (ma or "").strip()
    if not ma:
        return {"ok": False, "err": {"code": "", "desc": "Chưa nhập mã hàng", "style": "cam"}}
    c = client("quet_ma")
    rc, sets = c.call(
        "T_PRODUCT_GetByCodeForSell", raise_on_rc=False,
        p_ProductCode=ma, p_TaskPrice=0, p_ShopID="", p_CheckRealSL=1,
        p_TillID=till_id or "", p_CustID=cust_id or WALK_IN,
        p_ShopID_XRate=XRATE_SHOP,  # ⚠ '' — truyền ShopID thật thì SellRate ra 0
        p_RutGon="0")
    row = sets[0][0] if (sets and sets[0]) else None
    if rc != 0 or not row or str(row.get("ErrorCode", "0")) != "0":
        code = str((row or {}).get("ErrorCode") or "").strip()
        desc = str((row or {}).get("ErrorDesc") or "Không đọc được mã hàng").strip()
        if code == "P-002" and ma not in desc:
            desc = f"{desc}: {ma}"
        return {"ok": False, "err": {"code": code, "desc": desc,
                                     "style": SCAN_ERR_STYLE.get(code, "do")}}
    return {"ok": True, "item": _item_tu_row(row)}


def _item_tu_row(r):
    """Đổi 1 dòng proc trả về thành món trong giỏ (số lưu dạng chuỗi để qua session an toàn)."""
    pu = (r.get("PriceUnit") or "L").upper()
    tw, dw = M.dec(r.get("TotalWeight")), M.dec(r.get("DiamondWeight"))
    rate, task = M.dec(r.get("SellRate")), M.dec(r.get("TaskPrice"))
    return {
        "code": r.get("ProductCode"), "pid": r.get("ProductID"),
        "name": r.get("ProductDesc") or "", "gold": r.get("GoldCode") or "",
        "gold_desc": r.get("GoldDesc") or r.get("GoldCode") or "", "unit": pu,
        "section": r.get("SectionName") or "", "group": r.get("GroupName") or "",
        "ring": str(M.dec(r.get("RingSize"))), "stamp": str(M.dec(r.get("StampWeight"))),
        "tw": str(tw), "dw": str(dw), "gr": str(tw - dw),
        "rate": str(rate), "task": str(task),
        # tiền phần VÀNG (chưa cộng công) — để in "phép tính lộ thiên" cho người bán đọc to
        "tien_vang": str(M.round_vnd((tw - dw) / M.hs(pu) * rate * M.RATE_SCALE)),
        "amount": str(M.sell_amount(tw, dw, rate, task, pu)),
        "age": age_class(r.get("GoldCode")),
    }


def tim_hang(tu_khoa="", section="", gold="", limit=50):
    """Tìm hàng CÒN TỒN theo tên/quầy/tuổi vàng (Status='I')."""
    tu_khoa = (tu_khoa or "").strip()
    return client("tim_hang").query(
        f"SELECT TOP {int(limit)} p.ProductCode, p.ProductDesc, p.GoldCode, p.TotalWeight, "
        "ISNULL(p.TotalWeight,0)-ISNULL(p.DiamondWeight,0) AS GoldWeight, p.TaskPrice, p.RingSize, "
        "ISNULL(pg.GroupName,'') AS GroupName, ISNULL(s.SectionName,'') AS SectionName "
        "FROM T_PRODUCT p WITH (NOLOCK) "
        "LEFT JOIN I_PRODUCT_GROUP pg WITH (NOLOCK) ON pg.GroupID = p.GroupID "
        "LEFT JOIN T_SECTION s WITH (NOLOCK) ON s.SectionID = p.SectionID "
        "WHERE p.Status = 'I' AND (? = '' OR p.ProductDesc LIKE ? OR p.ProductCode LIKE ?) "
        "AND (? = '' OR p.SectionID = ?) AND (? = '' OR p.GoldCode = ?) "
        "ORDER BY p.ProductDesc",
        (tu_khoa, f"%{tu_khoa}%", f"{tu_khoa}%", section, section, gold, gold))


def ton_theo_quay():
    """Sổ tồn I_GOLD_BAL theo quầy + tuổi vàng (đọc 0,5ms)."""
    return client("ton_kho").query(
        "SELECT b.SectionID, ISNULL(s.SectionName,b.SectionID) AS SectionName, b.GoldCode, "
        "b.Qty_Bal, b.TotalWei_Bal, b.GoldWei_Bal "
        "FROM I_GOLD_BAL b WITH (NOLOCK) LEFT JOIN T_SECTION s WITH (NOLOCK) ON s.SectionID = b.SectionID "
        "WHERE b.Qty_Bal <> 0 OR b.TotalWei_Bal <> 0 ORDER BY s.SectionName, b.GoldCode")


# ─────────────────────────── KHÁCH HÀNG ───────────────────────────

def tim_khach(q="", limit=25):
    """Tra khách theo SĐT (gõ 3-4 số cuối) hoặc tên. Không dùng proc I_CUSTOMER_Lst
    (tên khách có dấu nháy đơn làm vỡ câu SQL động bên trong proc)."""
    q = (q or "").strip()
    if not q:
        return []
    return client("tim_khach").query(
        f"SELECT TOP {int(limit)} c.CustID, c.CustCode, c.CustName, c.Phone, c.Address, c.CMND, "
        "c.LastTradingDate, ISNULL(d.DiemDoiQua,0) AS Diem "
        "FROM I_CUSTOMER c WITH (NOLOCK) "
        "LEFT JOIN I_DIEMTICHLUY d WITH (NOLOCK) ON d.CustID = c.CustID "
        "WHERE c.Active = '1' AND c.CustID <> ? AND (c.Phone LIKE ? OR c.CustName LIKE ? OR c.CustCode LIKE ?) "
        "ORDER BY c.LastTradingDate DESC, c.CustName",
        (WALK_IN, f"%{q}%", f"%{q}%", f"{q}%"))


def khach_theo_id(cust_id):
    r = client("khach").query(
        "SELECT TOP 1 c.CustID, c.CustCode, c.CustName, c.Phone, c.Address, c.CMND, c.BirthDate, "
        "c.LastTradingDate, ISNULL(d.DiemDoiQua,0) AS Diem FROM I_CUSTOMER c WITH (NOLOCK) "
        "LEFT JOIN I_DIEMTICHLUY d WITH (NOLOCK) ON d.CustID = c.CustID WHERE c.CustID = ?", (cust_id,))
    return r[0] if r else None


def lich_su_khach(cust_id, limit=15):
    """Giao dịch gần nhất của khách: gộp cả BÁN và THÂU."""
    return client("ls_khach").query(
        f"SELECT TOP {int(limit)} * FROM ("
        "SELECT 'BAN' AS loai, TrnID, BillCode, TrnDate, TrnTime, PayAmount AS SoTien "
        "FROM TRN_RT_BUYSELL WITH (NOLOCK) WHERE CustID = ? AND IsDel = '0' "
        "UNION ALL "
        "SELECT 'THAU', TrnID, BillCode, TrnDate, TrnTime, TotalAmount "
        "FROM TRN_RT_BUYGOLD WITH (NOLOCK) WHERE CustID = ? AND IsDel = '0'"
        ") t ORDER BY TrnDate DESC, TrnTime DESC", (cust_id, cust_id))


def danh_sach_khach(q="", limit=60):
    q = (q or "").strip()
    return client("ds_khach").query(
        f"SELECT TOP {int(limit)} c.CustID, c.CustCode, c.CustName, c.Phone, c.Address, "
        "c.LastTradingDate, ISNULL(d.DiemDoiQua,0) AS Diem FROM I_CUSTOMER c WITH (NOLOCK) "
        "LEFT JOIN I_DIEMTICHLUY d WITH (NOLOCK) ON d.CustID = c.CustID "
        "WHERE c.Active = '1' AND c.CustID <> ? AND (? = '' OR c.Phone LIKE ? OR c.CustName LIKE ?) "
        "ORDER BY c.LastTradingDate DESC",
        (WALK_IN, q, f"%{q}%", f"%{q}%"))


# ─────────────────────────── NHÂN VIÊN / KÉT ───────────────────────────

def nhan_vien_ban():
    return cache.get_or_set("khbl:nvban", lambda: client("nv").query(
        "SELECT EmpID, EmpName FROM T_EMPLOYEE WITH (NOLOCK) WHERE ISNULL(Active,'1') = '1' "
        "ORDER BY EmpName"), 300)


def loai_vang_thau():
    """Các mã dẻ (Type='D') để chọn khi thâu vàng cũ."""
    return [r for r in bang_gia() if (r["Type"] or "").upper() == "D" and str(r["Active"]) == "1"]


# ─────────────────────────── HÓA ĐƠN TRONG NGÀY ───────────────────────────

def hoa_don_ngay(ngay_iso, limit=200):
    """Bán + thâu trong 1 ngày. CAST tham số cho ăn chỉ mục (SQL 2005)."""
    return client("hd_ngay").query(
        f"SELECT TOP {int(limit)} * FROM ("
        "SELECT 'BAN' AS loai, b.TrnID, b.BillCode, b.TrnTime, b.Status, b.IsDel, "
        "b.SellTotalAmount AS TienBan, b.BuyTotalAmount AS TienMua, b.PayAmount AS SoTien, "
        "ISNULL(c.CustName,'') AS CustName, ISNULL(e.EmpName,'') AS EmpName, b.CreatedDate "
        "FROM TRN_RT_BUYSELL b WITH (NOLOCK) "
        "LEFT JOIN I_CUSTOMER c WITH (NOLOCK) ON c.CustID = b.CustID "
        "LEFT JOIN T_EMPLOYEE e WITH (NOLOCK) ON e.EmpID = b.EmpID "
        "WHERE b.TrnDate = CAST(? AS datetime) "
        "UNION ALL "
        "SELECT 'THAU', t.TrnID, t.BillCode, t.TrnTime, t.Status, t.IsDel, "
        "0, t.TotalAmount, t.TotalAmount, ISNULL(c.CustName,''), ISNULL(e.EmpName,''), t.CreatedDate "
        "FROM TRN_RT_BUYGOLD t WITH (NOLOCK) "
        "LEFT JOIN I_CUSTOMER c WITH (NOLOCK) ON c.CustID = t.CustID "
        "LEFT JOIN T_EMPLOYEE e WITH (NOLOCK) ON e.EmpID = t.EmpID "
        "WHERE t.TrnDate = CAST(? AS datetime)"
        ") x ORDER BY x.TrnTime DESC", (ngay_iso, ngay_iso))


def tong_ngay(rows):
    ban = [r for r in rows if r["loai"] == "BAN" and str(r["IsDel"]) == "0"]
    thau = [r for r in rows if r["loai"] == "THAU" and str(r["IsDel"]) == "0"]
    return {
        "so_ban": len(ban), "tien_ban": sum((M.dec(r["SoTien"]) for r in ban), M.D0),
        "so_thau": len(thau), "tien_thau": sum((M.dec(r["SoTien"]) for r in thau), M.D0),
    }
