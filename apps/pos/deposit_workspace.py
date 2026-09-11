"""Danh sách công việc đặt hàng; tổng hợp từ bản đọc có thời điểm, không suy ra số dư tiền."""
import copy
import datetime as dt
from collections import Counter, defaultdict
from decimal import Decimal

from django.core.cache import cache
from django.core.paginator import Paginator
from django.utils import timezone

from . import deposit_orders as O

QUICK = [('', 'Tất cả công việc'), ('overdue', 'Quá hẹn'), ('today', 'Hẹn hôm nay'),
         ('call', 'Cần liên hệ'), ('callback', 'Đến lịch gọi lại'), ('money', 'Cọc cần đối soát'),
         ('unassigned', 'Chưa phân công'), ('cancelled', 'Đã hủy'), ('conflict', 'Hàng cần kiểm tra')]
SOURCES = [('', 'Mọi nguồn hàng'), ('stock', 'Có sẵn'), ('new', 'Đặt mới')]
FULFILMENT = {'waiting': 'Đang chuẩn bị', 'ready': 'Hàng sẵn sàng', 'partial': 'Giao một phần',
              'delivered': 'Đã giao', 'cancelled': 'Đã hủy', 'unknown': 'Cần xác nhận tiến độ'}


def invalidate(target):
    cache.delete('datcoc:workspace:v2:' + target)


def read_snapshot(c, refresh=False):
    key = 'datcoc:workspace:v2:' + c.target
    result = None if refresh else cache.get(key)
    if result is not None:
        return copy.deepcopy(result)
    headers = c.query(
        'SELECT TOP 20001 d.TrnID,d.BillCode,d.TrnDate,d.TrnTime,d.TrnDateTime_Upd,d.TienCoc,d.Status, '
        'd.Description,d.ShopID,d.UserID_Upd,d.CashPay,d.CardPay,d.EmpID,d.CustID,e.EmpName,k.CustName,k.Phone '
        'FROM TRN_DATCOC d WITH (NOLOCK) LEFT JOIN I_CUSTOMER k WITH (NOLOCK) ON k.CustID=d.CustID '
        'LEFT JOIN T_EMPLOYEE e WITH (NOLOCK) ON e.EmpID=d.EmpID ORDER BY d.TrnDate DESC,d.TrnID DESC')
    if len(headers) > 20000:
        raise ValueError('Dữ liệu vượt phạm vi tổng hợp trực tiếp; cần đồng bộ kho báo cáo trước khi tiếp tục.')
    items = c.query('SELECT TOP 100001 TrnID,TrnDTID,ProductDesc,GoldCode,SL,Size,Notes '
                    'FROM TRN_DATCOC_DT WITH (NOLOCK) ORDER BY TrnID,TrnDTID')
    if len(items) > 100000:
        raise ValueError('Chi tiết vượt phạm vi tổng hợp. Không hiển thị báo cáo thiếu dữ liệu.')
    grouped = defaultdict(list)
    for item in items:
        grouped[item['TrnID']].append(O.item_info(item))
    funds=c.query("SELECT t.TrnRefID,t.Status,t.TillID,t.TrnTotalAmount,COUNT(x.TillTxnID) detail_count,COUNT(x.Amount) amount_count, "
        "SUM(x.Amount) cash_amount,MAX(CASE WHEN x.GoldCcy<>'VND' OR x.CrDr<>'+' THEN 1 ELSE 0 END) bad_detail "
        "FROM T_TILL_TXN t WITH (NOLOCK) JOIN TRN_DATCOC d WITH (NOLOCK) ON d.TrnID=t.TrnRefID "
        "LEFT JOIN T_TILL_TXN_DETAIL x WITH (NOLOCK) ON x.TillTxnID=t.TillTxnID "
        "GROUP BY t.TillTxnID,t.TrnRefID,t.Status,t.TillID,t.TrnTotalAmount")
    links=c.query("SELECT d.DatCocID,b.TrnID,b.CustID,b.TienCoc,b.Status,'sell' kind FROM TRN_RT_BUYSELL_DatCoc d WITH (NOLOCK) "
        "JOIN TRN_RT_BUYSELL b WITH (NOLOCK) ON b.TrnID=d.TrnID WHERE b.IsDel='0' "
        "UNION ALL SELECT DatCocID,TrnID,NULL,NULL,NULL,'change' FROM TRN_RT_CHANGE_DatCoc WITH (NOLOCK)")
    result = {'headers': headers, 'items': dict(grouped), 'funds':funds,'links':links,'as_of': timezone.now()}
    cache.set(key, result, 30)
    return copy.deepcopy(result)


def prepare(snapshot, today=None, now=None):
    today, now = today or timezone.localdate(), now or timezone.now()
    rows = []
    for original in snapshot['headers']:
        row = O.enrich(dict(original), today=today)
        items = snapshot['items'].get(row['TrnID'], [])
        state = ({'W': 'waiting', 'R': 'ready', 'C': 'delivered', 'D': 'cancelled'}.get(row['Status'], 'unknown')
                 if row['mobile_order'] else ('waiting' if row['Status'] == 'W' else 'unknown'))
        row.update(items=items, fulfilment=state, state_name=FULFILMENT[state],
                   source_status=row['Status'], original_promise=row['promise_date'],
                   line_count=len(items), stock_count=sum(i['Mode'] == 'stock' for i in items),
                   quantity=sum(i.get('SL') or 1 for i in items),
                   item_summary=' · '.join(i.get('ProductDesc') or i.get('Notes') or i.get('ProductCode') or 'Món đặt' for i in items[:2]),
                   item_extra=max(0, len(items)-2), delivered_at=None, ready_at=None,
                   next_contact=None, last_contact=None, contacted=None, quote_state='estimate',
                   money_confirmed=False, money_balance=None, money_name='Chưa đối soát',
                   hold_conflict=False, work_note='', responsible=row.get('EmpName') or 'Chưa phân công')
        row['new_count'] = row['line_count'] - row['stock_count']
        row['source_label'] = 'appMobile' if row['mobile_order'] else 'PMV'
        row['estimate'] = row['estimate'] or None
        tx=[t for t in snapshot.get('funds',[]) if t['TrnRefID']==row['TrnID']]
        links=[l for l in snapshot.get('links',[]) if l['DatCocID']==row['TrnID']]
        cash,bank,total=(Decimal(row.get(k) or 0) for k in ('CashPay','CardPay','TienCoc'))
        valid=(len(tx)==1 and tx[0]['Status']=='P' and tx[0]['TillID'] and tx[0]['TrnTotalAmount']==cash and
               tx[0]['detail_count']==tx[0]['amount_count']==1 and tx[0]['cash_amount']==cash and not tx[0]['bad_detail'] and
               cash>=0 and bank>=0 and cash+bank==total)
        if valid and not links:
            row.update(money_confirmed=True,money_balance=total,money_name='Đã thu · còn giữ')
        elif valid and len(links)==1 and links[0]['kind']=='sell' and links[0]['Status']=='C' and links[0]['TienCoc']==total and links[0]['CustID']==row['CustID']:
            row.update(money_confirmed=True,money_balance=Decimal(0),money_name='Đã cấn hóa đơn')
        elif links: row['money_name']='Cọc liên kết · cần kiểm tra hóa đơn'
        rows.append(row)
    return rows


def classify(rows, today=None, now=None):
    today, now = today or timezone.localdate(), now or timezone.now()
    codes = defaultdict(set)
    for row in rows:
        row['active'] = row['fulfilment'] not in ('delivered', 'cancelled')
        if row['active']:
            for item in row['items']:
                if item['ProductCode']:
                    codes[item['ProductCode'].casefold()].add(row['TrnID'])
    for row in rows:
        promise = row['promise_date']
        row['overdue'] = bool(row['active'] and promise and promise < today)
        row['today_due'] = bool(row['active'] and promise == today)
        row['callback_due'] = bool(row['active'] and row['next_contact'] and row['next_contact'] <= now)
        row['needs_call'] = row['active'] and row['fulfilment'] == 'ready' and not row['contacted']
        row['needs_money'] = not row['money_confirmed']
        row['hold_conflict'] = row['hold_conflict'] or (row['active'] and any(
            len(codes[i['ProductCode'].casefold()]) > 1 for i in row['items'] if i['ProductCode']))
        if row['overdue']:
            row['due_text'] = f'Quá hẹn {(today-promise).days} ngày'
        elif row['today_due']:
            row['due_text'] = 'Hẹn hôm nay'
        else:
            row['due_text'] = ''
        if row['callback_due']:
            task = 'Đến lịch gọi lại'
        elif row['needs_call']:
            task = 'Xác nhận đã báo hàng cho khách' if row['contacted'] is None else 'Thông báo khách: hàng đã sẵn sàng'
        elif row['overdue']:
            task = 'Xác nhận lịch lấy hàng' if row['fulfilment'] == 'ready' else 'Kiểm tra tiến độ, báo khách'
        elif row['next_contact'] and row['active']:
            task = 'Gọi lại ' + timezone.localtime(row['next_contact']).strftime('%d/%m %H:%M')
        elif row['today_due']:
            task = 'Kiểm tra đủ hàng trước giờ hẹn'
        elif row['active'] and not row.get('EmpID'):
            task = 'Phân công người theo dõi'
        elif row['fulfilment'] == 'cancelled' and row['needs_money']:
            task = 'Kiểm tra xử lý cọc khi hủy'
        else:
            task = row['work_note'] or ('Theo dõi ngày hẹn' if row['active'] else 'Tra cứu lịch sử')
        row['next_task'] = task
        row['state_css'] = {'waiting': 'W', 'ready': 'R', 'delivered': 'C', 'cancelled': 'D',
                            'partial': 'P', 'unknown': 'unknown'}[row['fulfilment']]
    return rows


def matches(row, data, emp_id=''):
    tab = data.get('tab', 'all')
    if tab == 'ready' and row['fulfilment'] != 'ready': return False
    if tab == 'delivered' and row['fulfilment'] != 'delivered': return False
    if tab == 'pending' and not row['active']: return False
    created = row.get('TrnDate')
    if isinstance(created, dt.datetime): created = created.date()
    if data.get('start') and (not created or created < data['start']): return False
    if data.get('end') and (not created or created > data['end']): return False
    if data.get('mine') and (not emp_id or row.get('EmpID') != emp_id): return False
    if data.get('source') == 'stock' and not row['stock_count']: return False
    if data.get('source') == 'new' and not row['new_count']: return False
    q = (data.get('q') or '').strip().casefold()
    if q and q not in ' '.join(str(row.get(k) or '') for k in
                               ('TrnID','BillCode','CustName','Phone','responsible','item_summary')).casefold(): return False
    quick = data.get('quick')
    flags = {'overdue': row['overdue'], 'today': row['today_due'], 'call': row['needs_call'] or row['callback_due'],
             'callback': row['callback_due'], 'money': row['needs_money'],
             'unassigned': row['active'] and not row.get('EmpID'),
             'cancelled': row['fulfilment'] == 'cancelled', 'conflict': row['hold_conflict']}
    if quick and not flags.get(quick): return False
    due, promise, today = data.get('due'), row['promise_date'], timezone.localdate()
    if due and not row['active']: return False
    if due == 'overdue' and not row['overdue']: return False
    if due == 'today' and not row['today_due']: return False
    if due == 'none' and promise is not None: return False
    if due == 'soon' and not (promise and today < promise <= today + dt.timedelta(days=2)): return False
    return True


def sorted_rows(rows, sort='newest', tab='all'):
    # Stable second ordering: most recent order first.
    rows = sorted(rows, key=lambda r: (str(r.get('TrnDate') or ''), r['TrnID']), reverse=True)
    far = dt.date.max
    if sort == 'newest': return rows
    if sort == 'delivered':
        return sorted(rows, key=lambda r: (bool(r['delivered_at']), str(r['delivered_at'] or '')), reverse=True)
    if sort == 'due': return sorted(rows, key=lambda r: r['promise_date'] or far)
    if tab == 'ready':
        return sorted(rows, key=lambda r: (0 if r['callback_due'] else 1 if r['needs_call'] else 2,
                                           r['promise_date'] or far))
    return sorted(rows, key=lambda r: (0 if r['overdue'] else 1 if r['callback_due'] else
                                       2 if r['today_due'] else 3 if r['needs_call'] else 4,
                                       r['promise_date'] or far))


def summarize(rows):
    return {'total': len(rows), 'amount': sum((Decimal(r.get('TienCoc') or 0) for r in rows), Decimal(0)),
            'pending': sum(r['active'] for r in rows), 'overdue': sum(r['overdue'] for r in rows),
            'call': sum(r['needs_call'] or r['callback_due'] for r in rows),
            'money': sum(r['needs_money'] for r in rows), 'conflict': sum(r['hold_conflict'] for r in rows),
            'confirmed_balance':sum((r['money_balance'] for r in rows if r['money_confirmed']),Decimal(0))}


def paginate(rows, data, page=1, emp_id=''):
    # KPI base respects search/date/source/owner; tab and quick chips do not alter their scope.
    scope = {**data, 'tab': 'all', 'quick': ''}
    base = [r for r in rows if matches(r, scope, emp_id)]
    selected = sorted_rows([r for r in base if matches(r, data, emp_id)], data.get('sort') or 'newest', data.get('tab'))
    pager = Paginator(selected, 30).get_page(page)
    return {'rows': list(pager), 'stats': summarize(selected), 'kpis': summarize(base), 'pager': pager,
            'tab_counts': Counter(r['fulfilment'] for r in base), 'filtered_rows': selected}


def operations_report(rows):
    bands = [{'label': label, 'count': 0, 'amount': Decimal(0)} for label in ('0–7 ngày','8–30 ngày','31–90 ngày','Trên 90 ngày')]
    staff = defaultdict(lambda: {'active': 0, 'overdue': 0, 'call': 0})
    for r in rows:
        if not r['active']: continue
        age = r['age_days'] or 0
        b = bands[0 if age <= 7 else 1 if age <= 30 else 2 if age <= 90 else 3]
        b['count'] += 1; b['amount'] += Decimal(r.get('TienCoc') or 0)
        s = staff[r['responsible']]
        s['active'] += 1; s['overdue'] += r['overdue']; s['call'] += r['needs_call'] or r['callback_due']
    delivered = [r for r in rows if r['fulfilment'] == 'delivered']
    measurable = [r for r in delivered if r['delivered_at'] and r['original_promise']]
    on_time = sum(timezone.localtime(r['delivered_at']).date() <= r['original_promise'] for r in measurable)
    return {'bands': bands, 'staff': [{'name': name, **s} for name,s in sorted(staff.items())],
            'delivered_count':len(delivered), 'measured_count':len(measurable), 'on_time_count':on_time,
            'on_time_rate': round(on_time * 100 / len(measurable), 1) if measurable else None,
            'unknown_delivery':len(delivered)-len(measurable),
            'tasks': [r for r in sorted_rows(rows, 'priority') if r['active'] and
                      (r['overdue'] or r['today_due'] or r['needs_call'] or r['callback_due'])]}
