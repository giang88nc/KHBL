"""
Màn hình bán lẻ. v1 = TRA CỨU + TÍNH TOÁN + dựng PHIẾU TẠM (kênh ghi PMV chưa mở — RULE 2).
Mọi phép tính tiền gọi apps/pmv/money.py; mọi truy vấn gọi apps/pos/services.py.
"""
import datetime
import re

from django.contrib import messages
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from apps.pmv import money as M

from . import cart, cccd, services as S


# ─────────────────────────── TỔNG QUAN ───────────────────────────

def dashboard(request):
    ngay = request.GET.get("ngay") or datetime.date.today().isoformat()
    try:
        tq = S.tong_quan(ngay)
        loi = ""
    except Exception as exc:
        tq, loi = None, str(exc)
    return render(request, "pos/dashboard.html", {
        "nav_active": "tong", "ngay": ngay, "tq": tq, "loi_kk": loi,
        "gia": S.gia_noi_bat(), "gia_sig": S.gia_chu_ky(), "gia_moc": S.gia_moc(),
    })


def _so(x):
    """Bóc chữ số từ ô tiền có chấm nghìn ('1.234.000' → 1234000)."""
    s = re.sub(r"[^0-9\-]", "", str(x or ""))
    return M.dec(s or 0)


def _ctx_pos(request, extra=None):
    g = cart.get(request)
    ctx = {
        "nav_active": "ban", "gio": g, "t": cart.tong(request),
        "gia": S.gia_noi_bat(), "gia_sig": S.gia_chu_ky(), "gia_moc": S.gia_moc(),
        "nvs": S.nhan_vien_ban(), "loai_thau": S.loai_vang_thau(),
    }
    ctx.update(extra or {})
    return ctx


def _pos_oob(request, extra=None):
    """Trả 3 mảnh OOB: giỏ + ngăn thâu + dải quyết toán (+ toast)."""
    return render(request, "pos/_ban_oob.html", _ctx_pos(request, extra))


# ─────────────────────────── MUA BÁN ───────────────────────────

def ban(request):
    return render(request, "pos/ban.html", _ctx_pos(request))


@require_POST
def ban_quet(request):
    ma = (request.POST.get("ma") or "").strip()
    till = getattr(request, "khbl_till", "") or ""
    pu = request.POST.get("_till") or ""
    kq = S.quet_ma(ma, till_id=pu or till)
    if not kq["ok"]:
        return _pos_oob(request, {"scan_err": kq["err"], "scan_val": ma})
    ok, ly_do = cart.them_ban(request, kq["item"])
    if not ok:
        return _pos_oob(request, {"scan_err": {"code": "", "style": "cam",
                                               "desc": f"Mã {ma} đã có trong phiếu"}, "scan_val": ""})
    return _pos_oob(request, {"scan_ok": kq["item"]["code"]})


@require_POST
def ban_xoa(request):
    cart.xoa_ban(request, (request.POST.get("code") or "").strip())
    return _pos_oob(request)


@require_POST
def ban_mua_them(request):
    """Thêm 1 dòng VÀNG CŨ khách đưa vào (cùng hóa đơn bán — đúng cách PMV làm)."""
    gold = (request.POST.get("gold") or "").strip()
    gw = _so(request.POST.get("gw"))
    pct = M.dec(request.POST.get("pct") or 100)
    gm = S.gia_map().get(gold)
    if not gm or gw <= 0:
        return _pos_oob(request, {"mua_err": "Chọn loại vàng và nhập trọng lượng"})
    unit = (gm["PriceUnit"] or "L").upper()
    rate = M.dec(gm["BuyRate"])
    cart.them_mua(request, {
        "gold": gold, "gold_desc": gm["GoldDesc"], "unit": unit, "age": S.age_class(gold),
        "gw": str(gw), "rate": str(rate), "pct": str(pct),
        "amount": str(M.buy_amount_in_bill(gw, rate, pct, unit)),
    })
    return _pos_oob(request)


@require_POST
def ban_mua_xoa(request):
    try:
        cart.xoa_mua(request, int(request.POST.get("i", -1)))
    except (TypeError, ValueError):
        pass
    return _pos_oob(request)


@require_POST
def ban_dat(request):
    """Đặt khách / NV bán / giảm giá / công thêm / tiền khách đưa."""
    g = cart.get(request)
    if "cust_id" in request.POST:
        cid = (request.POST.get("cust_id") or "").strip()
        if not cid or cid == S.WALK_IN:
            g["cust"] = None
        else:
            k = S.khach_theo_id(cid)
            g["cust"] = {"id": k["CustID"], "code": k["CustCode"], "name": k["CustName"],
                         "phone": k["Phone"] or "", "diem": str(M.dec(k["Diem"]))} if k else None
    if "emp" in request.POST:
        g["emp"] = (request.POST.get("emp") or "").strip()
    if "discount" in request.POST:
        g["discount"] = str(_so(request.POST.get("discount")))
    if "task_add" in request.POST:
        g["task_add"] = str(_so(request.POST.get("task_add")))
    if "khach_dua" in request.POST:
        g["khach_dua"] = str(_so(request.POST.get("khach_dua")))
    cart.save(request, g)
    return _pos_oob(request)


@require_POST
def ban_bot_le(request):
    g = cart.get(request)
    t = cart.tong(request)
    g["discount"] = str(M.dec(g.get("discount") or 0) + M.bot_le(t["pay"]))
    cart.save(request, g)
    return _pos_oob(request)


@require_POST
def ban_moi(request):
    cart.clear(request)
    messages.success(request, "Đã bắt đầu phiếu mới.")
    return _pos_oob(request)


def ban_tim_khach(request):
    return render(request, "pos/_khach_goiy.html",
                  {"ds": S.tim_khach(request.GET.get("q", ""))})


def ban_tim_hang(request):
    return render(request, "pos/_tim_hang.html", {
        "ds": S.tim_hang(request.GET.get("q", ""), request.GET.get("sec", ""), request.GET.get("gold", "")),
        "q": request.GET.get("q", "")})


def ban_phieu(request):
    """Popup PHIẾU TẠM — nội dung sẽ gửi sang PMV khi kênh ghi mở (GĐ3)."""
    return render(request, "pos/_phieu_tam.html", _ctx_pos(request))


def ban_in(request):
    """Bản in thử giấy đảm bảo (chờ GĐ gửi mẫu giấy thật)."""
    ctx = _ctx_pos(request)
    ctx["hom_nay"] = datetime.date.today()
    return render(request, "pos/in_phieu.html", ctx)


# ─────────────────────────── BẢNG GIÁ ───────────────────────────

def bang_gia(request):
    rows = S.bang_gia()
    return render(request, "pos/bang_gia.html", {
        "nav_active": "gia", "rows": rows, "moc": S.gia_moc(),
        "lo": S.lich_su_gia(), "gia_sig": S.gia_chu_ky(rows),
        "vang": [r for r in rows if (r["Type"] or "").upper() == "G"],
        "de": [r for r in rows if (r["Type"] or "").upper() == "D"],
        "khac": [r for r in rows if (r["Type"] or "").upper() not in ("G", "D")],
    })


def gia_nhip(request):
    """Kênh poll 15s: chữ ký giống → 204 (không swap DOM). Khác → trả dải giá OOB."""
    rows = S.bang_gia()
    sig = S.gia_chu_ky(rows)
    if request.GET.get("sig") == sig:
        return HttpResponse(status=204)
    return render(request, "pos/_gia_strip.html",
                  {"gia": S.gia_noi_bat(), "gia_sig": sig, "gia_moc": S.gia_moc(), "oob": True})


# ─────────────────────────── KHÁCH HÀNG ───────────────────────────

MOI_TRANG = 50


def _ctx_khach(request):
    key = request.GET.get("key", "")
    addr = request.GET.get("addr", "")
    sinh = request.GET.get("sinh", "")
    try:
        trang = max(1, int(request.GET.get("trang") or 1))
    except ValueError:
        trang = 1
    ds, tong, so_trang = S.khach_loc(key, addr, sinh, trang, MOI_TRANG)
    trang = min(trang, so_trang)
    qs = request.GET.copy()
    qs.pop("trang", None)
    return {
        "nav_active": "khach", "ds": ds, "tong": tong, "trang": trang, "so_trang": so_trang,
        "key": key, "addr": addr, "sinh": sinh, "qstring": qs.urlencode(),
        "tu": (trang - 1) * MOI_TRANG + 1, "den": min(trang * MOI_TRANG, tong),
        "truoc": trang - 1 if trang > 1 else 0, "sau": trang + 1 if trang < so_trang else 0,
        "loai_ds": S.CUST_TYPES,
    }


def khach_hang(request):
    ctx = _ctx_khach(request)
    if request.headers.get("HX-Request") and request.GET.get("partial"):
        return render(request, "pos/_khach_bang.html", ctx)
    return render(request, "pos/khach_hang.html", ctx)


def khach_chi_tiet(request, cust_id):
    return render(request, "pos/_khach_modal.html", {
        "k": S.khach_theo_id(cust_id), "ls": S.lich_su_khach(cust_id)})


def khach_form(request, cust_id=None):
    """Popup THÊM / SỬA khách hàng — UI đầy đủ, có ô quét QR thẻ CCCD."""
    return render(request, "pos/_khach_form.html", {
        "k": S.khach_theo_id(cust_id) if cust_id else None,
        "loai_ds": S.CUST_TYPES, "duong_dan_anh": S.DUONG_DAN_ANH,
    })


@require_POST
def khach_luu(request):
    """v1 CHƯA GHI sang PMV (RULE 2). Kiểm tra dữ liệu, dựng đúng bộ tham số sẽ gửi cho
    I_CUSTOMER_Ins/_Upd rồi hiện lại để đối chiếu — mở kênh ghi ở giai đoạn sau."""
    d = request.POST
    ten = (d.get("CustName") or "").strip()
    loi = []
    if not ten:
        loi.append("Chưa nhập họ tên khách")
    phone = re.sub(r"[^\d]", "", d.get("Phone") or "")
    if phone and not (9 <= len(phone) <= 11):
        loi.append(f"Số điện thoại {phone} không hợp lệ (9–11 số)")
    cmnd = re.sub(r"[^\d]", "", d.get("CMND") or "")
    if cmnd and len(cmnd) not in (9, 12):
        loi.append(f"Số CCCD/CMND {cmnd} phải 9 hoặc 12 số")
    loai = (d.get("CustType") or "").strip().upper()
    if loai not in S.CUST_TYPE_MAP:
        loai = ""

    tham_so = {
        "p_CustID": (d.get("CustID") or "").strip(),
        "p_CustName": ten, "p_Phone": phone, "p_Address": (d.get("Address") or "").strip(),
        "p_CMND": cmnd, "p_BirthDate": _ngay_vn(d.get("BirthDate")),
        "p_Gender": "1" if d.get("Gender") == "1" else "0",
        "p_Email": (d.get("Email") or "").strip(),
        "p_NgayCap": _ngay_vn(d.get("NgayCap")), "p_NoiCap": (d.get("NoiCap") or "").strip(),
        "p_Notes": (d.get("Notes") or "").strip(),
        "CustType": loai or "(trống = Thường)",
        "p_Active": "1" if d.get("Active", "1") == "1" else "0",
    }
    return render(request, "pos/_khach_luu_kq.html", {
        "loi": loi, "tham_so": tham_so, "sua": bool(tham_so["p_CustID"]),
        "anh": [k for k in ("anh_dai_dien", "anh_truoc", "anh_sau") if request.FILES.get(k)],
    })


def _ngay_vn(s):
    """yyyy-mm-dd (ô date của trình duyệt) → dd/MM/yyyy như app gửi cho proc."""
    s = (s or "").strip()
    try:
        return datetime.date.fromisoformat(s).strftime("%d/%m/%Y")
    except ValueError:
        return ""


# ─────────────────────────── THÂU VÀO ───────────────────────────

def thau(request):
    ctx = {"nav_active": "thau", "loai": S.loai_vang_thau(), "nvs": S.nhan_vien_ban(),
           "gia": S.gia_noi_bat(), "gia_sig": S.gia_chu_ky(), "gia_moc": S.gia_moc()}
    return render(request, "pos/thau.html", ctx)


@require_POST
def thau_tinh(request):
    """Máy tính phiếu thâu: % nhân TRƯỚC, tiền bù/bớt cộng SAU (đúng công thức đã kiểm chứng)."""
    gold = (request.POST.get("gold") or "").strip()
    gw = _so(request.POST.get("gw"))
    pct = M.dec(request.POST.get("pct") or 100)
    bu = _so(request.POST.get("add_money"))
    dau = request.POST.get("dau") or "+"
    if dau == "-":
        bu = -bu
    gm = S.gia_map().get(gold)
    if not gm or gw <= 0:
        return render(request, "pos/_thau_kq.html", {"loi": "Chọn loại vàng và nhập trọng lượng"})
    unit = (gm["PriceUnit"] or "L").upper()
    rate = M.dec(gm["BuyRate"])
    tien = M.buy_amount_standalone(gw, rate, pct, bu, unit)
    return render(request, "pos/_thau_kq.html", {
        "gm": gm, "gw": gw, "pct": pct, "bu": bu, "rate": rate, "unit": unit, "tien": tien,
        "tho": M.dec(gw) / M.hs(unit) * rate * M.RATE_SCALE * pct / M.dec(100)})


# ─────────────────────────── HÓA ĐƠN HÔM NAY ───────────────────────────

def hoa_don(request):
    ngay = request.GET.get("ngay") or datetime.date.today().isoformat()
    rows = S.hoa_don_ngay(ngay)
    return render(request, "pos/hoa_don.html", {
        "nav_active": "hoadon", "ngay": ngay, "rows": rows, "tong": S.tong_ngay(rows),
        "la_hom_nay": ngay == datetime.date.today().isoformat()})


def hoa_don_nhip(request):
    ngay = request.GET.get("ngay") or datetime.date.today().isoformat()
    rows = S.hoa_don_ngay(ngay)
    sig = str(len(rows)) + "|" + "|".join(str(r["TrnID"]) for r in rows[:3])
    if request.GET.get("sig") == sig:
        return HttpResponse(status=204)
    return render(request, "pos/_hoa_don_bang.html",
                  {"rows": rows, "tong": S.tong_ngay(rows), "sig": sig, "oob": True})
