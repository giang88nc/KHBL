"""Group-level bank evidence. PMV is read-only; links never overwrite bank labels."""
import datetime as dt
import hashlib
import json
import re
from collections import Counter
from decimal import Decimal

from django.core import signing
from django.db import connection, transaction
from django.db.models import Q
from django.http import JsonResponse
from django.utils import timezone
from django.contrib.auth.decorators import login_not_required
from django.views.decorators.http import require_POST

from apps.pmv import money as M
from apps.pmv.models import PmvState, UserModuleAccess
from . import services as S
from .bank_reconcile import single_run, bank_time
from .invoice_display import compact, groups_for, membership_for
from .models import GoldBill, ThauNhom, ThauPaymentLink, BankReconcileState
from .transfers import query

LABELS = {'unconfirmed': 'Chưa xác nhận', 'confirmed': 'Đã xác nhận CK',
          'partial': 'Đã chuyển một phần', 'review': 'Cần kiểm tra', 'na': 'Không áp dụng'}
BANK_FIELDS = ('id,provider,ref_code,bank_number,bank_name,trans_amount,transaction_time,direction,description,bill_code_raw,'
               'ma_chung_tu,loai_chung_tu')


def serial(value):
    return json.loads(json.dumps(value, default=str, sort_keys=True))


def key(ids):
    return hashlib.sha256('|'.join(sorted(ids)).encode()).hexdigest()


def evidence(bank):
    # Descriptive labels/is_check can change independently; financial evidence cannot.
    return serial({k: bank.get(k) for k in ('id', 'provider', 'ref_code', 'bank_number',
                  'trans_amount', 'transaction_time', 'direction', 'description')})


def permitted(user):
    return user.is_authenticated and (user.is_superuser or UserModuleAccess.objects.filter(
        user=user, module='THAU_VAO', can_view=True, can_approve=True).exists())


def catalog(d1, d2):
    raw, live = S.hoa_don_loc(d1, d2, loai='THAU', limit=1000, force_live=True)
    groups = groups_for([r['TrnID'] for r in raw])
    membership = membership_for(groups)
    cached = {g.trn_id: g for g in GoldBill.objects.filter(trn_id__in=[r['TrnID'] for r in raw])
              .only('trn_id', 'tien_ck', 'tong').order_by()}
    orders = compact(raw, groups)
    for row in orders:
        row['nghiep_vu'] = ThauNhom.THAU
        ids = sorted(m['TrnID'] for m in row['members'])
        group = membership.get(row['TrnID'])
        receiver = {k: getattr(group, k, '') for k in ('ck_bank', 'ck_stk', 'ck_ten', 'ck_nd')}
        required = sum((-M.dec(m.get('CardPay')) for m in row['members'] if M.dec(m.get('CardPay')) < 0), M.D0)
        problems = []
        if row['partial_group'] or len(raw) >= 1000:
            problems.append('Danh sách chưa chứa đầy đủ nhóm hoặc đã đạt giới hạn 1.000 phiếu.')
        if any(m['Status'] != 'C' or str(m['IsDel']) != '0' for m in row['members']):
            problems.append('Phiếu chưa chốt hoặc đã bị hủy.')
        if group and M.dec(group.tien_ck) != required:
            problems.append('Tiền CK lưu ở nhóm khác PMV.')
        anchor = group.trn_ids[0] if group else ids[0]
        gb = cached.get(anchor)
        if gb and (M.dec(gb.tien_ck) != required or M.dec(gb.tong) != M.dec(row['SoTien'])):
            problems.append('Thông tin gold_bill khác tổng nhóm PMV.')
        snapshot = {'ids': ids, 'receiver': receiver, 'required': str(required),
                    'bills': serial([{k: m.get(k) for k in ('TrnID', 'BillCode', 'CreatedDate', 'Status', 'IsDel', 'CardPay', 'SoTien')}
                                     for m in sorted(row['members'], key=lambda m: m['TrnID'])])}
        row.update(order_key=key(ids), ids=ids, required=required, snapshot=snapshot, problems=problems, live=live, bank=group)
    doi = catalog_doi(d1, d2) + catalog_camdo(d1, d2)
    if doi:     # chỉ xếp lại khi thật có hóa đơn đổi — danh sách chỉ-thâu giữ nguyên thứ tự cũ
        orders = sorted(orders + doi, key=lambda o: (str(o.get('TrnDate') or ''), str(o.get('TrnTime') or ''),
                                                    str(o.get('CreatedDate') or '')), reverse=True)
    return orders, live


# ─────── HÓA ĐƠN BÁN-ĐỔI DƯ — tiệm chuyển khoản trả khách (GĐ chốt 19/09/2026) ───────
# Nguồn: nhóm ThauNhom(nghiep_vu='doi') do màn Bán hàng ghi lúc THANH TOÁN + đúng hóa đơn TRN_RT_BUYSELL đó trên KK.
# Khác phiếu thâu ở 3 điểm, còn lại đi CHUNG bộ luật đối soát bên dưới:
#   · tiền cần chuyển lấy từ NHÓM (tien_ck) — CardPay trên KK chỉ được ghi SAU khi khớp (phương án a);
#   · snapshot KHÔNG gồm CardPay/CashPay, vì chính việc ghi CK sau khớp làm hai cột đó đổi;
#   · CardPay trên KK phải là 0 hoặc đúng số của một liên kết từng có, lạ hơn thì "Cần kiểm tra".
DOI_COT = ('TrnID', 'BillCode', 'CreatedDate', 'Status', 'IsDel', 'SoTien')


def catalog_doi(d1, d2):
    from apps.pmv.client import PmvClient

    start = dt.datetime.combine(dt.date.fromisoformat(d1), dt.time.min)
    if timezone.is_aware(timezone.now()):
        start = timezone.make_aware(start)
    latest = {}
    for g in ThauNhom.objects.filter(nghiep_vu=ThauNhom.DOI, created_at__gte=start).order_by('-pk'):
        for t in g.trn_ids or []:
            latest.setdefault(t, g)
    if not latest:
        return []           # chưa có hóa đơn đổi nào có nhóm CK → không tốn thêm lượt đọc máy KK
    c = PmvClient('kk', tag='ck_doi')
    ids, rows = sorted(latest), []
    for i in range(0, len(ids), 500):
        lo = ids[i:i+500]
        rows += c.query(
            "SELECT b.TrnID, b.BillCode, b.TrnDate, b.TrnTime, b.Status, b.IsDel, b.PayAmount AS SoTien, "
            "ISNULL(b.CardPay,0) AS CardPay, b.CreatedDate, b.CustID, ISNULL(c.CustName,'') AS CustName, "
            "ISNULL(c.Phone,'') AS Phone, ISNULL(c.CMND,'') AS CMND, ISNULL(e.EmpName,'') AS EmpName "
            "FROM TRN_RT_BUYSELL b WITH (NOLOCK) LEFT JOIN I_CUSTOMER c WITH (NOLOCK) ON c.CustID = b.CustID "
            "LEFT JOIN T_EMPLOYEE e WITH (NOLOCK) ON e.EmpID = b.EmpID "
            f"WHERE b.TrnID IN ({','.join('?' * len(lo))}) "
            "AND b.TrnDate >= CAST(? AS datetime) AND b.TrnDate < DATEADD(day,1,CAST(? AS datetime))",
            (*lo, d1, d2))
    orders = []
    for r in rows:
        group = latest[r['TrnID']]
        ids = [r['TrnID']]
        required, pay = M.dec(group.tien_ck), M.dec(r['SoTien'])
        if pay >= 0 and not required:
            continue        # khách trả dương, nhóm chỉ giữ ảnh vàng đổi — không phải tiệm trả khách
        problems = []
        if r['Status'] != 'C' or str(r['IsDel']) != '0':
            problems.append('Phiếu chưa chốt hoặc đã hủy.')
        if required and pay >= 0:
            problems.append('Hóa đơn không còn là tiệm trả khách.')
        elif required > -pay:
            problems.append('Tiền CK lớn hơn số tiệm trả khách.')
        receiver = {k: getattr(group, k, '') for k in ('ck_bank', 'ck_stk', 'ck_ten', 'ck_nd')}
        snapshot = {'ids': ids, 'receiver': receiver, 'required': str(required), 'nghiep_vu': ThauNhom.DOI,
                    'bills': serial([{k: r.get(k) for k in DOI_COT}])}
        member = dict(r, loai='BAN_DOI')
        row = dict(member, members=[member], thau_lines=[], SoTien=abs(pay), tien_ck=required, group_size=1,
                   partial_group=False, mixed_status=False, nghiep_vu=ThauNhom.DOI,
                   cust_id=r.get('CustID') or group.cust_id, kk_ck=-M.dec(r['CardPay']))
        row.update(order_key=key(ids), ids=ids, required=required, snapshot=snapshot, problems=problems,
                   live=True, bank=group)
        orders.append(row)
    return orders


# ─────── CẦM ĐỒ — tiệm chuyển khoản tiền cầm cho khách (GĐ chốt 22/09/2026) ───────
# Nguồn: nhóm ThauNhom(nghiep_vu='camdo') do KHCD ghi (live_loans.upsert_thau_nhom) — KHÔNG có chứng từ trên KK nên
# không đọc/ghi KK. Nội dung chuẩn "THANH TOAN TIEN VANG {6 số log phiên}", chi từ TÀI KHOẢN CẦM ĐỒ (gold_bank type 'pawn').
def catalog_camdo(d1, d2):
    start = dt.datetime.combine(dt.date.fromisoformat(d1), dt.time.min)
    end = dt.datetime.combine(dt.date.fromisoformat(d2) + dt.timedelta(days=1), dt.time.min)
    if timezone.is_aware(timezone.now()):
        start, end = timezone.make_aware(start), timezone.make_aware(end)
    orders = []
    for group in ThauNhom.objects.filter(nghiep_vu=ThauNhom.CAMDO, created_at__gte=start, created_at__lt=end).order_by('-pk'):
        trn = (group.trn_ids or [''])[0]
        if not trn:
            continue
        luc = timezone.localtime(group.created_at) if timezone.is_aware(group.created_at) else group.created_at
        bill = (group.bill_codes or [''])[0]
        required = M.dec(group.tien_ck)
        member = {'TrnID': trn, 'BillCode': bill, 'TrnDate': luc.date(), 'TrnTime': luc.strftime('%H:%M:%S'),
                  'CreatedDate': luc.replace(tzinfo=None).isoformat(sep=' '), 'Status': 'C', 'IsDel': '0',
                  'SoTien': M.dec(group.tien_mat) + required, 'CustID': group.cust_id or '',
                  'CustName': group.cust_name or '', 'Phone': '', 'CMND': '', 'EmpName': '', 'loai': 'CAMDO'}
        receiver = {k: getattr(group, k, '') for k in ('ck_bank', 'ck_stk', 'ck_ten', 'ck_nd')}
        snapshot = {'ids': [trn], 'receiver': receiver, 'required': str(required), 'nghiep_vu': ThauNhom.CAMDO,
                    'bills': serial([{k: member[k] for k in ('TrnID', 'BillCode', 'SoTien')}])}
        row = dict(member, members=[member], thau_lines=[], tien_ck=required, group_size=1, partial_group=False,
                   mixed_status=False, nghiep_vu=ThauNhom.CAMDO, cust_id=group.cust_id, bill_codes=[bill] if bill else [])
        row.update(order_key=key([trn]), ids=[trn], required=required, snapshot=snapshot, problems=[], live=True, bank=group)
        orders.append(row)
    return orders


def tai_khoan_cam_do():
    """Tài khoản chi tiền cầm đồ = gold_bank type 'pawn' đang dùng (127606)."""
    return tuple(str(r['bank_number']) for r in query("SELECT bank_number FROM gold_bank WHERE Active=1 AND LOWER(type)='pawn'"))


def tranh_chap(order, item, claimants):
    """Giao dịch ngân hàng này có bị nhóm KHÁC nhận cùng không (GĐ chốt 19/09/2026 — đối soát kèm LOẠI nghiệp vụ).

    Nội dung chỉ mang 4 số cuối mã phiếu, nên phiếu thâu và hóa đơn đổi có thể trùng đuôi. Nhóm khác CÙNG loại
    luôn tính là tranh chấp (y hệt luật cũ: hai phiếu thâu cùng đuôi → không tự nối). Nhóm khác LOẠI chỉ tính khi
    số tiền của nó cũng khớp giao dịch — lệch tiền thì chắc chắn không phải của nó.
    """
    tien = M.dec(item['trans_amount'])
    return any(ok != order['order_key'] and (nv == order.get('nghiep_vu') or can == tien)
               for nv, can, ok in claimants.get(item['id'], []))


# Nội dung chuyển khoản CHUẨN (GĐ chốt 10/09/2026): "THANH TOAN TIEN VANG {4 số cuối mã phiếu}" — phiếu
# TBG260900001445 ghi "THANH TOAN TIEN VANG 1445". Ngân hàng nối thêm đuôi ngày giờ và mã kênh của họ thành
# "THANH TOAN TIEN VANG 1445-100926-10:47:17 6253ASCB...", nên chỉ cần bắt cụm 4 số ngay sau chữ VANG.
# ⚠ Đúng 4 số, không nhiều hơn: mọi cụm ngày ddmmyy đều 6 số nên tự động trượt. Cộng thêm ràng buộc KHOẢNG TRẮNG
# trước dãy số (nội dung không mang mã trông như "THANH TOAN TIEN VANG-100926-11:03:21") và ràng buộc không có
# chữ số liền sau, hai lớp này chặn hẳn việc nhận nhầm ngày 100926 thành mã phiếu rồi xác nhận sai phiếu.
FORM_NOI_DUNG = re.compile(r'TIEN\s+VANG\s+(\d{4})(?!\d)')
# Ngân hàng để mã phiếu ở hai ô: nội dung tự do và ô mã hóa đơn. Thực đo 10/09/2026: 28/316 giao dịch OUT mang mã
# đầy đủ ở bill_code_raw, mà hàm này trước chỉ đọc description nên bỏ lỡ sạch. bill_code_norm và full_code hiện
# luôn rỗng nên không đọc, khỏi phải nới BANK_FIELDS.
# Tài khoản tiệm dùng trả tiền thâu. GĐ chốt 11/09/2026 khoá TẠM đúng một số; mở thêm thì thêm vào đây.
TAI_KHOAN_TRA = ("666141168",)
# "THANH TOAN TIEN VANG 1-…" là khoản chi khác (chi CĐ), KHÔNG phải trả tiền thâu — loại thẳng.
LOAI_TRU = "THANH TOAN TIEN VANG 1-"


def duoi_ma(value):
    """4 số cuối của SỐ HÓA ĐƠN: 26-09-21-000234 → 0234 (khớp phần quầy gõ trong nội dung chuyển khoản)."""
    so = re.sub(r'\D', '', str(value or ''))
    return so[-4:] if len(so) >= 4 else ''


def ngay_phieu(order):
    """Ngày của phiếu (yymmdd trong mã chứng từ) — TrnDate, không thì CreatedDate của phiếu đầu."""
    for m in [order] + list(order.get('members') or []):
        v = m.get('TrnDate') or m.get('CreatedDate')
        if v:
            try:
                return v if isinstance(v, dt.date) and not isinstance(v, dt.datetime) else dt.date.fromisoformat(str(v)[:10])
            except ValueError:
                pass
    return None


def ma_phieu(order, ngay=None):
    """MÃ CHỨNG TỪ mong đợi của phiếu = skill tách mã chạy trên thau_nhom.ck_nd (GĐ chốt 22/09/2026).

    Thâu / đổi dư → TV yymmdd+00xxxx (…VANG 1014 → 260921001014) · cầm đồ → CD yymmdd+6 số (…VANG 101234 → 260922101234).
    ck_nd trống/không mang mã → dựng lại nội dung chuẩn từ số HĐ đầu. ngay=None → mã trần 4/6 số.
    """
    from . import ma_chung_tu_ck as MC
    loai = MC.CHI_CD if order.get('nghiep_vu') == ThauNhom.CAMDO else MC.STT
    nd = ((order.get('snapshot') or {}).get('receiver') or {}).get('ck_nd') or order.get('ck_nd') or ''
    ra = {(x.loai, x.ma) for x in MC.nhan_dien(nd, MC.OUT, ngay) if x.loai == loai}
    if ra:
        return ra
    # ck_nd trống / không mang mã (nhóm cũ, phiếu không qua màn thâu) → dựng nội dung chuẩn từ số HĐ đầu
    bills = order.get('bill_codes') or [m.get('BillCode') for m in order.get('members') or []]
    so = re.sub(r'\D', '', str(next((b for b in bills if b), '')))
    if not so:
        return set()
    nd = 'THANH TOAN TIEN VANG ' + (so[-6:] if loai == MC.CHI_CD else so[-4:])
    return {(x.loai, x.ma) for x in MC.nhan_dien(nd, MC.OUT, ngay) if x.loai == loai}


def ma_bank(bank):
    """Mã chứng từ của giao dịch: cột ma_chung_tu (skill ghi ngay lúc webhook V2 upsert); dòng chưa nhận diện → tách tại chỗ."""
    from . import ma_chung_tu_ck as MC
    if bank.get('ma_chung_tu'):
        return {(bank.get('loai_chung_tu') or '', bank['ma_chung_tu'])}
    when = bank_time(bank)
    return {(x.loai, x.ma) for x in MC.nhan_dien(bank.get('description') or '', MC.OUT, when.date() if when else None)}


def code_match(order, bank):
    """ĐỐI SOÁT = so MÃ CHỨNG TỪ (GĐ chốt 22/09/2026): ma_phieu(thau_nhom.ck_nd) == bank_notifications.ma_chung_tu.

    Trả 'ma'   : trùng đủ 12 số (cùng NGÀY + cùng số) → ứng viên CHẮC, được tự xác nhận.
         'duoi': chỉ trùng đuôi 4/6 số (CK khác ngày phiếu) → vẫn là ứng viên nhưng chỉ xác nhận TAY.
         False : không liên quan. Nội dung "THANH TOAN TIEN VANG 1-…" (chi khác) luôn loại.
    """
    nd = (bank.get('description') or '').upper()
    if LOAI_TRU in nd:
        return False
    if 'transaction_time' not in bank and 'ma_chung_tu' not in bank:      # chỉ có nội dung (kiểm khuôn ck_nd)
        return bool(ma_phieu(order) & _ma_tran(nd))
    of_bank = ma_bank(bank)
    if not of_bank:
        return False
    if ma_phieu(order, ngay_phieu(order)) & of_bank:
        return 'ma'
    tran = ma_phieu(order)
    return 'duoi' if any(lb == lt and len(m) == 12 and m[-len(t):] == t for lb, m in of_bank for lt, t in tran) else False


def _ma_tran(nd):
    from . import ma_chung_tu_ck as MC
    return {(x.loai, x.ma) for x in MC.nhan_dien(nd, MC.OUT, None)}


def near(order, bank):
    when = bank_time(bank)
    if not when:
        return False
    times = [dt.datetime.fromisoformat(str(m['CreatedDate'])) for m in order['members'] if m.get('CreatedDate')]
    return bool(times) and any(when-dt.timedelta(minutes=30) <= t <= when for t in times)


def eligible(bank, accounts=None, nghiep_vu=None, tk_cam_do=()):
    """CHUẨN ĐỐI SOÁT MỚI (GĐ chốt 11/09/2026) — thay hẳn chuẩn cũ. Chỉ xét giao dịch đủ CẢ BỐN điều kiện:

        · direction = out (tiền rời tài khoản tiệm);
        · bank_number nằm trong TAI_KHOAN_TRA (tạm thời đúng một số);
        · trans_amount > 0;
        · nội dung KHÔNG phải khoản chi khác "THANH TOAN TIEN VANG 1-…".

    Tham số accounts giữ cho chỗ gọi cũ nhưng KHÔNG dùng nữa: danh sách tài khoản lấy từ hằng trên chứ không
    từ bảng gold_bank, để khỏi vô tình nhận tiền ra từ tài khoản khác của tiệm.
    """
    tk = tk_cam_do if nghiep_vu == ThauNhom.CAMDO else TAI_KHOAN_TRA     # cầm đồ chi từ TK cầm đồ (22/09/2026)
    return (str(bank.get('direction', '')).lower() == 'out'
            and M.dec(bank['trans_amount']) > 0
            and str(bank.get('bank_number') or '') in tk
            and LOAI_TRU not in (bank.get('description') or '').upper())


def history_for(ids):
    cond = Q()
    for t in ids:
        cond |= Q(trn_ids__contains=[t])
    return list(ThauPaymentLink.objects.filter(cond).order_by('-pk')) if ids else []


def inspect(d1, d2):
    if d1 > d2 or (dt.date.fromisoformat(d2)-dt.date.fromisoformat(d1)).days > 31:
        raise ValueError('Chọn khoảng ngày tối đa 31 ngày để đối soát.')
    orders, live = catalog(d1, d2)
    tk_cd = tai_khoan_cam_do() if any(o.get('nghiep_vu') == ThauNhom.CAMDO for o in orders) else ()
    accounts = {r['bank_number'] for r in query('SELECT bank_number FROM gold_bank WHERE Active=1')}
    start = (dt.date.fromisoformat(d1)-dt.timedelta(days=1)).isoformat()
    end = (dt.date.fromisoformat(d2)+dt.timedelta(days=2)).isoformat()
    banks = query(f'SELECT {BANK_FIELDS} FROM bank_notifications WHERE transaction_time >= %s AND transaction_time < %s '
                  "AND LOWER(direction)='out' ORDER BY id", [start, end])
    ids = {t for o in orders for t in o['ids']}
    history = history_for(ids)
    all_active = list(ThauPaymentLink.objects.filter(active_notification_id__isnull=False)
                      .values('active_notification_id', 'order_key', 'bank_snapshot'))
    used = {r['active_notification_id']: r['order_key'] for r in all_active}
    used_refs = {(r['bank_snapshot'].get('provider'), r['bank_snapshot'].get('bank_number'), r['bank_snapshot'].get('ref_code'))
                 for r in all_active if r['bank_snapshot'].get('ref_code')}
    by_id = {b['id']: b for b in banks}
    missing = [l.notification_id for l in history if l.active_notification_id and l.notification_id not in by_id]
    if missing:
        by_id.update({b['id']: b for b in query(f'SELECT {BANK_FIELDS} FROM bank_notifications WHERE id IN ('+
                     ','.join(['%s']*len(missing))+')', missing)})
    legacy = {s.notification_id: s.trn_id for s in BankReconcileState.objects.filter(trn_id__isnull=False)}
    refs = Counter((b['provider'], b['bank_number'], b['ref_code']) for b in banks if b['ref_code'])
    claims = Counter()
    claimants = {}      # bank id → [(nghiệp vụ, tiền cần CK, order_key)] — xét tranh chấp theo LOẠI (19/09/2026)
    for order in orders:
        candidates = []
        for bank in banks:
            if not eligible(bank, accounts, order.get('nghiep_vu'), tk_cd) or bank['id'] in used:
                continue
            ref = (bank['provider'], bank['bank_number'], bank['ref_code'])
            if bank['ref_code'] and ref in used_refs:
                continue
            # CHUẨN MỚI (11/09/2026): chỉ nội dung khớp mã phiếu mới thành ứng viên. Đường "đúng tiền + trong
            # 30 phút" đã bỏ — nó chỉ là phỏng đoán, dễ gán nhầm khoản chi khác cùng số tiền.
            exact = code_match(order, bank)
            if not exact:
                continue
            foreign = legacy.get(bank['id']) and legacy[bank['id']] not in order['ids']
            foreign = foreign or ((bank['bill_code_raw'] or '').startswith('TBG') and bank['bill_code_raw'] not in order['ids'])
            item = dict(bank, exact=exact == 'ma', conflict=bool(foreign or (bank['ref_code'] and refs[ref] > 1)))
            candidates.append(item)
            claims[bank['id']] += 1
            claimants.setdefault(bank['id'], []).append((order.get('nghiep_vu'), order['required'], order['order_key']))
        order['candidates'] = candidates
    for order in orders:
        related = [l for l in history if set(l.trn_ids) & set(order['ids'])]
        active = [l for l in related if l.active_notification_id]
        paid = sum((l.amount for l in active), Decimal(0))
        problems = list(order['problems'])
        for link in active:
            bank = by_id.get(link.notification_id)
            if link.order_key != order['order_key'] or link.snapshot != order['snapshot']:
                problems.append('Phiếu/nhóm đã thay đổi sau khi xác nhận CK.')
            if not bank or evidence(bank) != link.bank_snapshot or not eligible(bank, accounts, order.get('nghiep_vu'), tk_cd):
                problems.append('Giao dịch ngân hàng đã thay đổi hoặc không còn hợp lệ.')
            if bank and bank.get('ref_code') and refs[(bank['provider'], bank['bank_number'], bank['ref_code'])] > 1:
                problems.append('Mã giao dịch ngân hàng bị trùng sau khi liên kết.')
        if order.get('nghiep_vu') == ThauNhom.DOI and order['kk_ck'] not in (
                {Decimal(0), paid} | {Decimal(l.amount) for l in related}):
            problems.append('CK trên KK khác số đã đối soát — cần kiểm tra.')
        if order['required'] == 0 and not active:
            status = 'review' if problems and any('CK' in p for p in problems) else 'na'
        elif problems:
            status = 'review'
        elif paid == order['required']:
            status = 'confirmed'
        elif paid > order['required']:
            status = 'review'
            problems.append('Tổng tiền liên kết lớn hơn số cần CK.')
        elif paid:
            status = 'partial'
        else:
            status = 'review' if order['candidates'] else 'unconfirmed'
        for item in order['candidates']:
            item['ambiguous'] = tranh_chap(order, item, claimants)   # chỉ-thâu: y hệt claims > 1 như trước
            item['can_link'] = not problems and not active and not item['conflict'] and M.dec(item['trans_amount']) == order['required']
            item['token'] = signing.dumps({'snapshot': order['snapshot'], 'bank': evidence(item)}, salt='thau-payment')
        order.update(payment_status=status, payment_label=LABELS[status], paid=paid,
                     remaining=max(Decimal(0), order['required']-paid), problems=problems,
                     links=related, auto_blocked=bool(related), claims=claims)
    return orders, live


def create_link(order, bank, user=None, reason='', mode='auto'):
    if not bank['can_link'] or (mode == 'auto' and (bank.get('ambiguous') or not bank['exact'] or not bank.get('ref_code') or order['auto_blocked'])):
        raise ValueError('Chưa đủ điều kiện liên kết; cần kiểm tra số tiền, trạng thái và giao dịch trùng.')
    with transaction.atomic():
        suffix = ' FOR UPDATE' if connection.vendor == 'mysql' else ''
        fresh = query(f'SELECT {BANK_FIELDS} FROM bank_notifications WHERE id=%s'+suffix, [bank['id']])
        if not fresh or evidence(fresh[0]) != evidence(bank) or (fresh[0].get('bill_code_raw') or '').casefold() == 'chi cđ':
            raise ValueError('Giao dịch ngân hàng vừa thay đổi. Hãy đối soát lại.')
        if bank.get('ref_code') and query('SELECT id FROM bank_notifications WHERE provider=%s AND bank_number=%s AND ref_code=%s AND id<>%s LIMIT 1',
                [bank['provider'], bank['bank_number'], bank['ref_code'], bank['id']]):
            raise ValueError('Mã giao dịch ngân hàng bị trùng; cần kiểm tra trước khi liên kết.')
        # unique key additionally protects against concurrent sessions.
        return ThauPaymentLink.objects.create(order_key=order['order_key'], trn_ids=order['ids'],
            notification_id=bank['id'], active_notification_id=bank['id'], amount=bank['trans_amount'],
            snapshot=order['snapshot'], bank_snapshot=evidence(bank), mode=mode,
            user=user, username=getattr(user, 'username', '') if user else 'system', reason=reason)


def write_buygold_account(order, bank):
    """After evidence is linked, persist the sending shop account on PMV BUYGOLD rows."""
    if order.get('nghiep_vu')!=ThauNhom.THAU:return ''
    from apps.pmv.gateway import pmv_buygold_payment_account
    try:
        pmv_buygold_payment_account(order['ids'],bank.get('bank_number') or '')
        return ' Đã lưu số tài khoản chuyển trên phiếu thâu.'
    except Exception:
        import logging
        logging.getLogger(__name__).exception('Chưa ghi được MaPhieuChi cho phiếu thâu đã đối soát')
        return ' ⚠ Đã đối soát nhưng chưa ghi được số tài khoản chuyển lên KK.'


def ghi_kk_sau(d1, d2, ids):
    """Sau khi nối / gỡ tay: nhóm là hóa đơn BÁN-ĐỔI thì ghi ngay CardPay lên KK theo tổng liên kết còn hiệu lực.
    Phiếu thâu (CardPay đã ghi lúc THANH TOÁN) không đụng gì. Lỗi không làm hỏng thao tác vừa xong — job 5 phút
    doi_soat_ck sẽ ghi lại. Trả câu nối thêm vào thông báo."""
    from . import ck_tra_khach as CK
    if not CK.nhom_doi((list(ids) or [''])[0]):
        return ''
    try:
        orders, live = inspect(d1, d2)
        xong, loi = CK.dong_bo_ck_kk(orders, chi_ids=ids) if live else (0, ['không đọc được máy KK'])
    except Exception as exc:
        xong, loi = 0, [str(exc)]
    if loi:
        return ' ⚠ Chưa ghi được CK lên máy KK — lượt chạy ngầm sẽ thử lại.'
    return ' Đã cập nhật CK trên máy KK.' if xong else ''


@login_not_required     # 22/09/2026: trang Thâu vào 2 công khai trong tiệm — lượt TỰ đối soát (automatic=1) không cần đăng nhập
@require_POST
def action(request):
    tu_dong = request.POST.get('automatic') == '1' and request.POST.get('action', 'scan') == 'scan'
    if not tu_dong and not permitted(request.user):
        return JsonResponse({'error': 'Bạn cần quyền Duyệt/chốt Thâu vào.'}, status=403)
    try:
        d1 = dt.date.fromisoformat(request.POST.get('d1', '')).isoformat()
        d2 = dt.date.fromisoformat(request.POST.get('d2', '')).isoformat()
        if d1 > d2 or (dt.date.fromisoformat(d2)-dt.date.fromisoformat(d1)).days > 31:
            raise ValueError('Chọn khoảng ngày tối đa 31 ngày để đối soát.')
        mode = request.POST.get('action', 'scan')
        if mode not in ('scan', 'link', 'unlink'):
            raise ValueError('Thao tác không hợp lệ.')
        automatic = request.POST.get('automatic') == '1'
        if automatic and (mode != 'scan' or d2 != timezone.localdate().isoformat()):
            return JsonResponse({'changed': 0, 'message': 'Chỉ tự đối soát khi đến ngày là hôm nay.'})
        if mode in ('link', 'unlink'):
            from .views import _passcode_dung
            if not _passcode_dung(request, request.POST.get('passcode', '')):
                raise ValueError('Passcode không đúng hoặc đang tạm khóa.')
        with single_run() as acquired:
            if not acquired:
                return JsonResponse({'changed': 0, 'message': 'Đang có lượt đối soát khác.'})
            if automatic:
                last = float(PmvState.get('thau_payment_scan', '0'))
                if timezone.now().timestamp()-last < 10:     # mọi máy đang mở trang cộng lại: tối đa 1 lượt/10 giây
                    return JsonResponse({'changed': 0})
                PmvState.set('thau_payment_scan', timezone.now().timestamp())
            if mode == 'unlink':
                reason = request.POST.get('reason', '').strip()
                if not reason or len(reason) > 500:
                    raise ValueError('Nhập lý do gỡ liên kết (tối đa 500 ký tự).')
                with transaction.atomic():
                    link = ThauPaymentLink.objects.select_for_update().get(pk=request.POST.get('link_id'))
                    if not link.active_notification_id:
                        raise ValueError('Liên kết đã được gỡ trước đó.')
                    link.active_notification_id = None
                    link.revoked_at = timezone.now()
                    link.revoked_by = request.user.username
                    link.revoke_reason = reason
                    link.save(update_fields=['active_notification_id', 'revoked_at', 'revoked_by', 'revoke_reason'])
                return JsonResponse({'changed': 1, 'message': 'Đã gỡ liên kết và lưu lịch sử; không hoàn tiền ngân hàng.'
                                     + ghi_kk_sau(d1, d2, link.trn_ids)})
            orders, live = inspect(d1, d2)
            # Mutations must re-read the live ledger, never confirm against stale history.
            if not live:
                raise ValueError('Kho lịch sử chỉ dùng xem. Chọn khoảng ngày gồm hôm nay để kiểm tra lại phiếu trên KK.')
            if mode == 'link':
                order = next((o for o in orders if o['order_key'] == request.POST.get('order_key')), None)
                bank = next((b for b in order['candidates'] if str(b['id']) == request.POST.get('bank_id')), None) if order else None
                if not bank:
                    raise ValueError('Phiếu hoặc giao dịch không còn là ứng viên. Hãy tải lại.')
                token = signing.loads(request.POST.get('token', ''), salt='thau-payment', max_age=600)
                if token != {'snapshot': order['snapshot'], 'bank': evidence(bank)}:
                    raise ValueError('Dữ liệu đã thay đổi từ lúc mở popup.')
                reason = request.POST.get('reason', '').strip()
                if not reason or len(reason) > 500:
                    raise ValueError('Nhập căn cứ xác nhận (tối đa 500 ký tự).')
                create_link(order, bank, request.user, reason, 'manual')
                return JsonResponse({'changed': 1, 'message': 'Đã xác nhận liên kết giao dịch ngân hàng.'
                                     + ghi_kk_sau(d1, d2, order['ids']) + write_buygold_account(order,bank)})
            changed = 0
            for order in orders:
                matches = [b for b in order['candidates'] if b['can_link'] and not b['ambiguous'] and b['exact'] and b.get('ref_code')]
                if len(matches) == 1 and not order['auto_blocked']:
                    create_link(order, matches[0], reason='Khớp mã phiếu trong nội dung ngân hàng, đủ tiền, không tranh chấp.')
                    write_buygold_account(order,matches[0])
                    changed += 1
                    if changed >= 30:
                        break
            revision = hashlib.sha256(json.dumps(serial([(o['order_key'], o['snapshot'], o['payment_status'], o['paid'], o['problems']) for o in orders]), sort_keys=True).encode()).hexdigest()
            return JsonResponse({'changed': changed, 'revision': revision, 'message': f'Đối soát xong: {changed} nhóm mới được xác nhận.'})
    except (ValueError, signing.BadSignature, ThauPaymentLink.DoesNotExist) as exc:
        return JsonResponse({'error': str(exc)}, status=409)
    except Exception:
        import logging
        logging.getLogger(__name__).exception('Đối soát CK nhóm thâu thất bại')
        return JsonResponse({'error': 'Không thể hoàn tất đối soát. Hãy tải lại để kiểm tra kết quả.'}, status=503)
