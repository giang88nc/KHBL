# -*- coding: utf-8 -*-
"""XẾP HÀNG ZNS — KHBL TỰ DỰNG DS CHỜ GỬI + LỊCH GỬI. CHƯA CÓ LỚP GỌI API OA.

═══════════════════════════════════════════════════════════════════════════════════════════════
GIÁM ĐỐC CHỐT 14/09/2026 (bản SAU CÙNG — thay bản "sổ đối soát" trước đó):

    · CARE360 vẫn cho hoạt động bình thường, KHÔNG tác động đến. (sẽ OFF nếu KHBL hoàn thiện)
    · KHBL sẽ cho ra DS (mỗi khi hoàn thành HĐ) + lịch gửi GIỐNG TƯƠNG TỰ CARE360, nhưng
      CHƯA gọi API OA.
    · KHÔNG cần đối soát 2 DS với nhau. Chỉ cần xác nhận: hoàn thành HĐ có tạo đúng
      2 template chờ gửi đúng rule hay không.

⇒ Dòng tệp này ghi ra là **HÀNG CHỜ GỬI THẬT** — bản nháp của bộ gửi TƯƠNG LAI sẽ THAY CARE360.
  Thiếu đúng một thứ: lớp gọi API. Vì thế trạng thái là ``queued``, y như CARE360.
  KHÔNG có việc đối soát hai sổ — GĐ đã bỏ.

KHÁCH KHÔNG NHẬN TRÙNG nhờ HAI điều (không phải nhờ trạng thái):
  1. HAI SỔ RIÊNG — KHBL ghi ``khj_bl``; bộ gửi CARE360 chỉ đọc ``pmv_report``, khác cả database
     lẫn tài khoản MySQL. Nó KHÔNG THỂ nhìn thấy dòng của KHBL.
  2. ``apps/oa`` KHÔNG CÓ MỘT DÒNG MẠNG NÀO — ``smoke_oa`` (kịch bản 16) quét tĩnh cả thư mục
     mỗi lần chạy, thấy bất kỳ thư viện HTTP nào được import là TRƯỢT.
     ⚠ Bộ quét đó cố ý NGÂY THƠ (so chuỗi thô) để không thể bị lừa — nên ngay cả CHÚ THÍCH ở đây
     cũng không được viết tên các thư viện HTTP ra, viết là bài kiểm trượt oan.

⚠⚠ NGÀY BẬT BỘ GỬI CỦA KHBL: PHẢI TẮT 2 QUY TẮC TỰ QUÉT CỦA CARE360 **TRƯỚC**, đúng lộ trình GĐ
   đã nêu ("sẽ OFF nếu KHBL hoàn thiện"). Bật cả hai = khách nhận 2 tin, tiệm trả tiền 2 lần.
   Và phải rà dòng ``queued`` quá hạn trước khi bật, không thì bắn một mẻ tin cũ.
═══════════════════════════════════════════════════════════════════════════════════════════════

CÔNG THỨC — chép từ ``zbs_auto_worker.py`` của CARE360, đã kiểm chứng khớp 100% dòng thật id=16684.
Dòng KHBL ghi ra phải TƯƠNG THÍCH HOÀN TOÀN với dòng CARE360 để sau này đối soát/nhập chung không
vỡ. **KHÁC BIỆT DUY NHẤT** là 3 chỗ:
    source_type          'auto_rule'          →  'khbl_invoice'
    dedupe_key           …|auto_rule|…        →  …|khbl_invoice|…   (cùng công thức sha256)
    recipient_ciphertext token Fernet         →  'khbl_no_cipher'   (KHBL không giữ khóa KH_GATEWAY)
⚠ Tiền tố băm số điện thoại GIỮ NGUYÊN ``"care360-zbs|"`` — đổi là vỡ đối soát VÀ vỡ khoảng lặng chéo.

BA CÔNG TẮC AN TOÀN (mọi đường ghi mới đều đi qua ``_duoc_ghi_moi()``):
  1. ``STOP_ZNS.flag`` ở gốc dự án — DỪNG KHẨN, không cần RESET. Tạo tệp là dừng tức thì.
  2. ``ZNS_XEP_HANG`` trong ``.env`` — **MẶC ĐỊNH TẮT**. Bật xong phải RESET_KHBL.
  3. ``PMV_TARGET`` phải là **kk**. Đích ``sandbox`` là BẢN SAO dữ liệu thật; ghi dòng mang dữ liệu
     sandbox vào SỔ THẬT thì không cột nào phân biệt được. Đây cũng chính là thứ khiến
     ``smoke_ban_hang`` / ``smoke_ban_coc`` / ``smoke_datcoc_money`` (đều ép ``dat_dich("sandbox")``
     rồi gọi ``bill.chot``) KHÔNG đẻ dòng nào. Lệnh kiểm nào sau này chạy trên KK phải tự gọi
     :func:`tat_cho_tien_trinh` trước.
  Công tắc 1–2 CHỈ chặn đường GHI MỚI; :func:`huy_xep_hang` vẫn chạy (dòng đã xếp phải được hủy
  đúng, nếu không sẽ thành tin mồ côi). Công tắc 3 chặn cả hai chiều.

NUỐT LỖI: mọi hàm public ở đây **KHÔNG BAO GIỜ NÉM**. Hóa đơn nằm ở MSSQL, sổ nằm ở MySQL — hai
CSDL rời, không có transaction phân tán, lỗi MySQL không thể rollback hóa đơn PMV. Ngoại lệ lọt ra
sẽ rơi vào ``except`` của ``views.ban_thanh_toan`` và báo "thanh toán lỗi" trong khi TIỀN ĐÃ VÀO
KÉT THẬT — người bán bấm lại, đúng kịch bản đơn đôi. Theo đúng nếp ``apps/pos/gold_bill.py``.

KHÔNG KHÓA: tuyệt đối không ``select_for_update`` trên đường chốt. Chống đua bằng INSERT + bắt
``IntegrityError``, không bằng khóa hàng.

HẠN CHẾ ĐÃ BIẾT — nói rõ để không ai tưởng sổ là bức tranh đầy đủ: CARE360 ghi mọi quyết định
``suppressed/blocked/cancelled`` vào bảng ``zalo_auto_send_events``; bảng đó **KHÔNG được chép sang**
``khj_bl`` nên KHBL không có chỗ ghi. Lý do một hóa đơn không có tin (hoặc chỉ có 1 thay vì 2) chỉ
nằm ở ``logs/zns_xep_hang.log`` và ở ``manage.py xep_hang_zns --xem``, KHÔNG lên màn hình GĐ.
"""
import hashlib
import json
import logging
import os
import re
import uuid
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.db.models.functions import Coalesce
from django.utils import timezone

from .models import ZaloMessage, ZaloSendRule

# ───────────────────────────────── HẰNG SỐ ─────────────────────────────────
#: Nhãn RIÊNG của KHBL — phân biệt với 'auto_rule' của CARE360 trong cùng một bảng.
NGUON_KHBL = "khbl_invoice"

#: ``recipient_ciphertext`` là cột TEXT **NOT NULL**. CARE360 ghi token Fernet bằng khóa
#: ``KH_GATEWAY\instance\settings-master.key`` — KHBL KHÔNG có và KHÔNG NÊN có khóa đó (chép khóa ra
#: là nhân bản rủi ro lộ toàn bộ secret của Gateway). Chuỗi này: không chứa chữ số nào của số thật,
#: không giả dạng token (không tiền tố 'gAAAAA') nên ``decrypt_value()`` báo lỗi SẠCH thay vì trả rác.
#: KHÔNG cản trở bước 2: lớp gửi của CARE360 lấy số từ ``customer_phone``, không giải mã cột này.
KHONG_CO_MA_HOA = "khbl_no_cipher"

#: ⚠ GIỮ NGUYÊN. Đổi tiền tố là vỡ đối soát với 16.657 dòng cũ VÀ vỡ kiểm khoảng lặng chéo.
TIEN_TO_BAM = "care360-zbs|"

TRANG_THAI_CUOI = ZaloMessage.TRANG_THAI_CUOI            # sent · delivered · seen
TRANG_THAI_CON_SONG = ZaloMessage.TRANG_THAI_CON_SONG    # + queued · retry · sending

#: Chỉ hồi sinh dòng chết khi lỗi thuộc bộ này (chép ``AUTO_RESTORABLE_ERRORS`` của CARE360).
#: ⚠ CỐ Ý KHÔNG có ``task_activation_cutoff`` (CARE360 hủy CÓ CHỦ Ý vì hóa đơn có trước mốc bật
#: task — hồi sinh = nhắn "cảm ơn đã mua hàng" cho hóa đơn tháng 7) và KHÔNG có ``-118`` / ``-141``
#: (Zalo từ chối VĨNH VIỄN số nhận). Bản thân CARE360 cũng không hồi sinh ``failed_permanent``.
LOI_HOI_SINH_DUOC = {
    "rule_inactive",
    "template_inactive",
    "template_group_mismatch",
    "template_data_invalid",
    "rule_template_changed",
    "source_not_eligible",
}

GIO_VN = ZoneInfo("Asia/Ho_Chi_Minh")
TEN_CO_DUNG_KHAN = "STOP_ZNS.flag"

#: Công tắc CẤP TIẾN TRÌNH (lớp thứ hai sau ``PMV_TARGET``) — lệnh kiểm bật lên để chắc chắn
#: không dòng nào lọt vào sổ thật dù đích có bị đổi.
_TAT_CHO_TIEN_TRINH = False

_log_da_gan = False


# ───────────────────────────────── NHẬT KÝ ─────────────────────────────────
def nhat_ky():
    """Logger riêng ``apps.oa.xep_hang`` → ``logs/zns_xep_hang.log`` (UTF-8).

    ⚠ Nội dung log KHÔNG BAO GIỜ chứa số điện thoại rõ — chỉ ``trn_id`` + mã lý do.
    Tự gắn handler ở đây (không sửa ``config/settings``) để module đứng độc lập.
    """
    global _log_da_gan
    lg = logging.getLogger("apps.oa.xep_hang")
    if not _log_da_gan:
        _log_da_gan = True
        try:
            thu_muc = os.path.join(str(settings.BASE_DIR), "logs")
            os.makedirs(thu_muc, exist_ok=True)
            h = logging.FileHandler(os.path.join(thu_muc, "zns_xep_hang.log"), encoding="utf-8")
            h.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s %(message)s"))
            lg.addHandler(h)
            lg.setLevel(logging.INFO)
        except Exception:      # không ghi được log cũng KHÔNG được làm hỏng lượt bán
            pass
    return lg


# ─────────────────────────────── CÔNG TẮC ───────────────────────────────
def tat_cho_tien_trinh(tat=True):
    """Tắt/bật công tắc cấp tiến trình. Lệnh kiểm gọi ``tat_cho_tien_trinh(True)`` ở đầu ``handle()``."""
    global _TAT_CHO_TIEN_TRINH
    _TAT_CHO_TIEN_TRINH = bool(tat)


def _co_dung_khan():
    try:
        return os.path.exists(os.path.join(str(settings.BASE_DIR), TEN_CO_DUNG_KHAN))
    except Exception:
        return False


def _co_bat_trong_env():
    """``settings.ZNS_XEP_HANG_BAT`` nếu có; nếu không thì đọc thẳng ``ZNS_XEP_HANG`` từ môi trường
    (``environ.Env.read_env`` trong ``config/settings/base.py`` đã nạp ``.env`` vào ``os.environ``).
    MẶC ĐỊNH **TẮT** — cài mã lên máy thật mà chưa ai bấm gì thì không một dòng nào được ghi."""
    gia = getattr(settings, "ZNS_XEP_HANG_BAT", None)
    if gia is not None:
        return bool(gia)
    return str(os.environ.get("ZNS_XEP_HANG", "")).strip().lower() in {"1", "true", "yes", "on"}


def _dich_hien_tai(c=None):
    if c is not None:
        return getattr(c, "target", None)
    try:
        from apps.pmv import gateway
        return gateway.dich_hien_tai()
    except Exception:
        return None


def _duoc_ghi_moi(c=None):
    """(được_ghi, mã_lý_do). Dùng cho MỌI đường tạo/cập nhật dòng mới."""
    if _TAT_CHO_TIEN_TRINH:
        return False, "tat_cho_tien_trinh"
    if _co_dung_khan():
        return False, "co_dung_khan"
    if not _co_bat_trong_env():
        return False, "cong_tac_tat"
    dich = _dich_hien_tai(c)
    if dich != "kk":
        return False, f"dich_khong_phai_kk({dich})"
    return True, "ok"


# ───────────────────────── TIỆN ÍCH — CHÉP TỪ CARE360 ─────────────────────────
def _json(value):
    """``ensure_ascii=False`` (CÓ dấu tiếng Việt) + ``separators`` không khoảng trắng — đúng
    ``_json()`` của CARE360, nhờ đó chuỗi ra giống hệt từng byte."""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def _doc_json(value, mac_dinh):
    try:
        return json.loads(value or "")
    except (TypeError, ValueError):
        return mac_dinh


def chuan_hoa_sdt(value):
    """Chép NGUYÊN ``normalize_vn_phone()`` của CARE360. Trả ``(digits, local, masked, phone_hash)``.

    digits = '84' + 9 số (11 ký tự) · local = '0' + 9 số (10 ký tự, vào cột ``customer_phone``
    varchar(11)) · masked = 3 đầu + '***' + 3 cuối · phone_hash = sha256(TIEN_TO_BAM + digits).
    Sai dạng ⇒ ``ValueError`` (CARE360 coi là ``phone_invalid``, không tạo dòng).
    """
    digits = re.sub(r"\D", "", str(value or ""))
    if digits.startswith("84") and len(digits) == 11:
        local = "0" + digits[2:]
    elif digits.startswith("0") and len(digits) == 10:
        local = digits
        digits = "84" + digits[1:]
    else:
        raise ValueError("Số điện thoại Việt Nam không hợp lệ.")
    if local[1] not in "35789":
        raise ValueError("Số điện thoại không thuộc dải di động Việt Nam.")
    masked = f"{local[:3]}***{local[-3:]}"
    phone_hash = hashlib.sha256((TIEN_TO_BAM + digits).encode("utf-8")).hexdigest()
    return digits, local, masked, phone_hash


def _gio_vn_sang_aware(value):
    """datetime naive đọc từ MSSQL là GIỜ VN ⇒ gắn múi giờ VN. Sổ ``zalo_messages`` lưu giờ VN (GioVNField +
    kết nối TIME_ZONE VN từ 20/09/2026) nên ghi ra đúng giờ máy KK. TUYỆT ĐỐI không trừ/cộng tay 7 giờ."""
    if not isinstance(value, datetime):
        return None
    if timezone.is_aware(value):
        return value
    return timezone.make_aware(value, GIO_VN)


def _moc_giao_dich(bill):
    """``transaction_at`` = TrnDate ghép TrnTime (TrnTime hỏng ⇒ 00:00:00). Chép CARE360."""
    base = bill.get("TrnDate")
    if not isinstance(base, datetime):
        return _gio_vn_sang_aware(bill.get("CreatedDate"))
    raw = bill.get("TrnTime")
    gio = phut = giay = 0
    if hasattr(raw, "hour"):
        gio, phut, giay = raw.hour, raw.minute, raw.second
    elif raw:
        token = str(raw).split(".", 1)[0]
        for fmt in ("%H:%M:%S", "%H:%M"):
            try:
                p = datetime.strptime(token, fmt)
                gio, phut, giay = p.hour, p.minute, p.second
                break
            except ValueError:
                continue
    return _gio_vn_sang_aware(base.replace(hour=gio, minute=phut, second=giay))


def _moc_nguon_cap_nhat(bill):
    return _gio_vn_sang_aware(
        bill.get("SourceUpdatedAt") or bill.get("TrnDateTime_Upd") or bill.get("CreatedDate")
    )


def moc_du_dieu_kien(bill):
    """``eligible_at`` = GIỜ TẠO HÓA ĐƠN (CreatedDate), có 3 nấc dự phòng. Đây là mốc để tính
    ``scheduled_at`` — lịch gửi đếm từ lúc LẬP HÓA ĐƠN, KHÔNG phải lúc xếp hàng."""
    return (
        _gio_vn_sang_aware(bill.get("CreatedDate"))
        or _moc_giao_dich(bill)
        or _moc_nguon_cap_nhat(bill)
        or timezone.now()
    )


#: Mã nghiệp vụ mà một quy tắc phục vụ, đọc từ cột ``options_json`` — ``{"su_kien": "..."}``.
#: Bảng ``zalo_send_rules`` của CARE360 không có cột nào nói quy tắc dùng cho việc gì, vì bên đó
#: chỉ có MỘT nghiệp vụ (quét hóa đơn). Nay sổ dùng chung cho nhiều nghiệp vụ nên phải phân biệt,
#: không thì chốt một hóa đơn sẽ đem cả quy tắc nghỉ phép và cầm đồ ra thử rồi trượt.
SU_KIEN_HOA_DON = "hoa_don"

#: Trạng thái MỌI dòng KHBL ghi ra. GĐ CHỐT LẠI 14/09/2026 (bản sau, thay bản "sổ đối soát"):
#:   "CARE360 vẫn cho hoạt động bt, không tác động đến (sẽ OFF nếu KHBL hoàn thiện).
#:    KHBL sẽ cho ra DS (mỗi khi hoàn thành HĐ) + lịch gửi giống tương tự CARE360 nhưng CHƯA
#:    gọi API OA. Không cần đối soát 2 DS với nhau."
#: ⇒ Dòng KHBL là HÀNG CHỜ GỬI THẬT — bản nháp của bộ gửi tương lai sẽ THAY CARE360, chỉ thiếu
#:   đúng một thứ: lớp gọi API. Vì thế dùng ``queued`` ("chờ gửi"), giống hệt CARE360.
#: KHÁCH KHÔNG NHẬN TRÙNG nhờ HAI điều, không phải nhờ trạng thái:
#:   (1) HAI SỔ RIÊNG — KHBL ghi ``khj_bl``, bộ gửi CARE360 chỉ đọc ``pmv_report``, khác cả
#:       database lẫn tài khoản MySQL; nó KHÔNG THỂ nhìn thấy dòng của KHBL.
#:   (2) ``apps/oa`` KHÔNG CÓ MỘT DÒNG MẠNG NÀO (``smoke_oa`` quét tĩnh cả thư mục mỗi lần chạy).
#: ⚠ NGÀY BẬT BỘ GỬI CỦA KHBL thì PHẢI tắt 2 quy tắc tự quét của CARE360 TRƯỚC — đúng lộ trình
#:   GĐ đã nêu ("sẽ OFF nếu KHBL hoàn thiện"). Bật cả hai = khách nhận 2 tin, tiệm trả 2 lần.
TRANG_THAI_GHI_SO = "queued"


def su_kien_cua(rule):
    """Nghiệp vụ của một quy tắc. ``options_json`` trống ⇒ HÓA ĐƠN BÁN.

    Mặc định này CỐ Ý: 2 quy tắc CARE360 đang chạy (auto_send_affter_2m/5m) đều có
    ``options_json = NULL`` và đều phục vụ hóa đơn bán — giữ nguyên hành vi cũ, không phải sửa dữ liệu.
    Quy tắc của nghiệp vụ khác PHẢI khai rõ, ví dụ ``{"su_kien": "cam_do"}``.
    """
    import json as _json

    tho = (rule.options_json or "").strip()
    if not tho:
        return SU_KIEN_HOA_DON
    try:
        goi = _json.loads(tho)
    except (TypeError, ValueError):
        return SU_KIEN_HOA_DON      # JSON hỏng ⇒ về mặc định, KHÔNG ném trên đường chốt hóa đơn
    if not isinstance(goi, dict):
        return SU_KIEN_HOA_DON
    return str(goi.get("su_kien") or SU_KIEN_HOA_DON).strip() or SU_KIEN_HOA_DON


def nguon_du_dieu_kien(bill):
    """Chép nguyên ``_source_eligible()``. Trả ``(bool, mã)``. Sai bất kỳ điều nào ⇒ không tạo dòng.

    ⚠ Khách VÃNG LAI (``services.WALK_IN``) bị loại TƯỜNG MINH. Trước đây chỉ trông chờ họ rụng ở
    ``phone_invalid`` — đó là giả định về dữ liệu, không phải rào chắn: ngày nào có người điền số
    điện thoại vào hồ sơ vãng lai dùng chung là CẢ TIỆM nhận tin về hóa đơn của người khác.
    """
    if not bill:
        return False, "source_missing"
    if str(bill.get("Status") or "").strip().upper() != "C":
        return False, "source_not_completed"
    if str(bill.get("IsDel") or "0").strip().lower() not in {"", "0", "false"}:
        return False, "source_deleted"
    ma_khach = str(bill.get("CustID") or "").strip()
    if not ma_khach:
        return False, "customer_missing"
    from apps.pos.services import WALK_IN
    if ma_khach == WALK_IN:
        return False, "khach_vang_lai"
    try:
        chuan_hoa_sdt(bill.get("Phone"))
    except ValueError:
        return False, "phone_invalid"
    return True, "eligible"


def dung_template_data(template, bill):
    """Chép nguyên ``build_template_data()``. Ném ``ValueError`` khi thiếu biến BẮT BUỘC.

    · Bảng bí danh giữ ĐỦ cả hai tên SAI CHÍNH TẢ của CARE360 (``cutomer_name``, ``oder_code``) —
      bỏ đi là mẫu nào dùng tên đó sẽ trượt.
    · Tiền = SỐ TRẦN ``str(int(float(PayAmount)))`` — không chấm nghìn, không '₫', cắt phần lẻ.
    · Ngày = ``%d/%m/%Y``.
    · Tên không bắt buộc mà thiếu ⇒ BỎ HẲN khóa (không ghi ``null``).
    · Thứ tự khóa = thứ tự trong ``params_json``, KHÔNG sắp xếp.
    """
    params = _doc_json(template.params_json, [])
    if not isinstance(params, list):
        params = []
    ngay = bill.get("TrnDate")
    ngay = ngay.strftime("%d/%m/%Y") if hasattr(ngay, "strftime") else str(ngay or "")
    ten_kh = str(bill.get("CustName") or "Khách hàng")[:100]
    ma_don = str(bill.get("BillCode") or bill.get("TrnID") or "")
    tien = str(int(float(bill.get("PayAmount") or 0)))
    ten_nv = str(bill.get("EmpName") or "Nhân viên")[:100]
    bi_danh = {
        "customer_name": ten_kh, "customer": ten_kh, "name": ten_kh, "cutomer_name": ten_kh,
        "bill_code": ma_don, "order_code": ma_don, "transaction_code": ma_don,
        "order_id": ma_don, "id": ma_don, "code": ma_don, "oder_code": ma_don,
        "purchase_date": ngay, "date": ngay,
        "amount": tien, "total": tien, "pay_amount": tien, "price": tien,
        "staff_name": ten_nv, "employee_name": ten_nv, "staff": ten_nv,
    }
    ket, thieu = {}, []
    for item in params:
        ten = str((item or {}).get("name") or "").strip()
        if not ten:
            continue
        gia = bi_danh.get(ten.lower())
        if gia in (None, "") and bool(item.get("require") or item.get("required")):
            thieu.append(ten)
        elif gia is not None:
            toi_da = int(item.get("maxLength") or 0)
            ket[ten] = str(gia)[:toi_da] if toi_da else str(gia)
    if thieu:
        raise ValueError("Thiếu biến bắt buộc của template: " + ", ".join(thieu))
    return ket


def khoa_chong_trung(oa_id, rule_id, trn_id):
    """``dedupe_key`` = sha256(``"{oa}|{rule}|khbl_invoice|{trn}"``).

    Cùng công thức CARE360, chỉ khác ``source_type`` ⇒ **KHÔNG BAO GIỜ đụng** dedupe_key của
    CARE360 cho cùng hóa đơn. ``trn_id`` trong vật liệu băm dùng bản ``.strip()`` (CHƯA cắt 30),
    đúng như CARE360.
    """
    vat_lieu = f"{oa_id}|{rule_id}|{NGUON_KHBL}|{trn_id}"
    return hashlib.sha256(vat_lieu.encode("utf-8")).hexdigest()


def _tien_3_le(v):
    """``pay_amount`` là ``decimal(18,3)`` — làm tròn tường minh, đừng để MySQL strict mode từ chối."""
    if v is None:
        return None
    try:
        return Decimal(str(v)).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)
    except Exception:
        return None


# ───────────────────────── ĐỌC HÓA ĐƠN TỪ PMV ─────────────────────────
#: Bộ cột y hệt ``fetch_bill()`` của CARE360, thêm ``WITH (NOLOCK)`` theo RULE 3 của KHBL.
SQL_HOA_DON = (
    "SELECT TOP 1 b.TrnID,b.BillCode,b.TrnDate,b.TrnTime,b.CustID,b.PayAmount,b.EmpID,b.ShopID,"
    "b.TillID,b.Status,b.IsDel,b.CreatedDate,b.TrnDateTime_Upd,c.CustName,c.Phone,e.EmpName "
    "FROM TRN_RT_BUYSELL b WITH (NOLOCK) "
    "LEFT JOIN I_CUSTOMER c WITH (NOLOCK) ON c.CustID=b.CustID "
    "LEFT JOIN T_EMPLOYEE e WITH (NOLOCK) ON e.EmpID=b.EmpID "
    "WHERE b.TrnID=?"
)


def doc_hoa_don(trn_id, *, c=None, giay_cho=5, giay_noi=5):
    """MỘT câu SELECT lấy trọn thông tin cần. ``None`` nếu không có.

    ⚠ KHÔNG lấy dữ liệu từ giỏ session: ``cart.tu_phieu`` ghi ``"phone": ""`` CỨNG ⇒ mở đơn cũ
    chốt lại là mất số điện thoại.
    ⚠ Đừng tin lời "dùng lại kết nối đang mở": ``c.query()`` → ``gateway.pmv_read()`` →
    ``with _connect_dich(...)`` ⇒ MỖI câu SELECT là MỘT bắt tay ODBC mới rồi đóng. Vì thế truyền
    ``query_timeout`` ngắn để KK treo không giữ thread quầy.
    ⚠ PHẢI truyền CẢ ``timeout`` (chờ BẮT TAY ODBC, mặc định của ``pmv_read`` là 30 giây). Khi máy KK
    treo hoặc rớt mạng LAN, khâu lâu nhất chính là bắt tay — để mặc định 30 giây là người bán đứng
    chờ nửa phút ngay trên đường CHỐT HÓA ĐƠN. Đọc hụt chỉ mất một dòng ZNS; lệnh quét bù xếp lại sau.
    """
    from apps.pmv import gateway
    dich = _dich_hien_tai(c) or "kk"
    rows = gateway.pmv_read(SQL_HOA_DON, (str(trn_id),), tag="zns_xep_hang", audit=False,
                            target=dich, timeout=giay_noi, query_timeout=giay_cho)
    from apps.pos.document_contacts import overlay
    return overlay("KHBL_BUYSELL",rows[0],dich) if rows else None


# ───────────────────────── DỰNG / CẬP NHẬT MỘT DÒNG ─────────────────────────
def _ghi_snapshot(msg, bill, rule, template, local, masked, phone_hash, tdata, moc):
    """Điền TOÀN BỘ cột nội dung của một dòng (đúng ``_apply_snapshot()`` CARE360 + 3 khác biệt KHBL).

    KHÔNG đụng: ``tracking_id`` (bất biến sau INSERT), ``dedupe_key``, ``created_at``,
    ``status``, ``attempt_count`` — những thứ đó do lớp gọi quyết định.
    """
    bay_gio = timezone.now()
    msg.oa_account_id = rule.oa_account_id
    msg.template_id = template.id
    msg.send_rule_id = rule.id
    msg.external_template_id = template.template_id
    msg.channel = "phone"                       # CHỮ THƯỜNG — 16.657/16.657 dòng thật đều vậy
    msg.source_type = NGUON_KHBL
    msg.source_ref = str(bill.get("TrnID") or "")          # KHÔNG cắt
    msg.trn_id = str(bill.get("TrnID") or "")[:30]
    msg.bill_code = str(bill.get("BillCode") or "")[:100]
    msg.cust_id = str(bill.get("CustID") or "")[:50]
    # Chuỗi RỖNG (không phải NULL) khi thiếu — giữ đúng cách CARE360 ghi để đối soát cột-với-cột
    # không lệch vô cớ. (template_data_json vẫn ghi "Khách hàng" nhờ fallback trong bí danh.)
    msg.customer_name = str(bill.get("CustName") or "")[:200]
    msg.customer_phone = local
    msg.emp_id = str(bill.get("EmpID") or "")[:30]
    msg.employee_name = str(bill.get("EmpName") or "")[:200]
    msg.shop_id = str(bill.get("ShopID") or "")[:30]
    msg.till_id = str(bill.get("TillID") or "")[:30]
    msg.transaction_at = _moc_giao_dich(bill)
    msg.eligible_at = moc
    msg.source_status = str(bill.get("Status") or "")[:10]
    msg.source_updated_at = _moc_nguon_cap_nhat(bill)
    msg.source_eligible = True
    msg.pay_amount = _tien_3_le(bill.get("PayAmount"))
    msg.recipient_ciphertext = KHONG_CO_MA_HOA
    msg.recipient_masked = masked
    msg.recipient_hash = phone_hash
    msg.template_data_json = _json(tdata)
    # ĐÁP ÁN lịch gửi: đếm từ GIỜ TẠO HÓA ĐƠN + delay của quy tắc (thực đo khớp 3,0' và 5,0').
    msg.scheduled_at = moc + timedelta(minutes=int(rule.delay_minutes or 0))
    msg.estimated_cost = template.price_sdt      # LẤY THẲNG — không VAT, không hệ số
    # 9 khóa ĐÚNG THỨ TỰ của CARE360. Ghi lại mỗi lần upsert (luôn phản ánh rule lúc chốt gần nhất).
    msg.rule_snapshot_json = _json({
        "rule_id": rule.id,
        "rule_name": rule.rule_name,
        "message_group": rule.message_group,
        "delay_minutes": rule.delay_minutes,
        "cooldown_minutes": rule.cooldown_minutes,
        "max_attempts": rule.max_attempts,
        "retry_base_minutes": rule.retry_base_minutes,
        "template_id": template.id,
        "external_template_id": template.template_id,
    })
    msg.updated_at = bay_gio


def _lop2_da_co_dong_cung_mau(rule, trn_id):
    """LỚP 2 — chốt chặn GỬI ĐÔI với 16.657 dòng CARE360 đã chép sang.

    ⚠ CỐ Ý **KHÔNG lọc ``source_type``** và **KHÔNG lọc ``send_rule_id``** (chép nguyên
    ``_existing_template_bill()``): hóa đơn nào CARE360 đã phục vụ cho cặp (OA, mẫu) này thì KHBL
    tự im lặng bỏ qua.
    """
    return (ZaloMessage.objects
            .filter(oa_account_id=rule.oa_account_id, template_id=rule.template_id,
                    trn_id=trn_id, status__in=TRANG_THAI_CON_SONG)
            .order_by("-id").first())


def _lop3_khoang_lang(rule, local, phone_hash, moc, trn_id):
    """LỚP 3 — KHOẢNG LẶNG. Bỏ qua khi ``cooldown_minutes <= 0`` (rule 2 TRANSACTION = 0).

    Phạm vi chép nguyên CARE360: cùng OA · **KHÁC trn_id** · cùng người nhận (số HOẶC băm) ·
    trạng thái còn sống · ``COALESCE(eligible_at, created_at)`` trong ``[moc − cooldown, moc]`` ·
    và **cùng NHÓM TIN** qua JOIN ``zalo_templates.template_tag == rule.message_group``
    (KHÔNG so ``send_rule_id``).

    ⚠ 16.657 dòng lịch sử nằm CÙNG BẢNG nên tham gia khoảng lặng luôn — đây là điều TỐT: khách vừa
    nhận tin chăm sóc từ CARE360 trong 60 phút trước sẽ chặn dòng CUSTOMER_CARE của KHBL.
    ⚠ Cột ``cooldown_scope`` KHÔNG được đọc — phạm vi "số + nhóm tin" là CỨNG ở đây.
    """
    cd = int(rule.cooldown_minutes or 0)
    if cd <= 0:
        return None
    duoi = moc - timedelta(minutes=cd)
    return (ZaloMessage.objects
            .filter(oa_account_id=rule.oa_account_id, status__in=TRANG_THAI_CON_SONG,
                    template__template_tag=rule.message_group)
            .exclude(trn_id=trn_id).exclude(trn_id__isnull=True)   # = SQL `trn_id <> ?` của CARE360
            .filter(Q(customer_phone=local) | Q(recipient_hash=phone_hash))
            .annotate(moc_xet=Coalesce("eligible_at", "created_at"))
            .filter(moc_xet__gte=duoi, moc_xet__lte=moc)
            .order_by("-moc_xet").first())


def _upsert_mot_quy_tac(rule, bill, moc, local, masked, phone_hash, trn_id):
    """Xếp hàng cho ĐÚNG MỘT quy tắc. Trả mã kết quả (chuỗi) để thống kê/log.

    Bốn lớp chống trùng chạy theo đúng thứ tự — **KHÔNG BAO GIỜ INSERT MÙ**.
    """
    template = rule.template
    if not template:
        return "rule_khong_co_mau"
    if not template.gui_duoc:
        return "mau_khong_gui_duoc"
    # Hai điều kiện còn lại của CARE360 (_template_error): mẫu phải CÙNG OA với quy tắc, và
    # template_tag phải TRÙNG message_group. Thiếu chúng thì đổi nhóm một mẫu ở trang Zalo là
    # quy tắc lặng lẽ gửi sai mẫu, và lớp khoảng lặng (so theo nhóm) tự tắt mà không ai biết.
    if int(template.oa_account_id or 0) != int(rule.oa_account_id or 0):
        return "mau_khac_oa"
    if str(template.template_tag or "").upper() != str(rule.message_group or "").upper():
        return "mau_lech_nhom"
    # Điều kiện của CARE360: hóa đơn có trước mốc hiệu lực ⇒ BỎ QUA quy tắc (không ghi cancelled).
    if rule.effective_from and moc < rule.effective_from:
        return "truoc_moc_hieu_luc"
    try:
        tdata = dung_template_data(template, bill)
    except ValueError:
        return "queue_build_failed"

    oa_id = rule.oa_account_id
    with transaction.atomic():
        # ── LỚP 0 — ô khóa `uq_zalo_message_auto_rule_bill` đã bị hệ KHÁC chiếm chưa? ──
        # Khóa duy nhất này KHÔNG có source_type, nên nhãn riêng của KHBL không cứu được.
        # Thấy dòng của CARE360 ⇒ BỎ QUA TOÀN BỘ quy tắc, KHÔNG update MỘT CỘT NÀO (kể cả
        # source_status) — đây là ràng buộc "không sửa 16.657 dòng lịch sử".
        bat_ky = (ZaloMessage.objects
                  .filter(oa_account_id=oa_id, send_rule_id=rule.id, trn_id=trn_id).first())
        if bat_ky is not None and bat_ky.source_type != NGUON_KHBL:
            return "da_co_dong_he_khac"

        # ── LỚP 1 — dòng CỦA CHÍNH KHBL ──
        cu = bat_ky
        if cu is not None:
            if cu.status in TRANG_THAI_CUOI:
                # Đã gửi rồi: KHÔNG đụng nội dung, chỉ soi lại trạng thái nguồn.
                cu.source_status = str(bill.get("Status") or "")[:10]
                cu.source_updated_at = _moc_nguon_cap_nhat(bill)
                cu.source_eligible = True
                cu.updated_at = timezone.now()
                cu.save(update_fields=["source_status", "source_updated_at", "source_eligible",
                                       "updated_at"])
                return "terminal_giu_nguyen"
            _ghi_snapshot(cu, bill, rule, template, local, masked, phone_hash, tdata, moc)
            hoi_sinh = (cu.status in ("cancelled", "failed")
                        and (cu.error_code or "") in LOI_HOI_SINH_DUOC)
            if hoi_sinh:
                cu.status = TRANG_THAI_GHI_SO
                cu.error_code = cu.error_message = None
                cu.next_retry_at = cu.lease_token = cu.locked_until = None
                cu.cancelled_at = None
                cu.cancel_reason = None
            cu.save()
            return "hoi_sinh" if hoi_sinh else "cap_nhat"

        # ── LỚP 2 — hóa đơn này đã có tin của (OA, mẫu) rồi (kể cả của CARE360) ──
        if _lop2_da_co_dong_cung_mau(rule, trn_id) is not None:
            return "bi_mau_khac_phu"

        # ── LỚP 3 — khoảng lặng ──
        if _lop3_khoang_lang(rule, local, phone_hash, moc, trn_id) is not None:
            return "phone_group_cooldown"

        # ── LỚP 4 — INSERT, bọc savepoint + bắt IntegrityError ──
        for _ in range(3):
            moi = ZaloMessage(
                tracking_id=uuid.uuid4().hex[:32],
                dedupe_key=khoa_chong_trung(oa_id, rule.id, trn_id),
                status=TRANG_THAI_GHI_SO,
                attempt_count=0,
                created_by=rule.created_by,
                created_at=timezone.now(),
            )
            _ghi_snapshot(moi, bill, rule, template, local, masked, phone_hash, tdata, moc)
            try:
                with transaction.atomic():          # SAVEPOINT: lỗi không phá transaction ngoài
                    moi.save(force_insert=True)
                return "tao_moi"
            except IntegrityError as e:
                if "tracking" in str(e).lower():
                    continue                        # đụng uq_zalo_tracking_id — sinh uuid mới
                # Tiến trình khác vừa chèn: soi lại ĐÚNG MỘT LẦN rồi thôi. Không bao giờ update
                # dòng của hệ khác.
                lai = (ZaloMessage.objects
                       .filter(oa_account_id=oa_id, send_rule_id=rule.id, trn_id=trn_id).first())
                if lai is not None and lai.source_type == NGUON_KHBL \
                        and lai.status not in TRANG_THAI_CUOI:
                    _ghi_snapshot(lai, bill, rule, template, local, masked, phone_hash, tdata, moc)
                    lai.save()
                    return "cap_nhat_sau_dua"
                return "bo_qua_sau_dua"
        return "trung_tracking_3_lan"


# ─────────────────────────── HAI HÀM PUBLIC ───────────────────────────
def xep_hang_tu_hoa_don(bill, *, ghi_log=True):
    """Lõi thuần: nhận dict hóa đơn (đã đọc sẵn), xếp hàng cho MỌI quy tắc đang bật.

    Trả dict ``{mã_kết_quả: số_lần}``. KHÔNG kiểm công tắc (lớp gọi làm) và KHÔNG NÉM.
    Lệnh kiểm gọi thẳng hàm này với hóa đơn GIẢ trong bộ nhớ, không đụng PMV.
    """
    ket = {}
    try:
        trn_id = str(bill.get("TrnID") or "").strip()
        du, ma = nguon_du_dieu_kien(bill)
        if not du:
            # mục G: khách không có số hợp lệ ⇒ BỎ QUA IM LẶNG + ghi log, KHÔNG ghi dòng
            # `cancelled` (3 cột recipient_* đều NOT NULL — không có số thì không dựng nổi một
            # dòng hợp lệ; CARE360 cũng không tạo dòng, nó ghi vào zalo_auto_send_events mà bảng
            # đó KHÔNG được chép sang khj_bl).
            if ghi_log:
                nhat_ky().info("BỎ QUA %s: %s", trn_id or "?", ma)
            return {ma: 1}
        digits, local, masked, phone_hash = chuan_hoa_sdt(bill.get("Phone"))
        moc = moc_du_dieu_kien(bill)
        for rule in (ZaloSendRule.objects.filter(is_active=True)
                     .select_related("template").order_by("id")):
            if su_kien_cua(rule) != SU_KIEN_HOA_DON:
                continue        # quy tắc của nghiệp vụ khác (nghỉ phép, cầm đồ, hàng sẵn sàng)
            try:
                ma = _upsert_mot_quy_tac(rule, bill, moc, local, masked, phone_hash, trn_id)
            except Exception as e:                  # một quy tắc hỏng không kéo theo quy tắc kia
                ma = "loi_ngoai_le"
                if ghi_log:
                    nhat_ky().exception("LỖI xếp hàng %s|rule %s: %s", trn_id, rule.id, e)
            ket[ma] = ket.get(ma, 0) + 1
            if ghi_log and ma not in ("tao_moi", "cap_nhat", "hoi_sinh"):
                nhat_ky().info("%s|rule %s|%s", trn_id, rule.id, ma)
    except Exception as e:
        ket["loi_ngoai_le"] = ket.get("loi_ngoai_le", 0) + 1
        try:
            nhat_ky().exception("LỖI xếp hàng (lõi): %s", e)
        except Exception:
            pass
    return ket


def xep_hang_hoa_don(trn_id, *, c=None):
    """MÓC ĐỒNG BỘ gọi từ ``apps/pos/bill.chot()`` ngay trước ``return True``.

    ⚠ HÀM NÀY KHÔNG BAO GIỜ NÉM — hóa đơn PHẢI chốt xong dù xếp hàng hỏng hoàn toàn.
    Trả dict thống kê (rỗng khi công tắc tắt).
    """
    try:
        duoc, ly_do = _duoc_ghi_moi(c)
        if not duoc:
            return {}
        bill = doc_hoa_don(trn_id, c=c)
        if not bill:
            nhat_ky().info("BỎ QUA %s: source_missing", trn_id)
            return {"source_missing": 1}
        return xep_hang_tu_hoa_don(bill)
    except Exception as e:
        try:
            nhat_ky().exception("LỖI móc xếp hàng %s: %s", trn_id, e)
        except Exception:
            pass
        return {}


def huy_xep_hang(trn_id, ly_do, *, c=None, bill=None):
    """Hóa đơn không còn đủ điều kiện (HỦY THANH TOÁN / XÓA ĐƠN) ⇒ **CẬP NHẬT, KHÔNG XÓA DÒNG**.

    Làm đúng ``_cancel_ineligible()`` của CARE360, cộng ràng buộc sống còn:
    ⚠ **Bộ lọc BẮT BUỘC có ``source_type='khbl_invoice'``** — thiếu là chạm vào dòng ``auto_rule``
    của CARE360 đã chép sang.
    Dòng đã ở trạng thái cuối (sent/delivered/seen) GIỮ NGUYÊN, chỉ soi lại trạng thái nguồn.
    Dòng ĐANG ``cancelled`` vẫn được cập nhật ``cancel_reason`` (``source_not_completed`` →
    ``source_deleted``) — nếu không thì móc ở ``bill.huy()`` vô nghĩa, vì ``huy()`` chỉ nhận đơn W,
    tức luôn đã qua ``mo_lai()``.

    Công tắc ``ZNS_XEP_HANG`` / ``STOP_ZNS.flag`` KHÔNG chặn hàm này (dòng đã xếp phải được hủy,
    nếu không sẽ thành tin mồ côi); nhưng đích vẫn phải là **kk** — thao tác trên sandbox không
    được phép chạm sổ thật (TrnID sandbox trùng TrnID thật vì sandbox là bản sao).

    KHÔNG BAO GIỜ NÉM. Trả số dòng đã đổi.
    """
    try:
        if _TAT_CHO_TIEN_TRINH or _dich_hien_tai(c) != "kk":
            return 0
        if bill is None:
            # Soi lại nguồn để ghi đúng source_status/source_updated_at. Đọc hỏng thì vẫn hủy —
            # hủy quan trọng hơn thông tin phụ.
            try:
                bill = doc_hoa_don(trn_id, c=c)
            except Exception:
                bill = None
        return huy_xep_hang_thuan(trn_id, ly_do, bill=bill)
    except Exception as e:
        try:
            nhat_ky().exception("LỖI hủy xếp hàng %s: %s", trn_id, e)
        except Exception:
            pass
        return 0


def huy_xep_hang_thuan(trn_id, ly_do, *, bill=None):
    """Lõi hủy, KHÔNG kiểm công tắc (lệnh kiểm gọi thẳng). Vẫn lọc ``source_type`` như trên."""
    trn_id = str(trn_id or "").strip()
    bay_gio = timezone.now()
    trang_thai_nguon = str((bill or {}).get("Status") or "")[:10] or None
    moc_nguon = _moc_nguon_cap_nhat(bill) if bill else None
    dem = 0
    rows = ZaloMessage.objects.filter(source_type=NGUON_KHBL, trn_id=trn_id)
    for m in rows:
        if m.status in TRANG_THAI_CUOI:
            # Đã gửi thật — KHÔNG hủy được nữa, chỉ ghi nhận nguồn đã đổi.
            if trang_thai_nguon or moc_nguon:
                m.source_status = trang_thai_nguon or m.source_status
                m.source_updated_at = moc_nguon or m.source_updated_at
                m.updated_at = bay_gio
                m.save(update_fields=["source_status", "source_updated_at", "updated_at"])
            continue
        m.source_status = trang_thai_nguon or m.source_status
        m.source_updated_at = moc_nguon or m.source_updated_at
        m.source_eligible = False
        m.status = "cancelled"
        m.cancelled_at = bay_gio
        m.cancel_reason = str(ly_do)[:100]
        m.error_code = "source_not_eligible"
        m.error_message = "Hóa đơn không còn đủ điều kiện gửi tự động."
        m.lease_token = m.locked_until = m.next_retry_at = None
        m.updated_at = bay_gio
        m.save(update_fields=[
            "source_status", "source_updated_at", "source_eligible", "status", "cancelled_at",
            "cancel_reason", "error_code", "error_message", "lease_token", "locked_until",
            "next_retry_at", "updated_at",
        ])
        dem += 1
    if dem:
        nhat_ky().info("HỦY %s: %s (%d dòng)", trn_id, ly_do, dem)
    return dem
