# -*- coding: utf-8 -*-
"""BÁO NGƯỢC KẾT QUẢ ĐỐI SOÁT CHI CẦM ĐỒ SANG KHCD (GĐ chốt 29/09/2026).

Cầm đồ KHÔNG có chứng từ trên KK — nên sau khi đối soát khớp giao dịch ngân hàng RA ("THANH TOAN TIEN VANG + 6 số
log phiên"), KHBL không ghi KK mà ghi lại ĐÚNG dòng tiền phiên bên KHCD: khj_cd.cd_payments (dòng tách tiền,
channel IS NULL, direction='OUT' — KHCD tự ghi lúc lập phiên với cashPay = amount, cardPay = 0, 'RECORDED').

  · cardPay = tổng liên kết còn hiệu lực (CK nhiều lần cộng dồn) · cashPay = amount − cardPay (ràng buộc
    ck_cd_payment_split của KHCD: cashPay + cardPay = amount).
  · reconciliation_state: RECONCILED (đủ) · PARTIAL (một phần) · RECORDED (gỡ hết) — KHCD đã có sẵn luật: phiên ở
    PARTIAL/RECONCILED thì KHÔNG cho sửa / xóa dòng tiền (live_loans.payment_edit_state, delete_state).
  · bank_snapshot: chỉ JSON_SET thêm transfer_status/reconciled_* — không đọc lại ảnh QR lớn trong đó.
Chỉ đụng dòng đang ở RECORDED / PARTIAL / RECONCILED; trạng thái khác (LEGACY_RECORDED, MATCHED…) là của luồng
khác → bỏ qua, không ghi. Ghi lại TỔNG (không cộng dồn tại chỗ) nên chạy lại bao nhiêu lần cũng an toàn.
"""
import datetime as dt
import json
import logging
import re
from decimal import Decimal

from django.conf import settings
from django.db import connection, transaction
from django.utils import timezone

logger = logging.getLogger(__name__)
TRANG_THAI_CUA_TA = ('RECORDED', 'PARTIAL', 'RECONCILED')
# GĐ chốt 29/09/2026: giữ nguyên ngày đã qua, chỉ báo ngược nhóm cầm đồ lập TỪ HÔM NAY (job quét 3 ngày không ghi lùi).
AP_DUNG_TU = dt.date(2026, 9, 29)


def thuoc_dien(order):
    g = order.get('bank')
    tao = getattr(g, 'created_at', None)
    if not tao:
        return False
    ngay = timezone.localtime(tao).date() if timezone.is_aware(tao) else tao.date()
    return ngay >= AP_DUNG_TU


def _bang(ten):
    db = getattr(settings, 'KHCD_DB', 'khj_cd') or 'khj_cd'
    if not re.fullmatch(r'[A-Za-z0-9_]+', db):
        raise ValueError('Cấu hình KHCD_DB không hợp lệ.')
    return f'`{db}`.`{ten}`'


def log_id_cua(order):
    """bill_codes[0] của nhóm cầm đồ = yy-mm-dd-{log_id 6 số} (KHCD live_loans.outgoing_bill_code)."""
    bill = (order.get('bill_codes') or [''])[0] or ''
    so = re.sub(r'\D', '', bill)
    return int(so[-6:]) if len(so) >= 7 and int(so[-6:]) > 0 else None


def muc_tieu(order):
    """(cardPay, trạng thái) mong muốn theo đối soát."""
    paid = Decimal(order.get('paid') or 0)
    if paid <= 0:
        return Decimal(0), 'RECORDED'
    return paid, ('RECONCILED' if paid >= Decimal(order.get('required') or 0) else 'PARTIAL')


def ghi_mot(order):
    """Ghi 1 nhóm cầm đồ. Trả True khi vừa đổi; False khi đã đúng / không thuộc diện; ValueError khi dữ liệu lệch."""
    if order.get('problems') or not thuoc_dien(order):
        return False
    log_id, sku = log_id_cua(order), (order.get('ids') or [''])[0]
    if not log_id or not sku:
        raise ValueError('Nhóm cầm đồ thiếu mã phiên/log.')
    card, trang_thai = muc_tieu(order)
    refs = [str((l.bank_snapshot or {}).get('ref_code') or '') for l in (order.get('links') or [])
            if l.active_notification_id]
    my = connection.vendor == 'mysql'
    khoa, json_vao = (' FOR UPDATE', 'CAST(%s AS JSON)') if my else ('', 'json(%s)')      # sqlite: bộ kiểm
    with transaction.atomic(), connection.cursor() as cur:
        cur.execute(f'SELECT l.id FROM {_bang("cd_loan_logs")} l JOIN {_bang("cd_loans")} n ON n.id = l.loan_id '
                    'WHERE l.id = %s AND n.sku = %s', [log_id, sku])
        if not cur.fetchone():
            raise ValueError(f'Không thấy log #{log_id} của phiếu {sku} bên Cầm đồ.')
        cur.execute(f"SELECT id, amount, cashPay, cardPay, reconciliation_state FROM {_bang('cd_payments')} "
                    "WHERE log_id = %s AND direction = 'OUT' AND channel IS NULL" + khoa, [log_id])
        rows = cur.fetchall()
        if len(rows) != 1:
            raise ValueError(f'Log #{log_id} có {len(rows)} dòng chi tiền — cần kiểm tra bên Cầm đồ.')
        pid, amount, cash0, card0, st0 = rows[0]
        if st0 not in TRANG_THAI_CUA_TA:
            return False                    # dòng của luồng khác — không đụng
        amount = Decimal(amount)
        if card > amount:
            raise ValueError(f'Tổng CK đã đối soát ({card}) lớn hơn tiền chi của phiên ({amount}).')
        if (Decimal(card0 or 0), st0) == (card, trang_thai):
            return False
        cur.execute(
            f"UPDATE {_bang('cd_payments')} SET cashPay = %s, cardPay = %s, reconciliation_state = %s, "
            "bank_snapshot = JSON_SET(bank_snapshot, '$.transfer_status', %s, '$.reconciled_amount', %s, "
            f"'$.reconciled_refs', {json_vao}, '$.reconciled_by', 'KHBL', '$.reconciled_at', %s) "
            "WHERE id = %s AND reconciliation_state = %s",
            [amount - card, card, trang_thai, 'PREPARED' if trang_thai == 'RECORDED' else trang_thai, str(card),
             json.dumps(refs), timezone.localtime().replace(tzinfo=None, microsecond=0).isoformat(sep=' '), pid, st0])
        if cur.rowcount != 1:
            raise ValueError('Dòng tiền phiên vừa bị tác vụ khác sửa — lượt sau thử lại.')
    logger.info('Báo KHCD log #%s (%s): cardPay=%s, %s', log_id, sku, card, trang_thai)
    return True


def dong_bo(orders):
    """Ghi mọi nhóm cầm đồ trong danh sách. Trả (số vừa đổi, list lỗi) — lỗi từng nhóm không chặn nhóm khác."""
    xong, loi = 0, []
    for od in orders:
        if od.get('nghiep_vu') != 'camdo':
            continue
        try:
            xong += bool(ghi_mot(od))
        except Exception as exc:
            logger.warning('Báo KHCD %s: %s', od.get('ids'), exc)
            loi.append(f"{','.join(od.get('ids') or [])}: {exc}")
    return xong, loi


def trang_thai(orders):
    """{TrnID: (reconciliation_state, cardPay)} của dòng chi tiền phiên cầm đồ bên KHCD — CHỈ ĐỌC, cho danh sách."""
    can = {log_id_cua(o): (o.get('ids') or [''])[0] for o in orders if o.get('nghiep_vu') == 'camdo'}
    can = {k: v for k, v in can.items() if k}
    if not can:
        return {}
    try:
        with connection.cursor() as cur:
            cur.execute(f"SELECT log_id, reconciliation_state, cardPay FROM {_bang('cd_payments')} "
                        "WHERE direction = 'OUT' AND channel IS NULL AND log_id IN (" + ','.join(['%s'] * len(can)) + ')',
                        list(can))
            return {can[r[0]]: (r[1], Decimal(r[2] or 0)) for r in cur.fetchall()}
    except Exception as exc:
        logger.warning('Đọc trạng thái KHCD: %s', exc)
        return {}
