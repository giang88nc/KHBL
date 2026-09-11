"""Đối soát và thực hiện thu/hoàn cọc qua proc PMV, không phát lại bước có kết quả không rõ."""
import datetime as dt
import hashlib
import json
import re
import uuid
from decimal import Decimal

from django import forms
from django.core import signing
from django.db import IntegrityError, connection, transaction
from django.http import Http404
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from apps.pmv.client import PmvClient
from apps.pmv.models import pmv_user_for_web_user
from . import deposits as D, deposit_workspace as W
from .deposit_models import DepositMoneyOperation as Operation, DepositOrderState, DepositEvent

STATES={'queued':'Chờ đối soát ngân hàng','running':'Đang ghi PMV','done':'Đã xác minh',
        'blocked':'Cần kiểm tra','uncertain':'Chưa rõ kết quả · không ghi lại','cancelled':'Đã hủy yêu cầu'}


def safe_data(value):
    return json.loads(json.dumps(value,default=str))


def financial(c, pk):
    h=D.header(c,pk)
    if not h: raise ValueError('Phiếu không còn tồn tại.')
    tx=c.query('SELECT TillTxnID,TillID,Status,TrnTotalAmount FROM T_TILL_TXN WITH (NOLOCK) WHERE TrnRefID=?',(pk,))
    links=c.query('SELECT d.TrnID,b.Status,b.TienCoc,b.CustID FROM TRN_RT_BUYSELL_DatCoc d WITH (NOLOCK) '
                  'JOIN TRN_RT_BUYSELL b WITH (NOLOCK) ON b.TrnID=d.TrnID WHERE d.DatCocID=? AND b.IsDel=\'0\'',(pk,))
    changes=c.query('SELECT TrnID FROM TRN_RT_CHANGE_DatCoc WITH (NOLOCK) WHERE DatCocID=?',(pk,))
    amount=Decimal(h.get('TienCoc') or 0); cash=Decimal(h.get('CashPay') or 0); bank=Decimal(h.get('CardPay') or 0)
    posted=len(tx)==1 and tx[0]['Status']=='P' and bool(tx[0]['TillID']) and tx[0]['TrnTotalAmount'] is not None
    valid=posted and cash>=0 and bank>=0 and cash+bank==amount and Decimal(tx[0]['TrnTotalAmount'])==cash
    if valid:
        detail=c.query('SELECT Amount,CrDr,GoldCcy FROM T_TILL_TXN_DETAIL WITH (NOLOCK) WHERE TillTxnID=?',(tx[0]['TillTxnID'],))
        valid=len(detail)==1 and detail[0]['Amount'] is not None and detail[0]['Amount']==cash and detail[0]['CrDr']=='+' and detail[0]['GoldCcy']=='VND'
    applied=valid and len(links)==1 and not changes and links[0]['Status']=='C' and links[0]['TienCoc']==amount and links[0]['CustID']==h['CustID']
    confirmed=valid and not changes and (not links or applied)
    refunded=False
    if not tx and not links and not changes and h['Status']=='W' and cash==bank==0:
        latest=Operation.objects.filter(target=c.target,trn_id=pk,status='done').order_by('-id').first()
        refunded=bool(latest and latest.kind=='refund' and money_stamp(c,latest.evidence.get('after',{}).get('h',{}))==money_stamp(c,h))
        confirmed=confirmed or refunded
    return {'h':h,'tx':tx,'links':links,'changes':changes,'amount':amount,'cash':cash,'bank':bank,
            'valid':valid,'confirmed':confirmed,'applied':applied,'refunded':refunded,'balance':Decimal(0) if applied or refunded else amount if confirmed else None}


def money_stamp(c,h):
    # Complete của vendor không luôn đổi TrnDateTime_Upd: ký thêm trạng thái/số tiền.
    if not h.get('TrnID'): return {}
    return {**D.version(c,h),**{k:str(h.get(k) or '') for k in ('Status','CustID')},
            **{k:format(Decimal(h.get(k) or 0).normalize(),'f') for k in ('TienCoc','CashPay','CardPay')}}


def validate_operation(op, f):
    if op.amount<=0 or op.amount!=f['amount'] or op.cash+op.bank!=op.amount:
        raise ValueError('Tiền thao tác phải bằng tiền cọc trên phiếu, TM + CK phải đủ tổng.')
    if f['links'] or f['changes']: raise ValueError('Cọc đang liên kết hóa đơn; kiểm tra hóa đơn trước khi thu/hoàn.')
    if op.kind=='receive':
        if D.O.is_mobile_order(f['h']): raise ValueError('Phiếu appMobile cũ chưa có chứng từ thu quỹ; cần xác minh nguồn tiền trước khi thu lại.')
        if f['h']['Status']!='W' or f['tx'] or f['cash'] or f['bank']:
            raise ValueError('Chỉ thu phiếu lưu tạm chưa có giao dịch quỹ hoặc phân bổ tiền.')
    elif op.kind=='refund':
        if not f['valid'] or f['h']['Status']!='P': raise ValueError('Chỉ hoàn cọc đã xác minh thu quỹ, chưa dùng trên hóa đơn.')
        if op.cash!=f['cash'] or op.bank!=f['bank']: raise ValueError('Hoàn toàn bộ theo đúng tiền mặt/chuyển khoản đã thu.')
        if f['tx'][0]['TillID']!=op.till_id: raise ValueError('Cần dùng đúng két đã thu để hoàn cọc.')
    else: raise ValueError('Loại thao tác tiền chưa được hỗ trợ.')


def matches_bank(row,op,codes):
    if row.get('direction')!=('in' if op.kind=='receive' else 'out') or Decimal(row.get('trans_amount') or 0)!=op.bank:
        return False
    text=str(row.get('description') or '')+' '+str(row.get('bill_code_raw') or '')
    return any(code and re.search(r'(?<![A-Za-z0-9])'+re.escape(code)+r'(?![A-Za-z0-9])',text,re.I) for code in codes)


def bank_evidence(op,h):
    """Chỉ khớp duy nhất mã phiếu + số tiền + chiều + ngày tạo yêu cầu; không ghép theo số tiền đơn lẻ."""
    if not op.bank: return None
    if op.target!='kk': raise ValueError('Bản thử không nhận chứng từ ngân hàng thật.')
    from .transfers import query
    start=timezone.localtime(op.created_at).replace(hour=0,minute=0,second=0,microsecond=0)
    rows=query('SELECT id,bank_number,transaction_time,trans_amount,direction,bill_code_raw,is_check,description '
               'FROM bank_notifications WHERE direction=%s AND trans_amount=%s AND transaction_time >= %s '
               'AND transaction_time < %s AND is_check=0 ORDER BY id',
               ['in' if op.kind=='receive' else 'out',op.bank,start, start+dt.timedelta(days=3)])
    candidates=[r for r in rows if matches_bank(r,op,[op.trn_id,h.get('BillCode')])]
    if len(candidates)!=1: raise ValueError('Chưa có duy nhất một giao dịch ngân hàng khớp mã phiếu và số tiền.')
    return candidates[0]


def log_event(op):
    DepositEvent.objects.create(target=op.target,trn_id=op.trn_id,action='money_'+op.kind,username=op.username,
        token=uuid.uuid4().hex,note=STATES.get(op.status,op.status)+' · '+op.message,
        data={'operation':op.pk,'amount':str(op.amount),'cash':str(op.cash),'bank':str(op.bank),'status':op.status})


def execute(op):
    """Claim in MySQL before any PMV write. A crash never causes a second debit/credit."""
    if op.status!='queued': return op
    c=PmvClient(op.target,tag='datcoc-money:'+op.username)
    try:
        f=financial(c,op.trn_id); validate_operation(op,f)
        if money_stamp(c,f['h'])!=op.evidence['stamp']: raise ValueError('Phiếu đã thay đổi từ khi lập yêu cầu.')
        bank=bank_evidence(op,f['h'])
    except ValueError as exc:
        op.message=str(exc); op.save(update_fields=['message']); return op
    # Reserve evidence and operation in one local transaction; never reuse a bank notification.
    with transaction.atomic():
        locked=Operation.objects.select_for_update().get(pk=op.pk)
        if locked.status!='queued': return locked
        if bank:
            from .transfers import query
            from .models import BankReconcileState, ThauPaymentLink
            suffix=' FOR UPDATE' if connection.vendor=='mysql' else ''
            latest=query('SELECT id,bank_number,transaction_time,trans_amount,direction,bill_code_raw,is_check,description '
                         'FROM bank_notifications WHERE id=%s'+suffix,[bank['id']])
            if not latest or latest[0]!=bank: raise ValueError('Giao dịch ngân hàng vừa thay đổi; sẽ đối soát lại.')
            if BankReconcileState.objects.filter(notification_id=bank['id']).exclude(trn_id='').exists() or ThauPaymentLink.objects.filter(active_notification_id=bank['id']).exists():
                raise ValueError('Chứng từ ngân hàng đã liên kết nghiệp vụ khác; không sử dụng lại.')
            op.bank_key='bank:'+str(bank['id'])
            with connection.cursor() as cur:
                cur.execute('UPDATE bank_notifications SET is_check=1 WHERE id=%s AND is_check=0',[bank['id']])
                if cur.rowcount!=1: raise ValueError('Giao dịch ngân hàng đã được sử dụng.')
        op.evidence.update(before=safe_data(f),bank=safe_data(bank))
        op.status='running'; op.message=''; op.save()
    try:
        # Re-read immediately before proc; user approval binds the complete financial snapshot.
        fresh=financial(c,op.trn_id); validate_operation(op,fresh)
        if op.kind=='receive' and DepositOrderState.objects.filter(target=op.target,trn_id=op.trn_id,fulfilment='cancelled').exists():
            raise ValueError('Đặt hàng đã hủy trước khi ghi thu cọc.')
        if money_stamp(c,fresh['h'])!=op.evidence['stamp']: raise ValueError('Phiếu vừa đổi trước khi ghi PMV.')
        if op.kind=='receive':
            c.call('TRN_DATCOC_Complete',write=True,p_TrnID=op.trn_id,p_UserID=op.user_id)
            tx=c.query('SELECT TillTxnID,Status,TrnTotalAmount FROM T_TILL_TXN WITH (NOLOCK) WHERE TrnRefID=?',(op.trn_id,))
            if len(tx)!=1 or tx[0]['Status']!='U' or tx[0]['TrnTotalAmount']!=op.amount: raise ValueError('Quỹ chưa tạo đúng tổng cọc.')
            # NULL là bắt buộc; chuỗi rỗng làm proc vendor rẽ sai nhánh SRT.
            c.call('CARDPAY_Ins',write=True,day_du=True,p_TrnID=op.trn_id,p_TillID=op.till_id,p_TillTxnID=tx[0]['TillTxnID'],
                p_TypeTrade='TDC',p_ProductIDs=None,p_CardAmounts=None,p_Amount=format(op.bank,'f'),p_AmountTra=format(op.cash,'f'),p_List='<NewDataSet/>')
            detail=c.query('SELECT Amount,CrDr,GoldCcy FROM T_TILL_TXN_DETAIL WITH (NOLOCK) WHERE TillTxnID=?',(tx[0]['TillTxnID'],))
            if len(detail)!=1 or detail[0]['Amount'] is None or detail[0]['Amount']!=op.cash or detail[0]['CrDr']!='+' or detail[0]['GoldCcy']!='VND':
                raise ValueError('Chi tiết quỹ không khớp; dừng trước khi ghi số dư két.')
            c.call('T_TILL_TXN_Proc',write=True,p_TrnIDs=op.trn_id,p_TillID=op.till_id,p_UserID=op.user_id)
            after=financial(c,op.trn_id)
            if not after['valid'] or after['cash']!=op.cash or after['bank']!=op.bank or after['tx'][0]['TillID']!=op.till_id:
                raise ValueError('Chưa xác minh đủ chứng từ thu cọc sau khi ghi.')
        else:
            c.call('T_TILL_TXN_Del',write=True,day_du=True,p_TrnRefID=op.trn_id,pType='TDC',pCongNoBanLe=0,
                p_UserUpd=op.user_id,p_TrnDateTime_Upd=fresh['h']['TrnDateTime_Upd'],p_Type='0')
            after=financial(c,op.trn_id)
            if after['h']['Status']!='W' or after['tx'] or after['cash'] or after['bank']: raise ValueError('Chưa xác minh hoàn cọc trên PMV.')
        op.evidence['after']=safe_data(after)
        op.status='done'; op.active_key=None; op.completed_at=timezone.now(); op.message='PMV đã ghi và đọc lại khớp.'
    except Exception:
        D.log.exception('Thao tác tiền cọc chưa xác định kết quả')
        op.status='uncertain'; op.message='Dừng ghi lại. Kiểm tra chứng từ PMV theo mã phiếu và nhật ký thao tác.'
    op.save(); log_event(op); W.invalidate(op.target)
    return op


class MoneyForm(forms.Form):
    cash=forms.DecimalField(label='Tiền mặt (₫)',min_value=0,max_digits=18,decimal_places=3)
    bank=forms.DecimalField(label='Chuyển khoản (₫)',min_value=0,max_digits=18,decimal_places=3)
    note=forms.CharField(label='Diễn giải chứng từ',max_length=500,widget=forms.Textarea(attrs={'rows':2}))
    confirm=forms.BooleanField(label='Xác nhận tiền mặt thực thu/hoàn; phần CK chỉ ghi PMV sau khi tự đối soát khớp ngân hàng')


@require_http_methods(['GET','POST'])
def action(request,pk,kind):
    if kind not in ('receive','refund','history','cancel'): raise Http404
    D.authorize(request,'can_view' if kind=='history' else 'can_approve')
    c=PmvClient(tag='datcoc-money-view:'+request.user.username)
    ctx={'pk':pk,'title':{'receive':'Thu tiền cọc','refund':'Hoàn toàn bộ cọc','history':'Chứng từ & đối soát cọc','cancel':'Hủy yêu cầu chờ đối soát'}[kind]}
    try:
        f=financial(c,pk)
        operations=list(Operation.objects.filter(target=c.target,trn_id=pk).order_by('-id')[:30])
        for op in operations: op.state_name=STATES.get(op.status,op.status)
        ctx.update(financial=f,operations=operations)
        if kind=='history': return render(request,'pos/_dat_coc_money.html',ctx)
        if kind=='cancel':
            form=forms.Form(request.POST or None)
        else:
            form=MoneyForm(request.POST or None,initial={'cash':f['cash'] if kind=='refund' else f['amount'],'bank':f['bank'] if kind=='refund' else 0})
        stamp={**money_stamp(c,f['h']),'kind':kind,'nonce':uuid.uuid4().hex}
        ctx.update(form=form,token=request.POST.get('token') or signing.dumps(stamp,salt='dc-money'))
        if request.method=='POST' and form.is_valid():
            submitted=signing.loads(ctx['token'],salt='dc-money',max_age=7200)
            submitted.pop('nonce',None)
            if submitted!={**money_stamp(c,f['h']),'kind':kind}: raise ValueError('Phiếu vừa thay đổi. Mở lại popup.')
            if kind=='cancel':
                with transaction.atomic():
                    op=Operation.objects.select_for_update().filter(target=c.target,trn_id=pk,status__in=['queued','blocked'],bank_key__isnull=True,active_key__isnull=False).first()
                    if not op: raise ValueError('Không có yêu cầu đang chờ có thể hủy.')
                    op.status='cancelled'; op.active_key=None; op.message='Người dùng hủy trước khi ghi PMV.'; op.save(); log_event(op)
            else:
                pu=pmv_user_for_web_user(request.user)
                if not pu or not pu.till_id: raise ValueError('Tài khoản chưa liên kết nhân viên/két PMV.')
                data=form.cleaned_data
                op=Operation(target=c.target,trn_id=pk,kind=kind,amount=f['amount'],cash=data['cash'],bank=data['bank'],
                    user_id=pu.user_id,till_id=pu.till_id,username=request.user.username,active_key=c.target+':'+pk,
                    token=hashlib.sha256(ctx['token'].encode()).hexdigest(),evidence={'stamp':money_stamp(c,f['h']),'note':data['note']})
                validate_operation(op,f)
                from .deposit_operations import base_state
                with transaction.atomic():
                    op.save()
                    state=DepositOrderState.objects.select_for_update().filter(target=c.target,trn_id=pk).first()
                    if not state:
                        items=[D.O.item_info(i) for i in D.lines(c,pk)]; state=base_state(c,f['h'],items)
                        state.fulfilment=W.prepare({'headers':[f['h']],'items':{pk:items}})[0]['fulfilment']; state.save()
                    log_event(op)
                op=execute(op)
            from .deposit_messages import saved
            return saved(STATES[op.status]+': '+op.message,'success' if op.status in ('done','cancelled') else 'warning' if op.status=='queued' else 'error')
    except (ValueError,signing.BadSignature) as exc: ctx['error']=str(exc)
    except IntegrityError: ctx['error']='Phiếu đã có yêu cầu tiền đang xử lý hoặc chứng từ ngân hàng đã dùng. Xem lịch sử đối soát.'
    except Exception:
        D.log.exception('Đối soát tiền cọc'); ctx['error']='Không đọc/ghi được chứng từ. Xem lịch sử trước khi thử lại.'
    return render(request,'pos/_dat_coc_money.html',ctx)


def process_queue(target):
    from django.contrib.auth import get_user_model
    Operation.objects.filter(target=target,status='running',created_at__lt=timezone.now()-dt.timedelta(minutes=5)).update(status='uncertain',message='Tiến trình gián đoạn; cần kiểm tra PMV, không tự ghi lại.')
    count=0
    for op in Operation.objects.filter(target=target,status='queued').order_by('id')[:30]:
        user=get_user_model().objects.filter(username=op.username,is_active=True).first()
        pu=pmv_user_for_web_user(user) if user else None
        if not user or not D.allowed(user,'can_approve') or not pu or pu.user_id!=op.user_id or pu.till_id!=op.till_id:
            op.status='blocked'; op.message='Quyền hoặc liên kết két của người lập đã thay đổi.'; op.save(); continue
        if op.created_at<timezone.now()-dt.timedelta(days=3):
            op.status='blocked'; op.message='Quá 3 ngày chưa đối soát; cần kiểm tra yêu cầu.'; op.save(); continue
        try: count+=execute(op).status=='done'
        except Exception: D.log.exception('Đối soát cọc tự động')
    return count


def invoice_deposit_guard(c,invoice_id,amount,cust_id):
    linked=c.query('SELECT DatCocID FROM TRN_RT_BUYSELL_DatCoc WITH (NOLOCK) WHERE TrnID=?',(invoice_id,))
    if not linked: return
    funds=[financial(c,r['DatCocID']) for r in linked]
    if any(not f['valid'] or f['changes'] or len(f['links'])!=1 or f['h']['CustID']!=cust_id for f in funds):
        raise ValueError('Chứng từ cọc liên kết hóa đơn không còn hợp lệ; cần đối soát trước khi sửa/chốt.')
    if sum((f['amount'] for f in funds),Decimal(0))!=Decimal(amount or 0):
        raise ValueError('Tiền cọc hóa đơn phải khớp tổng phiếu cọc đã liên kết.')


@require_http_methods(['GET','POST'])
def apply_to_invoice(request,pk):
    D.authorize(request,'can_approve')
    c=PmvClient(tag='datcoc-apply:'+request.user.username)
    class ApplyForm(forms.Form):
        invoice=forms.CharField(label='Mã TrnID hóa đơn bán đang lưu tạm',max_length=15)
        confirm=forms.BooleanField(label='Hóa đơn cùng khách đã nhập đúng tiền cọc để giảm tiền khách trả')
    form=ApplyForm(request.POST or None)
    ctx={'pk':pk,'title':'Cấn cọc vào hóa đơn','form':form}
    try:
        f=financial(c,pk)
        ctx['token']=request.POST.get('token') or signing.dumps({**money_stamp(c,f['h']),'nonce':uuid.uuid4().hex},salt='dc-apply')
        if request.method=='POST' and form.is_valid():
            stamp=signing.loads(ctx['token'],salt='dc-apply',max_age=7200); stamp.pop('nonce',None)
            if stamp!=money_stamp(c,f['h']): raise ValueError('Phiếu cọc vừa thay đổi. Mở lại popup.')
            if not f['valid'] or f['h']['Status']!='P' or f['links'] or f['changes']: raise ValueError('Cần cọc đã thu quỹ và chưa liên kết hóa đơn nào.')
            invoice=form.cleaned_data['invoice']
            inv=c.query("SELECT TrnID,Status,CustID,TienCoc,PayAmount,SellTotalAmount,BuyTotalAmount,Discount,TaskPriceAdd,AddMoney,TrnDateTime_Upd FROM TRN_RT_BUYSELL WITH (NOLOCK) WHERE TrnID=? AND IsDel='0'",(invoice,))
            if not inv or inv[0]['Status']!='W' or inv[0]['CustID']!=f['h']['CustID']: raise ValueError('Hóa đơn phải còn lưu tạm và cùng khách hàng.')
            inv=inv[0]
            from .bill import tinh_tong
            expected=tinh_tong(inv['SellTotalAmount'],inv['BuyTotalAmount'],inv['Discount'],inv['TaskPriceAdd'],inv['AddMoney'],f['amount'])['khach_tra']
            if inv['TienCoc']!=f['amount'] or inv['PayAmount']!=expected or expected<0:
                raise ValueError('Nhập đúng tiền cọc trên hóa đơn bán trước khi liên kết; tiền khách trả sau cấn phải không âm.')
            if c.query('SELECT DatCocID FROM TRN_RT_BUYSELL_DatCoc WITH (NOLOCK) WHERE TrnID=?',(invoice,)):
                raise ValueError('Hóa đơn đã liên kết phiếu cọc. Không ghi đè danh sách cọc.')
            pu=pmv_user_for_web_user(request.user)
            if not pu: raise ValueError('Tài khoản chưa liên kết PMV.')
            with transaction.atomic():
                # Unique invoice token serializes concurrent attempts to bind a different deposit to the same invoice.
                op=Operation.objects.create(target=c.target,trn_id=pk,kind='apply',amount=f['amount'],cash=f['cash'],bank=f['bank'],
                    invoice_id=invoice,bank_key=c.target+':invoice:'+invoice,active_key=c.target+':'+pk,status='running',
                    token=hashlib.sha256(ctx['token'].encode()).hexdigest(),user_id=pu.user_id,till_id=pu.till_id,username=request.user.username,
                    evidence={'before':safe_data(f),'invoice':safe_data(inv)})
            try:
                check=financial(c,pk)
                if money_stamp(c,check['h'])!=stamp or check['links'] or check['changes']: raise ValueError('Cọc vừa đổi trước khi liên kết.')
                current=c.query('SELECT TrnDateTime_Upd,Status,TienCoc,PayAmount,CustID FROM TRN_RT_BUYSELL WITH (NOLOCK) WHERE TrnID=?',(invoice,))
                if not current or any(current[0][k]!=inv[k] for k in current[0]): raise ValueError('Hóa đơn vừa thay đổi.')
                c.call('TRN_RT_BUYSELL_DatCoc_Ins',write=True,p_TrnID=invoice,p_IDCoc=pk)
                links=c.query('SELECT DatCocID FROM TRN_RT_BUYSELL_DatCoc WITH (NOLOCK) WHERE TrnID=?',(invoice,))
                if links!=[{'DatCocID':pk}]: raise ValueError('Chưa xác nhận liên kết.')
                op.status='done'; op.active_key=None; op.completed_at=timezone.now(); op.message='Đã liên kết; cọc được sử dụng khi hóa đơn bán hoàn tất.'
            except Exception:
                D.log.exception('Liên kết cọc chưa xác định'); op.status='uncertain'; op.message='Kiểm tra liên kết cọc trên PMV trước khi thao tác tiếp.'
            op.save(); log_event(op); W.invalidate(c.target)
            from .deposit_messages import saved
            return saved(op.message,'success' if op.status=='done' else 'error')
    except (ValueError,signing.BadSignature) as exc: ctx['error']=str(exc)
    except IntegrityError: ctx['error']='Cọc hoặc hóa đơn đã có yêu cầu liên kết. Kiểm tra chứng từ hiện tại.'
    except Exception:
        D.log.exception('Cấn tiền cọc'); ctx['error']='Chưa liên kết được hóa đơn. Kiểm tra dữ liệu trước khi thử lại.'
    return render(request,'pos/_dat_coc_action.html',ctx)
