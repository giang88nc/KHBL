"""
HÓA ĐƠN BÁN LẺ — dựng · lưu · sửa · chốt · hủy, qua ĐÚNG proc vendor.

Đây là nơi DUY NHẤT biết cách nói chuyện với TRN_RT_BUYSELL_*. View chỉ đưa dữ liệu
người dùng nhập vào và nhận kết quả; không view nào được tự gọi proc hóa đơn.

Kiểm chứng thật trên bản thử 03/09/2026 (xem manage.py smoke_ban_hang):
  TẠO   TRN_RT_BUYSELL_Ins       → TrnID + BillCode do vendor cấp, Status 'W'
  SỬA   TRN_RT_BUYSELL_Upd       → thêm/bớt/đổi dòng hàng, đổi vàng đổi, đổi các khoản tiền
  CHỐT  TRN_RT_BUYSELL_Complete + T_TILL_TXN_Proc → Status 'C', hàng sang 'S'
  MỞ LẠI T_TILL_TXN_Del @p_Type='1' → 'C' về 'W' (điều kiện để sửa)
  HỦY   TRN_RT_BUYSELL_Del       → xóa hẳn, hàng về kho 'I'

BA LUẬT PHẢI NHỚ (đều đã trả giá để biết):
1. Hóa đơn ĐÃ CHỐT (C) thì proc _Upd TỪ CHỐI (rc=-1). Muốn sửa phải mở khóa về 'W' trước.
2. Mọi lệnh _Upd/_Del so mốc khóa lạc quan; sai mốc thì proc trả rc=0 mà KHÔNG làm gì.
   Vì vậy hàm nào ở đây cũng ĐỌC LẠI để xác nhận, không tin mỗi mã trả về.
3. Món hàng đang nằm trên hóa đơn KHÔNG quét lại được (nó đã mang trạng thái đã bán).
   Mở hóa đơn ra sửa thì dòng hàng phải dựng từ TRN_RT_BUYSELL_Get — xem dong_ban_tu_phieu.
"""
from decimal import Decimal

from apps.pmv import money as M
from apps.pmv.client import PmvClient, PmvProcError

from . import services as S

# Trạng thái hóa đơn của vendor
NHAP, CHOT_ROI = "W", "C"
TEN_TRANG_THAI = {NHAP: "Còn nháp", CHOT_ROI: "Đã chốt"}

# Các tham số vendor luôn đòi nhưng nghiệp vụ bán lẻ của tiệm không dùng.
# ⚠ Nhiều cái khai kiểu varchar nhưng proc CONVERT sang numeric — để rỗng là chết,
# phải là "0". Danh sách chép từ chính lệnh app desktop gửi (nhật ký hành vi).
_MAC_DINH = dict(
    p_DebitAmount=0, p_IsCatGia="0", p_IsCatGIa="0", p_Add="0", p_TaskPriceAdd="0",
    DiscountPercent="0", OldDebitAmount="0", p_TotalPromotionAmount=0,
    p_TienKhachTraThuc="0", p_TienTraLai="0", p_SoLuongDoi="0", p_DiemDoi="0",
    p_TienDoiQua="0", p_KhongTLD="0", p_TienCoc=0, p_IsGiaoDichNhanh="0",
    p_MaQuaTang="", p_Discount="0", p_GoldCode="", p_Description="", p_SoHDTuNhap="",
    Desc1="", Desc2="", Desc3="", Desc4="", Desc5="", p_IsSync="0", p_TrnID_GDN="",
)

# Cột 1 dòng VÀNG BÁN cần gửi lại khi sửa (đọc ra từ TRN_RT_BUYSELL_Get)
COT_DONG_BAN = (
    "STT", "ProductID", "ProductCode", "ProductDesc", "PriceCcy", "PriceUnit", "CcyRate",
    "CcyRateTaskPrice", "TotalWeight", "GoldWeight", "DiamondWeight", "SectionID", "SectionName",
    "TaskPrice", "RingSize", "InPrice", "GoldCode", "GoldDesc", "SellRate", "SellAmount",
    "CatNi", "GoldReal", "SL", "SL_Ban", "A", "DiamondPrice", "GiaBanMon", "DiamondTaskPrice",
    "DiamondInPrice", "GiaVon", "ProductTypeCode", "TienCongThemMonHang", "LoaiTienChiTra",
    "TienQuyDoi", "Discounts", "CongBanTrenTL", "OrderBy", "TienBotTrenChi",
    "GroupID", "GroupName", "StampWeight", "WeightUnit",
)
COT_DONG_DOI = (
    "GoldCode", "GoldDesc", "PriceUnit", "GoldWeight", "DiamondWeight", "Dirty",
    "TruDoTrenChi", "TotalGoldWeight", "BuyRate", "BuyAmount", "PercentValue",
    "GCatNi", "DiamondPrice",
)


# ─────────────────────────── dựng dòng ───────────────────────────
def dong_ban_tu_quet(r, stt=1):
    """1 dòng VÀNG BÁN từ kết quả quét mã (T_PRODUCT_GetByCodeForSell)."""
    tw, dw = M.dec(r.get("TotalWeight")), M.dec(r.get("DiamondWeight"))
    rate, task = M.dec(r.get("SellRate")), M.dec(r.get("TaskPrice"))
    pu = (r.get("PriceUnit") or "L").upper()
    tien = M.sell_amount(tw, dw, rate, task, pu)
    return tien, {
        "STT": stt,
        "ProductID": r.get("ProductID"), "ProductCode": r.get("ProductCode"),
        "ProductDesc": r.get("ProductDesc") or "", "PriceCcy": "VND", "PriceUnit": pu,
        "CcyRate": "1000.000", "CcyRateTaskPrice": "1000.000",
        "TotalWeight": tw, "GoldWeight": tw - dw, "DiamondWeight": dw,
        "SectionID": r.get("SectionID") or "", "SectionName": r.get("SectionName") or "",
        "GroupID": r.get("GroupID") or "", "GroupName": r.get("GroupName") or "",
        "StampWeight": M.dec(r.get("StampWeight")), "WeightUnit": r.get("WeightUnit") or pu,
        "TaskPrice": task, "RingSize": M.dec(r.get("RingSize")), "InPrice": "0.000",
        "GoldCode": r.get("GoldCode") or "", "GoldDesc": r.get("GoldDesc") or "",
        "SellRate": rate, "SellAmount": tien, "CatNi": "0", "GoldReal": tw - dw,
        "SL": "1", "SL_Ban": "1", "A": "1", "DiamondPrice": "0.000", "GiaBanMon": "0.000",
        "DiamondTaskPrice": "0.000", "DiamondInPrice": "0.000", "GiaVon": "0.000",
        "ProductTypeCode": r.get("ProductTypeCode") or "TTM", "TienCongThemMonHang": "0",
        "LoaiTienChiTra": "VND", "TienQuyDoi": tien, "Discounts": "0",
        "CongBanTrenTL": "0.000", "OrderBy": "0", "TienBotTrenChi": "0.000",
    }


def dong_ban_tu_phieu(r):
    """1 dòng VÀNG BÁN dựng lại TỪ HÓA ĐƠN — dùng khi mở hóa đơn ra sửa.
    KHÔNG quét lại mã được: món đang trên hóa đơn đã mang trạng thái đã bán (luật 3)."""
    return M.dec(r.get("SellAmount")), {k: r[k] for k in COT_DONG_BAN if r.get(k) is not None}


def dong_doi(gold_code, gold_desc, tong_tl, tl_hot, gia, price_unit="L", doi_ngang=False):
    """1 dòng VÀNG ĐỔI (dẻ khách đưa). tl_vàng = tổng TL − TL hột.
    doi_ngang=True: dòng này định GIÁ BÁN RA (đổi ngang cùng tuổi vàng) — chỉ để đánh dấu/hiệu ứng,
    PMV vẫn nhận là 1 dòng TRN_RT_BUYSELL_BUYGOLD với BuyRate = giá bán ra."""
    tong_tl, tl_hot = M.dec(tong_tl), M.dec(tl_hot)
    tl_vang = tong_tl - tl_hot
    tien = M.buy_amount_in_bill(tl_vang, gia, 100, price_unit)
    return tien, {
        "GoldCode": gold_code, "GoldDesc": gold_desc, "PriceUnit": price_unit,
        "TotalGoldWeight": tong_tl, "DiamondWeight": tl_hot, "GoldWeight": tl_vang,
        "Dirty": "0.00", "TruDoTrenChi": "0.00", "BuyRate": M.dec(gia), "BuyAmount": tien,
        "PercentValue": "100.00", "GCatNi": gold_code, "DiamondPrice": "0.000",
        "DoiNgang": "1" if doi_ngang else "0",
    }


def dong_doi_tu_phieu(r):
    return M.dec(r.get("BuyAmount")), {k: r[k] for k in COT_DONG_DOI if r.get(k) is not None}


# ─────────────────────────── tính tiền ───────────────────────────
def tinh_tong(tien_ban, tien_doi, bot=0, cong_them=0, vang_them=0, coc=0):
    """Công thức GĐ chốt 03/09/2026 (phần \"còn lại\" đo trên 18.441 hóa đơn thật):
         còn lại   = tiền vàng mới − tiền vàng cũ
         khách trả = còn lại − bớt + công thêm + vàng thêm − cọc"""
    ban, doi = M.dec(tien_ban), M.dec(tien_doi)
    bot, cong_them = M.dec(bot), M.dec(cong_them)
    vang_them, coc = M.dec(vang_them), M.dec(coc)
    # Làm tròn về HÀNG NGHÌN các con số quyết toán (GĐ chốt 05/09/2026): giá vàng/công đều
    # theo nghìn nên trong thực tế đây là no-op, nhưng bảo đảm màn hình + PayAmount không lộ số lẻ.
    con_lai = M.tron_ngan(ban - doi)
    khach_tra = M.tron_ngan(con_lai - bot + cong_them + vang_them - coc)
    return {
        "vang_moi": ban, "vang_cu": doi, "con_lai": con_lai,
        "vang_them": vang_them, "cong_them": cong_them, "bot": bot, "coc": coc,
        "khach_tra": khach_tra,
    }


# ─────────────────────────── đọc ───────────────────────────
def ma_du_kien(c=None):
    """Mã hóa đơn DỰ KIẾN cho phiếu kế tiếp — chỉ để người bán dễ hình dung.
    ⚠ Mã THẬT do vendor cấp lúc lưu (RULE 5); máy KK bán song song có thể lấy trước
    số này. Không bao giờ dùng giá trị đây để ghi vào CSDL."""
    c = c or S.client("ma_du_kien")
    r = c.query("SELECT MAX(TrnID) AS m FROM TRN_RT_BUYSELL WITH (NOLOCK)")
    cuoi = (r[0]["m"] if r else None) or ""
    if len(cuoi) < 4 or not cuoi[3:].isdigit():
        return ""
    return cuoi[:3] + str(int(cuoi[3:]) + 1).zfill(len(cuoi) - 3)


def doc(trn_id, c=None):
    """Đọc trọn hóa đơn về dạng form dùng được: đầu phiếu + dòng bán + dòng đổi + tổng."""
    c = c or S.client("doc_hoa_don")
    b = c.bill(trn_id)
    h = b["header"]
    if not h:
        return None
    ban = [dong_ban_tu_phieu(x) for x in b["lines"]]
    doi = [dong_doi_tu_phieu(x) for x in b["old_gold"]]
    return {
        "trn_id": h["TrnID"], "bill_code": h.get("BillCode") or "",
        "status": h.get("Status"), "ten_trang_thai": TEN_TRANG_THAI.get(h.get("Status"), h.get("Status")),
        "sua_duoc": h.get("Status") == NHAP,      # luật 1
        "ngay": h.get("TrnDate"), "gio": h.get("TrnTime"),
        "cust_id": h.get("CustID") or "", "khach": h.get("CustName") or "",
        "emp_id": h.get("EmpID") or "", "nhan_vien": h.get("EmpName") or "",
        "ghi_chu": h.get("Description") or "",
        "ban": ban, "doi": doi,
        "tong": tinh_tong(sum((x[0] for x in ban), Decimal(0)),
                          sum((x[0] for x in doi), Decimal(0)),
                          h.get("Discount"), h.get("TaskPriceAdd"),
                          h.get("AddMoney"), h.get("TienCoc")),
    }


DS_TRAN = 2000   # trần dòng popup DANH SÁCH — chạm trần thì popup cảnh báo thống kê chưa đủ


def danh_sach(d1, d2, *, emp_id="", khach="", trang_thai="", c=None, limit=DS_TRAN):
    """Danh sách hóa đơn BÁN theo KHOẢNG NGÀY [d1, d2] + lọc NV / khách / trạng thái — popup
    DANH SÁCH (thiết kế lại 07/09/2026). Trả (rows, thong_ke).
    Nguồn: d1 quá khứ → kho lịch sử, d1 = hôm nay → live (KK chết → lùi về kho) — cùng quy ước
    services.hoa_don_loc. Khoảng ngày chỉ dùng tham số, không ghép chuỗi vào SQL."""
    emp_id, key = (emp_id or "").strip(), (khach or "").strip()
    tt = (trang_thai or "").strip().upper()
    sql = (f"SELECT TOP {int(limit)} b.TrnID, b.BillCode, b.TrnDate, b.TrnTime, b.Status, b.PayAmount, "
           "b.SellTotalAmount, b.BuyTotalAmount, ISNULL(b.Discount,0) AS Discount, "
           "ISNULL(b.TienCoc,0) AS TienCoc, k.CustName, k.Phone, e.EmpName "
           "FROM TRN_RT_BUYSELL b WITH (NOLOCK) "
           "LEFT JOIN I_CUSTOMER k WITH (NOLOCK) ON k.CustID = b.CustID "
           "LEFT JOIN T_EMPLOYEE e WITH (NOLOCK) ON e.EmpID = b.EmpID "
           "WHERE b.IsDel = '0' AND b.TrnDate >= CAST(? AS datetime) "
           "AND b.TrnDate < DATEADD(day, 1, CAST(? AS datetime)) "
           "AND (? = '' OR b.EmpID = ?) AND (? = '' OR b.Status = ?) "
           "AND (? = '' OR k.CustName LIKE ? OR k.Phone LIKE ? OR k.CMND LIKE ?) "
           "ORDER BY b.TrnDate DESC, b.TrnTime DESC")
    args = (d1, d2, emp_id, emp_id, tt, tt, key, f"%{key}%", f"%{key}%", f"%{key}%")
    if c is not None:
        rows = c.query(sql, args)
    else:
        from apps.pmv import hist_read as HR
        rows = HR.doc(lambda cl: cl.query(sql, args), ngay_iso=d1, tag="ds_hoa_don")
    return rows, thong_ke_ds(rows)


def thong_ke_ds(rows):
    """Số liệu tổng cho đầu popup DANH SÁCH: số đơn, đã chốt / nháp, Σ vàng mới·cũ·bớt·cọc·khách trả."""
    tk = {"so": len(rows), "chot": 0, "nhap": 0, "vang_moi": Decimal(0), "vang_cu": Decimal(0),
          "bot": Decimal(0), "coc": Decimal(0), "khach_tra": Decimal(0)}
    for r in rows:
        tk["chot" if r.get("Status") == CHOT_ROI else "nhap"] += 1
        tk["vang_moi"] += M.dec(r.get("SellTotalAmount"))
        tk["vang_cu"] += M.dec(r.get("BuyTotalAmount"))
        tk["bot"] += M.dec(r.get("Discount"))
        tk["coc"] += M.dec(r.get("TienCoc"))
        tk["khach_tra"] += M.dec(r.get("PayAmount"))
    return tk


def trong_ngay(ngay, c=None, limit=100):
    """Danh sách hóa đơn của 1 ngày — cho nút DANH SÁCH.
    Không truyền c: ngày QUÁ KHỨ → kho lịch sử, hôm nay → live (KK chết → lùi về kho)."""
    sql = (f"SELECT TOP {int(limit)} b.TrnID, b.BillCode, b.TrnTime, b.Status, b.PayAmount, "
           "b.SellTotalAmount, b.BuyTotalAmount, k.CustName, e.EmpName "
           "FROM TRN_RT_BUYSELL b WITH (NOLOCK) "
           "LEFT JOIN I_CUSTOMER k WITH (NOLOCK) ON k.CustID = b.CustID "
           "LEFT JOIN T_EMPLOYEE e WITH (NOLOCK) ON e.EmpID = b.EmpID "
           "WHERE b.IsDel = '0' AND b.TrnDate = ? ORDER BY b.TrnTime DESC")
    if c is not None:
        return c.query(sql, (ngay,))
    from apps.pmv import hist_read as HR
    return HR.doc(lambda cl: cl.query(sql, (ngay,)), ngay_iso=ngay, tag="ds_hoa_don")


# ─────────────────────────── ghi ───────────────────────────
def _tham_so(c, proc, **rieng):
    co = {n for n, _ in c.params_of(proc)}
    d = {k: v for k, v in _MAC_DINH.items() if k in co}
    d.update({k: v for k, v in rieng.items() if k in co or not k.startswith("p_")})
    return d


def luu(*, trn_id, ban, doi, ngay, gio, cust_id, emp_id, till_id, shop_id, user_id,
        ghi_chu="", bot=0, cong_them=0, vang_them=0, coc=0, c=None):
    """trn_id rỗng = TẠO MỚI, có trn_id = SỬA. Trả dict(trn_id, bill_code, tong).

    Sau khi ghi LUÔN đọc lại đối chiếu — proc có thể trả rc=0 mà không đổi gì (luật 2)."""
    c = c or S.client("luu_hoa_don")
    t_ban = sum((x[0] for x in ban), Decimal(0))
    t_doi = sum((x[0] for x in doi), Decimal(0))
    tong = tinh_tong(t_ban, t_doi, bot, cong_them, vang_them, coc)
    xml = c.xml_nhieu_bang([("TRN_RT_BUYSELL_SELL", [x[1] for x in ban]),
                            ("TRN_RT_BUYSELL_BUYGOLD", [x[1] for x in doi])])
    chung = dict(
        p_TrnDate=ngay, p_TrnTime=gio, p_CustID=cust_id or S.WALK_IN,
        p_SellTotalAmount=c.money(t_ban), p_BuyTotalAmount=c.money(t_doi),
        p_TotalAmount=c.money(tong["con_lai"]), p_PayAmount=c.money(tong["khach_tra"]),
        p_Discount=c.money(bot), p_TaskPriceAdd=c.money(cong_them),
        p_Add=c.money(vang_them), p_TienCoc=c.money(coc),
        p_Status=NHAP, p_CreatedBy=user_id, p_EmpID=emp_id or "", p_ShopID=shop_id,
        p_TillID=till_id, p_Description=ghi_chu, p_BanLeBanSi="BL",
        p_Trn_RT_BUYSELL=xml, p_TrnDateTime_Upd_GDN=PmvClient.MOC_TRONG,
    )
    ma_hang = sorted(x[1].get("ProductCode") for x in ban)

    if not trn_id:
        rc, sets = c.call("TRN_RT_BUYSELL_Ins", write=True, day_du=True, raise_on_rc=False,
                          **_tham_so(c, "TRN_RT_BUYSELL_Ins", p_TrnID="", **chung))
        moi = next((r["TrnID"] for s in sets for r in s if r.get("TrnID")), None)
        if rc != 0 or not moi:
            raise PmvProcError("TRN_RT_BUYSELL_Ins", rc, sets)
        trn_id = moi
    else:
        cu = c.query("SELECT Status FROM TRN_RT_BUYSELL WITH (NOLOCK) WHERE TrnID=?", (trn_id,))
        if not cu:
            raise PmvProcError("TRN_RT_BUYSELL_Upd", -1, [{"loi": f"Không thấy hóa đơn {trn_id}"}])
        if cu[0]["Status"] != NHAP:                                    # luật 1
            raise PmvProcError("TRN_RT_BUYSELL_Upd", -1, [{
                "loi": "Hóa đơn đã chốt — phải MỞ LẠI (hủy phần két) rồi mới sửa được."}])
        moc = c.moc_khoa("TRN_RT_BUYSELL", "TrnID", trn_id)
        rc, sets = c.call("TRN_RT_BUYSELL_Upd", write=True, day_du=True, raise_on_rc=False,
                          **_tham_so(c, "TRN_RT_BUYSELL_Upd", p_TrnID=trn_id, p_UserUpd=user_id,
                                     p_TrnDateTime_Upd=PmvClient.fmt_moc(moc), **chung))
        if rc != 0:
            raise PmvProcError("TRN_RT_BUYSELL_Upd", rc, sets)

    # ── đối chiếu lại: rc=0 KHÔNG bảo đảm dữ liệu đã đổi (luật 2) ──
    h = c.query("SELECT BillCode, Status, PayAmount FROM TRN_RT_BUYSELL WITH (NOLOCK) WHERE TrnID=?",
                (trn_id,))
    thuc = sorted(x["ProductCode"] for x in c.query(
        "SELECT ProductCode FROM TRN_RT_BUYSELL_SELL WITH (NOLOCK) WHERE TrnID=?", (trn_id,)))
    if not h or thuc != ma_hang or M.dec(h[0]["PayAmount"]) != tong["khach_tra"]:
        raise PmvProcError("TRN_RT_BUYSELL", -2, [{
            "loi": f"Lưu xong nhưng đọc lại KHÔNG khớp — hàng {thuc} ≠ {ma_hang} "
                   f"hoặc tiền {h and h[0]['PayAmount']} ≠ {tong['khach_tra']}. "
                   "Nhiều khả năng mốc khóa đã cũ (phiếu vừa bị sửa ở máy khác)."}])
    return {"trn_id": trn_id, "bill_code": h[0]["BillCode"] or "", "tong": tong}


def chot(trn_id, *, till_id, user_id, c=None):
    """DUYỆT (v5 pha 5): Complete → (kiểm chưa có sổ quỹ) → T_TILL_TXN_Proc → KIỂM 6 ĐIỂM.
    Idempotent: đơn đã C thì không Complete lại; đã có dòng sổ quỹ thì không Proc lần 2."""
    c = c or S.client("chot_hoa_don")
    st = (c.query("SELECT Status FROM TRN_RT_BUYSELL WITH (NOLOCK) WHERE TrnID=?", (trn_id,)) or [{}])[0].get("Status")
    if st != CHOT_ROI:
        c.call("TRN_RT_BUYSELL_Complete", write=True, p_TrnID=trn_id, p_UserID=user_id, p_ThuHo="0")
    # ⚠ Complete tự tạo dòng T_TILL_TXN CHỜ (Status 'U', TillID NULL); Proc mới gán két + 'P'.
    # Chỉ coi là "đã vào sổ quỹ" khi Status='P' — thấy dòng U mà bỏ qua Proc là đơn C không két (đã dính 06/09).
    da_vao_ket = c.query("SELECT TOP 1 1 AS co FROM T_TILL_TXN WITH (NOLOCK) WHERE TrnRefID = ? AND Status = 'P'",
                         (trn_id,))
    if not da_vao_ket:
        c.call("T_TILL_TXN_Proc", write=True, p_TrnIDs=trn_id, p_TillID=till_id, p_UserID=user_id)
    # ── kiểm sau DUYỆT (v5 5.40) ──
    h = c.query("SELECT Status, PayAmount FROM TRN_RT_BUYSELL WITH (NOLOCK) WHERE TrnID=?", (trn_id,))
    if not h or h[0]["Status"] != CHOT_ROI:
        raise PmvProcError("TRN_RT_BUYSELL_Complete", -2, [{
            "loi": f"Chốt xong mà trạng thái vẫn {h and h[0]['Status']} — chưa vào sổ quỹ."}])
    chua_s = c.query(
        "SELECT s.ProductCode FROM TRN_RT_BUYSELL_SELL s WITH (NOLOCK) JOIN T_PRODUCT p WITH (NOLOCK) "
        "ON p.ProductID = s.ProductID WHERE s.TrnID = ? AND p.Status <> 'S'", (trn_id,))
    if chua_s:
        raise PmvProcError("TRN_RT_BUYSELL_Complete", -2, [{
            "loi": "Đã chốt nhưng hàng chưa sang trạng thái ĐÃ BÁN: " + ", ".join(r["ProductCode"] for r in chua_s)}])
    quy = c.query("SELECT TOP 1 1 AS co FROM T_TILL_TXN WITH (NOLOCK) WHERE TrnRefID = ? AND Status = 'P' "
                  "AND TillID IS NOT NULL", (trn_id,))
    if not quy:
        raise PmvProcError("T_TILL_TXN_Proc", -2, [{"loi": "Đã chốt nhưng sổ quỹ chưa vào KÉT (Status≠P) cho hóa đơn."}])
    return True


def mo_lai(trn_id, *, user_id, c=None):
    """HỦY THANH TOÁN: hóa đơn ĐÃ CHỐT (C) về nháp (W), hoàn toàn bộ pha DUYỆT (sổ quỹ, hàng S→I, tracking).
    Quy trình v5 pha 7 (đo thật 06:58:30 đơn 606): app dùng **@p_Type='0'**. Ghi chú cũ "'0' không ăn gì"
    chỉ đúng khi đơn còn W (không có gì để hoàn) — GĐ chốt 5 (05/09/2026)."""
    c = c or S.client("mo_lai_hoa_don")
    # '0' hoàn cả két (đòi T_TILL_TXN có TillID) — đơn C mà chưa vào két (Status U, TillID NULL,
    # vd chốt dở giữa chừng) thì '0' nổ NULL TillID → dùng '1' (chỉ trả trạng thái, không có két để hoàn)
    da_vao_ket = c.query("SELECT TOP 1 1 AS co FROM T_TILL_TXN WITH (NOLOCK) WHERE TrnRefID = ? AND Status = 'P' "
                         "AND TillID IS NOT NULL", (trn_id,))
    c.goi_co_khoa(
        "T_TILL_TXN_Del", bang="TRN_RT_BUYSELL", cot_id="TrnID", gia_tri=trn_id,
        kiem_tra=lambda cl: (cl.query("SELECT Status FROM TRN_RT_BUYSELL WITH (NOLOCK) WHERE TrnID=?",
                                      (trn_id,)) or [{}])[0].get("Status") == NHAP,
        p_TrnRefID=trn_id, pType="SRT", pCongNoBanLe=0, p_UserUpd=user_id,
        p_TrnDateTime_Upd_GDN=PmvClient.MOC_TRONG, p_Type="0" if da_vao_ket else "1")
    return True


def huy(trn_id, *, user_id, c=None):
    """XÓA ĐƠN (chỉ còn trong *_Log), hàng + dẻ về kho. Chỉ nhận đơn W.
    GĐ chốt 2 (05/09/2026): đơn ĐÃ THANH TOÁN phải qua 2 BƯỚC, 2 XÁC NHẬN rõ ràng —
    bước 1 HỦY THANH TOÁN (mo_lai) → bước 2 XÓA — KHÔNG tự chuỗi 2 proc như app."""
    c = c or S.client("huy_hoa_don")
    st = c.query("SELECT Status FROM TRN_RT_BUYSELL WITH (NOLOCK) WHERE TrnID=? AND IsDel='0'", (trn_id,))
    if not st:
        return False
    if st[0]["Status"] == CHOT_ROI:
        raise PmvProcError("TRN_RT_BUYSELL_Del", -1, [{
            "loi": "Hóa đơn ĐÃ THANH TOÁN — bước 1 bấm HỦY THANH TOÁN (đơn về chờ), bước 2 mới XÓA được."}])
    c.goi_co_khoa(
        "TRN_RT_BUYSELL_Del", bang="TRN_RT_BUYSELL", cot_id="TrnID", gia_tri=trn_id,
        kiem_tra=lambda cl: not cl.query(
            "SELECT TrnID FROM TRN_RT_BUYSELL WITH (NOLOCK) WHERE TrnID=? AND IsDel='0'", (trn_id,)),
        p_TrnID=trn_id, p_UserUpd=user_id,
        p_TrnDateTime_Upd_GDN=PmvClient.MOC_TRONG, p_LogDel="0")
    return True


def huy_thau(trn_id, *, user_id, c=None):
    """Hủy phiếu thâu đang ở nháp; phiếu đã chốt phải hủy thanh toán trước.

    Hai bước giống hóa đơn bán: trả sổ quỹ bằng BRT rồi mới gọi proc xóa phiếu.
    Mỗi bước dùng mốc khóa mới và đọc lại để tránh báo thành công giả.
    """
    c = c or S.client("huy_thau")
    st = c.query("SELECT Status FROM TRN_RT_BUYGOLD WITH (NOLOCK) WHERE TrnID=? AND IsDel='0'", (trn_id,))
    if not st:
        return False
    if st[0]["Status"] == CHOT_ROI:
        mo_lai_thau(trn_id, user_id=user_id, c=c)
    c.goi_co_khoa(
        "TRN_RT_BUYGOLD_Del", bang="TRN_RT_BUYGOLD", cot_id="TrnID", gia_tri=trn_id,
        kiem_tra=lambda cl: not cl.query("SELECT TrnID FROM TRN_RT_BUYGOLD WITH (NOLOCK) WHERE TrnID=? AND IsDel='0'", (trn_id,)),
        p_TrnID=trn_id, p_UserUpd=user_id, p_TrnDateTime_Upd_GDN=PmvClient.MOC_TRONG,
        p_TrnID_GDN="")
    return True


def mo_lai_thau(trn_id, *, user_id, c=None):
    """Hủy phần thanh toán của phiếu thâu, đưa C về W."""
    c = c or S.client("mo_lai_thau")
    c.goi_co_khoa(
        "T_TILL_TXN_Del", bang="TRN_RT_BUYGOLD", cot_id="TrnID", gia_tri=trn_id,
        kiem_tra=lambda cl: (cl.query("SELECT Status FROM TRN_RT_BUYGOLD WITH (NOLOCK) WHERE TrnID=?", (trn_id,)) or [{}])[0].get("Status") == NHAP,
        p_TrnRefID=trn_id, pType="BRT", pCongNoBanLe=0, p_UserUpd=user_id,
        p_TrnDateTime_Upd_GDN=PmvClient.MOC_TRONG, p_Type="1")
    return True
