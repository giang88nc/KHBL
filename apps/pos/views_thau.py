"""
THÂU VÀO — trang cùng khung màn BÁN HÀNG (GĐ chốt 08/09/2026 tối): THÔNG TIN · VÀNG THÂU (form như Vàng đổi,
NHIỀU DÒNG cùng 1 khách, công tắc Giá THÂU VÀO (mặc định) / BÁN RA) · TÍNH TỔNG đầy đủ · chân 4 nút · popup DANH SÁCH.

Ghi KK theo quy trình THAU_VANG: mỗi dòng = 1 TRN_RT_BUYGOLD (Ins/Upd) → cả nhóm chốt CHUNG 1 lệnh
TRN_RT_BUYGOLD_CompleteMore 'A@B@' → [CARDPAY_Ins từng dòng có CK] → T_TILL_TXN_Proc 'A@B@'. Nhóm dòng lưu ở MySQL
(models.ThauNhom) để mở lại / in 1 tờ 110mm. Bù/bớt gắn vào dòng lớn nhất, CK rót tuần tự (thau_cart.phan_bo).
"""
import datetime
import json
import logging
import re
from django.contrib.auth.decorators import login_not_required
from django.core.cache import cache
from django.http import HttpResponse
from django.shortcuts import render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from apps.pmv import gateway, money as M
from apps.pmv.client import PmvProcError

from . import quyen as Q
from . import anh_cccd as AC, bill as B, customer as C, services as S, thau_cart as TC, vietqr as QR
from .models import GoldBill, ThauAnhTam, ThauNhom, ThauPaymentLink

# 5 slot ảnh chuyển khoản (GĐ chốt 08/09/2026 tối): CCCD 2 mặt · 2 hình · QR chuyển khoản → cột gold_bill.anh_*
ANH_SLOTS = (("cccd1", "CCCD mặt trước", "environment"), ("cccd2", "CCCD mặt sau", "environment"),
             ("hinh1", "Hình 1", "environment"), ("hinh2", "Hình 2", "environment"), ("qr", "QR chuyển khoản", "environment"))
ANH_COT = {s: f"anh_{s}" for s, _, _ in ANH_SLOTS}          # tên ô ảnh (5 ô) — cũng là tên cột cũ trong gold_bill (giữ cột, không dùng nữa)
# LUỒNG ẢNH (GĐ chốt 09/09/2026): Hình 1 · Hình 2 · QR thuộc NHÓM ĐƠN (thau_nhom.anh_*); CCCD trước/sau thuộc KHÁCH (hồ sơ KK
# + file máy chủ). Chụp/chọn/✂ chỉ tạo ẢNH CHỜ (thau_anh_tam theo phiên) — bấm THANH TOÁN mới ghi.
NHOM_COT = {"hinh1": "anh_hinh1", "hinh2": "anh_hinh2", "qr": "anh_qr"}
CCCD_SLOT = {"cccd1": ("ImagePathMatTruoc", "mat-truoc", "p_ImageDataMatTruoc", "p_ImagePathMatTruoc", "MT", "CCCD mặt trước"),
             "cccd2": ("ImagePathMatSau", "mat-sau", "p_ImageDataMatSau", "p_ImagePathMatSau", "MS", "CCCD mặt sau")}
ANH_MAX = 12 * 1024 * 1024
from .views import (PC_SAI_TOI_DA, _co_fullcontrol_hoa_don, _loi_goi, _passcode_dung, _passcode_khoa, _phien,
                    _so, _so_tl, _tien_bang_chu)

logger = logging.getLogger(__name__)
KHOA_MSG = "Phiếu thâu đang KHÓA 🔒 (đã thanh toán) — bấm 🔓 SỬA (passcode) để mở"
# GĐ chốt 10/09/2026: TẠM KHÓA IN phiếu thâu — mọi nút in bị vô hiệu và view in cũng chặn ở máy chủ để không ai
# gọi thẳng đường dẫn. Mở lại: đổi hằng này về False (không cần sửa gì thêm, nút và view cùng đọc một chỗ).
KHOA_IN = True
KHOA_IN_MSG = "Tạm khóa in phiếu thâu"

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
    ctx["anh"] = _anh_slots(request, g)
    ctx["banks_vn"] = QR.BANKS
    ctx["khoa_in"], ctx["khoa_in_msg"] = KHOA_IN, KHOA_IN_MSG
    ctx["ck_nd_mac_dinh"] = TC.ck_nd_day_du(g)         # "THANH TOAN TIEN VANG {4 số cuối mã phiếu}" (GĐ chốt 10/09/2026)
    ctx["tao_qr_duoc"] = ctx["phieu_chot"] and bool(g.get("ck_bank") and g.get("ck_stk"))
    ctx.update(extra or {})
    return ctx


def _skey(request):
    if not request.session.session_key:
        request.session.save()
    return request.session.session_key


def _nhom_co_anh(nhom_id):
    """3 ô ảnh của nhóm đơn có hay không — đếm OCTET_LENGTH (MySQL), không tải blob chỉ để vẽ trạng thái."""
    if not nhom_id:
        return {}
    from django.db.models.expressions import RawSQL
    qs = ThauNhom.objects.filter(pk=nhom_id).annotate(**{f"n_{sl}": RawSQL(f"COALESCE(OCTET_LENGTH({c}),0)", []) for sl, c in NHOM_COT.items()})
    r = qs.values(*[f"n_{sl}" for sl in NHOM_COT]).first()
    return {sl: bool(r[f"n_{sl}"]) for sl in NHOM_COT} if r else {}


def _anh_slots(request, g):
    """Trạng thái 5 ô ảnh (GĐ chốt 09/09/2026):
    · CCCD trước/sau THEO KHÁCH: hiện từ hồ sơ khách trên máy KK; chưa chọn khách → ô KHÓA 'chọn khách hàng';
    · Hình 1 · Hình 2 · QR THEO NHÓM ĐƠN (thau_nhom.anh_*);
    · ẢNH CHỜ (thau_anh_tam theo phiên — vừa chụp/chọn/✂, chưa THANH TOÁN) đè lên hiển thị của cả hai, gắn nhãn 'chờ'."""
    tam = set(ThauAnhTam.objects.filter(session_key=_skey(request)).values_list("slot", flat=True))
    cust_id = (g.get("cust") or {}).get("id") or ""
    khach = None
    if cust_id and not {"cccd1", "cccd2"} <= tam:
        try:
            khach = C._current(S.client("khach_anh"), cust_id)
        except Exception:
            khach = None
    nhom_id = g.get("nhom_id") or ""
    nhom_co = _nhom_co_anh(nhom_id)
    v = timezone.localtime().strftime("%H%M%S")
    url_anh = reverse("pos:thau_anh")
    out = []
    for sl, label, facing in ANH_SLOTS:
        d = {"slot": sl, "label": label, "facing": facing, "co": False, "src": "", "tam": False, "khoa": False, "nguon": ""}
        if sl in tam:
            d.update(co=True, tam=True, nguon="tam", src=f"{url_anh}?slot={sl}&v={v}")
        elif sl in CCCD_SLOT:
            if not cust_id:
                d["khoa"] = True
            elif khach and khach.get(CCCD_SLOT[sl][0]):
                d.update(co=True, nguon="khach", src=f"/banle/khach-hang/{cust_id}/anh/{CCCD_SLOT[sl][1]}/?v={v}")
        elif nhom_co.get(sl):
            d.update(co=True, nguon="nhom", src=f"{url_anh}?slot={sl}&nhom={nhom_id}&v={v}")
        out.append(d)
    return out


def _nen_anh(upload, canh=1280, chat_luong=80, tran=None):
    """Xoay đúng EXIF, thu ≤canh px, JPEG. upload = UploadedFile hoặc bytes. tran = dung lượng tối đa (bytes) → hạ
    chất lượng dần. Tạm khi tải lên: 1280/80; lúc THANH TOÁN bóp MIN (_nen_min)."""
    from io import BytesIO
    from PIL import Image, ImageOps
    if isinstance(upload, (bytes, bytearray)):
        src = BytesIO(bytes(upload))
    else:
        if getattr(upload, "size", 0) > ANH_MAX:
            raise ValueError(f"{upload.name}: ảnh lớn quá 12 MB")
        upload.seek(0)
        src = upload
    im = Image.open(src)
    im.load()
    im = ImageOps.exif_transpose(im)
    if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
        rgba = im.convert("RGBA")
        base = Image.new("RGB", rgba.size, "white")
        base.paste(rgba, mask=rgba.getchannel("A"))
        im = base
    else:
        im = im.convert("RGB")
    im.thumbnail((canh, canh), Image.Resampling.LANCZOS)
    data = b""
    for q in (chat_luong, 70, 62, 55, 48):
        out = BytesIO()
        im.save(out, "JPEG", quality=q, optimize=True, progressive=True, subsampling="4:2:0")
        data = out.getvalue()
        if not tran or len(data) <= tran:
            break
    return data


def _nen_min(data):
    """Bóp MIN lúc THANH TOÁN (GĐ chốt 08/09 tối): ≤1000px, ≤250 KB, vẫn đọc được số CCCD / QR."""
    try:
        return _nen_anh(data, canh=1000, chat_luong=62, tran=250 * 1024)
    except Exception:
        return bytes(data)


@require_POST
def thau_anh_len(request):
    """Tải ảnh lên (chụp / chọn) cho 1 hoặc nhiều ô → chỉ tạo ẢNH CHỜ theo phiên; THANH TOÁN mới ghi (GĐ chốt 09/09)."""
    if _dang_khoa(request):                       # đơn KHÓA → không chụp/chọn; 🔓 SỬA rồi THANH TOÁN mới thay được ảnh
        return _loi(request, KHOA_MSG)
    g = TC.get(request)
    n = 0
    try:
        for slot in ANH_COT:
            f = request.FILES.get("anh_" + slot)
            if not f:
                continue
            _luu_anh_slot(request, g, slot, _nen_anh(f))
            n += 1
    except ValueError as exc:                     # CCCD mà chưa chọn khách
        return _loi(request, str(exc), {"loi_o": "#o-khach"})
    except Exception as exc:
        logger.warning("Ảnh thâu lỗi: %s", exc)
        return _loi(request, "Ảnh không hợp lệ: " + str(exc)[:160])
    if not n:
        return _loi(request, "Chưa chọn ảnh nào")
    return _oob(request, {"tin": f"Đã nhận {n} ảnh chờ — ghi vào phiếu / hồ sơ khách khi THANH TOÁN"})


def _tin_anh(tin, canh_bao_kh):
    tin = tin + (" · " + " · ".join(canh_bao_kh) if canh_bao_kh else "")
    return {"tin": tin} if not any("CHƯA" in x for x in canh_bao_kh) else {"loi_phieu": tin}


def _luu_anh_slot(request, g, slot, data, canh_bao_kh=None):
    """Ghi 1 ảnh (bytes JPEG đã nén) làm ẢNH CHỜ của ô (thau_anh_tam theo phiên). CCCD đòi phải đã chọn khách
    (chưa chọn → ValueError, ô đang khóa 'chọn khách hàng'). Ghi thật chỉ diễn ra ở THANH TOÁN (_chot_anh)."""
    if slot in CCCD_SLOT and not (g.get("cust") or {}).get("id"):
        raise ValueError("Chọn khách hàng trước khi lưu ảnh CCCD")
    ThauAnhTam.objects.update_or_create(session_key=_skey(request), slot=slot, defaults={"data": data})


def _anh_slot_data(request, g, slot):
    """Bytes ảnh đang HIỆN ở ô: ảnh chờ (phiên) → ảnh nhóm đơn (Hình/QR) → ảnh hồ sơ khách (CCCD)."""
    t = ThauAnhTam.objects.filter(session_key=_skey(request), slot=slot).first()
    if t:
        return bytes(t.data)
    if slot in NHOM_COT and g.get("nhom_id"):
        n = ThauNhom.objects.filter(pk=g["nhom_id"]).only(NHOM_COT[slot]).first()
        if n and getattr(n, NHOM_COT[slot]):
            return bytes(getattr(n, NHOM_COT[slot]))
    cust_id = (g.get("cust") or {}).get("id") or ""
    if cust_id and slot in CCCD_SLOT:
        try:
            data, _ = C.saved_image(cust_id, CCCD_SLOT[slot][1], client=S.client("khach_anh"))
            return data
        except Exception:
            return None
    return None


def _goc_xoay(raw):
    """Góc xoay người dùng kéo (độ, thuận kim đồng hồ như CSS rotate) → float trong [0, 360), làm tròn 0,1°."""
    try:
        goc = float(str(raw or "0").replace(",", "."))
    except ValueError:
        goc = 0.0
    if goc != goc or goc in (float("inf"), float("-inf")):
        goc = 0.0
    return round(goc % 360.0, 1)


def _phan_cat(request):
    """4 tỉ lệ cắt bớt cạnh (trên, phải, dưới, trái) từ tay cầm popup — phần cạnh, chấp nhận phẩy VN, kẹp [−0,3; 0,45]."""
    out = []
    for k in ("cat_t", "cat_r", "cat_b", "cat_l"):
        try:
            v = float(str(request.POST.get(k) or "0").replace(",", "."))
        except ValueError:
            v = 0.0
        if v != v:
            v = 0.0
        out.append(min(max(v, -0.3), 0.45))
    return tuple(out)


@require_GET
def thau_anh_cat(request):
    """Popup ✂: OpenCV tìm thẻ CCCD trong ảnh ô cccd1/cccd2 → cắt + nắn phối cảnh + khổ chuẩn 1170×738; xem trước ở 0°,
    người dùng KÉO XOAY bằng con trỏ trên ảnh kết quả (JS), ✓ LƯU gửi góc → thau_anh_cat_luu."""
    import base64
    slot = (request.GET.get("slot") or "").strip()
    label = {"cccd1": "CCCD mặt trước", "cccd2": "CCCD mặt sau"}.get(slot)
    if not label:
        return HttpResponse("Chỉ tách thẻ cho ô CCCD.", status=400)
    g = TC.get(request)
    ctx = {"slot": slot, "label": label}
    if _dang_khoa(request):
        return render(request, "pos/_thau_cat_modal.html", {**ctx, "loi": KHOA_MSG})
    data = _anh_slot_data(request, g, slot)
    if not data:
        return render(request, "pos/_thau_cat_modal.html", {**ctx, "loi": "Ô này chưa có ảnh — chụp / chọn ảnh trước."})
    try:
        kq = AC.cat_cccd(data, 0.0)
    except AC.KhongThayThe as exc:
        return render(request, "pos/_thau_cat_modal.html", {**ctx, "loi": str(exc)})
    except Exception as exc:
        logger.exception("Tách CCCD lỗi")
        return render(request, "pos/_thau_cat_modal.html", {**ctx, "loi": "Lỗi xử lý ảnh: " + str(exc)[:120]})
    goc = _nen_anh(data, canh=900, chat_luong=70)
    xem = _nen_anh(kq, canh=900, chat_luong=80)
    ctx.update({"anh_goc": "data:image/jpeg;base64," + base64.b64encode(goc).decode("ascii"),
                "anh_kq": "data:image/jpeg;base64," + base64.b64encode(xem).decode("ascii"), "w": AC.CHUAN_W, "h": AC.CHUAN_H})
    return render(request, "pos/_thau_cat_modal.html", ctx)


@require_POST
def thau_anh_cat_luu(request):
    """✓ LƯU thẻ đã tách: tính lại y hệt xem trước (cùng ảnh nguồn) + xoay theo góc người dùng kéo (goc, độ) rồi ghi đè
    slot (+ hồ sơ khách). Bội 90° → xoay không mất nét (90/270 ra ảnh dọc 738×1170); góc lẻ → xoay + cắt hình nội tiếp."""
    slot = (request.POST.get("slot") or "").strip()
    xoay = _goc_xoay(request.POST.get("goc"))
    cat = _phan_cat(request)
    if slot not in ("cccd1", "cccd2"):
        return _loi(request, "Chỉ tách thẻ cho ô CCCD", {"dong_modal": True})
    if _dang_khoa(request):
        return _loi(request, KHOA_MSG, {"dong_modal": True})
    g = TC.get(request)
    data = _anh_slot_data(request, g, slot)
    if not data:
        return _loi(request, "Ô này chưa có ảnh", {"dong_modal": True})
    try:
        kq = AC.cat_cccd(data, xoay, cat)
    except Exception as exc:
        return _loi(request, str(exc)[:160], {"dong_modal": True})
    # lưu đúng khổ đã xoay (ngang 1170×738 hay dọc 738×1170) — _nen_anh chỉ thu khi cạnh dài vượt CHUAN_W
    canh_bao_kh = []
    _luu_anh_slot(request, g, slot, _nen_anh(kq, canh=AC.CHUAN_W, chat_luong=88), canh_bao_kh)
    kq_tin = _tin_anh("Đã lưu thẻ đã tách", canh_bao_kh)
    kq_tin["dong_modal"] = True
    return _oob(request, kq_tin)


KH_CAT_MAT = {"truoc": ("anh_truoc", "mat-truoc", "CCCD mặt trước"), "sau": ("anh_sau", "mat-sau", "CCCD mặt sau")}
KH_CAT_TTL = 30 * 60            # ảnh gốc giữ trong cache 30 phút cho bước ✓ DÙNG


@require_POST
def khach_anh_cat(request):
    """✂ TÁCH THẺ trong popup THÊM/SỬA KHÁCH (/khach-hang/ — GĐ 10/09/2026): TÁI DÙNG y nguyên thuật toán AC.cat_cccd của
    trang Thâu vào (không sửa thuật toán). Nguồn ảnh: tệp đang chọn/chụp ở ô (multipart — nút ✂ hx-params chỉ gửi ô đó);
    ô chưa chọn tệp → lấy ảnh ĐÃ LƯU của khách trên KK theo CustID. Ảnh gốc (xoay đúng EXIF, ≤1600 px) giữ 30 phút trong
    cache theo mã ngẫu nhiên `nguon` để bước ✓ tính lại đúng cùng nguồn với xem trước. Popup LỒNG vào #kh-cat-root (nằm
    trong popup khách) — Đóng/×/màn mờ/Esc chỉ đóng popup lồng (khblKhCatDong), popup khách còn nguyên."""
    import base64
    import secrets
    mat = (request.POST.get("mat") or "").strip()
    cau_hinh = KH_CAT_MAT.get(mat)
    if not cau_hinh:
        return HttpResponse("Chỉ tách thẻ cho CCCD mặt trước / mặt sau.", status=400)
    field, kind, label = cau_hinh
    tpl = "pos/_khach_cat_modal.html"
    popup_render = getattr(request, "customer_render", render)
    ctx = {"mat": mat, "label": label, "slot": "kh_" + mat}
    upload = request.FILES.get(field)
    data = None
    try:
        if upload:
            data = _nen_anh(upload, canh=1600, chat_luong=92)
        else:
            cust_id = (request.POST.get("CustID") or "").strip()
            if cust_id:
                da_luu, _ct = C.saved_image(cust_id, kind, client=getattr(request, "customer_client", None))
                data = _nen_anh(bytes(da_luu), canh=1600, chat_luong=92)
    except Exception as exc:
        logger.warning("✂ khách: không đọc được ảnh nguồn (%s): %s", mat, exc)
        return popup_render(request, tpl, {**ctx, "loi": "Không đọc được ảnh nguồn: " + str(exc)[:120]})
    if not data:
        return popup_render(request, tpl, {**ctx, "loi": "Ô này chưa có ảnh — chọn tệp / chụp ảnh trước (khách đã lưu ảnh thì bấm ✂ được ngay)."})
    try:
        kq = AC.cat_cccd(data, 0.0)
    except AC.KhongThayThe as exc:
        return popup_render(request, tpl, {**ctx, "loi": str(exc)})
    except Exception as exc:
        logger.exception("Tách CCCD (popup khách) lỗi")
        return popup_render(request, tpl, {**ctx, "loi": "Lỗi xử lý ảnh: " + str(exc)[:120]})
    nguon = secrets.token_urlsafe(18)
    cache.set("khbl:khcat:" + nguon, data, KH_CAT_TTL)
    goc = _nen_anh(data, canh=900, chat_luong=70)
    xem = _nen_anh(kq, canh=900, chat_luong=80)
    ctx.update({"nguon": nguon, "anh_goc": "data:image/jpeg;base64," + base64.b64encode(goc).decode("ascii"),
                "anh_kq": "data:image/jpeg;base64," + base64.b64encode(xem).decode("ascii"), "w": AC.CHUAN_W, "h": AC.CHUAN_H})
    return popup_render(request, tpl, ctx)


@require_POST
def khach_anh_cat_luu(request):
    """✓ DÙNG thẻ đã tách (popup khách): tính lại từ ảnh gốc trong cache + góc xoay + 4 cạnh cắt bớt (cùng AC.cat_cccd) →
    trả JSON base64 JPEG (1170×738 ngang hay 738×1170 dọc) để JS (khblKhCatNhan) đặt vào ô tệp của biểu mẫu; ảnh chỉ ghi
    lên KK khi bấm LƯU KHÁCH (khach_luu → prepare_images như mọi ảnh chọn tay)."""
    import base64
    from io import BytesIO
    from django.http import JsonResponse
    from PIL import Image
    mat = (request.POST.get("mat") or "").strip()
    nguon = (request.POST.get("nguon") or "").strip()
    if mat not in KH_CAT_MAT or not re.fullmatch(r"[A-Za-z0-9_-]{16,40}", nguon):
        return JsonResponse({"loi": "Yêu cầu không hợp lệ"}, status=400)
    data = cache.get("khbl:khcat:" + nguon)
    if not data:
        return JsonResponse({"loi": "Ảnh gốc đã hết hạn (30 phút) — đóng popup rồi bấm ✂ lại."}, status=410)
    try:
        kq = AC.cat_cccd(data, _goc_xoay(request.POST.get("goc")), _phan_cat(request))
    except Exception as exc:
        return JsonResponse({"loi": str(exc)[:160]}, status=422)
    jpg = _nen_anh(kq, canh=AC.CHUAN_W, chat_luong=88)
    w, h = Image.open(BytesIO(jpg)).size
    return JsonResponse({"mat": mat, "ten": f"cccd_{mat}_{timezone.localtime():%Y%m%d_%H%M%S}.jpg", "w": w, "h": h,
                         "b64": base64.b64encode(jpg).decode("ascii")})


@require_POST
def thau_anh_xoa(request):
    """× chỉ bỏ ẢNH CHỜ. Ảnh đã gắn nhóm / hồ sơ khách thì thay bằng chụp/chọn hình mới rồi THANH TOÁN (GĐ chốt 09/09)."""
    slot = (request.POST.get("slot") or "").strip()
    if slot not in ANH_COT:
        return _loi(request, "Ô ảnh không hợp lệ")
    if _dang_khoa(request):
        return _loi(request, KHOA_MSG)
    n, _ = ThauAnhTam.objects.filter(session_key=_skey(request), slot=slot).delete()
    if not n:
        return _loi(request, "Ảnh này đã gắn phiếu / hồ sơ khách — chụp hoặc chọn hình mới rồi THANH TOÁN để thay")
    return _oob(request, {"tin": "Đã bỏ ảnh chờ"})


# GĐ chốt 10/09/2026: ảnh của phiếu xem được không cần đăng nhập, vì popup chi tiết trang /thau-vao-2/
# đã mở tự do — chặn ở đây thì popup chỉ còn khung trắng.
@login_not_required
@require_GET
def thau_anh(request):
    """Phục vụ ảnh theo ô: ?slot=&nhom=<id> → thau_nhom.anh_* (Hình 1/2/QR); không có nhom → ảnh chờ của phiên.
    Ảnh CCCD của khách đi đường /banle/khach-hang/<id>/anh/<mặt>/ (đọc từ máy KK)."""
    slot = (request.GET.get("slot") or "").strip()
    nhom = (request.GET.get("nhom") or "").strip()
    if slot not in ANH_COT:
        return HttpResponse(status=404)
    data = None
    if nhom and slot in NHOM_COT and nhom.isdigit():
        n = ThauNhom.objects.filter(pk=int(nhom)).only(NHOM_COT[slot]).first()
        data = getattr(n, NHOM_COT[slot], None) if n else None
    else:
        t = ThauAnhTam.objects.filter(session_key=_skey(request), slot=slot).first()
        data = t.data if t else None
    if not data:
        return HttpResponse(status=404)
    r = HttpResponse(bytes(data), content_type="image/jpeg")
    r["Cache-Control"] = "private, no-store"
    r["X-Content-Type-Options"] = "nosniff"
    return r


def _gb_mac_dinh(g, trn):
    t = TC.tong_cua(g)
    cust = g.get("cust") or {}
    return {"bill_code": (g.get("bill_codes") or [""])[0], "trn_date": datetime.date.today(), "trn_time": (g.get("gio") or "")[:8],
            "nguon": "KHBL", "emp_id": g.get("emp") or "", "cust_id": cust.get("id") or "", "cust_name": (cust.get("name") or "")[:200],
            "cust_phone": (cust.get("phone") or "")[:30], "tien_vang_cu": M.tron_ngan(t["tien_vang"]), "tien_bot": M.tron_ngan(t["bot"]),
            "tien_vang_them": M.tron_ngan(t["bu"]), "tong": M.tron_ngan(t["khach_tra"]), "pay_method": g.get("pay_method") or "cash",
            "tien_mat": M.tron_ngan(t["tien_mat"]), "tien_ck": M.tron_ngan(t["tien_ck"]) if g.get("pay_method") != "card" else M.D0,
            "tien_the": M.tron_ngan(t["tien_ck"]) if g.get("pay_method") == "card" else M.D0,
            "doi": [{"vang": x["gold"], "tl": x["tl_vang"], "hot": x["tl_hot"], "gia": x["gia"], "kieu": x["kieu"], "tien": x["tien"]} for x in g["lines"]],
            "status": g.get("status") or "C"}


def _ghi_gold_bill(request, g, trn, rows):
    """THANH TOÁN xong: ghi bản ghi gold_bill(TrnID đầu) cho báo cáo — KHÔNG còn mang ảnh (từ 09/09 ảnh ở thau_nhom / hồ sơ
    khách; 5 cột anh_* giữ nguyên trong bảng, không đọc ghi). Nuốt lỗi MySQL (KK vẫn đúng)."""
    try:
        d = _gb_mac_dinh(g, trn)
        d.update({"bill_code": rows[0].get("BillCode") or trn, "trn_time": (rows[0].get("TrnTime") or "")[:8],
                  "status": "C", "user_web": getattr(request.user, "username", ""), "synced_at": timezone.now()})
        GoldBill.objects.update_or_create(trn_id=trn, defaults=d)
    except Exception:
        logger.exception("gold_bill thâu: không ghi được %s", trn)


def _chot_anh(request, g, nhom, nhom_cu_id):
    """THANH TOÁN (GĐ chốt 09/09/2026) — ghi ẢNH CHỜ vào nhà thật:
    · nhóm cũ (🔓 SỬA → thanh toán lại tạo nhóm MỚI) → chép 3 ảnh sang nhóm mới trước, rồi ảnh chờ Hình 1/2/QR đè lên (bóp MIN);
    · CCCD chờ → hồ sơ KHÁCH trên máy KK (I_CUSTOMER_Upd, ghi đè nếu khách đã có) + kho private web;
    · ô nào ghi xong mới xóa ảnh chờ; CCCD ghi hồ sơ khách thất bại thì GIỮ chờ (🔓 SỬA rồi THANH TOÁN lại). Trả list cảnh báo."""
    sk = _skey(request)
    canh_bao, xong = [], []
    tam = list(ThauAnhTam.objects.filter(session_key=sk))
    try:
        if nhom_cu_id and str(nhom_cu_id) != str(nhom.pk):
            cu = ThauNhom.objects.filter(pk=nhom_cu_id).first()
            if cu:
                for c in NHOM_COT.values():
                    setattr(nhom, c, getattr(cu, c))
        for t in tam:
            if t.slot in NHOM_COT:
                setattr(nhom, NHOM_COT[t.slot], _nen_min(t.data))
                xong.append(t.slot)
        nhom.save(update_fields=list(NHOM_COT.values()))
    except Exception:
        logger.exception("thau_nhom %s: không ghi được ảnh Hình/QR", nhom.pk)
        canh_bao.append("CHƯA ghi được Hình 1/2/QR vào nhóm đơn — ảnh vẫn giữ chờ")
        xong = [x for x in xong if x not in NHOM_COT]
    cust_id = (g.get("cust") or {}).get("id") or ""
    for t in tam:
        if t.slot not in CCCD_SLOT:
            continue
        _, _, dp, pp, _, nhan = CCCD_SLOT[t.slot]
        if not cust_id:
            canh_bao.append(f"{nhan} CHƯA lưu: phiếu không chọn khách")
            continue
        data = bytes(t.data)
        try:
            with C.SAVE_LOCK:
                C.cap_nhat_anh(cust_id, {dp: data, pp: ".jpg"}, client=S.client("khach_anh_upd"))
            xong.append(t.slot)
            canh_bao.append(f"đã cập nhật {nhan} vào hồ sơ khách")
        except Exception as exc:
            logger.warning("Không cập nhật ảnh CCCD khách %s: %s", cust_id, exc)
            canh_bao.append(f"{nhan}: CHƯA cập nhật hồ sơ khách ({C.error_message(exc)[:100]}) — ảnh vẫn giữ chờ, 🔓 SỬA rồi THANH TOÁN lại")
    if xong:
        ThauAnhTam.objects.filter(session_key=sk, slot__in=xong).delete()
    return canh_bao


def _anh_tam_nhan(request):
    """Nhãn các ô ảnh đang giữ TẠM theo phiên (chưa gắn phiếu nào), theo thứ tự 5 ô."""
    co = set(ThauAnhTam.objects.filter(session_key=_skey(request)).values_list("slot", flat=True))
    return [nhan for s, nhan, _ in ANH_SLOTS if s in co]


def _chan_mat_anh(request, viec, url, vals=None):
    """GĐ chốt 09/09/2026 (phương án 1): còn ảnh TẠM mà rời phiếu → HỎI TRƯỚC bằng popup, không xóa âm thầm.
    Ảnh chỉ gắn vào phiếu khi THANH TOÁN; trước đó nằm ở thau_anh_tam theo phiên nên mọi lối 'trắng trang' đều mất.
    Trả None khi không có ảnh tạm hoặc người dùng đã bấm 'Bỏ ảnh' (bo_anh=1); ngược lại trả popup OOB vào #modal-root
    (nút gọi thường có hx-swap="none" nên phải đi đường OOB)."""
    if request.POST.get("bo_anh") == "1":
        return None
    nhan = _anh_tam_nhan(request)
    if not nhan:
        return None
    html = render_to_string("pos/_thau_anh_mat_modal.html",
                            {"anh_cho": nhan, "viec": viec, "url": url,
                             "vals": json.dumps(dict(vals or {}, bo_anh="1"), ensure_ascii=False)}, request=request)
    return HttpResponse('<div id="modal-root" hx-swap-oob="true">' + html + "</div>")


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
    Q.chan(request, "THAU_VAO")
    return render(request, "pos/thau.html", _ctx(request))


@require_POST
def thau_moi(request):
    """＋ PHIẾU MỚI: xóa trắng TẤT CẢ kể cả nhân viên thâu (GĐ chốt 08/09 tối) + ảnh tạm; dọn ảnh tạm cũ >2 ngày.
    Còn ảnh tạm chưa gắn phiếu → hỏi trước (GĐ chốt 09/09)."""
    from django.urls import reverse
    chan = _chan_mat_anh(request, "Phiếu mới", reverse("pos:thau_moi"))
    if chan is not None:
        return chan
    TC.clear(request, giu_nv=False)
    ThauAnhTam.objects.filter(session_key=_skey(request)).delete()
    ThauAnhTam.objects.filter(created_at__lt=timezone.now() - datetime.timedelta(days=2)).delete()
    tin = {"tin": "Phiếu thâu mới"}
    if request.POST.get("bo_anh") == "1":
        tin["dong_modal"] = True                                   # đóng popup cảnh báo vừa bấm "Bỏ ảnh"
    return _oob(request, tin)


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
        previous_cust=g.get("cust") or {}
        g["cust"] = {"id": k["CustID"], "code": k.get("CustCode") or "", "name": k["CustName"],
                     "phone": k.get("Phone") or ""} if k else None
        if k: g["cust"]["phones"]=[k.get(field) for field in ("Phone","GhiChu2","GhiChu3") if k.get(field)]
        if k and previous_cust.get("id")==k["CustID"]: g["cust"]["phone"]=previous_cust.get("phone","")
    if "document_phone" in P and g.get("cust"):
        from .document_contacts import normalize_phone
        try: g["cust"]["phone"] = normalize_phone(P.get("document_phone"))
        except ValueError as exc: return _loi(request,str(exc))
    if "emp" in P:
        g["emp"] = (P.get("emp") or "").strip()
    if "ghi_chu" in P:
        g["ghi_chu"] = (P.get("ghi_chu") or "").strip()[:300]
    for o in ("bu", "bot"):
        if o in P:
            g[o] = str(M.tron_ngan(_so(P.get(o))))
    if "pay_method" in P:
        pm = (P.get("pay_method") or "").strip()
        g["pay_method"] = "bank" if pm in ("bank", "card") else "cash"      # 08/09 tối: BỎ THẺ
    if "ck_bank" in P:
        g["ck_bank"] = (P.get("ck_bank") or "").strip().upper()[:12]
    if "ck_stk" in P:
        g["ck_stk"] = "".join(ch for ch in (P.get("ck_stk") or "") if ch.isalnum())[:40]
    if "ck_nd" in P:
        g["ck_nd"] = (P.get("ck_nd") or "").strip()[:60]
    if "ck_ten" in P:
        g["ck_ten"] = (P.get("ck_ten") or "").strip()[:100]
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
    if moc <= 0:
        return _loi(request, 'Chưa có giá MySQL hợp lệ cho loại vàng này. Hãy kiểm tra Bảng giá.', {"loi_o": "#t-gia"})
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
    """× dòng: dòng ĐÃ LƯU CHỜ trên KK (có trn_id, sau SỬA) → xóa hẳn dòng đó trên KK (GĐ chốt 08/09 tối) rồi bỏ khỏi giỏ."""
    if _dang_khoa(request):
        return _loi(request, KHOA_MSG)
    try:
        i = int(request.POST.get("i", -1))
    except (TypeError, ValueError):
        return _oob(request)
    g = TC.get(request)
    if not (0 <= i < len(g["lines"])):
        return _oob(request)
    x = g["lines"][i]
    tin = ""
    if x.get("trn_id"):
        try:
            B.huy_thau(x["trn_id"], user_id=_phien(request)["user_id"])
        except Exception as exc:
            return _loi(request, f"Xóa dòng {x.get('bill_code') or x['trn_id']} trên KK không được: {_loi_goi(exc)}")
        gateway.canh_bao("thau_xoa_dong", f"XÓA dòng chờ {x.get('bill_code') or x['trn_id']}; người: {request.user.username}")
        GoldBill.objects.filter(trn_id=x["trn_id"]).update(is_del=True)
        g["trn_ids"] = [t for t in g.get("trn_ids") or [] if t != x["trn_id"]]
        g["bill_codes"] = [b for b in g.get("bill_codes") or [] if b != x.get("bill_code")]
        cache.delete("khbl:thau_treo")
        tin = f"Đã xóa hẳn dòng {x.get('bill_code') or x['trn_id']} trên máy KK"
    g["lines"].pop(i)
    TC.save(request, g)
    return _oob(request, {"tin": tin} if tin else None)      # ảnh theo NHÓM đơn (09/09) → xóa dòng nào cũng không đụng ảnh


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
    # GĐ chốt 08/09 tối: cùng LOẠI VÀNG & cùng GIÁ → gộp về 1 dòng (cộng tổng TL + hột). Dòng đã lưu W giữ trn_id
    # của dòng đầu; các trn_id bị gộp bớt → xóa hẳn trên KK (như bấm ×).
    gop, thu_tu, bo_kk = {}, [], []
    so_truoc = len(moi)
    for x in moi:
        k = (x["gold"], x["gia"], x["kieu"])
        if k in gop:
            d = gop[k]
            tong = M.dec(d["tong_tl"]) + M.dec(x["tong_tl"])
            hot = M.dec(d["tl_hot"]) + M.dec(x["tl_hot"])
            tien = M.buy_amount_standalone(tong - hot, M.dec(d["gia"]), 100, 0, d["unit"])
            gop[k] = TC.dong(d["gold"], d["desc"], d["unit"], tong, hot, d["gia"], d["kieu"], tien,
                             d.get("trn_id"), d.get("bill_code"), d.get("upd"))
            if x.get("trn_id"):
                bo_kk.append(x)
        else:
            gop[k] = x
            thu_tu.append(k)
    for x in bo_kk:
        try:
            B.huy_thau(x["trn_id"], user_id=_phien(request)["user_id"])
            g["trn_ids"] = [t for t in g.get("trn_ids") or [] if t != x["trn_id"]]
            g["bill_codes"] = [b for b in g.get("bill_codes") or [] if b != x.get("bill_code")]
        except Exception as exc:
            return _loi(request, f"Gộp dòng: xóa {x.get('bill_code') or x['trn_id']} trên KK không được: {_loi_goi(exc)}")
    moi = [gop[k] for k in thu_tu]
    g["lines"], g["kieu_ui"] = moi, kieu
    TC.save(request, g)
    if bo_kk:
        cache.delete("khbl:thau_treo")
    return _oob(request, {"tin": f"Đã tính lại {len(moi)} dòng theo giá {'BÁN RA' if kieu == 'ban' else 'THÂU VÀO'}"
                                 + (f" · đã gộp {so_truoc - len(moi)} dòng trùng loại & giá" if so_truoc > len(moi) else "")})


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
    if kind == "khach":
        from .customer_phones import suggestion_rows
        ds = suggestion_rows(ds, cccd or q)
    return render(request, "pos/_thau_goiy.html", {"ds": ds, "kind": kind})


# ─────────────────────────── danh sách · mở lại ───────────────────────────
def _ngay(v, mac_dinh):
    try:
        return datetime.date.fromisoformat(v or mac_dinh).isoformat()
    except ValueError:
        return mac_dinh


def _gom_dong_ds(ds, nhom):
    """GỘP các phiếu cùng MỘT nhóm thành MỘT dòng của popup DANH SÁCH (GĐ chốt 10/09/2026).

    Trước đây mỗi dòng TRN_RT_BUYGOLD là một dòng bảng, nên một lần thâu nhiều loại vàng nằm rải ra nhiều dòng
    trùng khách trùng giờ. Nay gom theo ThauNhom; phiếu không thuộc nhóm nào vẫn đứng riêng một dòng.

    Mỗi dòng gộp mang thêm:
      · ma_phieu  — danh sách mã, template in mỗi mã một dòng;
      · tong_tien — tổng tiền tiệm trả của cả nhóm;
      · tong_ck   — tổng tiền chuyển khoản (CardPay âm trên PMV, đổi dấu cho dễ đọc);
      · so_dong / da_huy / co_huy / chot — để vẽ badge trạng thái chung.
    Tiền tính trên các dòng CÒN SỐNG; nhóm bị hủy sạch thì tính trên chính các dòng đã hủy để vẫn thấy con số.
    """
    dong, chi_muc = [], {}
    for r in ds:
        n = nhom.get(r["TrnID"])
        khoa = f"nhom-{n.pk}" if n else f"phieu-{r['TrnID']}"
        d = chi_muc.get(khoa)
        if d is None:
            d = dict(r)                       # phiếu ĐẦU đại diện: ngày giờ, khách, nhân viên
            d["rows"] = []
            chi_muc[khoa] = d
            dong.append(d)
        d["rows"].append(r)
    for d in dong:
        rows = d["rows"]
        song = [r for r in rows if str(r.get("IsDel")) == "0"]
        tinh = song or rows
        d["ma_phieu"] = [r.get("BillCode") or r["TrnID"] for r in rows]
        d["tong_tien"] = sum((M.dec(r.get("SoTien")) for r in tinh), M.D0)
        d["tong_ck"] = sum((-M.dec(r.get("CardPay")) for r in tinh if M.dec(r.get("CardPay")) < 0), M.D0)
        d["so_dong"] = len(rows)
        d["da_huy"] = not song
        d["co_huy"] = len(song) != len(rows)
        d["chot"] = bool(song) and all(r.get("Status") == "C" for r in song)
        d["mo_id"] = (song or rows)[0]["TrnID"]         # MỞ / IN dùng phiếu đầu còn sống, thau_mo tự nạp cả nhóm
    return dong


def _danh_dau_xac_nhan(dong):
    """Gắn cờ da_xac_nhan cho từng dòng của popup DANH SÁCH: nhóm đã được đối soát đủ tiền chuyển khoản
    (bảng thau_payment_link) thì trang thâu chỉ cho XEM, không cho mở ra sửa nữa (GĐ chốt 10/09/2026).

    Đọc MỘT lần toàn bộ liên kết còn hiệu lực rồi ghép trong bộ nhớ — bảng này mỗi phiếu một dòng nên rất nhỏ,
    làm vậy tránh bắn mỗi dòng một truy vấn."""
    from decimal import Decimal

    lk = list(ThauPaymentLink.objects.filter(active_notification_id__isnull=False).values("trn_ids", "amount"))
    for d in dong:
        ids = {r["TrnID"] for r in d["rows"]}
        da_tra = sum((l["amount"] for l in lk if ids & set(l["trn_ids"])), Decimal(0))
        d["da_tra_ck"] = da_tra
        d["da_xac_nhan"] = bool(d["tong_ck"]) and da_tra >= d["tong_ck"]


@require_GET
def thau_ds(request, standalone=False):
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
    ds = _gom_dong_ds(ds, nhom)
    _danh_dau_xac_nhan(ds)
    return render(request, "pos/_thau_ds.html", {
        "standalone": standalone, "nav_active": "thau",
        "ds_base": "pos/_thau_ds_page.html" if standalone else "partials/modal_shell.html",
        "ds": ds, "tk": tk, "d1": d1, "d2": d2, "hom_nay": hom_nay, "loc": loc, "nvs": S.nhan_vien_ban(),
        "khoa_in": KHOA_IN, "khoa_in_msg": KHOA_IN_MSG,
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
    """MỞ đơn từ DANH SÁCH. GĐ chốt 09/09 chiều: KHÔNG đụng ảnh chờ — ảnh chờ đè lên ảnh đơn vừa mở, THANH TOÁN mới ghi."""
    trn = (request.POST.get("trn_id") or "").strip()
    try:
        rows, nhom = _rows_cua(trn)
    except Exception as exc:
        return _loi(request, "Không mở được phiếu: " + S.error_message(exc), {"dong_modal": True})
    if not rows:
        return _loi(request, f"Không thấy phiếu {trn}", {"dong_modal": True})
    g = TC.nap(request, rows, nhom)
    ma = ", ".join(g["bill_codes"])
    if request.POST.get('from_list_page') == '1':
        response = HttpResponse(status=204)
        from django.urls import reverse
        response['HX-Redirect'] = reverse('pos:thau')
        return response
    return _oob(request, {"tin": f"Đã mở {ma}" + (" (đã chốt — chỉ xem, 🔓 SỬA để đổi)" if g["status"] == B.CHOT_ROI else ""),
                          "dong_modal": True})


def _ten_tk_da_biet(bank, stk):
    """Tên chủ tài khoản đã dùng cho SỐ TK này ở phiếu thâu gần nhất. Dùng khi QR của khách không ghi tên:
    khách quen chuyển lại cùng tài khoản thì khỏi gõ tay. Chỉ đọc dữ liệu của chính tiệm, không gọi ra ngoài."""
    stk = (stk or "").strip()
    if not stk:
        return ""
    qs = ThauNhom.objects.filter(ck_stk=stk).exclude(ck_ten="").order_by("-pk")
    if bank:
        qs = qs.filter(ck_bank=bank) | ThauNhom.objects.filter(ck_stk=stk, ck_bank="").exclude(ck_ten="")
        qs = qs.order_by("-pk")
    nhom = qs.first()
    return (nhom.ck_ten or "").strip() if nhom else ""


@require_POST
def thau_qr_quet(request):
    """Quét mã QR trong ảnh ô 'QR chuyển khoản' (chỉ nhận VietQR/NAPAS chuyển khoản tới tài khoản) → điền ngân hàng · số TK ·
    nội dung (= mã phiếu) vào khối THÔNG TIN CHUYỂN KHOẢN của TÍNH TỔNG + chuyển phương thức sang CHUYỂN KHOẢN."""
    if _dang_khoa(request):
        return _loi(request, KHOA_MSG)
    g = TC.get(request)
    chuoi = (request.POST.get("chuoi") or "").strip()          # 08/09 tối: ô SCAN máy quét gửi thẳng chuỗi QR
    if not chuoi:
        data = _anh_slot_data(request, g, "qr")               # ảnh chờ → ảnh QR của nhóm đơn
        if not data:
            return _loi(request, "Chưa có ảnh ở ô QR chuyển khoản — chụp / chọn ảnh QR của khách trước", {"loi_o": "#o-ckscan"})
        try:
            chuoi = QR.doc_anh(data)
        except RuntimeError as exc:
            return _loi(request, str(exc))
        if not chuoi:
            return _loi(request, "Không đọc được mã QR trong ảnh — chụp lại rõ, thẳng, đủ 4 góc")
    kq = QR.parse(chuoi)
    if not kq["hop_le"]:
        return _loi(request, kq["loi"] or "QR không hợp lệ", {"loi_o": "#o-ckscan"})
    g["ck_bank"], g["ck_stk"] = kq["bank_code"], kq["account"]
    g["ck_nd"] = TC.noi_dung_ck(g)
    # TÊN CHỦ TK — 4 lớp, dừng ở lớp đầu tiên có kết quả (GĐ chốt 10/09/2026):
    #   1. tên ghi sẵn trong QR (EMVCo tag 59). Nhiều QR tĩnh in ra không có trường này nên thường trượt.
    #   2. tên đã dùng cho chính số TK đó ở phiếu thâu cũ — khách quen chuyển lại thì khỏi gõ.
    #   3. tên nhân viên đang gõ dở — không bao giờ đè lên.
    #   4. GỢI Ý theo tên khách của phiếu: khách bán vàng phần lớn nhận vào tài khoản của chính họ.
    #      Chỉ là gợi ý nên toast nói rõ để nhân viên liếc lại; tên này không quyết định tiền đi đâu, số TK mới quyết.
    # (Không tra tên qua mạng: VietQR đã ngừng dịch vụ, SePay không có, các bên còn lại đều tính phí theo lượt.)
    goi_y = ""
    ten_qr = kq["ten"] or _ten_tk_da_biet(kq["bank_code"], kq["account"])
    if not ten_qr and not (g.get("ck_ten") or "").strip():
        goi_y = QR.khong_dau((g.get("cust") or {}).get("name") or "")[:100]
    g["ck_ten"] = ten_qr or (g.get("ck_ten") or "") or goi_y
    g["pay_method"] = "bank"
    TC.save(request, g)
    tin = f"QR: {kq['bank_ten']} · {kq['account']}"
    if g["ck_ten"]:
        tin += f" · {g['ck_ten']}" + (" (tên khách — kiểm lại)" if goi_y and g["ck_ten"] == goi_y else "")
    return _oob(request, {"tin": tin + (f" · nội dung QR: {kq['info']}" if kq["info"] else "")})


@require_GET
def thau_qr_tao(request):
    """Popup QR chuyển khoản CHO KHÁCH (tiệm trả): ngân hàng + số TK khách trong form + số tiền CK + nội dung = mã phiếu."""
    g = TC.get(request)
    if g.get("status") != B.CHOT_ROI:
        return render(request, "pos/_qr_modal.html", {"loi": "Phiếu chưa THANH TOÁN — chốt xong (có mã phiếu) mới tạo QR."})
    t = TC.tong_cua(g)
    bank, stk = (g.get("ck_bank") or "").strip(), (g.get("ck_stk") or "").strip()
    nd = TC.ck_nd_day_du(g)
    amount = t["tien_ck"]
    try:
        chuoi = QR.payload(bank, stk, amount, nd, g.get("ck_ten") or "")
    except ValueError as exc:
        return render(request, "pos/_qr_modal.html", {"loi": str(exc)})
    return render(request, "pos/_qr_modal.html", {
        "qr_img": "data:image/png;base64," + _qr_png_b64(chuoi),
        "bank_ten": QR.bank_ten(bank) if QR.bank_bin(bank) else bank, "bank_num": stk, "bank_user": g.get("ck_ten") or "",
        "amount": M.dec(amount), "info": nd, "ten_kh": (g.get("cust") or {}).get("name") or "",
        "luu_url": "/banle/thau-vao/qr/luu/"})


def _qr_png_b64(chuoi, scale=7):
    import base64
    from io import BytesIO
    import segno
    buf = BytesIO()
    segno.make(chuoi, error="m").save(buf, kind="png", scale=scale, border=4)   # viền 4 ô = vùng trắng CHUẨN QR (2 ô: OpenCV có lúc không đọc được)
    return base64.b64encode(buf.getvalue()).decode("ascii")


@require_POST
def thau_qr_luu(request):
    """LƯU hình QR vừa tạo ĐÈ lên ô 'QR chuyển khoản' của NHÓM ĐƠN đã chốt (thau_nhom.anh_qr) — GĐ chốt 08/09 tối, nhà mới 09/09."""
    import base64
    g = TC.get(request)
    nhom = ThauNhom.objects.filter(pk=g.get("nhom_id") or 0).first()
    if g.get("status") != B.CHOT_ROI or nhom is None:
        return _loi(request, "Phiếu chưa THANH TOÁN — không có nhóm đơn để gắn hình QR", {"dong_modal": True})
    t = TC.tong_cua(g)
    nd = TC.ck_nd_day_du(g)
    try:
        chuoi = QR.payload(g.get("ck_bank"), g.get("ck_stk"), t["tien_ck"], nd)
    except ValueError as exc:
        return _loi(request, str(exc), {"dong_modal": True})
    png = base64.b64decode(_qr_png_b64(chuoi, scale=8))
    # GĐ chốt 09/09: nút LƯU trong popup Tạo QR ghi THẲNG lên nhóm đơn (đơn đã chốt, nhóm đã có) — không chờ THANH TOÁN
    nhom.anh_qr = _nen_anh(png, canh=800, chat_luong=90)
    nhom.ck_bank, nhom.ck_stk, nhom.ck_nd, nhom.ck_ten = g.get("ck_bank") or "", g.get("ck_stk") or "", nd, g.get("ck_ten") or ""
    nhom.save(update_fields=["anh_qr", "ck_bank", "ck_stk", "ck_nd", "ck_ten"])
    ThauAnhTam.objects.filter(session_key=_skey(request), slot="qr").delete()    # QR chờ (nếu có) đã bị QR tạo thay
    return _oob(request, {"tin": "Đã lưu hình QR chuyển khoản vào nhóm đơn", "dong_modal": True})


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
    if not ck and nhom is not None and M.dec(getattr(nhom, "tien_ck", 0)) > 0:
        ck = M.dec(nhom.tien_ck)    # 29/09/2026: CK ghi lên KK SAU đối soát — tờ in vẫn ghi đúng số chuyển khoản
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
    """Lưu từng dòng (Ins/Upd) rồi CHỐT CẢ NHÓM: CompleteMore 'A@B@' → T_TILL_TXN_Proc 'A@B@'.

    GĐ chốt 29/09/2026 — "thống nhất như bán": THANH TOÁN để KK nằm TIỀN MẶT (không gọi CARDPAY_Ins nữa). Số CK
    cần chuyển giữ ở thau_nhom.tien_ck; đối soát khớp giao dịch ngân hàng RA mới ghi CardPay (cộng dồn các lần CK)
    — xem thau_payments.dong_bo_sau → ck_tra_khach.ghi_ck_kk → gateway.pmv_out_allocate_buygold."""
    g = TC.get(request)
    if "document_phone" in request.POST and g.get("cust"):
        from .document_contacts import normalize_phone
        try: g["cust"]["phone"]=normalize_phone(request.POST["document_phone"])
        except ValueError as exc: return _loi(request,str(exc))
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
            from . import document_contacts as DC
            import uuid
            token=x.setdefault("contact_token",uuid.uuid4().hex);TC.save(request,g);request.session.save()
            intent=dict(token=token,contact=DC.save_cart("KHBL_BUYGOLD",g,request.user.username))
            def created(trn_id):
                x["trn_id"]=trn_id;TC.save(request,g);request.session.save()
            row = B.luu_thau(trn_id=x.get("trn_id") or "", cust_id=cust_id, emp_id=g["emp"], till_id=ph["till_id"],
                             shop_id=ph["shop_id"], user_id=ph["user_id"], gold_code=x["gold"], gw=M.dec(x["tl_vang"]),
                             dw=M.dec(x["tl_hot"]), rate=M.dec(x["gia"]), pct=100, add_money=add,
                             notes=g.get("ghi_chu") or "", unit=x["unit"], c=c, contact_intent=intent, on_created=created)
            ids.append(row["TrnID"])
        B.chot_thau_nhom(ids, till_id=ph["till_id"], user_id=ph["user_id"], ck_theo_phieu=None, c=c)   # KK: tiền mặt
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
                                   bu=t["bu"], bot=t["bot"], ghi_chu=g.get("ghi_chu") or "", kieu=[x["kieu"] for x in g["lines"]],
                                   ck_bank=g.get("ck_bank") or "", ck_stk=g.get("ck_stk") or "",
                                   ck_nd=TC.ck_nd_day_du(g, rows[0].get("BillCode") or "") if t["tien_ck"] else "",
                                   ck_ten=g.get("ck_ten") or "")
    from apps.pmv.models import PmvState
    from . import thau_payments as TP
    if not TP.moc_ck_sau():         # mốc chuyển sang "CK ghi sau đối soát": nhóm đầu tiên thanh toán bằng mã mới
        PmvState.set(TP.MOC_CK_SAU, nhom.pk)
    gateway.canh_bao("thau_tt", f"THÂU {', '.join(nhom.bill_codes)} — tiệm trả {M.money_vn(t['khach_tra'])} "
                                f"(CK {M.money_vn(t['tien_ck'])}); người: {request.user.username}")
    _ghi_gold_bill(request, g, ids[0], rows)
    canh_bao_anh = _chot_anh(request, g, nhom, g.get("nhom_id"))     # g còn nhom_id CŨ (chưa nap) → chép ảnh nhóm cũ sang
    tin = f"Đã thanh toán {', '.join(nhom.bill_codes)} — tiệm trả {M.money_vn(t['khach_tra'])}"
    extra = _tin_anh(tin, canh_bao_anh)
    if request.POST.get("in") == "1":
        p = _phieu_ctx(rows, nhom)
        extra["in_html"] = render_to_string("pos/_thau_phieu.html", {"p": p, "tiem": S.thong_tin_tiem(),
                                                                     "in_luc": datetime.datetime.now()}, request=request)
        if "tin" in extra:
            extra["tin"] = extra["tin"] + " · đang in phiếu — form đã trắng cho khách kế"
        TC.clear(request, giu_nv=True)
        ThauAnhTam.objects.filter(session_key=_skey(request)).delete()   # form trắng cho KHÁCH KẾ = đơn mới → dọn ảnh chờ còn sót
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
        GoldBill.objects.filter(trn_id__in=ids).update(is_del=True)
    cache.delete("khbl:thau_treo")                        # ảnh chờ GIỮ (GĐ chốt 09/09 chiều)
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
    elif hanh_dong == "huy_tt":                       # ảnh chờ GIỮ (GĐ chốt 09/09 chiều) — chỉ ＋ PHIẾU MỚI mới dọn
        TC.clear(request, giu_nv=False)
        tin = f"Đã HỦY THANH TOÁN {ma} — phiếu về DANH SÁCH CHỜ."
    else:
        GoldBill.objects.filter(trn_id__in=ids).update(is_del=True)
        TC.clear(request, giu_nv=False)                   # ảnh chờ GIỮ
        tin = f"Đã XÓA phiếu thâu {ma}."
    return _oob(request, {"tin": tin, "dong_modal": True})


# ─────────────────────────── in 110mm ───────────────────────────
@require_GET
def thau_in(request):
    """In phiếu thâu 110mm cho cả nhóm: ?oob=1 → nhét #pos-in in THẲNG từ cửa sổ; mặc định trang standalone (auto=1 tự in)."""
    if KHOA_IN:
        return HttpResponse(KHOA_IN_MSG, status=403)
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
