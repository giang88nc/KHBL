"""Quyền sửa phiếu và tiến độ; không dùng Passcode để vượt khóa chứng từ PMV."""
import hashlib
import json
from decimal import Decimal

from django import forms
from django.core import signing
from django.db import transaction
from django.http import JsonResponse, HttpResponse
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from . import deposits as D, deposit_orders as O, deposit_operations as OP, deposit_photos as P
from .deposit_models import DepositOrderState, DepositEvent, DepositStockHold, DepositMoneyOperation

PROGRESS = [('new','Đơn mới'),('ordering','Đang đặt hàng'),('crafting','Thợ đang làm'),
            ('ready','Hàng sẵn sàng'),('shipping','Đang giao'),
            ('cancel_refund','Đã hủy - hoàn cọc'),('cancel_keep','Đã hủy - không hoàn cọc'),('delivered','Hoàn thành - đặt'),('applied','Hoàn thành - áp dụng')]


def entries(h, state):
    return O.note_entries(h.get('Description')) + (state.extra_notes if state else [])


def current_progress(state, work):
    if work.get('application_applied'): return 'applied'
    value=work.get('fulfilment','new')
    if value=='cancelled' and state and state.cancellation_policy:
        return 'cancel_'+state.cancellation_policy
    return 'new' if value=='waiting' else value


def configure(form, state, work):
    current=current_progress(state,work)
    choices=list(PROGRESS)
    if current not in dict(choices): choices.insert(0,(current,D.W.FULFILMENT.get(current,'Giữ tiến độ hiện tại')))
    form.fields['Progress']=forms.ChoiceField(label='Tiến độ',choices=choices,initial=current)
    form.initial['Progress']=current
    if form.is_bound and 'Progress' not in form.data:
        form.data=form.data.copy(); form.data['Progress']=current


def check_notes(old, submitted):
    result=O.validate_notes(submitted)
    if result[:len(old)]!=old:
        raise ValueError('Không được xóa hoặc sửa ghi chú đã lưu. Hãy thêm ghi chú mới để đính chính.')
    # Ngày của ghi chú mới do máy chủ quyết định.
    return old + [{'date':timezone.localdate().isoformat(),'text':x['text']} for x in result[len(old):]]


def check_stamp(request,c,h,state):
    stamp=signing.loads(request.POST.get('token',''),salt='datcoc',max_age=7200)
    stamp.pop('nonce',None)
    local=stamp.pop('local',None)
    source=stamp.pop('source',None)
    if stamp!=D.version(c,h) or local!=(state.version if state else 0) or source!=source_stamp(h):
        raise ValueError('Phiếu vừa thay đổi. Đóng và mở lại popup trước khi lưu.')


def source_stamp(h):
    return {'status':str(h.get('Status') or ''),'money':[str(h.get(k) or 0) for k in ('TienCoc','CashPay','CardPay')]} if h else {}


def grant_stamp(request,c,pk):
    return {'target':c.target,'id':pk,'user':str(request.user.pk),
            'session':hashlib.sha256(str(request.session.session_key or '').encode()).hexdigest(),
            'form':hashlib.sha256(request.POST.get('token','').encode()).hexdigest()}


def unlocked(request,c,pk):
    token=request.POST.get('edit_grant','')
    if not token: return False
    try:
        if signing.loads(token,salt='dc-edit-grant',max_age=600)==grant_stamp(request,c,pk): return True
    except signing.BadSignature: pass
    raise ValueError('Xác nhận Passcode đã hết hạn hoặc không thuộc phiếu này. Xác nhận lại.')


@require_POST
def unlock(request,pk):
    D.authorize(request,'can_edit')
    from apps.pmv.client import PmvClient
    from .views import _passcode_dung, _passcode_khoa
    c=PmvClient(tag='datcoc-unlock:'+request.user.username)
    try:
        h=D.header(c,pk)
        if not h: raise ValueError('Không tìm thấy phiếu.')
        state=DepositOrderState.objects.filter(target=c.target,trn_id=pk).first()
        check_stamp(request,c,h,state)
        if h['Status']!='W': raise ValueError('PMV đã khóa chứng từ. Chỉ cập nhật tiến độ, hẹn, ghi chú, ảnh và nhân viên; thay đổi cọc qua chứng từ thu/hoàn.')
        if not _passcode_dung(request,request.POST.get('passcode')):
            wait=_passcode_khoa(request)
            raise ValueError(f'Tạm khóa Passcode. Thử lại sau {wait} giây.' if wait else 'Passcode không đúng.')
        return JsonResponse({'grant':signing.dumps(grant_stamp(request,c,pk),salt='dc-edit-grant')})
    except (ValueError,signing.BadSignature) as exc:
        return JsonResponse({'error':str(exc)},status=400)


def set_progress(state,value,items,user,work,new_notes=None):
    before=current_progress(state,work)
    if value=='applied' and not work.get('application_applied'):
        raise ValueError('Hoàn thành - áp dụng được cập nhật tự động khi hóa đơn chốt thành công.')
    if work.get('application_applied') and value!='applied':
        raise ValueError('Phiếu đang áp dụng hóa đơn; gỡ áp dụng trên hóa đơn trước khi đổi tình trạng.')
    if value==before and state.fulfilment: return
    target='cancelled' if value.startswith('cancel_') else value
    if target=='cancelled' and not any(str(n.get('text') or '').strip() for n in (new_notes or [])):
        raise ValueError('Hủy hoàn cọc hoặc không hoàn cọc phải có lý do trong ghi chú mới.')
    previous=state.items if state.item_signature==OP.signature(items) else []
    now=timezone.now()
    state.fulfilment=target
    state.cancellation_policy=value.removeprefix('cancel_') if target=='cancelled' else ''
    if target in ('ready','shipping','applied') and not state.ready_at: state.ready_at=now
    elif target not in ('ready','shipping','delivered','applied'): state.ready_at=None
    if target in ('delivered','applied') and not state.delivered_at: state.delivered_at=now
    elif target not in ('delivered','applied'): state.delivered_at=None
    if target in ('delivered','applied','cancelled'): state.next_contact=None
    if target!='cancelled':
        state.items=[{'quantity':i.get('SL') or 1,'code':i.get('ProductCode',''),
                      'ready':(i.get('SL') or 1) if target in ('ready','shipping','applied') else previous[n].get('ready',0) if target=='delivered' and n<len(previous) else 0,
                      'delivered':(i.get('SL') or 1) if target=='applied' else previous[n].get('delivered',0) if target=='delivered' and n<len(previous) else 0}
                     for n,i in enumerate(items)]
        state.item_signature=OP.signature(items)


def finish_progress(state,user):
    if state.fulfilment in ('cancelled','delivered','applied'):
        DepositStockHold.objects.filter(target=state.target,trn_id=state.trn_id,active_key__isnull=False).update(
            active_key=None,released_at=timezone.now(),released_by=user.username)
    if state.fulfilment=='cancelled':
        DepositMoneyOperation.objects.filter(target=state.target,trn_id=state.trn_id,kind='receive',status='queued').update(
            status='cancelled',active_key=None,message='Dừng yêu cầu thu vì đơn đã hủy.')


def saved_response(pk,progress,request):
    payload={'message':'Đã cập nhật phiếu đặt hàng.'}
    if progress.startswith('cancel_'):
        payload['message']='Đã cập nhật '+dict(PROGRESS)[progress]+'.'
    response=HttpResponse('')
    response['HX-Trigger']=json.dumps({'depositSaved':payload})
    return response


def save_direct(request,c,h,state,form,work,old_lines,money):
    """Sửa thường: giữ nguyên hàng/tiền; W lưu JSON qua proc, phiếu đã khóa lưu phần vận hành KHBL."""
    check_stamp(request,c,h,state)
    if request.POST.get('CustID',h['CustID'])!=h['CustID'] or any(k.startswith('items-') for k in request.POST) or any(k in request.POST for k in ('CashDeposit','BankDeposit')):
        raise ValueError('Thay đổi khách hàng, sản phẩm hoặc cọc cần xác nhận Passcode.')
    if request.POST.get('payment_action')=='receive': raise ValueError('Thu cọc qua Chứng từ cọc hoặc xác nhận Passcode trước khi sửa phiếu.')
    notes=check_notes(entries(h,state),form.raw_description)
    promise=form.cleaned_data.get('PromiseDate'); employee=form.cleaned_data['EmpID']; progress=form.cleaned_data['Progress']
    prepared=P.prepare(request.FILES)
    with D.SAVE_LOCK, transaction.atomic():
        locked=DepositOrderState.objects.select_for_update().filter(target=c.target,trn_id=h['TrnID']).first()
        fresh=D.header(c,h['TrnID']); check_stamp(request,c,fresh,locked)
        s=locked or OP.base_state(c,h,[O.item_info(i) for i in old_lines])
        before={'progress':current_progress(s,work),'promise':str(s.promise),'employee':s.employee_id}
        set_progress(s,progress,[O.item_info(i) for i in old_lines],request.user,work,notes[len(entries(h,state)):])
        active_money=DepositMoneyOperation.objects.filter(target=c.target,trn_id=h['TrnID'],active_key__isnull=False).exists()
        if progress==before['progress'] and h['Status']=='W' and not active_money and not (money['refunded'] or money['links'] or money['changes']):
            description=O.pack_description(promise,notes,h.get('estimate'))
            if notes!=O.note_entries(h.get('Description')) or promise!=h.get('promise_date') or employee!=(h.get('EmpID') or ''):
                from apps.pmv.models import pmv_user_for_web_user
                pu=pmv_user_for_web_user(request.user)
                if not pu or not pu.shop_id: raise ValueError('Tài khoản chưa liên kết nhân viên / cửa hàng PMV.')
                D.save(c,{'CustID':h['CustID'],'TienCoc':Decimal(h.get('TienCoc') or 0),'EmpID':employee,'Description':description},old_lines,pu,h)
            s.extra_notes=[]
        else:
            s.extra_notes=notes[len(O.note_entries(h.get('Description'))):]
        s.status=fresh['Status']; s.promise=promise; s.employee_id=employee; s.employee_name=dict(form.fields['EmpID'].choices).get(employee,'')
        s.version+=1; P.save(s,prepared,request.POST); finish_progress(s,request.user)
        DepositEvent.objects.create(target=c.target,trn_id=h['TrnID'],action='edit_info',username=request.user.username,
            token=hashlib.sha256(request.POST['token'].encode()).hexdigest(),
            data={'before':before,'after':{'progress':progress,'promise':str(promise),'employee':employee},'notes_added':len(notes)-len(entries(h,state))})
    D.W.invalidate(c.target)
    return saved_response(h['TrnID'],progress if progress!=before['progress'] else '',request)
