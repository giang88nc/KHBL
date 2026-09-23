"""CHECK GOLD V4 — SỔ HÀNG LẤY RA KHỎI TỦ + dòng GIAO DỊCH realtime (GĐ chốt 23/09/2026).

    https://127.0.0.1:8100/banle/bao-cao/check-gold/     (trang đứng riêng, KHÔNG đăng nhập, không menu)

KHÔNG phải trang kiểm hàng (GĐ chốt 23/09): quầy quét mã khi LẤY món ra khỏi tủ để bán (chủ yếu vàng 9999),
quét lại đúng mã đó khi TRẢ món về tủ. Dựng lại từ bản V3 của BANLE_V5 (/check-gold/) nhưng GOM VỀ KHBL:
  · nhật ký quét nằm ở MySQL 8 khj_bl — model GoldCheckEntry (bảng gold_check_entry), KHÔNG còn pmv_report;
  · dữ liệu máy KK đọc qua PmvClient (CHỈ ĐỌC, qua gateway có audit);
  · chuyển khoản đọc khj_bl.bank_notifications (webhook V2 ghi) — cùng nguồn với CHECK BILL.

Khác V3 (sửa các điểm yếu của bản cũ):
  · sửa hóa đơn (như V3) đi qua cổng gateway.pmv_billsell_edit: khóa ghi, kiểm trigger, ràng TM+CK = tổng, audit;
  · bỏ SSE while-True (mỗi tab giữ 1 luồng, ~5 truy vấn/2 giây) → làm mới 5 giây/lần và CHỈ đọc lại khi chữ ký đổi;
  · thay khối "còn thiếu" bằng dòng GIAO DỊCH realtime đọc khj_bl.money_flow (kiểu bảng bình luận livestream);
  · gỡ mã khỏi danh sách = ĐÁNH DẤU (còn truy vết) thay vì xóa cứng; unique (ngày, mã SP) chặn 2 máy quét cùng lúc.
"""
import datetime as dt
import logging
import re

from django.db import IntegrityError, connection
from django.utils import timezone

from apps.pmv import money as M
from .models import GoldCheckEntry

log = logging.getLogger(__name__)

MA_SP = re.compile(r'^[0-9][A-Z](?:[0-9]{6}|[0-9]{8})$')     # 1 số + 1 chữ hoa + 6 hoặc 8 số (y luật V3)
N9999 = 'N9999'
TT_NHAN = {'I': 'Trong kho', 'O': 'Đã xuất', 'S': 'Đã bán'}
TRANG_THAI_HD = {'C': 'Hoàn thành', 'W': 'Đang chờ', 'D': 'Hủy', 'R': 'Trả lại'}
DICH_VU = {'RETAIL': 'Bán hàng', 'GOLD_BUY': 'Thâu vàng', 'DEPOSIT': 'Đặt cọc', 'PAWN': 'Cầm đồ', 'REDEEM': 'Chuộc đồ'}
TT_TIEN = {'waiting': 'Chờ tiền', 'recorded': 'Đã ghi', 'partial': 'Thiếu', 'confirmed': 'Đủ tiền',
           'review': 'Cần kiểm', 'void': 'Đã hủy'}


class LoiQuet(Exception):
    """Lỗi nghiệp vụ hiện thẳng cho người quét."""


def chi(value):
    """Trọng lượng KK lưu theo đơn vị chi × 100 → đổi ra CHI (2 số lẻ)."""
    return round(float(M.dec(value)) / 100, 2)


def loai_ma(raw):
    """('sp', mã) · ('hd', mã HĐ dạng 26-09-23-000096) · (None, '') — cùng luật nhận mã của V3."""
    ma = str(raw or '').strip().upper()
    if MA_SP.match(ma):
        return 'sp', ma
    so = re.sub(r'\D', '', ma)
    if len(so) >= 7:
        return 'hd', f'{so[:2]}-{so[2:4]}-{so[4:6]}-{so[6:].zfill(6)}'
    return None, ''


def nhan_vien_ds(c):
    return c.query("SELECT EmpID, EmpName FROM T_EMPLOYEE WITH (NOLOCK) "
                   "WHERE Active=1 AND LTRIM(RTRIM(EmpName))<>'' ORDER BY EmpName")


def _trang_thai_sp(c, codes):
    """{mã SP: {tt, ngay_xuat, khach, nv_ban}} — T_PRODUCT + lượt bán gần nhất (CHỈ ĐỌC máy KK)."""
    codes = [x for x in {str(v or '').strip().upper() for v in codes} if x]
    ra = {}
    for i in range(0, len(codes), 200):
        lo = codes[i:i + 200]
        rows = c.query(
            "SELECT p.ProductCode, p.Status AS TT, p.SellOutDate AS NgayXuat, ISNULL(c.CustName,'') AS CustName, "
            "ISNULL(e.EmpName,'') AS EmpName FROM T_PRODUCT p WITH (NOLOCK) "
            "OUTER APPLY (SELECT TOP 1 bs.CustID, bs.EmpID FROM TRN_RT_BUYSELL_SELL s WITH (NOLOCK) "
            "  JOIN TRN_RT_BUYSELL bs WITH (NOLOCK) ON bs.TrnID = s.TrnID "
            "  WHERE s.ProductCode = p.ProductCode ORDER BY bs.TrnDate DESC, bs.TrnTime DESC, bs.TrnID DESC) ban "
            "LEFT JOIN I_CUSTOMER c WITH (NOLOCK) ON c.CustID = ban.CustID "
            "LEFT JOIN T_EMPLOYEE e WITH (NOLOCK) ON e.EmpID = ban.EmpID "
            f"WHERE UPPER(p.ProductCode) IN ({','.join('?' * len(lo))})", tuple(lo))
        for r in rows:
            ra[str(r['ProductCode']).strip().upper()] = {
                'tt': str(r['TT'] or '').strip().upper(), 'ngay_xuat': r['NgayXuat'],
                'khach': r['CustName'], 'nv_ban': r['EmpName']}
    return ra


def danh_sach(c, ngay):
    """Danh sách đã kiểm trong ngày + trạng thái sản phẩm đọc LẠI từ KK (I/O/S)."""
    rows = list(GoldCheckEntry.objects.filter(ngay=ngay, go_luc__isnull=True))
    tt = _trang_thai_sp(c, [r.product_code for r in rows]) if rows else {}
    ra = []
    for r in rows:
        t = tt.get(r.product_code, {})
        ra.append({'id': r.id, 'ma': r.product_code, 'vang': r.gold_code, 'ten': r.product_desc,
                   'chi': chi(r.total_weight), 'nv': r.nhan_vien, 'quet_luc': timezone.localtime(r.quet_luc),
                   'tt': t.get('tt', ''), 'tt_nhan': TT_NHAN.get(t.get('tt', ''), 'Không thấy'),
                   'ngay_xuat': t.get('ngay_xuat'), 'khach': t.get('khach', ''), 'nv_ban': t.get('nv_ban', '')})
    return ra


SONG_VAO, SONG_RA = 3 * 60, 10 * 60     # dòng tiền VÀO sống 3 phút · tiền RA sống 10 phút (GĐ chốt 23/09/2026)
# Màu theo nhóm nghiệp vụ: tiền VÀO tiệm (bán hàng · chuộc đồ · cọc) vàng kim · tiền RA (thâu vàng · cầm đồ) đỏ
NHOM_MAU = {'RETAIL': 'vao', 'REDEEM': 'vao', 'DEPOSIT': 'vao', 'GOLD_BUY': 'ra', 'PAWN': 'ra'}


def _gio_vn(value):
    """Mọi mốc thời gian về GIỜ VN dạng KHÔNG tz để so sánh được với nhau (DB lưu giờ VN)."""
    if not value:
        return None
    if isinstance(value, str):
        try:
            value = dt.datetime.fromisoformat(value[:19])
        except ValueError:
            return None
    return timezone.localtime(value).replace(tzinfo=None) if timezone.is_aware(value) else value


def _luc_giao_dich(happened_at, created_at):
    """Giờ giao dịch — ưu tiên mốc lúc quầy thao tác, không có thì lấy giờ tạo dòng."""
    return _gio_vn(happened_at) or _gio_vn(created_at)


def giao_dich(ngay, tu_id=0):
    """DÒNG GIAO DỊCH trong ngày — khj_bl.money_flow (KHÔNG hỏi KK). tu_id > 0 chỉ lấy dòng mới hơn.

    Trang hiện kiểu bảng bình luận livestream: dòng mới trượt vào đầu bảng, dòng vừa đổi tiền/trạng thái thì nháy.
    Chữ ký của mỗi dòng gồm các cột GĐ chốt phải theo dõi: expected/cash/bank/cus_cash/cus_change/payment_status.
    """
    from .models import MoneyFlow
    qs = MoneyFlow.objects.filter(business_date=ngay)
    if tu_id:
        qs = qs.filter(id__gt=tu_id)
    rows = list(qs.order_by('-id').values(
        'id', 'service', 'direction', 'source_bill_code', 'source_id', 'customer_name', 'expected_amount',
        'cash_amount', 'bank_amount', 'cus_cash', 'cus_change', 'payment_status', 'is_void',
        'source_snapshot', 'created_at')[:60])
    bay_gio = _gio_vn(timezone.now())
    ra = []
    for r in rows:
        snap = r['source_snapshot'] or {}
        if isinstance(snap, str):
            import json
            try:
                snap = json.loads(snap)
            except ValueError:
                snap = {}
        mobile = snap.get('mobile') or {}
        luc = _luc_giao_dich(mobile.get('happened_at'), r['created_at'])
        ra_tien = str(r['direction']).upper() == 'OUT'
        # GĐ chốt 23/09/2026: dòng TIỀN VÀO sống 3 phút, dòng TIỀN RA sống 10 phút rồi tự rời bảng
        if luc and (bay_gio - luc).total_seconds() > (SONG_RA if ra_tien else SONG_VAO):
            continue
        ra.append({
            'id': r['id'], 'gio': luc.strftime('%H:%M') if luc else '',
            'dich_vu': DICH_VU.get(r['service'], r['service']), 'nhom': NHOM_MAU.get(r['service'], 'vao'),
            'nv': mobile.get('employee_name') or mobile.get('employee_pmv') or '',
            'ma': r['source_bill_code'] or r['source_id'], 'khach': r['customer_name'] or 'Khách lẻ',
            'tong': M.dec(r['expected_amount']), 'tien_mat': M.dec(r['cash_amount']), 'ck': M.dec(r['bank_amount']),
            'khach_dua': M.dec(r['cus_cash']), 'thoi_lai': M.dec(r['cus_change']),
            'tt': r['payment_status'], 'tt_nhan': TT_TIEN.get(r['payment_status'], r['payment_status']),
            'huy': bool(r['is_void']), 'ra_tien': ra_tien,
            'ky': '|'.join(str(x) for x in (r['expected_amount'], r['cash_amount'], r['bank_amount'],
                                            r['cus_cash'], r['cus_change'], r['payment_status'], r['is_void'])),
        })
    return ra


def sua_hoa_don(c, trn_id, bill_code, posted):
    """Sửa hóa đơn bán trên KK (như V3) — nhưng qua cổng an toàn gateway.pmv_billsell_edit."""
    from apps.pmv import gateway
    hd = hoa_don(c, bill_code)
    if not hd or str(hd['TrnID']).strip() != str(trn_id).strip():
        raise LoiQuet('Hóa đơn vừa thay đổi trên KK. Mở lại rồi sửa.')
    doi = {}
    for cot, khoa in (('CustID', 'cust_id'), ('EmpID', 'emp_id'), ('Desc4', 'desc4'), ('Desc5', 'desc5')):
        giu = str(posted.get(khoa) or '').strip()
        if giu != str(hd.get(cot) or '').strip():
            doi[cot] = giu
    tong = M.dec(hd['PayAmount'])
    tien_mat, ck = M.dec(posted.get('cash_pay')), M.dec(posted.get('card_pay'))
    if tien_mat + ck != tong:
        raise LoiQuet(f'Tiền mặt + chuyển khoản phải bằng tổng thanh toán {M.money_vn(tong)}.')
    if tien_mat != M.dec(hd['CashPay']) or ck != M.dec(hd['CardPay']):
        doi['CashPay'], doi['CardPay'] = tien_mat, ck
    if not doi:
        return 'Không có thay đổi nào.'
    try:
        gateway.pmv_billsell_edit(trn_id, doi)
    except Exception as exc:
        raise LoiQuet(str(exc))
    return f'✓ Đã lưu hóa đơn {bill_code}: ' + ', '.join(sorted(doi))


def quet(c, raw, nhan_vien, ngay):
    """Quét 1 mã. Trả (kiểu, dữ liệu, tin nhắn, mức) — mức: ok · canh_bao. Lỗi nghiệp vụ ném LoiQuet."""
    kieu, ma = loai_ma(raw)
    if not kieu:
        raise LoiQuet('Mã không đúng dạng mã sản phẩm hay mã hóa đơn.')
    if kieu == 'hd':
        hd = hoa_don(c, ma)
        if not hd:
            raise LoiQuet(f'Không tìm thấy hóa đơn {ma} trên máy KK.')
        return 'hd', hd, f'Đã mở hóa đơn {ma}.', 'ok'
    cu = GoldCheckEntry.objects.filter(ngay=ngay, product_code=ma, go_luc__isnull=True).first()
    if cu:      # quét lần 2 = GỠ khỏi danh sách (y V3), nhưng giữ dòng để truy vết
        cu.go_luc, cu.go_boi = timezone.now(), (nhan_vien or '')[:150]
        cu.save(update_fields=['go_luc', 'go_boi'])
        return 'sp', None, f'Đã gỡ {ma} khỏi danh sách kiểm.', 'canh_bao'
    rows = c.query("SELECT TOP 1 GoldCode, ProductCode, ProductDesc, TotalWeight, Status, SellOutDate "
                   "FROM T_PRODUCT WITH (NOLOCK) WHERE UPPER(ProductCode)=?", (ma,))
    if not rows:
        raise LoiQuet(f'Không tìm thấy sản phẩm {ma} trên máy KK.')
    sp = rows[0]
    tt = str(sp['Status'] or '').strip().upper()
    if tt in ('O', 'S'):
        ngay_xuat = sp['SellOutDate']
        raise LoiQuet(f'{ma} — {TT_NHAN[tt]}'
                      + (f' ngày {ngay_xuat:%d/%m/%Y}' if hasattr(ngay_xuat, 'strftime') else '')
                      + ', không còn trong kho.')
    vang = str(sp['GoldCode'] or '').strip()
    try:
        GoldCheckEntry.objects.update_or_create(
            ngay=ngay, product_code=ma,
            defaults={'gold_code': vang, 'product_desc': str(sp['ProductDesc'] or '').strip(),
                      'total_weight': M.dec(sp['TotalWeight']), 'nhan_vien': (nhan_vien or '')[:150],
                      'go_luc': None, 'go_boi': ''})
    except IntegrityError:
        raise LoiQuet(f'{ma} vừa được máy khác quét — tải lại danh sách.')
    if vang.upper() != N9999:
        return 'sp', None, f'✓ {ma} · {vang or "?"} — KHÔNG thuộc vàng 9999, vẫn ghi vào danh sách kiểm.', 'canh_bao'
    return 'sp', None, f'✓ {ma} · {chi(sp["TotalWeight"])} chỉ · {str(sp["ProductDesc"] or "").strip()}', 'ok'


def go_dong(pk, nguoi):
    """Nút ✕ trên bảng — gỡ 1 dòng khỏi danh sách kiểm (vẫn giữ dòng)."""
    row = GoldCheckEntry.objects.filter(pk=pk, go_luc__isnull=True).first()
    if not row:
        return ''
    row.go_luc, row.go_boi = timezone.now(), (nguoi or '')[:150]
    row.save(update_fields=['go_luc', 'go_boi'])
    return row.product_code


# ─────────────────────────── hóa đơn ───────────────────────────
def hoa_don(c, bill_code):
    """Chi tiết 1 hóa đơn bán (header + hàng bán ra + hàng thu vào) — CHỈ ĐỌC."""
    rows = c.query(
        "SELECT TOP 1 b.TrnID, b.BillCode, b.TrnDate, b.TrnTime, b.CustID, ISNULL(c.CustName,'') AS CustName, "
        "ISNULL(c.Phone,'') AS Phone, ISNULL(c.CMND,'') AS CMND, ISNULL(c.Address,'') AS Address, "
        "b.SellTotalAmount, b.BuyTotalAmount, b.TotalAmount, b.Discount, b.PayAmount, b.Status, b.IsDel, "
        "ISNULL(b.CashPay,0) AS CashPay, ISNULL(b.CardPay,0) AS CardPay, b.Desc4, b.Desc5, "
        "ISNULL(e.EmpName,'') AS EmpName FROM TRN_RT_BUYSELL b WITH (NOLOCK) "
        "LEFT JOIN I_CUSTOMER c WITH (NOLOCK) ON c.CustID = b.CustID "
        "LEFT JOIN T_EMPLOYEE e WITH (NOLOCK) ON e.EmpID = b.EmpID "
        "WHERE b.BillCode=? ORDER BY b.TrnDate DESC, b.TrnTime DESC, b.TrnID DESC", (bill_code,))
    if not rows:
        return None
    h = dict(rows[0])
    h['trang_thai'] = TRANG_THAI_HD.get(str(h['Status'] or '').strip().upper(), 'Không rõ')
    h['da_huy'] = str(h['IsDel'] or '0') != '0'
    trn = str(h['TrnID']).strip()
    ban = c.query("SELECT ProductCode, ProductDesc, GoldCode, TotalWeight, PriceUnit, TaskPrice, SellAmount "
                  "FROM TRN_RT_BUYSELL_SELL WITH (NOLOCK) WHERE TrnID=?", (trn,))
    thu = c.query("SELECT GoldCode, GoldWeight, DiamondWeight, TotalGoldWeight, BuyRate, BuyAmount "
                  "FROM TRN_RT_BUYSELL_BUYGOLD WITH (NOLOCK) WHERE TrnID=?", (trn,))
    h['ban'] = [dict(r, chi=chi(r['TotalWeight'])) for r in ban]
    h['thu'] = [dict(r, chi=chi(r['TotalGoldWeight'] or r['GoldWeight'])) for r in thu]
    return h


# ─────────────────────────── 3 danh sách nhanh trong ngày ───────────────────────────
def ds_chuyen_khoan(ngay):
    """CK trong ngày — khj_bl.bank_notifications (KHÔNG hỏi KK), kèm mã chứng từ skill đã tách."""
    with connection.cursor() as cur:
        cur.execute("SELECT transaction_time, direction, trans_amount, bank_name, bank_number, ma_chung_tu, "
                    "loai_chung_tu, description FROM bank_notifications "
                    "WHERE transaction_time >= %s AND transaction_time < %s "
                    "ORDER BY transaction_time DESC, id DESC",
                    [ngay.isoformat(), (ngay + dt.timedelta(days=1)).isoformat()])
        cot = [c[0] for c in cur.description]
        rows = [dict(zip(cot, r)) for r in cur.fetchall()]
    for r in rows:
        r['gio'] = str(r['transaction_time'] or '')[11:16]
        r['vao'] = str(r['direction']).lower() == 'in'
    vao = sum((M.dec(r['trans_amount']) for r in rows if r['vao']), M.D0)
    ra = sum((M.dec(r['trans_amount']) for r in rows if not r['vao']), M.D0)
    return rows, {'so': len(rows), 'vao': vao, 'ra': ra, 'rong': vao - ra}


def ds_phieu_thau(c, ngay):
    rows = c.query(
        "SELECT g.TrnID, g.BillCode, g.TrnTime, ISNULL(c.CustName,'') AS CustName, ISNULL(e.EmpName,'') AS EmpName, "
        "g.TotalAmount, ISNULL(g.CardPay,0) AS CardPay, g.Status, g.IsDel FROM TRN_RT_BUYGOLD g WITH (NOLOCK) "
        "LEFT JOIN I_CUSTOMER c WITH (NOLOCK) ON c.CustID=g.CustID "
        "LEFT JOIN T_EMPLOYEE e WITH (NOLOCK) ON e.EmpID=g.EmpID "
        "WHERE g.CreatedDate >= ? AND g.CreatedDate < ? ORDER BY g.BillCode DESC",
        (ngay.isoformat(), (ngay + dt.timedelta(days=1)).isoformat()))
    return rows, {'so': len(rows), 'tong': sum((M.dec(r['TotalAmount']) for r in rows), M.D0),
                  'ck': sum((M.dec(r['CardPay']) for r in rows), M.D0)}


def ds_phieu_ban(c, ngay):
    rows = c.query(
        "SELECT b.TrnID, b.BillCode, b.TrnTime, ISNULL(c.CustName,'') AS CustName, ISNULL(e.EmpName,'') AS EmpName, "
        "b.PayAmount, ISNULL(b.CashPay,0) AS CashPay, ISNULL(b.CardPay,0) AS CardPay, b.Status, b.IsDel "
        "FROM TRN_RT_BUYSELL b WITH (NOLOCK) LEFT JOIN I_CUSTOMER c WITH (NOLOCK) ON c.CustID=b.CustID "
        "LEFT JOIN T_EMPLOYEE e WITH (NOLOCK) ON e.EmpID=b.EmpID "
        "WHERE b.CreatedDate >= ? AND b.CreatedDate < ? ORDER BY b.BillCode DESC",
        (ngay.isoformat(), (ngay + dt.timedelta(days=1)).isoformat()))
    return rows, {'so': len(rows), 'tong': sum((M.dec(r['PayAmount']) for r in rows), M.D0),
                  'tien_mat': sum((M.dec(r['CashPay']) for r in rows), M.D0),
                  'ck': sum((M.dec(r['CardPay']) for r in rows), M.D0)}


def chu_ky(ngay):
    """Chữ ký NHẸ của ngày (chỉ MySQL) — trang chỉ tải lại bảng khi chữ ký đổi, thay SSE 2 giây của V3."""
    with connection.cursor() as cur:
        cur.execute("SELECT COUNT(*), COALESCE(MAX(id),0), COALESCE(MAX(go_luc),'') FROM gold_check_entry WHERE ngay=%s",
                    [ngay])
        a = cur.fetchone()
        # Các cột GĐ chốt phải theo dõi: expected/cash/bank/cus_cash/cus_change/payment_status (+ id mới, + hủy)
        cur.execute("SELECT COUNT(*), COALESCE(MAX(id),0), COALESCE(SUM(expected_amount),0), "
                    "COALESCE(SUM(cash_amount),0), COALESCE(SUM(bank_amount),0), COALESCE(SUM(cus_cash),0), "
                    "COALESCE(SUM(cus_change),0), COALESCE(SUM(is_void),0), "
                    "SUM(CASE WHEN payment_status='confirmed' THEN 1 ELSE 0 END), "
                    "SUM(CASE WHEN payment_status='partial' THEN 1 ELSE 0 END), "
                    "SUM(CASE WHEN payment_status='waiting' THEN 1 ELSE 0 END), "
                    "SUM(CASE WHEN payment_status='review' THEN 1 ELSE 0 END), "
                    "SUM(CASE WHEN payment_status='void' THEN 1 ELSE 0 END) "
                    "FROM money_flow WHERE business_date=%s", [ngay])
        b = cur.fetchone()
    return '|'.join(str(x) for x in (*a, *b))


# ─────────────────────────── views ───────────────────────────
# TRANG ĐỨNG RIÊNG (GĐ chốt 23/09/2026): KHÔNG đăng nhập, KHÔNG menu — @login_not_required vượt LoginRequiredMiddleware.
from django.contrib.auth.decorators import login_not_required       # noqa: E402
from django.http import HttpResponse, JsonResponse                 # noqa: E402
from django.shortcuts import render                                 # noqa: E402
from django.views.decorators.http import require_GET, require_POST  # noqa: E402

from .check_bill import _nguoi                                      # noqa: E402  (cùng cách xác định "người thao tác")


def _ngay(request):
    raw = (request.GET.get('d') or request.POST.get('d') or '').strip()
    try:
        return dt.date.fromisoformat(raw[:10])
    except ValueError:
        return timezone.localdate()


def _nv(request):
    return (request.GET.get('nv') or request.POST.get('nv') or '').strip()


def _khoi(request, ngay, nv, tin='', muc='', hd=None):
    """Khối làm việc: bảng HÀNG ĐÃ LẤY RA + dòng GIAO DỊCH (hoặc panel hóa đơn khi vừa quét mã HĐ)."""
    from . import services as S
    ctx = {'ngay': ngay, 'nv': nv, 'tin': tin, 'muc': muc, 'hd': hd, 'hom_nay': timezone.localdate()}
    try:
        ctx.update(rows=danh_sach(S.client('check_gold'), ngay), gd=giao_dich(ngay), chu_ky=chu_ky(ngay))
    except Exception as exc:
        log.exception('CHECK GOLD: không đọc được dữ liệu ngày %s', ngay)
        from .customer import error_message
        ctx['loi'] = error_message(exc)
    return ctx


@login_not_required
@require_GET
def trang(request):
    from . import services as S
    ngay, nv = _ngay(request), _nv(request)
    ctx = _khoi(request, ngay, nv)
    try:
        ctx['nhan_vien_ds'] = nhan_vien_ds(S.client('check_gold'))
    except Exception:
        ctx['nhan_vien_ds'] = []
    tep = 'pos/_check_gold_khoi.html' if request.headers.get('HX-Request') else 'pos/check_gold.html'
    return render(request, tep, ctx)


@login_not_required
@require_POST
def quet_view(request):
    from . import services as S
    ngay, nv = _ngay(request), _nv(request)
    hd = None
    try:
        kieu, du_lieu, tin, muc = quet(S.client('check_gold'), request.POST.get('ma', ''), nv, ngay)
        hd = du_lieu if kieu == 'hd' else None
    except LoiQuet as exc:
        tin, muc = str(exc), 'loi'
    except Exception as exc:
        log.exception('CHECK GOLD: quét lỗi')
        from .customer import error_message
        tin, muc = error_message(exc), 'loi'
    return render(request, 'pos/_check_gold_khoi.html', _khoi(request, ngay, nv, tin, muc, hd))


@login_not_required
@require_POST
def go_view(request, pk):
    ngay, nv = _ngay(request), _nv(request)
    ma = go_dong(pk, _nguoi(request))
    tin = f'Đã gỡ {ma} khỏi danh sách kiểm.' if ma else 'Dòng đã được gỡ trước đó.'
    return render(request, 'pos/_check_gold_khoi.html', _khoi(request, ngay, nv, tin, 'canh_bao'))


@login_not_required
@require_GET
def theo_doi(request):
    """Nhịp 2 giây (chỉ MySQL): trả CHỮ KÝ + trọn dòng GIAO DỊCH để bảng livestream tự nháy/chèn dòng mới.

    Trang chỉ tải lại bảng HÀNG ĐÃ LẤY RA (có hỏi KK) khi phần chữ ký của bảng đó đổi — tiết kiệm máy KK."""
    ngay = _ngay(request)
    ky = chu_ky(ngay)
    return JsonResponse({'chu_ky': ky, 'doi': ky != (request.GET.get('ky') or ''),
                         'gd': [{**g, 'tong': str(g['tong']), 'tien_mat': str(g['tien_mat']), 'ck': str(g['ck']),
                                 'khach_dua': str(g['khach_dua']), 'thoi_lai': str(g['thoi_lai'])}
                                for g in giao_dich(ngay)]})


@login_not_required
@require_GET
def sua_form(request, bill_code):
    """Popup SỬA HÓA ĐƠN (như V3): khách · nhân viên · ngân hàng · ghi chú CK · tách tiền mặt/chuyển khoản."""
    from . import services as S
    c = S.client('check_gold')
    try:
        hd = hoa_don(c, bill_code)
        nv = nhan_vien_ds(c)
        bank = c.query("SELECT NumberBank, AccName, BankName FROM I_BankCard WITH (NOLOCK) WHERE Active=1 "
                       "ORDER BY NumberBank DESC")
    except Exception as exc:
        log.exception('CHECK GOLD: mở popup sửa hóa đơn lỗi')
        from .customer import error_message
        return render(request, 'pos/_check_gold_sua.html', {'loi': error_message(exc), 'ngay': _ngay(request)})
    if not hd:
        return render(request, 'pos/_check_gold_sua.html', {'loi': f'Không tìm thấy hóa đơn {bill_code}.',
                                                            'ngay': _ngay(request)})
    return render(request, 'pos/_check_gold_sua.html', {'hd': hd, 'nhan_vien_ds': nv, 'bank_ds': bank,
                                                        'ngay': _ngay(request), 'nv': _nv(request)})


@login_not_required
@require_POST
def sua_luu(request, bill_code):
    """Lưu popup sửa hóa đơn → ghi KK qua cổng an toàn. Lỗi thì giữ popup, không ghi gì."""
    from . import services as S
    ngay, nv = _ngay(request), _nv(request)
    c = S.client('check_gold')
    try:
        tin = sua_hoa_don(c, request.POST.get('trn_id', ''), bill_code, request.POST)
    except LoiQuet as exc:
        hd = hoa_don(c, bill_code)
        return render(request, 'pos/_check_gold_sua.html', {'hd': hd, 'loi': str(exc), 'ngay': ngay, 'nv': nv,
                                                            'nhan_vien_ds': nhan_vien_ds(c),
                                                            'bank_ds': c.query("SELECT NumberBank, AccName, BankName "
                                                                               "FROM I_BankCard WITH (NOLOCK) WHERE Active=1 "
                                                                               "ORDER BY NumberBank DESC")})
    except Exception as exc:
        log.exception('CHECK GOLD: lưu hóa đơn lỗi')
        from .customer import error_message
        return render(request, 'pos/_check_gold_sua.html', {'hd': hoa_don(c, bill_code), 'loi': error_message(exc),
                                                            'ngay': ngay, 'nv': nv, 'nhan_vien_ds': [], 'bank_ds': []})
    ra = render(request, 'pos/_check_gold_khoi.html', _khoi(request, ngay, nv, tin, 'ok', hoa_don(c, bill_code)))
    ra.content = b'<div id="cg-modal" hx-swap-oob="true"></div>' + ra.content       # đóng popup
    return ra


@login_not_required
@require_GET
def tim_khach(request):
    """Gợi ý khách hàng cho popup sửa (>= 2 ký tự) — chỉ đọc."""
    from . import services as S
    q = (request.GET.get('q') or '').strip()
    if len(q) < 2:
        return JsonResponse({'rows': []})
    try:
        rows = S.tim_khach(q, limit=10)
    except Exception:
        log.exception('CHECK GOLD: tìm khách lỗi')
        return JsonResponse({'rows': []})
    return JsonResponse({'rows': [{'id': r.get('CustID'), 'ten': r.get('CustName') or '',
                                   'dt': r.get('Phone') or '', 'cccd': r.get('CMND') or ''} for r in rows]})


@login_not_required
@require_GET
def hoa_don_view(request, bill_code):
    from . import services as S
    ngay, nv = _ngay(request), _nv(request)
    try:
        hd = hoa_don(S.client('check_gold'), bill_code)
    except Exception as exc:
        log.exception('CHECK GOLD: đọc hóa đơn lỗi')
        from .customer import error_message
        return render(request, 'pos/_check_gold_khoi.html', _khoi(request, ngay, nv, error_message(exc), 'loi'))
    if not hd:
        return render(request, 'pos/_check_gold_khoi.html',
                      _khoi(request, ngay, nv, f'Không tìm thấy hóa đơn {bill_code}.', 'loi'))
    return render(request, 'pos/_check_gold_khoi.html', _khoi(request, ngay, nv, f'Hóa đơn {bill_code}.', 'ok', hd))


@login_not_required
@require_GET
def manifest(request):
    """Manifest để lưu trang ra màn hình chính (điện thoại/Windows) với đúng biểu tượng GĐ cấp."""
    from django.templatetags.static import static
    return JsonResponse({
        'name': 'CHECK GOLD · Kim Hạnh 2', 'short_name': 'CHECK GOLD',
        'description': 'Hàng lấy ra khỏi tủ + dòng giao dịch realtime',
        'start_url': '/banle/bao-cao/check-gold/', 'display': 'standalone',
        'background_color': '#FDFCF9', 'theme_color': '#C9A02C',
        'icons': [{'src': static('img/check-gold-32.png'), 'sizes': '32x32', 'type': 'image/png'},
                  {'src': static('img/check-gold.png'), 'sizes': '100x100', 'type': 'image/png'},
                  {'src': static('img/check-gold-180.png'), 'sizes': '180x180', 'type': 'image/png',
                   'purpose': 'any maskable'}]})


@login_not_required
@require_GET
def popup(request, kieu):
    """3 danh sách nhanh trong ngày: chuyển khoản · phiếu thâu · phiếu bán."""
    from . import services as S
    ngay = _ngay(request)
    try:
        if kieu == 'ck':
            rows, tong = ds_chuyen_khoan(ngay)
        elif kieu == 'thau':
            rows, tong = ds_phieu_thau(S.client('check_gold'), ngay)
        elif kieu == 'ban':
            rows, tong = ds_phieu_ban(S.client('check_gold'), ngay)
        else:
            return HttpResponse('')
    except Exception as exc:
        log.exception('CHECK GOLD: popup %s lỗi', kieu)
        from .customer import error_message
        return render(request, 'pos/_check_gold_popup.html', {'kieu': kieu, 'ngay': ngay, 'loi': error_message(exc)})
    return render(request, 'pos/_check_gold_popup.html', {'kieu': kieu, 'ngay': ngay, 'rows': rows, 'tong': tong})
