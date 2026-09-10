"""Đối soát khi mở Chuyển khoản; chỉ ghi MySQL, PMV chỉ đọc qua gateway.

GĐ duyệt 08/09/2026: d2=hôm nay, out/is_check=0, CardPay âm, [T-30p,T].
Khóa MySQL chung mọi process; không giữ transaction MySQL khi chờ PMV.
"""
import datetime as dt
import hashlib
import json
import logging
from collections import Counter
from contextlib import contextmanager
from decimal import Decimal

from django.db import connection, transaction
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.pmv import gateway
from apps.pmv.models import PmvState
from .models import BankReconcileState
from .transfers import query

logger = logging.getLogger(__name__)
LOCK = 'khbl:bank-reconcile:v1'
LAST_RUN = 'bank_reconcile_last_run'
BATCH = 30
FIELDS = 'id, bank_number, transaction_time, trans_amount, direction, bill_code_raw, is_check, updated_at, description'
CD_PATTERN = '%THANH TOAN TIEN VANG 1%'


def classify_cd(start=None, end=None):
    """Explicit content rule; retain is_check and prior reconciliation audit."""
    sql = ('UPDATE bank_notifications SET bill_code_raw = %s '
           "WHERE UPPER(COALESCE(description, '')) LIKE %s "
           "AND COALESCE(bill_code_raw, '') <> %s")
    params = ['chi CĐ', CD_PATTERN, 'chi CĐ']
    if start is not None and end is not None:
        sql += ' AND transaction_time >= %s AND transaction_time < %s'
        params += [start, end]
    with connection.cursor() as cur:
        cur.execute(sql, params)
        return cur.rowcount


@contextmanager
def single_run():
    with connection.cursor() as cur:
        cur.execute('SELECT GET_LOCK(%s, 0)', [LOCK])
        acquired = cur.fetchone()[0] == 1
    try:
        yield acquired
    finally:
        if acquired:
            with connection.cursor() as cur:
                cur.execute('SELECT RELEASE_LOCK(%s)', [LOCK])


def fingerprint(row):
    values = [row.get(k) for k in ('bank_number', 'transaction_time', 'trans_amount', 'direction', 'bill_code_raw', 'is_check', 'description')]
    return hashlib.sha256(json.dumps(values, default=str).encode()).hexdigest()


def bank_time(row):
    try:
        value = dt.datetime.fromisoformat(row['transaction_time'])
        if value.tzinfo:
            value = timezone.localtime(value).replace(tzinfo=None)
        return value
    except (ValueError, TypeError):
        return None


def candidates(row, bills):
    when = bank_time(row)
    if when is None:
        return []
    return [b for b in bills if b['Status'] == 'C' and b['IsDel'] == '0'
            and b['CardPay'] < 0 and -b['CardPay'] == Decimal(row['trans_amount'])
            and when - dt.timedelta(minutes=30) <= b['CreatedDate'] <= when]


def defer(row, status, message, now):
    state, _ = BankReconcileState.objects.get_or_create(notification_id=row['id'])
    if state.trn_id:
        return
    state.attempts = state.attempts + 1 if state.fingerprint == fingerprint(row) else 1
    state.fingerprint = fingerprint(row)
    state.status, state.message = status, message
    state.next_attempt = now + dt.timedelta(seconds=min(300, 15 * 2 ** min(state.attempts - 1, 5)))
    state.save()


def save_match(row, trn_id, created_date):
    """Giữ reservation unique và cập nhật nguồn trong cùng transaction ngắn."""
    with transaction.atomic():
        from .models import ThauPaymentLink
        active_links = ThauPaymentLink.objects.filter(active_notification_id__isnull=False)
        if active_links.filter(active_notification_id=row['id']).exists() or any(
                trn_id in ids for ids in active_links.values_list('trn_ids', flat=True)):
            return False
        suffix = ' FOR UPDATE' if connection.vendor == 'mysql' else ''
        fresh = query(f'SELECT {FIELDS} FROM bank_notifications WHERE id = %s' + suffix, [row['id']])
        if not fresh or fingerprint(fresh[0]) != fingerprint(row) or fresh[0]['updated_at'] != row['updated_at']:
            return False
        if BankReconcileState.objects.filter(trn_id=trn_id).exclude(notification_id=row['id']).exists():
            return False
        if query('SELECT id FROM bank_notifications WHERE bill_code_raw = %s AND id <> %s LIMIT 1', [trn_id, row['id']]):
            return False
        # Một UPSERT khác có thể đến SAU lúc lấy danh sách đầu lượt.
        day = bank_time(row).date()
        rivals = query("SELECT id, transaction_time FROM bank_notifications WHERE direction = 'out' AND is_check = 0 "
                       "AND UPPER(COALESCE(description, '')) NOT LIKE %s "
                       'AND trans_amount = %s AND id <> %s AND transaction_time >= %s AND transaction_time < %s',
                       [CD_PATTERN, row['trans_amount'], row['id'], day.isoformat(), (day + dt.timedelta(days=1)).isoformat()])
        if any(bank_time(r) is not None and created_date <= bank_time(r) <= created_date + dt.timedelta(minutes=30) for r in rivals):
            return False
        state, _ = BankReconcileState.objects.get_or_create(notification_id=row['id'])
        if state.trn_id:
            return False
        state.trn_id, state.status = trn_id, 'matched'
        state.fingerprint = fingerprint(row)
        state.message = 'Khớp duy nhất phiếu thâu trong 30 phút.'
        state.next_attempt = None
        state.save()
        with connection.cursor() as cur:
            cur.execute('UPDATE bank_notifications SET bill_code_raw = %s, is_check = 1 WHERE id = %s AND is_check = 0', [trn_id, row['id']])
            if cur.rowcount != 1:
                raise RuntimeError('Bản ghi thay đổi trong khi đối soát')
    return True


def report(result, rows):
    review = BankReconcileState.objects.filter(notification_id__in=[r['id'] for r in rows],
        status__in=['ambiguous', 'conflict', 'invalid']).order_by('notification_id')
    result['needs_review'] = review.count()
    result['issues'] = [dict(id=s.notification_id, message=s.message) for s in review[:10]]
    return result


def reconcile(today):
    now = timezone.now()
    last = float(PmvState.get(LAST_RUN, '0'))
    if now.timestamp() - last < 5:
        return dict(status='busy', matched=0)
    PmvState.set(LAST_RUN, now.timestamp())
    start, end = today.isoformat(), (today + dt.timedelta(days=1)).isoformat()
    classified = classify_cd(start, end)
    rows = query(f'SELECT {FIELDS} FROM bank_notifications '
                 "WHERE direction = 'out' AND is_check = 0 AND trans_amount > 0 "
                 "AND UPPER(COALESCE(description, '')) NOT LIKE %s "
                 'AND transaction_time >= %s AND transaction_time < %s ORDER BY id', [CD_PATTERN, start, end])
    states = BankReconcileState.objects.in_bulk([r['id'] for r in rows])
    from .models import ThauPaymentLink
    reserved = set(ThauPaymentLink.objects.filter(active_notification_id__isnull=False).values_list('active_notification_id', flat=True))
    rows = [r for r in rows if r['id'] not in reserved]
    for row in rows:
        state = states.get(row['id'])
        if state and state.trn_id and state.status != 'conflict':
            state.status = 'conflict'
            state.message = 'Giao dịch đã khớp trước đây nhưng is_check bị đặt lại; cần kiểm tra.'
            state.save(update_fields=['status', 'message', 'updated_at'])
    due = [r for r in rows if r['id'] not in states or (
        not states[r['id']].trn_id and (states[r['id']].fingerprint != fingerprint(r)
        or not states[r['id']].next_attempt or states[r['id']].next_attempt <= now))][:BATCH]
    valid = [r for r in due if bank_time(r) is not None]
    for row in due:
        if bank_time(row) is None:
            defer(row, 'invalid', 'Thời gian giao dịch không hợp lệ.', now)
    result = dict(status='ok', matched=0, classified=classified, pending=len(rows), reviewed=len(due))
    if not valid:
        return report(result, rows)
    lower = min(bank_time(r) for r in valid) - dt.timedelta(minutes=30)
    upper = max(bank_time(r) for r in valid)
    amounts = sorted({-Decimal(r['trans_amount']) for r in valid})
    try:
        bills = gateway.pmv_read(
            "SELECT TrnID, CardPay, CreatedDate, Status, IsDel FROM TRN_RT_BUYGOLD WITH (NOLOCK) "
            "WHERE Status = 'C' AND IsDel = '0' AND CardPay < 0 AND CreatedDate >= ? AND CreatedDate <= ? "
            'AND CardPay IN (' + ','.join('?' for _ in amounts) + ')',
            [lower, upper] + amounts, tag='bank_reconcile', target='kk', timeout=3, query_timeout=3, audit=False)
    except Exception:
        for row in valid:
            defer(row, 'error', 'Không đọc được PMV; đang chờ thử lại.', now)
        logger.exception('Không đọc được PMV để đối soát ngân hàng')
        return dict(result, status='error', message='Chưa kết nối được PMV. Sẽ thử lại; bảng ngân hàng vẫn xem được.')
    # Kiểm tra tranh chấp với TẤT CẢ khoản RA chưa đối soát hôm nay, kể cả đang chờ retry.
    claims = Counter(b['TrnID'] for row in rows for b in candidates(row, bills))
    for row in valid:
        matches = candidates(row, bills)
        if not matches:
            defer(row, 'pending', 'Chưa tìm thấy phiếu phù hợp.', now)
            continue
        if len(matches) != 1:
            defer(row, 'ambiguous', 'Nhiều phiếu khớp; cần kiểm tra thủ công.', now)
            continue
        trn_id = matches[0]['TrnID']
        if claims[trn_id] != 1:
            defer(row, 'conflict', 'Nhiều giao dịch ngân hàng cùng khớp một phiếu.', now)
            continue
        if (row['bill_code_raw'] or '').strip() not in ('', trn_id):
            defer(row, 'conflict', 'Mã HĐ hiện tại khác TrnID tìm được; không ghi đè.', now)
            continue
        if save_match(row, trn_id, matches[0]['CreatedDate']):
            result['matched'] += 1
        else:
            defer(row, 'conflict', 'Dữ liệu vừa thay đổi hoặc phiếu đã được sử dụng.', now)
    result['pending'] -= result['matched']
    return report(result, rows)


@require_POST
def reconcile_view(request):
    today = timezone.localdate()
    if request.POST.get('d2') != today.isoformat():
        return JsonResponse(dict(status='skipped', matched=0, message='Chỉ đối soát khi đến ngày là hôm nay.'))
    try:
        with single_run() as acquired:
            if not acquired:
                return JsonResponse(dict(status='busy', matched=0))
            return JsonResponse(reconcile(today))
    except Exception:
        logger.exception('Đối soát ngân hàng thất bại')
        return JsonResponse(dict(status='error', message='Đối soát tạm lỗi. Vui lòng thử lại.'), status=503)
