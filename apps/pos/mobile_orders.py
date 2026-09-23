"""Read-only mobile orders. Business data comes exclusively from KK MSSQL."""
import datetime as dt
import logging
from decimal import Decimal
from urllib.parse import urlencode
from django.core.paginator import Paginator
from django.http import Http404
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_GET
from apps.pmv import gateway
from . import deposit_orders as O

HEAD = ('SELECT TOP 20001 d.TrnID,d.BillCode,d.TrnDate,d.TrnTime,d.Status,d.ShopID,'
        'd.Description,d.UserID_Upd,d.TienCoc,d.CashPay,d.CardPay,d.EmpID,d.CustID,'
        'c.CustName,c.Phone,e.EmpName FROM TRN_DATCOC d '
        'LEFT JOIN I_CUSTOMER c ON c.CustID=d.CustID LEFT JOIN T_EMPLOYEE e ON e.EmpID=d.EmpID ')
TABS=[('active','Đang xử lý'),('ready','Có hàng'),('delivered','Đã giao'),('all','Tất cả')]

def read(sql,params=()):
    return gateway.pmv_read(sql,params,target='kk',tag='mobile-orders-read',audit=False)

def authorize(request):
    from .quyen import chan
    # Existing Face ID mobile scope can view this read-only module, not desktop writes.
    if request.session.get('mobile_employee'):
        chan(request,'CHUYEN_KHOAN')
    else:chan(request,'DAT_COC')

def decorate(row):
    row=O.enrich(dict(row));status=row.get('Status')
    row['BillCode']=row.get('BillCode') or row['TrnID']
    mobile=O.is_mobile_order(row)
    row['state_label']=({'W':'Đang chờ','R':'Có hàng','C':'Đã giao','D':'Đã hủy','P':'Đã thanh toán'} if mobile else
        {'W':'Lưu tạm','R':'Có hàng','C':'Đã sử dụng cọc','D':'Đã hủy','P':'Đã thanh toán'}).get(status,'Chưa rõ trạng thái')
    row['delivered']=mobile and status=='C'
    row['active']=status not in ('C','D')
    row['overdue']=bool(row['active'] and row['promise_date'] and row['promise_date']<timezone.localdate())
    row['split_ok']=all(row.get(k) is not None for k in ('TienCoc','CashPay','CardPay')) and (
        Decimal(row['TienCoc'])==Decimal(row['CashPay'])+Decimal(row['CardPay']) and
        min(Decimal(row['CashPay']),Decimal(row['CardPay']))>=0)
    row['source_label']='appMobile' if mobile else 'PMV'
    return row

def item_view(item):
    result=O.item_info(item)
    result['ProductDesc']=result.get('ProductDesc') or result.get('Notes') or 'Chưa có tên món'
    if result.get('SL') is None:result['SL']='—'
    return result

@require_GET
def index(request):
    authorize(request)
    key=request.GET.get('q','').strip()[:100];tab=request.GET.get('tab','active')
    if tab not in dict(TABS):tab='active'
    mine=request.GET.get('mine')=='1';due=request.GET.get('due','')
    ctx={'q':key,'tab':tab,'mine':mine,'due':due,'tabs':TABS,'mobile_section':'orders'}
    try:
        where=[];params=[]
        if key:
            literal=key.replace('[','[[]').replace('%','[%]').replace('_','[_]')
            where.append('(d.BillCode LIKE ? OR REPLACE(d.BillCode,\'-\',\'\') LIKE ? OR c.CustName LIKE ? OR c.Phone LIKE ?)')
            params.extend(['%'+literal+'%','%'+literal.replace('-','')+'%','%'+literal+'%','%'+literal+'%'])
        if mine:
            from apps.pmv.models import pmv_user_for_web_user
            employee=request.session.get('mobile_employee')
            mapping=pmv_user_for_web_user(request.user) if not employee else None
            emp=str(employee.get('employee_pmv') or '').strip() if employee else str(getattr(mapping,'emp_id','') or '').strip()
            where.append('d.EmpID=?');params.append(emp or '__NO_EMPLOYEE__')
        records=read(HEAD+('WHERE '+' AND '.join(where) if where else '')+' ORDER BY d.TrnDate DESC,d.TrnID DESC',tuple(params))
        if len(records)>20000:raise ValueError('Phạm vi quá lớn. Nhập mã phiếu, tên hoặc SĐT để thu hẹp.')
        rows=[decorate(r) for r in records]
        rows=[r for r in rows if tab=='all' or (tab=='active' and r['active']) or (tab=='ready' and r['Status']=='R') or (tab=='delivered' and r['delivered'])]
        ctx['overdue_count']=sum(r['overdue'] for r in rows)
        ctx['today_count']=sum(r['active'] and r['promise_date']==timezone.localdate() for r in rows)
        if due=='overdue':rows=[r for r in rows if r['overdue']]
        elif due=='today':rows=[r for r in rows if r['active'] and r['promise_date']==timezone.localdate()]
        if tab in ('active','ready'):rows.sort(key=lambda r:(not r['overdue'],r['promise_date'] or dt.date.max))
        page=Paginator(rows,20).get_page(request.GET.get('page'))
        ids=[r['TrnID'] for r in page]
        items=read('SELECT TrnID,ProductDesc,Notes,SL FROM TRN_DATCOC_DT WHERE TrnID IN ('+','.join('?' for _ in ids)+') ORDER BY TrnID,TrnDTID',tuple(ids)) if ids else []
        grouped={pk:[] for pk in ids}
        for item in items:grouped[item['TrnID']].append(item_view(item))
        for row in page:
            lines=grouped[row['TrnID']];row['item_count']=len(lines)
            row['item_summary']=' · '.join(i.get('ProductDesc') or 'Món đặt' for i in lines[:2])
        ctx.update(page=page,total=len(rows),as_of=timezone.localtime(),query=urlencode({'q':key,'tab':tab,'mine':int(mine),'due':due}))
    except Exception as exc:
        logging.getLogger(__name__).exception('Mobile order list unavailable')
        ctx['error']=str(exc) if isinstance(exc,ValueError) else 'Chưa kết nối được MSSQL. Vui lòng thử lại.'
    return render(request,'pos/_mobile_orders.html' if request.headers.get('HX-Request') else 'pos/mobile_orders.html',ctx)

@require_GET
def detail(request,pk):
    authorize(request)
    ctx={}
    try:
        rows=read(HEAD+'WHERE d.TrnID=?',(pk,))
        if not rows:raise Http404('Không tìm thấy phiếu')
        if len(rows)!=1:raise ValueError('Mã phiếu không duy nhất.')
        row=decorate(rows[0])
        items=read('SELECT TrnDTID,ProductDesc,GoldCode,SL,Size,Notes FROM TRN_DATCOC_DT WHERE TrnID=? ORDER BY TrnDTID',(pk,))
        links=read("SELECT b.BillCode,b.Status,b.TrnID FROM TRN_RT_BUYSELL_DatCoc l JOIN TRN_RT_BUYSELL b ON b.TrnID=l.TrnID WHERE l.DatCocID=? AND b.IsDel='0'",(pk,))
        changes=read('SELECT TrnID FROM TRN_RT_CHANGE_DatCoc WHERE DatCocID=?',(pk,))
        ctx.update(row=row,items=[item_view(i) for i in items],links=links,changes=changes,notes=O.note_entries(row.get('Description')))
    except Http404:raise
    except Exception:
        logging.getLogger(__name__).exception('Mobile order detail unavailable')
        ctx={'error':'Chưa đọc được chi tiết từ MSSQL. Đóng và thử lại.'}
    return render(request,'pos/_mobile_order_detail.html',ctx)
