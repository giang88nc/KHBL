"""Bốn ảnh của phiếu đặt lưu BLOB trong gold_bill, đọc qua quyền ĐẶT-CỌC."""
from io import BytesIO
from pathlib import Path
from PIL import Image, ImageOps, UnidentifiedImageError
from django.conf import settings
from django.core.files.storage import FileSystemStorage
from django.http import FileResponse, Http404
from django.urls import reverse
from django.views.decorators.http import require_GET, require_http_methods

SLOTS={'sample1':'Hình mẫu 1','sample2':'Hình mẫu 2','finished1':'Thành phẩm 1','finished2':'Thành phẩm 2'}


def storage():
    return FileSystemStorage(location=Path(settings.BASE_DIR)/'media'/'datcoc')


def context(state,pk):
    return [{'key':key,'label':label,'url':reverse('pos:dat_coc_photo',args=[pk,key]) if pk and state and state.photos.get(key) else ''}
            for key,label in SLOTS.items()]


def prepare(files):
    result={}
    for slot,label in SLOTS.items():
        upload=files.get('photo_'+slot)
        if not upload: continue
        if upload.size>10*1024*1024: raise ValueError(label+': tối đa 10 MB mỗi ảnh.')
        try:
            img=Image.open(upload)
            if img.width*img.height>25000000: raise ValueError(label+': ảnh quá lớn, chọn ảnh dưới 25 megapixel.')
            img.load(); img=ImageOps.exif_transpose(img)
            rgba=img.convert('RGBA'); img=Image.new('RGB',rgba.size,'white'); img.paste(rgba,mask=rgba.getchannel('A'))
            img.thumbnail((2000,2000))
            out=BytesIO(); img.save(out,format='JPEG',quality=88,optimize=True)
            result[slot]=out.getvalue()
        except (UnidentifiedImageError,OSError,Image.DecompressionBombError) as exc:
            raise ValueError(label+': chọn ảnh JPEG, PNG hoặc WebP hợp lệ.') from exc
    return result


def save(state,prepared,post):
    photos=dict(state.photos)
    for slot in SLOTS:
        if post.get('remove_photo_'+slot)=='1':
            photos.pop(slot,None)
            setattr(state, 'dc_photo_'+slot, None)
        if slot in prepared:
            setattr(state, 'dc_photo_'+slot, prepared[slot])
            photos[slot]='db'
    state.photos=photos
    state.save()


@require_GET
def photo(request,pk,slot):
    from . import deposits as D
    from .deposit_models import DepositOrderState
    from apps.pmv.client import PmvClient
    D.authorize(request)
    if slot not in SLOTS: raise Http404
    c=PmvClient(tag='datcoc-photo')
    state=DepositOrderState.objects.filter(target=c.target,trn_id=pk).first()
    name=(state.photos if state else {}).get(slot)
    if name == 'db':
        data = getattr(state, 'dc_photo_'+slot)
        if not data: raise Http404
        response = FileResponse(BytesIO(bytes(data)),content_type='image/jpeg')
        response['Cache-Control']='private, no-store'
        response['X-Content-Type-Options']='nosniff'
        return response
    if not name or not name.startswith(c.target+'/') or not storage().exists(name): raise Http404
    response=FileResponse(storage().open(name,'rb'),content_type='image/jpeg')
    response['Cache-Control']='private, no-store'; response['X-Content-Type-Options']='nosniff'
    return response


@require_http_methods(['GET','POST'])
def edit(request,pk):
    from django.core import signing
    from django.db import transaction
    from django.shortcuts import render
    from apps.pmv.client import PmvClient
    from . import deposits as D, deposit_operations as O
    from .deposit_models import DepositOrderState
    D.authorize(request,'can_edit')
    c=PmvClient(tag='datcoc-photos'); h=D.header(c,pk)
    if not h: raise Http404
    state=DepositOrderState.objects.filter(target=c.target,trn_id=pk).first()
    stamp={'target':c.target,'id':pk,'version':state.version if state else 0}
    ctx={'pk':pk,'photos':context(state,pk),'form':True,'title':'Hình mẫu & thành phẩm',
         'token':request.POST.get('token') if request.method=='POST' else signing.dumps(stamp,salt='dc-photos')}
    if request.method=='POST':
        try:
            if signing.loads(ctx['token'],salt='dc-photos',max_age=7200)!=stamp: raise ValueError('Phiếu đã đổi. Mở lại hình ảnh trước khi lưu.')
            prepared=prepare(request.FILES)
            with transaction.atomic():
                locked=DepositOrderState.objects.select_for_update().filter(target=c.target,trn_id=pk).first()
                if (locked.version if locked else 0)!=stamp['version']: raise ValueError('Phiếu vừa được cập nhật. Mở lại để lưu ảnh.')
                locked=locked or O.base_state(c,h,[D.O.item_info(r) for r in D.lines(c,pk)])
                locked.version+=1; save(locked,prepared,request.POST)
            from .deposit_messages import saved
            return saved('Đã lưu hình mẫu và hình thành phẩm.')
        except signing.BadSignature: ctx['error']='Phiên ảnh đã hết hạn. Mở lại popup.'
        except ValueError as exc: ctx['error']=str(exc)
        except Exception:
            import logging
            logging.getLogger(__name__).exception('Không lưu được ảnh phiếu %s',pk)
            ctx['error']='Chưa lưu được hình ảnh. Kiểm tra kết nối và thử lại.'
    return render(request,'pos/_dat_coc_photo_popup.html',ctx)
