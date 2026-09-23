"""KHJ passkey handoff; KHBL remains the authority for users and permissions."""
import hashlib
import hmac
import json
import logging
import secrets
import time
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, build_opener, ProxyHandler
from urllib.error import HTTPError

from django.conf import settings
from django.contrib.auth import login, logout
from django.contrib.auth.views import LoginView
from django.contrib.auth.decorators import login_not_required
from django.contrib.sessions.middleware import SessionMiddleware
from django.core.cache import cache
from django.contrib.auth import get_user_model
from django.db import transaction, IntegrityError
from django.shortcuts import redirect, render
from django.http import HttpResponseForbidden, HttpResponse, JsonResponse
from django.views.decorators.http import require_GET, require_POST

from .models import MobileEmployeeIdentity

logger=logging.getLogger(__name__)
LAN_ORIGIN='http://192.168.1.6:8102'
PUBLIC_ORIGIN='https://unopposable-parheliacal-waylon.ngrok-free.dev'
PREFIX='/portal-user/auth/khbl/'
PUBLIC_PATH='/banle/mobile/'
FACEID_SESSION_AGE=365*24*3600
FACEID_RECHECK_SECONDS=300


class MissingPmv(ValueError):
    pass


def is_mobile_admin(data):
    return data.get('mobile_admin') is True and data.get('employee_id',data.get('id'))==24


@login_not_required
@require_GET
def notice(request):
    return render(request,'pos/mobile_access_notice.html',status=403)


def is_mobile_http(request):
    return request.get_host()=='192.168.1.6:8102' and not request.is_secure()


class MobilePasswordLogin(LoginView):
    template_name='accounts/login.html'
    next_page='/banle/mobile/dashboard/'

    def form_valid(self, form):
        response=super().form_valid(form)
        self.request.session.set_expiry(8*3600)
        self.request.session.pop('mobile_employee',None)
        return response


class MobileSessionMiddleware(SessionMiddleware):
    """Keep desktop session untouched; mobile and its QR calls have a named cookie."""
    def process_request(self, request):
        ref=urlparse(request.META.get('HTTP_REFERER',''))
        request.khbl_mobile_session=request.path.startswith('/banle/mobile/') or (
            request.path.startswith('/banle/check-money-flow/') and (
                request.headers.get('X-KHBL-Mobile')=='1' or
                (ref.netloc==request.get_host() and ref.path.startswith('/banle/mobile/'))))
        request.khbl_mobile_cookie='khbl_mobile_http_session' if is_mobile_http(request) else 'khbl_mobile_session'
        if request.khbl_mobile_session:
            request.COOKIES=request.COOKIES.copy()
            request.COOKIES.pop(settings.SESSION_COOKIE_NAME,None)
            if request.COOKIES.get(request.khbl_mobile_cookie):
                request.COOKIES[settings.SESSION_COOKIE_NAME]=request.COOKIES[request.khbl_mobile_cookie]
        super().process_request(request)

    def process_view(self, request, view_func, view_args, view_kwargs):
        # Public liveness probe: no identity queries, refresh, or session extension.
        if request.resolver_match.url_name=='mobile_health':
            return None
        if is_mobile_http(request):
            from .models import MoneyFlow, MoneyFlowPayment
            route=request.resolver_match.url_name
            if route=='money_flow_payment' and not (
                view_kwargs.get('method','').upper()=='BANK' and
                MoneyFlow.objects.filter(pk=view_kwargs.get('pk'),direction='IN').exists()
            ):
                return HttpResponseForbidden('Cổng mobile chỉ phục vụ QR nhận tiền IN.')
            if route=='money_flow_reconcile_pawn' and not MoneyFlowPayment.objects.filter(
                pk=view_kwargs.get('pk'),flow__direction='IN',method='BANK'
            ).exists():
                return HttpResponseForbidden('Cổng mobile chỉ phục vụ QR nhận tiền IN.')
        if not request.user.is_authenticated:
            return None
        mobile_employee=request.session.get('mobile_employee')
        if mobile_employee and request.resolver_match.url_name!='mobile_logout':
            if not str(mobile_employee.get('employee_pmv') or '').strip() and not is_mobile_admin(mobile_employee):
                logout(request)
                return notice(request)
            now=time.time()
            if now-request.session.get('faceid_checked_at',0)>=FACEID_RECHECK_SECONDS:
                try:
                    state=bridge_call('status',{'employee_id':mobile_employee['id'],
                        'passkey_id':mobile_employee['passkey_id']})
                except Exception:
                    return HttpResponse('Chưa kiểm tra được phiên Face ID với KHJ. Vui lòng thử lại.',status=503)
                if state.get('active') is not True:
                    logout(request)
                    if state.get('code')=='pmv_required':return notice(request)
                    return HttpResponseForbidden('Nhân viên hoặc Face ID đã bị khóa/thu hồi. Vui lòng đăng nhập lại.')
                request.session['faceid_checked_at']=now
                if 'employee_pmv' in state:
                    mobile_employee={**mobile_employee,'employee_pmv':state['employee_pmv'],
                                     'mobile_admin':state.get('mobile_admin',False),
                                     'full_name':state.get('full_name') or mobile_employee['full_name']}
                    request.session['mobile_employee']=mobile_employee
            if now-request.session.get('faceid_renewed_at',0)>=24*3600:
                request.session.set_expiry(FACEID_SESSION_AGE)
                request.session['faceid_renewed_at']=now
        identity=MobileEmployeeIdentity.objects.filter(user_id=request.user.pk).first()
        if mobile_employee and (not identity or identity.employee_id!=mobile_employee.get('id')
                or not identity.mobile_only):
            logout(request)
            return HttpResponseForbidden('Liên kết nhân viên đã thay đổi. Vui lòng đăng nhập lại.')
        if not identity or not identity.mobile_only:
            return None
        route=request.resolver_match.url_name
        if route=='mobile_logout':
            return None
        if not identity.enabled:
            return HttpResponseForbidden('Tài khoản mobile đã bị khóa.')
        if mobile_employee and is_mobile_admin(mobile_employee) and request.path.startswith('/banle/mobile/'):
            request.khbl_mobile_admin=True
            return None
        allowed={'money_flow_mobile','money_flow_mobile_dashboard','money_flow_mobile_in','mobile_money_check','mobile_qr_image','mobile_qr_success','mobile_invoice',
                 'mobile_orders','mobile_order_detail',
                 'mobile_online','mobile_online_pickup',
                 'mobile_qr_list','mobile_qr_new','mobile_qr_detail','mobile_qr_check',
                 'mobile_auth_start','mobile_auth_callback','mobile_auth_result','mobile_password_login'}
        if route in allowed:
            return None
        from .models import MoneyFlow, MoneyFlowPayment
        if route=='money_flow_payment' and view_kwargs.get('method','').upper()=='BANK':
            if MoneyFlow.objects.filter(pk=view_kwargs.get('pk'),direction='IN').exists():
                return None
        if route=='money_flow_reconcile_pawn':
            if MoneyFlowPayment.objects.filter(pk=view_kwargs.get('pk'),flow__direction='IN',method='BANK').exists():
                return None
        return HttpResponseForbidden('Tài khoản này chỉ được xem giao dịch IN và tạo QR trên KHBL Mobile.')

    def process_response(self, request, response):
        response=super().process_response(request,response)
        if getattr(request,'khbl_mobile_session',False):
            response['Cache-Control']='no-store, private'
            if settings.SESSION_COOKIE_NAME in response.cookies:
                cookie=response.cookies.pop(settings.SESSION_COOKIE_NAME)
                name=request.khbl_mobile_cookie
                response.cookies[name]=cookie.value
                for attr,value in cookie.items():
                    response.cookies[name][attr]=value
                response.cookies[name]['secure']=not is_mobile_http(request)
                response.cookies[name]['httponly']=True
                response.cookies[name]['samesite']='Lax'
        return response


def bridge_call(action, data):
    path=PREFIX+'internal/'+action+'/'
    body=json.dumps(data,separators=(',',':')).encode()
    stamp=str(int(time.time()))
    secret=Path(getattr(settings,'KHBL_AUTH_KEY_FILE','C:/ProgramData/KHJ/auth-bridge/khbl.key')).read_bytes().strip()
    if len(secret)<32:
        raise ValueError('Bridge key not configured')
    signature=hmac.new(secret,stamp.encode()+b'\n'+path.encode()+b'\n'+body,hashlib.sha256).hexdigest()
    req=Request('http://127.0.0.1:8000'+path,data=body,headers={
        'Content-Type':'application/json','X-KHBL-Time':stamp,'X-KHBL-Signature':signature})
    with build_opener(ProxyHandler({})).open(req,timeout=8) as response:
        return json.load(response)


@login_not_required
@require_GET
def home(request):
    if request.user.is_authenticated:
        return redirect('pos:money_flow_mobile_dashboard')
    context={} if is_mobile_http(request) else {'canonical':LAN_ORIGIN+'/banle/mobile/'}
    pending=request.session.get('faceid_pending') or {}
    context['app_pending']=pending.get('mode')=='app' and 0<=time.time()-pending.get('created',0)<=240
    if context['app_pending']:
        context['app_authorize']=pending.get('authorize','')
        context['app_created']=pending.get('created',0)
    return render(request,'pos/mobile_login.html',context)


@login_not_required
@require_GET
def health(request):
    response=JsonResponse({'service':'khbl-mobile','ok':True})
    response['Cache-Control']='no-store'
    return response


@login_not_required
@require_POST
def start(request):
    # A single canonical callback origin ensures the initiating browser cookie returns.
    if not is_mobile_http(request):
        return render(request,'pos/mobile_login.html',{'canonical':LAN_ORIGIN+'/banle/mobile/'},status=400)
    bucket='khbl-faceid:'+hashlib.sha256(request.META.get('REMOTE_ADDR','').encode()).hexdigest()
    if not cache.add(bucket,1,timeout=5):
        return render(request,'pos/mobile_login.html',{'error':'Vui lòng đợi vài giây rồi thử lại.'},status=429)
    verifier=secrets.token_urlsafe(32)
    try:
        data=bridge_call('start',{'challenge':hashlib.sha256(verifier.encode()).hexdigest()})
        target=data['authorize']
        if not target.startswith(PUBLIC_ORIGIN+PUBLIC_PATH+'?ticket='):
            raise ValueError('Unexpected authorization URL')
    except Exception:
        logger.warning('KHJ Face ID start unavailable')
        return render(request,'pos/mobile_login.html',{'error':'Chưa kết nối được KHJ. Vui lòng thử lại hoặc dùng đăng nhập dự phòng.'},status=503)
    app_mode=request.POST.get('mode')=='app'
    if app_mode:
        target+='&app=1'
    request.session['faceid_pending']={'verifier':verifier,'created':time.time(),'mode':'app' if app_mode else 'browser'}
    if app_mode:
        request.session['faceid_pending']['authorize']=target
    request.session.set_expiry(8*3600)
    if app_mode:
        # The original app keeps its cookie and verifier; the external browser gets neither.
        return JsonResponse({'authorize':target,'created':request.session['faceid_pending']['created']})
    return redirect(target)


def resolve_identity(data, *, retry=True):
    """Only call with identity verified by KHJ or the trusted local provisioning job."""
    from apps.pmv.models import UserModuleAccess
    employee_id=int(data['employee_id'])
    if employee_id<=0:
        raise ValueError('Mã nhân viên không hợp lệ.')
    emp=str(data.get('employee_pmv') or '').strip()
    if not emp and not is_mobile_admin(data):
        raise MissingPmv('Nhân viên chưa có mã PMV. Vui lòng liên hệ quản trị để liên kết.')
    try:
        with transaction.atomic():
            identity=MobileEmployeeIdentity.objects.select_for_update().select_related('user').filter(
                employee_id=employee_id).first()
            if identity is None:
                username=f'khj_{employee_id}'
                if get_user_model().objects.filter(username=username).exists():
                    raise ValueError('Tên tài khoản đã tồn tại nhưng chưa liên kết nhân viên. Cần quản trị kiểm tra.')
                user=get_user_model().objects.create_user(username=username,password=None,
                    first_name=str(data.get('full_name') or '')[:150],is_staff=False,is_superuser=False)
                identity=MobileEmployeeIdentity.objects.create(employee_id=employee_id,user=user,
                    employee_pmv=emp,mobile_only=True)
            if not identity.enabled or not identity.user.is_active:
                raise ValueError('Tài khoản mobile đã bị khóa.')
            if not identity.mobile_only or identity.user.is_staff or identity.user.is_superuser:
                raise ValueError('Face ID chỉ dành cho tài khoản mobile riêng; cần quản trị kiểm tra liên kết.')
            if identity.employee_pmv!=emp:
                identity.employee_pmv=emp
                identity.save(update_fields=['employee_pmv'])
            UserModuleAccess.objects.update_or_create(user=identity.user,module='CHUYEN_KHOAN',
                defaults={'can_view':True,'can_edit':True,'can_delete':False,'can_approve':False})
    except IntegrityError:
        # A simultaneous verified login may have provisioned the same employee.
        if retry and MobileEmployeeIdentity.objects.filter(employee_id=employee_id).exists():
            return resolve_identity(data,retry=False)
        raise
    return identity


@login_not_required
@require_GET
def callback(request):
    pending=request.session.pop('faceid_pending',None)
    if not pending or not 0<=time.time()-pending.get('created',0)<=240:
        return render(request,'pos/mobile_login.html',{'error':'Phiên đăng nhập hết hạn hoặc khác trình duyệt. Hãy bắt đầu lại.'},status=400)
    try:
        data=bridge_call('redeem',{'code':request.GET.get('code',''),'verifier':pending['verifier']})
        identity=resolve_identity(data)
    except MissingPmv:
        return notice(request)
    except ValueError as exc:
        return render(request,'pos/mobile_login.html',{'error':str(exc)},status=403)
    except HTTPError as exc:
        try:denial=json.loads(exc.read())
        except (ValueError,TypeError):denial={}
        if exc.code==403 and denial.get('code')=='pmv_required':return notice(request)
        return render(request,'pos/mobile_login.html',{'error':'Không xác nhận được đăng nhập. Vui lòng thử lại.'},status=400)
    except Exception:
        logger.warning('KHJ Face ID callback rejected or unavailable')
        return render(request,'pos/mobile_login.html',{'error':'Không xác nhận được đăng nhập. Mã có thể đã dùng hoặc hết hạn.'},status=400)
    establish_session(request,identity,data)
    response=redirect('pos:money_flow_mobile_dashboard')
    response['Referrer-Policy']='no-referrer'
    return response


def establish_session(request,identity,data):
    login(request,identity.user,backend='django.contrib.auth.backends.ModelBackend')
    request.session['mobile_employee']={'id':data['employee_id'],'full_name':data['full_name'],
        'employee_pmv':data['employee_pmv'],'passkey_id':data['passkey_id'],'mobile_only':identity.mobile_only,
        'mobile_admin':is_mobile_admin(data)}
    request.session['faceid_checked_at']=time.time()
    request.session['faceid_renewed_at']=time.time()
    request.session.set_expiry(FACEID_SESSION_AGE)
    logger.info('Mobile Face ID login user_id=%s employee_id=%s',identity.user_id,identity.employee_id)


@login_not_required
@require_POST
def auth_result(request):
    """Complete only in the initiating app, without sharing Safari/Chrome cookies."""
    if not is_mobile_http(request):
        return JsonResponse({'error':'Mở KHBL từ Wi-Fi cửa hàng.'},status=400)
    pending=request.session.get('faceid_pending') or {}
    if pending.get('mode')!='app' or not 0<=time.time()-pending.get('created',0)<=240:
        return JsonResponse({'error':'Phiên Face ID đã hết hạn. Hãy bắt đầu lại.'},status=400)
    # Per-initiator throttling; never use IP alone (many employees share a network).
    bucket='khbl-faceid-result:'+hashlib.sha256(pending['verifier'].encode()).hexdigest()
    if not cache.add(bucket,1,timeout=2):
        return JsonResponse({'status':'pending'})
    try:
        data=bridge_call('result',{'verifier':pending['verifier']})
        if data.get('status')=='pending':
            return JsonResponse({'status':'pending'})
        if data.get('status')!='verified':
            request.session.pop('faceid_pending',None)
            return JsonResponse({'error':'Phiên Face ID hết hạn hoặc đã dùng. Hãy bắt đầu lại.'},status=400)
        identity=resolve_identity(data)
    except MissingPmv:
        request.session.pop('faceid_pending',None)
        return JsonResponse({'redirect':'/banle/mobile/auth/notice/','error':'Chưa liên kết mã PMV.'},status=403)
    except ValueError as exc:
        return JsonResponse({'error':str(exc)},status=403)
    except HTTPError as exc:
        if exc.code==403:
            try:denial=json.loads(exc.read())
            except (ValueError,TypeError):denial={}
            if denial.get('code')=='pmv_required':
                request.session.pop('faceid_pending',None)
                return JsonResponse({'redirect':'/banle/mobile/auth/notice/','error':denial['error']},status=403)
        if exc.code==400:
            request.session.pop('faceid_pending',None)
            return JsonResponse({'error':'Face ID hết hạn hoặc đã bị thu hồi. Hãy bắt đầu lại.'},status=400)
        return JsonResponse({'error':'Chưa kiểm tra được Face ID với KHJ. Vui lòng thử lại.'},status=503)
    except Exception:
        logger.warning('KHJ Face ID app result unavailable')
        return JsonResponse({'error':'Chưa kết nối được KHJ. Vui lòng thử lại.'},status=503)
    request.session.pop('faceid_pending',None)
    establish_session(request,identity,data)
    return JsonResponse({'status':'success','redirect':'/banle/mobile/dashboard/'})


@require_POST
def sign_out(request):
    logout(request)
    return redirect('pos:money_flow_mobile')
