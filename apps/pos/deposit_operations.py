import hashlib
import json
from decimal import Decimal

from django import forms
from django.core import signing
from django.db import IntegrityError, transaction
from django.http import Http404, HttpResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from apps.pmv.client import PmvClient
from . import deposits as D, deposit_workspace as W
from .deposit_models import DepositOrderState, DepositEvent, DepositStockHold

CONTACTS = [('notified','Đã báo hàng xong'),('no_answer','Không nghe máy'),('reschedule','Khách hẹn lại'),
            ('waiting','Chờ khách xác nhận'),('workshop','Chờ xưởng xác nhận')]


def signature(items):
    data = [{k:i.get(k) for k in ('ProductDesc','GoldCode','SL','Size','Notes','ProductCode')} for i in items]
    return hashlib.sha256(json.dumps(data, default=str, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def base_state(c, h, items):
    info = D.decorate(dict(h))
    return DepositOrderState(target=c.target,trn_id=h['TrnID'],original_promise=info['promise_date'],
                             promise=info['promise_date'],employee_id=h.get('EmpID') or '',
                             employee_name=h.get('EmpName') or '',item_signature=signature(items))


def overlay(rows, target):
    from .deposit_models import DepositMoneyOperation
    from .deposit_money import money_stamp
    from types import SimpleNamespace
    states = {s.trn_id:s for s in DepositOrderState.objects.filter(target=target)}
    holds = list(DepositStockHold.objects.filter(target=target,active_key__isnull=False))
    refunds={op.trn_id:op for op in DepositMoneyOperation.objects.filter(target=target,status='done').order_by('id')}
    for r in rows:
        op=refunds.get(r['TrnID'])
        if op and op.kind=='refund' and money_stamp(SimpleNamespace(target=target),op.evidence.get('after',{}).get('h',{}))==money_stamp(SimpleNamespace(target=target),r):
            r.update(money_confirmed=True,money_balance=Decimal(0),money_name='Đã hoàn toàn bộ')
        s = states.get(r['TrnID'])
        r['holds'] = [h for h in holds if h.trn_id == r['TrnID']]
        if not s: continue
        r.update(promise_date=s.promise,original_promise=s.original_promise,EmpID=s.employee_id,
                 responsible=s.employee_name or 'Chưa phân công',next_contact=s.next_contact,
                 last_contact=s.last_contact,contacted=s.contacted,work_note=s.note,
                 quote_state=s.quote_status,quote_terms=s.quote_terms,estimate=s.quote_amount if s.quote_amount is not None else r['estimate'],
                 ready_at=s.ready_at,delivered_at=s.delivered_at)
        changed = s.item_signature and s.item_signature != signature(r['items'])
        r['items_changed'] = changed
        if changed:
            r['hold_conflict'] = True
            r['work_note'] = 'Chi tiết món đã đổi; cần xác nhận lại tiến độ'
        elif s.fulfilment:
            r['fulfilment'] = s.fulfilment
            r['state_name'] = W.FULFILMENT[s.fulfilment]
            total = sum(x.get('quantity',0) for x in s.items)
            ready = sum(x.get('ready',0) for x in s.items)
            delivered = sum(x.get('delivered',0) for x in s.items)
            r['progress_text'] = f'{ready}/{total} sẵn sàng · {delivered}/{total} đã giao' if total else ''
        # Một app khác đã kết thúc phiếu hoặc KHBL đã hủy: không khôi phục nhắc hẹn từ trạng thái cũ.
        if r['mobile_order'] and r['source_status'] in ('C','D'):
            r['fulfilment']={'C':'delivered','D':'cancelled'}[r['source_status']]
            r['state_name']=W.FULFILMENT[r['fulfilment']]; r['next_contact']=None
        elif s.fulfilment=='cancelled':
            r['fulfilment']='cancelled'; r['state_name']=W.FULFILMENT['cancelled']; r['next_contact']=None
    return rows


def check_holds(codes, target):
    keys = [target+':'+str(code).strip().casefold() for code in codes]
    hold = DepositStockHold.objects.filter(active_key__in=keys).first()
    if hold:
        raise ValueError(f'Hàng {hold.product_code} đang giữ cho phiếu {hold.trn_id} tại {hold.location}. Mở phiếu đặt hàng để bàn giao hoặc giải phóng trước khi bán.')


class ContactForm(forms.Form):
    employee = forms.ChoiceField(label='Người phụ trách',required=False)
    result = forms.ChoiceField(label='Kết quả liên hệ',choices=CONTACTS)
    promise = forms.DateField(label='Ngày khách hẹn lấy',required=False,widget=forms.DateInput(attrs={'type':'date'},format='%Y-%m-%d'))
    next_contact = forms.DateTimeField(label='Nhắc lại lúc',required=False,widget=forms.DateTimeInput(attrs={'type':'datetime-local'},format='%Y-%m-%dT%H:%M'))
    note = forms.CharField(label='Nội dung / lý do dời lịch',max_length=1000,widget=forms.Textarea(attrs={'rows':2}))

    def clean(self):
        d=super().clean()
        if d.get('next_contact') and d['next_contact'] <= timezone.now(): self.add_error('next_contact','Lịch nhắc phải ở tương lai.')
        if d.get('result') == 'reschedule' and not d.get('promise'): self.add_error('promise','Nhập ngày hẹn mới.')
        return d


class HoldForm(forms.Form):
    product = forms.ChoiceField(label='Mã hàng')
    location = forms.CharField(label='Vị trí giữ hàng / lý do giải phóng',max_length=200)


class QuoteForm(forms.Form):
    quote_status = forms.ChoiceField(label='Giá',choices=[('estimate','Tạm tính'),('fixed','Đã chốt')])
    quote_amount = forms.DecimalField(label='Tổng giá trị (₫)',min_value=0,max_digits=18,decimal_places=3)
    quote_terms = forms.CharField(label='Thỏa thuận giá / trọng lượng / tiền công',max_length=1000,widget=forms.Textarea(attrs={'rows':3}))


class CancelForm(forms.Form):
    reason = forms.CharField(label='Lý do hủy đơn',max_length=1000,widget=forms.Textarea(attrs={'rows':2}))
    confirm = forms.BooleanField(label='Xác nhận hủy tiến độ đặt hàng; tiền cọc được xử lý riêng theo chứng từ')


@require_http_methods(['GET','POST'])
def action(request, pk, kind):
    if kind not in ('contact','progress','hold','release','quote','cancel'): raise Http404
    D.authorize(request,'can_delete' if kind == 'cancel' else 'can_edit')
    c=PmvClient(tag='datcoc-ops:'+request.user.username)
    ctx={'pk':pk,'kind':kind,'title':{'contact':'Liên hệ & nhắc hẹn','progress':'Tiến độ từng món',
          'hold':'Giữ hàng','release':'Giải phóng hàng','quote':'Thỏa thuận giá','cancel':'Hủy đặt hàng'}[kind]}
    try:
        h=D.header(c,pk)
        if not h: raise Http404
        items=[D.O.item_info(i) for i in D.lines(c,pk)]
        s=DepositOrderState.objects.filter(target=c.target,trn_id=pk).first() or base_state(c,h,items)
        stamp={**D.version(c,h),'local':s.version,'items':signature(items),'kind':kind}
        ctx['token']=request.POST.get('token') if request.method=='POST' else signing.dumps(stamp,salt='dc-ops')
        initial_state=W.prepare({'headers':[h],'items':{pk:items}})[0]['fulfilment']
        current=s.fulfilment or initial_state
        employees=[]
        if kind=='contact':
            employees=c.query('SELECT EmpID,EmpName FROM T_EMPLOYEE WITH (NOLOCK) ORDER BY EmpName')
            form=ContactForm(request.POST or None,initial={'employee':s.employee_id,'promise':s.promise,'next_contact':s.next_contact})
            form.fields['employee'].choices=[('','Chưa phân công')]+[(e['EmpID'],e['EmpName']) for e in employees]
        elif kind in ('hold','release'):
            form=HoldForm(request.POST or None)
            codes=[i['ProductCode'] for i in items if i['ProductCode']] if kind=='hold' else list(DepositStockHold.objects.filter(target=c.target,trn_id=pk,active_key__isnull=False).values_list('product_code',flat=True))
            form.fields['product'].choices=[(code,code) for code in codes]
        elif kind=='quote': form=QuoteForm(request.POST or None,initial={k:getattr(s,k) for k in ('quote_status','quote_amount','quote_terms')})
        elif kind=='cancel': form=CancelForm(request.POST or None)
        else:
            form=forms.Form(request.POST or None)
            for n,item in enumerate(items):
                quantity=item.get('SL') or 1
                previous=s.items[n] if s.item_signature==signature(items) and n<len(s.items) else {}
                label=item.get('ProductDesc') or item.get('Notes') or f'Món {n+1}'
                form.fields[f'ready_{n}']=forms.IntegerField(label=f'{label} · Sẵn sàng / {quantity}',min_value=0,max_value=quantity,initial=previous.get('ready',quantity if current in ('ready','delivered') else 0))
                form.fields[f'delivered_{n}']=forms.IntegerField(label=f'{label} · Đã giao / {quantity}',min_value=0,max_value=quantity,initial=previous.get('delivered',quantity if current=='delivered' else 0))
            if s.item_signature and s.item_signature!=signature(items):
                form.fields['accept_changed']=forms.BooleanField(label='Chi tiết đã đổi: xác nhận nhập lại tiến độ cho các món hiện tại')
            form.fields['note']=forms.CharField(label='Ghi chú bàn giao / nguyên nhân trễ',max_length=1000,required=False)
        ctx.update(form=form,h=h,events=DepositEvent.objects.filter(target=c.target,trn_id=pk).order_by('-id')[:15])
        if request.method=='POST' and form.is_valid():
            submitted=signing.loads(ctx['token'],salt='dc-ops',max_age=7200)
            if submitted!=stamp: raise ValueError('Phiếu vừa thay đổi. Đóng và mở lại popup.')
            if current in ('cancelled','delivered') and kind in ('hold','progress'):
                raise ValueError('Phiếu đã kết thúc; không thêm giữ hàng hoặc sửa tiến độ giao.')
            if current=='delivered' and kind=='cancel': raise ValueError('Phiếu đã giao; cần chứng từ trả hàng, không hủy đặt hàng.')
            if kind=='hold' and not c.query("SELECT ProductCode FROM T_PRODUCT WITH (NOLOCK) WHERE ProductCode=? AND Status='I'",(form.cleaned_data['product'],)):
                raise ValueError('Mã hàng không còn sẵn trong kho.')
            with transaction.atomic():
                locked=DepositOrderState.objects.select_for_update().filter(target=c.target,trn_id=pk).first()
                if locked and locked.version!=s.version: raise ValueError('Có người vừa cập nhật phiếu. Mở lại popup.')
                if not locked:
                    if s.pk: raise ValueError('Dữ liệu đã thay đổi.')
                    s.save()
                else: s=locked
                token=hashlib.sha256(ctx['token'].encode()).hexdigest()
                if DepositEvent.objects.filter(token=token).exists(): raise ValueError('Thao tác này đã được xử lý.')
                data=form.cleaned_data; now=timezone.now()
                before={'state':s.fulfilment,'promise':str(s.promise),'items':s.items,'next_contact':str(s.next_contact)}
                if kind=='contact':
                    s.employee_id=data['employee']; s.employee_name=dict(form.fields['employee'].choices).get(s.employee_id,'')
                    s.promise=data['promise']; s.next_contact=data['next_contact']; s.last_contact=now
                    s.contacted=data['result'] in ('notified','reschedule','waiting')
                    s.note=data['note']
                elif kind=='progress':
                    if not items: raise ValueError('Phiếu chưa có món để cập nhật tiến độ.')
                    progress=[]
                    for n,item in enumerate(items):
                        ready,delivered=data[f'ready_{n}'],data[f'delivered_{n}']
                        if delivered>ready: raise ValueError('Số đã giao không được lớn hơn số sẵn sàng.')
                        previous=s.items[n] if s.item_signature==signature(items) and n<len(s.items) else {}
                        if delivered<previous.get('delivered',0): raise ValueError('Không giảm số đã giao; hàng khách trả cần chứng từ xử lý riêng.')
                        progress.append({'quantity':item.get('SL') or 1,'ready':ready,'delivered':delivered,'code':item['ProductCode']})
                    new='delivered' if all(i['delivered']==i['quantity'] for i in progress) else 'partial' if any(i['delivered'] for i in progress) else 'ready' if all(i['ready']==i['quantity'] for i in progress) else 'waiting'
                    if all(i['ready']==i['quantity'] for i in progress) and not s.ready_at:
                        s.ready_at=now; s.contacted=False
                    if new=='delivered' and current!='delivered': s.delivered_at=now; s.next_contact=None
                    s.fulfilment=new; s.items=progress; s.item_signature=signature(items); s.note=data.get('note','')
                    for i in progress:
                        if i['code'] and i['delivered']==i['quantity']:
                            DepositStockHold.objects.filter(target=c.target,trn_id=pk,product_code=i['code'],active_key__isnull=False).update(active_key=None,released_at=now,released_by=request.user.username)
                elif kind=='hold':
                    code=data['product']; key=c.target+':'+code.casefold()
                    if DepositStockHold.objects.filter(active_key=key).exists(): raise ValueError('Mã hàng đã được giữ. Xem lại phiếu đang giữ hàng.')
                    DepositStockHold.objects.create(target=c.target,trn_id=pk,product_code=code,active_key=key,location=data['location'],username=request.user.username)
                elif kind=='release':
                    count=DepositStockHold.objects.filter(target=c.target,trn_id=pk,product_code=data['product'],active_key__isnull=False).update(active_key=None,released_at=now,released_by=request.user.username)
                    if not count: raise ValueError('Hàng đã được giải phóng trước đó.')
                elif kind=='quote':
                    for key in ('quote_status','quote_amount','quote_terms'): setattr(s,key,data[key])
                elif kind=='cancel':
                    s.fulfilment='cancelled'; s.next_contact=None; s.note=data['reason']
                    from .deposit_models import DepositMoneyOperation
                    DepositMoneyOperation.objects.filter(target=c.target,trn_id=pk,kind='receive',status='queued').update(
                        status='cancelled',active_key=None,message='Dừng yêu cầu thu chưa thực hiện vì đặt hàng đã hủy.')
                    DepositStockHold.objects.filter(target=c.target,trn_id=pk,active_key__isnull=False).update(active_key=None,released_at=now,released_by=request.user.username)
                s.version+=1; s.save()
                DepositEvent.objects.create(target=c.target,trn_id=pk,action=kind,username=request.user.username,
                    note=str(data.get('note') or data.get('reason') or data.get('location') or ''),token=token,
                    data=json.loads(json.dumps({'before':before,'after':data},default=str)))
            W.invalidate(c.target)
            response=HttpResponse(''); response['HX-Trigger']=json.dumps({'depositSaved':{'message':'Đã cập nhật '+ctx['title'].lower()+'.'}}); return response
    except Http404: raise
    except (ValueError,signing.BadSignature,IntegrityError) as exc: ctx['error']=str(exc) if not isinstance(exc,IntegrityError) else 'Dữ liệu vừa được người khác cập nhật. Mở lại popup.'
    except Exception:
        D.log.exception('Cập nhật vận hành đặt hàng'); ctx['error']='Không cập nhật được. Kiểm tra kết nối và mở lại phiếu.'
    return render(request,'pos/_dat_coc_action.html',ctx)
