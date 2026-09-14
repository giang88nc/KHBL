import datetime as dt
import json
import logging
import re
import segno
from django.core.exceptions import PermissionDenied
from django.http import JsonResponse
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.http import require_http_methods
from apps.pmv.models import UserModuleAccess
from . import deposit_print_layout as L

@require_http_methods(['GET','POST'])
def config(request):
    permission='can_edit' if request.method=='POST' else 'can_view'
    if not request.user.is_authenticated or not (request.user.is_superuser or UserModuleAccess.objects.filter(
            user=request.user,module='HE_THONG',can_view=True,**({'can_edit':True} if permission=='can_edit' else {})).exists()):
        raise PermissionDenied('Bạn không có quyền cấu hình mẫu in hệ thống.')
    if request.method=='POST':
        try:
            data=json.loads(request.body or b'{}')
            if not isinstance(data,dict): raise ValueError('JSON không hợp lệ.')
            state=L.save(L.defaults() if data.get('reset') else data.get('layout'))
        except (ValueError,TypeError) as exc:
            return JsonResponse({'ok':False,'loi':str(exc)},status=400)
        logging.getLogger(__name__).info('deposit_print_layout saved by %s',request.user.username)
        return JsonResponse({'ok':True,'layout':state,'css':L.css(state)})
    layout=L.load()
    if request.GET.get('sample')=='1':
        ctx=dict(pk='TDC260900000001',h=dict(CustID='MAU',CustName='Nguyễn Minh Anh',Phone='0901234567',
            Address='1276 Kha Vạn Cân, P. Linh Xuân, TP. HCM',TrnDate=dt.date(2026,9,13),TienCoc=1500000),
            work=dict(promise_date=dt.date(2026,9,30),responsible='Trần Ngọc Phụng'),
            view_lines=[dict(ProductCode='SP001',ProductDesc='Dây hoa sen',GoldCode='N9999',Size='0',GoldWeight=3.220,unit='chỉ',SL=1),
                        dict(ProductCode='SP002',ProductDesc='Cara hoa sen',GoldCode='18K',Size='14',GoldWeight=.540,unit='chỉ',SL=1),
                        dict(ProductCode='',ProductDesc='Vòng theo mẫu khách',GoldCode='24K',Size='0',GoldWeight=2.396,unit='chỉ',SL=1)],
            receipt_qr=segno.make('TDC260900000001',micro=False).svg_data_uri(),deposit_print_calibration=L.css(layout))
        response=render(request,'pos/dat_coc_print.html',ctx)
        response['Cache-Control']='private, no-store'
        response['X-Frame-Options']='SAMEORIGIN'
        return response
    trn=request.GET.get('trn_id','').strip()
    if trn and not re.fullmatch(r'[A-Za-z0-9_-]{1,50}',trn):
        return JsonResponse({'ok':False,'loi':'Mã phiếu không hợp lệ.'},status=400)
    preview=reverse('pos:dat_coc_popup',args=[trn,'view'])+'?print=1&configure=1' if trn else reverse('pmv:deposit_print_config')+'?sample=1&configure=1'
    return render(request,'pos/deposit_print_config.html',dict(nav_active='hethong',blocks=L.BLOCKS,
        layout=layout,defaults=L.defaults(),preview_url=preview,trn_id=trn))
