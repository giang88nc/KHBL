"""Chuẩn hóa chỉ dẫn CASH/BANK và QR cho sổ dòng tiền GĐ2."""
import base64
from io import BytesIO
from decimal import Decimal, InvalidOperation
from django.db import transaction

import segno

from . import vietqr
from .models import MoneyFlow, MoneyFlowPayment


@transaction.atomic
def reopen_instruction(flow, instruction_id, user):
    """Explicit POST: resume an eligible saved QR without regenerating its payload."""
    from .money_in import check_source
    from .models import MoneyFlowSourceWrite
    from django.shortcuts import get_object_or_404
    flow = MoneyFlow.objects.select_for_update().get(pk=flow.pk)
    qr = get_object_or_404(MoneyFlowPayment, pk=instruction_id, flow=flow, method='BANK')
    if flow.direction != 'IN' or flow.is_void or qr.status == 'cancelled':
        raise ValueError('QR không còn hợp lệ.')
    if qr.status == 'success':
        return qr
    check_source(flow, creating=True)
    if qr.transfer_content != transfer_content(flow):
        raise ValueError('Mã chuyển khoản không khớp phiếu.')
    if MoneyFlowSourceWrite.objects.filter(flow=flow,status='pending').exists():
        raise ValueError('Đang đồng bộ chứng từ; vui lòng thử lại sau.')
    received = sum((r.amount for r in flow.bank_receipts.filter(status='applied')), Decimal(0))
    if qr.amount <= 0 or qr.amount > flow.expected_amount-received:
        raise ValueError('Số tiền QR vượt phần còn lại của phiếu.')
    MoneyFlowPayment.objects.filter(flow=flow,method='BANK',status='ready').exclude(pk=qr.pk).update(status='superseded',active_key=None)
    if qr.status != 'ready':
        qr.received_before = received
        qr.status = 'ready'
        qr.active_key = f'in:{flow.pk}'
        qr.updated_by = getattr(user,'username','')
        qr.save(update_fields=['received_before','status','active_key','updated_by','updated_at'])
    return qr


def channel_amount(flow, method):
    if method == MoneyFlowPayment.CASH:
        return flow.cash_amount
    # Trên app IN, phương thức được chọn trước khi khách quét. Với chứng từ cũ
    # chưa tách BANK, dùng toàn bộ số phải thu nhưng không sửa phân bổ ở nguồn.
    if flow.direction == MoneyFlow.IN and flow.bank_amount <= 0:
        return flow.expected_amount
    return flow.bank_amount


def transfer_content(flow):
    if flow.source_system == 'KHCD' and flow.source_type == 'cd_loan_log':
        from .payment_reference import pawn_reference
        return pawn_reference(flow.business_date, flow.source_group_id, flow.source_id)
    raw = flow.source_bill_code or f"{flow.source_system}{flow.source_id}"
    if flow.direction == MoneyFlow.IN:
        return ''.join(c for c in raw if c.isalnum())[:25]
    return "".join(c for c in raw if c.isalnum() or c in " -").strip()[:25]


def validate_split(total, bank, cash):
    try:
        bank, cash = Decimal(str(bank)), Decimal(str(cash))
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError('Số tiền không hợp lệ.')
    if not bank.is_finite() or not cash.is_finite() or bank <= 0 or cash < 0:
        raise ValueError('Tiền CK phải lớn hơn 0, tiền mặt không được âm.')
    if bank != bank.to_integral_value() or cash != cash.to_integral_value() or bank + cash != total:
        raise ValueError('CK + tiền mặt phải bằng tổng phiếu, theo đồng nguyên.')
    return bank, cash


def outgoing_bank(flow):
    snap = flow.source_snapshot or {}
    bank = snap.get("bank_snapshot") or {}
    return {
        "bank_code": snap.get("bank_code") or bank.get("bank_code") or bank.get("bank") or bank.get("bin") or "",
        "bank_account": snap.get("bank_account") or bank.get("bank_account") or bank.get("account") or bank.get("account_no") or "",
        "bank_owner": snap.get("bank_owner") or bank.get("bank_owner") or bank.get("owner") or bank.get("account_name") or "",
    }


def qr_image(payload):
    buf = BytesIO()
    segno.make(payload, error="m").save(buf, kind="png", scale=7, border=4)
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


@transaction.atomic
def save_instruction(flow, method, user, *, company_bank=None, bank_code="",
                     bank_account="", bank_owner="", custom_bank=None, custom_cash=None, note=None):
    import hashlib, json
    from .models import MoneyFlowSourceWrite
    flow=MoneyFlow.objects.select_for_update().get(pk=flow.pk)
    if flow.is_void:raise ValueError('Dòng tiền đã vô hiệu lực.')
    in_bank=flow.direction=='IN' and method=='BANK'
    received=sum((r.amount for r in flow.bank_receipts.filter(status='applied')),Decimal(0)) if in_bank else Decimal(0)
    if in_bank:
        from .money_in import check_source
        check_source(flow,creating=True)
        if MoneyFlowSourceWrite.objects.filter(flow=flow,status='pending').exists():
            raise ValueError('Đang đồng bộ chứng từ đã nhận; không tạo lại QR hoặc chuyển lại tiền.')
        if not company_bank:raise ValueError('Chưa chọn tài khoản nhận.')
        bank_code=company_bank.get('bank_bin') or ''
        bank_account=company_bank.get('bank_number') or ''
        bank_owner=company_bank.get('bank_user') or ''
        bank_name=company_bank.get('bank_name') or vietqr.bank_ten(bank_code)
        content=transfer_content(flow)
        if note is not None and str(note).replace('-','').replace(' ','')!=content:
            raise ValueError('Nội dung chuyển khoản phải giữ nguyên mã phiếu chuẩn hóa.')
        try:amount=Decimal(str(custom_bank)) if custom_bank is not None else flow.expected_amount-received
        except InvalidOperation:raise ValueError('Số tiền không hợp lệ.')
    else:
        amount=channel_amount(flow,method)
        bank_name=vietqr.bank_ten(bank_code)
        content=transfer_content(flow) if method=='BANK' else ''
    if not amount.is_finite() or amount<=0 or amount!=amount.to_integral_value():raise ValueError('Số tiền phải là đồng nguyên và lớn hơn 0.')
    key=hashlib.sha256(json.dumps([flow.pk,method,vietqr.bank_bin(bank_code),bank_account,str(amount.normalize()),content],ensure_ascii=True).encode()).hexdigest()
    existing=MoneyFlowPayment.objects.filter(qr_key=key).first() if in_bank else MoneyFlowPayment.objects.filter(flow=flow,method=method).first()
    if in_bank and existing:
        if existing.status=='cancelled':raise ValueError('Yêu cầu này đã hủy; cần kiểm tra trước khi sử dụng lại.')
        if existing.status!='success':
            validate_split(flow.expected_amount-received,amount,
                custom_cash if custom_cash is not None else flow.expected_amount-received-amount)
        if existing.status=='superseded':
            MoneyFlowPayment.objects.filter(flow=flow,method=method,status='ready').update(status='superseded',active_key=None)
            existing.status='ready';existing.active_key=f'in:{flow.pk}'
            existing.save(update_fields=['status','active_key'])
        return existing
    if in_bank:
        validate_split(flow.expected_amount-received,amount,custom_cash if custom_cash is not None else flow.expected_amount-received-amount)
        # Retain old QR payloads/accounts for late transfers; only one active instruction.
        MoneyFlowPayment.objects.filter(flow=flow,method=method,status='ready').update(status='superseded',active_key=None)
        if flow.service=='DEPOSIT':
            from .models import GoldBill
            from .deposit_models import DepositMoneyOperation
            if DepositMoneyOperation.objects.filter(trn_id=flow.source_id,kind='bank_reconcile',status__in=['running','queued']).exists():
                raise ValueError('Bộ đối soát cọc cũ đang chạy; thử lại sau khi kết thúc.')
            bill=GoldBill.all_objects.select_for_update().get(target='kk',trn_id=flow.source_id)
            bill.payment_plan={**(bill.payment_plan or {}),'money_flow_in_v2':True,'reconcile_enabled':False}
            bill.save(update_fields=['payment_plan','updated_at'])
    values=dict(amount=amount,received_before=received,status='ready',updated_by=getattr(user,'username','') or '',
                bank_code=bank_code if method=='BANK' else '',bank_name=bank_name if method=='BANK' else '',
                bank_account=bank_account if method=='BANK' else '',bank_owner=bank_owner if method=='BANK' else '',
                transfer_content=content,qr_payload=vietqr.payload(bank_code,bank_account,amount,content,bank_owner,exact_amount=True) if method=='BANK' else '')
    if in_bank:
        return MoneyFlowPayment.objects.create(flow=flow,method=method,qr_key=key,active_key=f'in:{flow.pk}',
            created_by=getattr(user,'username','') or '',**values)
    if existing:
        for k,v in values.items():setattr(existing,k,v)
        existing.save(update_fields=[*values,'updated_at']);return existing
    return MoneyFlowPayment.objects.create(flow=flow,method=method,created_by=getattr(user,'username','') or '',**values)
