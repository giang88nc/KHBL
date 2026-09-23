"""Standalone IN QR: no source invoice, money_flow, till or stock writes."""
import datetime as dt
import hashlib
import re
import uuid
import secrets
from decimal import Decimal
from django.core import signing
from django.contrib.auth import get_user_model
from django.db import transaction, connection, IntegrityError
from django.db.models import Sum
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST, require_http_methods
from .models import MoneyFlowPayment as Payment, MoneyFlowBankReceipt as Receipt
from . import vietqr, money_flow_payment as QR, pawn_bank_reconcile as P
from .mobile_invoice import money
from .quyen import chan

CATEGORIES={'CD':('Cầm đồ','pawn'),'BH':('Bán hàng','cty'),'DC':('Đặt cọc','gold'),'KH':('Khác','gold')}
ALPHABET='ABCDEFGHJKLMNPQRSTUVWXYZ23456789'

def new_token(user):
    # GĐ chốt 22/09/2026: mã QR độc lập = "KHBL" + 10 số giây epoch (skill nhan-dien-ma-chung-tu-ck).
    # 10 số KHÔNG trùng dạng mã HĐ 12 số / mã cầm đồ 14 số nên money_in.codes không nhận nhầm.
    import time
    ts=int(time.time())
    while Payment.objects.filter(standalone_reference=f'KHBL{ts}').exists():ts+=1
    return signing.dumps({'user':user.pk,'nonce':uuid.uuid4().hex,'suffix':str(ts)},salt='standalone-qr')

def own(request):
    qs=Payment.objects.filter(origin='standalone',flow__isnull=True,method='BANK')
    # Employee accounts see only their own manually-created QR.
    if not request.user.is_superuser and not getattr(request,'khbl_mobile_admin',False):
        qs=qs.filter(created_by=request.user.username)
    return qs

@require_GET
def index(request):
    chan(request,'CHUYEN_KHOAN')
    today=timezone.localdate()
    start=timezone.make_aware(dt.datetime.combine(today,dt.time.min))
    end=timezone.make_aware(dt.datetime.combine(today+dt.timedelta(days=1),dt.time.min))
    rows=list(own(request).filter(created_at__gte=start,created_at__lt=end).order_by('-created_at','-pk'))
    names={u.username:u.get_full_name().strip() or u.username for u in
           get_user_model().objects.filter(username__in={r.created_by for r in rows})}
    employee=request.session.get('mobile_employee') or {}
    if employee.get('full_name'):names[request.user.username]=employee['full_name']
    for row in rows:row.creator_name=names.get(row.created_by,row.created_by)
    return render(request,'pos/_mobile_qr_list.html' if request.headers.get('HX-Request') else 'pos/mobile_qr.html',{'page':rows,'mobile_section':'standalone_qr'})

@transaction.atomic
def create(user,token,bank,amount,note,category='KH'):
    data=signing.loads(token,salt='standalone-qr',max_age=1800)
    if data['user']!=user.pk:raise ValueError('Phiên tạo QR không hợp lệ.')
    if category not in CATEGORIES:raise ValueError('Loại QR không hợp lệ.')
    amount=money(amount)
    if amount<=0:raise ValueError('Số tiền phải lớn hơn 0.')
    if len(note)>500:raise ValueError('Ghi chú tối đa 500 ký tự.')
    key=hashlib.sha256(('standalone:'+data['nonce']).encode()).hexdigest()
    # Random letter-only suffix cannot be mistaken for a numeric bill reference.
    content='KH2Q'+''.join(chr(65+int(c,16)) for c in data['nonce'][:20])
    if data.get('suffix'):content=('KHBL'+data['suffix']) if data['suffix'].isdigit() else category+data['suffix']
    payload=vietqr.payload(bank['bank_bin'],bank['bank_number'],amount,content,bank['bank_user'],exact_amount=True)
    if data.get('suffix') and Payment.objects.filter(standalone_reference=content).exclude(qr_key=key).exists():
        raise ValueError('Mã QR vừa trùng với QR khác tạo cùng giây. Đóng và mở lại Thêm QR để lấy mã mới.')
    qr,created=Payment.objects.get_or_create(qr_key=key,defaults=dict(origin='standalone',flow=None,method='BANK',
        amount=amount,bank_code=bank['bank_bin'],bank_name=bank['bank_name'],bank_account=bank['bank_number'],
        bank_owner=bank['bank_user'],transfer_content=content,qr_payload=payload,note=note,
        qr_category=category,standalone_reference=content if data.get('suffix') else None,
        created_by=user.username,updated_by=user.username))
    if not created and (qr.origin!='standalone' or qr.created_by!=user.username or qr.qr_payload!=payload or qr.note!=note):
        raise ValueError('Yêu cầu đã được tạo với thông tin khác. Đóng và mở Thêm QR để tạo yêu cầu mới.')
    return qr

def show(request,qr):
    return render(request,'pos/_mobile_qr_popup.html',{'qr':qr,'qr_img':QR.qr_image(qr.qr_payload)})

@require_http_methods(['GET','POST'])
def new(request):
    chan(request,'CHUYEN_KHOAN','can_edit')
    banks=vietqr.active_banks()
    ctx={'banks':banks,'token':request.POST.get('token') or new_token(request.user),
         'amount':request.POST.get('amount',''),'note':request.POST.get('note',''),'bank_id':request.POST.get('bank_id','')}
    ctx['category']=request.POST.get('category','KH')
    ctx['categories']=[{'code':k,'label':v[0],'bank_type':v[1]} for k,v in CATEGORIES.items()]
    if request.method=='POST':
        try:
            bank=next((b for b in banks if str(b['id'])==ctx['bank_id']),None)
            if not bank:raise ValueError('Chọn tài khoản ngân hàng đang hoạt động.')
            response=show(request,create(request.user,ctx['token'],bank,ctx['amount'],ctx['note'],ctx['category']))
            response['HX-Trigger']='money-flow-reconciled'
            return response
        except (ValueError,signing.BadSignature) as exc:ctx['error']=str(exc) if isinstance(exc,ValueError) else 'Phiên tạo QR hết hạn. Đóng và mở lại.'
        except IntegrityError:ctx['error']='Mã QR bị trùng. Đóng và mở Thêm QR để lấy mã mới.'
    try:
        data=signing.loads(ctx['token'],salt='standalone-qr',max_age=1800)
        ctx['suffix']=data.get('suffix','')
    except signing.BadSignature:ctx['suffix']=''
    ctx['keep_bank']=request.method=='POST'
    return render(request,'pos/_mobile_qr_popup.html',ctx)

@require_GET
def detail(request,pk):
    chan(request,'CHUYEN_KHOAN')
    return show(request,get_object_or_404(own(request),pk=pk))

@transaction.atomic
def reconcile(pk):
    from .money_in import codes
    qr=Payment.objects.select_for_update().get(pk=pk,origin='standalone',flow__isnull=True,method='BANK')
    received=qr.standalone_receipts.aggregate(n=Sum('amount'))['n'] or Decimal(0)
    if qr.status=='success':return {'status':'success','received':str(received)}
    if qr.status!='ready':return {'status':'review','message':'QR không còn sẵn sàng.'}
    start=timezone.localtime(qr.created_at).replace(tzinfo=None)
    end=start+dt.timedelta(hours=3)
    rows=P.query('SELECT id,ref_code,bank_number,bank_name,transaction_time,trans_amount,direction,description,is_check '
        'FROM bank_notifications WHERE direction=%s AND bank_number=%s AND transaction_time>=%s AND transaction_time<=%s '
        'AND description LIKE %s ORDER BY id FOR UPDATE',['in',qr.bank_account,str(start),str(end),'%'+qr.transfer_content+'%'])
    for row in rows:
        description=str(row['description'] or '').upper()
        # regex dùng chung (22/09/2026): dạng MỚI KHBL+10 số; dạng cũ chỉ còn để QR đã phát (cửa sổ 3 giờ) vẫn đối soát được
        from .ma_chung_tu_ck import QR_MOI, QR_DOC_LAP
        refs=set(QR_MOI.findall(description))|set(QR_DOC_LAP.findall(description))
        if refs!={qr.transfer_content} or codes(description):continue
        if str(row['direction']).lower()!='in' or str(row['bank_number'])!=qr.bank_account:continue
        if not P.same_bank(row['bank_name'],qr.bank_code):continue
        stamp=dt.datetime.fromisoformat(str(row['transaction_time']))
        if stamp.tzinfo:stamp=timezone.localtime(stamp).replace(tzinfo=None)
        if not start<=stamp<=end:continue
        identity=P.identity(row)
        if Receipt.objects.filter(payment=qr,notification_id=row['id']).exists():continue
        if row['is_check'] or Receipt.objects.filter(notification_id=row['id']).exists() or Receipt.objects.filter(bank_identity=identity).exists():continue
        amount=money(row['trans_amount'])
        if amount<=0:continue
        if received+amount>qr.amount:return {'status':'review','message':'Tiền nhận vượt yêu cầu; cần kiểm tra, không tự phân bổ.'}
        Receipt.objects.create(payment=qr,flow=None,notification_id=row['id'],bank_identity=identity,amount=amount,
            bill_code=qr.transfer_content,source_system='MANUAL',direction='IN',bank_account=qr.bank_account,
            bank_ref=row['ref_code'] or '',evidence={k:str(v) for k,v in row.items()})
        with connection.cursor() as cur:
            cur.execute('UPDATE bank_notifications SET is_check=1 WHERE id=%s AND is_check=0',[row['id']])
            if cur.rowcount!=1:raise ValueError('Chứng từ ngân hàng vừa được xử lý. Vui lòng kiểm tra lại.')
        received+=amount
    if received==qr.amount:
        qr.status='success';qr.save(update_fields=['status','updated_at'])
        return {'status':'success','received':str(received)}
    if timezone.localtime().replace(tzinfo=None)>end:
        return {'status':'review','message':'Ngoài thời gian tự đối soát 3 giờ. QR ngân hàng vẫn có thể chuyển tiền; cần kiểm tra thủ công.'}
    return {'status':'partial' if received else 'waiting','received':str(received)}

@require_POST
def check(request,pk):
    chan(request,'CHUYEN_KHOAN','can_edit')
    qr=get_object_or_404(own(request),pk=pk)
    try:result=reconcile(qr.pk)
    except ValueError as exc:result={'status':'review','message':str(exc)}
    response=JsonResponse(result);response['Cache-Control']='no-store';return response
