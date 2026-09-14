"""Hàng đợi OA: soạn theo mẫu, duyệt nhà cung cấp, gửi một lần, giữ kết quả không chắc chắn."""
import datetime as dt
import hashlib
import json
import re
import string
import uuid

from urllib.request import Request, urlopen
from urllib.parse import urlencode
from django import forms
from django.conf import settings
from django.core import signing
from django.core.paginator import Paginator
from django.db import transaction
from django.http import Http404, HttpResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods

from apps.pmv.client import PmvClient
from . import deposits as D, deposit_workspace as W
from .deposit_operations import overlay
from .deposit_models import DepositMessage, DepositMessageTemplate, DepositEvent
from . import deposit_reminders as R

STATUSES={'draft':'Bản nháp','waiting_template':'Chờ duyệt mẫu OA','scheduled':'Đã lên lịch',
          'sending':'Đang gửi','sent':'OA đã nhận tin','failed':'Gửi lỗi','uncertain':'Cần kiểm tra kết quả',
          'cancelled':'Đã xóa khỏi lịch','blocked':'Cần kiểm tra trước khi gửi'}
VARIABLES={'customer','bill','promise','deposit','store'}


def digest(text): return hashlib.sha256(text.encode()).hexdigest()


def oa_request(method, url, token, payload=None):
    body=json.dumps(payload).encode() if payload is not None else None
    request=Request(url,data=body,method=method,headers={'access_token':token,'Content-Type':'application/json'})
    with urlopen(request,timeout=20) as response:
        return json.loads(response.read(1024*1024))


def phone_number(value):
    value=re.sub(r'[\s().+-]','',str(value or ''))
    if value.startswith('0'): value='84'+value[1:]
    if not re.fullmatch(r'84[35789]\d{8}',value): raise ValueError('Số điện thoại di động Việt Nam chưa hợp lệ.')
    return value


def render_body(template, parameters):
    for _,name,spec,conversion in string.Formatter().parse(template.body):
        if name is not None and (name not in VARIABLES or spec or conversion):
            raise ValueError('Mẫu chỉ dùng {customer}, {bill}, {promise}, {deposit}, {store}.')
    return template.body.format(**parameters)


def provider_info(template):
    token=getattr(settings,'DATCOC_OA_ACCESS_TOKEN','')
    if not token: raise ValueError('Chưa cấu hình kết nối OA trên máy chủ.')
    result=oa_request('GET','https://business.openapi.zalo.me/template/info/v2?'+urlencode({'template_id':template.provider_id}),token)
    if result.get('error')!=0 or result.get('data',{}).get('status')!='ENABLE':
        raise ValueError('Mẫu OA chưa ở trạng thái ENABLE; tiếp tục chờ nhà cung cấp duyệt.')
    return result['data']


def validate_provider_parameters(template, message, info):
    payload={key:message.parameters[value] for key,value in template.parameter_map.items()}
    for param in info.get('listParams',[]):
        value=payload.get(param['name'])
        if param.get('require') and (value is None or (value=='' and not param.get('acceptNull'))):
            raise ValueError('Thiếu tham số mẫu OA: '+param['name'])
        if value is not None and len(str(value))>param.get('maxLength',10000):
            raise ValueError('Tham số vượt độ dài mẫu OA: '+param['name'])
    return payload


class QueueFilter(forms.Form):
    day=forms.DateField(label='Ngày xem / lịch gửi',widget=forms.DateInput(attrs={'type':'date'},format='%Y-%m-%d'))
    status=forms.ChoiceField(label='Trạng thái',required=False,choices=[('','Tất cả'),*STATUSES.items()])
    q=forms.CharField(label='Khách / SĐT / phiếu',required=False,max_length=100)


class TemplateForm(forms.ModelForm):
    parameter_map=forms.JSONField(label='Ánh xạ tham số OA (JSON)',required=False,widget=forms.Textarea(attrs={'rows':2}))
    class Meta:
        model=DepositMessageTemplate
        fields=['name','provider_id','body','parameter_map']
        labels={'name':'Tên mẫu','provider_id':'Mã template OA (chưa có có thể để trống)',
                'body':'Nội dung mẫu dùng để xem trước','parameter_map':'Ánh xạ tham số OA (JSON)'}
        widgets={'body':forms.Textarea(attrs={'rows':4}),'parameter_map':forms.Textarea(attrs={'rows':2})}

    def clean_parameter_map(self):
        data=self.cleaned_data.get('parameter_map') or {}
        if not isinstance(data,dict) or any(not isinstance(k,str) or not isinstance(v,str) or v not in VARIABLES for k,v in data.items()):
            raise forms.ValidationError('Ví dụ: {"customer_name":"customer","order_code":"bill"}.')
        return data

    def clean_body(self):
        body=self.cleaned_data['body']
        try: render_body(DepositMessageTemplate(body=body),{k:'Mẫu' for k in VARIABLES})
        except (ValueError,KeyError) as exc: raise forms.ValidationError(str(exc))
        return body


@require_GET
def listing(request):
    D.authorize(request)
    c=PmvClient(tag='datcoc-messages')
    params=request.GET.copy(); params.setdefault('day',timezone.localdate().isoformat())
    form=QueueFilter(params)
    rows=DepositMessage.objects.none()
    suggested=[]; suggestion_error=''
    if form.is_valid():
        try:
            source=W.classify(overlay(W.prepare(W.read_snapshot(c)),c.target))
            suggested=R.suggestions(source,c.target,form.cleaned_data['day'],form.cleaned_data['q'])
        except Exception:
            suggestion_error='Chưa tải được phiếu để gợi ý. Hãy tải lại danh sách.'
        rows=DepositMessage.objects.filter(target=c.target,planned_day=form.cleaned_data['day']).select_related('template').order_by('scheduled_at','id')
        if form.cleaned_data['status']: rows=rows.filter(status=form.cleaned_data['status'])
        else: rows=rows.exclude(status='cancelled')
        if form.cleaned_data['q']:
            from django.db.models import Q
            q=form.cleaned_data['q']; rows=rows.filter(Q(trn_id__icontains=q)|Q(customer_name__icontains=q)|Q(phone__icontains=q))
    pager=Paginator(rows,30).get_page(request.GET.get('page',1))
    for row in pager: row.state_name=STATUSES.get(row.status,row.status)
    return render(request,'pos/_dat_coc_messages.html' if request.headers.get('HX-Request') else 'pos/dat_coc.html',{'message_form':form,'queue':pager,'pager':pager,
        'nav_active':'datcoc','suggestions':suggested,'suggestion_error':suggestion_error,
        'tab':'notifications','tabs':D.O.TABS,'templates':DepositMessageTemplate.objects.all(),
        'can_edit':D.allowed(request.user,'can_edit'),'can_approve':D.allowed(request.user,'can_approve'),
        'oa_connected':bool(getattr(settings,'DATCOC_OA_ACCESS_TOKEN',''))})


@require_http_methods(['GET','POST'])
def suggestion_edit(request, kind):
    if kind not in ('add','remove'): raise Http404
    D.authorize(request,'can_edit')
    c=PmvClient(tag='datcoc-reminder-list')
    rows=W.classify(overlay(W.prepare(W.read_snapshot(c,refresh=True)),c.target))
    active={r['TrnID']:r for r in rows if r['active']}
    params=request.POST if request.method=='POST' else request.GET
    q=params.get('q','').strip().casefold()
    choices=[(pk,f"{pk} · {r.get('CustName') or ''} · {r.get('Phone') or ''}") for pk,r in active.items()
             if (kind=='remove' and pk==params.get('receipt')) or (kind=='add' and (not q or q in f"{pk} {r.get('CustName')} {r.get('Phone')}".casefold()))]
    # On POST validate against all current active receipts, not just the search page.
    if request.method=='POST': choices=[(pk,pk) for pk in active]
    class SuggestionForm(forms.Form):
        day=forms.DateField(widget=forms.HiddenInput,initial=params.get('day') or timezone.localdate())
        orders=forms.MultipleChoiceField(label='Chọn phiếu', choices=choices[:200] if request.method=='GET' else choices,
                                         widget=forms.CheckboxSelectMultiple, initial=[params.get('receipt')] if kind=='remove' else [])
    form=SuggestionForm(request.POST or None)
    ctx={'title':'Thêm phiếu nhắc' if kind=='add' else 'Bỏ phiếu khỏi gợi ý ngày này','form':form,
         'reminder_search':kind=='add','search_query':params.get('q',''),'search_day':params.get('day',''),
         'token':params.get('token') or signing.dumps({'target':c.target,'kind':kind},salt='dc-suggestion')}
    if request.method=='POST' and form.is_valid():
        try:
            if signing.loads(ctx['token'],salt='dc-suggestion',max_age=7200)!={'target':c.target,'kind':kind}: raise ValueError('Phiên dữ liệu đã thay đổi.')
            if len(form.cleaned_data['orders'])>200: raise ValueError('Mỗi lượt tối đa 200 phiếu.')
            with transaction.atomic():
                for pk in form.cleaned_data['orders']:
                    DepositEvent.objects.create(target=c.target,trn_id=pk,action='reminder_'+kind,
                        username=request.user.username,token=uuid.uuid4().hex,data={'day':form.cleaned_data['day'].isoformat()})
            return saved('Đã thêm phiếu nhắc.' if kind=='add' else 'Đã bỏ khỏi gợi ý ngày đã chọn.')
        except (ValueError,signing.BadSignature) as exc: ctx['error']=str(exc)
    return render(request,'pos/_dat_coc_action.html',ctx)


@require_http_methods(['GET','POST'])
def template_edit(request, pk=0):
    D.authorize(request,'can_approve')
    template=DepositMessageTemplate.objects.filter(pk=pk).first() if pk else DepositMessageTemplate()
    if template is None: raise Http404
    form=TemplateForm(request.POST or None,instance=template)
    ctx={'form':form,'title':'Mẫu thông báo OA','pk':str(pk),'kind':'template'}
    ctx['token']=request.POST.get('token') if request.method=='POST' else signing.dumps({'pk':pk,'version':template.version},salt='dc-template')
    if request.method=='POST' and form.is_valid():
        try:
            stamp=signing.loads(ctx['token'],salt='dc-template',max_age=7200)
            if stamp['pk']!=pk: raise ValueError('Phiên kiểm tra không đúng mẫu.')
            with transaction.atomic():
                fresh=DepositMessageTemplate.objects.select_for_update().filter(pk=pk).first() if pk else None
                if stamp!={'pk':pk,'version':fresh.version if fresh else 1}: raise ValueError('Mẫu vừa được sửa. Mở lại popup.')
                obj=form.save(commit=False); obj.status='draft'; obj.approved_body_hash=''; obj.version=(fresh.version+1) if fresh else 1; obj.save()
            return saved('Đã lưu mẫu nháp. Cần kiểm tra duyệt OA trước khi gửi.')
        except (ValueError,signing.BadSignature) as exc: ctx['error']=str(exc)
    return render(request,'pos/_dat_coc_action.html',ctx)


def saved(message,tone='success'):
    response=HttpResponse(''); response['HX-Trigger']=json.dumps({'depositSaved':{'message':message,'tone':tone}}); return response


@require_http_methods(['GET','POST'])
def approve_template(request,pk):
    D.authorize(request,'can_approve')
    template=DepositMessageTemplate.objects.filter(pk=pk).first()
    if not template: raise Http404
    ctx={'title':'Kiểm tra mẫu đã được OA duyệt','pk':str(pk),'form':forms.Form(request.POST or None)}
    ctx['token']=request.POST.get('token') if request.method=='POST' else signing.dumps({'pk':pk,'version':template.version},salt='dc-template')
    if request.method=='POST':
        try:
            stamp=signing.loads(ctx['token'],salt='dc-template',max_age=7200)
            if stamp.get('pk')!=pk: raise ValueError('Phiên kiểm tra không đúng mẫu.')
            info=provider_info(template)
            if any(p.get('require') and p['name'] not in template.parameter_map for p in info.get('listParams',[])):
                raise ValueError('Ánh xạ chưa đủ tham số bắt buộc của mẫu OA.')
            count=DepositMessageTemplate.objects.filter(pk=pk,version=stamp['version']).update(status='approved',approved_body_hash=digest(template.body))
            if not count: raise ValueError('Mẫu vừa được thay đổi. Kiểm tra lại.')
            # Không tự gửi các tin đã quá lịch trong lúc chờ duyệt; người dùng lên lịch lại.
            return saved('OA xác nhận mẫu đang được phép gửi. Có thể lên lịch các tin đã soạn.')
        except Exception as exc: ctx['error']=str(exc) if isinstance(exc,(ValueError,signing.BadSignature)) else 'Không kiểm tra được OA. Mẫu chưa được kích hoạt.'
    return render(request,'pos/_dat_coc_action.html',ctx)


@require_http_methods(['GET','POST'])
def compose(request):
    D.authorize(request,'can_edit')
    c=PmvClient(tag='datcoc-compose'); snapshot=W.read_snapshot(c,refresh=request.method=='POST')
    rows=W.classify(overlay(W.prepare(snapshot),c.target))
    day_text=request.POST.get('day') or request.GET.get('day') or timezone.localdate().isoformat()
    try: day=dt.date.fromisoformat(day_text)
    except ValueError: day=timezone.localdate()
    eligible=[r for r in rows if r['active']]
    selected=request.POST.getlist('orders') if request.method=='POST' else request.GET.getlist('orders')
    if request.method=='GET' and not selected:
        selected=[r['TrnID'] for r in R.suggestions(rows,c.target,day)]
    eligible=[r for r in eligible if r['TrnID'] in selected]
    selected_day=day
    class ComposeForm(forms.Form):
        day=forms.DateField(label='Ngày trong lịch gửi',initial=selected_day,widget=forms.DateInput(attrs={'type':'date'},format='%Y-%m-%d'))
        template=forms.ModelChoiceField(label='Mẫu tin',queryset=DepositMessageTemplate.objects.all())
        scheduled_at=forms.DateTimeField(label='Giờ gửi chung (để trống: lưu bản soạn)',required=False,
            widget=forms.DateTimeInput(attrs={'type':'datetime-local'},format='%Y-%m-%dT%H:%M'))
        orders=forms.MultipleChoiceField(label='Phiếu đã chọn',choices=[(r['TrnID'],f"{r.get('CustName') or ''} · {r['TrnID']} · {r.get('Phone') or 'Chưa có SĐT'}") for r in eligible],widget=forms.CheckboxSelectMultiple,initial=selected)
    form=ComposeForm(request.POST or None)
    form.fields['template'].label_from_instance=lambda obj: f"{obj.name} · {'OA đã duyệt' if obj.status=='approved' else 'Chờ OA duyệt'}"
    ctx={'title':'Soạn cùng một mẫu OA','pk':'','form':form,'submit_label':'Xem trước',
         'token':request.POST.get('token') or signing.dumps({'target':c.target,'nonce':uuid.uuid4().hex},salt='dc-compose')}
    if request.method=='POST' and form.is_valid():
        try:
            if signing.loads(ctx['token'],salt='dc-compose',max_age=7200)['target']!=c.target: raise ValueError('Đích dữ liệu đã thay đổi.')
            if len(form.cleaned_data['orders'])>200: raise ValueError('Mỗi lượt tối đa 200 phiếu.')
            template=form.cleaned_data['template']; planned=form.cleaned_data['day']; count=0
            scheduled=form.cleaned_data['scheduled_at']
            if scheduled:
                D.authorize(request,'can_approve')
                if scheduled<=timezone.now(): raise ValueError('Chọn giờ gửi trong tương lai.')
                if timezone.localtime(scheduled).date()!=planned: raise ValueError('Giờ gửi phải thuộc ngày đã chọn.')
                if not 8<=timezone.localtime(scheduled).hour<21: raise ValueError('Chọn giờ gửi từ 08:00 đến trước 21:00.')
            prepared=[]
            for r in eligible:
                if r['TrnID'] not in form.cleaned_data['orders']: continue
                params={'customer':r.get('CustName') or 'Quý khách','bill':r['TrnID'],
                        'promise':r['promise_date'].strftime('%d/%m/%Y') if r['promise_date'] else 'xin liên hệ tiệm',
                        'deposit':format(r.get('TienCoc') or 0,',.0f').replace(',','.'),'store':'KIM HẠNH 2'}
                phone=phone_number(r.get('Phone')) if scheduled else r.get('Phone') or ''
                prepared.append({'row':r,'params':params,'phone':phone,'body':render_body(template,params)})
            signature=digest(json.dumps({'target':c.target,'day':str(planned),'at':str(scheduled),
                'template':[template.pk,template.version,template.status,template.body],
                'messages':[(p['row']['TrnID'],p['phone'],p['body']) for p in prepared]},ensure_ascii=False))
            confirmation=request.POST.get('confirmation','')
            if not confirmation or signing.loads(confirmation,salt='dc-compose-preview',max_age=7200)!=signature:
                ctx.update(previews=prepared,confirmation=signing.dumps(signature,salt='dc-compose-preview'),
                           submit_label='Xác nhận lên lịch' if scheduled else 'Lưu bản soạn')
                return render(request,'pos/_dat_coc_action.html',ctx)
            with transaction.atomic():
                # Serialise batch composition across templates and users.
                list(DepositMessageTemplate.objects.select_for_update().order_by('pk').values_list('pk',flat=True))
                fresh_template=DepositMessageTemplate.objects.get(pk=template.pk)
                if (fresh_template.version,fresh_template.status)!=(template.version,template.status): raise ValueError('Mẫu vừa thay đổi. Xem trước lại.')
                for prepared_row in prepared:
                    r=prepared_row['row']; params=prepared_row['params']; body=prepared_row['body']
                    existing=DepositMessage.objects.filter(target=c.target,trn_id=r['TrnID'])
                    if existing.filter(status__in=R.PENDING).exists() or existing.filter(status='sent',planned_day=planned).exists(): continue
                    key=digest(f'{c.target}|{r["TrnID"]}|{planned}|{template.pk}')
                    _,created=DepositMessage.objects.get_or_create(dedupe_key=key,defaults={'target':c.target,'trn_id':r['TrnID'],
                        'customer_name':r.get('CustName') or '', 'phone':prepared_row['phone'], 'template':template,
                        'template_version':template.version,'body':body,'parameters':params,'planned_day':planned,
                        'scheduled_at':scheduled,
                        'status':('scheduled' if scheduled else 'draft') if template.status=='approved' else 'waiting_template','purpose':'ready' if r['fulfilment']=='ready' else 'appointment','username':request.user.username})
                    count+=created
            return saved(f'Đã lưu {count} tin mới. Phiếu đã có tin chờ hoặc đã gửi trong ngày được bỏ qua.' + (' Mẫu chưa được OA duyệt: tin tiếp tục chờ.' if template.status!='approved' else ''))
        except (ValueError,signing.BadSignature) as exc: ctx['error']=str(exc)
    return render(request,'pos/_dat_coc_action.html',ctx)


@require_http_methods(['GET','POST'])
def edit(request,pk,kind):
    if kind not in ('edit','schedule','delete'): raise Http404
    D.authorize(request,'can_approve' if kind=='schedule' else 'can_edit')
    target=PmvClient().target
    message=DepositMessage.objects.select_related('template').filter(pk=pk,target=target).first()
    if not message: raise Http404
    class EditForm(forms.Form):
        phone=forms.CharField(label='SĐT khách',max_length=30,initial=message.phone)
        planned_day=forms.DateField(label='Ngày trong lịch gửi',initial=message.planned_day,widget=forms.DateInput(attrs={'type':'date'},format='%Y-%m-%d'))
        scheduled_at=forms.DateTimeField(label='Giờ gửi',required=kind=='schedule',initial=message.scheduled_at,widget=forms.DateTimeInput(attrs={'type':'datetime-local'},format='%Y-%m-%dT%H:%M'))
    form=EditForm(request.POST or None) if kind!='delete' else forms.Form(request.POST or None)
    if kind!='delete':
        for key,label in [('customer','Tên khách trong tin'),('bill','Mã phiếu'),('promise','Ngày hẹn trong tin'),('deposit','Tiền cọc trong tin'),('store','Tên cửa hàng')]:
            form.fields[key]=forms.CharField(label=label,max_length=1000,initial=message.parameters.get(key,''))
    ctx={'title':{'edit':'Sửa tin đã soạn','schedule':'Lên lịch gửi','delete':'Xóa tin khỏi danh sách chờ'}[kind],
         'pk':message.trn_id,'form':form,'preview':message.body,'token':request.POST.get('token') or signing.dumps({'id':pk,'target':target,'version':message.version},salt='dc-message')}
    if request.method=='POST' and form.is_valid():
        try:
            stamp=signing.loads(ctx['token'],salt='dc-message',max_age=7200)
            with transaction.atomic():
                fresh=DepositMessage.objects.select_for_update().get(pk=pk,target=target)
                if stamp!={'id':pk,'target':target,'version':fresh.version}: raise ValueError('Tin vừa thay đổi. Mở lại popup.')
                if fresh.status in ('sending','sent','uncertain','cancelled'): raise ValueError('Không sửa/xóa tin đã gửi, đang gửi hoặc chưa rõ kết quả.')
                before={'body':fresh.body,'status':fresh.status,'scheduled_at':str(fresh.scheduled_at)}
                if kind=='delete': fresh.status='cancelled'; fresh.dedupe_key=None
                else:
                    data=form.cleaned_data
                    fresh.phone=phone_number(data['phone']); fresh.parameters={k:data[k] for k in VARIABLES}
                    fresh.body=render_body(message.template,fresh.parameters); fresh.template_version=message.template.version
                    fresh.planned_day=data['planned_day']; fresh.scheduled_at=data['scheduled_at']
                    if kind=='schedule':
                        if not fresh.scheduled_at or fresh.scheduled_at<=timezone.now(): raise ValueError('Chọn giờ gửi trong tương lai.')
                        if timezone.localtime(fresh.scheduled_at).date()!=fresh.planned_day: raise ValueError('Giờ gửi phải thuộc ngày đã chọn.')
                        if not (8<=timezone.localtime(fresh.scheduled_at).hour<21): raise ValueError('Chọn giờ gửi từ 08:00 đến trước 21:00.')
                        fresh.status='scheduled' if message.template.status=='approved' else 'waiting_template'
                    else: fresh.status='draft' if message.template.status=='approved' else 'waiting_template'
                    fresh.dedupe_key=digest(f'{target}|{fresh.trn_id}|{fresh.planned_day}|{fresh.template_id}')
                fresh.username=request.user.username; fresh.version+=1; fresh.error=''; fresh.save()
                DepositEvent.objects.create(target=target,trn_id=fresh.trn_id,action='message_'+kind,username=request.user.username,
                    token=digest(ctx['token']),data={'message_id':pk,'before':before,'after':{'status':fresh.status,'body':fresh.body,'scheduled_at':str(fresh.scheduled_at)}})
            return saved('Đã cập nhật tin nhắn.' + (' Mẫu OA chưa duyệt: tin tiếp tục chờ.' if fresh.status=='waiting_template' else ''))
        except (ValueError,signing.BadSignature) as exc: ctx['error']=str(exc)
        except Exception: ctx['error']='Không cập nhật được; có thể đã có tin cùng phiếu/ngày/mẫu. Mở lại danh sách.'
    return render(request,'pos/_dat_coc_action.html',ctx)


def send_due(target):
    now=timezone.now()
    R.sync_descriptions(target)
    # Tiến trình chết khi đang gọi OA: không phát lại tự động.
    DepositMessage.objects.filter(target=target,status='sending',claimed_at__lt=now-dt.timedelta(minutes=5)).update(status='uncertain',error='Tiến trình gửi bị gián đoạn; kiểm tra OA bằng tracking_id.')
    if target!='kk' or not getattr(settings,'DATCOC_OA_ACCESS_TOKEN',''): return 0
    count=0
    for pk in DepositMessage.objects.filter(target=target,status='scheduled',scheduled_at__lte=now).values_list('pk',flat=True)[:30]:
        if DepositMessage.objects.filter(pk=pk,status='scheduled').update(status='sending',claimed_at=now)!=1: continue
        message=DepositMessage.objects.select_related('template').get(pk=pk)
        attempted=False
        try:
            from django.contrib.auth import get_user_model
            user=get_user_model().objects.filter(username=message.username,is_active=True).first()
            if not user or not D.allowed(user,'can_approve'): raise ValueError('Người lên lịch không còn quyền duyệt gửi.')
            if message.scheduled_at<now-dt.timedelta(hours=2): raise ValueError('Lịch gửi đã quá 2 giờ; cần lên lịch lại.')
            local=timezone.localtime(now)
            if not 8<=local.hour<21: raise ValueError('Ngoài giờ gửi 08:00–21:00.')
            template=message.template
            if template.status!='approved' or template.version!=message.template_version or template.approved_body_hash!=digest(template.body):
                raise ValueError('Mẫu đã thay đổi hoặc chưa được duyệt; cần kiểm tra lại tin.')
            c=PmvClient(target,tag='datcoc-oa-send'); h=D.header(c,message.trn_id)
            if not h: raise ValueError('Phiếu không còn tồn tại.')
            current=W.classify(overlay(W.prepare({'headers':[h],'items':{h['TrnID']:[D.O.item_info(i) for i in D.lines(c,h['TrnID'])]}}),target))[0]
            if not current['active']: raise ValueError('Phiếu đã giao/hủy. Dừng nhắc khách.')
            if message.purpose=='ready' and current['fulfilment']!='ready': raise ValueError('Hàng chưa sẵn sàng hoặc tiến độ đã thay đổi.')
            promise=current['promise_date'].strftime('%d/%m/%Y') if current['promise_date'] else 'xin liên hệ tiệm'
            if promise!=message.parameters.get('promise'): raise ValueError('Ngày hẹn đã đổi. Cần soạn lại nội dung.')
            if phone_number(h.get('Phone'))!=message.phone: raise ValueError('SĐT trên phiếu đã đổi. Kiểm tra người nhận.')
            info=provider_info(template); payload=validate_provider_parameters(template,message,info)
            attempted=True
            result=oa_request('POST','https://business.openapi.zalo.me/message/template',settings.DATCOC_OA_ACCESS_TOKEN,
                {'phone':message.phone,'template_id':template.provider_id,'template_data':payload,'tracking_id':str(message.tracking_id)})
            if result.get('error')!=0:
                message.status='failed'; message.error='OA từ chối: '+str(result.get('error'))
            elif not result.get('data',{}).get('msg_id'):
                message.status='uncertain'; message.error='OA chưa trả mã tin; kiểm tra tracking_id.'
            else:
                message.status='sent'; message.sent_at=timezone.now(); message.provider_message_id=str(result['data']['msg_id']); count+=1
        except Exception as exc:
            message.status='uncertain' if attempted else 'blocked'
            message.error=str(exc) if isinstance(exc,ValueError) else 'Không xác định kết quả kết nối OA. Không tự gửi lại.'
        with transaction.atomic():
            message.version+=1; message.save()
            DepositEvent.objects.create(target=target,trn_id=message.trn_id,action='message_result',username='scheduler',
                token=uuid.uuid4().hex,note=STATUSES[message.status],data={'message_id':message.pk,'tracking_id':str(message.tracking_id),
                'provider_message_id':message.provider_message_id,'error':message.error})
            if message.status=='sent': R.record_sent(message,current)
    return count
