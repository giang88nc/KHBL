"""
Màn hình bán lẻ.
Mọi phép tính tiền gọi apps/pmv/money.py · truy vấn gọi apps/pos/services.py ·
ghi hóa đơn gọi apps/pos/bill.py (KHÔNG view nào tự gọi proc hóa đơn).
"""
import datetime
import hashlib
import json
from collections import OrderedDict
import logging
import re
import secrets

from django.contrib import messages
from django.db import DatabaseError
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_GET, require_POST

from apps.pmv import money as M
from apps.pmv.client import PmvClient, PmvProcError
from apps.pmv.models import PmvUser

from . import bill as B, cart, cccd, customer as C, services as S, vietqr as QR


logger = logging.getLogger(__name__)


# ─────────────────────────── TỔNG QUAN ───────────────────────────

def dashboard(request):
    # Tổng quan là màn điều hành hiện thời: luôn đọc trực tiếp MSSQL máy KK, không dùng kho lịch sử.
    ngay = datetime.date.today().isoformat()
    try:
        tq = S.tong_quan_realtime(ngay)
        loi = ""
    except Exception as exc:
        tq, loi = None, str(exc)
    return render(request, "pos/dashboard.html", {
        "nav_active": "tong", "ngay": ngay, "tq": tq, "loi_kk": loi, "hide_dich": True,
    })


def _so(x):
    """Bóc chữ số từ ô tiền có chấm nghìn ('1.234.000' → 1234000)."""
    s = re.sub(r"[^0-9\-]", "", str(x or ""))
    return M.dec(s or 0)


_CODE39 = {
    "0": "nnnwwnwnn", "1": "wnnwnnnnw", "2": "nnwwnnnnw", "3": "wnwwnnnnn",
    "4": "nnnwwnnnw", "5": "wnnwwnnnn", "6": "nnwwwnnnn", "7": "nnnwnnwnw",
    "8": "wnnwnnwnn", "9": "nnwwnnwnn", "-": "nnnwnnnww", "*": "nwnnwnwnn",
}


def _codebar(code):
    """Các vạch Code 39 cho bản in GĐB (mã phiếu chỉ chứa số và dấu gạch ngang)."""
    chars = "*" + re.sub(r"[^0-9-]", "-", str(code or "").upper()) + "*"
    bars = []
    for pos, char in enumerate(chars):
        for index, width in enumerate(_CODE39.get(char, _CODE39["-"])):
            bars.append({"bar": index % 2 == 0, "w": 3 if width == "w" else 1})
        if pos < len(chars) - 1:
            bars.append({"bar": False, "w": 1})
    return bars


def _tien_bang_chu(value):
    """Đọc số tiền VNĐ để in ngay dưới tổng thanh toán của giấy đảm bảo."""
    number = int(abs(M.dec(value or 0)))
    digit = ("không", "một", "hai", "ba", "bốn", "năm", "sáu", "bảy", "tám", "chín")

    def doc_nhom(n, doc_du=False):
        tram, phan_du = divmod(n, 100)
        chuc, don_vi = divmod(phan_du, 10)
        out = []
        if tram or doc_du:
            out += [digit[tram], "trăm"]
        if chuc:
            out.append("mười" if chuc == 1 else f"{digit[chuc]} mươi")
        elif don_vi and (tram or doc_du):
            out.append("lẻ")
        if don_vi:
            if don_vi == 1 and chuc > 1:
                out.append("mốt")
            elif don_vi == 5 and chuc:
                out.append("lăm")
            else:
                out.append(digit[don_vi])
        return " ".join(out)

    if not number:
        return "Không đồng"
    groups, scales = [], ("", "nghìn", "triệu", "tỷ", "nghìn tỷ", "triệu tỷ")
    while number:
        groups.append(number % 1000)
        number //= 1000
    out = []
    top = len(groups) - 1
    for index in range(top, -1, -1):
        if groups[index]:
            out.append(doc_nhom(groups[index], index != top))
            if scales[index]:
                out.append(scales[index])
    text = " ".join(out) + " đồng"
    return text[:1].upper() + text[1:]


def _so_tl(x):
    """Bóc TRỌNG LƯỢNG có thể LẺ (kiểu VN: phẩy = thập phân, chấm = nghìn).
    '258,2'→258.2 · '1.234,5'→1234.5 · '258.2'→258.2 · '250'→250. Khác _so (parser tiền, bỏ hết dấu)."""
    s = str(x or "").strip().replace(" ", "")
    if not s:
        return M.D0
    if "," in s:                       # có phẩy → phẩy là thập phân, chấm là nghìn
        s = s.replace(".", "").replace(",", ".")
    s = re.sub(r"[^0-9.\-]", "", s)
    try:
        return M.dec(s or 0)
    except Exception:
        return M.D0


def _phien(request):
    """Bối cảnh PMV của người đang đăng nhập: tài khoản · két · tiệm."""
    pu = PmvUser.objects.filter(django_user=request.user).first()
    tiem = S.thong_tin_tiem() or {}
    return {
        "user_id": (pu.user_id if pu else ""),
        "till_id": (pu.till_id if pu else "") or "",
        "emp_id": (pu.emp_id if pu else "") or "",
        "shop_id": tiem.get("ShopID") or "",
    }


def _ctx_pos(request, extra=None):
    g = cart.get(request)
    ph = _phien(request)
    ctx = {
        "nav_active": "ban", "g": g, "t": cart.tong(request), "phien": ph,
        "gia": S.gia_noi_bat(), "gia_sig": S.gia_chu_ky(), "gia_moc": S.gia_moc(),
        "nvs": S.nhan_vien_ban(), "loai_de": S.loai_de(), "hm_ngang": _hm_ngang_list(g),
        "banks": QR.active_banks(), "bank_sel": str(g.get("bank_id") or QR.default_bank_id() or ""),
        "treo": __import__("apps.pos.don", fromlist=["don_treo"]).don_treo(),   # đơn W treo >30' (v5 pha 9)
        "ngay": g.get("ngay") or datetime.date.today().isoformat(),
        "hom_nay": datetime.date.today().isoformat(),
        "ma_du_kien": g.get("trn_id") or _ma_du_kien_an_toan(),
        "dang_sua": bool(g.get("trn_id")),
        # hóa đơn đã chốt thì proc vendor TỪ CHỐI sửa — phải MỞ LẠI trước (bill.py luật 1)
        "phieu_chot": g.get("status") == B.CHOT_ROI,
    }
    ctx["ten_nv"] = next((e["EmpName"] for e in ctx["nvs"] if e["EmpID"] == g.get("emp")), "")
    ctx.update(extra or {})
    return ctx


def _ma_du_kien_an_toan():
    """Mã dự kiến chỉ để người bán dễ hình dung — hỏng thì thôi, không chặn bán hàng."""
    try:
        return B.ma_du_kien()
    except Exception:
        return ""


def _pos_oob(request, extra=None):
    """Trả các mảnh OOB: thông tin phiếu + vàng bán + vàng đổi + tính tổng (+ toast).
    v5 (GĐ chốt 1): đây là ĐIỂM RA của mọi thao tác → đồng bộ giỏ thành đơn W thật trên PMV
    (Ins ngay món đầu, mỗi thay đổi = 1 Upd; idempotent theo vân tay giỏ)."""
    from . import don

    extra = dict(extra or {})
    loi = don.dong_bo(request)
    if loi and not extra.get("loi_phieu"):
        extra["loi_phieu"] = loi
    return render(request, "pos/_ban_oob.html", _ctx_pos(request, extra))


def _loi(request, thong_diep, extra=None):
    e = {"loi_phieu": thong_diep}
    e.update(extra or {})
    return _pos_oob(request, e)


# ─────────────────────────── MUA BÁN ───────────────────────────

def ban(request):
    return render(request, "pos/ban.html", _ctx_pos(request))


@require_POST
def ban_quet(request):
    """Quét tem hoặc gõ mã hàng → thêm 1 dòng VÀNG BÁN, ô nhập tự trống để quét tiếp."""
    ma = (request.POST.get("ma") or "").strip()
    ph = _phien(request)
    kq = S.quet_ma(ma, till_id=ph["till_id"])
    if not kq["ok"]:
        err = dict(kq["err"])
        if err.get("code") == "P-008":            # v5 pha 9: báo rõ ĐƠN NÀO đang giữ SP
            from . import don
            d = don.don_giu_sp(ma)
            if d:
                cd = d.get("CreatedDate")
                err["desc"] += (f" — đang trong đơn {'chờ' if d.get('Status') == B.NHAP else 'ĐÃ THANH TOÁN'} "
                                f"{d.get('BillCode') or d.get('TrnID')} · {d.get('CreatedBy') or ''} · "
                                f"két {d.get('TillID') or ''} · {cd:%H:%M %d/%m}" if cd else "")
                if d.get("Status") == B.NHAP and str(d.get("CreatedBy") or "") == str(ph["user_id"]):
                    err["mo_don"] = d.get("TrnID")      # đơn của chính mình → nút MỞ
        return _pos_oob(request, {"scan_err": err, "scan_val": ma})
    row = S.quet_ma_row(ma, till_id=ph["till_id"])
    if not row:
        return _pos_oob(request, {"scan_err": {"code": "", "style": "do",
                                               "desc": f"Không đọc lại được mã {ma}"}, "scan_val": ma})
    tien, dong = B.dong_ban_tu_quet(row, len(cart.get(request)["ban"]) + 1)
    ok, vi_sao = cart.them_ban(request, tien, dong)
    if not ok:
        return _pos_oob(request, {"scan_err": {"code": "", "style": "cam", "desc": vi_sao},
                                  "scan_val": ""})
    return _pos_oob(request, {"scan_ok": ma})


@require_POST
def ban_xoa(request):
    cart.xoa_ban(request, (request.POST.get("code") or "").strip())
    return _pos_oob(request)


def _hm_ngang_list(g):
    """[{base, con}] — hạn mức đổi ngang CÒN LẠI theo từng loại vàng đang bán (>0 mới hiện).
    Dùng cho gợi ý trên khối VÀNG ĐỔI + JS tính trước khi tick 'Đổi ngang'."""
    bases = {(x["row"].get("GoldCode") or "").strip() for x in g["ban"]}
    out = []
    for b in sorted(x for x in bases if x):
        con = _han_muc_doi_ngang(g, b)
        if con > 0:
            out.append({"base": b, "con": str(con)})
    return out


def _han_muc_doi_ngang(g, base):
    """TL vàng bán ra CÙNG LOẠI (base) còn lại cho đổi ngang = Σ GoldReal hàng bán loại đó
    − Σ TL vàng các dòng dẻ đã đổi ngang loại đó."""
    ban = sum((M.dec(x["row"].get("GoldReal")) for x in g["ban"]
               if (x["row"].get("GoldCode") or "").strip() == base), M.D0)
    da_dung = sum((M.dec(x["row"].get("GoldWeight")) for x in g["doi"]
                   if str(x["row"].get("DoiNgang")) == "1" and M.de_base(x["row"].get("GoldCode")) == base), M.D0)
    return ban - da_dung


@require_POST
def ban_doi_them(request):
    """Thêm dòng VÀNG ĐỔI (dẻ khách đưa). Không đổi ngang → 1 dòng giá thâu (như cũ).
    Đổi ngang → chia theo HẠN MỨC vàng bán cùng loại: phần trong hạn mức giá BÁN RA, phần dư giá thâu."""
    ma = (request.POST.get("gold") or "").strip()
    tong_tl = _so_tl(request.POST.get("tong_tl"))
    tl_hot = _so_tl(request.POST.get("tl_hot"))
    doi_ngang = request.POST.get("doi_ngang") == "1"
    # ⚠ Người bán gõ giá theo ĐỒNG ("8.750.000 ₫/chỉ"), PMV lưu theo NGHÌN (8750).
    gia_dong = _so(request.POST.get("gia"))
    gia = gia_dong / M.RATE_SCALE if gia_dong else M.D0
    de = next((x for x in S.loai_de() if x["GoldCode"] == ma), None)
    if not de:
        return _loi(request, "Chưa chọn loại dẻ")
    if tong_tl <= 0:
        return _loi(request, "Chưa nhập tổng trọng lượng vàng đổi")
    if tl_hot > tong_tl:
        return _loi(request, "Trọng lượng hột không được lớn hơn tổng trọng lượng")
    gia_thau = gia if gia > 0 else M.dec(de["BuyRate"])
    pu = de["PriceUnit"]

    if not doi_ngang:
        if gia_thau <= 0:
            return _loi(request, f"Bảng giá chưa có giá đổi cho {de['GoldDesc']} — nhập tay giá đổi")
        tien, dong = B.dong_doi(ma, de["GoldDesc"], tong_tl, tl_hot, gia_thau, pu)
        cart.them_doi(request, tien, dong)
        return _pos_oob(request)

    # ── ĐỔI NGANG ──
    # Hạn mức = TL vàng bán ra cùng loại còn lại. Không có hàng bán loại đó (hạn mức ≤ 0)
    # thì KHÔNG chặn (GĐ chốt 05/09/2026): coi như hạn mức 0 → toàn bộ TL vàng đổi tính
    # GIÁ THÂU như thường ((tổng − hột) × giá thâu). Có hàng bán thì phần trong hạn mức mới
    # tính giá BÁN RA.
    g = cart.get(request)
    base = de.get("base") or M.de_base(ma)
    han_muc = max(_han_muc_doi_ngang(g, base), M.D0)
    sell = M.dec(de.get("SellRate"))
    if han_muc > 0 and sell <= 0:
        return _loi(request, f"Bảng giá chưa có giá BÁN RA cho {de['GoldDesc']} — không đổi ngang được")
    tl_vang = tong_tl - tl_hot
    phan = M.chia_doi_ngang(tl_vang, tl_hot, han_muc, sell, gia_thau, pu)
    if any(not p["ngang"] and p["w"] > 0 for p in phan) and gia_thau <= 0:
        return _loi(request, f"Phần tính GIÁ THÂU của {de['GoldDesc']} chưa có giá — nhập tay giá đổi")
    for p in phan:
        tien, dong = B.dong_doi(ma, de["GoldDesc"], p["w"] + p["hot"], p["hot"], p["rate"], pu, doi_ngang=p["ngang"])
        cart.them_doi(request, tien, dong)
    return _pos_oob(request)


@require_POST
def ban_doi_xoa(request):
    try:
        cart.xoa_doi(request, int(request.POST.get("i", -1)))
    except (TypeError, ValueError):
        pass
    return _pos_oob(request)


@require_POST
def ban_doi_tinh_lai(request):
    """GÔM & TÍNH LẠI vàng đổi theo TỪNG LOẠI VÀNG (GĐ chốt 05/09/2026): mỗi loại tối đa
    2 dòng — 1 NGANG (phần TL vàng trong hạn mức bán ra cùng loại, giá BÁN RA) + 1 THÂU
    (phần dư, giá thâu). Dùng ĐƠN GIÁ CHUẨN từ bảng giá, giống lúc THÊM có tick đổi ngang."""
    g = cart.get(request)
    if not g["doi"]:
        return _pos_oob(request)
    loai_map = {d["GoldCode"]: d for d in S.loai_de()}
    # gộp TL vàng + hột theo GoldCode, giữ thứ tự xuất hiện
    groups, orig = OrderedDict(), OrderedDict()
    for x in g["doi"]:
        code = (x["row"].get("GoldCode") or "").strip()
        grp = groups.setdefault(code, {"vang": M.D0, "hot": M.D0})
        grp["vang"] += M.dec(x["row"].get("GoldWeight"))
        grp["hot"] += M.dec(x["row"].get("DiamondWeight"))
        orig.setdefault(code, []).append(x)
    # hạn mức đổi ngang theo BASE = Σ GoldReal hàng bán cùng loại; chia dần nếu nhiều dẻ cùng base
    budget = {}

    def _budget(base):
        if base not in budget:
            budget[base] = max(sum((M.dec(x["row"].get("GoldReal")) for x in g["ban"]
                                    if M.de_base(x["row"].get("GoldCode")) == base), M.D0), M.D0)
        return budget[base]

    rows = []
    for code, grp in groups.items():
        de = loai_map.get(code)
        if not de:  # loại không còn trong bảng giá → giữ nguyên các dòng cũ (không có đơn giá chuẩn)
            rows.extend((M.dec(x["tien"]), x["row"]) for x in orig[code])
            continue
        base = de.get("base") or M.de_base(code)
        pu = de["PriceUnit"]
        buy = M.dec(de.get("BuyRate"))
        sell = M.dec(de.get("SellRate"))
        hm = _budget(base) if sell > 0 else M.D0
        phan = M.chia_doi_ngang(grp["vang"], grp["hot"], hm, sell, buy, pu)
        if base in budget:  # trừ phần đã đổi ngang để dẻ cùng base sau không lấn hạn mức
            budget[base] = max(budget[base] - sum((p["w"] for p in phan if p["ngang"]), M.D0), M.D0)
        for p in phan:
            rows.append(B.dong_doi(code, de["GoldDesc"], p["w"] + p["hot"], p["hot"], p["rate"], pu,
                                   doi_ngang=p["ngang"]))
    cart.dat_doi(request, rows)
    return _pos_oob(request, {"tin": "Đã gộp & tính lại vàng đổi theo từng loại."})


@require_POST
def ban_dat(request):
    """Đặt khách · nhân viên · ngày · các khoản tiền · ghi chú."""
    g = cart.get(request)
    if "cust_id" in request.POST:
        cid = (request.POST.get("cust_id") or "").strip()
        k = S.khach_theo_id(cid) if cid and cid != S.WALK_IN else None
        g["cust"] = {"id": k["CustID"], "code": k["CustCode"], "name": k["CustName"],
                     "phone": k["Phone"] or "", "diem": str(M.dec(k["Diem"]))} if k else None
    if "emp" in request.POST:
        g["emp"] = (request.POST.get("emp") or "").strip()
    if "ghi_chu" in request.POST:
        g["ghi_chu"] = (request.POST.get("ghi_chu") or "").strip()[:200]
    for o in ("bot", "cong_them", "vang_them", "coc"):
        if o in request.POST:
            g[o] = str(M.tron_ngan(_so(request.POST.get(o))))  # mọi khoản tiền tròn hàng nghìn
    if "pay_method" in request.POST:
        pm = (request.POST.get("pay_method") or "").strip()
        g["pay_method"] = pm if pm in ("cash", "bank", "card") else "cash"
    if "tien_mat" in request.POST:                 # rỗng = auto theo phương thức, có số = chốt tay
        v = (request.POST.get("tien_mat") or "").strip()
        g["tien_mat"] = str(M.tron_ngan(_so(v))) if v else ""
    if "bank_id" in request.POST:
        g["bank_id"] = (request.POST.get("bank_id") or "").strip()
    cart.save(request, g)
    return _pos_oob(request)


@require_GET
def ban_qr(request):
    """Popup mã QR chuyển khoản VietQR (dựng offline). Tài khoản nhận lấy từ MySQL gold_bank
    theo bank_id (mặc định TK 666141168); số tiền = amount (ô CK), ≤0 → QR để người chuyển tự nhập."""
    import base64
    from io import BytesIO
    import segno

    g = cart.get(request)
    bank_id = (request.GET.get("bank_id") or g.get("bank_id") or "").strip() or QR.default_bank_id()
    row = QR.get_bank(bank_id)
    if not row:
        return render(request, "pos/_qr_modal.html", {"loi": "Chưa có tài khoản ngân hàng nhận (gold_bank)"})
    amount = _so(request.GET.get("amount"))
    info = (g.get("bill_code") or g.get("trn_id") or "").strip()
    ten_kh = (g.get("cust") or {}).get("name") or ""
    try:
        chuoi = QR.payload(row["bank_bin"], row["bank_number"], amount, info)
    except ValueError as exc:
        return render(request, "pos/_qr_modal.html", {"loi": str(exc)})
    buf = BytesIO()
    segno.make(chuoi, error="m").save(buf, kind="png", scale=7, border=2)
    img = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")
    return render(request, "pos/_qr_modal.html", {
        "qr_img": img, "bank_ten": row.get("bank_name") or QR.bank_ten(row["bank_bin"]),
        "bank_num": row["bank_number"], "bank_user": row.get("bank_user") or "",
        "amount": M.dec(amount), "info": info, "ten_kh": ten_kh,
    })


@require_POST
def ban_bot_le(request):
    g = cart.get(request)
    t = cart.tong(request)
    g["bot"] = str(M.dec(g.get("bot") or 0) + M.bot_le(t["khach_tra"]))
    cart.save(request, g)
    return _pos_oob(request)


@require_POST
def ban_moi(request):
    cart.clear(request)
    return _pos_oob(request, {"tin": "Đã mở phiếu mới."})


def ban_tim_khach(request):
    return render(request, "pos/_khach_goiy.html", {"ds": S.tim_khach(request.GET.get("q", ""))})


def ban_tim_nv(request):
    return render(request, "pos/_nv_goiy.html", {"ds": S.tim_nhan_vien(request.GET.get("q", ""))})


def ban_tim_hang(request):
    return render(request, "pos/_tim_hang.html", {
        "ds": S.tim_hang(request.GET.get("q", ""), request.GET.get("sec", ""),
                         request.GET.get("gold", "")),
        "q": request.GET.get("q", "")})


# ─────── hóa đơn đã lưu: danh sách · mở · mở lại · thanh toán · xóa ───────

def ban_ds(request):
    """Popup DANH SÁCH hóa đơn theo ngày."""
    ngay = request.GET.get("ngay") or datetime.date.today().isoformat()
    try:
        ds, loi = B.trong_ngay(ngay), ""
    except Exception as exc:
        ds, loi = [], str(exc)
    return render(request, "pos/_ban_ds.html", {"ds": ds, "ngay": ngay, "loi": loi})


@require_POST
def ban_mo(request):
    """Nạp 1 hóa đơn đã lưu lên form để xem / sửa."""
    trn = (request.POST.get("trn_id") or "").strip()
    try:
        phieu = B.doc(trn)
    except Exception as exc:
        return _loi(request, f"Không mở được hóa đơn: {exc}")
    if not phieu:
        return _loi(request, f"Không thấy hóa đơn {trn}")
    cart.nap(request, phieu)
    tin = f"Đã mở {phieu['bill_code'] or trn} · {phieu['ten_trang_thai']}"
    if not phieu["sua_duoc"]:
        tin += " — muốn sửa phải bấm MỞ LẠI."
    return _pos_oob(request, {"tin": tin})


@require_POST
def ban_mo_lai(request):
    """Đưa hóa đơn ĐÃ CHỐT về nháp để sửa (hủy phần sổ quỹ)."""
    g = cart.get(request)
    ph = _phien(request)
    if not g.get("trn_id"):
        return _loi(request, "Chưa mở hóa đơn nào")
    try:
        B.mo_lai(g["trn_id"], user_id=ph["user_id"])
        cart.nap(request, B.doc(g["trn_id"]))
    except Exception as exc:
        return _loi(request, f"Mở lại không được: {exc}")
    return _pos_oob(request, {"tin": "Đã mở lại — hóa đơn về trạng thái nháp, sửa được rồi."})


def _kiem_truoc_khi_luu(request, g, ph):
    if not g["ban"] and not g["doi"]:
        return "Phiếu chưa có món nào"
    if not ph["user_id"]:
        return "Tài khoản web chưa gắn với tài khoản PMV — vào trang Hệ thống đồng bộ lại"
    if not ph["till_id"]:
        return "Tài khoản chưa gắn KÉT — không ghi sổ quỹ được"
    if not g.get("emp"):
        return "Chưa chọn nhân viên bán"
    return ""


@require_POST
def ban_thanh_toan(request):
    """Lưu phiếu rồi CHỐT. GĐ chốt 03/09/2026: hóa đơn mới luôn mang ngày HÔM NAY."""
    g = cart.get(request)
    ph = _phien(request)
    loi = _kiem_truoc_khi_luu(request, g, ph)
    if loi:
        return _loi(request, loi)
    c = S.client("thanh_toan")
    now = datetime.datetime.now()
    try:
        kq = B.luu(trn_id=g.get("trn_id") or "", ban=cart.dong_ban(g), doi=cart.dong_doi(g),
                   ngay=c.fmt_date(now.date()), gio=c.fmt_time(now),
                   cust_id=(g.get("cust") or {}).get("id") or S.WALK_IN,
                   emp_id=g.get("emp"), till_id=ph["till_id"], shop_id=ph["shop_id"],
                   user_id=ph["user_id"], ghi_chu=g.get("ghi_chu") or "",
                   bot=g.get("bot"), cong_them=g.get("cong_them"),
                   vang_them=g.get("vang_them"), coc=g.get("coc"), c=c)
        B.chot(kq["trn_id"], till_id=ph["till_id"], user_id=ph["user_id"], c=c)
    except Exception as exc:
        return _loi(request, _loi_goi(exc))
    cart.nap(request, B.doc(kq["trn_id"], c))
    tin = f"Đã thanh toán {kq['bill_code']} — {M.money_vn(kq['tong']['khach_tra'])}"
    return _pos_oob(request, {"tin": tin, "vua_chot": kq["trn_id"],
                              "in_luon": request.POST.get("in") == "1"})


@require_POST
def ban_huy(request):
    """Nút XÓA: phiếu chưa lưu thì dọn form; hóa đơn đã lưu thì HỦY THẬT (hàng về kho)."""
    g = cart.get(request)
    ph = _phien(request)
    trn = g.get("trn_id")
    if not trn:
        cart.clear(request)
        return _pos_oob(request, {"tin": "Đã xóa phiếu đang nhập."})
    try:
        B.huy(trn, user_id=ph["user_id"])
    except Exception as exc:
        return _loi(request, f"Hủy không được: {_loi_goi(exc)}")
    cart.clear(request)
    return _pos_oob(request, {"tin": f"Đã hủy hóa đơn {g.get('bill_code') or trn} — hàng trả về kho."})


def _loi_goi(exc):
    """Lấy câu lỗi NGUYÊN VĂN của vendor nếu có, đỡ phải đoán."""
    sets = getattr(exc, "sets", None)
    if sets:
        for s in sets:
            if isinstance(s, dict) and s.get("loi"):
                return s["loi"]
            for r in (s if isinstance(s, list) else []):
                if isinstance(r, dict) and r.get("ErrorDesc"):
                    return r["ErrorDesc"]
    return str(exc)


def ban_in(request):
    """GIẤY ĐẢM BẢO — mẫu tạm, chờ GĐ đưa mẫu giấy thật."""
    ctx = _ctx_pos(request)
    ctx["hom_nay"] = datetime.date.today()
    ctx["tiem"] = S.thong_tin_tiem()
    return render(request, "pos/in_phieu.html", ctx)


# ─────────────────────────── BẢNG GIÁ ───────────────────────────

@require_GET
def bang_gia(request):
    from . import prices
    ctx = prices.page_data()
    ctx["edit_token"] = prices.edit_token(ctx["prices"], request.user.pk, ctx["sync_target"])
    return render(request, "pos/bang_gia.html", {"nav_active": "gia", **ctx})


@require_POST
def gia_cap_nhat(request):
    from . import prices
    try:
        prices.save_prices(request.POST, request.user)
    except (prices.PriceError, DatabaseError) as exc:
        if isinstance(exc, DatabaseError):
            logger.exception("Không thể xác nhận cập nhật bảng giá")
            messages.error(request, "Không thể xác nhận cập nhật MySQL. Hãy kiểm tra trạng thái lô mới nhất trước khi thử lại.")
            return redirect("pos:bang_gia")
        ctx = prices.page_data()
        for row in ctx["prices"]:
            key = str(row["id"])
            row["buy_input"] = request.POST.get("buy_" + key, str(row["buy"]))
            row["sell_input"] = request.POST.get("sell_" + key, str(row["sell"]))
            row["position"] = request.POST.get("position_" + key, row["position"])
            row["pinned"] = request.POST.get("pinned_" + key) == "on"
            if row.get("unit_choice"):
                row["unit"] = request.POST.get("unit_" + key, "")
        ctx.update(edit_token=request.POST.get("edit_token", ""), price_error=str(exc))
        return render(request, "pos/bang_gia.html", {"nav_active": "gia", **ctx}, status=400)
    return redirect("pos:bang_gia")


@require_POST
def gia_dong_bo_lai(request, batch_id):
    from . import prices
    try:
        prices.retry_sync(batch_id)
    except (prices.PriceError, prices.PriceBatch.DoesNotExist) as exc:
        messages.error(request, str(exc) if isinstance(exc, prices.PriceError) else "Không tìm thấy lô giá.")
    return redirect("pos:bang_gia")


@require_GET
def gia_xem_bang(request):
    from .prices import board_data
    response = render(request, "pos/gia_niem_yet.html", board_data())
    response["Cache-Control"] = "no-store"
    response["X-Frame-Options"] = "SAMEORIGIN"
    return response


@require_POST
def gia_luu_png(request):
    """Lưu nguyên bytes PNG đã render trong trình duyệt, không đổi font/màu ảnh."""
    from io import BytesIO
    from pathlib import Path
    import uuid
    from PIL import Image, UnidentifiedImageError
    from django.conf import settings
    from django.http import JsonResponse
    from django.urls import reverse
    from django.utils import timezone

    raw = request.body
    if request.content_type != "image/png" or not raw or len(raw) > 2_000_000:
        return JsonResponse({"error": "Ảnh PNG không hợp lệ hoặc lớn hơn 2 MB."}, status=400)
    try:
        with Image.open(BytesIO(raw)) as img:
            if img.format != "PNG" or img.width * img.height > 4_000_000:
                raise ValueError
            img.verify()
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
        return JsonResponse({"error": "Không đọc được ảnh PNG."}, status=400)
    export_id = uuid.uuid4()
    directory = Path(settings.BASE_DIR) / "exports" / "bang-gia" / str(export_id)
    directory.mkdir(parents=True, exist_ok=False)
    filename = timezone.localtime().strftime("%Y-%m-%d-bang-gia-vang-%H-%M-%S.png")
    (directory / filename).write_bytes(raw)
    return JsonResponse({"url": reverse("pos:gia_tai_png", args=[export_id]), "filename": filename})


@require_GET
def gia_tai_png(request, export_id):
    from pathlib import Path
    from django.conf import settings
    from django.http import FileResponse, Http404
    directory = Path(settings.BASE_DIR) / "exports" / "bang-gia" / str(export_id)
    path = next(directory.glob("*.png"), None)
    if not path:
        raise Http404
    response = FileResponse(path.open("rb"), as_attachment=True, filename=path.name, content_type="image/png")
    response["Cache-Control"] = "private, no-store"
    return response


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
        "save_token": secrets.token_urlsafe(24),
    })


@require_GET
def khach_anh(request, cust_id, kind):
    """Phục vụ ảnh PMV theo mã khách; URL không bao giờ nhận đường dẫn đĩa."""
    try:
        data, content_type = C.saved_image(cust_id, kind)
    except Exception as exc:
        logger.warning("Không đọc được ảnh khách %s/%s: %s", cust_id, kind, exc)
        return HttpResponse(status=404)
    response = HttpResponse(data, content_type=content_type)
    response["Cache-Control"] = "private, max-age=300"
    response["X-Content-Type-Options"] = "nosniff"
    return response


@require_POST
def khach_luu(request):
    """UPSERT thật qua proc PMV; ảnh chỉ gửi sau khi dữ liệu khách đã COMMIT."""
    data, errors, warnings = C.clean_form(request.POST)
    token = (request.POST.get("save_token") or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{20,80}", token):
        errors.append("Phiên lưu không hợp lệ; hãy đóng và mở lại biểu mẫu")
    try:
        images, image_info = C.prepare_images(request.FILES)
    except C.CustomerSaveError as exc:
        images, image_info = {}, []
        errors.append(str(exc))
    if errors:
        return render(request, "pos/_khach_luu_kq.html", {"loi": errors})

    fp = hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode("utf-8"))
    for key in sorted(images):
        value = images[key]
        if isinstance(value, bytes):
            fp.update(key.encode("ascii")); fp.update(value)
    fingerprint = fp.hexdigest()

    try:
        with C.SAVE_LOCK:
            result = C.previous_save(token, fingerprint)
            replay = result is not None
            if result is None:
                result = C.upsert(data, images, shop_id=_phien(request)["shop_id"])
                result["warnings"] = list(dict.fromkeys(warnings + result["warnings"]))
                result["images"] = image_info
                if result.get("complete", True):
                    C.remember_save(token, fingerprint, result)
                else:
                    return render(request, "pos/_khach_luu_kq.html", {
                        "loi": result.get("errors") or ["Chưa hoàn tất lưu khách hàng"],
                        "da_luu_thong_tin": True, "saved_cust_id": result["cust_id"],
                        "saved_cust_code": result["cust_code"],
                    })
    except C.CustomerSaveError as exc:
        return render(request, "pos/_khach_luu_kq.html", {"loi": str(exc).splitlines()})
    except Exception as exc:
        logger.exception("Không lưu được khách hàng qua PMV", exc_info=exc)
        return render(request, "pos/_khach_luu_kq.html", {
            "loi": ["PMV/SQL: " + C.error_message(exc)]})

    message = (("Đã thêm" if result["created"] else "Đã cập nhật") +
               f" {result['cust_code']} — {result['name']}")
    if replay:
        message = f"Yêu cầu này đã hoàn tất trước đó: {result['cust_code']} — {result['name']}"
    payload = {"khachSaved": {"message": message, "warnings": result["warnings"],
                               "custId": result["cust_id"]}}
    response = HttpResponse(status=204)
    # Header HTTP phải ASCII; \uXXXX vẫn được JSON.parse phía HTMX trả lại đúng tiếng Việt.
    response["HX-Trigger"] = json.dumps(payload, ensure_ascii=True)
    return response


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

def _ngay_hd(value, mac_dinh):
    try:
        return datetime.date.fromisoformat(value or mac_dinh).isoformat()
    except ValueError:
        return mac_dinh


def hoa_don(request):
    hom_nay = datetime.date.today().isoformat()
    d1, d2 = _ngay_hd(request.GET.get("d1"), hom_nay), _ngay_hd(request.GET.get("d2"), hom_nay)
    if d1 > d2:
        d1, d2 = d2, d1
    loc = {"loai": (request.GET.get("loai") or "").strip(),
           "emp_id": (request.GET.get("emp_id") or "").strip(),
           "khach": (request.GET.get("khach") or "").strip(),
           "trang_thai": (request.GET.get("trang_thai") or "").strip()}
    try:
        rows, la_hom_nay = S.hoa_don_loc(d1, d2, **loc)
        nvs = S.nhan_vien_hoa_don(la_hom_nay)
        loi = ""
    except Exception as exc:
        logger.exception("Không đọc được danh sách hóa đơn")
        rows, nvs, la_hom_nay, loi = [], [], False, S.error_message(exc)
    return render(request, "pos/hoa_don.html", {
        "nav_active": "hoadon", "d1": d1, "d2": d2, "loc": loc, "nvs": nvs, "rows": rows,
        "tong": S.tong_ngay(rows), "la_hom_nay": la_hom_nay, "nguon_kk": la_hom_nay, "loi_hd": loi,
    })


def _doc_hd_kk(trn_id, loai):
    """Đọc lại bản ghi KK cho hành động hủy; không tin mã/trạng thái gửi từ trình duyệt."""
    if loai == "THAU":
        bang = "TRN_RT_BUYGOLD"
    else:
        bang = "TRN_RT_BUYSELL"
    c = PmvClient("kk", tag="hd_xac_nhan")
    rows = c.query(f"SELECT TrnID, BillCode, TrnDate, TrnTime, Status, IsDel FROM {bang} WITH (NOLOCK) WHERE TrnID=?", (trn_id,))
    return c, (rows[0] if rows else None), bang


def _la_hd_hom_nay(hd):
    """ODBC thường trả datetime, nhưng so theo chuỗi ISO để không phụ thuộc driver."""
    return str(hd.get("TrnDate") or "")[:10] == datetime.date.today().isoformat()


def hoa_don_xac_nhan(request):
    trn_id, loai = (request.GET.get("trn_id") or "").strip(), (request.GET.get("loai") or "").strip()
    action = (request.GET.get("action") or "").strip()
    if action not in ("thanh_toan", "hoa_don") or loai not in ("BAN", "BAN_DOI", "THAU") or not trn_id:
        return HttpResponse("Yêu cầu xác nhận không hợp lệ.", status=400)
    try:
        _, hd, _ = _doc_hd_kk(trn_id, loai)
    except Exception as exc:
        return HttpResponse("Không thể kiểm tra dữ liệu KK: " + S.error_message(exc), status=503)
    hom_nay = datetime.date.today()
    if not hd or str(hd.get("IsDel")) != "0" or not _la_hd_hom_nay(hd):
        return HttpResponse("Phiếu không còn hiệu lực để hủy trong ngày hôm nay.", status=409)
    if action == "thanh_toan" and hd.get("Status") != B.CHOT_ROI:
        return HttpResponse("Chỉ hủy thanh toán khi phiếu đã hoàn tất.", status=409)
    if action == "hoa_don" and hd.get("Status") != B.NHAP:
        return HttpResponse("Hãy hủy thanh toán trước khi hủy hóa đơn.", status=409)
    return render(request, "pos/_hoa_don_xac_nhan.html", {"trn_id": trn_id, "loai": loai,
        "action": action, "bill_code": hd.get("BillCode") or trn_id,
        "action_name": "Hủy thanh toán" if action == "thanh_toan" else "Hủy hóa đơn"})


@require_GET
def hoa_don_chi_tiet(request):
    """Popup xem nhanh; nguồn luôn bám theo bảng người dùng đang xem."""
    trn_id, loai = (request.GET.get("trn_id") or "").strip(), (request.GET.get("loai") or "").strip()
    nguon = "kk" if request.GET.get("nguon") == "kk" else "hist"
    if not trn_id or loai not in ("BAN", "BAN_DOI", "THAU"):
        return HttpResponse("Phiếu không hợp lệ.", status=400)
    bang = "TRN_RT_BUYGOLD" if loai == "THAU" else "TRN_RT_BUYSELL"
    try:
        c = PmvClient(nguon, tag="hd_xem")
        extra = "t.TotalAmount AS TienMua, t.TotalAmount AS SoTien, 0 AS TienBan, 0 AS TienVangThem, 0 AS TienCongThem, 0 AS TienBot, 0 AS TienCoc, t.GoldCode, t.GoldWeight, t.WeightUnit, t.BuyRate" if loai == "THAU" else "b.SellTotalAmount AS TienBan, b.BuyTotalAmount AS TienMua, b.PayAmount AS SoTien, ISNULL(b.AddMoney,0) AS TienVangThem, ISNULL(b.TaskPriceAdd,0) AS TienCongThem, ISNULL(b.Discount,0) AS TienBot, ISNULL(b.TienCoc,0) AS TienCoc"
        alias = "t" if loai == "THAU" else "b"
        row = c.query(f"SELECT {alias}.TrnID, {alias}.BillCode, {alias}.TrnDate, {alias}.TrnTime, {alias}.Status, {alias}.IsDel, {extra}, ISNULL(k.CustName,'') AS CustName, ISNULL(k.Phone,'') AS Phone, ISNULL(k.Address,'') AS Address, ISNULL(e.EmpName,'') AS EmpName FROM {bang} {alias} WITH (NOLOCK) LEFT JOIN I_CUSTOMER k WITH (NOLOCK) ON k.CustID={alias}.CustID LEFT JOIN T_EMPLOYEE e WITH (NOLOCK) ON e.EmpID={alias}.EmpID WHERE {alias}.TrnID=?", (trn_id,))
    except Exception as exc:
        return HttpResponse("Không thể đọc thông tin phiếu: " + S.error_message(exc), status=503)
    if not row:
        return HttpResponse("Không tìm thấy phiếu.", status=404)
    r = row[0]
    if loai == "THAU":
        lines = [{"ProductCode": "—", "ProductDesc": "Vàng khách bán", "GoldCode": r.get("GoldCode") or "",
                  "GoldLabel": M.tuoi(r.get("GoldCode") or ""),
                  "tl_text": M.weight_bill(r.get("GoldWeight"), r.get("WeightUnit") or "L"),
                  "rate": r.get("BuyRate"), "task": 0, "amount": r.get("TienMua")}]
        old_lines = [{"ProductCode": "—", "ProductDesc": "Vàng khách bán", "GoldLabel": M.tuoi(r.get("GoldCode") or ""),
                      "tl_vang": M._vn(r.get("GoldWeight") or 0, 2), "tl_hot": "0", "rate": r.get("BuyRate"),
                      "task": 0, "amount": r.get("TienMua")}]
    else:
        raw_lines = c.query(
            "SELECT s.ProductCode, s.ProductDesc, s.GoldCode, s.GoldReal, s.PriceUnit, s.SellRate, "
            "s.TaskPrice, s.SellAmount FROM TRN_RT_BUYSELL_SELL s WITH (NOLOCK) WHERE s.TrnID=? ORDER BY s.OrderBy, s.ProductCode", (trn_id,))
        lines = [{"ProductCode": x.get("ProductCode") or "", "ProductDesc": x.get("ProductDesc") or "",
                  "GoldCode": x.get("GoldCode") or "", "GoldLabel": M.tuoi(x.get("GoldCode") or ""),
                  "tl_text": M.weight_bill(x.get("GoldReal"), x.get("PriceUnit") or "L"),
                  "rate": x.get("SellRate"), "task": x.get("TaskPrice"), "amount": x.get("SellAmount")} for x in raw_lines]
        old_raw = c.query(
            "SELECT GoldCode, GoldWeight, DiamondWeight, TotalGoldWeight, BuyRate, BuyAmount, ProductCode, GhiChu "
            "FROM TRN_RT_BUYSELL_BUYGOLD WITH (NOLOCK) WHERE TrnID=?", (trn_id,))
        old_lines = [{"ProductCode": x.get("ProductCode") or "—", "ProductDesc": x.get("GhiChu") or "Vàng khách bán",
                      "GoldLabel": M.tuoi(x.get("GoldCode") or ""), "tl_vang": M._vn(x.get("GoldWeight") or 0, 2),
                      "tl_hot": M._vn(x.get("DiamondWeight") or 0, 2), "rate": x.get("BuyRate"),
                      "task": 0, "amount": x.get("BuyAmount")} for x in old_raw]
    bill_code = r.get("BillCode") or r.get("TrnID") or trn_id
    return render(request, "pos/_hoa_don_chi_tiet.html", {
        "r": r, "loai": loai, "nguon": nguon, "lines": lines, "old_lines": old_lines,
        "tong_mon": len(lines), "tien_chu": _tien_bang_chu(r.get("SoTien")), "codebar": _codebar(bill_code),
    })


@require_POST
def hoa_don_huy(request):
    trn_id, loai = (request.POST.get("trn_id") or "").strip(), (request.POST.get("loai") or "").strip()
    action = (request.POST.get("action") or "").strip()
    passcode = request.POST.get("passcode") or ""
    if not request.user.check_password(passcode):
        return render(request, "pos/_hoa_don_xac_nhan.html", {"trn_id": trn_id, "loai": loai,
            "action": action, "loi": "Mật mã không đúng. Hành động chưa được thực hiện."}, status=403)
    if action not in ("thanh_toan", "hoa_don") or loai not in ("BAN", "BAN_DOI", "THAU") or not trn_id:
        return HttpResponse("Yêu cầu hủy không hợp lệ.", status=400)
    try:
        c, hd, _ = _doc_hd_kk(trn_id, loai)
        if not hd or str(hd.get("IsDel")) != "0" or not _la_hd_hom_nay(hd):
            raise ValueError("Chỉ cho phép hủy phiếu của hôm nay còn hiệu lực.")
        if action == "thanh_toan":
            if hd.get("Status") != B.CHOT_ROI:
                raise ValueError("Phiếu chưa ở trạng thái hoàn tất.")
            (B.mo_lai_thau if loai == "THAU" else B.mo_lai)(trn_id, user_id=_phien(request)["user_id"], c=c)
        else:
            if hd.get("Status") != B.NHAP:
                raise ValueError("Hãy hủy thanh toán trước khi hủy hóa đơn.")
            (B.huy_thau if loai == "THAU" else B.huy)(trn_id, user_id=_phien(request)["user_id"], c=c)
    except (ValueError, PmvProcError) as exc:
        return render(request, "pos/_hoa_don_xac_nhan.html", {"trn_id": trn_id, "loai": loai,
            "action": action, "loi": S.error_message(exc)}, status=409)
    except Exception as exc:
        logger.exception("Hủy hóa đơn thất bại: %s", trn_id)
        return render(request, "pos/_hoa_don_xac_nhan.html", {"trn_id": trn_id, "loai": loai,
            "action": action, "loi": S.error_message(exc)}, status=503)
    response = HttpResponse(status=204)
    response["HX-Trigger"] = json.dumps({"khblConfirmDone": {"message": "Đã " + ("hủy thanh toán" if action == "thanh_toan" else "hủy hóa đơn") + " " + (hd.get("BillCode") or trn_id)}}, ensure_ascii=True)
    return response
