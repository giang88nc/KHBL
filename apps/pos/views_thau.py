"""
THÂU VÀO — trang cùng khung màn BÁN HÀNG (GĐ chốt 08/09/2026 tối): THÔNG TIN · VÀNG THÂU (form như Vàng đổi,
NHIỀU DÒNG cùng 1 khách, công tắc Giá THÂU VÀO (mặc định) / BÁN RA) · TÍNH TỔNG đầy đủ · chân 4 nút · popup DANH SÁCH.

Ghi KK theo quy trình THAU_VANG: mỗi dòng = 1 TRN_RT_BUYGOLD (Ins/Upd) → cả nhóm chốt CHUNG 1 lệnh
TRN_RT_BUYGOLD_CompleteMore 'A@B@' → [CARDPAY_Ins từng dòng có CK] → T_TILL_TXN_Proc 'A@B@'. Nhóm dòng lưu ở MySQL
(models.ThauNhom) để mở lại / in 1 tờ 110mm. Bù/bớt gắn vào dòng lớn nhất, CK rót tuần tự (thau_cart.phan_bo).
"""
import datetime
import logging

from django.core.cache import cache
from django.http import HttpResponse
from django.shortcuts import render
from django.template.loader import render_to_string
from django.views.decorators.http import require_GET, require_POST

from apps.pmv import gateway, money as M
from apps.pmv.client import PmvProcError

from . import bill as B, services as S, thau_cart as TC
from .models import ThauNhom
from .views import (PC_SAI_TOI_DA, _co_fullcontrol_hoa_don, _loi_goi, _passcode_dung, _passcode_khoa, _phien,
                    _so, _so_tl, _tien_bang_chu)

logger = logging.getLogger(__name__)
KHOA_MSG = "Phiếu thâu đang KHÓA 🔒 (đã thanh toán) — bấm 🔓 SỬA (passcode) để mở"

HANH_DONG = {
    "sua":    {"ten": "SỬA PHIẾU", "icon": "🔓", "hau_qua": [
        "Sổ quỹ (két) của phiếu bị HOÀN: vàng ra két, tiền về két; phiếu về trạng thái CHỜ.",
        "Phiếu ở lại màn hình để sửa dòng vàng, khách, nhân viên, tiền — xong phải THANH TOÁN lại.",
        "Phiếu thâu đã in KHÔNG CÒN HIỆU LỰC — in lại sau khi thanh toán."]},
    "huy_tt": {"ten": "HỦY THANH TOÁN", "icon": "↩", "hau_qua": [
        "Sổ quỹ (két) của phiếu bị HOÀN; phiếu về trạng thái CHỜ, nằm ở DANH SÁCH.",
        "Màn hình về phiếu trắng. Phiếu thâu đã in KHÔNG CÒN HIỆU LỰC."]},
    "huy_hd": {"ten": "XÓA PHIẾU", "icon": "🗑", "nguy": True, "hau_qua": [
        "Sổ quỹ (két) bị HOÀN, rồi TOÀN BỘ dòng của phiếu bị XÓA HẲN khỏi phần mềm (phiếu chờ chưa thanh toán thì phần mềm KHÔNG giữ log — chỉ còn nhật ký web).",
        "KHÔNG khôi phục được. Phiếu thâu đã in KHÔNG CÒN HIỆU LỰC."]},
    "xoa_nhap": {"ten": "XÓA PHIẾU NHÁP", "icon": "🗑", "nhap": True, "hau_qua": [
        "Các dòng đang nhập bị bỏ; dòng đã LƯU CHỜ trên máy KK (nếu có) bị xóa hẳn.",
        "Màn hình về phiếu trắng."]},
}


# ─────────────────────────── ngữ cảnh & OOB ───────────────────────────
def _thau_treo():
    """Số phiếu thâu CHỜ hôm nay (W) — badge ⚠ cạnh DANH SÁCH; cache 60s."""
    def _dem():
        hom_nay = datetime.date.today().isoformat()
        try:
            rows, _ = S.hoa_don_loc(hom_nay, hom_nay, loai="THAU", trang_thai="W")
            return [{"BillCode": r.get("BillCode") or r["TrnID"], "TrnID": r["TrnID"]} for r in rows]
        except Exception:
            return []
    return cache.get_or_set("khbl:thau_treo", _dem, 60)


def _ctx(request, extra=None):
    g = TC.get(request)
    ph = _phien(request)
    hom_nay = datetime.date.today().isoformat()
    nvs = S.nhan_vien_ban()
    ctx = {"nav_active": "thau", "g": g, "t": TC.tong_cua(g), "phien": ph, "nvs": nvs, "loai_de": S.loai_de(),
           "hom_nay": hom_nay, "ngay": g.get("ngay") or hom_nay, "phieu_chot": g.get("status") == B.CHOT_ROI,
           "dang_sua": bool(g.get("sua_lai")), "co_don": bool(g["lines"] or g.get("trn_ids")),
           "ten_nv": next((e["EmpName"] for e in nvs if e["EmpID"] == g.get("emp")), ""),
           "kieu_ui": g.get("kieu_ui") or "thau", "treo": _thau_treo(), "pmv_dich_mo_ta": gateway.mo_ta_dich(gateway.dich_hien_tai())}
    ctx["don_hom_nay"] = (g.get("ngay") or hom_nay) == hom_nay
    ctx["khoa_ngay_cu"] = ctx["phieu_chot"] and not ctx["don_hom_nay"]
    ctx.update(extra or {})
    return ctx


def _oob(request, extra=None):
    return render(request, "pos/_thau_oob.html", _ctx(request, extra))


def _loi(request, msg, extra=None):
    e = {"loi_phieu": msg}
    e.update(extra or {})
    return _oob(request, e)


def _dang_khoa(request):
    return TC.get(request).get("status") == B.CHOT_ROI


# ─────────────────────────── trang + thao tác giỏ ───────────────────────────
def thau(request):
    return render(request, "pos/thau.html", _ctx(request))


@require_POST
def thau_moi(request):
    TC.clear(request, giu_nv=True)
    return _oob(request, {"tin": "Phiếu thâu mới"})


@require_POST
def thau_dat(request):
    """Đặt khách · nhân viên · bù/bớt · ghi chú · phương thức thanh toán · công tắc giá."""
    if _dang_khoa(request):
        return _loi(request, KHOA_MSG)
    g = TC.get(request)
    P = request.POST
    if "cust_id" in P:
        cid = (P.get("cust_id") or "").strip()
        k = S.khach_theo_id(cid) if cid and cid != S.WALK_IN else None
        g["cust"] = {"id": k["CustID"], "code": k.get("CustCode") or "", "name": k["CustName"],
                     "phone": k.get("Phone") or ""} if k else None
    if "emp" in P:
        g["emp"] = (P.get("emp") or "").strip()
    if "ghi_chu" in P:
        g["ghi_chu"] = (P.get("ghi_chu") or "").strip()[:300]
    for o in ("bu", "bot"):
        if o in P:
            g[o] = str(M.tron_ngan(_so(P.get(o))))
    if "pay_method" in P:
        pm = (P.get("pay_method") or "").strip()
        g["pay_method"] = pm if pm in ("cash", "bank", "card") else "cash"
    if "tien_mat" in P:
        v = (P.get("tien_mat") or "").strip()
        g["tien_mat"] = str(M.tron_ngan(_so(v))) if v else ""
    if "kieu_ui" in P:
        g["kieu_ui"] = "ban" if P.get("kieu_ui") == "ban" else "thau"
    TC.save(request, g)
    return _oob(request)


@require_POST
def thau_them(request):
    """Thêm 1 dòng VÀNG THÂU: loại dẻ · tổng TL · TL hột · giá (đồng, sửa tay được) · kiểu giá thâu vào / bán ra."""
    if _dang_khoa(request):
        return _loi(request, KHOA_MSG)
    ma = (request.POST.get("gold") or "").strip()
    tong_tl, tl_hot = _so_tl(request.POST.get("tong_tl")), _so_tl(request.POST.get("tl_hot"))
    kieu = "ban" if request.POST.get("kieu") == "ban" else "thau"
    gia_dong = _so(request.POST.get("gia"))
    gia = gia_dong / M.RATE_SCALE if gia_dong else M.D0
    de = next((x for x in S.loai_de() if x["GoldCode"] == ma), None)
    if not de:
        return _loi(request, "Chưa chọn loại vàng cũ", {"loi_o": "#t-de"})
    if tong_tl <= 0:
        return _loi(request, "Chưa nhập tổng trọng lượng", {"loi_o": "#t-tongtl"})
    if tl_hot >= tong_tl:
        return _loi(request, "Trọng lượng hột phải nhỏ hơn tổng trọng lượng", {"loi_o": "#t-tlhot"})
    pu = de.get("PriceUnit") or "L"
    moc = M.dec(de.get("SellRate") if kieu == "ban" else de.get("BuyRate"))
    if gia > 0 and moc > 0 and abs(gia - moc) / moc > M.dec("0.3"):
        return _loi(request, f"Giá {M.money_vn(gia * M.RATE_SCALE)} lệch quá 30% so với bảng giá "
                             f"{M.money_vn(moc * M.RATE_SCALE)} ₫/{de.get('don_vi', 'chỉ')} — nhập theo ĐỒNG, kiểm lại",
                    {"loi_o": "#t-gia"})
    gia = gia if gia > 0 else moc
    if gia <= 0:
        return _loi(request, f"Bảng giá chưa có giá {'bán ra' if kieu == 'ban' else 'thâu'} cho {de['GoldDesc']} — nhập tay", {"loi_o": "#t-gia"})
    tien = M.buy_amount_standalone(tong_tl - tl_hot, gia, 100, 0, pu)
    if tien <= 0:
        return _loi(request, "Thành tiền phải lớn hơn 0", {"loi_o": "#t-tongtl"})
    g = TC.get(request)
    g["kieu_ui"] = kieu
    TC.save(request, g)
    TC.them_dong(request, TC.dong(ma, de["GoldDesc"], pu, tong_tl, tl_hot, gia, kieu, tien))
    return _oob(request)


@require_POST
def thau_xoa_dong(request):
    if _dang_khoa(request):
        return _loi(request, KHOA_MSG)
    try:
        TC.xoa_dong(request, int(request.POST.get("i", -1)))
    except (TypeError, ValueError):
        pass
    return _oob(request)


@require_POST
def thau_tinh_lai(request):
    """Tính lại mọi dòng theo BẢNG GIÁ MySQL hiện hành với công tắc đang chọn (giá thâu vào / bán ra)."""
    if _dang_khoa(request):
        return _loi(request, KHOA_MSG)
    kieu = "ban" if request.POST.get("kieu") == "ban" else "thau"
    de_map = {x["GoldCode"]: x for x in S.loai_de()}
    g = TC.get(request)
    moi = []
    for x in g["lines"]:
        de = de_map.get(x["gold"])
        if not de:
            moi.append(x)
            continue
        gia = M.dec(de.get("SellRate") if kieu == "ban" else de.get("BuyRate"))
        if gia <= 0:
            moi.append(x)
            continue
        tien = M.buy_amount_standalone(M.dec(x["tl_vang"]), gia, 100, 0, x["unit"])
        moi.append(TC.dong(x["gold"], x["desc"], x["unit"], x["tong_tl"], x["tl_hot"], gia, kieu, tien,
                           x.get("trn_id"), x.get("bill_code"), x.get("upd")))
    g["lines"], g["kieu_ui"] = moi, kieu
    TC.save(request, g)
    return _oob(request, {"tin": f"Đã tính lại {len(moi)} dòng theo giá {'BÁN RA' if kieu == 'ban' else 'THÂU VÀO'}"})


@require_GET
def thau_tim(request):
    """Gợi ý khách (kind=khach) / nhân viên (kind=nv) — bấm là hx-post thau_dat (như màn bán)."""
    q = request.GET.get("q", "")
    kind = "nv" if request.GET.get("kind") == "nv" else "khach"
    if kind == "khach":
        cccd = S.cccd_tu_qr(q)
        ds = [k for k in S.tim_khach(cccd, limit=50) if (k.get("CMND") or "").strip() == cccd] if cccd else (S.tim_khach(q) if q.strip() else [])
    else:
        ds = S.tim_nhan_vien(q)
    return render(request, "pos/_thau_goiy.html", {"ds": ds, "kind": kind})


# ─────────────────────────── danh sách · mở lại ───────────────────────────
def _ngay(v, mac_dinh):
    try:
        return datetime.date.fromisoformat(v or mac_dinh).isoformat()
    except ValueError:
        return mac_dinh


@require_GET
def thau_ds(request):
    """Popup DANH SÁCH phiếu thâu (80vw, cùng kiểu Bán hàng): khoảng ngày · NV · khách · trạng thái + thống kê."""
    hom_nay = datetime.date.today().isoformat()
    d1, d2 = _ngay(request.GET.get("d1"), hom_nay), _ngay(request.GET.get("d2"), hom_nay)
    if d1 > d2:
        d1, d2 = d2, d1
    loc = {"emp_id": (request.GET.get("emp_id") or "").strip(), "khach": (request.GET.get("khach") or "").strip(),
           "trang_thai": (request.GET.get("trang_thai") or "").strip().upper()}
    try:
        ds, live = S.hoa_don_loc(d1, d2, loai="THAU", **loc)
        loi = ""
    except Exception as exc:
        ds, live, loi = [], True, S.error_message(exc)
    song = [r for r in ds if str(r.get("IsDel")) == "0"]
    chot = [r for r in song if r.get("Status") == "C"]
    tk = {"so": len(song), "chot": len(chot), "nhap": len(song) - len(chot),
          "tien": sum((M.dec(r.get("SoTien")) for r in chot), M.D0)}
    nhom = {}
    # ⚠ MySQL chưa nạp bảng timezone → lookup __date trả rỗng; lọc theo khoảng [d1, d2+1) như quy ước toàn hệ
    tu = datetime.datetime.fromisoformat(d1)
    den = datetime.datetime.fromisoformat(d2) + datetime.timedelta(days=1)
    from django.conf import settings as _st
    from django.utils import timezone as _tz
    if getattr(_st, "USE_TZ", False):
        tu, den = _tz.make_aware(tu), _tz.make_aware(den)
    for n in ThauNhom.objects.filter(created_at__gte=tu, created_at__lt=den):
        for t in n.trn_ids:
            nhom[t] = n
    for r in ds:
        n = nhom.get(r["TrnID"])
        r["nhom"] = n.pk if n else ""
        r["nhom_n"] = len(n.trn_ids) if n else 1
    return render(request, "pos/_thau_ds.html", {
        "ds": ds, "tk": tk, "d1": d1, "d2": d2, "hom_nay": hom_nay, "loc": loc, "nvs": S.nhan_vien_ban(),
        "nhieu_ngay": d1 != d2, "loi": loi, "nguon_hist": not live})


def _rows_cua(trn_id, c=None):
    """Các dòng TRN_RT_BUYGOLD cùng NHÓM với trn_id (theo ThauNhom) — không có nhóm thì chỉ dòng đó. Trả (rows, nhom)."""
    nhom = ThauNhom.objects.filter(trn_ids__contains=[trn_id]).order_by("-pk").first()
    ids = list(nhom.trn_ids) if nhom else [trn_id]
    rows = [B.phieu_thau(t, c) for t in ids]
    rows = [r for r in rows if r and str(r.get("IsDel")) == "0"]
    return rows, nhom


@require_POST
def thau_mo(request):
    trn = (request.POST.get("trn_id") or "").strip()
    try:
        rows, nhom = _rows_cua(trn)
    except Exception as exc:
        return _loi(request, "Không mở được phiếu: " + S.error_message(exc), {"dong_modal": True})
    if not rows:
        return _loi(request, f"Không thấy phiếu {trn}", {"dong_modal": True})
    g = TC.nap(request, rows, nhom)
    ma = ", ".join(g["bill_codes"])
    return _oob(request, {"tin": f"Đã mở {ma}" + (" (đã chốt — chỉ xem, 🔓 SỬA để đổi)" if g["status"] == B.CHOT_ROI else ""),
                          "dong_modal": True})


# ─────────────────────────── THANH TOÁN ───────────────────────────
def _kiem(request, g, ph):
    if not g["lines"]:
        return "Phiếu chưa có dòng vàng nào — nhập loại dẻ + trọng lượng rồi ＋ THÊM", "#t-tongtl"
    t = TC.tong_cua(g)
    if t["khach_tra"] <= 0:
        return f"Tiệm trả khách phải lớn hơn 0 (tiền vàng {M.money_vn(t['tien_vang'])} − bớt {M.money_vn(t['bot'])})", "#o-bot"
    for tien, add, ck, td in TC.phan_bo(g):
        if td <= 0:
            return "Tiền bớt lớn hơn dòng vàng lớn nhất — giảm bớt hoặc chia lại", "#o-bot"
    if not ph["user_id"]:
        return "Tài khoản web chưa gắn tài khoản PMV — vào trang Hệ thống đồng bộ lại", ""
    if not ph["till_id"]:
        return "Tài khoản chưa gắn KÉT — không ghi sổ quỹ được", ""
    if not g.get("emp"):
        return "Chưa chọn nhân viên thâu", "#o-nv"
    return "", ""


def _phieu_ctx(rows, nhom=None):
    """Dữ liệu tờ in 110mm cho cả NHÓM dòng."""
    r0 = rows[0]
    tong = sum((M.dec(r.get("TotalAmount")) for r in rows), M.D0)
    add = sum((M.dec(r.get("AddMoney")) for r in rows), M.D0)
    ck = sum((-M.dec(r.get("CardPay")) for r in rows if M.dec(r.get("CardPay")) < 0), M.D0)
    lines = []
    for r in rows:
        lines.append({"desc": r.get("GoldDesc") or r.get("GoldCode"), "gold": r.get("GoldCode"), "unit": r.get("WeightUnit") or "L",
                      "tl_vang": M.dec(r.get("GoldWeight")), "tl_hot": M.dec(r.get("DiamondWeight")),
                      "pct": M.dec(r.get("PercentValue")), "gia": M.dec(r.get("BuyRate")),
                      "tien": M.dec(r.get("TotalAmount")) - M.dec(r.get("AddMoney")), "bill": r.get("BillCode") or r["TrnID"]})
    return {"TrnID": r0["TrnID"], "BillCode": r0.get("BillCode") or r0["TrnID"], "bill_codes": [x["bill"] for x in lines],
            "TrnDate": r0.get("TrnDate"), "TrnTime": r0.get("TrnTime"), "EmpName": r0.get("EmpName"),
            "CustName": r0.get("CustName"), "Phone": r0.get("Phone"), "CMND": r0.get("CMND"), "Address": r0.get("Address"),
            "Notes": r0.get("Notes"), "lines": lines, "tien_vang": tong - add, "AddMoney": add, "TotalAmount": tong,
            "tien_ck": ck, "tien_mat": tong - ck, "tien_chu": _tien_bang_chu(tong), "so_dong": len(lines),
            "nhom": nhom.pk if nhom else ""}


@require_POST
def thau_thanh_toan(request):
    """Lưu từng dòng (Ins/Upd) rồi CHỐT CẢ NHÓM: CompleteMore 'A@B@' → CARDPAY_Ins (dòng có CK) → T_TILL_TXN_Proc 'A@B@'."""
    g = TC.get(request)
    ph = _phien(request)
    if _dang_khoa(request):
        return _loi(request, KHOA_MSG)
    loi, o = _kiem(request, g, ph)
    if loi:
        return _loi(request, loi, {"loi_o": o})
    khoa = f"khbl:thau_tt:{request.session.session_key}"
    if not cache.add(khoa, 1, 30):
        return _loi(request, "Đang thanh toán phiếu này — chờ vài giây, đừng bấm lại.")
    c = S.client("thau_tt")
    cust_id = (g.get("cust") or {}).get("id") or S.WALK_IN
    ids, ck_map = [], {}
    try:
        for x, (tien, add, ck, td) in zip(g["lines"], TC.phan_bo(g)):
            row = B.luu_thau(trn_id=x.get("trn_id") or "", cust_id=cust_id, emp_id=g["emp"], till_id=ph["till_id"],
                             shop_id=ph["shop_id"], user_id=ph["user_id"], gold_code=x["gold"], gw=M.dec(x["tl_vang"]),
                             dw=M.dec(x["tl_hot"]), rate=M.dec(x["gia"]), pct=100, add_money=add,
                             notes=g.get("ghi_chu") or "", unit=x["unit"], c=c)
            ids.append(row["TrnID"])
            if ck > 0:
                ck_map[row["TrnID"]] = ck
        B.chot_thau_nhom(ids, till_id=ph["till_id"], user_id=ph["user_id"], ck_theo_phieu=ck_map, c=c)
    except Exception as exc:
        cache.delete(khoa)
        # dòng đã Ins mà chốt hỏng: giữ trn_id vào giỏ để THANH TOÁN lại không tạo trùng
        if ids:
            for x, t in zip(g["lines"], ids):
                x["trn_id"] = t
            g["trn_ids"] = ids
            TC.save(request, g)
        return _loi(request, _loi_goi(exc) if isinstance(exc, PmvProcError) else str(exc))
    cache.delete(khoa)
    cache.delete("khbl:thau_treo")
    rows = [B.phieu_thau(t, c) for t in ids]
    t = TC.tong_cua(g)
    nhom = ThauNhom.objects.create(trn_ids=ids, bill_codes=[r.get("BillCode") or r["TrnID"] for r in rows],
                                   cust_id=cust_id, cust_name=(g.get("cust") or {}).get("name") or "", emp_id=g["emp"],
                                   user=request.user if request.user.is_authenticated else None,
                                   pay_method=g.get("pay_method") or "cash", tien_mat=t["tien_mat"], tien_ck=t["tien_ck"],
                                   bu=t["bu"], bot=t["bot"], ghi_chu=g.get("ghi_chu") or "", kieu=[x["kieu"] for x in g["lines"]])
    gateway.canh_bao("thau_tt", f"THÂU {', '.join(nhom.bill_codes)} — tiệm trả {M.money_vn(t['khach_tra'])} "
                                f"(CK {M.money_vn(t['tien_ck'])}); người: {request.user.username}")
    tin = f"Đã thanh toán {', '.join(nhom.bill_codes)} — tiệm trả {M.money_vn(t['khach_tra'])}"
    extra = {"tin": tin}
    if request.POST.get("in") == "1":
        p = _phieu_ctx(rows, nhom)
        extra["in_html"] = render_to_string("pos/_thau_phieu.html", {"p": p, "tiem": S.thong_tin_tiem(),
                                                                     "in_luc": datetime.datetime.now()}, request=request)
        extra["tin"] = tin + " · đang in phiếu — form đã trắng cho khách kế"
        TC.clear(request, giu_nv=True)
    else:
        g2 = TC.nap(request, rows, nhom, emp=g["emp"])
        g2["kieu_ui"] = g.get("kieu_ui") or "thau"
        TC.save(request, g2)
    return _oob(request, extra)


# ─────────────────────────── xác nhận (passcode) ───────────────────────────
def _xn_ctx(request, hanh_dong, loi=""):
    g = TC.get(request)
    hd = HANH_DONG.get(hanh_dong)
    if hd and hd.get("nhap"):
        hop_le = g.get("status") != B.CHOT_ROI and bool(g["lines"] or g.get("trn_ids"))
    else:
        hop_le = g.get("status") == B.CHOT_ROI and bool(hd)
    return {"g": g, "hd": hd, "hanh_dong": hanh_dong, "loi": loi, "khoa": hop_le,
            "nguoi": request.user.first_name or request.user.username, "username": request.user.username,
            "luc": datetime.datetime.now()}


@require_GET
def thau_xac_nhan(request, hanh_dong):
    if hanh_dong not in HANH_DONG:
        return HttpResponse("Hành động không hợp lệ", status=400)
    return render(request, "pos/_thau_xac_nhan.html", _xn_ctx(request, hanh_dong))


def _xoa_nhap(request):
    g = TC.get(request)
    ph = _phien(request)
    ids = [x["trn_id"] for x in g["lines"] if x.get("trn_id")] or list(g.get("trn_ids") or [])
    for t in ids:
        try:
            B.huy_thau(t, user_id=ph["user_id"])
        except Exception as exc:
            return _loi(request, f"Xóa {t} không được: {_loi_goi(exc)}", {"dong_modal": True})
    if ids:
        gateway.canh_bao("thau_xoa", f"XÓA phiếu thâu CHỜ {', '.join(ids)}; người: {request.user.username}")
    cache.delete("khbl:thau_treo")
    TC.clear(request, giu_nv=False)
    return _oob(request, {"tin": "Đã xóa phiếu nháp." if not ids else f"Đã xóa {len(ids)} dòng chờ trên máy KK.", "dong_modal": True})


@require_POST
def thau_thuc_hien(request, hanh_dong):
    hd = HANH_DONG.get(hanh_dong)
    if not hd:
        return HttpResponse("Hành động không hợp lệ", status=400)
    g = TC.get(request)
    ph = _phien(request)
    if hd.get("nhap"):
        if g.get("status") == B.CHOT_ROI:
            return _loi(request, "Phiếu đã CHỐT — dùng 🗑 XÓA (passcode).", {"dong_modal": True})
        return _xoa_nhap(request)
    ids = list(g.get("trn_ids") or [])
    if not ids:
        return _loi(request, "Chưa mở phiếu nào", {"dong_modal": True})
    if g.get("status") != B.CHOT_ROI:
        return _loi(request, "Phiếu đang CHỜ — không cần thao tác này.", {"dong_modal": True})
    if (g.get("ngay") or "") != datetime.date.today().isoformat() and not _co_fullcontrol_hoa_don(request):
        return render(request, "pos/_thau_xac_nhan.html", _xn_ctx(request, hanh_dong, loi="Phiếu của NGÀY KHÁC chỉ được xem — Admin mới có toàn quyền."))
    con = _passcode_khoa(request)
    if con:
        return render(request, "pos/_thau_xac_nhan.html", _xn_ctx(request, hanh_dong, loi=f"Sai passcode {PC_SAI_TOI_DA} lần — khóa nhập, thử lại sau {con} giây."))
    if not _passcode_dung(request, request.POST.get("passcode")):
        con = _passcode_khoa(request)
        return render(request, "pos/_thau_xac_nhan.html", _xn_ctx(
            request, hanh_dong, loi=f"Sai passcode {PC_SAI_TOI_DA} lần — khóa nhập {con} giây." if con else "Passcode không đúng — thử lại."))
    ma = ", ".join(g.get("bill_codes") or ids)
    c = S.client("thau_xn")
    try:
        for t in ids:
            if hanh_dong == "huy_hd":
                B.huy_thau(t, user_id=ph["user_id"], c=c)
            else:
                B.mo_lai_thau(t, user_id=ph["user_id"], c=c)
    except Exception as exc:
        return _loi(request, f"{hd['ten']} không được: {_loi_goi(exc)}", {"dong_modal": True})
    gateway.canh_bao("thau_" + hanh_dong, f"{hd['ten']} phiếu thâu {ma}; người: {request.user.username}")
    cache.delete("khbl:thau_treo")
    if hanh_dong == "sua":
        rows = [B.phieu_thau(t, c) for t in ids]
        nhom = ThauNhom.objects.filter(pk=g.get("nhom_id") or 0).first()
        g2 = TC.nap(request, rows, nhom, emp=g.get("emp"))
        g2["sua_lai"] = True
        TC.save(request, g2)
        tin = f"Đã mở {ma} về CHỜ để sửa — sửa xong bấm THANH TOÁN lại."
    elif hanh_dong == "huy_tt":
        TC.clear(request, giu_nv=False)
        tin = f"Đã HỦY THANH TOÁN {ma} — phiếu về DANH SÁCH CHỜ."
    else:
        TC.clear(request, giu_nv=False)
        tin = f"Đã XÓA phiếu thâu {ma}."
    return _oob(request, {"tin": tin, "dong_modal": True})


# ─────────────────────────── in 110mm ───────────────────────────
@require_GET
def thau_in(request):
    """In phiếu thâu 110mm cho cả nhóm: ?oob=1 → nhét #pos-in in THẲNG từ cửa sổ; mặc định trang standalone (auto=1 tự in)."""
    trn = (request.GET.get("trn_id") or "").strip() or ((TC.get(request).get("trn_ids") or [""])[0])
    try:
        rows, nhom = _rows_cua(trn) if trn else ([], None)
    except Exception as exc:
        return HttpResponse("Không đọc được phiếu: " + S.error_message(exc), status=503)
    if not rows:
        return HttpResponse("Không tìm thấy phiếu thâu.", status=404)
    if any(r.get("Status") != B.CHOT_ROI for r in rows):
        if request.GET.get("oob") == "1":
            return _loi(request, "Phiếu chưa THANH TOÁN — chốt xong mới in.")
        return HttpResponse("Phiếu chưa thanh toán.", status=409)
    p = _phieu_ctx(rows, nhom)
    ctx = {"p": p, "tiem": S.thong_tin_tiem(), "in_luc": datetime.datetime.now()}
    if request.GET.get("oob") == "1":
        return _oob(request, {"in_html": render_to_string("pos/_thau_phieu.html", ctx, request=request),
                              "tin": f"Đang in {p['BillCode']}"})
    ctx["auto"] = request.GET.get("auto") == "1"
    return render(request, "pos/thau_in.html", ctx)
