# -*- coding: utf-8 -*-
"""
TIỆM CHUYỂN KHOẢN CHO KHÁCH — phần dùng chung cho nghiệp vụ NGOÀI phiếu thâu (GĐ chốt 19/09/2026).

Bối cảnh: hóa đơn BÁN-ĐỔI mà vàng cũ khách đưa lớn hơn vàng mới → PayAmount ÂM, tiệm phải TRẢ tiền cho khách.
Trước đây phần trả này chỉ có tiền mặt; nay làm y như THÂU VÀO chuyển khoản:

  1. Màn Bán hàng: tiệm trả + chọn Chuyển khoản → khối THÔNG TIN CHUYỂN KHOẢN (ngân hàng · số TK · tên chủ TK ·
     nội dung) + ô Scan QR của khách. Được CHIA tiền mặt + chuyển khoản (GĐ chốt).
  2. THANH TOÁN xong → ghi một dòng ThauNhom(nghiep_vu='doi') cho hóa đơn đó, cùng các cột ck_* như phiếu thâu.
  3. Đối soát (thau_payments, job 5 phút) bắt giao dịch ngân hàng RA theo đúng khuôn nội dung
     "THANH TOAN TIEN VANG {4 số cuối mã hóa đơn}", có phân biệt loại nghiệp vụ.
  4. Khớp xong mới ghi CardPay ÂM lên máy KK (phương án a — gateway.pmv_out_allocate_retail); gỡ liên kết thì
     trả CardPay về đúng tổng còn hiệu lực. Trước đó KK coi như chi tiền mặt.

Cầm đồ sẽ vào sau với nghiep_vu='camdo' theo đúng khuôn này.
Mã thâu vào (views_thau, thau_cart) KHÔNG sửa — module này chỉ GỌI LẠI các hàm của nó.
"""
import logging
from decimal import Decimal

from apps.pmv import money as M

from .models import ThauNhom

logger = logging.getLogger(__name__)

DOI = ThauNhom.DOI
CK_KEYS = ("ck_bank", "ck_stk", "ck_ten", "ck_nd")


# ─────────────────────────── giỏ bán hàng ───────────────────────────
def tra_khach(t):
    """Hóa đơn đang làm có phải TIỆM TRẢ KHÁCH không (khách trả < 0)."""
    return M.dec(t.get("khach_tra")) < 0


def nhan_form(g, P):
    """Ghi các ô THÔNG TIN CHUYỂN KHOẢN từ form vào giỏ — cùng cách làm sạch như trang Thâu vào (thau_dat)."""
    if "ck_bank" in P:
        g["ck_bank"] = (P.get("ck_bank") or "").strip().upper()[:12]
    if "ck_stk" in P:
        g["ck_stk"] = "".join(ch for ch in (P.get("ck_stk") or "") if ch.isalnum())[:40]
    if "ck_nd" in P:
        g["ck_nd"] = (P.get("ck_nd") or "").strip()[:60]
    if "ck_ten" in P:
        g["ck_ten"] = (P.get("ck_ten") or "").strip()[:100]


def giu(cu, moi):
    """Giữ thông tin chuyển khoản khi giỏ được nạp lại từ KK (KK không có chỗ lưu các ô này)."""
    for k in CK_KEYS:
        if cu.get(k):
            moi[k] = cu[k]


def noi_dung(g):
    """Nội dung CK CHUẨN: "THANH TOAN TIEN VANG {4 số cuối mã hóa đơn}" — cùng khuôn phiếu thâu (GĐ chốt giữ
    nguyên). Người bán gõ nội dung riêng thì giữ; chưa có mã hóa đơn thì chỉ phần chữ."""
    from . import thau_cart as TC

    nd = (g.get("ck_nd") or "").strip()
    return TC.noi_dung_ck(None, g.get("bill_code") or "") if not nd or nd == TC.CK_ND_CHU else nd   # 4 số cuối SỐ HĐ (22/09/2026)


def nhom_doi(trn_id):
    """Nhóm CK mới nhất của hóa đơn bán-đổi (bán lại sau SỬA tạo nhóm mới; nhóm mới nhất là bản đúng)."""
    if not trn_id:
        return None
    from django.db import connection

    qs = ThauNhom.objects.filter(nghiep_vu=DOI).order_by("-pk")
    if connection.vendor == "mysql":
        return qs.filter(trn_ids__contains=[trn_id]).first()
    # SQLite của bộ kiểm không có lookup JSON contains — lọc trong Python (bảng nhóm đổi nhỏ)
    return next((n for n in qs if trn_id in (n.trn_ids or [])), None)


def nap_tu_nhom(g):
    """Mở lại hóa đơn đổi đã có nhóm → điền lại các ô chuyển khoản của lần chốt trước."""
    n = nhom_doi(g.get("trn_id"))
    if n:
        g["ck_bank"], g["ck_stk"], g["ck_ten"], g["ck_nd"] = n.ck_bank, n.ck_stk, n.ck_ten, n.ck_nd
    return n


def kiem_truoc_chot(g):
    """Chặn trước THANH TOÁN. Trả câu lỗi hoặc ''.
    GĐ chốt 19/09/2026 tối: QUẸT THẺ CHƯA ÁP DỤNG (phát triển sau) — đơn cũ còn mang cách 'card' phải chọn lại."""
    if (g.get("pay_method") or "cash") == "card":
        return "Quẹt thẻ chưa áp dụng — chọn Tiền mặt hoặc Chuyển khoản"
    return ""


# ─────────────────────────── FORM IN: khách chuyển khoản VÀO tài khoản tiệm (GĐ chốt 19/09/2026 tối) ───────────────────────────
# Dùng lại NGUYÊN luồng tiền vào đang chạy, không viết đường ghi mới:
#   dòng tiền MoneyFlow (RETAIL, gold_bill_retail) → money_flow_payment.save_instruction tạo MỘT dòng money_flow_payment
#   (BANK, ready, nội dung = mã hóa đơn bỏ gạch: 26-09-19-000195 → 260919000195) → job 30 giây reconcile_pawn_in
#   khớp thông báo ngân hàng VÀO rồi ghi CashPay/CardPay lên KK (money_in, GĐ duyệt 17/09/2026).
def tk_tiem_mac_dinh():
    """Tài khoản nhận mặc định của form IN: gold_bank đang bật có type 'cty'; không có thì TK mặc định cũ."""
    from . import vietqr as QR

    for b in QR.active_banks():
        if "cty" in str(b.get("type") or "").lower():
            return b["id"]
    return QR.default_bank_id()


def noi_dung_in(g):
    """Nội dung CK cố định của form IN = mã hóa đơn chỉ giữ chữ số (khớp money_flow_payment.transfer_content)."""
    return "".join(c for c in (g.get("bill_code") or "") if c.isalnum())[:25]


def _dong_tien_in(g):
    from .models import MoneyFlow

    return MoneyFlow.objects.filter(source_system="KHBL", service="RETAIL", source_type="gold_bill_retail",
                                    source_id=g.get("trn_id") or "", flow_role="settlement").first()


# ─────── ĐỔI PHƯƠNG THỨC SAU KHI THANH TOÁN (GĐ chốt 19/09/2026 tối) ───────
# Đơn đã chốt HÔM NAY vẫn chọn lại được cách trả tiền — đó chỉ là HÌNH THỨC khách trả sau THANH TOÁN, hóa đơn trên
# KK không đổi (don.dong_bo bỏ qua đơn đã chốt). Chỉ ghi MySQL: gold_bill (hình thức trả) + nhóm CK trả khách.
# CashPay/CardPay trên KK chỉ đổi khi thuật toán đối soát xác nhận có tiền (money_in.finish · ghi_ck_kk).
PAY_KEYS = {"pay_method", "tien_mat", "bank_id", "ck_bank", "ck_stk", "ck_ten", "ck_nd", "chuoi", "anh_bqr",
            "csrfmiddlewaretoken"}


def sua_tt_sau_chot(request):
    """Request này có phải CHỈ đổi phương thức trả tiền của đơn ĐÃ CHỐT HÔM NAY không."""
    import datetime
    from . import bill as B, cart

    g = cart.get(request)
    keys = set(request.POST) | set(request.FILES)
    return (g.get("status") == B.CHOT_ROI and (g.get("ngay") or "") == datetime.date.today().isoformat()
            and bool(keys - {"csrfmiddlewaretoken"}) and keys <= PAY_KEYS)


def _nhom_can_ghi_lai(request, g, t):
    from .models import ThauAnhTam

    if ThauAnhTam.objects.filter(session_key=_skey(request), slot="bqr").exists():
        return True
    ck, cu = abs(M.dec(t.get("tien_ck"))), nhom_doi(g.get("trn_id"))
    if cu is None:
        return bool(ck)
    moi = (ck, abs(M.dec(t.get("tien_mat"))), *((g.get(k) or "") if ck else "" for k in ("ck_bank", "ck_stk", "ck_ten")),
           noi_dung(g) if ck else "")
    return moi != (M.dec(cu.tien_ck), M.dec(cu.tien_mat), cu.ck_bank, cu.ck_stk, cu.ck_ten, cu.ck_nd)


def sau_chot_ghi_lai(request, g):
    """Sau khi đổi phương thức trên đơn đã chốt: ghi lại hình thức trả vào gold_bill; tiệm trả khách thì ghi nhóm
    CK mới (chỉ khi khác nhóm hiện tại) để đối soát đọc đúng số và tài khoản mới."""
    from . import bill as B, cart, gold_bill as GB

    try:
        GB.upsert_tu_gio(g, status=B.CHOT_ROI, is_del=False, user=request.user)
    except Exception:
        logger.exception("Ghi lại hình thức trả %s", g.get("trn_id"))
    t = cart.tong_cua(g)
    if tra_khach(t) and _nhom_can_ghi_lai(request, g, t):
        ghi_nhom_sau_chot(request, g, t, g.get("bill_code"))


# ─────── KIỂM LẠI TIỀN TRÊN KK MỖI LẦN BẤM "CHUYỂN KHOẢN" (GĐ chốt 19/09/2026 tối) ───────
# CashPay trên TRN_RT_BUYSELL = số THỰC TẾ còn phải thu/chi (CardPay chỉ được ghi khi đối soát khớp tiền).
# CashPay ≠ 0 → còn phải thu (QR không vượt số này) · CashPay = 0 và CardPay ≠ 0 → XÁC NHẬN XONG: ẩn form, hiện bằng chứng.
def doc_tien_kk(g):
    """Đọc lại MỘT lần PayAmount/CashPay/CardPay của hóa đơn đã chốt trên máy KK (chỉ đọc, NOLOCK, 1 dòng theo mã)
    → g['kk_tien'] (chuỗi, đi qua session). Lỗi đọc thì ghi câu lỗi, không chặn gì."""
    import datetime
    from . import bill as B, services as S

    trn = g.get("trn_id") or ""
    if not trn or g.get("status") != B.CHOT_ROI:
        g.pop("kk_tien", None)
        return None
    luc = datetime.datetime.now().strftime("%H:%M:%S")
    try:
        r = S.client("ck_kiem_tien").query(
            "SELECT PayAmount, CashPay, CardPay FROM TRN_RT_BUYSELL WITH (NOLOCK) WHERE TrnID = ?", (trn,))
    except Exception as exc:
        logger.warning("Đọc tiền KK %s: %s", trn, exc)
        g["kk_tien"] = {"trn": trn, "luc": luc, "loi": "Chưa đọc được máy KK — bấm ⟳ thử lại"}
        return g["kk_tien"]
    if not r:
        g["kk_tien"] = {"trn": trn, "luc": luc, "loi": "Không thấy hóa đơn trên máy KK"}
        return g["kk_tien"]
    x = r[0]
    g["kk_tien"] = {"trn": trn, "luc": luc, "pay": str(M.dec(x["PayAmount"])), "cash": str(M.dec(x["CashPay"])),
                    "card": str(M.dec(x["CardPay"]))}
    return g["kk_tien"]


def tien_kk(g):
    """Số tiền KK vừa đọc cho đúng hóa đơn đang mở: dict {pay, cash, card (Decimal), luc, xong, loi} hoặc None."""
    kk = g.get("kk_tien") or {}
    if not kk or kk.get("trn") != g.get("trn_id"):
        return None
    if kk.get("loi"):
        return {"luc": kk["luc"], "loi": kk["loi"], "xong": False}
    cash, card = M.dec(kk["cash"]), M.dec(kk["card"])
    return {"luc": kk["luc"], "loi": "", "pay": M.dec(kk["pay"]), "cash": cash, "card": card,
            "con": abs(cash), "xong": cash == 0 and card != 0}


def bang_chung(g, ra=False):
    """Các phiên BẰNG CHỨNG đã xác nhận tiền của hóa đơn: tiền VÀO = money_flow_bank_receipt của dòng tiền;
    ra=True (tiệm trả khách) = liên kết giao dịch ngân hàng RA (thau_payment_link còn hiệu lực)."""
    from django.db import connection
    from django.utils import timezone
    from .models import MoneyFlowBankReceipt, ThauPaymentLink

    out = []
    trn = g.get("trn_id") or ""
    if not ra:
        flow = _dong_tien_in(g) if trn else None
        for p in (MoneyFlowBankReceipt.objects.filter(flow=flow).exclude(status="cancelled").order_by("id") if flow else []):
            ev = p.evidence or {}
            out.append({"gio": str(ev.get("transaction_time") or timezone.localtime(p.created_at))[11:16],
                        "amount": p.amount, "ref": p.bank_ref or ev.get("ref_code") or "", "tk": p.bank_account,
                        "nd": str(ev.get("description") or "")[:48], "trang_thai": p.status})
        return out
    qs = ThauPaymentLink.objects.filter(active_notification_id__isnull=False).order_by("id")
    if connection.vendor == "mysql":
        qs = qs.filter(trn_ids__contains=[trn])
    for l in qs if trn else []:
        if trn in (l.trn_ids or []):
            b = l.bank_snapshot or {}
            out.append({"gio": str(b.get("transaction_time") or "")[11:16], "amount": l.amount,
                        "ref": b.get("ref_code") or "", "tk": b.get("bank_number") or "",
                        "nd": str(b.get("description") or "")[:48], "trang_thai": "applied"})
    return out


def qr_in_tinh_trang(g):
    """Khối CK của form IN: DS QR đã tạo THEO NỘI DUNG CK của hóa đơn (✔ khi đã có tiền khớp) · đã nhận · còn lại."""
    from django.utils import timezone
    from .models import MoneyFlowBankReceipt, MoneyFlowPayment

    nd = noi_dung_in(g)
    flow = _dong_tien_in(g) if g.get("trn_id") else None
    ds = list(MoneyFlowPayment.objects.filter(method="BANK", transfer_content=nd).exclude(status="cancelled")
              .order_by("-id")[:30]) if nd else []
    phieu = list(MoneyFlowBankReceipt.objects.filter(flow=flow).exclude(status="cancelled")) if flow else []
    da_nhan = sum((M.dec(p.amount) for p in phieu if p.status == "applied"), M.D0)
    con = [(p.bank_account, M.dec(p.amount)) for p in phieu]          # mỗi khoản tiền vào chỉ đánh ✔ cho MỘT QR
    out = []
    for q in ds:
        khop = (q.bank_account, M.dec(q.amount))
        checked = q.status == MoneyFlowPayment.SUCCESS or khop in con
        if khop in con:
            con.remove(khop)
        out.append({"pk": q.pk, "gio": timezone.localtime(q.created_at).strftime("%H:%M"), "amount": q.amount,
                    "tk": q.bank_account, "status": q.status, "checked": checked})
    tong = M.dec(flow.expected_amount) if flow else M.D0
    return {"ds": out, "da_nhan": da_nhan, "con_lai": max(M.D0, tong - da_nhan) if flow else None}


def qr_in_hien(g):
    """QR IN đã tạo (đang chờ / đã khớp) của hóa đơn — để hiện trong ô hình của form IN. Trả (lệnh, ảnh) hoặc (None, '')."""
    from .models import MoneyFlowPayment
    from . import money_flow_payment as MFP

    flow = _dong_tien_in(g) if g.get("trn_id") else None
    lenh = (MoneyFlowPayment.objects.filter(flow=flow, method="BANK", status__in=["ready", "success"])
            .order_by("-id").first()) if flow else None
    return (lenh, MFP.qr_image(lenh.qr_payload)) if lenh and lenh.qr_payload else (None, "")


def ban_in_qr_xem(request, pk):
    """Popup xem lại MỘT QR đã tạo trong DS QR của form IN (bấm icon 🔳 thay chữ "chờ" — GĐ chốt 19/09/2026 tối).
    Chỉ mở QR mang đúng nội dung CK của hóa đơn đang mở trên màn Bán hàng."""
    from django.shortcuts import render
    from . import cart, money_flow_payment as MFP
    from .models import MoneyFlowPayment

    g = cart.get(request)
    q = MoneyFlowPayment.objects.filter(pk=pk, method="BANK").first()
    if not q or not q.qr_payload or q.transfer_content != noi_dung_in(g):
        return render(request, "pos/_qr_modal.html", {"loi": "QR không thuộc hóa đơn đang mở."})
    return render(request, "pos/_qr_modal.html", {
        "qr_img": MFP.qr_image(q.qr_payload), "bank_ten": q.bank_name or q.bank_code, "bank_num": q.bank_account,
        "bank_user": q.bank_owner, "amount": M.dec(q.amount), "info": q.transfer_content,
        "ten_kh": (g.get("cust") or {}).get("name") or ""})


def ban_in_qr(request):
    """🔳 TẠO QR của form IN — chỉ khi hóa đơn ĐÃ THANH TOÁN, khách trả > 0, có phần chuyển khoản."""
    from decimal import Decimal
    from . import bill as B, cart, money_flow as MF, money_flow_payment as MFP, quyen as Q, vietqr as QR
    from .models import GoldBill

    Q.chan(request, "BAN_HANG")
    V = _views()
    g = cart.get(request)
    t = cart.tong(request)
    if g.get("status") != B.CHOT_ROI:
        return V._loi(request, "THANH TOÁN xong mới tạo QR")
    if M.dec(t.get("khach_tra")) <= 0:
        return V._loi(request, "Hóa đơn này không phải khách trả tiền cho tiệm")
    # GĐ chốt 19/09/2026 tối: TẠO QR bao nhiêu lần cũng được, KHÔNG đổi hóa đơn — số tiền do người bán nhập, chỉ cần
    # ≤ phần còn phải thanh toán (tổng − tiền đã khớp). QR cũ vẫn nhận tiền (money_in gom mọi QR chưa hủy của phiếu).
    so = "".join(c for c in (request.POST.get("qr_tien") or "") if c.isdigit())
    ck = M.dec(so or 0)
    if ck <= 0:
        return V._loi(request, "Nhập số tiền QR lớn hơn 0", {"loi_o": "#o-qrtien"})
    bank_id = str(g.get("bank_id") or tk_tiem_mac_dinh() or "")
    bank = next((b for b in QR.active_banks() if str(b["id"]) == bank_id), None)
    if not bank:
        return V._loi(request, "Chưa có tài khoản nhận của tiệm (gold_bank) đang bật")
    # Sổ IN/OUT chỉ đồng bộ 2 phút/lần → LUÔN chiếu lại đúng hóa đơn này từ gold_bill mới nhất trước khi tạo QR.
    # Sự cố 19/09/2026 20:17: hóa đơn vừa SỬA từ −2.832.000 (tiệm trả, OUT) sang +14.585.000 rồi chốt lại, dòng tiền
    # vẫn là bản OUT cũ → save_instruction đi nhánh CHI, bỏ tài khoản tiệm → "Chưa chọn ngân hàng hợp lệ".
    gb = GoldBill.objects.filter(trn_id=g.get("trn_id")).first()
    if gb and gb.trn_date:
        MF.sync_gold_bills(gb.trn_date, gb.trn_date, trn_ids=[gb.trn_id])
    flow = _dong_tien_in(g)
    if flow is None:
        return V._loi(request, "Chưa có dòng tiền của hóa đơn trên sổ IN/OUT — thử lại sau vài giây")
    if flow.is_void or flow.direction != "IN" or M.dec(flow.expected_amount) != M.dec(t.get("khach_tra")):
        return V._loi(request, f"Sổ IN/OUT chưa khớp hóa đơn ({flow.direction} {M.money_vn(flow.expected_amount)}) — "
                               "hóa đơn vừa sửa? Thử lại sau vài giây")
    con_so = qr_in_tinh_trang(g)["con_lai"] or M.D0      # còn lại theo chứng từ web (save_instruction đòi CK + mặt = số này)
    con_lai = con_so
    doc_tien_kk(g)                              # đọc lại CashPay trên KK ngay trước khi tạo QR
    cart.save(request, g)
    kk = tien_kk(g)
    if kk and not kk["loi"]:
        if kk["xong"]:
            return V._loi(request, "Hóa đơn đã XÁC NHẬN XONG trên máy KK (CashPay = 0) — không cần tạo QR nữa")
        con_lai = min(con_lai, kk["con"])       # CashPay = số thực tế còn phải thu → QR không vượt số này
    if ck > con_lai:
        return V._loi(request, f"Số tiền QR {M.money_vn(ck)} vượt phần còn phải thanh toán {M.money_vn(con_lai)}",
                      {"loi_o": "#o-qrtien"})
    try:
        lenh = MFP.save_instruction(flow, "BANK", request.user, company_bank=bank,
                                    custom_bank=Decimal(ck), custom_cash=Decimal(con_so - ck), note=noi_dung_in(g))
    except ValueError as exc:
        return V._loi(request, f"Chưa tạo được QR: {exc}")
    return V._pos_oob(request, {"tin": f"Đã tạo QR {M.money_vn(lenh.amount)} · {bank.get('bank_number')} · nội dung "
                                       f"{lenh.transfer_content} — tiền vào sẽ tự đối soát"})


def ghi_nhom_sau_chot(request, g, t, bill_code):
    """THANH TOÁN xong → ghi nhóm nghiep_vu='doi' cho hóa đơn khi có một trong ba thứ:
    phần tiệm CHUYỂN KHOẢN cho khách · ảnh chờ (Hình 1 / Hình 2 / QR) · nhóm cũ của chính hóa đơn này.

    · Nhóm luôn mới (bán lại sau SỬA cũng vậy) — đối soát và ảnh đọc nhóm MỚI NHẤT. Đổi sang trả tiền mặt vẫn ghi
      nhóm CK = 0 để số CK cũ không bị đối soát nhầm.
    · Khách trả DƯƠNG (tiệm thu tiền) mà có ảnh vàng đổi: nhóm chỉ giữ ảnh, tiền CK = 0 — đối soát bỏ qua.
    · Ảnh: ảnh chờ nén MIN rồi ghi; ô không có ảnh chờ thì chép từ nhóm cũ (QR chỉ chép khi vẫn đúng số TK).
    Trả nhóm vừa ghi, hoặc None khi không cần.
    """
    from .models import ThauAnhTam

    trn = g.get("trn_id") or ""
    if not trn:
        return None
    tam = {r.slot: bytes(r.data) for r in ThauAnhTam.objects.filter(session_key=_skey(request), slot__in=SLOT_COT)}
    cu = nhom_doi(trn)
    tra = tra_khach(t)
    ck = abs(M.dec(t.get("tien_ck"))) if tra else M.D0
    if not ck and not tam and not cu:
        return None
    cust = g.get("cust") or {}
    tk = {k: (g.get(k) or "") if ck else "" for k in ("ck_bank", "ck_stk", "ck_ten")}
    n = ThauNhom.objects.create(
        nghiep_vu=DOI, trn_ids=[trn], bill_codes=[bill_code or trn], cust_id=cust.get("id") or "",
        cust_name=cust.get("name") or "", emp_id=g.get("emp") or "",
        user=request.user if request.user.is_authenticated else None,
        pay_method=("bank" if not M.dec(t.get("tien_mat")) else "mixed") if ck else "cash",
        tien_mat=abs(M.dec(t.get("tien_mat"))) if tra else M.D0, tien_ck=ck, ghi_chu=(g.get("ghi_chu") or "")[:300],
        ck_nd=noi_dung({**g, "bill_code": g.get("bill_code") or bill_code or ""}) if ck else "", **tk)   # 4 số cuối SỐ HĐ (cùng nguồn với _nhom_can_ghi_lai)
    from .views_thau import _nen_min

    doi = []
    for s, c, _ in BAN_SLOTS:
        col = "anh_" + c
        if s in tam:
            setattr(n, col, _nen_min(tam[s]))
        elif cu and getattr(cu, col) and (c != "qr" or (ck and cu.ck_stk == n.ck_stk)):
            setattr(n, col, bytes(getattr(cu, col)))
        else:
            continue
        doi.append(col)
    if doi:
        n.save(update_fields=doi)
    ThauAnhTam.objects.filter(session_key=_skey(request), slot__in=SLOT_COT).delete()
    return n


# ─────────────────────────── ảnh của hóa đơn đổi (GĐ chốt 19/09/2026) ───────────────────────────
# Hình 1 · Hình 2 (bảng .pg-tb--hinh cạnh bảng vàng đổi) + QR chuyển khoản của khách (ô nhỏ trong khối CK).
# Ảnh CHỜ nằm ở thau_anh_tam theo phiên như Thâu vào, nhưng tên ô riêng (b…) để một trình duyệt mở cả hai trang
# không lẫn ảnh của nhau; THANH TOÁN mới ghi vào thau_nhom.anh_* (ghi_nhom_sau_chot).
BAN_SLOTS = (("bhinh1", "hinh1", "Hình 1"), ("bhinh2", "hinh2", "Hình 2"), ("bqr", "qr", "QR khách"))
SLOT_COT = {s: "anh_" + c for s, c, _ in BAN_SLOTS}


def _skey(request):
    from .views_thau import _skey as sk
    return sk(request)


def anh_o(request, g):
    """Trạng thái 3 ô ảnh cho template: ảnh chờ (phiên) đè ảnh đã ghi của nhóm mới nhất."""
    from django.db.models.functions import Length
    from django.urls import reverse
    from django.utils import timezone
    from .models import ThauAnhTam

    tam = set(ThauAnhTam.objects.filter(session_key=_skey(request), slot__in=SLOT_COT).values_list("slot", flat=True))
    nhom = nhom_doi(g.get("trn_id")) if g.get("trn_id") else None
    co = {}
    if nhom:
        co = ThauNhom.objects.filter(pk=nhom.pk).annotate(**{"n_" + s: Length(c) for s, c in SLOT_COT.items()}) \
            .values(*["n_" + s for s in SLOT_COT]).first() or {}
    v = timezone.localtime().strftime("%H%M%S")
    out = {}
    for s, c, label in BAN_SLOTS:
        d = {"slot": s, "label": label, "tam": s in tam, "src": ""}
        if s in tam:
            d["src"] = f"{reverse('pos:ban_anh')}?slot={s}&v={v}"
        elif co.get("n_" + s):
            d["src"] = f"{reverse('pos:thau_anh')}?slot={c}&nhom={nhom.pk}&v={v}"
        out[s] = d
    return out


def ban_anh_len(request):
    """📷/📁 ô Hình 1 · Hình 2 · QR → ẢNH CHỜ theo phiên. Ô QR chỉ nhận ảnh ĐÚNG là mã chuyển khoản VietQR:
    đọc được thì điền luôn ngân hàng · số TK · tên chủ TK; không phải mã CK thì từ chối, không lưu."""
    from . import cart, vietqr as QR
    from .models import ThauAnhTam
    from .views_thau import _nen_anh

    V = _views()
    sau_chot = V._dang_khoa(request)          # đơn đã chốt: chỉ ô QR khách (đổi phương thức trả sau THANH TOÁN)
    if sau_chot and not sua_tt_sau_chot(request):
        return V._loi(request, V.KHOA_MSG)
    g = cart.get(request)
    n, tin = 0, []
    for s, c, label in BAN_SLOTS:
        f = request.FILES.get("anh_" + s)
        if not f:
            continue
        try:
            f.seek(0)
            goc = f.read()
            data = _nen_anh(goc)
        except Exception as exc:
            logger.warning("Ảnh bán-đổi lỗi: %s", exc)
            return V._loi(request, "Ảnh không hợp lệ: " + str(exc)[:160])
        if s == "bqr":
            try:
                chuoi = QR.doc_anh(goc) or QR.doc_anh(data)
            except RuntimeError as exc:
                return V._loi(request, str(exc))
            if not chuoi:
                return V._loi(request, "Ảnh này không đọc ra mã QR — chọn đúng ảnh mã QR chuyển khoản của khách (chưa lưu)")
            t, loi = quet_qr(g, chuoi)
            if loi:
                return V._loi(request, f"{loi} — ảnh không phải mã chuyển khoản, chưa lưu")
            tin.append(t)
        ThauAnhTam.objects.update_or_create(session_key=_skey(request), slot=s, defaults={"data": data})
        n += 1
    if not n:
        return V._loi(request, "Chưa chọn ảnh nào")
    cart.save(request, g)
    if sau_chot:
        sau_chot_ghi_lai(request, g)
        return V._pos_oob(request, {"tin": " · ".join(tin) + " · đã ghi vào hóa đơn"})
    return V._pos_oob(request, {"tin": " · ".join(tin) or f"Đã nhận {n} ảnh chờ — ghi vào hóa đơn khi THANH TOÁN"})


def ban_anh(request):
    """Phục vụ ẢNH CHỜ của phiên (ảnh đã ghi nhóm đi đường thau_anh ?slot=&nhom=)."""
    from django.http import HttpResponse
    from .models import ThauAnhTam

    slot = (request.GET.get("slot") or "").strip()
    t = ThauAnhTam.objects.filter(session_key=_skey(request), slot=slot).first() if slot in SLOT_COT else None
    if not t:
        return HttpResponse(status=404)
    r = HttpResponse(bytes(t.data), content_type="image/jpeg")
    r["Cache-Control"] = "private, no-store"
    r["X-Content-Type-Options"] = "nosniff"
    return r


def ban_anh_xoa(request):
    """× bỏ ẢNH CHỜ của một ô (ảnh đã ghi vào hóa đơn thì chọn ảnh mới rồi THANH TOÁN lại để thay)."""
    from .models import ThauAnhTam

    V = _views()
    slot = (request.POST.get("slot") or "").strip()
    if slot not in SLOT_COT:
        return V._loi(request, "Ô ảnh không hợp lệ")
    if V._dang_khoa(request):
        return V._loi(request, V.KHOA_MSG)
    ThauAnhTam.objects.filter(session_key=_skey(request), slot=slot).delete()
    return V._pos_oob(request, {"tin": "Đã bỏ ảnh chờ"})


def xoa_anh_cho(request):
    """ĐƠN MỚI: dọn ảnh chờ của màn Bán hàng (không đụng ảnh chờ của trang Thâu vào)."""
    from .models import ThauAnhTam

    ThauAnhTam.objects.filter(session_key=_skey(request), slot__in=SLOT_COT).delete()


def quet_qr(g, chuoi):
    """Điền ngân hàng · số TK · tên chủ TK từ chuỗi VietQR của KHÁCH. Trả (tin, loi).

    Tên chủ TK dò theo đúng 4 lớp của trang Thâu vào (QR → tên đã dùng cho số TK đó → tên đang gõ → gợi ý
    theo tên khách), gọi lại chính hàm của nó chứ không viết lại.
    """
    from . import vietqr as QR
    from .views_thau import _ten_tk_da_biet

    kq = QR.parse(chuoi)
    if not kq["hop_le"]:
        return "", kq["loi"] or "QR không hợp lệ"
    g["ck_bank"], g["ck_stk"] = kq["bank_code"], kq["account"]
    g["ck_nd"] = noi_dung({**g, "ck_nd": ""})
    goi_y = ""
    ten_qr = kq["ten"] or _ten_tk_da_biet(kq["bank_code"], kq["account"])
    if not ten_qr and not (g.get("ck_ten") or "").strip():
        goi_y = QR.khong_dau((g.get("cust") or {}).get("name") or "")[:100]
    g["ck_ten"] = ten_qr or (g.get("ck_ten") or "") or goi_y
    g["pay_method"] = "bank"
    tin = f"QR: {kq['bank_ten']} · {kq['account']}"
    if g["ck_ten"]:
        tin += f" · {g['ck_ten']}" + (" (tên khách — kiểm lại)" if goi_y and g["ck_ten"] == goi_y else "")
    return tin, ""


# ─────────────────────────── màn Bán hàng: 3 view ───────────────────────────
def _views():
    from . import views as V
    return V


def ban_ck_quet(request):
    """Ô Scan QR (máy quét gõ chuỗi + Enter) hoặc ảnh QR của khách → điền khối chuyển khoản cho khách."""
    from . import cart, vietqr as QR

    V = _views()
    sau_chot = V._dang_khoa(request)          # đơn đã chốt hôm nay: vẫn đổi được TK khách nhận (19/09/2026 tối)
    if sau_chot and not sua_tt_sau_chot(request):
        return V._loi(request, V.KHOA_MSG)
    g = cart.get(request)
    chuoi = (request.POST.get("chuoi") or "").strip()
    anh = request.FILES.get("anh")
    if not chuoi and anh:
        try:
            chuoi = QR.doc_anh(anh.read())
        except RuntimeError as exc:
            return V._loi(request, str(exc))
        if not chuoi:
            return V._loi(request, "Không đọc được mã QR trong ảnh — chụp lại rõ, thẳng, đủ 4 góc")
    if not chuoi:
        return V._loi(request, "Chưa có mã QR — quét bằng máy scan hoặc chọn ảnh QR của khách", {"loi_o": "#o-ckscan"})
    tin, loi = quet_qr(g, chuoi)
    if loi:
        return V._loi(request, loi, {"loi_o": "#o-ckscan"})
    cart.save(request, g)
    if sau_chot:
        sau_chot_ghi_lai(request, g)
    return V._pos_oob(request, {"tin": tin})


def _qr_ctx(g, t):
    from . import vietqr as QR

    bank, stk = (g.get("ck_bank") or "").strip(), (g.get("ck_stk") or "").strip()
    nd = noi_dung(g)
    amount = abs(M.dec(t.get("tien_ck")))
    chuoi = QR.payload(bank, stk, amount, nd, g.get("ck_ten") or "")
    return chuoi, {"bank_ten": QR.bank_ten(bank) if QR.bank_bin(bank) else bank, "bank_num": stk,
                   "bank_user": g.get("ck_ten") or "", "amount": amount, "info": nd,
                   "ten_kh": (g.get("cust") or {}).get("name") or ""}


def ban_ck_qr(request):
    """Popup QR CHUYỂN KHOẢN CHO KHÁCH (tiệm trả): TK của khách + số tiền CK + nội dung chuẩn."""
    from django.shortcuts import render
    from . import bill as B, cart
    from .views_thau import _qr_png_b64

    g = cart.get(request)
    t = cart.tong(request)
    if g.get("status") != B.CHOT_ROI:
        return render(request, "pos/_qr_modal.html", {"loi": "Hóa đơn chưa THANH TOÁN — chốt xong (có mã hóa đơn) mới tạo QR."})
    if not tra_khach(t) or not M.dec(t.get("tien_ck")):
        return render(request, "pos/_qr_modal.html", {"loi": "Hóa đơn này không có phần tiệm chuyển khoản cho khách."})
    try:
        chuoi, ctx = _qr_ctx(g, t)
    except ValueError as exc:
        return render(request, "pos/_qr_modal.html", {"loi": str(exc)})
    ctx.update(qr_img="data:image/png;base64," + _qr_png_b64(chuoi), luu_url="/banle/ban-hang/ck-khach/qr/luu/")
    return render(request, "pos/_qr_modal.html", ctx)


def ban_ck_qr_luu(request):
    """LƯU hình QR vừa tạo vào ô 'QR chuyển khoản' của nhóm CK của hóa đơn (như nút LƯU bên Thâu vào)."""
    import base64
    from . import bill as B, cart
    from .views_thau import _nen_anh, _qr_png_b64

    V = _views()
    g = cart.get(request)
    t = cart.tong(request)
    nhom = nhom_doi(g.get("trn_id"))
    if g.get("status") != B.CHOT_ROI or nhom is None or not nhom.tien_ck:
        return V._loi(request, "Hóa đơn chưa THANH TOÁN hoặc không có phần chuyển khoản cho khách", {"dong_modal": True})
    try:
        chuoi, ctx = _qr_ctx(g, t)
    except ValueError as exc:
        return V._loi(request, str(exc), {"dong_modal": True})
    png = base64.b64decode(_qr_png_b64(chuoi, scale=8))
    nhom.anh_qr = _nen_anh(png, canh=800, chat_luong=90)
    nhom.ck_bank, nhom.ck_stk, nhom.ck_nd, nhom.ck_ten = (g.get("ck_bank") or "", g.get("ck_stk") or "",
                                                         ctx["info"], g.get("ck_ten") or "")
    nhom.save(update_fields=["anh_qr", "ck_bank", "ck_stk", "ck_nd", "ck_ten"])
    return V._pos_oob(request, {"tin": "Đã lưu hình QR chuyển khoản cho khách vào hóa đơn", "dong_modal": True})


# ─────────────────────────── ghi CK lên máy KK sau đối soát ───────────────────────────
def ghi_ck_kk(order):
    """Đặt CK trên KK = TỔNG liên kết ngân hàng còn hiệu lực (GĐ chốt 29/09/2026: cộng dồn). Trả True khi vừa ghi.

    · Hóa đơn BÁN-ĐỔI: CardPay TRN_RT_BUYSELL = −tổng (phương án a, 19/09/2026).
    · PHIẾU THÂU cách mới (order['ck_sau']): CardPay/CashPay từng dòng TRN_RT_BUYGOLD của nhóm (thống nhất như bán).
    Chỉ chạy khi nhóm không có vấn đề. CK hiện tại trên KK phải là số DO ĐỐI SOÁT GHI (0 / từng khoản / tổng cộng dồn)
    — con số lạ (ai đó sửa tay trên PMV) thì DỪNG, báo kiểm tra.
    """
    from apps.pmv import gateway
    from .thau_payments import muc_kk_hop_le

    if order.get("problems"):
        return False
    paid = Decimal(order.get("paid") or 0)
    links = order.get("links") or []
    if order.get("nghiep_vu") == DOI and len(order.get("ids") or []) == 1:
        snap = gateway.pmv_out_snapshot_retail(order["ids"][0])
        hien = -Decimal(snap["CardPay"] or 0)
        if hien == paid:
            return False
        if hien not in muc_kk_hop_le(links, paid):
            raise ValueError(f"CK trên KK ({hien}) không do đối soát ghi — cần kiểm tra tay.")
        if paid > -Decimal(snap["PayAmount"]):
            raise ValueError("Tổng đã đối soát lớn hơn số tiệm trả khách.")
        gateway.pmv_out_allocate_retail(snap, -paid)
        return True
    if order.get("ck_sau") and order.get("ids"):
        snap = gateway.pmv_out_snapshot_buygold(order["ids"])
        hien = sum((-Decimal(r["CardPay"] or 0) for r in snap), Decimal(0))
        muc = gateway.chia_ck_thau(snap, paid)          # kiểm luôn: tổng CK không vượt tổng nhóm
        if all(-Decimal(r["CardPay"] or 0) == m for r, m in zip(snap, muc)):
            return False
        if hien not in muc_kk_hop_le(links, paid):
            raise ValueError(f"CK trên KK ({hien}) không do đối soát ghi — cần kiểm tra tay.")
        gateway.pmv_out_allocate_buygold(snap, paid)
        return True
    return False


def dong_bo_ck_kk(orders, chi_ids=None):
    """Chạy ghi_ck_kk cho mọi nhóm bán-đổi + phiếu thâu cách mới (hoặc chỉ nhóm chứa chi_ids). Lỗi từng nhóm chỉ ghi
    log — lượt chạy ngầm 5 phút sau sẽ thử lại. Trả (số nhóm vừa ghi, danh sách lỗi)."""
    xong, loi = 0, []
    for od in orders:
        if od.get("nghiep_vu") != DOI and not od.get("ck_sau"):
            continue
        if chi_ids and not set(chi_ids) & set(od.get("ids") or []):
            continue
        try:
            xong += bool(ghi_ck_kk(od))
        except Exception as exc:
            logger.warning("Ghi CK lên KK %s: %s", od.get("ids"), exc)
            loi.append(f"{','.join(od.get('ids') or [])}: {exc}")
    return xong, loi
