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
from .vn_text import chuan_hoa

# Bảng giá dùng ShopID = CHUỖI RỖNG (khác ShopID hóa đơn 'TSP141100000001')
XRATE_SHOP = ""
WALK_IN = "CU0000000000000"
# Ảnh khách lưu bằng ĐƯỜNG DẪN trên máy KK (SYS_PARAMETERS LuuAnhBangDuongDan=1)
DUONG_DAN_ANH = r"D:\PHANMEMVANG\HINHANHKH\\"

# 4 loại lên bảng giá đầu màn, theo số dòng bán thật (N9999 16.033 · 18K 8.250 · 24K 4.339 · BK 56)
GIA_NOI_BAT = ["N9999", "18K", "24K", "BK"]

AGE_CLASS = {"N9999": "9999", "D9999": "9999", "SJC": "9999", "DSJC": "9999",
             "24K": "24k", "D24K": "24k", "18K": "18k", "D18K": "18k",
             "BK": "bk", "DBK": "bk", "DBk": "bk", "DT": "bk", "VT": "vt", "VBK": "vt"}


def client(tag="pos"):
    return PmvClient(tag=tag)


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


def _quet(ma, till_id="", cust_id=WALK_IN):
    """Gọi proc quét mã, trả (row_thô, lỗi). row_thô giữ NGUYÊN mọi cột — dòng hàng gửi
    lên proc hóa đơn dựng từ đây (apps/pos/bill.dong_ban_tu_quet)."""
    ma = (ma or "").strip()
    if not ma:
        return None, {"code": "", "desc": "Chưa nhập mã hàng", "style": "cam"}
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
        return None, {"code": code, "desc": desc, "style": SCAN_ERR_STYLE.get(code, "do")}
    return row, None


def quet_ma(ma, till_id="", cust_id=WALK_IN):
    """Quét mã → dict(ok, item|err). Câu lỗi lấy NGUYÊN VĂN của vendor."""
    row, err = _quet(ma, till_id, cust_id)
    return {"ok": False, "err": err} if err else {"ok": True, "item": _item_tu_row(row)}


def quet_ma_row(ma, till_id="", cust_id=WALK_IN):
    """Bản thô của quet_ma — dùng khi cần dựng dòng hàng gửi lên proc hóa đơn."""
    return _quet(ma, till_id, cust_id)[0]


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
    """Tra khách theo SĐT (gõ 3-4 số cuối) · tên · CCCD · mã KH. Không dùng proc
    I_CUSTOMER_Lst (tên khách có dấu nháy đơn làm vỡ câu SQL động bên trong proc)."""
    q = (q or "").strip()
    if not q:
        return []
    return client("tim_khach").query(
        f"SELECT TOP {int(limit)} c.CustID, c.CustCode, c.CustName, c.Phone, c.Address, c.CMND, "
        "c.LastTradingDate, ISNULL(d.DiemDoiQua,0) AS Diem "
        "FROM I_CUSTOMER c WITH (NOLOCK) "
        "LEFT JOIN I_DIEMTICHLUY d WITH (NOLOCK) ON d.CustID = c.CustID "
        "WHERE c.Active = '1' AND c.CustID <> ? AND (c.Phone LIKE ? OR c.CustName LIKE ? "
        "  OR c.CustCode LIKE ? OR c.CMND LIKE ?) "
        "ORDER BY c.LastTradingDate DESC, c.CustName",
        (WALK_IN, f"%{q}%", f"%{q}%", f"{q}%", f"%{q}%"))


def khach_theo_id(cust_id):
    r = client("khach").query(
        "SELECT TOP 1 c.CustID, c.CustCode, c.CustName, c.Phone, c.Address, c.CMND, c.BirthDate, "
        "c.Gender, COALESCE(NULLIF(c.CustTypeID,''), c.CustType) AS CustType, "
        "c.Email, c.Notes, c.NgayCap, c.NoiCap, c.Company, c.Masothue, "
        "c.ImagePath, c.ImagePathMatTruoc, c.ImagePathMatSau, c.Active, "
        "c.LastTradingDate, ISNULL(d.DiemDoiQua,0) AS Diem FROM I_CUSTOMER c WITH (NOLOCK) "
        "LEFT JOIN I_DIEMTICHLUY d WITH (NOLOCK) ON d.CustID = c.CustID WHERE c.CustID = ?", (cust_id,))
    return _dep_khach(r[0]) if r else None


# Loại khách — proc vendor ghi vào I_CUSTOMER.CustTypeID; dữ liệu cũ từng dùng CustType
# nên câu SELECT đọc CustTypeID trước và rơi về CustType. TRỐNG/NULL = Thường.
CUST_TYPES = [
    {"ma": "", "ten": "Thường", "badge": ""},
    {"ma": "VIP", "ten": "VIP", "badge": "khbl-badge--cam"},
    {"ma": "VVIP", "ten": "VVIP", "badge": "khbl-badge--xanh"},
    {"ma": "CANHBAO", "ten": "Cảnh báo", "badge": "khbl-badge--do"},
]
CUST_TYPE_MAP = {t["ma"]: t for t in CUST_TYPES}


# Xưng hô đầu tên — dùng để CẢNH BÁO lệch giới tính, KHÔNG dùng để sửa dữ liệu
_TIEN_TO_NAM = ("anh ", "ông ", "chú ", "a ", "chu ", "ong ", "chau trai ")
_TIEN_TO_NU = ("chị ", "cô ", "bà ", "chi ", "co ", "ba ", "e ", "em gai ")


def _dep_khach(r):
    """Bổ sung trường hiển thị: loại khách, giới tính (bit: 1 = Nam, 0 = Nữ — đã đối chiếu
    106 khách Gender=1 đều là 'Anh/Chú'). ⚠ Phần mềm để MẶC ĐỊNH 0 nên 13.805 khách tên
    'Anh …' vẫn đang mang giới tính Nữ — đánh dấu `gt_lech` để UI báo, KHÔNG tự sửa."""
    # Dữ liệu cũ có thể đã lưu ở trạng thái mojibake nửa chừng. Chuẩn hóa lúc đọc
    # để danh sách/popup hiển thị đúng ngay; lần Sửa + Lưu kế tiếp sẽ ghi bản sạch.
    for field in ("CustName", "Address", "NoiCap"):
        if r.get(field):
            r[field] = chuan_hoa(r[field])[0]
    loai = (r.get("CustType") or "").strip().upper()
    r["loai"] = CUST_TYPE_MAP.get(loai, CUST_TYPE_MAP[""])
    r["Active"] = "1" if r.get("Active") is True or str(r.get("Active")) == "1" else "0"
    g = r.get("Gender")
    la_nam = g is True or str(g) in ("1", "True")
    r["gioi_tinh"] = "Nam" if la_nam else "Nữ"
    ten = (r.get("CustName") or "").strip().lower() + " "
    goi_nam = ten.startswith(_TIEN_TO_NAM)
    goi_nu = ten.startswith(_TIEN_TO_NU)
    r["gt_lech"] = (goi_nam and not la_nam) or (goi_nu and la_nam)
    return r


def khach_loc(key="", addr="", ngay_sinh="", trang=1, moi_trang=50):
    """Lọc khách: key = SĐT / CCCD / họ tên · addr = địa chỉ · ngay_sinh = ISO yyyy-mm-dd.
    Phân trang bằng ROW_NUMBER (SQL 2005 không có OFFSET/FETCH). Trả (dòng, tổng, số trang)."""
    key = (key or "").strip()
    addr = (addr or "").strip()
    ngay = (ngay_sinh or "").strip()
    dk = ("WHERE c.CustID <> ? "
          "AND (? = '' OR c.Phone LIKE ? OR c.CMND LIKE ? OR c.CustName LIKE ? OR c.CustCode LIKE ?) "
          "AND (? = '' OR c.Address LIKE ?) "
          "AND (? = '' OR c.BirthDate = CAST(? AS datetime))")
    ps = (WALK_IN, key, f"%{key}%", f"%{key}%", f"%{key}%", f"{key}%", addr, f"%{addr}%", ngay, ngay)
    c = client("khach_loc")
    tong = c.query(f"SELECT COUNT(*) AS n FROM I_CUSTOMER c WITH (NOLOCK) {dk}", ps)[0]["n"]
    tu = (max(1, int(trang)) - 1) * moi_trang + 1
    den = tu + moi_trang - 1
    rows = c.query(
        "SELECT * FROM (SELECT ROW_NUMBER() OVER (ORDER BY c.LastTradingDate DESC, c.CustName) AS rn, "
        "c.CustID, c.CustCode, c.CustName, c.Address, c.CMND, c.Phone, c.BirthDate, "
        "COALESCE(NULLIF(c.CustTypeID,''), c.CustType) AS CustType, "
        "c.Gender, c.LastTradingDate, c.Active "
        f"FROM I_CUSTOMER c WITH (NOLOCK) {dk}) t WHERE t.rn BETWEEN ? AND ? ORDER BY t.rn",
        ps + (tu, den))
    so_trang = max(1, -(-tong // moi_trang))
    return [_dep_khach(r) for r in rows], tong, so_trang


def lich_su_khach(cust_id, limit=15):
    """Giao dịch gần nhất của khách: gộp cả BÁN và THÂU. Live trước, KK chết → kho lịch sử."""
    from apps.pmv import hist_read as HR
    return HR.doc(lambda c: c.query(
        f"SELECT TOP {int(limit)} * FROM ("
        "SELECT 'BAN' AS loai, TrnID, BillCode, TrnDate, TrnTime, PayAmount AS SoTien "
        "FROM TRN_RT_BUYSELL WITH (NOLOCK) WHERE CustID = ? AND IsDel = '0' "
        "UNION ALL "
        "SELECT 'THAU', TrnID, BillCode, TrnDate, TrnTime, TotalAmount "
        "FROM TRN_RT_BUYGOLD WITH (NOLOCK) WHERE CustID = ? AND IsDel = '0'"
        ") t ORDER BY TrnDate DESC, TrnTime DESC", (cust_id, cust_id)), tag="ls_khach")


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


def tim_nhan_vien(q="", limit=15):
    """Tra nhân viên bán. GĐ chốt gõ theo TÊN GỌI (chữ cuối của họ tên) nên kết quả xếp
    tên-gọi-khớp lên trước, rồi mới tới khớp ở giữa: gõ 'nguyện' ra 'Trần Ngọc Nguyện'."""
    q = (q or "").strip().lower()
    ds = nhan_vien_ban()
    if not q:
        return ds[:limit]
    dau, giua = [], []
    for e in ds:
        ten = (e.get("EmpName") or "").strip()
        goi = ten.split()[-1].lower() if ten else ""
        if goi.startswith(q):
            dau.append(e)
        elif q in ten.lower():
            giua.append(e)
    return (dau + giua)[:limit]


def loai_de():
    """Danh mục LOẠI DẺ (vàng cũ khách đưa) kèm giá đổi hiện hành.
    'Dẻ' là từ của chính phần mềm vendor: I_GOLD.GoldType='D' (D18K, D9999, DBk...).
    Bảng giá có sẵn đúng các mã đó nên giá đổi lấy thẳng, không phải quy đổi."""
    def _lay():
        ds = client("loai_de").query(
            "SELECT GoldCode, GoldDesc, PriceUnit FROM I_GOLD WITH (NOLOCK) "
            "WHERE GoldType = 'D' AND ISNULL(Active,'1') = '1' ORDER BY OrderBy, GoldCode")
        gia = {g["GoldCcy"]: g for g in bang_gia()}
        for x in ds:
            g = gia.get(x["GoldCode"], {})
            x["BuyRate"] = g.get("BuyRate") or 0
            # Giá BÁN RA để ĐỔI NGANG: SellRate của chính dòng dẻ; nếu 0 thì lấy của loại vàng bán tương ứng
            base = M.de_base(x["GoldCode"])
            x["base"] = base
            sell = M.dec(g.get("SellRate"))
            if sell <= 0:
                sell = M.dec((gia.get(base) or {}).get("SellRate"))
            x["SellRate"] = str(sell)
            x["don_vi"] = "₫/chỉ" if (x.get("PriceUnit") or "L").upper() in ("L", "M") else "₫/g"
        return ds
    return cache.get_or_set("khbl:loaide", _lay, 30)


def loai_vang_thau():
    """Các mã dẻ (Type='D') để chọn khi thâu vàng cũ."""
    return [r for r in bang_gia() if (r["Type"] or "").upper() == "D" and str(r["Active"]) == "1"]


# ─────────────────────────── HÓA ĐƠN TRONG NGÀY ───────────────────────────

def hoa_don_ngay(ngay_iso, limit=200):
    """Bán + thâu trong 1 ngày. CAST tham số cho ăn chỉ mục (SQL 2005).
    Ngày QUÁ KHỨ → kho lịch sử; hôm nay → live (KK chết → lùi về kho)."""
    from apps.pmv import hist_read as HR
    return HR.doc(lambda c: c.query(
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
        ") x ORDER BY x.TrnTime DESC", (ngay_iso, ngay_iso)), ngay_iso=ngay_iso, tag="hd_ngay")


def tong_quan(ngay_iso, so_ngay=7):
    """Số liệu trang Tổng quan. Ngày QUÁ KHỨ → kho lịch sử; hôm nay → live (KK chết → lùi về kho)."""
    from apps.pmv import hist_read as HR
    return HR.doc(lambda c: _tong_quan(c, ngay_iso, so_ngay), ngay_iso=ngay_iso, tag="tong_quan")


def _tong_quan(c, ngay_iso, so_ngay=7):
    """Gộp trong ÍT truy vấn nhất có thể (nguồn qua LAN). c = client đã chọn kho."""
    import datetime as _dt

    d0 = _dt.date.fromisoformat(ngay_iso)
    tu = (d0 - _dt.timedelta(days=so_ngay - 1)).isoformat()

    ban = c.query(
        "SELECT CONVERT(VARCHAR(10), TrnDate, 23) AS ngay, COUNT(*) AS so, "
        "SUM(ISNULL(PayAmount,0)) AS tien, SUM(ISNULL(SellTotalAmount,0)) AS ban_ra, "
        "SUM(ISNULL(BuyTotalAmount,0)) AS vang_cu "
        "FROM TRN_RT_BUYSELL WITH (NOLOCK) "
        "WHERE TrnDate >= CAST(? AS datetime) AND TrnDate <= CAST(? AS datetime) AND IsDel = '0' "
        "GROUP BY CONVERT(VARCHAR(10), TrnDate, 23) ORDER BY 1", (tu, ngay_iso))
    thau = c.query(
        "SELECT CONVERT(VARCHAR(10), TrnDate, 23) AS ngay, COUNT(*) AS so, "
        "SUM(ISNULL(TotalAmount,0)) AS tien FROM TRN_RT_BUYGOLD WITH (NOLOCK) "
        "WHERE TrnDate >= CAST(? AS datetime) AND TrnDate <= CAST(? AS datetime) AND IsDel = '0' "
        "GROUP BY CONVERT(VARCHAR(10), TrnDate, 23) ORDER BY 1", (tu, ngay_iso))
    mb = {r["ngay"]: r for r in ban}
    mt = {r["ngay"]: r for r in thau}

    ngays = [(d0 - _dt.timedelta(days=i)).isoformat() for i in range(so_ngay - 1, -1, -1)]
    chuoi, dinh = [], M.D0
    for n in ngays:
        b = mb.get(n) or {}
        t = mt.get(n) or {}
        tien = M.dec(b.get("tien"))
        dinh = max(dinh, tien)
        chuoi.append({"ngay": n, "nhan": n[8:10] + "/" + n[5:7],
                      "so_ban": b.get("so") or 0, "tien_ban": tien,
                      "so_thau": t.get("so") or 0, "tien_thau": M.dec(t.get("tien"))})
    for x in chuoi:
        x["pct"] = int(x["tien_ban"] / dinh * 100) if dinh > 0 else 0

    hom_nay = chuoi[-1]
    khach_moi = c.query(
        "SELECT COUNT(*) AS n FROM I_CUSTOMER WITH (NOLOCK) WHERE DateOfJoining = CAST(? AS datetime)",
        (ngay_iso,))[0]["n"]
    ton = c.query(
        "SELECT ISNULL(ms.MainSectionName, N'Khác') AS nhom, SUM(ISNULL(b.Qty_Bal,0)) AS sl, "
        "SUM(ISNULL(b.GoldWei_Bal,0)) AS tl FROM I_GOLD_BAL b WITH (NOLOCK) "
        "LEFT JOIN T_SECTION s WITH (NOLOCK) ON s.SectionID = b.SectionID "
        "LEFT JOIN T_MAINSECTION ms WITH (NOLOCK) ON ms.MainSectionID = s.MainSectionID "
        "GROUP BY ms.MainSectionName ORDER BY 1")
    return {
        "chuoi": chuoi, "hom_nay": hom_nay, "khach_moi": khach_moi, "ton": ton,
        "tuan_ban": sum((x["tien_ban"] for x in chuoi), M.D0),
        "tuan_thau": sum((x["tien_thau"] for x in chuoi), M.D0),
        "tuan_so_ban": sum(x["so_ban"] for x in chuoi),
        "tong_khach": c.query("SELECT COUNT(*) AS n FROM I_CUSTOMER WITH (NOLOCK)")[0]["n"],
        "hang_ton": c.query("SELECT COUNT(*) AS n FROM T_PRODUCT WITH (NOLOCK) WHERE Status = 'I'")[0]["n"],
    }


def tong_ngay(rows):
    ban = [r for r in rows if r["loai"] == "BAN" and str(r["IsDel"]) == "0"]
    thau = [r for r in rows if r["loai"] == "THAU" and str(r["IsDel"]) == "0"]
    return {
        "so_ban": len(ban), "tien_ban": sum((M.dec(r["SoTien"]) for r in ban), M.D0),
        "so_thau": len(thau), "tien_thau": sum((M.dec(r["SoTien"]) for r in thau), M.D0),
    }


def thong_tin_tiem():
    """Tên · địa chỉ · điện thoại tiệm cho chân trang (đọc T_SHOP, cache 1 giờ)."""
    def _doc():
        r = client("tiem").query(
            "SELECT TOP 1 ShopID, ShopCode, ShopName, ShopAddress, ShopTel, ShopTax "
            "FROM T_SHOP WITH (NOLOCK) WHERE Active = '1'")
        return r[0] if r else {}
    try:
        return cache.get_or_set("khbl:tiem", _doc, 3600)
    except Exception:
        return {}
