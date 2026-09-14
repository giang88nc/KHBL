# -*- coding: utf-8 -*-
"""Áp nhiều phiếu cọc đã thu P vào một hóa đơn; chỉ hoàn tất khi PMV và quỹ khớp.

Hàng đặt được miễn đối chiếu mã, nhưng vẫn phải đủ chứng từ thu tiền.
Liên kết dùng proc vendor một lần với toàn bộ danh sách; bấm lại chỉ đọc kết quả.
"""
import datetime as dt
import hashlib
import logging
import uuid
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from apps.pmv import money as M
from apps.pmv.models import pmv_user_for_web_user

from .deposit_models import DepositEvent, DepositMoneyOperation as Operation, DepositOrderState, DepositStockHold

log = logging.getLogger(__name__)

# Trạng thái phiếu cọc CÒN DÙNG ĐƯỢC: W đang chờ · R hàng sẵn sàng · P đã thu quỹ (luồng mới).
# C = đã giao khách, D = đã hủy → không đưa ra danh sách.
TRANG_THAI_CHO = ("W", "R", "P")
TOI_DA = 20                       # một khách hiếm khi có nhiều hơn thế; chặn danh sách dài vô hạn


def _sql_cho():
    cho = ",".join("?" * len(TRANG_THAI_CHO))
    return (
        "SELECT d.TrnID, d.BillCode, d.TrnDate, d.TienCoc, d.CashPay, d.CardPay, d.Status, d.Description, "
        "(SELECT COUNT(*) FROM TRN_DATCOC_DT t WITH (NOLOCK) WHERE t.TrnID=d.TrnID) AS SoMon "
        "FROM TRN_DATCOC d WITH (NOLOCK) "
        f"WHERE d.CustID=? AND d.Status IN ({cho}) AND ISNULL(d.TienCoc,0) > 0 "
        "AND NOT EXISTS (SELECT 1 FROM TRN_RT_BUYSELL_DatCoc l WITH (NOLOCK) WHERE l.DatCocID=d.TrnID AND l.TrnID<>?) "
        "ORDER BY d.TrnDate DESC, d.TrnID DESC")


def _da_huy(target, ids, include_completed=False):
    """Phiếu đã HỦY ĐẶT HÀNG bên trang ĐẶT-CỌC (trạng thái vận hành nằm ở MySQL, không ở PMV)."""
    if not ids:
        return set()
    return set(DepositOrderState.objects.filter(
        target=target, trn_id__in=list(ids), fulfilment__in=(['cancelled','delivered','applied'] if include_completed else ['cancelled'])).values_list("trn_id", flat=True))


def phieu_cho(c, cust_id, invoice_id=''):
    """Danh sách phiếu cọc CHỜ ÁP DỤNG của một khách. Trả list dict đã đủ thứ cần hiện lên màn."""
    if not cust_id:
        return []
    rows = c.query(_sql_cho(), (cust_id, *TRANG_THAI_CHO, invoice_id))[:TOI_DA]
    huy = _da_huy(c.target, [r["TrnID"] for r in rows])
    ra = []
    for r in rows:
        if r["TrnID"] in huy:
            continue
        from . import deposit_application as A
        try:
            A.check(c,[r['TrnID']],cust_id,invoice_id)
            money_error = ''
        except ValueError as exc:
            money_error = str(exc).split(': ',1)[-1]
        ngay = r.get("TrnDate")
        ra.append({
            "id": r["TrnID"],
            "ma": r["TrnID"],
            "bill_code": (r.get("BillCode") or "").strip(),
            "ngay": ngay.date() if isinstance(ngay, dt.datetime) else ngay,
            "so_mon": int(r.get("SoMon") or 0),
            "tien": M.dec(r.get("TienCoc")),
            "trang_thai": (r.get("Status") or "").strip(),
            "ghi_chu": (r.get("Description") or "").strip()[:120],
            "san_pham": [],
            "money_error": money_error,
        })
    if ra:
        from .deposit_orders import item_info
        theo_id = {p['id']: p for p in ra}
        placeholders = ','.join('?' for _ in ra)
        details = c.query(
            'SELECT TrnID, ProductDesc, Notes FROM TRN_DATCOC_DT WITH (NOLOCK) '
            f'WHERE TrnID IN ({placeholders}) ORDER BY TrnID, TrnDTID', tuple(theo_id))
        for row in details:
            phieu = theo_id.get(row['TrnID'])
            if phieu is not None:
                item = item_info(row)
                phieu['san_pham'].append({'ma': (item['ProductCode'] or '').strip(),
                                         'ten': (item.get('ProductDesc') or '').strip(), 'nguon': item['Mode']})
    return ra


def doi_soat_title(ds, ban):
    """Tooltip đối chiếu mã với giỏ hiện tại; không lưu kết quả đối chiếu vào cache theo khách."""
    codes = {str(x.get('row', {}).get('ProductCode') or '').strip().casefold() for x in (ban or [])}
    result = []
    for phieu in ds:
        text, missing = [], []
        items = phieu.get('san_pham', [])
        for item in items:
            code = item['ma']
            if item.get('nguon') == 'new':
                text.append('✓ ' + (code or 'Khách đặt') + ': ' + (item['ten'] or 'Hàng đặt'))
            elif code.casefold() in codes:
                text.append('✓ ' + code + ': ' + (item['ten'] or 'Sản phẩm'))
            else:
                missing.append(code or 'Mã SP chưa xác định')
                text.append('⚠️ ' + (code or 'Mã SP chưa xác định') + ': chưa có trong danh sách.')
        if not items:
            text.append('Phiếu tiền cọc không kèm sản phẩm.' if not phieu['so_mon'] else 'Chưa đọc được mã sản phẩm để đối chiếu.')
        incomplete = len(items) != phieu['so_mon']
        if incomplete and items: text.append('⚠️ Chưa đọc đủ sản phẩm trong phiếu.')
        if phieu.get('money_error'): text.append('⚠️ ' + phieu['money_error'])
        result.append({**phieu, 'doi_soat_title': '\n'.join(text), 'khong_duoc_dung': bool(missing) or incomplete or bool(phieu.get('money_error')),
                       'ma_thieu': missing})
    return result


def kiem_san_pham(c, ids, ban):
    """Đọc lại chi tiết khi chọn/thanh toán, không tin kết quả tooltip hoặc cache."""
    from .deposit_orders import item_info
    ids = list(dict.fromkeys(ids))
    if not ids: return ''
    grouped = {pk: {'id': pk, 'ma': pk, 'so_mon': 0, 'san_pham': []} for pk in ids}
    rows = c.query('SELECT TrnID, ProductDesc, Notes FROM TRN_DATCOC_DT WITH (NOLOCK) '
                   'WHERE TrnID IN (' + ','.join('?' for _ in ids) + ') ORDER BY TrnID, TrnDTID', tuple(ids))
    for row in rows:
        item = item_info(row); p = grouped[row['TrnID']]
        p['so_mon'] += 1
        p['san_pham'].append({'ma': (item['ProductCode'] or '').strip(), 'ten': (item.get('ProductDesc') or '').strip(),
                             'nguon': item['Mode']})
    for p in doi_soat_title(list(grouped.values()), ban):
        if p['khong_duoc_dung']:
            return 'Phiếu cọc ' + p['id'] + ' chưa dùng được: ' + ', '.join(p['ma_thieu']) + ' chưa có trong danh sách BÁN HÀNG. Thêm đủ hàng hoặc gỡ phiếu cọc.'
    return ''


def tong_tien(ds, ids):
    chon = set(ids or ())
    return sum((p["tien"] for p in ds if p["id"] in chon), M.D0)


def chuoi_tien(x):
    """Số tiền ghi vào giỏ phải là chuỗi THẬP PHÂN THƯỜNG.

    ⚠ ``str(Decimal)`` sau khi làm tròn nghìn cho ra dạng mũ — 2 triệu thành '2.000E+6', 0 thành
    '0E+3' — đem so sánh hay in ra màn là sai ngay. Luôn đi qua ``format(x, 'f')``."""
    return format(M.tron_ngan(x), "f")


def sp_dang_coc(c):
    """Bản đồ MÃ SẢN PHẨM → phiếu cọc CHƯA HOÀN THÀNH đang giữ mã đó (GĐ chốt 11/09/2026).

    Dùng cho danh sách VÀNG BÁN: bán trúng món người khác đã cọc là chuyện phải chặn lại trước khi
    thanh toán. Mã hàng nằm trong ghi chú từng dòng phiếu (``SP: <mã>|…`` hàng có sẵn, ``MA_DAT:<mã>|…``
    hàng đặt) nên phải bóc bằng chính ``deposit_orders.item_info``.

    Phiếu tính là còn hiệu lực khi: trạng thái W/R/P (chưa giao, chưa hủy), chưa gắn hóa đơn nào, và
    chưa bị hủy đặt hàng bên trang ĐẶT-CỌC. Một mã lỡ nằm ở nhiều phiếu thì lấy phiếu MỚI nhất.
    """
    from . import deposit_orders as O

    cho = ",".join("?" * len(TRANG_THAI_CHO))
    rows = c.query(
        "SELECT d.TrnID, d.CustID, d.TrnDate, d.TienCoc, k.CustName, t.ProductDesc, t.Notes "
        "FROM TRN_DATCOC d WITH (NOLOCK) "
        "JOIN TRN_DATCOC_DT t WITH (NOLOCK) ON t.TrnID = d.TrnID "
        "LEFT JOIN I_CUSTOMER k WITH (NOLOCK) ON k.CustID = d.CustID "
        f"WHERE d.Status IN ({cho}) "
        "AND NOT EXISTS (SELECT 1 FROM TRN_RT_BUYSELL_DatCoc l WITH (NOLOCK) WHERE l.DatCocID=d.TrnID) "
        "ORDER BY d.TrnDate, d.TrnID", TRANG_THAI_CHO)
    huy = _da_huy(c.target, {r['TrnID'] for r in rows}, include_completed=True)
    ra = {}
    for r in rows:
        if r["TrnID"] in huy:
            continue
        ma = (O.item_info(r)["ProductCode"] or "").strip()
        if ma and ma != "Khách đặt":
            ngay = r.get("TrnDate")
            ra[ma.upper()] = {"id": r["TrnID"], "cust": r["CustID"], "ten": (r.get("CustName") or "").strip(),
                              "tien": M.dec(r.get("TienCoc")),
                              "ngay": ngay.date() if isinstance(ngay, dt.datetime) else ngay}
    return ra


def danh_dau_sp(ban_do, codes, cust_id):
    """Gắn dấu cho từng mã hàng đang có trên đơn: 💸 của chính khách này · ⛔ của khách khác.

    Nhận sẵn bản đồ ``sp_dang_coc`` để chỗ gọi tự quyết định có dùng bản nhớ hay không."""
    ra = {}
    for ma in codes or ():
        p = ban_do.get((ma or "").strip().upper())
        if p:
            ra[ma] = {**p, "cua_khach": bool(cust_id) and p["cust"] == cust_id}
    return ra


def tong_da_gan(c, trn_id):
    """Tổng tiền các phiếu cọc ĐANG gắn vào một hóa đơn — con số ``bill.chot`` đối chiếu với Tiền cọc."""
    r = c.query("SELECT ISNULL(SUM(d.TienCoc), 0) AS t FROM TRN_RT_BUYSELL_DatCoc l WITH (NOLOCK) "
                "JOIN TRN_DATCOC d WITH (NOLOCK) ON d.TrnID=l.DatCocID WHERE l.TrnID=?", (trn_id,))
    return M.dec(r[0]["t"]) if r else M.D0


def kiem_truoc_khi_gan(c, pk, cust_id, invoice_id=''):
    """Đọc lại phiếu ngay trước khi ghi; trả (header, lỗi). Không tin danh sách đã quét lúc trước."""
    from . import deposit_application as A
    try:
        return A.check(c,[pk],cust_id,invoice_id)[0]['h'], ''
    except ValueError as exc:
        return None, str(exc)


def lien_ket(c, trn_id, ids, cust_id, user):
    """Gắn các phiếu cọc vào hóa đơn bán đang còn LƯU TẠM. Trả (đã_gắn, lỗi).

    ⚠ Proc vendor ``TRN_RT_BUYSELL_DatCoc_Ins`` nhận DANH SÁCH ngăn nhau bằng '@' và **XÓA SẠCH rồi
    ghi lại** toàn bộ liên kết của hóa đơn đó — gọi từng phiếu một thì phiếu sau đá phiếu trước
    (đã dính lúc dựng bài kiểm 11/09/2026). Nên phải kiểm hết rồi gọi ĐÚNG MỘT LẦN với cả danh sách,
    và luôn truyền đủ những phiếu muốn giữ.

    Được ăn cả, ngã về không: có một phiếu không hợp lệ là dừng, chưa ghi gì. Ghi xong còn đọc lại
    danh sách liên kết để chắc vendor đã nhận đúng từng mã.
    """
    pu = pmv_user_for_web_user(user) if getattr(user, "pk", None) else None
    # bộ kiểm truyền user giả mang sẵn user_id/till_id; người dùng thật lấy qua bảng ghép PMV
    ma_user = getattr(pu, "user_id", "") or getattr(user, "user_id", "") or ""
    ma_till = getattr(pu, "till_id", "") or getattr(user, "till_id", "") or ""
    ids = list(dict.fromkeys(ids))                 # bỏ trùng, giữ thứ tự người bán đã chọn
    if not ids:
        return [], ""
    heads = {}
    for pk in ids:
        h, loi = kiem_truoc_khi_gan(c, pk, cust_id, trn_id)
        if loi:
            return [], loi
        heads[pk] = h
    ban = [{'row': r} for r in c.query('SELECT ProductCode FROM TRN_RT_BUYSELL_SELL WITH (NOLOCK) WHERE TrnID=?', (trn_id,))]
    loi = kiem_san_pham(c, ids, ban)
    if loi: return [], loi
    from . import deposit_application as A
    inv = A.invoice(c,trn_id)
    if inv['Status'] != 'W' or inv['CustID'] != cust_id:
        return [], 'Hóa đơn phải còn lưu tạm và cùng khách.'
    try:
        A.check(c,ids,cust_id,trn_id,inv['TienCoc'])
    except ValueError as exc:
        return [],str(exc)
    existing = A.linked_ids(c,trn_id)
    if existing:
        if sorted(existing) != sorted(ids):
            return existing, 'Hóa đơn đã liên kết danh sách cọc khác. Phục hồi liên kết trước khi đổi phiếu.'
        return existing, ''  # đọc lại khớp: không gọi proc liên kết lần nữa
    with transaction.atomic():
        ops = [Operation.objects.create(
        target=c.target, trn_id=pk, kind="apply", amount=M.dec(heads[pk].get("TienCoc")),
        cash=M.dec(heads[pk].get("CashPay")), bank=M.dec(heads[pk].get("CardPay")), invoice_id=trn_id,
        active_key=c.target + ":" + pk, status="running",
        bank_key=c.target+':invoice:'+trn_id if index == 0 else None,
        token=hashlib.sha256(f"{c.target}:{pk}:{trn_id}:{uuid.uuid4().hex}".encode()).hexdigest(),
        user_id=ma_user, till_id=ma_till, username=getattr(user, "username", "") or "",
        evidence={"nguon": "ban-hang", "coc": {k: str(heads[pk].get(k) or "") for k in
                  ("TrnID", "CustID", "Status", "TienCoc", "CashPay", "CardPay")}}) for index,pk in enumerate(ids)]
    try:
        A.check(c,ids,cust_id,trn_id,inv['TienCoc'])
        if A.invoice(c,trn_id) != inv or A.linked_ids(c,trn_id):
            raise ValueError('Hóa đơn vừa thay đổi trước khi liên kết.')
        c.call("TRN_RT_BUYSELL_DatCoc_Ins", write=True, p_TrnID=trn_id, p_IDCoc="@".join(ids))
        co = {r["DatCocID"] for r in c.query(
            "SELECT DatCocID FROM TRN_RT_BUYSELL_DatCoc WITH (NOLOCK) WHERE TrnID=?", (trn_id,))}
        thieu = [pk for pk in ids if pk not in co]
        if thieu or co - set(ids):
            raise ValueError(f"vendor ghi nhận {sorted(co)} thay vì {ids}")
    except Exception as exc:
        log.exception("Gắn phiếu cọc %s vào hóa đơn %s hỏng", ids, trn_id)
        for op in ops:
            op.status = "uncertain"
            op.message = "Chưa rõ đã gắn hay chưa — kiểm tra liên kết cọc trên PMV trước khi làm tiếp."
            op.save()
        return [], f"Không gắn được phiếu cọc vào hóa đơn: {exc}."
    now = timezone.now()
    for op in ops:
        op.status = 'linked'
        op.message = f"Đã liên kết hóa đơn {trn_id}; chờ chốt và xác nhận quỹ."
        op.save()
    return ids, ""


def hoan_tat(c, ids, user, trn_id="", bill_code=""):
    """Hóa đơn đã CHỐT: đánh dấu phiếu cọc xong, giải phóng hàng đang giữ, ghi nhật ký từng phiếu."""
    from . import deposit_application as A
    verified = A.finish(c,trn_id)
    if sorted(verified) != sorted(ids):
        raise ValueError('Danh sách cọc sau chốt không khớp; cần kiểm tra hóa đơn.')
    now = timezone.now()
    ten = getattr(user, "username", "") or ""
    for pk in ids:
        try:
            with transaction.atomic():
                event_token=hashlib.sha256(f"xong:{c.target}:{pk}:{trn_id}".encode()).hexdigest()
                if DepositEvent.objects.filter(token=event_token).exists(): continue
                s = DepositOrderState.objects.select_for_update().filter(target=c.target, trn_id=pk).first()
                if not s:
                    s = DepositOrderState(target=c.target, trn_id=pk)
                s.fulfilment = "applied"
                s.delivered_at = s.delivered_at or now
                s.next_contact = None
                s.version += 1
                s.save()
                DepositStockHold.objects.filter(target=c.target, trn_id=pk, active_key__isnull=False).update(
                    active_key=None, released_at=now, released_by=ten)
                DepositEvent.objects.create(
                    target=c.target, trn_id=pk, action="money_apply", username=ten,
                    note=f"Đã dùng cho hóa đơn {bill_code or trn_id}; hàng giữ được giải phóng.",
                    token=event_token,
                    data={"hoa_don": trn_id, "so_phieu": bill_code})
        except Exception:                     # ghi nhận trạng thái hỏng KHÔNG được làm hỏng lần bán
            log.exception("Đánh dấu xong phiếu cọc %s sau hóa đơn %s hỏng", pk, trn_id)
    from . import deposit_workspace as W
    W.invalidate(c.target)


def da_dung(target, pk):
    """Phiếu cọc đã được một hóa đơn bán dùng (áp xong) → khóa mọi thao tác sửa/hủy bên ĐẶT-CỌC."""
    from apps.pmv.client import PmvClient
    from .deposit_money import financial
    return financial(PmvClient(target,tag='coc-used'),pk)['applied']
