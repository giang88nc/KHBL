"""Quy ước hàng đặt đã đối chiếu với BANLE_V5 /stock/_order.html."""
import datetime as dt
import re
import json
from decimal import Decimal, InvalidOperation

from django.utils import timezone

ORDER_STATES = {'W': 'Đang chờ', 'R': 'Hàng sẵn sàng', 'C': 'Đã giao khách', 'D': 'Đã hủy', 'P': 'Đã thanh toán'}
TABS = [('all', 'DANH SÁCH'), ('new', 'Phiếu mới'), ('ordering', 'Đang đặt'), ('ready', 'Hàng sẵn sàng'), ('notifications','THÔNG BÁO')]
TAB_STATUS = {'all': '', 'ready': 'R', 'delivered': 'C', 'pending': 'X'}


def split_stock_note(note):
    raw = str(note or '')
    match = re.match(r'^\s*SP\s*:\s*([^|]+)(?:\|\s*)?(.*)$', raw, re.I | re.S)
    return (match[1].strip(), match[2].strip()) if match else ('', raw)


def item_info(row):
    raw = str(row.get('ProductDesc') or '')
    try:
        value = json.loads(raw)
    except (ValueError, TypeError):
        value = None
    if isinstance(value, dict) and len(value) == 1:
        code, name = next(iter(value.items()))
        if isinstance(code, str) and code.strip() and isinstance(name, str):
            code = code.strip()
            return {**row, 'ProductDesc': name, 'ProductCode': code,
                    'Mode': 'new' if code == 'Khách đặt' else 'stock',
                    'Notes': row.get('Notes') or ''}
    if row.get('Mode') in ('stock', 'new') and 'ProductCode' in row:
        return dict(row)
    code, note = split_stock_note(row.get('Notes'))
    mode='stock' if code else 'new'
    ordered=re.match(r'^MA_DAT:([^|]+)\s*\|\s*(.*)$',note,re.S) if not code else None
    if ordered: code,note=ordered[1].strip(),ordered[2]
    return {**row, 'Mode': mode, 'ProductCode': code, 'Notes': note}


def product_description(row):
    """JSON một cặp mã:tên theo giới hạn NVARCHAR(500) của PMV."""
    item = item_info(row)
    code = item.get('ProductCode') if item.get('Mode') == 'stock' else 'Khách đặt'
    if not code:
        raise ValueError('Hàng sẵn phải có mã sản phẩm trước khi lưu.')
    raw = json.dumps({code: item.get('ProductDesc') or ''}, ensure_ascii=False, separators=(',', ':'))
    if len(raw.encode('utf-16-le')) // 2 > 500:
        raise ValueError('Mã và tên sản phẩm vượt giới hạn 500 ký tự JSON của PMV. Rút gọn tên món trước khi lưu.')
    return raw


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


def _parse_legacy(raw):
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


def validate_notes(entries):
    if not isinstance(entries,list) or len(entries)>50: raise ValueError('Danh sách ghi chú không hợp lệ.')
    result=[]
    for entry in entries:
        if not isinstance(entry,dict) or not isinstance(entry.get('text'),str): raise ValueError('Dòng ghi chú không hợp lệ.')
        date=entry.get('date') or None
        if date is not None:
            try: date=dt.date.fromisoformat(date).isoformat()
            except (ValueError,TypeError): raise ValueError('Ngày ghi chú không hợp lệ.')
        text=entry['text'].strip()
        if not text or len(text)>500: raise ValueError('Mỗi ghi chú cần nội dung, tối đa 500 ký tự.')
        result.append({'date':date,'text':text})
    return result


def _json_description(raw):
    try:
        data=json.loads(str(raw or ''))
        if not isinstance(data,dict): return None
        if 'notes' not in data:
            if 'zalo' in data:
                data={**data,'notes':[{'date':k,'text':v} for k,v in data.items() if re.fullmatch(r'\d{4}-\d{2}-\d{2}',k) and isinstance(v,str)]}
        if 'notes' not in data:
            # Đọc cả object ngày:text do công cụ khác lưu.
            if not data or not all(re.fullmatch(r'\d{4}-\d{2}-\d{2}',k) and isinstance(v,str) for k,v in data.items()): return None
            data={'notes':[{'date':k,'text':v} for k,v in data.items()]}
        notes=validate_notes(data['notes'])
        promise=dt.date.fromisoformat(data['promise']) if data.get('promise') else None
        estimate=Decimal(str(data['estimate'])) if data.get('estimate') is not None else None
        if estimate is not None and (not estimate.is_finite() or estimate<0): return None
        return {'notes':notes,'promise':promise,'estimate':estimate}
    except (ValueError,TypeError,InvalidOperation): return None


def note_entries(raw):
    parsed=_json_description(raw)
    if parsed is not None: return parsed['notes']
    old=_parse_legacy(raw)['note']
    return [{'date':None,'text':old}] if old.strip() else []


def parse_description(raw):
    parsed=_json_description(raw)
    if parsed is None: return _parse_legacy(raw)
    note='\n'.join((dt.date.fromisoformat(n['date']).strftime('%d/%m/%Y')+': ' if n['date'] else '')+n['text'] for n in parsed['notes'])
    return {'promise_date':parsed['promise'],'note':note,'estimate':parsed['estimate']}


def description_length(value):
    return len(value.encode('utf-16-le'))//2


def pack_description(promise, note, estimate=None):
    entries=validate_notes(note) if isinstance(note,list) else ([{'date':None,'text':str(note).strip()}] if str(note or '').strip() else [])
    data={'notes':entries}
    if promise: data['promise']=promise.isoformat()
    if estimate is not None: data['estimate']=format(Decimal(estimate),'f')
    return json.dumps(data,ensure_ascii=False,separators=(',',':'))


def merge_zalo(description, zalo):
    """Giữ nguyên JSON/ghi chú cũ; chỉ thay mốc nhắc Zalo gọn."""
    try: data=json.loads(description or '{}')
    except (ValueError,TypeError): data=None
    if not isinstance(data,dict):
        legacy=parse_description(description)
        data=json.loads(pack_description(legacy['promise_date'],note_entries(description),legacy['estimate']))
    data['zalo']=zalo
    result=json.dumps(data,ensure_ascii=False,separators=(',',':'))
    if description_length(result)>500: raise ValueError('Description đã đầy; mốc Zalo được giữ trong lịch sử gửi MySQL.')
    return result


def enrich(row, today=None, order=False):
    today = today or timezone.localdate()
    row.update(parse_description(row.get('Description')))
    if not row['promise_date'] and _json_description(row.get('Description')) is None:
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
