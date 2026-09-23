"""IN reconciliation only. MySQL evidence first, durable MSSQL allocation second."""
import datetime as dt
import json
import re
from decimal import Decimal
from contextlib import contextmanager
from django.db import connection, transaction
from django.utils import timezone
from apps.pmv import gateway
from .models import (MoneyFlow, MoneyFlowPayment, MoneyFlowBankReceipt, MoneyFlowSourceWrite,
                     GoldBill, BankReconcileState, ThauPaymentLink)
from . import pawn_bank_reconcile as P

WINDOW_HOURS = 3

class Busy(ValueError):
    pass


class AmbiguousReceipt(ValueError):
    """Unallocated bank evidence; polling may continue without accepting it."""
    pass


@contextmanager
def flow_lock(pk):
    """Also spans the remote commit, unlike a local transaction alone."""
    key=f'money-in:{pk}'
    if connection.vendor=='mysql':
        if P.query('SELECT GET_LOCK(%s,0) ok',[key])[0]['ok']!=1:
            raise Busy('Một tác vụ đang đối soát phiếu này; thử lại sau.')
    try:yield
    finally:
        if connection.vendor=='mysql':P.query('SELECT RELEASE_LOCK(%s) ok',[key])


def source(flow):
    if flow.direction!='IN' or flow.is_void:raise ValueError('Phiếu không còn hiệu lực IN.')
    if flow.source_system=='KHBL':
        bill=GoldBill.all_objects.get(trn_id=flow.source_id,target='kk')
        if bill.is_del or bill.fulfilment=='cancelled':raise ValueError('Phiếu đã hủy/xóa.')
        service='DEPOSIT' if flow.source_type=='gold_bill_deposit' else 'RETAIL'
        if flow.source_type not in ('gold_bill_deposit','gold_bill_retail'):raise ValueError('Nguồn chưa hỗ trợ IN.')
        snap=gateway.pmv_in_snapshot(service,flow.source_bill_code,flow.source_id)
        total=Decimal(snap['TienCoc' if service=='DEPOSIT' else 'PayAmount'])
        stamp=dt.datetime.combine(dt.datetime.fromisoformat(snap['TrnDate']).date(),dt.time.fromisoformat(snap['TrnTime'] or '00:00:00'))
        return stamp,total,snap
    rows=P.query('SELECT l.happened_at,l.operation_id,p.amount,p.direction FROM khj_cd.cd_loan_logs l '
                 'JOIN khj_cd.cd_payments p ON p.log_id=l.id WHERE l.id=%s AND l.loan_id=%s',[flow.source_id,flow.source_group_id])
    if not rows or any(r['direction']!='IN' or r['operation_id'] in (0,8) for r in rows):raise ValueError('Phiên cầm đồ đã hủy hoặc không có tiền IN.')
    return rows[0]['happened_at'],sum((Decimal(r['amount']) for r in rows),Decimal(0)),{}


def check_source(flow, *, creating=False):
    stamp,total,snap=source(flow)
    if total!=flow.expected_amount:raise ValueError('Tổng tiền nguồn đã thay đổi. Cần đồng bộ lại phiếu trước khi tạo/đối soát QR.')
    if creating and timezone.localtime().replace(tzinfo=None)>stamp+dt.timedelta(hours=WINDOW_HOURS):
        raise ValueError('Phiếu đã ngoài cửa sổ tự đối soát 3 giờ. Không tạo QR mới; cần kiểm tra thủ công.')
    return stamp,total,snap


def codes(text):
    text=text or ''
    text=re.sub(r'(?<!\d)(\d{2})-(\d{2})-(\d{2})-(\d{6})(?!\d)',r'\1\2\3\4',text)
    text=re.sub(r'(?<!\d)(\d{4})-(\d{5})-(\d{5})(?!\d)',r'\1\2\3',text)
    result=set()
    for value in re.findall(r'(?<!\d)(?:\d{14}|\d{12})(?!\d)',text):
        try:
            if len(value)==12:
                # Retail/deposit reference starts with a real YYMMDD. This rejects
                # MOMO/FT/ZP transaction IDs that merely happen to contain 12 digits.
                dt.date(2000+int(value[:2]),int(value[2:4]),int(value[4:6]))
            else:
                # Legacy pawn reference starts with YYMM. Bank transaction IDs such
                # as FT26264117829075 have month 26 and are not document references.
                if not 1<=int(value[2:4])<=12:continue
            result.add(value)
        except ValueError:
            continue
    return result


def collect(flow, start, end):
    """Caller holds transaction/flow lock; accept accounts from old AND new QR logs."""
    from .money_flow_payment import transfer_content
    ref=transfer_content(flow)
    variants={ref}
    if len(ref)==12:variants.add(f'{ref[:2]}-{ref[2:4]}-{ref[4:6]}-{ref[6:]}')
    if MoneyFlow.objects.filter(source_system=flow.source_system,direction='IN',source_bill_code__in=variants,is_void=False).exclude(pk=flow.pk).exists():
        raise ValueError('Mã chứng từ trùng trên nhiều dòng tiền; cần xác minh nguồn.')
    logs=list(MoneyFlowPayment.objects.filter(flow=flow,method='BANK').exclude(status='cancelled'))
    accounts={p.bank_account for p in logs if p.transfer_content.replace('-','').replace(' ','')==ref}
    old=list(MoneyFlowBankReceipt.objects.filter(flow=flow).order_by('id'))
    seen={r.bank_identity:r.amount for r in old}
    if not accounts:return old,[]
    rows=P.query('SELECT id,ref_code,bank_number,bank_name,transaction_time,trans_amount,direction,description,is_check '
                 'FROM bank_notifications WHERE direction=%s AND bank_number IN ('+','.join(['%s']*len(accounts))+') '
                 'AND transaction_time>=%s AND transaction_time<=%s ORDER BY id FOR UPDATE',
                 ['in',*sorted(accounts),str(start),str(end)])
    found={r['id']:r for r in rows}
    for receipt in old:
        row=found.get(receipt.notification_id)
        if not row or not row['is_check'] or Decimal(row['trans_amount'])!=receipt.amount or any(
                str(row[k])!=str(receipt.evidence.get(k)) for k in ('ref_code','bank_number','direction','description','transaction_time')):
            raise ValueError('Chứng từ ngân hàng đã dùng bị sửa/xóa; cần kiểm tra.')
    new=[]
    for row in rows:
        if ref not in codes(row['description']):continue
        try:
            stamp=dt.datetime.fromisoformat(row['transaction_time'])
            if stamp.tzinfo:stamp=timezone.localtime(stamp).replace(tzinfo=None)
        except (TypeError,ValueError):raise ValueError('Thời gian chứng từ ngân hàng không hợp lệ.')
        if not start<=stamp<=end:continue
        if codes(row['description'])!={ref}:raise AmbiguousReceipt('Nội dung chứa nhiều mã phiếu; không tự phân bổ.')
        if not any(p.bank_account==row['bank_number'] and P.same_bank(row['bank_name'],p.bank_code) for p in logs):
            raise ValueError('Ngân hàng thông báo không khớp tài khoản QR.')
        key=P.identity(row)
        if key in seen:
            if seen[key]!=row['trans_amount']:raise ValueError('Trùng mã ngân hàng nhưng khác tiền.')
            continue
        if (row['is_check'] or MoneyFlowBankReceipt.objects.filter(notification_id=row['id']).exists()
                or MoneyFlowBankReceipt.objects.filter(bank_identity=key).exists()
                or BankReconcileState.objects.filter(notification_id=row['id']).exclude(trn_id='').exclude(trn_id__isnull=True).exists()
                or ThauPaymentLink.objects.filter(active_notification_id=row['id']).exists()):
            raise ValueError('Chứng từ đã dùng ở tác vụ khác; cần đối chiếu trước khi tiếp tục.')
        seen[key]=row['trans_amount'];new.append((row,key))
    P.checked_total(flow.expected_amount,[r.amount for r in old]+[r['trans_amount'] for r,key in new])
    return old,new


def persist(flow, new, status):
    from .money_flow_payment import transfer_content
    for row,key in new:
        MoneyFlowBankReceipt.objects.create(flow=flow,notification_id=row['id'],bank_identity=key,amount=row['trans_amount'],
            bill_code=transfer_content(flow),direction='IN',source_system=flow.source_system,bank_account=row['bank_number'],
            bank_ref=row['ref_code'],status=status,evidence=json.loads(json.dumps(row,default=str)))
        with connection.cursor() as cur:
            cur.execute('UPDATE bank_notifications SET is_check=1 WHERE id=%s AND is_check=0',[row['id']])
            if cur.rowcount!=1:raise ValueError('Thông báo vừa được tác vụ khác nhận.')


def finish(op):
    """This function is safe to retry after an unknown MSSQL commit outcome."""
    flow=op.flow
    service='DEPOSIT' if flow.source_type=='gold_bill_deposit' else 'RETAIL'
    receiving_account=(MoneyFlowBankReceipt.objects.filter(flow=flow,status__in=['pending','applied'])
                       .exclude(bank_account='').order_by('-id').values_list('bank_account',flat=True).first() or '')
    gateway.pmv_in_allocate(service,op.expected,op.bank,bank_account=receiving_account)
    with transaction.atomic():
        flow=MoneyFlow.objects.select_for_update().get(pk=flow.pk)
        op=MoneyFlowSourceWrite.objects.select_for_update().get(pk=op.pk)
        bill=GoldBill.all_objects.select_for_update().get(trn_id=flow.source_id,target='kk')
        if bill.is_del:raise ValueError('Phiếu vừa bị hủy; cần kiểm tra kết quả nguồn.')
        if service=='DEPOSIT':
            bill.payment_plan={**(bill.payment_plan or {}),'cash':str(op.cash),'bank':str(op.bank),
                'reconcile_enabled':False,'money_flow_in_v2':True,'bank_status':'matched' if op.bank==op.total else 'partial'}
            bill.save(update_fields=['payment_plan','updated_at'])
        else:
            bill.tien_mat,bill.tien_ck,bill.tien_the=op.cash,op.bank,0
            bill.pay_method='bank' if op.cash==0 else 'mixed'
            bill.save(update_fields=['tien_mat','tien_ck','tien_the','pay_method','updated_at'])
        MoneyFlowBankReceipt.objects.filter(flow=flow,status='pending').update(status='applied')
        flow.cash_amount,flow.bank_amount=op.cash,op.bank
        flow.payment_status=MoneyFlow.CONFIRMED if op.bank==op.total else MoneyFlow.PARTIAL
        flow.save(update_fields=['cash_amount','bank_amount','payment_status','synced_at'])
        op.status,op.active_key,op.error='applied',None,''
        op.save(update_fields=['status','active_key','error','updated_at'])


def apply_verified_retail_on_checkout(trn_id, bill_code, total, *, target='kk'):
    """Áp lại phần CK đã xác nhận khi hóa đơn bán được mở SỬA rồi thanh toán lại.

    Proc cập nhật hóa đơn có thể đưa phân bổ về mặc định. Bằng chứng ngân hàng
    ``applied`` vẫn thuộc đúng TrnID, nên ghi lại CardPay bằng tổng bằng chứng và
    CashPay bằng phần còn lại của tổng hóa đơn mới. Không có bằng chứng thì không ghi.
    """
    from django.db.models import Sum
    flow = MoneyFlow.objects.filter(
        source_system='KHBL', source_type='gold_bill_retail', source_id=trn_id,
        source_bill_code=bill_code, direction='IN', is_void=False,
    ).first()
    if not flow:
        return None
    bank = MoneyFlowBankReceipt.objects.filter(
        flow=flow, status='applied', direction='IN',
    ).aggregate(total=Sum('amount'))['total'] or Decimal(0)
    total = Decimal(total or 0)
    if bank <= 0:
        return None
    if bank > total:
        raise ValueError('Tiền chuyển khoản đã xác nhận lớn hơn tổng hóa đơn sau khi sửa; cần đối soát trước khi thanh toán.')
    account = (MoneyFlowBankReceipt.objects.filter(
        flow=flow, status='applied', direction='IN',
    ).exclude(bank_account='').order_by('-id').values_list('bank_account', flat=True).first() or '')
    snapshot = gateway.pmv_in_snapshot('RETAIL', bill_code, trn_id, target=target)
    gateway.pmv_in_allocate('RETAIL', snapshot, bank, bank_account=account, target=target)
    return {'cash': total-bank, 'bank': bank, 'flow_id': flow.pk}


def response(flow, instruction):
    instruction.refresh_from_db()
    if instruction.status in ('superseded','cancelled'):
        return dict(status='review',received='0',message='QR đã được thay thế hoặc hủy. Mở lại phiếu.')
    received=sum((r.amount for r in flow.bank_receipts.filter(status='applied')),Decimal(0))
    if instruction.status=='success':state='success'
    elif received>=instruction.received_before+instruction.amount and received>0:
        MoneyFlowPayment.objects.filter(pk=instruction.pk,status='ready').update(status='success',active_key=None)
        state='success'
    else:state='partial' if received else 'waiting'
    return dict(status=state,received=str(received),cash=str(flow.expected_amount-received),
                fully_bank_paid=received==flow.expected_amount,message='Đã xác minh' if state=='success' else 'Chờ ngân hàng')


def reconcile(flow_id, instruction_id=None):
    P.ensure_transactional()
    with flow_lock(flow_id):
        flow=MoneyFlow.objects.get(pk=flow_id)
        qs=MoneyFlowPayment.objects.filter(flow=flow,method='BANK')
        instruction=qs.filter(pk=instruction_id).first() if instruction_id else qs.filter(status__in=['ready','success']).first()
        if not instruction:raise ValueError('Chưa có QR để đối soát.')
        if instruction.status in ('cancelled','superseded'):raise ValueError('QR đã thay thế. Đóng và mở lại phiếu.')
        if flow.is_void:raise ValueError('Phiếu đã hủy.')
        if flow.source_system=='KHCD':
            # KHCD evidence + allocation can commit atomically on the same MySQL server.
            P.reconcile(flow_id)
            result=response(flow,instruction)
            stamp,_,_=source(flow)
            if timezone.localtime().replace(tzinfo=None)>stamp+dt.timedelta(hours=WINDOW_HOURS) and result['status']!='success':
                result.update(status='review',message='Đã ngoài cửa sổ đối soát 3 giờ; cần kiểm tra khoản đến muộn.')
            return result
        if flow.service=='DEPOSIT':
            from .deposit_models import DepositMoneyOperation
            with transaction.atomic():
                bill=GoldBill.all_objects.select_for_update().get(trn_id=flow.source_id,target='kk')
                if not (bill.payment_plan or {}).get('money_flow_in_v2'):
                    if DepositMoneyOperation.objects.filter(trn_id=flow.source_id,kind='bank_reconcile',status__in=['running','queued']).exists():
                        raise Busy('Bộ đối soát cũ đang hoàn tất.')
                    bill.payment_plan={**(bill.payment_plan or {}),'money_flow_in_v2':True,'reconcile_enabled':False}
                    bill.save(update_fields=['payment_plan','updated_at'])
        pending=MoneyFlowSourceWrite.objects.filter(flow=flow,status='pending').first()
        if pending:
            try:finish(pending)
            except Exception as exc:
                MoneyFlowSourceWrite.objects.filter(pk=pending.pk).update(error=str(exc)[:2000])
                return dict(status='pending',received='0',message='Đã giữ chứng từ; chờ đồng bộ nguồn. Không chuyển lại tiền.')
        # Poll MySQL only when no new evidence. Re-read MSSQL immediately before a write.
        bill=GoldBill.all_objects.get(trn_id=flow.source_id,target='kk')
        if bill.is_del or bill.fulfilment=='cancelled':raise ValueError('Phiếu đã hủy/xóa.')
        start=dt.datetime.combine(bill.trn_date,dt.time.fromisoformat(bill.trn_time or '00:00:00'))
        total=flow.expected_amount
        now=timezone.localtime().replace(tzinfo=None)
        end=min(now,start+dt.timedelta(hours=WINDOW_HOURS))
        if now<start:raise ValueError('Thời điểm nguồn nằm trong tương lai.')
        with transaction.atomic():
            flow=MoneyFlow.objects.select_for_update().get(pk=flow_id)
            old,new=collect(flow,start,end)
            if new:
                from .deposit_models import DepositMoneyOperation
                if not old and (DepositMoneyOperation.objects.filter(trn_id=flow.source_id,kind='bank_reconcile',status='done').exists()
                                or BankReconcileState.objects.filter(trn_id=flow.source_id).exists()):
                    raise ValueError('Phiếu có chứng từ ở bộ đối soát cũ; cần chuyển bằng chứng trước, không ghi đè tiền đã thu.')
                actual_start,total,snap=check_source(flow)
                if actual_start!=start:
                    # Some local rows only preserve HH:MM. Recheck candidates using
                    # exact source seconds; never widen the source time window.
                    start=actual_start
                    end=min(now,start+dt.timedelta(hours=WINDOW_HOURS))
                    old,new=collect(flow,start,end)
                    GoldBill.all_objects.filter(pk=bill.pk).update(trn_date=start.date(),trn_time=start.strftime('%H:%M:%S'))
                if new:
                    bank,cash=P.checked_total(total,[r.amount for r in old]+[r['trans_amount'] for r,k in new])
                    persist(flow,new,'pending')
                    op=MoneyFlowSourceWrite.objects.create(flow=flow,active_key=f'in:{flow.pk}',expected=snap,total=total,bank=bank,cash=cash)
                else:op=None
            else:op=None
        if op:
            try:finish(op)
            except Exception as exc:
                MoneyFlowSourceWrite.objects.filter(pk=op.pk).update(error=str(exc)[:2000])
                return dict(status='pending',received='0',message='Đã giữ chứng từ; chờ đồng bộ nguồn. Không chuyển lại tiền.')
        result=response(flow,instruction)
        if now>start+dt.timedelta(hours=WINDOW_HOURS) and result['status']!='success':
            result.update(status='review',message='Ngoài cửa sổ 3 giờ. QR không tự vô hiệu; khoản đến muộn cần đối chiếu thủ công.')
        return result
