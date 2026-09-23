"""Read linked IN receipts without treating generated QR instructions as money."""
from collections import defaultdict
from decimal import Decimal
from django.db import connection
from .deposit_models import DepositMoneyOperation
from .models import MoneyFlowBankReceipt


def amounts_by_flow(flows):
    """{flow.pk: [số tiền CK đã xác nhận > 0]} — trang mobile hiện "đã nhận"."""
    result = receipts_by_flow(flows)
    return {f.pk: [v for v in result[f.pk].values() if v > 0] for f in flows}


def receipts_by_flow(flows):
    """{flow.pk: {khóa: số tiền}} — khóa là id bank_notifications (chuỗi); riêng KHCD là 'cd:<id cd_payments>'
    (khác không gian id, tách tiền tố để không trùng). CHECK BILL dùng khóa để biết khoản CK nào đã có chủ."""
    result = defaultdict(dict)
    for receipt in MoneyFlowBankReceipt.objects.filter(flow_id__in=[f.pk for f in flows],status='applied').order_by('id'):
        result[receipt.flow_id][str(receipt.notification_id)] = receipt.amount
    by_source = {f.source_id: f for f in flows if f.source_system == 'KHBL'}
    for op in DepositMoneyOperation.objects.filter(trn_id__in=by_source, kind='bank_reconcile', status='done').order_by('id'):
        bank = (op.evidence or {}).get('bank') or {}
        if bank.get('id') and bank.get('direction') == 'in':
            result[by_source[op.trn_id].pk][str(bank['id'])] = Decimal(str(bank.get('trans_amount') or 0))
    if connection.vendor == 'mysql':
        if by_source:
            with connection.cursor() as cur:
                cur.execute("SELECT id,bill_code_raw,trans_amount FROM bank_notifications WHERE direction='in' AND is_check=1 AND bill_code_raw IN (" + ','.join(['%s'] * len(by_source)) + ') ORDER BY transaction_time,id', list(by_source))
                for pk, source, amount in cur.fetchall():
                    result[by_source[source].pk][str(pk)] = Decimal(str(amount or 0))
        cd = {f.source_id: f for f in flows if f.source_system == 'KHCD'}
        if cd:
            with connection.cursor() as cur:
                cur.execute("SELECT id,log_id,amount FROM khj_cd.cd_payments WHERE channel='BANK' AND direction='IN' AND reconciliation_state IN ('MATCHED','RECONCILED','CONFIRMED') AND log_id IN (" + ','.join(['%s'] * len(cd)) + ') ORDER BY id', list(cd))
                for pk, log, amount in cur.fetchall():
                    result[cd[str(log)].pk]['cd:' + str(pk)] = Decimal(str(amount or 0))
    return result
