"""Đối soát CK sau xác nhận thu cọc; tổng cọc cố định, không thu thêm tự động."""
import datetime as dt
import hashlib
import json
import re
import uuid
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import connection, transaction
from django.utils import timezone

from apps.pmv.client import PmvClient
from apps.pmv.models import pmv_user_for_web_user
from . import deposit_money as F, deposits as D
from .deposit_models import DepositOrderState, DepositMoneyOperation as Operation
from .models import BankReconcileState, ThauPaymentLink
from .transfers import query

POLICY = 'fixed_total_v1'

COLUMNS = 'id,bank_number,transaction_time,trans_amount,direction,bill_code_raw,is_check,description'


def fingerprint(row):
    data = {key: row.get(key) for key in COLUMNS.split(',') if key != 'is_check'}
    return hashlib.sha256(json.dumps(data, sort_keys=True, default=str, ensure_ascii=False).encode()).hexdigest()


def contains_code(row, code):
    # Nội dung ngân hàng có thể dính liền mã tham chiếu, đúng yêu cầu LIKE %mã_phiếu%.
    return code.casefold() in str(row.get('description') or '').casefold()


def enable_reconcile(op):
    with transaction.atomic():
        state = DepositOrderState.objects.select_for_update().get(target=op.target, trn_id=op.trn_id)
        state.payment_plan = {**state.payment_plan, 'cash': str(op.cash), 'bank': str(op.bank),
            'reconcile_enabled': True, 'money_policy': POLICY, 'initial_bank': str(op.bank), 'receipt_operation': op.pk,
            'bank_status': 'waiting', 'bank_message': 'Đã xác nhận thu; chờ đối soát nội dung ngân hàng.'}
        state.status = 'P'
        state.version += 1
        state.save()


def allocation(amount, initial_bank, old_rows, candidates):
    """Tính từ tập giao dịch duy nhất; không cộng CK vào tổng cọc đã thu."""
    rows = {r['id']: r for r in [*old_rows, *candidates]}
    if any(r['direction'] != 'in' or Decimal(r['trans_amount'] or 0) <= 0 for r in rows.values()):
        raise ValueError('Giao dịch đối soát phải là tiền vào lớn hơn 0.')
    matched = sum((Decimal(r['trans_amount']) for r in rows.values()), Decimal(0))
    if matched > amount:
        raise ValueError('CK khớp mã vượt tổng cọc. Cần xác nhận thu thêm hoặc chuyển nhầm; chưa sửa phiếu.')
    bank = max(Decimal(initial_bank or 0), matched)
    if bank > amount:
        raise ValueError('Phân bổ đã xác nhận vượt tổng cọc.')
    return amount - bank, bank


def validate_reallocation(c, f, cash, bank):
    if not f['valid']:
        raise ValueError('Chứng từ quỹ cọc chưa khớp; cần kiểm tra trước khi đối soát CK.')
    if cash == f.get('cash') and bank == f.get('bank'):
        return  # Chỉ gắn bằng chứng cho phân bổ đã đúng, kể cả phiếu của ngày trước.
    if f['h']['Status'] != 'P' or f['links'] or f['changes']:
        raise ValueError('Phiếu đã liên kết/áp dụng, đã hoàn hoặc quỹ chưa khớp; cần kiểm tra phân bổ CK.')
    if cash < 0 or bank < 0 or cash + bank != f['amount']:
        raise ValueError('TM + CK phải bằng tổng cọc đã thu.')
    day = f['h']['TrnDate']
    if isinstance(day, dt.datetime): day = day.date()
    if day != timezone.localdate():
        raise ValueError('CK khớp phiếu của ngày trước; cần kiểm tra sổ quỹ trước khi đổi phân bổ.')
    if str(c.sys_param('TaoSoHDKhiThanhToan')) != '0':
        raise ValueError('PMV đang cấp lại số khi chốt; dừng để giữ nguyên số chứng từ cọc.')


def reallocate(c, op):
    """Điều chỉnh quỹ theo PMV, ghi tiền trực tiếp trên phiếu, không gọi CARDPAY."""
    f = F.financial(c, op.trn_id)
    validate_reallocation(c, f, op.cash, op.bank)
    if F.money_stamp(c, f['h']) != op.evidence['stamp']:
        raise ValueError('Phiếu vừa thay đổi trước khi đối soát.')
    if f['tx'][0]['TillID'] != op.till_id or op.amount != op.cash + op.bank:
        raise ValueError('Két thu hoặc tổng cọc không khớp.')
    if f['cash'] == op.cash and f['bank'] == op.bank:
        return f
    identity = {k: f['h'].get(k) for k in ('TrnID', 'BillCode', 'TrnDate', 'TienCoc', 'CustID', 'ShopID')}
    c.call('T_TILL_TXN_Del', write=True, day_du=True, p_TrnRefID=op.trn_id, pType='TDC', pCongNoBanLe=0,
           p_UserUpd=op.user_id, p_TrnDateTime_Upd=f['h']['TrnDateTime_Upd'], p_Type='0')
    cleared = F.financial(c, op.trn_id)
    if cleared['tx'] or cleared['cash'] or cleared['bank'] or cleared['h']['Status'] != 'W' or cleared['links'] or cleared['changes']:
        raise ValueError('Chưa xác minh bước điều chỉnh quỹ; không chốt lại.')
    c.call('TRN_DATCOC_Complete', write=True, p_TrnID=op.trn_id, p_UserID=op.user_id)
    c.update_deposit_money(D.header(c, op.trn_id), op.cash, op.bank, till_id=op.till_id)
    c.call('T_TILL_TXN_Proc', write=True, p_TrnIDs=op.trn_id, p_TillID=op.till_id, p_UserID=op.user_id)
    after = F.financial(c, op.trn_id)
    if (not after['valid'] or after['h']['Status'] != 'P' or after['cash'] != op.cash
            or after['bank'] != op.bank or after['amount'] != op.amount or after['tx'][0]['TillID'] != op.till_id):
        raise ValueError('Chưa xác minh tiền cọc sau đối soát.')
    if identity != {k: after['h'].get(k) for k in identity}:
        raise ValueError('Thông tin chứng từ gốc thay đổi trong khi đối soát.')
    return after


def reconcile(state):
    code = state.trn_id
    plan = state.payment_plan
    source = Operation.objects.get(pk=plan['receipt_operation'], target='kk', trn_id=code, kind='receive', status='done')
    user = get_user_model().objects.filter(username=source.username, is_active=True).first()
    pu = pmv_user_for_web_user(user) if user else None
    if not user or not D.allowed(user, 'can_edit') or not pu or pu.user_id != source.user_id or pu.till_id != source.till_id:
        raise ValueError('Nhân viên/két xác nhận thu đã thay đổi; cần kiểm tra quyền đối soát.')
    if Operation.objects.filter(target='kk', trn_id=code, active_key__isnull=False).exists():
        raise ValueError('Phiếu có thao tác tiền chưa hoàn tất; không tự ghi lại.')
    c = PmvClient('kk', tag='dc-bank:' + source.username)
    f = F.financial(c, code)
    if (f['amount'] != source.amount or f['cash'] != Decimal(plan['cash'])
            or f['bank'] != Decimal(plan['bank'])):
        raise ValueError('Tổng hoặc phân bổ cọc đã được sửa ngoài lượt đối soát; cần kiểm tra trước khi ghi.')
    prior = list(Operation.objects.filter(target='kk', trn_id=code, kind='bank_reconcile', status='done').order_by('id'))
    old_rows = []
    for op in prior:
        saved = op.evidence['bank']
        current = query('SELECT ' + COLUMNS + ' FROM bank_notifications WHERE id=%s', [saved['id']])
        if not current or fingerprint(current[0]) != op.evidence['bank_fingerprint']:
            raise ValueError('Giao dịch ngân hàng đã đối soát bị sửa/xóa; cần kiểm tra, không tự đảo tiền.')
        old_rows.append(current[0])
    candidates = query('SELECT ' + COLUMNS + ' FROM bank_notifications WHERE direction=%s AND is_check=0 '
        'AND transaction_time >= %s AND transaction_time <= %s AND description LIKE %s ORDER BY id',
        ['in', f['h']['TrnDate'], timezone.localtime().replace(tzinfo=None), '%' + code + '%'])
    candidates = [r for r in candidates if contains_code(r, code)]
    if not candidates:
        if old_rows and sum(Decimal(r['trans_amount']) for r in old_rows) < f['bank']:
            return 'partial', 'CK đã khớp một phần; còn chờ chứng từ cho phần CK nhân viên xác nhận.'
        return 'matched' if old_rows else 'waiting', 'Đã đối soát CK; tiếp tục kiểm tra giao dịch mới.' if old_rows else 'Đã xác nhận thu; chưa có CK chứa mã ' + code + '.'
    for row in candidates:
        codes = set(re.findall(r'(?:TDC|TRC)\d{12}', str(row.get('description') or '').upper()))
        if codes - {code.upper()}:
            raise ValueError('Nội dung CK chứa nhiều mã phiếu; cần chọn phân bổ, chưa tự ghi.')
    allocation(f['amount'], plan.get('initial_bank'), old_rows, candidates)  # Kiểm tra cả lô trước khi ghi.
    for row in candidates:
        cash, bank = allocation(f['amount'], plan.get('initial_bank'), old_rows, [row])
        validate_reallocation(c, f, cash, bank)
        with transaction.atomic():
            locked = DepositOrderState.objects.select_for_update().get(pk=state.pk)
            if locked.version != state.version:
                raise ValueError('Phiếu vừa được cập nhật; đối soát lại lượt sau.')
            suffix = ' FOR UPDATE' if connection.vendor == 'mysql' else ''
            latest = query('SELECT ' + COLUMNS + ' FROM bank_notifications WHERE id=%s' + suffix, [row['id']])
            if not latest or latest[0] != row:
                raise ValueError('Giao dịch ngân hàng vừa thay đổi hoặc đã được dùng.')
            if (BankReconcileState.objects.filter(notification_id=row['id'], trn_id__isnull=False).exclude(trn_id='').exists()
                    or ThauPaymentLink.objects.filter(active_notification_id=row['id']).exists()):
                raise ValueError('Giao dịch đã dùng cho hóa đơn bán/thâu; không dùng lại cho cọc.')
            op = Operation.objects.create(target='kk', trn_id=code, kind='bank_reconcile', amount=f['amount'], cash=cash, bank=bank,
                bank_key='bank:' + str(row['id']), active_key='kk:' + code, token=uuid.uuid4().hex, status='running',
                user_id=source.user_id, till_id=source.till_id, username=source.username,
                evidence={'stamp': F.money_stamp(c, f['h']), 'before': F.safe_data(f), 'bank': F.safe_data(row),
                          'bank_fingerprint': fingerprint(row)})
            with connection.cursor() as cur:
                cur.execute('UPDATE bank_notifications SET is_check=1 WHERE id=%s AND is_check=0', [row['id']])
                if cur.rowcount != 1: raise ValueError('CK đã được sử dụng.')
        try:
            after = reallocate(c, op)
            with transaction.atomic():
                locked = DepositOrderState.objects.select_for_update().get(pk=state.pk)
                locked.payment_plan = {**locked.payment_plan, 'cash': str(cash), 'bank': str(bank)}
                locked.version += 1
                locked.save()
                state.version = locked.version
                op.status = 'done'; op.active_key = None; op.completed_at = timezone.now()
                op.message = 'Đã đối soát CK và xác minh phân bổ TM + CK bằng tổng cọc.'
                op.evidence['after'] = F.safe_data(after)
                op.save()
            f = after
            old_rows.append(row)
        except Exception:
            D.log.exception('Đối soát CK cọc gián đoạn: %s', code)
            op.status = 'uncertain'; op.message = 'Cần kiểm tra quỹ; giữ bằng chứng ngân hàng, không phát lại.'; op.save()
            raise ValueError(op.message)
        finally:
            F.log_event(op); D.W.invalidate('kk')
    if sum(Decimal(r['trans_amount']) for r in old_rows) < f['bank']:
        return 'partial', 'CK đã khớp một phần; còn chờ chứng từ cho phần CK nhân viên xác nhận.'
    return 'matched', 'Đã đối soát CK theo mã phiếu; tổng cọc giữ nguyên.'


def process_bank():
    count = 0
    # Nếu PMV đã thu thành công nhưng ghi metadata bị gián đoạn, chỉ khôi phục cờ đối soát;
    # không gọi lại thu tiền. Thứ tự thời gian mới nhất trước để không quét lại toàn bộ lịch sử.
    for receipt in Operation.objects.filter(target='kk', kind='receive', status='done',
            evidence__reconcile_later=True).order_by('-id')[:100]:
        state = DepositOrderState.objects.filter(target='kk', trn_id=receipt.trn_id).first()
        if state and not state.payment_plan.get('receipt_operation'):
            enable_reconcile(receipt)
    states = list(DepositOrderState.objects.filter(target='kk', payment_plan__reconcile_enabled=True).order_by('updated_at')[:30])
    for state in states:
        try:
            status, message = reconcile(state)
            count += status == 'matched'
        except ValueError as exc:
            status, message = 'review', str(exc)
        except Exception:
            D.log.exception('Không đọc được đối soát CK cọc %s', state.trn_id)
            status, message = 'review', 'Chưa đối soát được; kiểm tra kết nối và chứng từ.'
        with transaction.atomic():
            fresh = DepositOrderState.objects.select_for_update().get(pk=state.pk)
            fresh.payment_plan = {**fresh.payment_plan, 'bank_status': status, 'bank_message': message}
            fresh.save()  # updated_at luân phiên danh sách; không thay đổi ghi chú/tiến độ của NV.
    if states: D.W.invalidate('kk')
    return count
