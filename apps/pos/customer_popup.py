"""Render the canonical KHBL popup for KHCD over the authenticated loopback bridge.

Only customer operations below are reachable. No arbitrary view, file, SQL, target,
session or cookie forwarding. Flask authenticates/validates CSRF at the browser edge;
the bridge independently validates the actor and customer permissions on every call.
"""
import base64
import json
import mimetypes
import re
import secrets
from copy import deepcopy
from functools import lru_cache

from django.conf import settings
from django.http import HttpResponse
from django.template.backends.django import DjangoTemplates
from django.test import RequestFactory
from django.urls import get_script_prefix, set_script_prefix, resolve, Resolver404
from django.utils.html import format_html
from . import customer as C, services as S

PREFIX = '/camdo/khach-hang/popup/api/'
ASSETS = {'css/fonts.css', 'css/khbl.css', 'js/vendor/htmx.min.js',
          'js/vn_text.js', 'js/cccd.js', 'js/khbl.js'}
METHODS = {'khach_them': 'GET', 'khach_sua': 'GET', 'khach_luu': 'POST',
           'khach_kiem_sdt': 'GET', 'khach_qr_phan_tich': 'POST',
           'khach_anh': 'GET', 'khach_anh_cat': 'POST', 'khach_anh_cat_luu': 'POST'}
MAX_BODY = 48 * 1024 * 1024


@lru_cache(maxsize=1)
def engine():
    config = deepcopy(settings.TEMPLATES[0])
    config.pop('BACKEND')
    config['NAME'] = 'customer_popup'
    config['OPTIONS']['context_processors'] = []
    # Read the same source file on each open, including future KHBL layout changes.
    config['APP_DIRS'] = False
    config['OPTIONS']['loaders'] = ['django.template.loaders.filesystem.Loader',
                                    'django.template.loaders.app_directories.Loader']
    return DjangoTemplates(config)


def render(request, template, context):
    return HttpResponse(engine().get_template(template).render(context, request))


def envelope(response):
    return {'status': response.status_code, 'body': base64.b64encode(response.content).decode('ascii'),
            'headers': {k: v for k, v in response.items() if k.lower() in
                        ('content-type', 'hx-trigger')}}


def dispatch(payload, user, client):
    from . import views, views_thau, customer_bridge as B
    if not C.duoc_xem(user):
        raise PermissionError('Tài khoản chưa có quyền xem khách hàng.')
    asset = payload.get('asset')
    if asset is not None:
        if asset not in ASSETS and not re.fullmatch(
                r'fonts/(?:BeVietnamPro-(?:400|500|600|700)|Cormorant-600)-(?:vietnamese|latin)\.woff2', asset):
            raise PermissionError('Tệp không được phép truy cập.')
        path = settings.BASE_DIR / 'static' / asset
        return envelope(HttpResponse(path.read_bytes(), content_type=mimetypes.guess_type(asset)[0] or 'application/octet-stream'))

    path = payload.get('path', '')
    if not isinstance(path, str) or not path.startswith('/banle/khach-hang/'):
        raise PermissionError('Đường dẫn không được hỗ trợ.')
    try:
        match = resolve(path)
    except Resolver404:
        raise ValueError('Đường dẫn không được hỗ trợ.') from None
    name = match.url_name
    method = payload.get('method', 'GET')
    if name not in METHODS or method != METHODS[name]:
        raise PermissionError('Thao tác không được hỗ trợ.')
    if name not in ('khach_anh', 'khach_kiem_sdt') and not C.duoc_sua(user):
        raise PermissionError('Tài khoản chưa có quyền tạo/sửa khách hàng.')
    raw = base64.b64decode(payload.get('body', ''), validate=True)
    if len(raw) > MAX_BODY:
        raise ValueError('Tối đa 48 MB cho mỗi yêu cầu ảnh.')
    query = payload.get('query', '')
    if not isinstance(query, str) or len(query) > 4000:
        raise ValueError('Tham số không hợp lệ.')
    request = RequestFactory().generic(method, path + ('?' + query if query else ''), raw,
        content_type=payload.get('content_type', 'application/octet-stream'))
    request.user = user
    request.session = {}
    request.customer_client = client
    request.customer_render = render
    prefix = get_script_prefix()
    set_script_prefix(PREFIX)
    try:
        if name in ('khach_them', 'khach_sua'):
            if request.GET.get('pawn_photos')=='1':
                groups={
                    'front':[('anh_truoc','CCCD mặt trước','truoc')],
                    'back':[('anh_sau','CCCD mặt sau','sau')],
                    'products':[('anh_sp1','Hình sản phẩm 1',''),('anh_sp2','Hình sản phẩm 2',''),('anh_qr','QR chuyển khoản khách','')],
                    'all':[('anh_truoc','CCCD mặt trước','truoc'),('anh_sau','CCCD mặt sau','sau'),('anh_sp1','Hình sản phẩm 1',''),('anh_sp2','Hình sản phẩm 2','')]}
                return envelope(render(request,'pos/_pawn_photos.html',{'slots':groups.get(request.GET.get('group','all'),groups['all'])}))
            cid = match.kwargs.get('cust_id')
            row = C._current(client, cid) if cid else None
            if cid and not row:
                return envelope(HttpResponse('Không tìm thấy khách KK.', status=404))
            k = S.khach_theo_id(cid, pmv_client=client) if cid else None
            guard = ''
            if request.GET.get('append_phone'):
                try: k,guard=C.prepare_append(client,k,request.GET['append_phone'],request.GET.get('cmnd',''))
                except C.CustomerSaveError as exc: return envelope(render(request,'pos/_khach_luu_kq.html',{'loi':[str(exc)]}))
            response = render(request, 'pos/_khach_form.html', {
                'append_guard':guard,
                'k': k, 'goi_y_dt': '' if cid else views._so_dt_tu(request.GET.get('q')),
                'loai_ds': S.CUST_TYPES, 'duong_dan_anh': S.DUONG_DAN_ANH,
                'save_token': secrets.token_urlsafe(24), 'customer_version': B.version(row) if row else ''})
        elif name == 'khach_luu':
            fields = request.POST.dict()
            token = fields.pop('save_token', '')
            version = fields.pop('version', '')
            fields.pop('csrfmiddlewaretoken', None)
            try:
                result = B.save({'form': fields, 'token': token, 'version': version}, user, client, request.FILES)
            except (ValueError, C.CustomerSaveError) as error:
                result = {'complete': False, 'errors': [str(error)]}
            if result.get('complete') is True:
                response = HttpResponse(status=204)
                response['HX-Trigger'] = json.dumps({'khachSaved': {
                    'custId': result['cust_id'], 'warnings': result.get('warnings', []),
                    'message': ('Đã thêm ' if result.get('created') else 'Đã cập nhật ') +
                               (result.get('cust_code') or result['cust_id']) + ' — ' + result.get('name', '')}})
            else:
                cid = result.get('cust_id', '')
                response = render(request, 'pos/_khach_luu_kq.html', {
                    'loi': result.get('errors') or ['Chưa hoàn tất lưu khách.'],
                    'da_luu_thong_tin': result.get('partial'), 'saved_cust_id': cid,
                    'saved_cust_code': result.get('cust_code', cid)})
                if result.get('partial') and cid:
                    current = C._current(client, cid)
                    response.write(format_html(
                        '<input type="hidden" name="save_token" value="{}" hx-swap-oob="outerHTML:[name=save_token]">'
                        '<input type="hidden" id="kh-customer-version" name="version" value="{}" hx-swap-oob="outerHTML">',
                        secrets.token_urlsafe(24), B.version(current)))
                if result.get('uncertain'):
                    response.write(format_html(
                        '<p>Mã yêu cầu: <code>{}</code>. '
                        '<a target="_parent" href="/camdo/khach-hang/ket-qua/{}">Kiểm tra kết quả lần lưu</a></p>', token, token))
        elif name == 'khach_anh':
            try:
                data, content_type = C.saved_image(match.kwargs['cust_id'], match.kwargs['kind'], client=client)
                response = HttpResponse(data, content_type=content_type)
            except Exception:
                response = HttpResponse('Không đọc được ảnh.', status=404)
        else:
            handler = {'khach_kiem_sdt': views.khach_kiem_sdt,
                       'khach_qr_phan_tich': views.khach_qr_phan_tich,
                       'khach_anh_cat': views_thau.khach_anh_cat,
                       'khach_anh_cat_luu': views_thau.khach_anh_cat_luu}[name]
            response = handler(request)
        return envelope(response)
    finally:
        set_script_prefix(prefix)
        request.close()
