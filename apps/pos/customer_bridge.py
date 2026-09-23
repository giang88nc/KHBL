"""Private authenticated customer service for KHCD. No generic SQL/proc interface.

Run separately from the KHBL website. All PMV traffic uses its existing gateway.
No cookies, passwords, or target-selection supplied by browser clients.
"""
import hashlib
import hmac
import json
import re
import threading
import time
from django.contrib.auth import get_user_model
from django.core.serializers.json import DjangoJSONEncoder
from django.db import close_old_connections
from apps.pmv.client import PmvClient
from . import customer as C, customer_phones as P
from .customer_bridge_models import CustomerBridgeReceipt
from .customer_sync_models import CustomerSyncReceipt

FIELDS = 'CustID,CustCode,CustName,Phone,GhiChu2,GhiChu3,CMND,Address,Active,Gender'   # Gender: KHCD dùng cho xưng hô Anh/Chị trong tin nhắc (18/09/2026)
FORM_FIELDS = ('CustName','Phone','GhiChu2','GhiChu3','CMND','Address','BirthDate','Gender','NgayCap','NoiCap','Email','Notes','CustType','Active')


def form_values(row):
    result={k:row.get(k) or '' for k in FORM_FIELDS}
    result['Gender']='' if row.get('Gender') is None else str(int(row['Gender']))
    result['Active']=str(row.get('Active') or '0')
    result['CustType']=row.get('CustTypeID') or ''
    for key in ('BirthDate','NgayCap'):
        value=row.get(key)
        result[key]=value.strftime('%Y-%m-%d') if hasattr(value,'strftime') else str(value or '')[:10]
    return result


def version(row):
    return hashlib.sha256(json.dumps(row,sort_keys=True,cls=DjangoJSONEncoder).encode()).hexdigest()


def save(payload,user,client,files=None):
    if not C.duoc_sua(user):
        raise PermissionError('Tài khoản chưa có quyền sửa khách hàng trong hệ thống.')
    token=payload.get('token','')
    if not re.fullmatch(r'[A-Za-z0-9_-]{20,80}',token):
        raise ValueError('Mã yêu cầu lưu không hợp lệ.')
    supplied=payload.get('form',{})
    if not isinstance(supplied,dict) or any(not isinstance(v,(str,type(None))) for v in supplied.values()):
        raise ValueError('Biểu mẫu không hợp lệ.')
    try:
        images,image_info=C.prepare_images(files or {})
    except C.CustomerSaveError as error:
        return {'complete':False,'errors':[str(error)],'cust_id':str(supplied.get('CustID') or '')}
    fingerprint_data={'form':supplied,'version':payload.get('version','')}
    if images:
        fingerprint_data['images']={k:hashlib.sha256(v).hexdigest() for k,v in images.items() if isinstance(v,bytes)}
    fingerprint=version(fingerprint_data)
    with C.SAVE_LOCK:
        receipt=CustomerBridgeReceipt.objects.filter(pk=token).first()
        if receipt:
            if (receipt.target,receipt.user_id,receipt.fingerprint)!=(client.target,user.pk,fingerprint):
                raise ValueError('Mã lưu đã dùng với nội dung hoặc tài khoản khác. Mở lại hồ sơ.')
            if receipt.status in ('done','failed','partial'):
                return receipt.result
            return {'complete':False,'cust_id':receipt.cust_id,'uncertain':True,
                    'errors':['Lần lưu trước chưa xác định kết quả. Không tạo lại; kiểm tra biên nhận với quản trị.']}
        cid=str(supplied.get('CustID') or '')
        current=C._current(client,cid) if cid else None
        if cid and not current:raise ValueError('Khách KK không còn tồn tại; không tự tạo khách thay thế.')
        if current and payload.get('version')!=version(current):
            raise ValueError('Hồ sơ KK đã thay đổi. Tải lại hồ sơ trước khi lưu.')
        # Preserve every field not sent by a caller; the full form can explicitly clear a field.
        merged=form_values(current) if current else {'NoiCap':'Cục Cảnh Sát QLHC về TTXH','Active':'1'}
        merged.update({k:v for k,v in supplied.items() if k in FORM_FIELDS and v is not None})
        merged['CustID']=cid
        merged['append_guard']=supplied.get('append_guard') or ''
        data,errors,warnings=C.clean_form(merged)
        if errors:return {'complete':False,'errors':errors,'cust_id':cid}
        # Validation errors before a vendor write are safe to correct and resubmit.
        # The service repeats its own duplicate check under the shared MySQL lock.
        effective=dict(data)
        for column,key in zip(P.COLUMNS,P.KEYS):
            if effective.get(key) is None:effective[key]=P.phone_key((current or {}).get(column))
        try:C.validate_append(data,current)
        except C.CustomerSaveError as error:return {'complete':False,'errors':[str(error)],'cust_id':cid}
        errors=C.duplicate_errors(client,effective)
        if errors:return {'complete':False,'errors':errors,'cust_id':cid}
        receipt=CustomerBridgeReceipt.objects.create(token=token,target=client.target,user_id=user.pk,
            fingerprint=fingerprint,cust_id=cid)
        def created(new_id):
            receipt.cust_id=new_id;receipt.save(update_fields=['cust_id','updated_at'])
        try:
            shops=client.query("SELECT TOP 1 ShopID FROM T_SHOP WITH (NOLOCK) WHERE Active='1' ORDER BY ShopID")
            if not shops:raise C.CustomerSaveError('Chưa xác định tiệm PMV; chưa lưu khách.')
            result=C.upsert(data,images,shop_id=shops[0]['ShopID'],client=client,on_created=created)
            result['warnings']=warnings+result.get('warnings',[])
            result['images']=image_info
            receipt.cust_id=result['cust_id']
            receipt.status='done' if result.get('complete') is True else 'partial'
            result['partial']=receipt.status=='partial'
        except Exception as error:
            # Conservative: a lost vendor response can mean COMMIT succeeded.
            receipt.status='uncertain'
            result={'complete':False,'cust_id':receipt.cust_id,'uncertain':True,
                    'errors':[C.error_message(error),'Giữ mã yêu cầu; kiểm tra kết quả trước khi thử lại.']}
        receipt.result=result;receipt.save()
        return result


def dispatch(payload,user,target):
    action=payload.get('action')
    client=PmvClient(target=target,tag='khcd_customer:'+str(user.pk))
    if action=='pawn_banks':
        from . import vietqr as QR
        rows=QR.active_banks()
        preferred=next((r['id'] for r in rows if str(r.get('type','')).strip().lower()=='pawn'),None)
        return {'rows':rows,'default_id':preferred}
    if action=='pawn_qr':
        from . import vietqr as QR
        import base64
        from django.core.files.uploadedfile import SimpleUploadedFile
        text=str(payload.get('text','')).strip()
        if len(text)>8192:raise ValueError('Mã QR quá dài.')
        if not text and payload.get('image'):
            raw=base64.b64decode(payload['image'],validate=True)
            prepared,_=C.prepare_images({'anh_dai_dien':SimpleUploadedFile('scan.jpg',raw)})
            text=QR.doc_anh(prepared[C.IMAGE_FIELDS['anh_dai_dien'][0]])
        if not text:raise ValueError('Không đọc được QR; quét lại rõ hoặc nhập mã bằng tay.')
        if payload.get('kind')=='receipt':return {'text':text}
        if payload.get('kind')!='bank':raise ValueError('Loại mã quét không hợp lệ.')
        result=QR.parse(text)
        tags=dict(QR._tach_tlv(text));service=dict(QR._tach_tlv(tags.get('38',''))).get('02')
        if not result['hop_le'] or service!='QRIBFTTA':
            raise ValueError(result.get('loi') or 'Chỉ nhận VietQR chuyển khoản tới tài khoản ngân hàng.')
        from .views_thau import _qr_png_b64, _ten_tk_da_biet
        holder=result['ten'] or _ten_tk_da_biet(result['bank_code'],result['account'])
        suggested=False
        if not holder:
            holder=QR.khong_dau(str(payload.get('customer_name') or ''))[:100]
            suggested=bool(holder)
        if result['ten']:
            warning=''
        elif holder and not suggested:
            warning='Tên chủ tài khoản lấy từ giao dịch trước có cùng ngân hàng và số tài khoản. Vui lòng đối chiếu.'
        elif holder:
            warning='QR không có tên chủ tài khoản; đang gợi ý theo tên khách trên phiếu. Vui lòng đối chiếu.'
        else:
            warning='QR không có tên chủ tài khoản. Nhập và đối chiếu tên người nhận.'
        return {'qr_image':_qr_png_b64(text),'bank_name':result['bank_ten'],'bank_code':result['bank_code'],'bank_account':result['account'],
                'bank_holder':holder,'warning':warning,'bank_holder_source':'qr' if result['ten'] else ('history' if holder and not suggested else ('customer' if holder else 'missing'))}
    if action=='employees':
        return {'rows':client.query("SELECT EmpID,EmpName FROM T_EMPLOYEE WITH (NOLOCK) WHERE ISNULL(Active,'1')='1' ORDER BY EmpName,EmpID")}
    if action=='pawn_images':
        if not C.duoc_sua(user):raise PermissionError('Chưa có quyền xử lý ảnh.')
        import base64
        from django.core.files.uploadedfile import SimpleUploadedFile
        supplied=payload.get('files',{})
        if not isinstance(supplied,dict) or set(supplied)-{'anh_truoc','anh_sau','anh_sp1','anh_sp2','anh_qr'}:
            raise ValueError('Danh sách ảnh không hợp lệ.')
        images={}
        for name,encoded in supplied.items():
            raw=base64.b64decode(encoded,validate=True)
            field=name if name in ('anh_truoc','anh_sau') else 'anh_dai_dien'
            prepared,_=C.prepare_images({field:SimpleUploadedFile(name+'.jpg',raw)})
            images[name]=base64.b64encode(prepared[C.IMAGE_FIELDS[field][0]]).decode('ascii')
        return {'images':images}
    if action=='popup':
        from .customer_popup import dispatch as popup_dispatch
        return popup_dispatch(payload,user,client)
    if action=='save':return save(payload,user,client)
    if action=='receipt':
        receipt=CustomerBridgeReceipt.objects.filter(token=str(payload.get('token','')),target=target,user_id=user.pk).first()
        if not receipt:return {'complete':False,'errors':['Chưa có biên nhận. Không gửi yêu cầu tạo mới khi chưa kiểm tra lần lưu trước.']}
        return receipt.result or {'complete':False,'cust_id':receipt.cust_id,'uncertain':True,'errors':['Yêu cầu đang xử lý hoặc chưa xác định kết quả.']}
    if action=='get':
        row=C._current(client,str(payload.get('id','')))
        return {'customer':row,'form':form_values(row) if row else {},'version':version(row) if row else '',
                'can_edit':C.duoc_sua(user)}
    if action=='list':
        q=str(payload.get('q',''))[:100];page=max(1,min(100000,int(payload.get('page',1))))
        where="c.CustID<>?";params=('CU0000000000000',)
        if q:
            clause,values=P.search(q);where+=' AND '+clause;params+=values
        total=client.query('SELECT COUNT(*) n FROM I_CUSTOMER c WITH (NOLOCK) WHERE '+where,params)[0]['n']
        rows=client.query('SELECT * FROM (SELECT '+','.join('c.'+c for c in FIELDS.split(','))+
            ',ROW_NUMBER() OVER (ORDER BY c.CustName,c.CustID) rn FROM I_CUSTOMER c WITH (NOLOCK) WHERE '+where+
            ') numbered WHERE rn>? AND rn<=? ORDER BY rn',params+((page-1)*20,page*20))
        return {'rows':rows,'total':total,'can_edit':C.duoc_sua(user)}
    if action=='search_ids':
        clause,params=P.search(str(payload.get('q',''))[:100])
        rows=client.query('SELECT TOP 1001 c.CustID FROM I_CUSTOMER c WITH (NOLOCK) WHERE '+clause,params)
        if len(rows)>1000:raise ValueError('Có quá nhiều khách phù hợp. Nhập SĐT hoặc mã khách cụ thể hơn.')
        return {'ids':[r['CustID'] for r in rows]}
    if action=='batch':
        ids=payload.get('ids',[])
        if not isinstance(ids,list) or len(ids)>200:raise ValueError('Tối đa 200 khách/lần.')
        if not ids:return {'rows':[]}
        return {'rows':client.query('SELECT '+FIELDS+' FROM I_CUSTOMER WITH (NOLOCK) WHERE CustID IN ('+
            ','.join('?' for _ in ids)+')',tuple(str(x) for x in ids))}
    if action=='directory':
        # Only used during reconciliation; never uses TOP 1 to establish identity.
        rows=client.query('SELECT '+FIELDS+' FROM I_CUSTOMER WITH (NOLOCK)')
        for row in rows:row['phone_keys']=sorted(P.values(row))
        receipts=list(CustomerSyncReceipt.objects.filter(target=target,status='done').values('source_id','cust_id','phone','fingerprint'))
        return {'rows':rows,'receipts':receipts}
    raise ValueError('Thao tác không được hỗ trợ.')


class Application:
    def __init__(self,key,target='kk'):
        self.key=key.encode();self.target=target;self.nonces={};self.lock=threading.Lock()

    def __call__(self,environ,start_response):
        close_old_connections()
        status=200
        try:
            if environ.get('PATH_INFO')=='/health' and environ.get('REQUEST_METHOD')=='GET':
                result={'app':'KHBL Customer Bridge','target':self.target}
            else:
                if environ.get('REMOTE_ADDR') not in ('127.0.0.1','::1') or environ.get('PATH_INFO')!='/v1/customers' or environ.get('REQUEST_METHOD')!='POST':
                    raise PermissionError('Không được truy cập.')
                length=int(environ.get('CONTENT_LENGTH') or 0)
                if not 0<length<88*1024*1024:raise ValueError('Yêu cầu quá lớn hoặc rỗng.')
                body=environ['wsgi.input'].read(length)
                stamp=environ.get('HTTP_X_KHCD_TIME','');nonce=environ.get('HTTP_X_KHCD_NONCE','')
                expected=hmac.new(self.key,stamp.encode()+b'\n'+nonce.encode()+b'\n'+body,hashlib.sha256).hexdigest()
                if abs(time.time()-float(stamp))>60 or not re.fullmatch('[a-f0-9]{32}',nonce) or not hmac.compare_digest(expected,environ.get('HTTP_X_KHCD_SIGNATURE','')):
                    raise PermissionError('Xác thực dịch vụ không hợp lệ.')
                with self.lock:
                    now=time.time();self.nonces={k:v for k,v in self.nonces.items() if now-v<120}
                    if nonce in self.nonces:raise PermissionError('Yêu cầu đã nhận.')
                    self.nonces[nonce]=now
                payload=json.loads(body)
                user=get_user_model().objects.filter(pk=payload.get('actor'),is_active=True).first()
                proof=hmac.new(self.key,(user.password if user else '').encode(),hashlib.sha256).hexdigest()
                if not user or not hmac.compare_digest(proof,payload.get('actor_proof','')) or not C.duoc_xem(user):
                    raise PermissionError('Tài khoản không có quyền khách hàng hoặc phiên đã thay đổi.')
                result=dispatch(payload,user,self.target)
        except PermissionError as error:status=403;result={'error':str(error)}
        except (ValueError,TypeError) as error:status=400;result={'error':str(error)}
        except Exception:
            status=503;result={'error':'Chưa đọc/ghi được nguồn khách KK. Không tự chuyển sang khách CĐ; kiểm tra biên nhận nếu vừa lưu.'}
        finally:close_old_connections()
        body=json.dumps(result,cls=DjangoJSONEncoder,ensure_ascii=False).encode()
        start_response(str(status)+' '+{200:'OK',400:'Bad Request',403:'Forbidden',503:'Service Unavailable'}[status],
            [('Content-Type','application/json; charset=utf-8'),('Content-Length',str(len(body))),('Cache-Control','no-store')])
        return [body]
