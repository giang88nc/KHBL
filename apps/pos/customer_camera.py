"""Tách CCCD theo skill; chỉ RAM trong request, không cache/tệp/hồ sơ ảnh."""
import base64
from io import BytesIO
from types import SimpleNamespace

from django.http import HttpResponse, JsonResponse
from django.shortcuts import render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST
from PIL import Image

from . import anh_cccd as AC, quyen as Q
from .views_thau import _nen_anh, _goc_xoay, _phan_cat


@require_POST
@never_cache
def split_card(request):
    Q.chan(request, 'KHACH_HANG')
    # Raw JPEG avoids multipart upload handlers / temporary files on the server.
    if request.content_type!='image/jpeg':
        return JsonResponse({'error':'Chỉ nhận ảnh JPEG từ popup chụp hình.'},status=415)
    mode=request.GET.get('mode','preview')
    if mode not in ('preview','apply'): return JsonResponse({'error':'Thao tác không hợp lệ.'},status=400)
    data=request.read(350*1024+1)
    if len(data)>350*1024: return JsonResponse({'error':'Ảnh cần được thu gọn trong popup trước khi tách.'},status=413)
    try:
        with Image.open(BytesIO(data)) as image:
            if image.width*image.height>25_000_000: raise ValueError('Ảnh quá lớn.')
            image.verify()
        source=_nen_anh(data,canh=1600,chat_luong=92)
    except Exception:
        return JsonResponse({'error':'Không đọc được ảnh hoặc ảnh vượt 25 megapixel.'},status=422)
    try:
        result=AC.cat_cccd(source,_goc_xoay(request.GET.get('goc')) if mode=='apply' else 0,
                           _phan_cat(SimpleNamespace(POST=request.GET)) if mode=='apply' else None)
    except AC.KhongThayThe as exc:
        return JsonResponse({'error':str(exc)},status=422)
    except Exception:
        return JsonResponse({'error':'Chưa tách được thẻ. Đặt thẻ rõ bốn góc rồi chụp lại.'},status=422)
    # Bound response bytes below Waitress's disk-spooling threshold as well.
    result=_nen_anh(result,canh=AC.CHUAN_W,chat_luong=88,tran=350*1024)
    if len(result)>350*1024: return JsonResponse({'error':'Ảnh quá nhiều nhiễu. Vui lòng chụp lại gần thẻ hơn.'},status=422)
    if mode=='apply': return HttpResponse(result,content_type='image/jpeg')
    source=_nen_anh(source,canh=900,chat_luong=80,tran=128*1024)
    result=_nen_anh(result,canh=900,chat_luong=80,tran=128*1024)
    if max(len(source),len(result))>128*1024: return JsonResponse({'error':'Ảnh xem trước quá nhiều nhiễu. Vui lòng chụp lại.'},status=422)
    encode=lambda value:'data:image/jpeg;base64,'+base64.b64encode(value).decode('ascii')
    return render(request,'pos/_customer_camera_card.html',{
        'label':'CCCD','anh_goc':encode(source),'anh_kq':encode(result),'w':AC.CHUAN_W,'h':AC.CHUAN_H})
