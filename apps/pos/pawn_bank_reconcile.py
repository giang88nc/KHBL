"""KHCD bank evidence + source split in ONE MySQL transaction. No PMV/till writes."""
import datetime as dt
import hashlib
import json
import re
from decimal import Decimal

from django.db import connection, transaction
from django.utils import timezone
from .models import MoneyFlow, MoneyFlowPayment, MoneyFlowBankReceipt, BankReconcileState, ThauPaymentLink
from .payment_reference import pawn_reference, contains_reference
from . import vietqr


def query(sql, params=()):
    with connection.cursor() as cur:
        cur.execute(sql, params)
        keys = [c[0] for c in cur.description]
        return [dict(zip(keys, row)) for row in cur.fetchall()]


def checked_total(total, amounts):
    values = [Decimal(str(v)) for v in amounts]
    if any(not v.is_finite() or v <= 0 or v != v.to_integral_value() for v in values):
        raise ValueError('Chứng từ ngân hàng có số tiền không hợp lệ.')
    bank = sum(values, Decimal(0))
    if bank > total:
        raise ValueError('Tổng CK vượt tiền phiên; cần kiểm tra, không tự trừ âm tiền mặt.')
    return bank, total - bank


def identity(row):
    ref = str(row.get('ref_code') or '').strip()
    if not ref:
        raise ValueError('Thông báo ngân hàng thiếu mã giao dịch để chống ghi trùng.')
    return hashlib.sha256((str(row['bank_number']).strip()+'|'+str(row['direction']).lower()+'|'+ref).encode()).hexdigest()


def same_bank(name, code):
    clean = lambda s: re.sub(r'[^a-z0-9]', '', str(s or '').lower())
    aliases = {clean(code),clean(vietqr.bank_bin(code)),clean(vietqr.bank_ten(code))} - {''}
    return clean(name) in aliases


def candidate(row, reference, account, direction, start, end):
    try:
        stamp = dt.datetime.fromisoformat(row['transaction_time'])
        if stamp.tzinfo:
            stamp = timezone.localtime(stamp).replace(tzinfo=None)
    except (ValueError, TypeError):
        return False
    return (str(row['bank_number']).strip() == account.strip()
            and str(row['direction']).lower() == direction.lower()
            and start <= stamp <= end and contains_reference(row['description'], reference))


def ensure_transactional():
    rows = query("SELECT TABLE_SCHEMA,TABLE_NAME,ENGINE FROM information_schema.TABLES WHERE "
                 "(TABLE_SCHEMA=DATABASE() AND TABLE_NAME IN ('bank_notifications','money_flow','money_flow_payment','money_flow_bank_receipt','money_flow_source_write')) "
                 "OR (TABLE_SCHEMA='khj_cd' AND TABLE_NAME IN ('cd_loans','cd_loan_logs','cd_payments'))")
    if len(rows) != 8 or any(r['ENGINE'] != 'InnoDB' for r in rows):
        raise ValueError('Chưa bật đối soát: bank_notifications và các bảng thanh toán phải dùng InnoDB để khóa/rollback an toàn.')


def reconcile(flow_id, version=None):
    """IN only. Lock source, evidence, source allocation and projection together."""
    from .money_in import collect, persist, WINDOW_HOURS
    if connection.vendor != 'mysql':
        raise ValueError('Đối soát cần MySQL/InnoDB.')
    ensure_transactional()
    with transaction.atomic():
        flow=MoneyFlow.objects.select_for_update().get(pk=flow_id)
        if flow.source_system!='KHCD' or flow.direction!='IN' or flow.is_void:
            raise ValueError('Phiếu không còn hiệu lực IN.')
        instruction=MoneyFlowPayment.objects.select_for_update().filter(flow=flow,method='BANK',status__in=['ready','success']).first()
        if not instruction:raise ValueError('Chưa có QR đang hiệu lực.')
        if version is not None and instruction.updated_at.isoformat()!=version:
            raise ValueError('QR đã thay đổi.')
        loans=query('SELECT id FROM khj_cd.cd_loans WHERE id=%s FOR UPDATE',[flow.source_group_id])
        logs=query('SELECT id,loan_id,happened_at,operation_id,request_key FROM khj_cd.cd_loan_logs WHERE id=%s FOR UPDATE',[flow.source_id])
        if not loans or not logs or str(logs[0]['loan_id'])!=flow.source_group_id or logs[0]['operation_id'] in (0,8):
            raise ValueError('Phiên đã bị hủy hoặc thay đổi.')
        log=logs[0]
        reference=pawn_reference(log['happened_at'],log['loan_id'],log['id'])
        if instruction.transfer_content!=reference:raise ValueError('QR không đúng mã phiên.')
        pays=query('SELECT * FROM khj_cd.cd_payments WHERE log_id=%s ORDER BY id FOR UPDATE',[log['id']])
        if len(pays)!=1 or not log['request_key']:
            raise ValueError('Phiên lịch sử nhiều dòng cần đối chiếu riêng.')
        pay=pays[0];total=Decimal(pay['amount'])
        if pay['direction']!='IN' or total!=flow.expected_amount or pay['reconciliation_state'] in ('VOID','REVERSED'):
            raise ValueError('Nguồn đã thay đổi tổng/hướng tiền.')
        now=timezone.localtime().replace(tzinfo=None)
        start=log['happened_at'];end=min(now,start+dt.timedelta(hours=WINDOW_HOURS))
        if now<start:raise ValueError('Thời gian phiên nằm trong tương lai.')
        old,new=collect(flow,start,end)
        prior=sum((r.amount for r in old),Decimal(0))
        source_bank=pay['cardPay'] if pay['cardPay'] is not None else pay['amount'] if pay['channel']=='BANK' else 0
        if Decimal(source_bank)!=prior:raise ValueError('Nguồn đã có CK ngoài bộ đối soát; cần xác minh trước.')
        bank,cash=checked_total(total,[r.amount for r in old]+[r['trans_amount'] for r,k in new])
        persist(flow,new,'applied')
        if new:
            with connection.cursor() as cur:
                cur.execute('UPDATE khj_cd.cd_payments SET channel=NULL,cashPay=%s,cardPay=%s,payment_ref=%s,reconciliation_state=%s WHERE id=%s',
                            [cash,bank,reference,'MATCHED' if bank==total else 'PARTIAL',pay['id']])
            verify=query('SELECT amount,cashPay,cardPay FROM khj_cd.cd_payments WHERE id=%s',[pay['id']])[0]
            if (verify['amount'],verify['cashPay'],verify['cardPay'])!=(total,cash,bank):
                raise ValueError('Đọc lại tiền nguồn không khớp.')
        flow.cash_amount,flow.bank_amount=cash,bank
        flow.payment_status=MoneyFlow.CONFIRMED if bank==total else MoneyFlow.PARTIAL if bank else MoneyFlow.RECORDED
        flow.save(update_fields=['cash_amount','bank_amount','payment_status','synced_at'])
        success=bank>=instruction.received_before+instruction.amount and bank>0
        if success:MoneyFlowPayment.objects.filter(pk=instruction.pk).update(status='success',active_key=None)
        return dict(status='success' if success else 'partial' if bank else 'waiting',received=str(bank),cash=str(cash),fully_bank_paid=bank==total)
