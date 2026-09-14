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
        # mốc khóa lạc quan lúc ĐỌC — views so lại trước Sửa/Hủy đơn chốt (chống 2 người cùng sửa, 07/09).
        # Đọc bằng CÙNG hàm moc_khoa (SELECT → datetime) để so chuỗi với kiem_moc khớp kiểu (proc _Get trả khác dạng).
        "upd": str(c.moc_khoa("TRN_RT_BUYSELL", "TrnID", h["TrnID"]) or ""),
        "ban": ban, "doi": doi,
        "coc_ids": [r['DatCocID'] for r in c.query('SELECT DatCocID FROM TRN_RT_BUYSELL_DatCoc WITH (NOLOCK) WHERE TrnID=?',(trn_id,))],
        "tong": tinh_tong(sum((x[0] for x in ban), Decimal(0)),
                          sum((x[0] for x in doi), Decimal(0)),
                          h.get("Discount"), h.get("TaskPriceAdd"),
                          h.get("AddMoney"), h.get("TienCoc")),
    }


DS_TRAN = 2000   # trần dòng popup DANH SÁCH — chạm trần thì popup cảnh báo thống kê chưa đủ


def danh_sach(d1, d2, *, emp_id="", khach="", trang_thai="", c=None, limit=DS_TRAN):
    """Danh sách hóa đơn BÁN theo KHOẢNG NGÀY [d1, d2] + lọc NV / khách / trạng thái — popup
    DANH SÁCH (thiết kế lại 07/09/2026). Trả (rows, thong_ke).
    Nguồn (GĐ chốt 07/09/2026): khoảng CÓ CHỨA hôm nay → KK live (KK chết → lùi về kho); khoảng
    TRỌN quá khứ → kho lịch sử — quy tắc chung `hist_read.la_qua_khu(d1, d2)`.
    Khoảng ngày chỉ dùng tham số, không ghép chuỗi vào SQL."""
    emp_id, key = (emp_id or "").strip(), (khach or "").strip()
    tt = (trang_thai or "").strip().upper()
    # CardPay (08/09/2026 chiều — GĐ chốt): cột KHÁCH TRẢ popup DS hiện 2 dòng PayAmount / ⚡ CardPay (tiền thẻ/CK)
    sql = (f"SELECT TOP {int(limit)} b.TrnID, b.BillCode, b.TrnDate, b.TrnTime, b.Status, b.PayAmount, "
           "ISNULL(b.CardPay,0) AS CardPay, "
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
        rows = HR.doc(lambda cl: cl.query(sql, args), ngay_iso=d1, den_iso=d2, tag="ds_hoa_don")
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


def ma_gdb_hop_le(ma):
    """Mã 9 số in trên Giấy đảm bảo (`views._ma_gdb`: BillCode 26-09-08-000038 → 260908038 = yymmdd + 3 số
    cuối). Trả (ngay_iso, stt3) khi đúng dạng và 6 số đầu là NGÀY CÓ THẬT, ngược lại None (mã hàng thường
    có chữ nên không lẫn; mã hàng 9 số thuần mà ngày vô lý cũng không lẫn)."""
    import datetime as _dt
    s = (ma or "").strip()
    if len(s) != 9 or not s.isdigit():
        return None
    try:
        d = _dt.date(2000 + int(s[:2]), int(s[2:4]), int(s[4:6]))
    except ValueError:
        return None
    return d.isoformat(), s[6:]


def tim_theo_ma_gdb(ma, c=None):
    """QUÉT MÃ GĐB ở ô quét màn bán (GĐ chốt 08/09/2026 chiều): mã 9 số → hóa đơn. BillCode vendor =
    yy-mm-dd-NNNNNN nên tra `LIKE 'yy-mm-dd-%stt'` theo ngày nằm trong mã; ngày quá khứ → kho lịch sử, hôm nay
    → live (quy tắc chung hist_read.la_qua_khu). Trả {TrnID, BillCode, Status} hoặc None; >1 khớp (hơn 999
    đơn/ngày — chưa từng) → dòng mới nhất."""
    kq = ma_gdb_hop_le(ma)
    if not kq:
        return None
    ngay, stt = kq
    y, m, d = ngay.split("-")
    mau = f"{y[2:]}-{m}-{d}-%{stt}"
    sql = ("SELECT TOP 2 TrnID, BillCode, Status FROM TRN_RT_BUYSELL WITH (NOLOCK) "
           "WHERE IsDel = '0' AND BillCode LIKE ? ORDER BY TrnDate DESC, TrnTime DESC")
    if c is not None:
        rows = c.query(sql, (mau,))
    else:
        from apps.pmv import hist_read as HR
        rows = HR.doc(lambda cl: cl.query(sql, (mau,)), ngay_iso=ngay, tag="quet_gdb")
    return rows[0] if rows else None


# ─────────────────────────── ghi ───────────────────────────
def _tham_so(c, proc, **rieng):
    co = {n for n, _ in c.params_of(proc)}
    d = {k: v for k, v in _MAC_DINH.items() if k in co}
    d.update({k: v for k, v in rieng.items() if k in co or not k.startswith("p_")})
    return d


def luu(*, trn_id, ban, doi, ngay, gio, cust_id, emp_id, till_id, shop_id, user_id,
        ghi_chu="", bot=0, cong_them=0, vang_them=0, coc=0, c=None, on_created=None):
    """trn_id rỗng = TẠO MỚI, có trn_id = SỬA. Trả dict(trn_id, bill_code, tong).

    Sau khi ghi LUÔN đọc lại đối chiếu — proc có thể trả rc=0 mà không đổi gì (luật 2)."""
    c = c or S.client("luu_hoa_don")
    if trn_id:
        from .deposit_money import invoice_deposit_guard
        invoice_deposit_guard(c,trn_id,coc,cust_id)
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
    from .deposit_operations import check_holds
    check_holds(ma_hang, c.target)

    if not trn_id:
        rc, sets = c.call("TRN_RT_BUYSELL_Ins", write=True, day_du=True, raise_on_rc=False,
                          **_tham_so(c, "TRN_RT_BUYSELL_Ins", p_TrnID="", **chung))
        moi = next((r["TrnID"] for s in sets for r in s if r.get("TrnID")), None)
        if rc != 0 or not moi:
            raise PmvProcError("TRN_RT_BUYSELL_Ins", rc, sets)
        trn_id = moi
        if on_created: on_created(trn_id)
        # Proc Ins của vendor không nhận p_TienCoc (luôn ghi 0), dù PayAmount đã trừ cọc.
        # Upd có tham số này: bổ sung ngay rồi kiểm chứng cả cột cọc và tiền khách trả.
        if M.dec(coc):
            moc=c.moc_khoa('TRN_RT_BUYSELL','TrnID',trn_id)
            c.call('TRN_RT_BUYSELL_Upd',write=True,day_du=True,
                **_tham_so(c,'TRN_RT_BUYSELL_Upd',p_TrnID=trn_id,p_UserUpd=user_id,
                           p_TrnDateTime_Upd=PmvClient.fmt_moc(moc),**chung))
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
    h = c.query("SELECT BillCode, Status, PayAmount, TienCoc FROM TRN_RT_BUYSELL WITH (NOLOCK) WHERE TrnID=?",
                (trn_id,))
    thuc = sorted(x["ProductCode"] for x in c.query(
        "SELECT ProductCode FROM TRN_RT_BUYSELL_SELL WITH (NOLOCK) WHERE TrnID=?", (trn_id,)))
    if not h or thuc != ma_hang or M.dec(h[0]["PayAmount"]) != tong["khach_tra"] or M.dec(h[0].get('TienCoc')) != M.dec(c.money(coc)):
        raise PmvProcError("TRN_RT_BUYSELL", -2, [{
            "loi": f"Lưu xong nhưng đọc lại KHÔNG khớp — hàng {thuc} ≠ {ma_hang} "
                   f"hoặc tiền {h and h[0]['PayAmount']} ≠ {tong['khach_tra']}. "
                   "Nhiều khả năng mốc khóa đã cũ (phiếu vừa bị sửa ở máy khác)."}])
    return {"trn_id": trn_id, "bill_code": h[0]["BillCode"] or "", "tong": tong}


def chot(trn_id, *, till_id, user_id, c=None):
    """DUYỆT (v5 pha 5): Complete → (kiểm chưa có sổ quỹ) → T_TILL_TXN_Proc → KIỂM 6 ĐIỂM.
    Idempotent: đơn đã C thì không Complete lại; đã có dòng sổ quỹ thì không Proc lần 2."""
    c = c or S.client("chot_hoa_don")
    from .deposit_models import DepositMoneyOperation
    if DepositMoneyOperation.objects.filter(target=c.target,invoice_id=trn_id,kind='unlink',
            status__in=['running','uncertain']).exists():
        raise ValueError('Hóa đơn đang phục hồi liên kết cọc; cần đối soát trước khi chốt.')
    from .deposit_money import invoice_deposit_guard
    deposit_header=c.query('SELECT CustID,TienCoc FROM TRN_RT_BUYSELL WITH (NOLOCK) WHERE TrnID=?',(trn_id,))
    if deposit_header:
        invoice_deposit_guard(c,trn_id,deposit_header[0]['TienCoc'],deposit_header[0]['CustID'])
    from . import deposit_application as A, ban_coc as BC
    linked = A.linked_ids(c,trn_id)
    if linked:
        err = BC.kiem_san_pham(c,linked,[{'row':r} for r in c.query(
            'SELECT ProductCode FROM TRN_RT_BUYSELL_SELL WITH (NOLOCK) WHERE TrnID=?',(trn_id,))])
        if err: raise ValueError(err)
    from .deposit_operations import check_holds
    check_holds([r['ProductCode'] for r in c.query('SELECT ProductCode FROM TRN_RT_BUYSELL_SELL WITH (NOLOCK) WHERE TrnID=?',(trn_id,))],c.target)
    st = (c.query("SELECT Status FROM TRN_RT_BUYSELL WITH (NOLOCK) WHERE TrnID=?", (trn_id,)) or [{}])[0].get("Status")
    if st != CHOT_ROI:
        c.call("TRN_RT_BUYSELL_Complete", write=True, p_TrnID=trn_id, p_UserID=user_id, p_ThuHo="0")
    if linked:
        current=A.invoice(c,trn_id)
        A.check(c,A.linked_ids(c,trn_id),current['CustID'],trn_id,current['TienCoc'],'C')
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
    if linked: A.finish(c,trn_id)
    # XẾP HÀNG ZNS (14/09/2026) — chỉ TẠO dòng chờ gửi, KHÔNG gửi tin. Đặt ở ĐÂY vì chỉ sau trọn
    # 6 điểm kiểm hậu-DUYỆT mới chắc chắn "hóa đơn lập THÀNH CÔNG". import TRONG hàm để app oa lỗi
    # nạp cũng không chặn bán; hàm tự nuốt mọi lỗi bên trong, except ở đây là lưới thứ hai.
    try:
        from apps.oa import xep_hang as XH
        XH.xep_hang_hoa_don(trn_id, c=c)
    except Exception:
        pass
    return True


def kiem_moc(trn_id, upd_da_doc, c=None):
    """Chống 2 người cùng sửa 1 đơn (GĐ chốt 07/09/2026): so mốc TrnDateTime_Upd lúc MỞ đơn với mốc
    HIỆN TẠI trên KK. Lệch → raise kèm mốc mới; upd_da_doc trống (đơn nạp trước bản này) → bỏ qua."""
    if not upd_da_doc:
        return
    c = c or S.client("kiem_moc")
    hien = c.moc_khoa("TRN_RT_BUYSELL", "TrnID", trn_id)
    if hien is None:
        raise PmvProcError("kiem_moc", -1, [{"loi": f"Hóa đơn {trn_id} không còn trên máy KK"}])
    if str(hien) != str(upd_da_doc):
        raise PmvProcError("kiem_moc", -1, [{
            "loi": f"Hóa đơn vừa bị người khác sửa ({hien:%H:%M:%S %d/%m}) — mở lại từ DANH SÁCH rồi thao tác tiếp."}])


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
    from .deposit_application import linked_ids
    from .deposit_completion import after_detach
    after_detach(c,linked_ids(c,trn_id),str(user_id))
    # Đơn về nháp ⇒ dòng ZNS đang chờ phải HỦY, nếu không sẽ thành tin mồ côi và được gửi khi
    # Giám đốc duyệt bước 2. Hủy = CẬP NHẬT status='cancelled', KHÔNG xóa dòng.
    try:
        from apps.oa import xep_hang as XH
        XH.huy_xep_hang(trn_id, "source_not_completed", c=c)
    except Exception:
        pass
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
    from .deposit_application import linked_ids
    deposits_to_release=linked_ids(c,trn_id)
    c.goi_co_khoa(
        "TRN_RT_BUYSELL_Del", bang="TRN_RT_BUYSELL", cot_id="TrnID", gia_tri=trn_id,
        kiem_tra=lambda cl: not cl.query(
            "SELECT TrnID FROM TRN_RT_BUYSELL WITH (NOLOCK) WHERE TrnID=? AND IsDel='0'", (trn_id,)),
        p_TrnID=trn_id, p_UserUpd=user_id,
        p_TrnDateTime_Upd_GDN=PmvClient.MOC_TRONG, p_LogDel="0")
    from .deposit_completion import after_detach
    after_detach(c,deposits_to_release,str(user_id))
    # Đơn bị XÓA ⇒ đổi lý do hủy sang 'source_deleted'. Móc này chỉ chạy sau khi đã qua mo_lai()
    # (huy() từ chối đơn C) nên dòng thường ĐANG cancelled — huy_xep_hang vẫn cập nhật lý do.
    try:
        from apps.oa import xep_hang as XH
        XH.huy_xep_hang(trn_id, "source_deleted", c=c)
    except Exception:
        pass
    return True


# ─────────────────────────── PHIẾU THÂU ĐỘC LẬP — THAU_VANG (GĐ chốt 08/09/2026) ───────────────────────────
# Đúng "giọng" app KK đo 08/09 (TBG…373 tiền mặt · 372/391/395 CK): Ins (W, BillCode sinh ngay) → [Upd khi SỬA, có mốc]
# → CompleteMore 'TBG…@' (C + T_TILL_TXN 'U' + điểm + LastTradingDate) → [CARDPAY_Ins BRT với DS thẻ RỖNG khi có CK: proc
# chỉ UPDATE CashPay/CardPay + TRN_TILL_TXN_Upd sửa dòng VND két — GĐ chốt KHÔNG cần dòng TRN_RT_BUYGOLD_CardPay]
# → T_TILL_TXN_Proc (P + T_TILL_BAL). Hủy: mo_lai_thau / huy_thau bên dưới. Mọi bước ĐỌC LẠI kiểm chứng (bẫy rc=0 im lặng).
THAU_COT = ("t.TrnID, t.BillCode, t.TrnDate, t.TrnTime, t.CustID, t.EmpID, t.TillID, t.GoldCode, t.GoldWeight, "
            "t.DiamondWeight, t.BuyRate, t.PercentValue, ISNULL(t.AddMoney,0) AS AddMoney, t.TotalAmount, t.Notes, t.Status, "
            "t.IsDel, ISNULL(t.CashPay,0) AS CashPay, ISNULL(t.CardPay,0) AS CardPay, t.WeightUnit, t.TrnDateTime_Upd, "
            "ISNULL(k.CustName,'') AS CustName, ISNULL(k.Phone,'') AS Phone, ISNULL(k.Address,'') AS Address, "
            "ISNULL(k.CMND,'') AS CMND, ISNULL(e.EmpName,'') AS EmpName, ISNULL(g.GoldDesc, t.GoldCode) AS GoldDesc")


def phieu_thau(trn_id, c=None):
    """1 dòng phiếu thâu kèm tên khách / NV / loại vàng (None nếu không có)."""
    c = c or S.client("phieu_thau")
    r = c.query(f"SELECT {THAU_COT} FROM TRN_RT_BUYGOLD t WITH (NOLOCK) "
                "LEFT JOIN I_CUSTOMER k WITH (NOLOCK) ON k.CustID = t.CustID "
                "LEFT JOIN T_EMPLOYEE e WITH (NOLOCK) ON e.EmpID = t.EmpID "
                "LEFT JOIN I_GOLD g WITH (NOLOCK) ON g.GoldCode = t.GoldCode WHERE t.TrnID = ?", (trn_id,))
    return r[0] if r else None


def _tham_so_thau(*, cust_id, emp_id, till_id, shop_id, user_id, gold_code, gw, dw, rate, pct, add_money, tien, notes):
    """Bộ tham số Ins/Upd y app (đo 373): TL ly đã trừ hột, giá nghìn/chỉ, tuổi %, GoldAge/AgeChange='1', CustPay=TotalAmount."""
    n = PmvClient.num
    return {
        "p_TrnDate": PmvClient.fmt_date(), "p_TrnTime": PmvClient.fmt_time(), "p_EmpID": emp_id,
        "p_CustID": cust_id or S.WALK_IN, "p_GoldCode": gold_code, "p_GoldWeight": n(gw, 3), "p_DiamondWeight": n(dw, 3),
        "p_BuyRate": n(rate, 3), "p_PercentValue": n(pct, 2), "p_TotalAmount": PmvClient.money(tien), "p_Notes": notes or "",
        "p_Status": None, "p_IsDel": "0", "p_CreatedBy": user_id, "p_CreatedDate": PmvClient.fmt_date(), "p_Dirty": "0",
        "p_ShopID": shop_id, "p_AddMoney": PmvClient.money(add_money), "p_GoldAge": "1", "p_TruLai": "0", "p_TruGia": "0",
        "p_AgeChange": "1", "p_Debt_Bal": "0", "p_CustPay": PmvClient.money(tien), "p_Debt_Bal_New": "0",
        "p_GoldWeightChange": n(gw, 3), "p_TillID": till_id, "p_TruDoTrenChi": "0", "p_PriceCcy": "VND", "p_CcyRate": "1000",
        "p_TrnDateTime_Upd_GDN": None, "p_TrnID_GDN": "", "p_SoHDTuNhap": "",
    }


def luu_thau(*, trn_id="", cust_id, emp_id, till_id, shop_id, user_id, gold_code, gw, dw=0, rate, pct=100,
             add_money=0, notes="", unit="L", c=None):
    """LƯU NHÁP phiếu thâu (Status W). trn_id trống → TRN_RT_BUYGOLD_Ins (vendor sinh TrnID + BillCode ngay);
    có trn_id → TRN_RT_BUYGOLD_Upd với mốc khóa lạc quan (phiếu C → vendor từ chối B-002). Trả dòng phiếu đọc lại."""
    c = c or S.client("luu_thau")
    if not (till_id and user_id and shop_id and emp_id):
        raise ValueError("Tài khoản chưa gắn két / tiệm PMV hoặc chưa chọn nhân viên — không lập phiếu được")
    tien = M.buy_amount_standalone(gw, rate, pct, add_money, unit, c)
    if tien <= 0:
        raise ValueError("Tiền thâu phải lớn hơn 0")
    p = _tham_so_thau(cust_id=cust_id, emp_id=emp_id, till_id=till_id, shop_id=shop_id, user_id=user_id,
                      gold_code=gold_code, gw=gw, dw=dw, rate=rate, pct=pct, add_money=add_money, tien=tien, notes=notes)
    if trn_id:
        cu = phieu_thau(trn_id, c)
        if not cu or str(cu.get("IsDel")) != "0":
            raise ValueError("Phiếu thâu không còn tồn tại")
        if cu.get("Status") != NHAP:
            raise ValueError("Phiếu đã THANH TOÁN — hủy thanh toán trước khi sửa")
        p.update(p_TrnID=trn_id, p_UserUpd=user_id, p_IsGiaoDichNhanh="0", p_CreatedDate=None)
        p.pop("p_TrnID_GDN", None)          # Upd không có tham số này (chỉ Ins/Del có)
        full = c.tham_so_day_du("TRN_RT_BUYGOLD_Upd", p)
        full.pop("p_TrnDateTime_Upd", None)
        moc_cu = cu.get("TrnDateTime_Upd")
        c.goi_co_khoa("TRN_RT_BUYGOLD_Upd", bang="TRN_RT_BUYGOLD", cot_id="TrnID", gia_tri=trn_id,
                      kiem_tra=lambda cl: cl.moc_khoa("TRN_RT_BUYGOLD", "TrnID", trn_id) != moc_cu, **full)
    else:
        _, sets = c.call("TRN_RT_BUYGOLD_Ins", write=True, day_du=True, p_TrnID="", **p)
        kq = (sets[0][0] if sets and sets[0] else {}) or {}
        if str(kq.get("ErrCode", "0")) != "0" or not kq.get("TrnID"):
            raise PmvProcError("TRN_RT_BUYGOLD_Ins", kq.get("ErrCode", -1), sets)
        trn_id = str(kq["TrnID"]).strip()
    row = phieu_thau(trn_id, c)
    if not row or M.dec(row["TotalAmount"]) != tien:
        raise PmvProcError("TRN_RT_BUYGOLD", -2, [{"loi": f"Đọc lại phiếu {trn_id} không khớp tiền {tien}"}])
    return row


def chot_thau_nhom(trn_ids, *, till_id, user_id, ck_theo_phieu=None, c=None):
    """THANH TOÁN CẢ NHÓM phiếu thâu W → C đúng chuỗi app (app cũng gọi 2 mã một lúc, đo 07:18:42):
    CompleteMore 'A@B@' → [CARDPAY_Ins từng dòng có CK] → T_TILL_TXN_Proc 'A@B@'. Kiểm chứng đọc lại sau MỖI bước.
    ck_theo_phieu = {TrnID: tiền CK} (web chia CK tuần tự — thau_cart.phan_bo). Trả list dòng phiếu sau chốt."""
    c = c or S.client("chot_thau")
    ck_theo_phieu = dict(ck_theo_phieu or {})
    if not till_id:
        raise ValueError("Tài khoản chưa gắn két (TillID) — tiền/vàng không vào két nào được")
    if not trn_ids:
        raise ValueError("Không có phiếu nào để chốt")
    rows = {}
    for t in trn_ids:
        row = phieu_thau(t, c)
        if not row or str(row.get("IsDel")) != "0":
            raise ValueError(f"Phiếu thâu {t} không còn tồn tại")
        if row.get("Status") != NHAP:
            raise ValueError(f"Phiếu {row.get('BillCode') or t} đã thanh toán rồi — muốn sửa hãy hủy thanh toán trước")
        ck = M.dec(ck_theo_phieu.get(t, 0))
        if ck < 0 or ck > M.dec(row["TotalAmount"]):
            raise ValueError("Tiền chuyển khoản phải trong khoảng 0 → tổng tiền của dòng")
        rows[t] = row
    chuoi = "".join(f"{t}@" for t in trn_ids)
    _, sets = c.call("TRN_RT_BUYGOLD_CompleteMore", write=True, p_TrnIDs=chuoi)
    kq = (sets[0][0] if sets and sets[0] else {}) or {}
    if str(kq.get("ErrorCode", "0")) != "0":
        raise PmvProcError("TRN_RT_BUYGOLD_CompleteMore", kq.get("ErrorCode"), sets)
    for t in trn_ids:
        txn = c.query("SELECT TillTxnID, Status FROM T_TILL_TXN WITH (NOLOCK) WHERE TrnRefID = ?", (t,))
        row = phieu_thau(t, c)
        if row.get("Status") != CHOT_ROI or not txn:
            raise PmvProcError("TRN_RT_BUYGOLD_CompleteMore", -2, [{"loi": f"Proc trả OK nhưng {t} chưa C / chưa có dòng két"}])
        ck = M.dec(ck_theo_phieu.get(t, 0))
        if ck > 0:
            mat = M.dec(row["TotalAmount"]) - ck
            # GĐ chốt 2 (08/09/2026): DS thẻ RỖNG → proc không INSERT TRN_RT_BUYGOLD_CardPay, chỉ UPDATE CashPay/CardPay
            # + TRN_TILL_TXN_Upd đặt dòng VND két = tiền mặt. Dấu ÂM = tiền đi ra (đo 372: CardPay=−67,9tr).
            # ⚠ p_ProductIDs phải NULL: chuỗi rỗng '' làm proc rẽ vào nhánh SRT (bán) sai.
            c.call("CARDPAY_Ins", write=True, day_du=True, p_TillID=till_id, p_TillTxnID=txn[0]["TillTxnID"], p_TrnID=t,
                   p_TypeTrade="BRT", p_ProductIDs=None, p_CardAmounts=None, p_Amount=-ck, p_AmountTra=-mat,
                   p_List="<NewDataSet/>")
            row = phieu_thau(t, c)
            vnd = c.query("SELECT TrnTotalAmount FROM T_TILL_TXN WITH (NOLOCK) WHERE TrnRefID = ?", (t,))
            if M.dec(row["CardPay"]) != -ck or not vnd or M.dec(vnd[0]["TrnTotalAmount"]) != mat:
                raise PmvProcError("CARDPAY_Ins", -2, [{"loi": f"Ghi CK {t} xong nhưng CardPay / tiền mặt két đọc lại không khớp"}])
    _, sets = c.call("T_TILL_TXN_Proc", write=True, day_du=True, p_TrnIDs=chuoi, p_TillID=till_id, p_UserID=user_id)
    kq = (sets[0][0] if sets and sets[0] else {}) or {}
    if str(kq.get("Result", "0")) != "0":
        raise PmvProcError("T_TILL_TXN_Proc", kq.get("Result"), sets)
    out = []
    for t in trn_ids:
        txn = c.query("SELECT TillID, Status FROM T_TILL_TXN WITH (NOLOCK) WHERE TrnRefID = ?", (t,))
        if not txn or txn[0]["Status"] != "P" or txn[0]["TillID"] != till_id:
            raise PmvProcError("T_TILL_TXN_Proc", -2, [{"loi": f"Két chưa nhận {t} (T_TILL_TXN chưa P / sai két)"}])
        out.append(phieu_thau(t, c))
    return out


def chot_thau(trn_id, *, till_id, user_id, tien_ck=0, c=None):
    """THANH TOÁN 1 phiếu thâu (gói của chot_thau_nhom). Trả dòng phiếu sau chốt."""
    ck = M.dec(tien_ck)
    return chot_thau_nhom([trn_id], till_id=till_id, user_id=user_id, ck_theo_phieu={trn_id: ck} if ck else None, c=c)[0]


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
