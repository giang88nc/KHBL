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
from django.http import HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_GET, require_POST

from apps.pmv import money as M
from apps.pmv.client import PmvClient, PmvProcError
from apps.pmv.models import pmv_user_for_web_user

from . import bill as B, cart, cccd, customer as C, passcode as PC, services as S, vietqr as QR


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
        "nav_active": "tong", "ngay": ngay, "tq": tq, "loi_kk": loi,
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
    """PNG Code 39 chuẩn, có start/stop và quiet-zone cho máy quét mã vạch.

    Không dùng các ``div`` CSS để browser không co vạch lẻ thành pixel mờ khi in.
    """
    import base64
    from io import BytesIO
    from PIL import Image, ImageDraw

    value = re.sub(r"[^0-9]", "", str(code or "")) or "0"
    chars = "*" + value + "*"
    narrow, wide, quiet, height = 4, 12, 40, 96
    widths = []
    for pos, char in enumerate(chars):
        widths.extend(wide if unit == "w" else narrow for unit in _CODE39[char])
        if pos < len(chars) - 1:
            widths.append(narrow)  # khoảng cách chuẩn giữa hai ký tự Code 39
    image = Image.new("1", (quiet * 2 + sum(widths), height), 1)
    draw, cursor = ImageDraw.Draw(image), quiet
    for pos, char in enumerate(chars):
        for index, unit in enumerate(_CODE39[char]):
            width = wide if unit == "w" else narrow
            if index % 2 == 0:
                draw.rectangle((cursor, 0, cursor + width - 1, height - 1), fill=0)
            cursor += width
        if pos < len(chars) - 1:
            cursor += narrow
    buffer = BytesIO()
    image.save(buffer, format="PNG", optimize=False)
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


def _ma_gdb(code):
    """Mã 9 số dùng cho barcode/QR của Giấy đảm bảo.

    Ví dụ: 26-09-07-000006 -> 260907006.
    """
    digits = re.sub(r"\D", "", str(code or ""))
    return f"{digits[:6]}{digits[-3:]}" if len(digits) >= 9 else digits


def _qr_hoa_don(code):
    """QR 9 số của hóa đơn trên phần tiệm giữ của GĐB."""
    import base64
    from io import BytesIO
    import segno

    buf = BytesIO()
    segno.make(str(code), error="m").save(buf, kind="png", scale=4, border=1)
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


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
    pu = pmv_user_for_web_user(request.user)
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
        "doi_ngang_ui": True,   # checkbox ⇄ Đổi ngang mặc định TICK; TÍNH LẠI trả lại trạng thái người bán chọn
    }
    # Trạng thái nút chân trang (GĐ chốt 08/09/2026): trang trắng → tất cả tắt · nháp có mã → XÓA/TT/TT&IN ·
    # chốt HÔM NAY → XÓA(hủy HĐ)/SỬA/IN · chốt NGÀY CŨ → tất cả tắt, chỉ xem (mọi user)
    ctx["co_don"] = bool(g.get("trn_id") or g["ban"] or g["doi"])
    ctx["don_hom_nay"] = (g.get("ngay") or ctx["hom_nay"]) == ctx["hom_nay"]
    ctx["khoa_ngay_cu"] = ctx["phieu_chot"] and not ctx["don_hom_nay"]
    ctx["ten_nv"] = next((e["EmpName"] for e in ctx["nvs"] if e["EmpID"] == g.get("emp")), "")
    ctx["ten_nv_sup"] = next((e["EmpName"] for e in ctx["nvs"] if e["EmpID"] == g.get("emp_sup")), "")
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


KHOA_MSG = "Hóa đơn đang KHÓA 🔒 (đã chốt) — bấm biểu tượng khóa cạnh mã đơn, nhập passcode để mở sửa"


def _dang_khoa(request):
    """Đơn ĐÃ CHỐT = KHÓA (GĐ chốt 07/09/2026): mọi thao tác lên giỏ (thêm/xóa món, dẻ, NV, khách,
    tiền) bị chặn ở server, không chỉ ẩn nút — mở bằng passcode (ban_mo_lai)."""
    return cart.get(request).get("status") == B.CHOT_ROI


def _passcode_cua(user):
    return PC.lay_hash(user)


def _passcode_dung(request, ma):
    """Ưu tiên passcode băm tại auth_user, sau đó .env rồi mật khẩu web."""
    from django.conf import settings as st
    ma = (ma or "").strip()
    if not ma:
        return False
    rieng = _passcode_cua(request.user)
    if rieng:
        return PC.kiem(request.user, ma)
    cau_hinh = (getattr(st, "KHBL_UNLOCK_PASSCODE", "") or "").strip()
    if cau_hinh:
        return secrets.compare_digest(ma, cau_hinh)
    return request.user.is_authenticated and request.user.check_password(ma)


def _passcode_ctx(request, target=None, loi="", ok=""):
    from django.contrib.auth import get_user_model
    target = target or request.user
    return {"target": target, "co_pc": PC.da_dat(target), "loi": loi, "ok": ok,
            "la_admin": request.user.is_superuser, "tu_minh": target.pk == request.user.pk,
            "users": get_user_model().objects.filter(is_active=True).order_by("username")
            if request.user.is_superuser else []}


@require_GET
def passcode_form(request):
    """Popup ĐẶT / ĐỔI passcode mở khóa: user tự đổi của mình; superuser chọn user bất kỳ."""
    from django.contrib.auth import get_user_model
    target = request.user
    uid = request.GET.get("user")
    if uid and request.user.is_superuser:
        target = get_user_model().objects.filter(pk=uid, is_active=True).first() or request.user
    return render(request, "pos/_passcode_modal.html", _passcode_ctx(request, target))


@require_POST
def passcode_save(request):
    """Lưu passcode. Tự đổi của mình → phải nhập passcode HIỆN TẠI (chưa có → mật khẩu web).
    Superuser đặt cho người khác → không cần hiện tại. 4–20 ký tự, nhập 2 lần khớp."""
    from django.contrib.auth import get_user_model
    target = request.user
    uid = (request.POST.get("user") or "").strip()
    if uid and str(request.user.pk) != uid:
        if not request.user.is_superuser:
            return render(request, "pos/_passcode_modal.html",
                          _passcode_ctx(request, loi="Chỉ tài khoản quản trị mới đặt passcode cho người khác."))
        target = get_user_model().objects.filter(pk=uid, is_active=True).first()
        if not target:
            return render(request, "pos/_passcode_modal.html", _passcode_ctx(request, loi="Không thấy tài khoản."))
    moi, moi2 = (request.POST.get("moi") or "").strip(), (request.POST.get("moi2") or "").strip()
    if not (4 <= len(moi) <= 20):
        return render(request, "pos/_passcode_modal.html", _passcode_ctx(request, target, loi="Passcode mới phải 4–20 ký tự."))
    if moi != moi2:
        return render(request, "pos/_passcode_modal.html", _passcode_ctx(request, target, loi="Hai lần nhập passcode mới không khớp."))
    if target.pk == request.user.pk:
        hien = (request.POST.get("hien_tai") or "").strip()
        rieng = _passcode_cua(request.user)
        dung = PC.kiem(request.user, hien) if rieng else request.user.check_password(hien)
        if not dung:
            return render(request, "pos/_passcode_modal.html", _passcode_ctx(
                request, target, loi="Passcode hiện tại không đúng." if rieng else "Mật khẩu web không đúng."))
    PC.dat(target, moi)
    logger.info("Passcode mở khóa: %s đặt cho %s", request.user.username, target.username)
    return render(request, "pos/_passcode_modal.html", _passcode_ctx(
        request, target, ok=f"Đã lưu passcode mở khóa cho {target.username}."))


@require_POST
def ban_quet(request):
    """Quét tem hoặc gõ mã hàng → thêm 1 dòng VÀNG BÁN, ô nhập tự trống để quét tiếp.
    + QUÉT MÃ GĐB (GĐ chốt 08/09/2026 chiều): mã 9 số yymmdd+stt in trên Giấy đảm bảo → MỞ LẠI hóa đơn đó lên
    form (như XEM từ DANH SÁCH, kể cả khi đang xem đơn khác). Không thấy hóa đơn → quét mã hàng như cũ."""
    ma = (request.POST.get("ma") or "").strip()
    if B.ma_gdb_hop_le(ma):
        try:
            hd = B.tim_theo_ma_gdb(ma)
        except Exception as exc:
            return _pos_oob(request, {"scan_err": {"code": "", "style": "do",
                                                   "desc": f"Không tra được mã GĐB {ma}: {S.error_message(exc)}"},
                                      "scan_val": ma})
        if hd:
            return _nap_phieu(request, hd["TrnID"], ghi_chu=f" · quét mã GĐB {ma}")
    if _dang_khoa(request):
        return _loi(request, KHOA_MSG)
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
    if _dang_khoa(request):
        return _loi(request, KHOA_MSG)
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
    if _dang_khoa(request):
        return _loi(request, KHOA_MSG)
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
    pu = de["PriceUnit"]

    # Ô GIÁ theo trạng thái tick (GĐ chốt 07/09/2026): BỎ TICK → ô = GIÁ THÂU VÀO (sửa tay được);
    # TICK → ô = GIÁ BÁN RA cho phần đổi ngang (sửa tay được), phần DƯ luôn giá thâu bảng giá MySQL.
    if not doi_ngang:
        gia_thau = gia if gia > 0 else M.dec(de["BuyRate"])
        if gia_thau <= 0:
            return _loi(request, f"Bảng giá chưa có giá thâu cho {de['GoldDesc']} — nhập tay giá thâu")
        tien, dong = B.dong_doi(ma, de["GoldDesc"], tong_tl, tl_hot, gia_thau, pu)
        cart.them_doi(request, tien, dong)
        return _pos_oob(request)

    # ── ĐỔI NGANG ──
    # a = TL vàng khách − TL vàng bán CÙNG LOẠI (cả hai = tổng − hột; hột không tính tiền).
    # a ≤ 0 → toàn bộ giá BÁN RA. a > 0 → 2 dòng: hạn mức × giá bán ra + a × giá thâu.
    # Không cùng loại / không có hàng bán loại đó → hạn mức 0 → toàn bộ giá THÂU (không chặn, GĐ 05/09).
    g = cart.get(request)
    base = de.get("base") or M.de_base(ma)
    han_muc = max(_han_muc_doi_ngang(g, base), M.D0)
    sell = gia if gia > 0 else M.dec(de.get("SellRate"))
    gia_thau = M.dec(de["BuyRate"])
    if han_muc > 0 and sell <= 0:
        return _loi(request, f"Bảng giá chưa có giá BÁN RA cho {de['GoldDesc']} — không đổi ngang được")
    tl_vang = tong_tl - tl_hot
    phan = M.chia_doi_ngang(tl_vang, tl_hot, han_muc, sell, gia_thau, pu)
    if any(not p["ngang"] and p["w"] > 0 for p in phan) and gia_thau <= 0:
        return _loi(request, f"Phần DƯ của {de['GoldDesc']} cần giá THÂU nhưng bảng giá chưa có — bỏ tick Đổi ngang và nhập tay giá thâu")
    for p in phan:
        tien, dong = B.dong_doi(ma, de["GoldDesc"], p["w"] + p["hot"], p["hot"], p["rate"], pu, doi_ngang=p["ngang"])
        cart.them_doi(request, tien, dong)
    return _pos_oob(request)


@require_POST
def ban_doi_xoa(request):
    if _dang_khoa(request):
        return _loi(request, KHOA_MSG)
    try:
        cart.xoa_doi(request, int(request.POST.get("i", -1)))
    except (TypeError, ValueError):
        pass
    return _pos_oob(request)


@require_POST
def ban_doi_tinh_lai(request):
    """GÔM & TÍNH LẠI vàng đổi theo TỪNG LOẠI VÀNG (GĐ chốt 05/09/2026): mỗi loại tối đa
    2 dòng — 1 NGANG (phần TL vàng trong hạn mức bán ra cùng loại, giá BÁN RA) + 1 THÂU
    (phần dư, giá thâu). LUÔN về ĐƠN GIÁ BẢNG GIÁ MySQL (giá sửa tay bị thay — GĐ chốt 07/09/2026),
    và theo TRẠNG THÁI TICK gửi kèm: tick → chia ngang/thâu · bỏ tick → mỗi loại 1 dòng giá thâu."""
    if _dang_khoa(request):
        return _loi(request, KHOA_MSG)
    g = cart.get(request)
    if not g["doi"]:
        return _pos_oob(request)
    doi_ngang = request.POST.get("doi_ngang") == "1"
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
        hm = _budget(base) if (doi_ngang and sell > 0) else M.D0
        phan = M.chia_doi_ngang(grp["vang"], grp["hot"], hm, sell, buy, pu)
        if base in budget:  # trừ phần đã đổi ngang để dẻ cùng base sau không lấn hạn mức
            budget[base] = max(budget[base] - sum((p["w"] for p in phan if p["ngang"]), M.D0), M.D0)
        for p in phan:
            rows.append(B.dong_doi(code, de["GoldDesc"], p["w"] + p["hot"], p["hot"], p["rate"], pu,
                                   doi_ngang=p["ngang"]))
    cart.dat_doi(request, rows)
    return _pos_oob(request, {"tin": "Đã gộp & tính lại vàng đổi theo từng loại"
                                     + (" (đổi ngang trong hạn mức bán ra)." if doi_ngang else " (toàn bộ giá thâu)."),
                              "doi_ngang_ui": doi_ngang})


@require_POST
def ban_dat(request):
    """Đặt khách · nhân viên · ngày · các khoản tiền · ghi chú."""
    if _dang_khoa(request):
        return _loi(request, KHOA_MSG)
    g = cart.get(request)
    if "cust_id" in request.POST:
        cid = (request.POST.get("cust_id") or "").strip()
        k = S.khach_theo_id(cid) if cid and cid != S.WALK_IN else None
        g["cust"] = {"id": k["CustID"], "code": k["CustCode"], "name": k["CustName"],
                     "phone": k["Phone"] or "", "diem": str(M.dec(k["Diem"]))} if k else None
    if "emp" in request.POST:
        moi = (request.POST.get("emp") or "").strip()
        if moi and moi == (g.get("emp_sup") or ""):
            return _loi(request, "NV bán không được trùng NV hỗ trợ — chọn người khác", {"loi_o": "#o-nv"})
        g["emp"] = moi
    if "emp_sup" in request.POST:                  # NV HỖ TRỢ (08/09/2026) — chỉ lưu gold_bill, không lên KK
        moi = (request.POST.get("emp_sup") or "").strip()
        if moi and moi == (g.get("emp") or ""):
            return _loi(request, "NV hỗ trợ không được trùng NV bán — chọn người khác", {"loi_o": "#o-nvsup"})
        g["emp_sup"] = moi
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
    if _dang_khoa(request):
        return _loi(request, KHOA_MSG)
    g = cart.get(request)
    t = cart.tong(request)
    g["bot"] = str(M.dec(g.get("bot") or 0) + M.bot_le(t["khach_tra"]))
    cart.save(request, g)
    return _pos_oob(request)


@require_POST
def ban_moi(request):
    """ĐƠN MỚI / THÊM MỚI: xóa trắng form — KỂ CẢ nhân viên bán (GĐ chốt 07/09/2026: mỗi đơn
    chọn lại NV để không ghi nhầm doanh số người trước)."""
    cart.clear(request, giu_nv=False)
    return _pos_oob(request, {"tin": "Đã mở phiếu mới."})


def ban_tim_khach(request):
    """Gợi ý khách theo tên / SĐT / CCCD / mã (gõ tay — giữ nguyên). QUÉT QR THẺ CCCD (GĐ chốt 08/09/2026 chiều):
    chuỗi 7 trường ngăn '|' (máy quét hay làm hỏng tiếng Việt: 'Tr0198017601980161ng' / 'Trng Ngc') → chỉ lấy
    12 số CCCD ở trường đầu, lọc khách ĐÚNG số đó; đúng 1 khách → tự chọn (script trong _khach_goiy), 0 → báo
    chưa có để bấm ＋ THÊM."""
    q = request.GET.get("q", "")
    cccd = S.cccd_tu_qr(q)
    if not cccd:
        return render(request, "pos/_khach_goiy.html", {"ds": S.tim_khach(q)})
    ds = [k for k in S.tim_khach(cccd, limit=50) if re.sub(r"\D", "", str(k.get("CMND") or "")) == cccd]
    return render(request, "pos/_khach_goiy.html", {"ds": ds, "qr_cccd": cccd, "auto": len(ds) == 1})


def ban_tim_nv(request):
    """Gợi ý NV. ?muc=sup → chọn vào ô NV HỖ TRỢ (loại người đang là NV bán), mặc định → NV bán."""
    muc = "emp_sup" if request.GET.get("muc") == "sup" else "emp"
    g = cart.get(request)
    loai = g.get("emp") if muc == "emp_sup" else g.get("emp_sup")
    ds = [e for e in S.tim_nhan_vien(request.GET.get("q", "")) if e["EmpID"] != (loai or "")]
    return render(request, "pos/_nv_goiy.html", {"ds": ds, "muc": muc})


def ban_tim_hang(request):
    return render(request, "pos/_tim_hang.html", {
        "ds": S.tim_hang(request.GET.get("q", ""), request.GET.get("sec", ""),
                         request.GET.get("gold", "")),
        "q": request.GET.get("q", "")})


# ─────── hóa đơn đã lưu: danh sách · mở · mở lại · thanh toán · xóa ───────

def ban_ds(request):
    """Popup DANH SÁCH hóa đơn (thiết kế lại 07/09/2026): khoảng ngày d1→d2 (mặc định hôm nay)
    + lọc nhân viên / khách / trạng thái; đầu popup = thống kê khoảng ngày. Lọc lại = hx-get
    chính URL này, thay trọn popup trong #modal-root."""
    hom_nay = datetime.date.today().isoformat()
    d1 = _ngay_hd(request.GET.get("d1") or request.GET.get("ngay"), hom_nay)
    d2 = _ngay_hd(request.GET.get("d2") or request.GET.get("ngay"), hom_nay)
    if d1 > d2:
        d1, d2 = d2, d1
    loc = {"emp_id": (request.GET.get("emp_id") or "").strip(),
           "khach": (request.GET.get("khach") or "").strip(),
           "trang_thai": (request.GET.get("trang_thai") or "").strip().upper()}
    from . import gold_bill as GB
    try:
        ds, tk = B.danh_sach(d1, d2, **loc)
        loi = ""
        if d2 == hom_nay:                        # đơn hôm nay: làm tươi gold_bill theo KK khi mở DS (08/09)
            GB.lam_tuoi_ds(ds)
    except Exception as exc:
        ds, tk, loi = [], B.thong_ke_ds([]), str(exc)
    ids = [r["TrnID"] for r in ds]
    ho_tro = GB.map_ho_tro(ids)
    for r in ds:
        r["ho_tro"] = ho_tro.get(r["TrnID"], "")
    tk_ho_tro = GB.thong_ke_ho_tro(ids)
    return render(request, "pos/_ban_ds.html", {
        "ds": ds, "tk": tk, "d1": d1, "d2": d2, "hom_nay": hom_nay, "loc": loc,
        "nvs": S.nhan_vien_ban(), "nhieu_ngay": d1 != d2, "loi": loi, "tk_ho_tro": tk_ho_tro,
        "cham_tran": len(ds) >= B.DS_TRAN, "tran": B.DS_TRAN})


@require_POST
def ban_mo(request):
    """Nạp 1 hóa đơn đã lưu lên form để xem / sửa (XEM từ DANH SÁCH)."""
    return _nap_phieu(request, (request.POST.get("trn_id") or "").strip())


def _nap_phieu(request, trn, ghi_chu=""):
    """Nạp hóa đơn `trn` lên form — dùng chung: XEM từ DANH SÁCH (ban_mo) + QUÉT MÃ GĐB ở ô quét (ban_quet)."""
    try:
        phieu = B.doc(trn)
    except Exception as exc:
        return _loi(request, f"Không mở được hóa đơn: {exc}")
    if not phieu:
        return _loi(request, f"Không thấy hóa đơn {trn}")
    g = cart.nap(request, phieu)
    # gold_bill (08/09/2026): nạp lại NV HỖ TRỢ + cách thanh toán (KK không giữ) và làm tươi dòng theo KK
    from . import gold_bill as GB
    from .models import GoldBill
    gb = GoldBill.objects.filter(trn_id=trn).first()
    if gb:
        g["emp_sup"], g["pay_method"], g["bank_id"] = gb.emp_sup_id, gb.pay_method, gb.bank_id
        if gb.pay_method != "cash" or gb.tien_mat:
            g["tien_mat"] = str(gb.tien_mat)
        cart.save(request, g)
    GB.lam_tuoi_tu_kk(trn, phieu=phieu)
    g = cart.get(request)
    g["_fp"], g["_fp_kk"] = __import__("apps.pos.don", fromlist=["van_tay"]).van_tay(g), \
        __import__("apps.pos.don", fromlist=["van_tay_kk"]).van_tay_kk(g)
    cart.save(request, g)
    tin = f"Đã mở {phieu['bill_code'] or trn} · {phieu['ten_trang_thai']}{ghi_chu}"
    if not phieu["sua_duoc"]:
        tin += " — muốn sửa phải bấm MỞ LẠI."
    return _pos_oob(request, {"tin": tin})


# ─────── đơn ĐÃ CHỐT: 3 hành động cần PASSCODE (GĐ chốt 07/09/2026 theo sơ đồ trạng thái) ───────
# sua    = Sửa đơn: hoàn két → nháp, Ở LẠI form để sửa rồi thanh toán lại
# huy_tt = Hủy thanh toán: hoàn két → nháp, đơn nằm DS CHỜ, form về trắng
# huy_hd = Hủy hóa đơn: hoàn két + xóa đơn (1 bước, 1 passcode), hàng về kho, form về trắng
HANH_DONG = {
    "sua":    {"ten": "SỬA ĐƠN", "icon": "🔓", "audit": "SUA",
               "hau_qua": ["Sổ quỹ (két) của hóa đơn bị HOÀN, hóa đơn về trạng thái NHÁP.",
                           "Đơn ở lại màn hình để sửa món, dẻ, khách, nhân viên, tiền — xong phải THANH TOÁN lại.",
                           "Giấy đảm bảo đã in của hóa đơn này KHÔNG CÒN HIỆU LỰC — in lại sau khi thanh toán."]},
    "huy_tt": {"ten": "HỦY THANH TOÁN", "icon": "↩", "audit": "HUY_TT",
               "hau_qua": ["Sổ quỹ (két) của hóa đơn bị HOÀN, hóa đơn về trạng thái NHÁP.",
                           "Đơn nằm ở DANH SÁCH CHỜ (đơn treo) — món hàng vẫn bị giữ cho đến khi thanh toán lại hoặc xóa.",
                           "Màn hình về phiếu trắng. Giấy đảm bảo đã in KHÔNG CÒN HIỆU LỰC."]},
    "huy_hd": {"ten": "HỦY HÓA ĐƠN", "icon": "🗑", "audit": "HUY_HD", "nguy": True,
               "hau_qua": ["Sổ quỹ (két) bị HOÀN, rồi hóa đơn bị XÓA khỏi phần mềm (chỉ còn trong nhật ký).",
                           "Toàn bộ món hàng + dẻ trả về kho. KHÔNG khôi phục được.",
                           "Giấy đảm bảo đã in KHÔNG CÒN HIỆU LỰC."]},
    # 08/09/2026 chiều (GĐ chốt): XÓA đơn NHÁP / ĐANG SỬA cũng đi qua popup XÁC NHẬN chung (thay JS confirm),
    # KHÔNG passcode (`nhap`: True) — OK → ban_huy như cũ (xóa đơn W trên KK, hàng về kho / dọn phiếu chưa lưu)
    "xoa_nhap": {"ten": "XÓA ĐƠN NHÁP", "icon": "🗑", "audit": "HUY_HD", "nguy": True, "nhap": True,
                 "hau_qua": ["Đơn nháp bị XÓA khỏi phần mềm (chỉ còn trong nhật ký) — KHÔNG khôi phục được.",
                             "Toàn bộ món hàng + dẻ đang giữ trả về kho.",
                             "Màn hình về phiếu trắng."]},
}


def _audit(request, g, action, note="", version=0):
    """Ghi 1 dòng nhật ký append-only kèm ẢNH CHỤP đơn hiện tại (trước hành động)."""
    from .models import BillAudit
    try:
        snap = {k: g.get(k) for k in ("trn_id", "bill_code", "status", "ngay", "emp", "cust", "ban", "doi",
                                      "bot", "cong_them", "vang_them", "coc", "ghi_chu", "pay_method",
                                      "tien_mat", "bank_id", "upd")}
        BillAudit.objects.create(trn_id=g.get("trn_id") or "", bill_code=g.get("bill_code") or "",
                                 action=action, user=request.user if request.user.is_authenticated else None,
                                 username=getattr(request.user, "username", ""), before=snap,
                                 note=note[:300], version=version)
    except Exception:
        logger.exception("Không ghi được bill_audit %s %s", action, g.get("trn_id"))


def _xac_nhan_ctx(request, hanh_dong, loi=""):
    g = cart.get(request)
    hd = HANH_DONG.get(hanh_dong)
    # khoa = popup có việc để làm: hành động đơn chốt cần đơn ĐÃ CHỐT; xoa_nhap cần phiếu NHÁP (đã lưu W hoặc đang nhập)
    if hd and hd.get("nhap"):
        hop_le = g.get("status") != B.CHOT_ROI and bool(g.get("trn_id") or g["ban"] or g["doi"])
    else:
        hop_le = g.get("status") == B.CHOT_ROI and bool(hd)
    return {"g": g, "hd": hd, "hanh_dong": hanh_dong, "loi": loi, "khoa": hop_le,
            "nguoi": request.user.first_name or request.user.username, "username": request.user.username,
            "luc": datetime.datetime.now()}


@require_GET
def ban_xac_nhan(request, hanh_dong):
    """Popup XÁC NHẬN CHUNG cho đơn đã chốt: hành động · mã hóa đơn · người thao tác · hậu quả · passcode."""
    if hanh_dong not in HANH_DONG:
        return HttpResponse("Hành động không hợp lệ", status=400)
    return render(request, "pos/_xac_nhan_modal.html", _xac_nhan_ctx(request, hanh_dong))


@require_POST
def ban_thuc_hien(request, hanh_dong):
    """Thực hiện hành động trên đơn ĐÃ CHỐT sau khi passcode đúng + mốc khóa còn khớp.
    Mọi thứ ghi audit TRƯỚC khi gọi PMV; passcode sai / mốc lệch → không đụng gì trên KK."""
    hd = HANH_DONG.get(hanh_dong)
    if not hd:
        return HttpResponse("Hành động không hợp lệ", status=400)
    g = cart.get(request)
    ph = _phien(request)
    trn = g.get("trn_id")
    if hd.get("nhap"):                                     # XÓA nháp: không passcode, không mốc két
        if g.get("status") == B.CHOT_ROI:
            return _loi(request, "Hóa đơn đã CHỐT — dùng 🗑 XÓA (hủy hóa đơn, passcode).", {"dong_modal": True})
        return _huy_nhap(request, {"dong_modal": True})
    if not trn:
        return _loi(request, "Chưa mở hóa đơn nào", {"dong_modal": True})
    if g.get("status") != B.CHOT_ROI:
        return _loi(request, "Hóa đơn đang là NHÁP — không cần thao tác này.", {"dong_modal": True})
    if (g.get("ngay") or "") != datetime.date.today().isoformat():
        # GĐ chốt 08/09/2026: hóa đơn NGÀY CŨ chỉ XEM — không sửa/hủy từ màn bán, với MỌI user
        return render(request, "pos/_xac_nhan_modal.html", _xac_nhan_ctx(
            request, hanh_dong, loi="Hóa đơn của NGÀY KHÁC chỉ được xem — không sửa/hủy từ màn bán hàng."))
    if not _passcode_dung(request, request.POST.get("passcode")):
        return render(request, "pos/_xac_nhan_modal.html",
                      _xac_nhan_ctx(request, hanh_dong, loi="Passcode không đúng — thử lại."))
    try:
        B.kiem_moc(trn, g.get("upd"))                       # chống 2 người cùng sửa
    except Exception as exc:
        return render(request, "pos/_xac_nhan_modal.html", _xac_nhan_ctx(request, hanh_dong, loi=_loi_goi(exc)))
    ma = g.get("bill_code") or trn
    _audit(request, g, hd["audit"])
    try:
        B.mo_lai(trn, user_id=ph["user_id"])
        if hanh_dong == "huy_hd":
            B.huy(trn, user_id=ph["user_id"])
    except Exception as exc:
        return _loi(request, f"{hd['ten']} không được: {_loi_goi(exc)}", {"dong_modal": True})
    from . import gold_bill as GB
    emp_sup, pm, bank = g.get("emp_sup") or "", g.get("pay_method") or "cash", g.get("bank_id") or ""
    if hanh_dong == "sua":
        g2 = cart.nap(request, B.doc(trn))
        g2["emp_sup"], g2["pay_method"], g2["bank_id"] = emp_sup, pm, bank   # phần app-only giữ qua mở lại
        cart.save(request, g2)
        GB.upsert_tu_gio(g2, status=B.NHAP, is_del=False, user=request.user)
        tin = f"Đã mở {ma} về NHÁP để sửa — sửa xong bấm THANH TOÁN lại."
    elif hanh_dong == "huy_tt":
        GB.upsert_tu_gio(g, status=B.NHAP, is_del=False, user=request.user)
        cart.clear(request, giu_nv=False)
        tin = f"Đã HỦY THANH TOÁN {ma} — đơn về DANH SÁCH CHỜ."
    else:
        GB.upsert_tu_gio(g, status=g.get("status"), is_del=True, user=request.user)
        cart.clear(request, giu_nv=False)
        tin = f"Đã HỦY HÓA ĐƠN {ma} — hàng trả về kho."
    return _pos_oob(request, {"tin": tin, "dong_modal": True})


@require_GET
def ban_mo_khoa(request):
    """URL cũ (07/09 sáng) → popup xác nhận SỬA ĐƠN."""
    return ban_xac_nhan(request, "sua")


@require_POST
def ban_mo_lai(request):
    """URL cũ (07/09 sáng) → thực hiện SỬA ĐƠN (passcode)."""
    return ban_thuc_hien(request, "sua")


def _kiem_truoc_khi_luu(request, g, ph):
    """Trả (thông điệp, selector ô đang thiếu) — selector để JS FOCUS + rung ô đó (GĐ 08/09/2026)."""
    if not g["ban"] and not g["doi"]:
        return "Phiếu chưa có món nào — quét tem hoặc gõ mã hàng", ".khbl-scan__in"
    if not ph["user_id"]:
        return "Tài khoản web chưa gắn với tài khoản PMV — vào trang Hệ thống đồng bộ lại", ""
    if not ph["till_id"]:
        return "Tài khoản chưa gắn KÉT — không ghi sổ quỹ được", ""
    if not g.get("emp"):
        return "Chưa chọn nhân viên bán", "#o-nv"
    if g.get("emp_sup") and g["emp_sup"] == g["emp"]:
        return "NV hỗ trợ trùng NV bán — bỏ hoặc chọn người khác", "#o-nvsup"
    return "", ""


@require_POST
def ban_thanh_toan(request):
    """Lưu phiếu rồi CHỐT. GĐ chốt 03/09/2026: hóa đơn mới luôn mang ngày HÔM NAY."""
    g = cart.get(request)
    ph = _phien(request)
    loi, o_loi = _kiem_truoc_khi_luu(request, g, ph)
    if loi:
        return _loi(request, loi, {"loi_o": o_loi})
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
    g["trn_id"], g["bill_code"] = kq["trn_id"], kq["bill_code"]
    _audit(request, g, "CHOT", note=f"khách trả {M.money_vn(kq['tong']['khach_tra'])}")
    # GĐ chốt 08/09/2026 (đổi so với 07/09): THANH TOÁN xong GIỮ ĐƠN VỪA CHỐT trên form ở chế độ xem
    # (chỉ IN bật) — ＋ ĐƠN MỚI để bán khách kế.
    emp_sup, pm, bank, tm = g.get("emp_sup") or "", g.get("pay_method") or "cash", g.get("bank_id") or "", g.get("tien_mat") or ""
    g2 = cart.nap(request, B.doc(kq["trn_id"], c))
    g2["emp_sup"], g2["pay_method"], g2["bank_id"], g2["tien_mat"] = emp_sup, pm, bank, tm   # app-only giữ qua nap
    cart.save(request, g2)
    from . import gold_bill as GB
    GB.upsert_tu_gio(g2, status=B.CHOT_ROI, is_del=False, user=request.user)       # ghi xuyên gold_bill
    tin = f"Đã thanh toán {kq['bill_code']} — {M.money_vn(kq['tong']['khach_tra'])}"
    extra = {"tin": tin, "vua_chot": kq["trn_id"]}
    if request.POST.get("in") == "1":
        # THANH TOÁN & IN (GĐ chốt 08/09/2026 chiều — "chưa in ra giấy được"): CHỐT → IN THẲNG → FORM TRẮNG (như ĐƠN MỚI).
        # Tờ GĐB dựng ngay ở đây (in_html) nhét vào #pos-in qua OOB, JS in từ CHÍNH cửa sổ bán hàng — không popup,
        # không iframe (print() trong iframe ẩn bị Edge 152 bỏ qua dù iframe đã nạp + in/dem đã đếm — log Caddy máy quầy).
        from django.template.loader import render_to_string
        ctx_in, _err = _gdb_ctx(kq["trn_id"], "BAN_DOI" if g["doi"] else "BAN", "live")
        if ctx_in:
            extra["in_html"] = render_to_string("pos/_gdb_a5.html", ctx_in, request=request)
            extra["tin"] = f"{tin} · đang in Giấy đảm bảo — form đã trắng cho khách kế"
        else:
            extra["loi_phieu"] = f"{tin} — nhưng KHÔNG dựng được tờ in; mở lại từ DANH SÁCH rồi bấm IN HÓA ĐƠN."
        cart.clear(request, giu_nv=False)
    return _pos_oob(request, extra)


@require_POST
def ban_in_dem(request):
    """Nút 🖨 IN trong popup Giấy đảm bảo: ĐẾM LẦN IN vào bill_audit (action=IN, version=lần thứ mấy)
    rồi trả toast; popup tự gọi window.print() sau đó. Chỉ đơn ĐÃ CHỐT (GĐ 08/09: nháp không in)."""
    from .models import BillAudit
    g = cart.get(request)
    trn = (request.POST.get("trn_id") or g.get("trn_id") or "").strip()
    if not trn:
        return _loi(request, "Chưa có hóa đơn để in")
    if trn == g.get("trn_id") and g.get("status") != B.CHOT_ROI:
        return _loi(request, "Hóa đơn đang SỬA / chưa thanh toán — THANH TOÁN xong mới in được Giấy đảm bảo.")
    lan = BillAudit.objects.filter(trn_id=trn, action="IN").count() + 1
    snap = g if trn == g.get("trn_id") else {"trn_id": trn}
    _audit(request, snap, "IN", version=lan)
    from .models import GoldBill
    GoldBill.objects.filter(trn_id=trn).update(so_lan_in=lan)
    if request.GET.get("im") == "1":          # THANH TOÁN & IN: đếm im lặng, không toast (GĐ 08/09)
        return HttpResponse(status=204)
    return _pos_oob(request, {"tin": f"Đã ghi nhận IN lần {lan} · {datetime.datetime.now():%H:%M} — "
                                     f"{g.get('bill_code') or trn}"})


@require_POST
def ban_in_thang(request):
    """Nút 🖨 IN trong popup Giấy đảm bảo (GĐ chốt 08/09/2026 chiều): chạy ĐÚNG thuật toán THANH TOÁN & IN —
    tờ GĐB nhét vào #pos-in in từ CHÍNH cửa sổ bán (khblInThang tự đếm lần in im=1) → ẨN popup (dong_modal)
    → PHIẾU TRẮNG như ĐƠN MỚI. Chỉ đơn ĐÃ CHỐT HÔM NAY (đơn ngày cũ: chỉ xem — cùng luật chân trang)."""
    g = cart.get(request)
    trn = (request.POST.get("trn_id") or g.get("trn_id") or "").strip()
    loai = request.POST.get("loai") or ("BAN_DOI" if g["doi"] else "BAN")
    if not trn:
        return _loi(request, "Chưa có hóa đơn để in")
    if trn == g.get("trn_id"):
        if g.get("status") != B.CHOT_ROI:
            return _loi(request, "Hóa đơn đang SỬA / chưa thanh toán — THANH TOÁN xong mới in được Giấy đảm bảo.")
        if (g.get("ngay") or datetime.date.today().isoformat()) != datetime.date.today().isoformat():
            return _loi(request, "Hóa đơn ngày khác — chỉ xem, không in lại.", {"dong_modal": True})
    ctx_in, _err = _gdb_ctx(trn, loai if loai in ("BAN", "BAN_DOI") else "BAN", "live")
    if not ctx_in:
        return _loi(request, "Không dựng được tờ in — thử lại hoặc mở lại đơn từ DANH SÁCH.")
    from django.template.loader import render_to_string
    in_html = render_to_string("pos/_gdb_a5.html", ctx_in, request=request)
    ma = (g.get("bill_code") if trn == g.get("trn_id") else "") or ctx_in["r"].get("BillCode") or trn
    cart.clear(request, giu_nv=False)
    return _pos_oob(request, {"tin": f"Đang in Giấy đảm bảo {ma} — đã mở phiếu trắng cho khách kế",
                              "vua_chot": trn, "in_html": in_html, "dong_modal": True})


@require_POST
def ban_huy(request):
    """URL cũ (POST thẳng): XÓA phiếu nháp. Từ 08/09 chiều nút 🗑 XÓA đi qua popup XÁC NHẬN (xoa_nhap)."""
    return _huy_nhap(request)


def _huy_nhap(request, extra=None):
    """XÓA nháp: phiếu chưa lưu thì dọn form; đơn W đã lưu thì HỦY THẬT trên KK (hàng về kho) + gold_bill is_del."""
    g = cart.get(request)
    ph = _phien(request)
    trn = g.get("trn_id")
    extra = dict(extra or {})
    if not trn:
        cart.clear(request)
        return _pos_oob(request, {**extra, "tin": "Đã xóa phiếu đang nhập."})
    try:
        B.huy(trn, user_id=ph["user_id"])
    except Exception as exc:
        return _loi(request, f"Hủy không được: {_loi_goi(exc)}", extra)
    from . import gold_bill as GB
    GB.upsert_tu_gio(g, status=g.get("status") or B.NHAP, is_del=True, user=request.user)
    cart.clear(request)
    return _pos_oob(request, {**extra, "tin": f"Đã hủy hóa đơn {g.get('bill_code') or trn} — hàng trả về kho."})


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
    """GIẤY ĐẢM BẢO — mẫu tạm, chờ GĐ đưa mẫu giấy thật.
    ?trn_id=... → in ĐÚNG hóa đơn đó đọc từ PMV (không đụng phiếu đang lập trên form — dùng cho
    THANH TOÁN & IN vì form đã được xóa trắng); không có → in phiếu đang lập như cũ."""
    trn = (request.GET.get("trn_id") or "").strip()
    ctx = _ctx_pos(request)
    if trn:
        phieu = B.doc(trn)
        if not phieu:
            return HttpResponse(f"Không thấy hóa đơn {trn}", status=404)
        ctx["g"] = cart.tu_phieu(phieu)
        ctx["t"] = cart.tong_cua(ctx["g"])
        ctx["ten_nv"] = phieu.get("nhan_vien") or next(
            (e["EmpName"] for e in ctx["nvs"] if e["EmpID"] == phieu.get("emp_id")), "")
    # GĐ chốt 07/09/2026 (quy tắc 5 + 7): CHỈ đơn ĐÃ CHỐT mới in; nháp/đang sửa → chặn.
    # Mỗi lần in ghi audit IN kèm SỐ LẦN IN; sau Sửa/Hủy đơn chốt, bản in cũ hết hiệu lực (hậu quả trong popup).
    if ctx["g"].get("status") != B.CHOT_ROI:
        return HttpResponse("<h3 style='font-family:sans-serif;padding:24px'>Hóa đơn đang SỬA / chưa thanh toán — "
                            "THANH TOÁN xong mới in được Giấy đảm bảo.</h3>", status=403)
    from .models import BillAudit
    lan = BillAudit.objects.filter(trn_id=ctx["g"]["trn_id"], action="IN").count() + 1
    _audit(request, ctx["g"], "IN", version=lan)
    ctx["ban_in_lan"] = lan
    ctx["ban_in_luc"] = datetime.datetime.now()
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
def gia_sync_kk_xem(request):
    from . import prices
    try:
        preview = prices.kk_sync_preview()
        preview['token'] = prices.kk_sync_token(preview, request.user.pk)
    except prices.PriceError as exc:
        return render(request, "pos/_gia_sync_kk.html", {"loi": str(exc)})
    except Exception:
        logger.exception("Không thể kiểm tra giá KK trước SYNC")
        return render(request, "pos/_gia_sync_kk.html", {"loi": "Không đọc được bảng giá KK. Hãy kiểm tra kết nối rồi thử lại."})
    return render(request, "pos/_gia_sync_kk.html", preview)


@require_GET
def gia_sync_pmv_report_xem(request):
    """Xem chênh lệch nguồn UPSERT chính → MySQL KHBL, chưa ghi dữ liệu."""
    from . import prices
    try:
        preview = prices.pmv_report_sync_preview()
        preview['token'] = prices.pmv_report_sync_token(preview, request.user.pk)
    except prices.PriceError as exc:
        return render(request, "pos/_gia_sync_pmv_report.html", {"loi": str(exc)})
    except Exception:
        logger.exception("Không thể kiểm tra giá PMV Report trước SYNC")
        return render(request, "pos/_gia_sync_pmv_report.html", {"loi": "Không đọc được bảng giá PMV Report. Hãy kiểm tra trang UPSERT chính rồi thử lại."})
    return render(request, "pos/_gia_sync_pmv_report.html", preview)


@require_POST
def gia_sync_pmv_report_ap_dung(request):
    from . import prices
    from django.http import HttpResponse
    try:
        count = prices.apply_pmv_report_sync(request.POST.get("sync_token", ""), request.user)
    except prices.PriceError as exc:
        return render(request, "pos/_gia_sync_pmv_report.html", {"loi": str(exc)}, status=409)
    messages.success(request, "PMV Report → MySQL đã đồng bộ %s loại giá." % count)
    response = HttpResponse(status=204)
    response["HX-Redirect"] = request.build_absolute_uri("/banle/bang-gia/")
    return response


@require_GET
def gia_pmv_report_trang_thai(request):
    from . import prices
    try:
        preview = prices.pmv_report_sync_preview()
    except prices.PriceError as exc:
        return render(request, "pos/_gia_pmv_report_status.html", {"loi": str(exc)})
    except Exception:
        logger.exception("Không thể kiểm tra giá PMV Report")
        return render(request, "pos/_gia_pmv_report_status.html", {"loi": "Không đọc được bảng giá PMV Report."})
    return render(request, "pos/_gia_pmv_report_status.html", preview)


@require_POST
def gia_sync_kk_ap_dung(request):
    from . import prices
    from django.http import HttpResponse
    try:
        count = prices.apply_kk_sync(request.POST.get("sync_token", ""), request.user)
    except prices.PriceError as exc:
        return render(request, "pos/_gia_sync_kk.html", {"loi": str(exc)}, status=409)
    messages.success(request, "KK → MySQL đã đồng bộ %s loại giá." % count)
    response = HttpResponse(status=204)
    response["HX-Redirect"] = request.build_absolute_uri("/banle/bang-gia/")
    return response


@require_GET
def gia_kk_trang_thai(request):
    from . import prices
    try:
        preview = prices.kk_sync_preview()
        return render(request, "pos/_gia_kk_status.html", preview)
    except prices.PriceError as exc:
        return render(request, "pos/_gia_kk_status.html", {"loi": str(exc)}, status=503)
    except Exception:
        logger.exception("Không thể kiểm tra chênh lệch giá KK")
        return render(request, "pos/_gia_kk_status.html", {"loi": "Không đọc được giá KK."}, status=503)


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
        co_fullcontrol = bool(request.user.is_authenticated and request.user.is_superuser)
        # Lọc có thể gồm nhiều ngày: quyền thao tác phải xét TỪNG phiếu, không
        # dựa vào cả khoảng ngày. Nhân viên chỉ được đổi phiếu hôm nay; Admin
        # (superuser được đồng bộ từ user `admin` PMV) có toàn quyền.
        for row in rows:
            row["la_hom_nay"] = _la_hd_hom_nay(row)
            row["co_the_thao_tac"] = co_fullcontrol or row["la_hom_nay"]
        loi = ""
    except Exception as exc:
        logger.exception("Không đọc được danh sách hóa đơn")
        rows, nvs, la_hom_nay, co_fullcontrol, loi = [], [], False, False, S.error_message(exc)
    return render(request, "pos/hoa_don.html", {
        "nav_active": "hoadon", "d1": d1, "d2": d2, "loc": loc, "nvs": nvs, "rows": rows,
        "tong": S.tong_ngay(rows), "la_hom_nay": la_hom_nay, "nguon_kk": la_hom_nay,
        "co_fullcontrol": co_fullcontrol, "loi_hd": loi,
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


def _co_fullcontrol_hoa_don(request):
    """`admin` PMV được đồng bộ thành Django superuser: được thao tác cả đơn cũ.

    Đây chỉ nới cửa sổ ngày. Passcode, trạng thái phiếu, chốt ghi KK và các
    kiểm tra lại trên MSSQL vẫn bắt buộc như bình thường.
    """
    return bool(request.user.is_authenticated and request.user.is_superuser)


def _duoc_thao_tac_hoa_don(request, hd):
    return _co_fullcontrol_hoa_don(request) or _la_hd_hom_nay(hd)


def hoa_don_xac_nhan(request):
    trn_id, loai = (request.GET.get("trn_id") or "").strip(), (request.GET.get("loai") or "").strip()
    action = (request.GET.get("action") or "").strip()
    if action not in ("thanh_toan", "hoa_don") or loai not in ("BAN", "BAN_DOI", "THAU") or not trn_id:
        return HttpResponse("Yêu cầu xác nhận không hợp lệ.", status=400)
    try:
        _, hd, _ = _doc_hd_kk(trn_id, loai)
    except Exception as exc:
        return HttpResponse("Không thể kiểm tra dữ liệu KK: " + S.error_message(exc), status=503)
    if not hd or str(hd.get("IsDel")) != "0":
        return HttpResponse("Phiếu không còn hiệu lực để thao tác.", status=409)
    if not _duoc_thao_tac_hoa_don(request, hd):
        return HttpResponse("Phiếu ngoài ngày hôm nay chỉ được xem. Tài khoản Admin mới có toàn quyền thao tác.", status=409)
    if action == "thanh_toan" and hd.get("Status") != B.CHOT_ROI:
        return HttpResponse("Chỉ hủy thanh toán khi phiếu đã hoàn tất.", status=409)
    if action == "hoa_don" and hd.get("Status") != B.NHAP and not (
        hd.get("Status") == B.CHOT_ROI and _co_fullcontrol_hoa_don(request)
    ):
        return HttpResponse("Hãy hủy thanh toán trước khi hủy hóa đơn.", status=409)
    return render(request, "pos/_hoa_don_xac_nhan.html", {"trn_id": trn_id, "loai": loai,
        "action": action, "bill_code": hd.get("BillCode") or trn_id,
        "action_name": "Hủy thanh toán" if action == "thanh_toan" else "Hủy hóa đơn"})


def _gdb_ctx(trn_id, loai, nguon="live"):
    """Dựng context TỜ GIẤY ĐẢM BẢO cho 1 phiếu (dùng chung: popup xem, in thẳng raw=1, trang chỉnh mẫu).
    nguon: "live" = theo công tắc đích · "kk" · "hist". Trả (ctx, None) hoặc (None, HttpResponse lỗi)."""
    if not trn_id or loai not in ("BAN", "BAN_DOI", "THAU"):
        return None, HttpResponse("Phiếu không hợp lệ.", status=400)
    bang = "TRN_RT_BUYGOLD" if loai == "THAU" else "TRN_RT_BUYSELL"
    try:
        c = PmvClient(tag="hd_xem") if nguon == "live" else PmvClient(nguon, tag="hd_xem")
        extra = "t.TotalAmount AS TienMua, t.TotalAmount AS SoTien, 0 AS TienBan, 0 AS TienVangThem, 0 AS TienCongThem, 0 AS TienBot, 0 AS TienCoc, t.GoldCode, t.GoldWeight, t.WeightUnit, t.BuyRate" if loai == "THAU" else "b.SellTotalAmount AS TienBan, b.BuyTotalAmount AS TienMua, b.PayAmount AS SoTien, ISNULL(b.AddMoney,0) AS TienVangThem, ISNULL(b.TaskPriceAdd,0) AS TienCongThem, ISNULL(b.Discount,0) AS TienBot, ISNULL(b.TienCoc,0) AS TienCoc"
        alias = "t" if loai == "THAU" else "b"
        row = c.query(f"SELECT {alias}.TrnID, {alias}.BillCode, {alias}.TrnDate, {alias}.TrnTime, {alias}.Status, {alias}.IsDel, {extra}, ISNULL(k.CustName,'') AS CustName, ISNULL(k.Phone,'') AS Phone, ISNULL(k.Address,'') AS Address, ISNULL(e.EmpName,'') AS EmpName FROM {bang} {alias} WITH (NOLOCK) LEFT JOIN I_CUSTOMER k WITH (NOLOCK) ON k.CustID={alias}.CustID LEFT JOIN T_EMPLOYEE e WITH (NOLOCK) ON e.EmpID={alias}.EmpID WHERE {alias}.TrnID=?", (trn_id,))
    except Exception as exc:
        return None, HttpResponse("Không thể đọc thông tin phiếu: " + S.error_message(exc), status=503)
    if not row:
        return None, HttpResponse("Không tìm thấy phiếu.", status=404)
    r = row[0]

    def _tl_chi(value, unit="L", co_don_vi=True):
        """GĐB luôn ghi trọng lượng theo chỉ: MSSQL lưu vàng theo ly."""
        weight = M.dec(value)
        chi = weight / M.dec("3.75") if (unit or "L").upper() in ("G", "K") else weight / M.dec("100")
        number = M._vn(chi, 3)
        return f"{number} chỉ" if co_don_vi else number

    if loai == "THAU":
        lines = [{"ProductCode": "—", "ProductDesc": "Vàng khách bán", "GoldCode": r.get("GoldCode") or "",
                  "GoldLabel": M.tuoi(r.get("GoldCode") or ""),
                  "tl_text": _tl_chi(r.get("GoldWeight"), r.get("WeightUnit") or "L"),
                  "rate": r.get("BuyRate"), "task": 0, "amount": r.get("TienMua")}]
        store_lines = [{"loai_dong": "Thâu", "ProductDesc": "Vàng khách", "GoldLabel": M.tuoi(r.get("GoldCode") or ""),
                        "tl_vang": _tl_chi(r.get("GoldWeight"), r.get("WeightUnit") or "L", False), "tl_hot": "0",
                        "rate": r.get("BuyRate"), "task": 0, "amount": r.get("TienMua")}]
    else:
        raw_lines = c.query(
            "SELECT s.ProductCode, s.ProductDesc, s.GoldCode, s.GoldReal, s.DiamondWeight, s.PriceUnit, s.SellRate, "
            "s.TaskPrice, s.SellAmount FROM TRN_RT_BUYSELL_SELL s WITH (NOLOCK) WHERE s.TrnID=? ORDER BY s.OrderBy, s.ProductCode", (trn_id,))
        lines = [{"ProductCode": x.get("ProductCode") or "", "ProductDesc": x.get("ProductDesc") or "",
                  "GoldCode": x.get("GoldCode") or "", "GoldLabel": M.tuoi(x.get("GoldCode") or ""),
                  "tl_text": _tl_chi(x.get("GoldReal"), x.get("PriceUnit") or "L"),
                  "rate": x.get("SellRate"), "task": x.get("TaskPrice"), "amount": x.get("SellAmount")} for x in raw_lines]
        old_raw = c.query(
            "SELECT GoldCode, GoldWeight, DiamondWeight, TotalGoldWeight, BuyRate, BuyAmount, ProductCode, GhiChu "
            "FROM TRN_RT_BUYSELL_BUYGOLD WITH (NOLOCK) WHERE TrnID=?", (trn_id,))

        # Bảng tiệm giữ đọc nguyên trạng các dòng đã chốt từ MSSQL. Không chia
        # trọng lượng hay tính lại tiền; KK không lưu cờ DoiNgang trên dòng đổi.
        store_lines = [{"loai_dong": x.get("ProductCode") or "—", "ProductDesc": x.get("ProductDesc") or "",
                        "GoldLabel": M.tuoi(x.get("GoldCode") or ""),
                        "tl_vang": _tl_chi(x.get("GoldReal"), x.get("PriceUnit") or "L", False),
                        "tl_hot": _tl_chi(x.get("DiamondWeight"), x.get("PriceUnit") or "L", False),
                        "rate": x.get("SellRate"), "task": x.get("TaskPrice"), "amount": x.get("SellAmount")} for x in raw_lines]
        gia_ban_da_luu = {M.dec(x.get("SellRate")) for x in raw_lines if x.get("SellRate") is not None}
        for x in old_raw:
            buy_rate = M.dec(x.get("BuyRate"))
            la_doi = loai == "BAN_DOI" and buy_rate in gia_ban_da_luu
            store_lines.append({
                "loai_dong": "Đổi" if la_doi else "Thâu",
                "ProductDesc": x.get("GhiChu") or "Vàng khách",
                "GoldLabel": M.tuoi(x.get("GoldCode") or ""),
                "tl_vang": _tl_chi(x.get("GoldWeight"), "L", False),
                "tl_hot": _tl_chi(x.get("DiamondWeight"), "L", False),
                "rate": x.get("BuyRate"), "task": 0, "amount": x.get("BuyAmount"),
            })
    store_lines = _sap_store_lines(store_lines)
    bill_code = r.get("BillCode") or r.get("TrnID") or trn_id
    barcode_code = _ma_gdb(bill_code)
    # Chỉ phiếu có vàng khách (đổi/thâu) mới hiện trạng thái đổi bù/dư.
    # Phiếu bán thuần không có dòng này thì để trống nhãn trạng thái.
    co_vang_doi_thau = any(x.get("loai_dong") in ("Đổi", "Thâu") for x in store_lines)
    so_tien = M.dec(r.get("SoTien"))
    gdb_status = ("Đổi bù" if so_tien > 0 else "Đổi dư" if so_tien < 0 else "") if co_vang_doi_thau else ""
    return {
        "r": r, "loai": loai, "nguon": nguon, "lines": lines, "store_lines": store_lines,
        "tong_mon": len(lines), "tien_chu": _tien_bang_chu(r.get("SoTien")), "barcode_code": barcode_code,
        "codebar": _codebar(barcode_code), "gdb_qr": _qr_hoa_don(barcode_code),
        "co_vang_doi_thau": co_vang_doi_thau,
        "gdb_status": gdb_status,
        # NV HỖ TRỢ (gold_bill, 08/09/2026) in ở chân phần tiệm giữ: "Bán: … | Hỗ trợ: …"
        "emp_sup_name": __import__("apps.pos.gold_bill", fromlist=["ten_nv"]).ten_nv(
            __import__("apps.pos.gold_bill", fromlist=["emp_sup_cua"]).emp_sup_cua(trn_id)) if loai != "THAU" else "",
    }, None


_TT_DONG_TIEM_GIU = {"Thâu": 0, "Đổi": 1}     # còn lại = dòng MÃ SP (vàng mới bán) → 2


def _sap_store_lines(ds):
    """Bảng vàng khách (tiệm giữ) xếp theo cột Mã SP — GĐ chốt 08/09/2026: THÂU trên cùng → ĐỔI → dòng MÃ SP.
    sorted ổn định nên trong từng nhóm giữ nguyên thứ tự KK trả về."""
    return sorted(ds, key=lambda x: _TT_DONG_TIEM_GIU.get(x.get("loai_dong"), 2))


def _gdb_ctx_mau():
    """Dữ liệu MẪU khi chưa có phiếu nào để xem trước trang chỉnh mẫu in."""
    now = datetime.datetime.now()
    r = {"TrnID": "TRB260900000000", "BillCode": "26-09-08-000001", "TrnDate": now, "TrnTime": now.strftime("%H:%M:%S"),
         "CustName": "Nguyễn Văn Mẫu", "Phone": "0909 000 000", "Address": "1276 Kha Vạn Cân, Thủ Đức",
         "EmpName": "Lý Ngọc Sơn", "TienBan": 25_600_000, "TienMua": 8_350_000, "SoTien": 17_250_000,
         "TienVangThem": 0, "TienCongThem": 0, "TienBot": 0, "TienCoc": 0}
    # ⚠ phải có cả GoldCode: template dùng `GoldLabel|default:x.GoldCode` — tham số filter thiếu key là NỔ
    # VariableDoesNotExist (dòng thật từ proc KK luôn có GoldCode nên chỉ dữ liệu mẫu mới dính).
    lines = [{"ProductCode": "1B60000975", "ProductDesc": "Bông khoen 2 nơ", "GoldCode": "610", "GoldLabel": "610",
              "tl_text": "0,385 chỉ", "rate": 8850, "task": 350, "amount": 3_757_000},
             {"ProductCode": "9N60016834", "ProductDesc": "Nhẫn trơn 1 chỉ", "GoldCode": "9999", "GoldLabel": "99.99",
              "tl_text": "1 chỉ", "rate": 14250, "task": 0, "amount": 14_250_000},
             {"ProductCode": "KC600175", "ProductDesc": "Dây chuyền", "GoldCode": "610", "GoldLabel": "610",
              "tl_text": "0,86 chỉ", "rate": 8850, "task": 450, "amount": 7_593_000}]
    store_lines = [{"loai_dong": "Đổi", "ProductDesc": "Dẻ 610", "GoldLabel": "610", "tl_vang": "0,5", "tl_hot": "0",
                    "rate": 8350, "task": 0, "amount": 4_175_000},
                   {"loai_dong": "Thâu", "ProductDesc": "Vàng khách", "GoldLabel": "99.99", "tl_vang": "0,3", "tl_hot": "0",
                    "rate": 13750, "task": 0, "amount": 4_125_000}]
    code = _ma_gdb(r["BillCode"])
    store_lines = _sap_store_lines(store_lines)
    return {"r": r, "loai": "BAN_DOI", "nguon": "mau", "lines": lines, "store_lines": store_lines, "tong_mon": len(lines),
            "tien_chu": _tien_bang_chu(r["SoTien"]), "barcode_code": code, "codebar": _codebar(code),
            "gdb_qr": _qr_hoa_don(code), "co_vang_doi_thau": True, "gdb_status": "Đổi bù", "emp_sup_name": "Trần Huỳnh Như"}


@require_GET
def hoa_don_chi_tiet(request):
    """Popup xem nhanh; nguồn luôn bám theo bảng người dùng đang xem."""
    trn_id, loai = (request.GET.get("trn_id") or "").strip(), (request.GET.get("loai") or "").strip()
    nguon = request.GET.get("nguon")
    # "live" (08/09/2026, nút IN màn bán): đi theo CÔNG TẮC ĐÍCH (kk/sandbox) như mọi thao tác bán hàng
    nguon = "live" if nguon == "live" else ("kk" if nguon == "kk" else "hist")
    ctx, err = _gdb_ctx(trn_id, loai, nguon)
    if err:
        return err
    # raw=1 (08/09/2026): trang IN THẲNG standalone cho iframe ẩn (THANH TOÁN & IN) — auto=1 tự window.print()
    tpl = "pos/gdb_in.html" if request.GET.get("raw") == "1" else "pos/_hoa_don_chi_tiet.html"
    # in_mode (08/09/2026): mở từ nút IN HÓA ĐƠN màn bán → popup rộng + nút 🖨 IN (đếm lần in qua ban_in_dem)
    ctx.update({"in_mode": request.GET.get("in") == "1", "auto": request.GET.get("auto") == "1"})
    return render(request, tpl, ctx)


def gdb_mau(request):
    """TRANG TÙY CHỈNH MẪU IN GĐB (GĐ chốt 08/09/2026): xem trước tờ A5 đúng 148mm, KÉO-THẢ từng khối, nhập
    left/top/rộng/cao (%) + cỡ chữ (pt); LƯU vào PmvState (gdb_layout) → popup xem & in thẳng dùng ngay.
    POST JSON {layout: {key: {left,top,w,h,fs}}} hoặc {reset: true} → trả {ok, layout, css}."""
    from . import gdb_layout as GL
    if request.method == "POST":
        try:
            data = json.loads(request.body or b"{}")
        except ValueError:
            return JsonResponse({"ok": False, "loi": "JSON không hợp lệ"}, status=400)
        layout = GL.save(None) if data.get("reset") else GL.save(data.get("layout") or {})
        logger.info("gdb_layout: %s %s", request.user.username, "reset" if data.get("reset") else "save")
        return JsonResponse({"ok": True, "layout": layout, "css": GL.css(layout)})
    trn = (request.GET.get("trn_id") or "").strip()
    loai = (request.GET.get("loai") or "").strip()
    if not trn:                                   # mặc định: phiếu ĐÃ CHỐT gần nhất theo công tắc đích
        try:
            row = PmvClient(tag="gdb_mau").query(
                "SELECT TOP 1 TrnID, BuyTotalAmount FROM TRN_RT_BUYSELL WITH (NOLOCK) "
                "WHERE Status = 'C' AND IsDel = '0' ORDER BY TrnDate DESC, TrnTime DESC")
            if row:
                trn = row[0]["TrnID"]
                loai = "BAN_DOI" if M.dec(row[0].get("BuyTotalAmount")) > 0 else "BAN"
        except Exception:
            trn = ""
    ctx = None
    if trn:
        ctx, _err = _gdb_ctx(trn, loai or "BAN", "live")
    if not ctx:
        ctx, trn = _gdb_ctx_mau(), ""
    layout = GL.load()
    ctx.update({
        "nav_active": "hoadon", "trn_mau": trn, "loai_mau": ctx.get("loai") or "BAN",
        "blocks": GL.BLOCKS, "layout_json": json.dumps(layout), "mac_dinh_json": json.dumps(GL.mac_dinh()),
        "canh_phai_json": json.dumps(sorted(GL.CANH_PHAI)), "kho_json": json.dumps(GL.IN_KHO),
        "sel_json": json.dumps({b["key"]: b["sel"] for b in GL.BLOCKS}),
    })
    return render(request, "pos/gdb_mau.html", ctx)


@require_POST
def hoa_don_huy(request):
    trn_id, loai = (request.POST.get("trn_id") or "").strip(), (request.POST.get("loai") or "").strip()
    action = (request.POST.get("action") or "").strip()
    passcode = request.POST.get("passcode") or ""
    if not _passcode_dung(request, passcode):
        # Trả 200 để HTMX thay chính popup bằng cảnh báo; HTTP 403 bị HTMX xem là
        # lỗi transport nên người dùng không nhìn thấy thông báo nhập sai.
        return render(request, "pos/_hoa_don_xac_nhan.html", {"trn_id": trn_id, "loai": loai,
            "action": action, "bill_code": (request.POST.get("bill_code") or trn_id).strip(),
            "action_name": "Hủy thanh toán" if action == "thanh_toan" else "Hủy hóa đơn",
            "loi": "Passcode không đúng. Hành động chưa được thực hiện."})
    if action not in ("thanh_toan", "hoa_don") or loai not in ("BAN", "BAN_DOI", "THAU") or not trn_id:
        return HttpResponse("Yêu cầu hủy không hợp lệ.", status=400)
    try:
        c, hd, _ = _doc_hd_kk(trn_id, loai)
        if not hd or str(hd.get("IsDel")) != "0":
            raise ValueError("Phiếu không còn hiệu lực để thao tác.")
        if not _duoc_thao_tac_hoa_don(request, hd):
            raise ValueError("Phiếu ngoài ngày hôm nay chỉ được xem. Tài khoản Admin mới có toàn quyền thao tác.")
        if action == "thanh_toan":
            if hd.get("Status") != B.CHOT_ROI:
                raise ValueError("Phiếu chưa ở trạng thái hoàn tất.")
            (B.mo_lai_thau if loai == "THAU" else B.mo_lai)(trn_id, user_id=_phien(request)["user_id"], c=c)
        else:
            if hd.get("Status") == B.CHOT_ROI:
                if not _co_fullcontrol_hoa_don(request):
                    raise ValueError("Hãy hủy thanh toán trước khi hủy hóa đơn.")
                # Admin có toàn quyền: một lần xác nhận passcode sẽ hoàn két /
                # mở khóa rồi xóa đơn. Proc thâu đã tự làm hai bước này; đơn
                # bán/đổi dùng hai proc vendor để đảm bảo hàng và két được hoàn.
                if loai == "THAU":
                    B.huy_thau(trn_id, user_id=_phien(request)["user_id"], c=c)
                else:
                    B.mo_lai(trn_id, user_id=_phien(request)["user_id"], c=c)
                    B.huy(trn_id, user_id=_phien(request)["user_id"], c=c)
            elif hd.get("Status") == B.NHAP:
                (B.huy_thau if loai == "THAU" else B.huy)(trn_id, user_id=_phien(request)["user_id"], c=c)
            else:
                raise ValueError("Phiếu không ở trạng thái có thể hủy.")
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
