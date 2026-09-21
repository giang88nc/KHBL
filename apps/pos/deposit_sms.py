# -*- coding: utf-8 -*-
"""GỬI SMS (ZNS Zalo) CHO PHIẾU ĐẶT CỌC — chọn phiếu → xếp tin CHỜ vào sổ ``khj_bl.zalo_messages``.

GĐ chốt 19/09/2026 (khung) · 20/09/2026 (lịch nhắc, Xem/XÓA):
  Trang Đặt-cọc, nút "📱 GỬI SMS" (trước Báo cáo) → popup 90% màn hình, thân 2 cột:
    DS CHỌN — phiếu cọc tiến độ "Hàng sẵn sàng" TỚI HẠN một mức nhắc → tick → TẠO SMS.
    DS CHỜ  — tin của nguồn này trong sổ → XEM (thẻ tin theo mẫu, đúng biến thật) · XÓA (xóa hẳn dòng → phiếu
              trở lại DS CHỌN). Tin ĐÃ GỬI / đang gửi không xóa được (giữ dấu vết).
  LỊCH NHẮC — phiếu có tiến độ SẴN SÀNG (fulfilment 'ready'), "quá hẹn" = hôm nay − ngày hẹn:
      san_sang  Thông báo hàng sẵn sàng — ngay khi phiếu sang SẴN SÀNG, không chờ tới ngày hẹn
      nhac1     Nhắc lần 1 — quá hẹn ≥ 10 ngày
      nhac2     Nhắc lần 2 — quá hẹn ≥ 20 ngày
      nhac3     Nhắc lần 3 — quá hẹn ≥ 30 ngày
    Đề xuất MỘT tin ở mức cao nhất đã tới hạn mà CHƯA xử lý (nhảy cóc không bắn bù các mức dưới). Phiếu vừa có tin
    (chờ/đã gửi) trong 10 ngày thì KHÔNG đề xuất lại. Tin hủy / lỗi / bị xóa không tính là đã xử lý.
  Mẫu ZNS **635720 "Thông báo hàng sẵn sàng"** dùng cho cả 4 mức: bill_code (che giữa) · customer_name · anh_chi.

HỢP ĐỒNG DÒNG zalo_messages — chép khuôn KHCD ``khcd/sms.py`` và ``apps/oa/xep_hang.py``:
    source_type 'khbl_deposit' · channel 'phone' · status 'queued' · send_rule_id NULL · source_status = MỨC ·
    băm SĐT sha256("care360-zbs|84…") · masked 3***3 · recipient_ciphertext 'khbl_no_cipher' ·
    dedupe_key sha256("1|khbl_deposit|{TrnID}|{mức}") — mức sẵn sàng giữ chữ 'hang_san_sang' (tương thích tin cũ).
⚠ MỌI câu ghi/xóa ở đây đều lọc ``source_type='khbl_deposit'`` — không đụng dòng nguồn khác.
⚠ Tệp này KHÔNG GỌI MẠNG. Tin chỉ nằm ở DS CHỜ; việc gửi thật là của bộ gửi ZNS theo ``scheduled_at``.
⚠ NGUY CƠ GỬI ĐÔI (CLAUDE.md mục 4g): đường gọi thẳng API Zalo ``deposit_messages.py`` cũng làm "hàng sẵn sàng".
  Tệp này KHÔNG đụng đường đó. Trước khi bật bộ gửi cho mẫu 635720 phải chốt bỏ một trong hai đường.
CÔNG TẮC: ``STOP_ZNS.flag`` + đích dữ liệu phải là **kk**. Không áp ``ZNS_XEP_HANG`` (công tắc xếp hàng TỰ ĐỘNG).
GIỜ: sổ zalo_messages lưu GIỜ VN naive từ 20/09/2026 (``apps/oa/models.GioVNField``) — ở đây vẫn gán datetime AWARE,
hiển thị qua ``timezone.localtime`` (giờ VN); không cộng/trừ tay 7 giờ.
"""
import hashlib
import json
import logging
import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from django.db import IntegrityError, transaction
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from apps.pmv import money as M

log = logging.getLogger(__name__)

NGUON = "khbl_deposit"
MAU = "635720"
OA_ID = 1
KHONG_MA_HOA = "khbl_no_cipher"
GIO_VN = ZoneInfo("Asia/Ho_Chi_Minh")
GIO_TU, GIO_DEN, GIO_MAC_DINH = 8, 20, 9          # khung giờ gửi — cùng mặc định trang Gửi SMS của Cầm đồ
XUNG_HO_MAC_DINH = "Anh/Chị"
# (khóa, số ngày QUÁ HẸN tối thiểu, nhãn) — từ nhẹ → nặng. None = không cần tới ngày hẹn.
MUCS = [("san_sang", None, "Thông báo sẵn sàng"), ("nhac1", 10, "Nhắc lần 1"),
        ("nhac2", 20, "Nhắc lần 2"), ("nhac3", 30, "Nhắc lần 3")]
THU_TU = {k: i for i, (k, _, _) in enumerate(MUCS)}
NHAN_MUC = {k: n for k, _, n in MUCS}
AN_SAU_NHAC_NGAY = 10                              # phiếu vừa có tin → không đề xuất lại trong 10 ngày
# Xưng hô suy từ tên (chép mặc định của KHCD, GĐ chốt 18/09/2026) — so khớp đúng dấu, không phân biệt hoa/thường
TIEN_TO_CHI, TIEN_TO_ANH = ("chị", "cô", "bà", "dì"), ("anh", "chú", "cậu", "bác", "ông")
LOT_CHI, LOT_ANH = ("thị", "mỹ"), ("văn", "tấn")
TRANG_THAI_TIN = {"cho": ("queued", "retry", "sending"), "gui": ("sent", "delivered", "seen"),
                  "loi": ("failed", "failed_permanent"), "huy": ("cancelled",)}
CON_HIEU_LUC = TRANG_THAI_TIN["cho"] + TRANG_THAI_TIN["gui"]
KHONG_XOA = TRANG_THAI_TIN["gui"] + ("sending",)
NHAN_TIN = {"queued": "Chờ gửi", "retry": "Chờ gửi lại", "sending": "Đang gửi", "sent": "Đã gửi",
            "delivered": "Đã nhận", "seen": "Đã xem", "failed": "Lỗi", "failed_permanent": "Lỗi",
            "cancelled": "Đã hủy"}
# Lọc theo TIẾN ĐỘ (fulfilment — cùng nhãn cột trạng thái trang Đặt-cọc), KHÔNG theo Status của PMV: GĐ chốt 20/09/2026
# "Hàng sẵn sàng" chỉ là tiến độ 'ready' (có ở cả phiếu R/W/P); phiếu R mà tiến độ 'delivered' = "Hoàn thành - đặt" không lấy.
TRANG_THAI_PHIEU = [("ready", "Hàng sẵn sàng"), ("waiting", "Đơn mới"), ("ordering", "Đang đặt hàng"),
                    ("partial", "Giao một phần"), ("delivered", "Hoàn thành - đặt"), ("", "Tất cả")]


# ─────────────────────────── tiện ích (chép khuôn KHCD) ───────────────────────────
def _tu(ten):
    return " ".join(str(ten or "").split()).split(" ") if str(ten or "").strip() else []


def anh_chi_cua(gioi_tinh, ten=None):
    """Tiền tố đầu tên → chữ lót ở giữa (tên ≥ 3 từ) → Gender=True mới tin là Anh → mặc định 'Anh/Chị'."""
    tu = [w.casefold() for w in _tu(ten)]
    if len(tu) >= 2:
        if tu[0] in TIEN_TO_CHI:
            return "Chị"
        if tu[0] in TIEN_TO_ANH:
            return "Anh"
    if len(tu) >= 3:
        for w in tu[1:-1]:
            if w in LOT_CHI:
                return "Chị"
            if w in LOT_ANH:
                return "Anh"
    return "Anh" if gioi_tinh is True else XUNG_HO_MAC_DINH


def ten_khong_tien_to(ten):
    tu = _tu(ten)
    if len(tu) >= 2 and tu[0].casefold() in TIEN_TO_CHI + TIEN_TO_ANH:
        tu = tu[1:]
    return " ".join(tu)


def che_ma(ma):
    """Giữ 2 ký tự đầu + 5 ký tự cuối, che phần giữa (tin tới nhầm số không lộ trọn mã — GĐ chốt 18–20/09/2026)."""
    ma = str(ma or "").strip()
    if len(ma) <= 7:
        return "*" * max(0, len(ma) - 3) + ma[-3:]
    return ma[:2] + "*" * (len(ma) - 7) + ma[-5:]


def dedupe(trn, muc="san_sang"):
    chu = "hang_san_sang" if muc == "san_sang" else muc          # giữ khóa của tin sẵn sàng tạo trước 20/09
    return hashlib.sha256(f"{OA_ID}|{NGUON}|{trn}|{chu}".encode("utf-8")).hexdigest()


def muc_cua_tin(m):
    """Mức của một dòng tin (source_status). Tin tạo trước 20/09 mang Status phiếu → coi là mức sẵn sàng."""
    return m.source_status if m.source_status in THU_TU else "san_sang"


def mau_zns():
    from apps.oa.models import ZaloTemplate
    return ZaloTemplate.objects.filter(template_id=MAU).first()


def duoc_ghi():
    """(được_ghi, lý_do) — STOP_ZNS.flag + đích dữ liệu phải là kk."""
    from apps.oa import xep_hang as XH
    if XH._co_dung_khan():
        return False, "Đang DỪNG KHẨN tin nhắn (STOP_ZNS.flag) — chưa tạo được tin."
    dich = XH._dich_hien_tai()
    if dich != "kk":
        return False, f"Đích dữ liệu đang là '{dich}', không phải máy KK — không ghi vào sổ tin thật."
    return True, ""


def gio_mac_dinh(now=None):
    """Bây giờ + 5 phút nếu còn trong khung giờ gửi; ngoài khung → 9:00 gần nhất (giờ VN)."""
    n = timezone.localtime(now) if now else timezone.localtime()
    thu = (n + timedelta(minutes=5)).replace(second=0, microsecond=0)
    if GIO_TU <= thu.hour < GIO_DEN:
        return thu
    moc = n.replace(hour=GIO_MAC_DINH, minute=0, second=0, microsecond=0)
    return moc if n < moc else moc + timedelta(days=1)


def doc_gio(chuoi):
    """'2026-09-19T15:30' (datetime-local, giờ VN) → datetime aware; kiểm khung giờ + không ở quá khứ."""
    try:
        gio = datetime.fromisoformat(str(chuoi or "").strip()).replace(tzinfo=GIO_VN)
    except ValueError:
        raise ValueError("Giờ gửi không hợp lệ.")
    if not (GIO_TU <= gio.hour < GIO_DEN):
        raise ValueError(f"Giờ gửi phải trong {GIO_TU}:00–{GIO_DEN}:00.")
    if gio < timezone.now() - timedelta(minutes=1):
        raise ValueError("Giờ gửi đã qua.")
    return gio


# ─────────────────────────── dữ liệu + lịch nhắc ───────────────────────────
def phieu_coc():
    """Toàn bộ phiếu cọc — CÙNG nguồn với danh sách trang Đặt-cọc (snapshot đọc từ máy KK, có bộ nhớ đệm)."""
    from apps.pmv.client import PmvClient
    from . import deposit_workspace as W
    from .deposit_operations import overlay

    c = PmvClient(tag="datcoc-sms")
    return W.classify(overlay(W.prepare(W.read_snapshot(c)), c.target))


def tin_theo_phieu(trn_ids):
    """{TrnID: [các dòng tin nguồn khbl_deposit, cũ → mới]}."""
    from apps.oa.models import ZaloMessage
    out = {}
    if trn_ids:
        for m in ZaloMessage.objects.filter(source_type=NGUON, trn_id__in=list(trn_ids)).order_by("id"):
            out.setdefault(m.trn_id, []).append(m)
    return out


def xet_lich(r, tin, hom_nay=None, bay_gio=None):
    """Xét lịch nhắc cho MỘT phiếu. Trả dict: muc (mức sẽ gửi hoặc '') · qua_han · ly_do (vì sao chưa đề xuất)."""
    hom_nay = hom_nay or timezone.localdate()
    bay_gio = bay_gio or timezone.now()
    hen = r.get("promise_date")
    if hasattr(hen, "date"):
        hen = hen.date()
    qua = (hom_nay - hen).days if hen else None
    if r.get("fulfilment") != "ready":
        return {"muc": "", "qua_han": qua, "ly_do": "Chưa phải tiến độ Hàng sẵn sàng"}
    toi = "san_sang"
    for k, nguong, _ in MUCS[1:]:
        if nguong is not None and qua is not None and qua >= nguong:
            toi = k
    song = [m for m in tin if m.status in CON_HIEU_LUC]
    da = max((THU_TU[muc_cua_tin(m)] for m in song), default=-1)
    moi_nhat = max((m.created_at for m in song if m.created_at), default=None)
    if moi_nhat and moi_nhat > bay_gio - timedelta(days=AN_SAU_NHAC_NGAY):
        het = timezone.localtime(moi_nhat + timedelta(days=AN_SAU_NHAC_NGAY))
        return {"muc": "", "qua_han": qua, "ly_do": f"Vừa có tin {timezone.localtime(moi_nhat):%d/%m} — ẩn tới {het:%d/%m}"}
    if THU_TU[toi] <= da:
        ke = next((f"{n} khi quá hẹn {ng} ngày" for k, ng, n in MUCS if THU_TU[k] == da + 1), "")
        return {"muc": "", "qua_han": qua, "ly_do": f"Đã xử lý {NHAN_MUC[MUCS[da][0]]}" + (f" — kế tiếp: {ke}" if ke else "")}
    return {"muc": toi, "qua_han": qua, "ly_do": ""}


def ung_vien(p):
    """DS CHỌN: tt (tiến độ, mặc định 'ready') · d1/d2 (ngày đặt) · q · tin ('can' = chỉ phiếu TỚI HẠN một mức nhắc)."""
    rows = phieu_coc()
    tt, q = p.get("tt", "ready"), (p.get("q") or "").strip().casefold()
    d1, d2, tin_loc = p.get("d1") or "", p.get("d2") or "", p.get("tin", "can")
    chon = []
    for r in rows:
        if tt and r.get("fulfilment") != tt:
            continue
        ngay = r.get("TrnDate")
        ngay_s = ngay.strftime("%Y-%m-%d") if hasattr(ngay, "strftime") else str(ngay or "")[:10]
        if (d1 and ngay_s < d1) or (d2 and ngay_s > d2):
            continue
        if q and not any(q in str(r.get(k) or "").casefold() for k in ("TrnID", "BillCode", "CustName", "Phone")):
            continue
        chon.append(r)
    tin = tin_theo_phieu([r["TrnID"] for r in chon])
    hom_nay, bay_gio = timezone.localdate(), timezone.now()
    from apps.oa import xep_hang as XH
    out = []
    for r in chon:
        ds = tin.get(r["TrnID"], [])
        lich = xet_lich(r, ds, hom_nay, bay_gio)
        if tin_loc == "can" and not lich["muc"]:
            continue
        try:
            XH.chuan_hoa_sdt(r.get("Phone"))
            sdt_ok = True
        except ValueError:
            sdt_ok = False
        cuoi = ds[-1] if ds else None
        out.append({"trn": r["TrnID"], "ma": r.get("BillCode") or r["TrnID"], "ngay": r.get("TrnDate"),
                    "khach": r.get("CustName") or "", "sdt": r.get("Phone") or "", "sdt_ok": sdt_ok,
                    "trang_thai": r.get("state_name") or r.get("Status"), "hen": r.get("promise_date"),
                    "qua_han": lich["qua_han"], "muc": lich["muc"], "nhan_muc": NHAN_MUC.get(lich["muc"], ""),
                    "ly_do": lich["ly_do"] or ("" if sdt_ok else "Không có SĐT di động hợp lệ"),
                    "tin": f"{NHAN_MUC[muc_cua_tin(cuoi)]} · {NHAN_TIN.get(cuoi.status, cuoi.status)}" if cuoi else "",
                    "chon_duoc": bool(lich["muc"]) and sdt_ok})
    out.sort(key=lambda x: (-(x["qua_han"] or -9999), str(x["ngay"] or "")), reverse=False)
    return out


def ds_cho(p):
    """DS CHỜ: tin của nguồn này — ctt ('cho' mặc định · gui · loi · huy · '' tất cả) · cq tìm."""
    from apps.oa.models import ZaloMessage
    qs = ZaloMessage.objects.filter(source_type=NGUON)
    ctt = p.get("ctt", "cho")
    if ctt in TRANG_THAI_TIN:
        qs = qs.filter(status__in=TRANG_THAI_TIN[ctt])
    cq = (p.get("cq") or "").strip().casefold()
    out = []
    for m in qs.order_by("scheduled_at", "id")[:300]:
        if cq and not any(cq in str(v or "").casefold() for v in (m.trn_id, m.bill_code, m.customer_name, m.recipient_masked)):
            continue
        out.append({"id": m.pk, "gio": timezone.localtime(m.scheduled_at) if m.scheduled_at else None, "trn": m.trn_id,
                    "ma": m.bill_code, "khach": m.customer_name, "sdt": m.recipient_masked,
                    "muc": NHAN_MUC[muc_cua_tin(m)], "status": m.status, "nhan": NHAN_TIN.get(m.status, m.status),
                    "xoa_duoc": m.status not in KHONG_XOA, "ly_do": m.cancel_reason or m.error_message or ""})
    return out


def _gioi_tinh(cust_ids):
    """Gender của khách (I_CUSTOMER, chỉ đọc) — chỉ dùng khi tên không tự nói được xưng hô."""
    from apps.pmv.client import PmvClient
    ids = [c for c in dict.fromkeys(cust_ids) if c]
    if not ids:
        return {}
    try:
        rows = PmvClient(tag="datcoc-sms").query(
            f"SELECT CustID, Gender FROM I_CUSTOMER WITH (NOLOCK) WHERE CustID IN ({','.join('?' * len(ids))})", tuple(ids))
    except Exception:
        log.warning("Không đọc được giới tính khách để xưng hô", exc_info=True)
        return {}
    return {r["CustID"]: (None if r.get("Gender") is None else bool(r["Gender"])) for r in rows}


def tao(trn_ids, gio, user):
    """Xếp tin cho các phiếu đã chọn, mỗi phiếu ĐÚNG mức đang tới hạn. Trả (tạo mới, mở lại, [lý do bỏ qua])."""
    from apps.oa import xep_hang as XH
    from apps.oa.models import ZaloMessage

    ok, ly_do = duoc_ghi()
    if not ok:
        raise ValueError(ly_do)
    mau = mau_zns()
    if mau is None:
        raise ValueError(f"Chưa có mẫu ZNS {MAU} trong zalo_templates.")
    if not trn_ids:
        raise ValueError("Chưa chọn phiếu nào.")
    theo_trn = {r["TrnID"]: r for r in phieu_coc()}
    tin = tin_theo_phieu(trn_ids)
    gioi = _gioi_tinh([theo_trn[t].get("CustID") for t in trn_ids if t in theo_trn])
    bay_gio = timezone.now()
    them = mo = 0
    bo = []
    for trn in trn_ids:
        r = theo_trn.get(trn)
        if not r:
            bo.append(f"{trn}: không còn trong danh sách phiếu cọc")
            continue
        ma = r.get("BillCode") or trn
        lich = xet_lich(r, tin.get(trn, []), bay_gio=bay_gio)
        if not lich["muc"]:
            bo.append(f"{ma}: {lich['ly_do']}")
            continue
        muc = lich["muc"]
        try:
            _digits, local, masked, ph = XH.chuan_hoa_sdt(r.get("Phone"))
        except ValueError:
            bo.append(f"{ma}: không có SĐT di động hợp lệ")
            continue
        bien = {"bill_code": che_ma(ma)[:30],
                "customer_name": (ten_khong_tien_to(r.get("CustName")) or "Khách hàng")[:30],
                "anh_chi": anh_chi_cua(gioi.get(r.get("CustID")), r.get("CustName"))[:30]}
        bien_json = XH._json(bien)
        khoa = dedupe(trn, muc)
        cu = ZaloMessage.objects.filter(dedupe_key=khoa, source_type=NGUON).first()
        if cu:      # cùng mức từng hủy / lỗi → mở lại CHÍNH dòng cũ (dedupe_key UNIQUE); đã gửi thì xet_lich đã chặn
            ZaloMessage.objects.filter(pk=cu.pk, source_type=NGUON).exclude(status__in=KHONG_XOA).update(
                status="queued", scheduled_at=gio, template_data_json=bien_json, customer_name=bien["customer_name"],
                customer_phone=local, recipient_masked=masked, recipient_hash=ph, source_status=muc, attempt_count=0,
                next_retry_at=None, error_code=None, error_message=None, cancelled_at=None, cancel_reason=None,
                created_at=bay_gio, updated_at=bay_gio)
            mo += 1
            continue
        ngay = r.get("TrnDate")
        try:
            giao_dich = datetime.combine(ngay.date() if hasattr(ngay, "date") else ngay,
                                         datetime.strptime(str(r.get("TrnTime") or "00:00:00")[:8], "%H:%M:%S").time()
                                         ).replace(tzinfo=GIO_VN)
        except (TypeError, ValueError):
            giao_dich = None
        try:
            with transaction.atomic():
                ZaloMessage.objects.create(
                    oa_account_id=OA_ID, template_id=mau.pk, send_rule=None, external_template_id=MAU, channel="phone",
                    source_type=NGUON, source_ref=trn[:100], trn_id=trn[:30], bill_code=ma[:100],
                    cust_id=str(r.get("CustID") or "")[:50], customer_name=bien["customer_name"], customer_phone=local,
                    emp_id=str(r.get("EmpID") or "")[:30] or None, employee_name=str(r.get("EmpName") or "")[:200] or None,
                    transaction_at=giao_dich, eligible_at=bay_gio, source_status=muc,
                    source_eligible=True, pay_amount=M.dec(r.get("TienCoc")), recipient_ciphertext=KHONG_MA_HOA,
                    recipient_masked=masked, recipient_hash=ph, template_data_json=bien_json,
                    tracking_id=uuid.uuid4().hex[:32], dedupe_key=khoa, status="queued", scheduled_at=gio,
                    attempt_count=0, estimated_cost=mau.price_sdt,
                    rule_snapshot_json=XH._json({"rule_name": f"{NGUON}_{muc}", "template_id": mau.pk,
                                                 "external_template_id": MAU, "qua_han": lich["qua_han"]}),
                    created_by=getattr(user, "pk", None), created_at=bay_gio, updated_at=bay_gio)
            them += 1
        except IntegrityError:
            bo.append(f"{ma}: vừa có tin khác cùng phiếu (thử lại sau)")
    log.info("GUI SMS dat coc: user=%s them=%s mo_lai=%s bo=%s", getattr(user, "username", ""), them, mo, len(bo))
    return them, mo, bo


def xoa(mid, user):
    """XÓA HẲN một tin của nguồn này (GĐ chốt 20/09/2026) → phiếu trở lại DS CHỌN. Tin đã gửi / đang gửi: không xóa."""
    from apps.oa.models import ZaloMessage
    m = ZaloMessage.objects.filter(pk=mid, source_type=NGUON).first()
    if not m:
        raise ValueError("Không thấy tin — tải lại danh sách.")
    if m.status in KHONG_XOA:
        raise ValueError("Tin đã gửi / đang gửi — không xóa được (giữ dấu vết đối soát).")
    n, _ = ZaloMessage.objects.filter(pk=mid, source_type=NGUON).exclude(status__in=KHONG_XOA).delete()
    if not n:
        raise ValueError("Tin vừa đổi trạng thái — tải lại danh sách.")
    log.warning("XOA tin dat coc #%s %s (%s) boi %s", mid, m.trn_id, m.status, getattr(user, "username", ""))


def the_tin(m=None):
    """Dữ liệu thẻ XEM: m=None → mẫu (biến để trống, hiện tên biến); có m → đúng nội dung sẽ gửi."""
    if m is None:
        return {"la_mau": True, "anh_chi": "<anh_chi>", "customer_name": "<customer_name>", "bill_code": "<bill_code>"}
    try:
        bien = json.loads(m.template_data_json or "{}")
    except ValueError:
        bien = {}
    return {"la_mau": False, "anh_chi": bien.get("anh_chi", ""), "customer_name": bien.get("customer_name", ""),
            "bill_code": bien.get("bill_code", ""), "sdt": m.recipient_masked, "ma": m.bill_code, "muc": NHAN_MUC[muc_cua_tin(m)],
            "gio": timezone.localtime(m.scheduled_at) if m.scheduled_at else None, "nhan": NHAN_TIN.get(m.status, m.status)}


# ─────────────────────────── view ───────────────────────────
def _ctx(request, p, tin_bao="", loi=""):
    from .deposits import allowed
    ctx = {"p": p, "tin_bao": tin_bao, "loi": loi, "mau": mau_zns(), "trang_thai_phieu": TRANG_THAI_PHIEU,
           "can_edit": allowed(request.user, "can_edit"), "mucs": MUCS, "an_ngay": AN_SAU_NHAC_NGAY,
           "gio_mac_dinh": gio_mac_dinh().strftime("%Y-%m-%dT%H:%M"), "gio_tu": GIO_TU, "gio_den": GIO_DEN,
           "cho_ghi": duoc_ghi()}
    try:
        ctx["ung_vien"] = ung_vien(p)
    except Exception:
        log.exception("Không tải được phiếu cọc cho Gửi SMS")
        ctx["ung_vien"], ctx["loi"] = [], (loi or "Không tải được danh sách phiếu cọc từ máy KK.")
    ctx["ds_cho"] = ds_cho(p)
    return ctx


def _tham_so(request):
    src = request.POST if request.method == "POST" else request.GET
    return {"tt": src.get("tt", "ready"), "d1": src.get("d1", ""), "d2": src.get("d2", ""), "q": src.get("q", ""),
            "tin": src.get("tin", "can"), "ctt": src.get("ctt", "cho"), "cq": src.get("cq", "")}


@require_GET
def popup(request):
    from .deposits import authorize
    authorize(request)
    return render(request, "pos/_dat_coc_sms.html", _ctx(request, _tham_so(request)))


@require_POST
def tao_view(request):
    from .deposits import authorize
    authorize(request, "can_edit")
    p = _tham_so(request)
    try:
        them, mo, bo = tao(request.POST.getlist("trn"), doc_gio(request.POST.get("gio")), request.user)
    except ValueError as exc:
        return render(request, "pos/_dat_coc_sms.html", _ctx(request, p, loi=str(exc)))
    tin = f"Đã xếp {them} tin mới" + (f", mở lại {mo} tin" if mo else "") + " vào DS CHỜ."
    return render(request, "pos/_dat_coc_sms.html", _ctx(request, p, tin_bao=tin, loi=" · ".join(bo)))


@require_POST
def xoa_view(request, mid):
    from .deposits import authorize
    authorize(request, "can_edit")
    p = _tham_so(request)
    try:
        xoa(mid, request.user)
    except ValueError as exc:
        return render(request, "pos/_dat_coc_sms.html", _ctx(request, p, loi=str(exc)))
    return render(request, "pos/_dat_coc_sms.html", _ctx(request, p, tin_bao="Đã xóa tin — phiếu trở lại DS CHỌN."))


@require_GET
def xem_view(request, mid=0):
    """Thẻ XEM nội dung tin (mid) hoặc MẪU (mid=0), dựng theo bố cục mẫu ZNS 635720."""
    from apps.oa.models import ZaloMessage
    from .deposits import authorize
    authorize(request)
    m = ZaloMessage.objects.filter(pk=mid, source_type=NGUON).first() if mid else None
    return render(request, "pos/_dat_coc_sms_the.html", {"t": the_tin(m)})
