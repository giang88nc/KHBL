"""Invoice annotations and customer tender; source of truth stays in KK MSSQL."""
import datetime as dt
import hashlib
import json
from decimal import Decimal, InvalidOperation
from django.core import signing
from django.db import connection,transaction
from django.http import Http404
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods
from apps.pmv import gateway
from .models import MoneyFlow

SPECS={'gold_bill_retail':('TRN_RT_BUYSELL','PayAmount','Desc3'),
       'gold_bill_deposit':('TRN_DATCOC','TienCoc','Description')}
TYPES=['Đơn cưới','Đơn online']
SOCIALS=['Zalo','Facebook','TikTok','Khác']
RETAIL_POST_KEYS=('channel','social','customer_hold','is_wedding','wedding','note','tender')

def attach_change(rows):
    """Read tender metadata in batches; never treat missing/invalid data as zero."""
    for row in rows:
        row.customer_change=None
        row.customer_tender=None        # tiền khách đưa (TienKhachTraThuc) — job nền ghi lại vào money_flow.cus_cash
        row.customer_received_exact=False
    for kind,(table,total_field,field) in SPECS.items():
        batch=[r for r in rows if r.source_system=='KHBL' and r.source_type==kind]
        for offset in range(0,len(batch),200):
            part=batch[offset:offset+200]
            try:
                value='TienTraLai,TienKhachTraThuc' if kind=='gold_bill_retail' else field
                found=gateway.pmv_read(f"SELECT TrnID,BillCode,CashPay,CardPay,{total_field},{value} FROM {table} WHERE TrnID IN ("+','.join('?' for _ in part)+')',
                    tuple(r.source_id for r in part),target='kk',tag='mobile-invoice-change',audit=False)
                indexed={(str(r['TrnID']),str(r['BillCode']).strip()):r for r in found}
                for row in part:
                    source=indexed.get((row.source_id,row.source_bill_code))
                    if source is None:continue
                    try:
                        data=source if kind=='gold_bill_retail' else document(source.get(field))
                        raw=data.get('TienTraLai')
                        if raw is not None and str(raw).strip():row.customer_change=money(raw)
                        amounts=[data.get('TienKhachTraThuc'),source.get('CashPay')]
                        if row.customer_change is not None and all(v is not None and str(v).strip() for v in amounts):
                            tender,cash=map(money,amounts)
                            row.customer_tender=tender
                            received=tender-row.customer_change
                            # Tiền thực giữ lại = tiền khách đưa - tiền trả khách.
                            # Cả hai cột bằng 0 vẫn là chưa xác nhận, kể cả CashPay = 0.
                            row.customer_received_exact=(tender>0 and row.customer_change<=tender
                                                         and received==cash)
                    except (ValueError,TypeError):pass
            except Exception:
                import logging
                logging.getLogger(__name__).exception('Cannot read invoice change for mobile list')

def ghi_tien_khach(flow, tender, change):
    """GĐ chốt 23/09/2026: bấm "Xác nhận tiền mặt" là ghi LUÔN vào money_flow — cus_cash (tiền khách đưa) và
    cus_change (tiền thừa trả khách). Trước đây 2 số này chỉ nằm trong source_snapshot['tender'] (JSON) nên
    các bảng khác (CHECK GOLD, báo cáo) phải bóc JSON mới thấy."""
    MoneyFlow.objects.filter(pk=flow.pk).update(cus_cash=Decimal(str(tender or 0)), cus_change=Decimal(str(change or 0)))


def columns(kind):
    cols=['TrnID','BillCode','CustID','EmpID','Status','CashPay','CardPay',SPECS[kind][1],SPECS[kind][2]]
    if kind=='gold_bill_retail':cols+=['IsDel','TienKhachTraThuc','TienTraLai']
    return cols

def stamp(row):
    return hashlib.sha256(json.dumps(row,sort_keys=True,default=str,ensure_ascii=False).encode()).hexdigest()

def document(raw):
    if not raw or not raw.strip():return {}
    try:
        data=json.loads(raw)
        if not isinstance(data,dict):raise ValueError()
        return data
    except (ValueError,TypeError):
        raise ValueError('Ghi chú nguồn chưa phải JSON hợp lệ. Cần kiểm tra/chuyển đổi riêng; chưa ghi đè nội dung cũ.')

def money(value):
    try:n=Decimal(str(value or '0'))
    except InvalidOperation:raise ValueError('Số tiền không hợp lệ.')
    if not n.is_finite() or n<0 or n!=n.to_integral_value() or n>Decimal('999999999999999'):
        raise ValueError('Số tiền phải là đồng nguyên, không âm.')
    return n

def state(kind,row):
    if row.get('Status') in ('D','VOID','CANCELLED') or str(row.get('IsDel','0'))!='0':
        raise ValueError('Phiếu đã hủy hoặc xóa; không được sửa.')
    total=Decimal(str(row[SPECS[kind][1]] or 0));bank=Decimal(str(row['CardPay'] or 0))
    if not total.is_finite() or not bank.is_finite() or total<0 or bank<0 or bank>total:
        raise ValueError('Tổng tiền hoặc chuyển khoản nguồn cần được kiểm tra.')
    data=document(row[SPECS[kind][2]])
    tender=row.get('TienKhachTraThuc') if kind=='gold_bill_retail' else data.get('TienKhachTraThuc')
    change=row.get('TienTraLai') if kind=='gold_bill_retail' else data.get('TienTraLai')
    return dict(total=total,bank=bank,cash=total-bank,tender=Decimal(str(tender or 0)),
        change=Decimal(str(change or 0)),meta=data,types=data.get('type',[]),
        wedding=data.get('NgayCuoi') or '',note=data.get('note') or '',
        channel=data.get('channel') or ('online' if 'Đơn online' in (data.get('type') or []) else 'store'),
        social=data.get('social') or '',customer_hold=data.get('customer_hold') is True,
        wedding_selected=data.get('wedding') is True or 'Đơn cưới' in (data.get('type') or []))

def changes(kind,row,posted,*,bill_quantity=None):
    s=state(kind,row);data=dict(s['meta']);updates={}
    if s['cash']>0:
        tender=money(posted.get('tender','0'));change=max(Decimal(0),tender-s['cash'])
        if kind=='gold_bill_retail':updates.update(TienKhachTraThuc=tender,TienTraLai=change)
        else:data.update(TienKhachTraThuc=str(int(tender)),TienTraLai=str(int(change)))
    if kind=='gold_bill_retail':
        types=posted.getlist('types') if hasattr(posted,'getlist') else posted.get('types',[])
        if 'channel' in posted:
            channel=posted.get('channel');social=posted.get('social','') if channel=='online' else ''
            if channel not in ('store','online'):raise ValueError('Kênh giao dịch không hợp lệ.')
            if channel=='online' and social not in SOCIALS:raise ValueError('Chọn kênh social cho đơn Online.')
            selected=posted.get('is_wedding')=='1'
            types=(['Đơn online'] if channel=='online' else [])+(['Đơn cưới'] if selected else [])
            data.update(channel=channel,social=social or None,
                customer_hold=channel=='online' and posted.get('customer_hold')=='1',wedding=selected)
            if selected:
                if bill_quantity is None:raise ValueError('Chưa đọc được số lượng trên bill.')
                data.update(wedding_quantity=str(bill_quantity),wedding_bill_total=format(s['total'],'f'))
            else:
                data.pop('wedding_quantity',None);data.pop('wedding_bill_total',None)
        if not isinstance(types,list) or any(t not in TYPES for t in types):raise ValueError('Loại phiếu không hợp lệ.')
        wedding=posted.get('wedding','') if 'Đơn cưới' in types else ''
        if wedding:
            try:wedding=dt.date.fromisoformat(wedding).isoformat()
            except ValueError:raise ValueError('Ngày cưới không hợp lệ.')
        note=str(posted.get('note','')).strip()
        if len(note)>2000:raise ValueError('Ghi chú tối đa 2.000 ký tự.')
        data.update(type=list(dict.fromkeys(types)),NgayCuoi=wedding or None,note=note)
    if kind=='gold_bill_retail' or s['cash']>0:
        updates[SPECS[kind][2]]=json.dumps(data,ensure_ascii=False,separators=(',',':'))
    return updates

def load(flow):
    kind=flow.source_type;cols=columns(kind);table=SPECS[kind][0]
    rows=gateway.pmv_read(f"SELECT {','.join(cols)} FROM {table} WITH (NOLOCK) WHERE BillCode=?",
        (flow.source_bill_code,),target='kk',tag='mobile-invoice-read',audit=False)
    if len(rows)!=1 or str(rows[0]['TrnID'])!=flow.source_id:raise ValueError('Không xác định duy nhất phiếu nguồn.')
    return rows[0]


def load_retail(trn_id,bill_code,*,target='kk'):
    """Đọc đúng một hóa đơn bán; dùng chung cho popup desktop trước thanh toán."""
    cols=columns('gold_bill_retail')
    rows=gateway.pmv_read(f"SELECT {','.join(cols)} FROM TRN_RT_BUYSELL WITH (NOLOCK) WHERE BillCode=?",
        (bill_code,),target=target,tag='retail-invoice-read',audit=False)
    if len(rows)!=1 or str(rows[0]['TrnID'])!=str(trn_id):
        raise ValueError('Không xác định duy nhất phiếu bán đang mở.')
    return rows[0]


def retail_quantity(trn_id,*,target='kk'):
    rows=gateway.pmv_read('SELECT SUM(SL) quantity FROM TRN_RT_BUYSELL_SELL WITH (NOLOCK) WHERE TrnID=?',
        (trn_id,),target=target,tag='invoice-quantity',audit=False)
    return rows[0]['quantity'] if rows else None


def retail_post(posted):
    """Đưa QueryDict về dữ liệu session nhỏ, không giữ khóa lạ từ trình duyệt."""
    return {key:str(posted.get(key,'') or '') for key in RETAIL_POST_KEYS}


def staged_retail(row,posted,*,bill_quantity):
    """Kiểm tra bằng đúng thuật toán mobile và dựng bản xem trước chưa ghi PMV."""
    clean=retail_post(posted)
    return clean,{**row,**changes('gold_bill_retail',row,clean,bill_quantity=bill_quantity)}


def apply_staged_retail(g,*,target='kk'):
    """Sau khi vendor lưu nháp, ghi phân loại/tender vào bản PMV cuối cùng."""
    posted=g.get('invoice_info')
    if not posted:return False
    row=load_retail(g.get('trn_id'),g.get('bill_code'),target=target)
    gateway.pmv_invoice_update('gold_bill_retail',str(row['TrnID']),str(row['BillCode']).strip(),
        stamp(row),posted,target=target)
    return True


def pawn_load(flow, *, lock=False):
    suffix=' FOR UPDATE' if lock else ''
    with connection.cursor() as cur:
        cur.execute('SELECT p.id,p.amount,p.cashPay,p.cardPay,p.channel,p.reconciliation_state,'
                    'l.request_key,l.employee_id,n.phone,n.customer_snapshot '
                    'FROM khj_cd.cd_payments p JOIN khj_cd.cd_loan_logs l ON l.id=p.log_id '
                    'JOIN khj_cd.cd_loans n ON n.id=l.loan_id '
                    'WHERE p.log_id=%s AND l.loan_id=%s AND p.direction=%s ORDER BY p.id'+suffix,
                    [flow.source_id,flow.source_group_id,'IN'])
        names=[c[0] for c in cur.description];rows=[dict(zip(names,row)) for row in cur.fetchall()]
    if len(rows)!=1:raise ValueError('Phiên cầm đồ không có duy nhất một khoản thu.')
    row=rows[0]
    total=money(row['amount']);bank=money(row['cardPay']);cash=total-bank
    if row['cashPay'] is not None and money(row['cashPay'])!=cash:
        raise ValueError('Phân bổ tiền của phiên cầm đồ đang lệch.')
    return row,total,bank,cash


def pawn_save(flow, token, posted):
    with transaction.atomic():
        row,total,bank,cash=pawn_load(flow,lock=True)
        if token.get('stamp')!=stamp(row):raise ValueError('Phiên cầm đồ đã thay đổi. Đóng và mở lại.')
        tender=money(posted.get('tender','0'))
        if tender<cash:raise ValueError('Tiền khách đưa chưa đủ tiền mặt cần thu.')
        state='MATCHED' if bank==total else 'PARTIAL' if bank else 'RECORDED'
        channel='BANK' if cash==0 else 'CASH' if bank==0 else None
        with connection.cursor() as cur:
            cur.execute('UPDATE khj_cd.cd_payments SET cashPay=%s,cardPay=%s,channel=%s,reconciliation_state=%s WHERE id=%s',
                        [cash,bank,channel,state,row['id']])
            if cur.rowcount!=1:raise ValueError('Không cập nhật đúng một khoản thu cầm đồ.')
        locked=MoneyFlow.objects.select_for_update().get(pk=flow.pk)
        locked.cash_amount,locked.bank_amount=cash,bank
        locked.payment_status=MoneyFlow.CONFIRMED if bank==total else MoneyFlow.PARTIAL if bank else MoneyFlow.RECORDED
        locked.source_snapshot={**(locked.source_snapshot or {}),'tender':{
            'change':str(tender-cash),'exact':tender==cash,'at':timezone.now().isoformat()}}
        locked.cus_cash,locked.cus_change=tender,tender-cash
        locked.save(update_fields=['cash_amount','bank_amount','payment_status','source_snapshot','cus_cash','cus_change','synced_at'])

@require_http_methods(['GET','POST'])
def popup(request,pk):
    from .quyen import chan
    chan(request,'CHUYEN_KHOAN','can_edit' if request.method=='POST' else 'can_view')
    flow=get_object_or_404(MoneyFlow,pk=pk,direction='IN',is_void=False)
    pawn=flow.source_system=='KHCD' and flow.source_type=='cd_loan_log'
    if not pawn and (flow.source_system!='KHBL' or flow.source_type not in SPECS):raise Http404()
    ctx={'flow':flow,'retail':not pawn and flow.source_type=='gold_bill_retail','pawn':pawn,
         'invoice_post_url':request.path,
         'type_choices':TYPES,'social_choices':SOCIALS,'can_save':False}
    try:
        if pawn:
            row,total,bank,cash=pawn_load(flow)
            if request.method=='POST':
                token=signing.loads(request.POST.get('version',''),salt='mobile-invoice',max_age=1800)
                if token.get('flow')!=flow.pk or token.get('user')!=request.user.pk:raise ValueError('Phiên sửa không hợp lệ.')
                pawn_save(flow,token,request.POST)
                return render(request,'pos/_mobile_invoice_saved.html')
            raw_snapshot=row.get('customer_snapshot') or {}
            try:snapshot=raw_snapshot if isinstance(raw_snapshot,dict) else json.loads(raw_snapshot)
            except (TypeError,ValueError):snapshot={}
            tender=(flow.source_snapshot or {}).get('tender') or {}
            entered=cash+Decimal(str(tender.get('change') or 0)) if tender else cash
            ctx.update(total=total,bank=bank,cash=cash,tender=entered,change=entered-cash,can_save=True,
                version=signing.dumps({'flow':flow.pk,'user':request.user.pk,'stamp':stamp(row)},salt='mobile-invoice'),
                party={'CustName':snapshot.get('name') or flow.customer_name,'Phone':row.get('phone') or '',
                       'EmpName':row.get('employee_id') or '—'})
            return render(request,'pos/_mobile_invoice.html',ctx)
        row=load(flow)
        if request.method=='POST':
            token=signing.loads(request.POST.get('version',''),salt='mobile-invoice',max_age=1800)
            if token.get('flow')!=flow.pk or token.get('user')!=request.user.pk:raise ValueError('Phiên sửa không hợp lệ.')
            gateway.pmv_invoice_update(flow.source_type,flow.source_id,flow.source_bill_code,token['stamp'],request.POST)
            s=state(flow.source_type,row)       # tiền khách đưa / tiền thừa của chính lần xác nhận này
            tien_dua=money(request.POST.get('tender','0')) if s['cash']>0 else Decimal(0)
            ghi_tien_khach(flow,tien_dua,max(Decimal(0),tien_dua-s['cash']) if s['cash']>0 else Decimal(0))
            try:                    # 21/09/2026: danh sách mobile đọc tiền thối từ money_flow — cập nhật ngay, không chờ job 5 phút
                from .mobile_projection import enrich_from_kk
                enrich_from_kk(trn_ids=[flow.source_id])
            except Exception:
                import logging
                logging.getLogger(__name__).exception('Chưa cập nhật tiền thối vào money_flow: %s',flow.source_id)
            return render(request,'pos/_mobile_invoice_saved.html')
        ctx.update(state(flow.source_type,row),can_save=True,
            version=signing.dumps({'flow':flow.pk,'user':request.user.pk,'stamp':stamp(row)},salt='mobile-invoice'))
        names=gateway.pmv_read(
            'SELECT c.CustName,c.Phone,e.EmpName FROM I_CUSTOMER c WITH (NOLOCK) '
            'LEFT JOIN T_EMPLOYEE e WITH (NOLOCK) ON e.EmpID=? WHERE c.CustID=?',(row['EmpID'],row['CustID']),
            target='kk',tag='mobile-invoice-party',audit=False)
        ctx['party']=names[0] if names else {'CustName':flow.customer_name,'EmpName':row['EmpID']}
        if ctx['retail']:
            quantities=gateway.pmv_read('SELECT SUM(SL) quantity FROM TRN_RT_BUYSELL_SELL WHERE TrnID=?',
                (row['TrnID'],),target='kk',tag='invoice-quantity',audit=False)
            ctx['bill_quantity']=quantities[0]['quantity'] if quantities else None
    except signing.BadSignature:ctx['error']='Phiên chỉnh sửa hết hạn. Đóng và mở lại phiếu.'
    except ValueError as exc:ctx['error']=str(exc)
    except Exception:
        import logging
        logging.getLogger(__name__).exception('Mobile invoice source unavailable')
        ctx['error']='Chưa đọc/lưu được nguồn PMV. Đóng và mở lại phiếu để kiểm tra trước khi thử lại.'
    if ctx.get('error'):ctx['can_save']=False
    return render(request,'pos/_mobile_invoice.html',ctx)


@require_http_methods(['GET','POST'])
def retail_popup(request):
    """Popup 📝 trên màn bán: giữ dữ liệu qua lần vendor Upd rồi ghi lúc thanh toán."""
    from . import cart
    from .quyen import chan
    chan(request,'BAN_HANG','can_edit')
    g=cart.get(request);trn=str(g.get('trn_id') or '').strip();bill=str(g.get('bill_code') or '').strip()
    ctx={'flow':{'source_bill_code':bill,'customer_name':(g.get('cust') or {}).get('name','')},
         'retail':True,'pawn':False,'invoice_title':'Phân loại HĐ & tiền khách',
         'invoice_save_label':'LƯU THÔNG TIN PHIẾU',
         'invoice_post_url':request.path,'type_choices':TYPES,'social_choices':SOCIALS,'can_save':False}
    try:
        if not trn or not bill:raise ValueError('Phiếu chưa được lưu vào PMV. Thêm món hàng rồi thử lại.')
        row=load_retail(trn,bill);quantity=retail_quantity(trn)
        if request.method=='POST':
            token=signing.loads(request.POST.get('version',''),salt='retail-invoice',max_age=1800)
            if token.get('trn')!=trn or token.get('bill')!=bill or token.get('user')!=request.user.pk:
                raise ValueError('Phiên sửa không hợp lệ.')
            if token.get('stamp')!=stamp(row):
                raise ValueError('Phiếu vừa thay đổi. Đóng và mở lại để kiểm tra; chưa lưu thông tin.')
            clean,preview=staged_retail(row,request.POST,bill_quantity=quantity)
            g['invoice_info']=clean;cart.save(request,g)
            return render(request,'pos/_ban_invoice_saved.html')
        preview=row
        if g.get('invoice_info'):
            _,preview=staged_retail(row,g['invoice_info'],bill_quantity=quantity)
        ctx.update(state('gold_bill_retail',preview),can_save=True,bill_quantity=quantity,
            version=signing.dumps({'trn':trn,'bill':bill,'user':request.user.pk,'stamp':stamp(row)},salt='retail-invoice'))
        names=gateway.pmv_read(
            'SELECT c.CustName,c.Phone,e.EmpName FROM I_CUSTOMER c WITH (NOLOCK) '
            'LEFT JOIN T_EMPLOYEE e WITH (NOLOCK) ON e.EmpID=? WHERE c.CustID=?',(row['EmpID'],row['CustID']),
            target='kk',tag='retail-invoice-party',audit=False)
        ctx['party']=names[0] if names else {'CustName':ctx['flow']['customer_name'],'EmpName':row['EmpID']}
    except signing.BadSignature:ctx['error']='Phiên chỉnh sửa hết hạn. Đóng và mở lại phiếu.'
    except ValueError as exc:ctx['error']=str(exc)
    except Exception:
        import logging
        logging.getLogger(__name__).exception('Retail invoice source unavailable')
        ctx['error']='Chưa đọc được phiếu PMV. Đóng và mở lại để thử lại.'
    if ctx.get('error'):ctx['can_save']=False
    return render(request,'pos/_mobile_invoice.html',ctx)
