"""Document contact snapshots. No writes to I_CUSTOMER or financial tables."""
import re
import uuid
from django.db import transaction, IntegrityError
from .models import DocumentContact, DocumentContactWrite

TYPES = {'KHBL_BUYSELL', 'KHBL_BUYGOLD', 'KHBL_DEPOSIT', 'KHCD_LOAN'}


def normalize_phone(value, required=False):
    raw = str(value or '').strip()
    if not raw and not required:
        return ''
    if not re.fullmatch(r'[+0-9 .()-]+', raw):
        raise ValueError('SĐT trên phiếu chỉ được gồm một số điện thoại.')
    phone = re.sub(r'[^0-9]', '', raw)
    if phone.startswith('84'):
        phone = '0' + phone[2:]
    if not re.fullmatch(r'0[0-9]{9,10}', phone):
        raise ValueError('SĐT trên phiếu phải có 10–11 số, bắt đầu bằng 0.')
    return phone


def payload(cust_id, name, phone, actor=''):
    values = dict(cust_id=str(cust_id or ''), customer_name=str(name or ''),
                  phone=normalize_phone(phone), updated_by=str(actor or ''))
    for key, limit in [('cust_id',55),('customer_name',255),('updated_by',150)]:
        if len(values[key]) > limit:
            raise ValueError('Thông tin liên hệ vượt giới hạn: '+key)
    return values


def read(kind, source_id, cust_id=None):
    if DocumentContactWrite.objects.filter(source_type=kind,source_id=str(source_id),target='kk',status='pending').exists():
        raise ValueError('Liên hệ phiếu đang chờ đối soát; chưa in/gửi SMS: '+str(source_id))
    row = DocumentContact.objects.filter(source_type=kind, source_id=str(source_id)).first()
    if row and cust_id is not None and row.cust_id != str(cust_id or ''):
        raise ValueError('CustID trên phiếu khác bản liên hệ đã lưu; cần đối soát trước khi in/gửi SMS.')
    return row


def overlay(kind, row, target='kk'):
    if not row or target != 'kk':
        return row
    contact = read(kind, row['TrnID'], row.get('CustID'))
    if contact:
        row = dict(row, Phone=contact.phone, CustName=contact.customer_name, contact_source='document')
    else:
        row = dict(row, contact_source='customer_fallback')
    return row


def overlay_many(kind, rows, target='kk'):
    if target != 'kk':
        return rows
    if DocumentContactWrite.objects.filter(source_type=kind,source_id__in=[str(r['TrnID']) for r in rows],target='kk',status='pending').exists():
        raise ValueError('Có liên hệ phiếu đang chờ đối soát; chưa gửi SMS.')
    contacts = {r.source_id:r for r in DocumentContact.objects.filter(source_type=kind, source_id__in=[str(r['TrnID']) for r in rows])}
    result=[]
    for row in rows:
        saved=contacts.get(str(row['TrnID']))
        if saved:
            if 'CustID' in row and saved.cust_id != str(row.get('CustID') or ''):
                raise ValueError('CustID không khớp liên hệ phiếu '+str(row['TrnID']))
            row=dict(row,Phone=saved.phone,CustName=saved.customer_name,contact_source='document')
        result.append(row)
    return result


def begin(kind, token, contact, target='kk', source_id=''):
    if kind not in TYPES or not token or len(token)>64:
        raise ValueError('Khóa lưu liên hệ không hợp lệ.')
    source_id=str(source_id or '')
    active=target+':'+kind+':'+(source_id or 'request:'+token)
    try:
        with transaction.atomic():
            previous=DocumentContactWrite.objects.select_for_update().filter(token=token).first()
            if previous:
                if not (previous.status=='done' and source_id and source_id==previous.source_id and previous.source_type==kind and previous.target==target):
                    raise ValueError('Yêu cầu đã gửi trước đó. Kiểm tra phiếu và bản liên hệ; không tạo lại tự động.')
                token=uuid.uuid4().hex
            return DocumentContactWrite.objects.create(token=token,source_type=kind,source_id=source_id,
                target=target,snapshot=contact,active_key=active)
    except IntegrityError as exc:
        raise ValueError('Phiếu đang có yêu cầu liên hệ chưa hoàn tất. Đối soát trước khi lưu lại.') from exc


def bind(intent, source_id):
    # Keep returned ID even if assigning the per-document lock fails afterwards.
    intent.source_id=str(source_id)
    intent.save(update_fields=['source_id','updated_at'])
    active=intent.target+':'+intent.source_type+':'+intent.source_id
    try:
        with transaction.atomic():
            DocumentContactWrite.objects.filter(pk=intent.pk,status='pending').update(active_key=active)
    except IntegrityError as exc:
        raise ValueError('PMV đã trả mã phiếu nhưng liên hệ đang bị khóa; cần đối soát, không tạo phiếu lại.') from exc


def finish(intent, document_code=''):
    if not intent.source_id:
        raise ValueError('Chưa xác định mã phiếu cho liên hệ.')
    with transaction.atomic():
        locked=DocumentContactWrite.objects.select_for_update().get(pk=intent.pk)
        if locked.status=='done': return
        if DocumentContactWrite.objects.filter(source_type=locked.source_type,source_id=locked.source_id,target=locked.target,status='done',id__gt=locked.id).exists():
            raise ValueError('Đã có liên hệ mới hơn; không ghi đè bằng yêu cầu cũ.')
        if locked.target=='kk':
            DocumentContact.objects.update_or_create(source_type=locked.source_type,source_id=locked.source_id,
                defaults=dict(locked.snapshot,document_code=str(document_code or '')))
        locked.status='done';locked.active_key=None;locked.save(update_fields=['status','active_key','updated_at'])


def save_cart(kind, g, actor):
    cust=g.get('cust') or {}
    return payload(cust.get('id') or 'CU0000000000000',cust.get('name'),cust.get('phone'),actor)


def start(value, kind, target, source_id):
    """Stage after local validations, immediately before the first PMV write."""
    if isinstance(value,dict):
        return begin(kind,value['token'],value['contact'],target,source_id)
    return value
