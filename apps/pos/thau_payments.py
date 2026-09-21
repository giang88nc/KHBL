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
BANK_FIELDS = 'id,provider,ref_code,bank_number,bank_name,trans_amount,transaction_time,direction,description,bill_code_raw'


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
    doi = catalog_doi(d1, d2)
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
    """4 số cuối của mã phiếu: TBG260900001445 → 1445 (khớp phần người dùng gõ trong nội dung chuyển khoản)."""
    so = re.sub(r'\D', '', str(value or ''))
    return so[-4:] if len(so) >= 4 else ''


def code_match(order, bank):
    """Nội dung ngân hàng có nhắc ĐÚNG phiếu của nhóm không — CHUẨN MỚI, chỉ đọc ô NỘI DUNG.

    Khớp khi nội dung chứa "THANH TOAN TIEN VANG {4 số cuối mã phiếu}", đúng khuôn quầy đang gõ; ngân hàng nối
    thêm đuôi ngày giờ phía sau vẫn khớp.

    GĐ chốt 11/09/2026 BỎ hai đường khớp cũ — đọc mã đầy đủ ở ô mã hóa đơn, và đoán theo số tiền + thời gian —
    chỉ giữ một đường này cho chắc, tránh nhận nhầm khoản chi khác.

    Nhóm nhiều phiếu: nội dung chỉ cần nhắc MỘT phiếu (thường là phiếu đầu, người đứng tên nhận tiền cho cả nhóm).
    Xác nhận vẫn ở mức NHÓM và số tiền vẫn phải bằng đúng tổng cần chuyển của nhóm.
    """
    nd = (bank.get('description') or '').upper()
    if LOAI_TRU in nd:
        return False
    # 4 số cuối lấy từ MÃ PHIẾU (TrnID), không lấy từ số hóa đơn: hai mã có đuôi khác nhau
    # (TBG260900000495 ↔ 26-09-10-000058) nên gom cả hai chỉ làm rộng vùng trùng chứ không thêm ca khớp nào.
    duoi = {duoi_ma(t) for t in order['ids']} - {''}
    return any(m.group(1) in duoi for m in FORM_NOI_DUNG.finditer(nd))


def near(order, bank):
    when = bank_time(bank)
    if not when:
        return False
    times = [dt.datetime.fromisoformat(str(m['CreatedDate'])) for m in order['members'] if m.get('CreatedDate')]
    return bool(times) and any(when-dt.timedelta(minutes=30) <= t <= when for t in times)


def eligible(bank, accounts=None):
    """CHUẨN ĐỐI SOÁT MỚI (GĐ chốt 11/09/2026) — thay hẳn chuẩn cũ. Chỉ xét giao dịch đủ CẢ BỐN điều kiện:

        · direction = out (tiền rời tài khoản tiệm);
        · bank_number nằm trong TAI_KHOAN_TRA (tạm thời đúng một số);
        · trans_amount > 0;
        · nội dung KHÔNG phải khoản chi khác "THANH TOAN TIEN VANG 1-…".

    Tham số accounts giữ cho chỗ gọi cũ nhưng KHÔNG dùng nữa: danh sách tài khoản lấy từ hằng trên chứ không
    từ bảng gold_bank, để khỏi vô tình nhận tiền ra từ tài khoản khác của tiệm.
    """
    return (str(bank.get('direction', '')).lower() == 'out'
            and M.dec(bank['trans_amount']) > 0
            and str(bank.get('bank_number') or '') in TAI_KHOAN_TRA
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
            if not eligible(bank, accounts) or bank['id'] in used:
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
            item = dict(bank, exact=bool(exact), conflict=bool(foreign or (bank['ref_code'] and refs[ref] > 1)))
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
            if not bank or evidence(bank) != link.bank_snapshot or not eligible(bank, accounts):
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


@require_POST
def action(request):
    if not permitted(request.user):
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
                if timezone.now().timestamp()-last < 5:
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
                                     + ghi_kk_sau(d1, d2, order['ids'])})
            changed = 0
            for order in orders:
                matches = [b for b in order['candidates'] if b['can_link'] and not b['ambiguous'] and b['exact'] and b.get('ref_code')]
                if len(matches) == 1 and not order['auto_blocked']:
                    create_link(order, matches[0], reason='Khớp mã phiếu trong nội dung ngân hàng, đủ tiền, không tranh chấp.')
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
