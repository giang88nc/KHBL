"""Quy ước hàng đặt đã đối chiếu với BANLE_V5 /stock/_order.html."""
import datetime as dt
import re
from decimal import Decimal, InvalidOperation

from django.utils import timezone

ORDER_STATES = {'W': 'Đang chờ', 'R': 'Hàng sẵn sàng', 'C': 'Đã giao khách', 'D': 'Đã hủy', 'P': 'Đã thanh toán'}
TABS = [('all', 'DANH SÁCH'), ('ready', 'Hàng sẵn sàng'), ('delivered', 'Đã giao'), ('pending', 'Chưa giao'), ('notifications','THÔNG BÁO')]
TAB_STATUS = {'all': '', 'ready': 'R', 'delivered': 'C', 'pending': 'X'}


def split_stock_note(note):
    raw = str(note or '')
    match = re.match(r'^\s*SP\s*:\s*([^|]+)(?:\|\s*)?(.*)$', raw, re.I | re.S)
    return (match[1].strip(), match[2].strip()) if match else ('', raw)


def item_info(row):
    code, note = split_stock_note(row.get('Notes'))
    return {**row, 'Mode': 'stock' if code else 'new', 'ProductCode': code, 'Notes': note}


def is_mobile_order(row):
    # appMobile tạo TRC không có ShopID; vendor có ShopID, có thể dùng cùng prefix.
    return str(row.get('TrnID') or '').startswith('TRC') and not row.get('ShopID')

# SQL Server 2005: ngày hẹn nằm trong Description; appMobile cũ còn ghi vào UserID_Upd.
# Chỉ chuyển chuỗi đã được xác nhận hợp lệ, không chuyển trực tiếp ghi chú tùy ý.
PROMISE_SQL = """CONVERT(datetime, CASE
 WHEN LEFT(LTRIM(d.Description),4)='HEN:'
 AND ISDATE(REPLACE(SUBSTRING(LTRIM(d.Description),5,10),'/','-'))=1
 THEN REPLACE(SUBSTRING(LTRIM(d.Description),5,10),'/','-')
 WHEN d.UserID_Upd LIKE '[12][0-9][0-9][0-9]-[01][0-9]-[0-3][0-9]'
 AND ISDATE(d.UserID_Upd)=1 THEN d.UserID_Upd ELSE NULL END,120)"""


def parse_description(raw):
    raw = str(raw or '')
    match = re.match(r'^\s*HEN\s*[:=]\s*(\d{4}[-/]\d{2}[-/]\d{2})(?:\s*\|\s*|\s*)(.*)$', raw, re.I | re.S)
    if not match:
        return {'promise_date': None, 'note': raw, 'estimate': None}
    try:
        promise = dt.date.fromisoformat(match[1].replace('/', '-'))
    except ValueError:
        return {'promise_date': None, 'note': raw, 'estimate': None}
    note, estimate = match[2], None
    if '|' in note:
        head, _, tail = note.rpartition('|')
        try:
            value = Decimal(tail.strip().replace(',', ''))
            if value.is_finite() and value >= 0:
                note, estimate = head.strip(), value
        except InvalidOperation:
            pass
    return {'promise_date': promise, 'note': note.strip(), 'estimate': estimate}


def pack_description(promise, note, estimate=None):
    if not promise:
        return note
    return f'HEN:{promise.isoformat()} | {note} | {format(estimate or Decimal(0), "f")}'


def enrich(row, today=None, order=False):
    today = today or timezone.localdate()
    row.update(parse_description(row.get('Description')))
    if not row['promise_date']:
        try:
            row['promise_date'] = dt.date.fromisoformat(str(row.get('UserID_Upd') or ''))
        except ValueError:
            pass
    row['mobile_order'] = is_mobile_order(row)
    if row['mobile_order']:
        row['state_name'] = ORDER_STATES.get(row.get('Status'), row.get('state_name', 'Không rõ'))
    created = row.get('TrnDate')
    if isinstance(created, dt.datetime):
        created = created.date()
    row['age_days'] = max(0, (today - created).days) if isinstance(created, dt.date) else None
    promise = row['promise_date']
    remain = (promise - today).days if promise else None
    row['due_state'], row['due_text'] = 'none', 'Chưa hẹn ngày'
    if row.get('Status') in ('C', 'D'):
        row['due_state'], row['due_text'] = 'done', 'Đã kết thúc'
    elif remain is not None:
        if remain < 0:
            row['due_state'], row['due_text'] = 'overdue', f'Quá hẹn {abs(remain)} ngày'
        elif remain == 0:
            row['due_state'], row['due_text'] = 'today', 'Hẹn hôm nay'
        else:
            row['due_state'], row['due_text'] = ('soon' if remain <= 2 else 'ok'), f'Còn {remain} ngày'
    row['payment_known'] = Decimal(row.get('CashPay') or 0) + Decimal(row.get('CardPay') or 0) == Decimal(row.get('TienCoc') or 0)
    return row
