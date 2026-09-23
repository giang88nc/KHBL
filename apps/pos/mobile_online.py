"""Online invoices from KK; only confirmed pickup annotations are writable."""
import datetime as dt
import json
import logging
from decimal import Decimal
from urllib.parse import urlencode
from django.core import signing
from django.core.paginator import Paginator
from django.http import Http404
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods
from apps.pmv import gateway
from .mobile_invoice import document, stamp
from .quyen import chan
from .mobile_sources import month_bounds, periods, prefer_live

PICKUP_COLUMNS=['TrnID','BillCode','Desc3','Status','IsDel']

def dates(params,today=None):
    today=today or timezone.localdate();quick=params.get('quick','')
    first=today.replace(day=1)
    if quick=='today':return today,today
    if quick=='yesterday':return today-dt.timedelta(days=1),today-dt.timedelta(days=1)
    if quick=='month':return first,(first.replace(day=28)+dt.timedelta(days=4)).replace(day=1)-dt.timedelta(days=1)
    if quick=='previous':return (first-dt.timedelta(days=1)).replace(day=1),first-dt.timedelta(days=1)
    try:
        first,last=month_bounds(today)
        d1=dt.date.fromisoformat(params.get('d1') or str(first));d2=dt.date.fromisoformat(params.get('d2') or str(last))
    except ValueError:raise ValueError('Ngày lọc không hợp lệ.')
    if d1>d2 or d2==dt.date.max:raise ValueError('Khoảng ngày không hợp lệ.')
    return d1,d2

def pickup_document(row,actor,now):
    data=document(row['Desc3'])
    if row['Status'] in ('D','VOID','CANCELLED') or str(row['IsDel'])!='0':raise ValueError('Phiếu đã hủy/xóa.')
    if data.get('channel')!='online' or data.get('customer_hold') is not True:raise ValueError('Phiếu không còn ở trạng thái khách gửi.')
    history=data.get('customer_pickups',[])
    if not isinstance(history,list):raise ValueError('Lịch sử nhận hàng cần kiểm tra.')
    event={'time':now.isoformat(),'employee':actor,'confirmed':True}
    data.update(customer_hold=False,customer_pickup=event,customer_pickups=[*history,event])
    return json.dumps(data,ensure_ascii=False,separators=(',',':'))

def read(sql,args=(),*,target='kk'):
    return gateway.pmv_read(sql,args,target=target,tag='mobile-online',audit=False)

@require_GET
def index(request):
    chan(request,'CHUYEN_KHOAN')
    tab=request.GET.get('tab','online')
    if tab not in ('online','hold','wedding'):tab='online'
    q=request.GET.get('q','').strip()[:100]
    hold_state=request.GET.get('hold_state','hold')
    if hold_state not in ('all','hold','delivered'):hold_state='hold'
    ctx={'tab':tab,'q':q,'mobile_section':'online','quick_choices':[('today','Hôm nay'),('yesterday','Hôm qua'),('month','Tháng này'),('previous','Tháng trước')]}
    try:
        d1,d2=dates(request.GET);ctx.update(d1=d1.isoformat(),d2=d2.isoformat())
        ctx['hold_state']=hold_state
        sql=('SELECT TOP 20001 b.TrnID,b.BillCode,b.TrnDate,b.TrnTime,b.Status,b.IsDel,b.PayAmount,b.CashPay,b.CardPay,b.Desc3,'
             'c.CustName,c.Phone,e.EmpName FROM TRN_RT_BUYSELL b '
             'LEFT JOIN I_CUSTOMER c ON c.CustID=b.CustID LEFT JOIN T_EMPLOYEE e ON e.EmpID=b.EmpID')
        raw=[]
        for target,start,end in periods(d1,d2,unbounded=bool(q)):
            where=" WHERE (b.Desc3 LIKE '%\"channel\"%' OR b.Desc3 LIKE '%\"wedding\"%')";args=[]
            if start:
                where+=' AND b.TrnDate>=? AND b.TrnDate<?';args.extend([str(start),str(end+dt.timedelta(days=1))])
            if q:
                escaped=q.replace('[','[[]').replace('%','[%]').replace('_','[_]')
                where+=" AND (c.Phone LIKE ? OR e.EmpName LIKE ? OR b.BillCode LIKE ? OR REPLACE(b.BillCode,'-','') LIKE ?)"
                args.extend(['%'+escaped+'%']*3+['%'+escaped.replace('-','')+'%'])
            found=read(sql+where+' ORDER BY b.TrnDate DESC,b.TrnTime DESC,b.TrnID DESC',tuple(args),target=target)
            if len(found)>20000:raise ValueError('Phạm vi quá lớn. Thu hẹp từ khóa để xem đầy đủ.')
            raw.extend({**r,'_source':target} for r in found)
        raw=prefer_live(raw,sql,read)
        raw.sort(key=lambda r:(str(r['TrnDate']),str(r.get('TrnTime') or ''),str(r['TrnID'])),reverse=True)
        if len(raw)>20000:raise ValueError('Phạm vi quá lớn. Thu hẹp ngày hoặc từ khóa để thống kê đầy đủ.')
        rows=[];invalid=0
        for row in raw:
            if str(row.get('IsDel','0'))!='0' or row.get('Status') in ('D','VOID','CANCELLED'):continue
            try:data=document(row['Desc3'])
            except ValueError:invalid+=1;continue
            row['online']=data.get('channel')=='online'
            row['wedding']=data.get('wedding') is True
            if not row['online'] and not row['wedding']:continue
            row['hold']=row['online'] and data.get('customer_hold') is True
            row['social']=(data.get('social') or 'Online') if row['online'] else 'Tại tiệm'
            row['note']=data.get('note') or ''
            row['wedding_quantity']=data.get('wedding_quantity')
            row['wedding_bill_total']=data.get('wedding_bill_total')
            try:row['wedding_date']=dt.date.fromisoformat(data.get('NgayCuoi') or '')
            except (ValueError,TypeError):row['wedding_date']=None
            pickup=data.get('customer_pickup');row['pickup']=pickup if isinstance(pickup,dict) else None
            row['delivered']=row['online'] and not row['hold'] and bool(row['pickup'] and row['pickup'].get('confirmed') is True)
            day=row['TrnDate'].date() if isinstance(row['TrnDate'],dt.datetime) else row['TrnDate']
            row['hold_days']=max(0,(timezone.localdate()-day).days) if isinstance(day,dt.date) else None
            # Only a valid telephone destination; no automatic outbound notifications.
            phone=''.join(c for c in str(row.get('Phone') or '') if c.isdigit())
            row['phone_link']=phone if 9<=len(phone)<=15 else ''
            rows.append(row)
        ctx['hold_count']=sum(r['hold'] for r in rows);ctx['online_count']=sum(r['online'] for r in rows)
        ctx['wedding_count']=sum(r['wedding'] for r in rows)
        if tab=='hold':rows=[r for r in rows if (r['hold'] or r['delivered']) and (hold_state=='all' or r[hold_state])]
        else:rows=[r for r in rows if r[tab]]
        ctx.update(count=len(rows),total=sum((Decimal(r['PayAmount'] or 0) for r in rows),Decimal(0)),
            bank=sum((Decimal(r['CardPay'] or 0) for r in rows),Decimal(0)),
            cash=sum((Decimal(r['CashPay'] or 0) for r in rows),Decimal(0)),invalid=invalid)
        ctx['page']=Paginator(rows,30).get_page(request.GET.get('page'))
        ctx['query']=urlencode({'tab':tab,'d1':str(d1),'d2':str(d2),'q':q,'hold_state':hold_state})
    except Exception as exc:
        logging.getLogger(__name__).exception('Online list unavailable')
        ctx['error']=str(exc) if isinstance(exc,ValueError) else 'Chưa đọc được MSSQL. Vui lòng thử lại.'
    return render(request,'pos/_mobile_online.html' if request.headers.get('HX-Request') else 'pos/mobile_online.html',ctx)

@require_http_methods(['GET','POST'])
def pickup(request,pk):
    chan(request,'CHUYEN_KHOAN','can_edit');ctx={}
    try:
        target='kk'
        rows=read('SELECT '+','.join(PICKUP_COLUMNS)+' FROM TRN_RT_BUYSELL WHERE TrnID=?',(pk,))
        if not rows:
            target='hist'
            rows=read('SELECT '+','.join(PICKUP_COLUMNS)+' FROM TRN_RT_BUYSELL WHERE TrnID=?',(pk,),target='hist')
        if not rows:raise Http404()
        if len(rows)!=1:raise ValueError('Không xác định duy nhất hóa đơn.')
        row=rows[0];data=document(row['Desc3']);ctx['row']=row
        if request.method=='POST':
            if request.POST.get('confirm')!='1':raise ValueError('Cần xác nhận khách đã nhận đủ hàng của phiếu.')
            token=signing.loads(request.POST.get('version',''),salt='online-pickup',max_age=1800)
            if token.get('user')!=request.user.pk or token.get('pk')!=pk or token.get('target','kk')!=target:raise ValueError('Nguồn hoặc phiên xác nhận đã thay đổi. Mở lại phiếu.')
            employee=request.session.get('mobile_employee') or {}
            from apps.pmv.models import pmv_user_for_web_user
            mapping=pmv_user_for_web_user(request.user) if not employee else None
            actor={'user_id':request.user.pk,'username':request.user.username,
                'full_name':employee.get('full_name') or request.user.get_full_name() or request.user.username,
                'employee_id':employee.get('id'),'employee_pmv':employee.get('employee_pmv') or getattr(mapping,'emp_id','')}
            gateway.pmv_online_pickup(pk,token['stamp'],actor,target=target)
            return render(request,'pos/_mobile_online_saved.html')
        pickup_document(row,{},timezone.localtime()) # Validate eligibility without writing.
        ctx.update(allowed=True,version=signing.dumps({'user':request.user.pk,'pk':pk,'stamp':stamp(row),'target':target},salt='online-pickup'))
    except Http404:raise
    except signing.BadSignature:ctx['error']='Phiên xác nhận hết hạn. Đóng và mở lại.'
    except ValueError as exc:ctx['error']=str(exc)
    except Exception:
        logging.getLogger(__name__).exception('Online pickup failed')
        ctx['error']='Chưa xác định kết quả lưu. Đóng và làm mới danh sách trước khi thử lại.'
    return render(request,'pos/_mobile_online_pickup.html',ctx)
