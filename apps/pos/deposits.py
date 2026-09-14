"""Phiếu cọc PMV: đọc header/chi tiết, CRUD phiếu W qua proc vendor."""
import datetime as dt
import hashlib
import json
import logging
import threading
import uuid
import csv
from decimal import Decimal
from xml.etree.ElementTree import Element, SubElement, tostring

from django import forms
from django.core import signing
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.forms import formset_factory
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods

from apps.pmv.client import PmvClient
from apps.pmv.models import UserModuleAccess, pmv_user_for_web_user
from . import deposit_orders as O
from . import deposit_workspace as W

log = logging.getLogger(__name__)
SAVE_LOCK = threading.Lock()
STATES = {'W': 'Lưu tạm', 'P': 'Đã thanh toán', 'C': 'Đã sử dụng',
          'D': 'Đã hủy', 'R': 'Hàng sẵn sàng'}


def allowed(user, action):
    return user.is_authenticated and (user.is_superuser or UserModuleAccess.objects.filter(
        user=user, module='DAT_COC', can_view=True, **({action: True} if action != 'can_view' else {})).exists())


def authorize(request, action='can_view'):
    if not allowed(request.user, action):
        raise PermissionDenied('Bạn chưa được cấp quyền ĐẶT-CỌC.')


def kiem_passcode(request, pk, ctx):
    """Xóa phiếu đã ghi tiền / đã có lịch sử vận hành: bắt buộc XÁC NHẬN PASSCODE (GĐ chốt 11/09/2026).

    Dùng đúng bộ passcode của màn bán (băm ở auth_user, sai 5 lần khóa nhập 30 giây) để cả hệ chỉ có
    MỘT mã mở khóa. Sai / bỏ trống → giữ nguyên popup kèm lỗi, không xóa gì.
    """
    from .views import _passcode_dung, _passcode_khoa

    if not _passcode_dung(request, request.POST.get('passcode')):
        cho = _passcode_khoa(request)
        raise ValueError(f'Tạm khóa nhập Passcode, thử lại sau {cho} giây.' if cho
                         else 'Passcode không đúng — phiếu CHƯA bị xóa.')
    log.warning('XOA PHIEU COC %s sau khi xac nhan passcode (user=%s, tien=%s, lich su=%s)',
                pk, request.user.username, ctx.get('has_money'), ctx.get('has_activity'))


def don_sau_khi_xoa(c, pk, header_cu, user):
    """PMV đã xóa phiếu → dọn phần KHBL: trả hàng đang giữ, bỏ tiến độ, GIỮ LẠI nhật ký.

    Nhật ký (``DepositEvent``) và chứng từ tiền cố tình không xóa: phiếu mất rồi thì đó là chỗ duy
    nhất còn ghi ai đã làm gì với nó. Thêm một dòng 'delete' chốt lại mốc xóa kèm số tiền lúc xóa.
    """
    from .deposit_models import DepositEvent, DepositOrderState, DepositStockHold

    ten = getattr(user, 'username', '') or ''
    DepositStockHold.objects.filter(target=c.target, trn_id=pk, active_key__isnull=False).update(
        active_key=None, released_at=timezone.now(), released_by=ten)
    DepositOrderState.objects.filter(target=c.target, trn_id=pk).delete()
    DepositEvent.objects.create(
        target=c.target, trn_id=pk, action='delete', username=ten,
        note='Xóa phiếu sau khi xác nhận Passcode; hàng đang giữ đã trả về kho.',
        token=hashlib.sha256(f'xoa:{c.target}:{pk}:{timezone.now().isoformat()}'.encode()).hexdigest(),
        data={'TienCoc': str(header_cu.get('TienCoc') or 0), 'CashPay': str(header_cu.get('CashPay') or 0),
              'CardPay': str(header_cu.get('CardPay') or 0), 'Status': header_cu.get('Status') or '',
              'CustID': header_cu.get('CustID') or ''})


class FilterForm(forms.Form):
    tab = forms.ChoiceField(choices=O.TABS)
    q = forms.CharField(required=False, max_length=100, label='Tìm phiếu - Khách hàng',
                        widget=forms.TextInput(attrs={'placeholder': 'Mã phiếu, tên khách, SĐT hoặc tên nhân viên…'}))
    start = forms.DateField(required=False, label='Ngày hẹn từ', widget=forms.DateInput(attrs={'type': 'date'}))
    end = forms.DateField(required=False, label='Ngày hẹn đến', widget=forms.DateInput(attrs={'type': 'date'}))
    status = forms.ChoiceField(required=False, label='Trạng thái', choices=[('', 'Tất cả trạng thái'), ('X', 'Chưa giao'), *STATES.items()])
    due = forms.ChoiceField(required=False, label='Ngày hẹn', choices=[('', 'Tất cả lịch hẹn'), ('overdue', 'Quá hẹn'), ('today', 'Hẹn hôm nay'), ('soon', 'Trong 2 ngày tới'), ('none', 'Chưa hẹn ngày')])
    sort = forms.ChoiceField(required=False, label='Sắp xếp', choices=[('priority', 'Việc cần xử lý trước'), ('newest', 'Mới nhất'), ('due', 'Ngày hẹn gần nhất'), ('delivered', 'Ngày giao thực tế')])
    quick = forms.ChoiceField(required=False, label='Công việc', choices=W.QUICK)
    source = forms.ChoiceField(required=False, label='Nguồn hàng', choices=W.SOURCES)
    mine = forms.BooleanField(required=False, label='Của tôi')

    def clean(self):
        d = super().clean()
        if d.get('start') and d.get('end') and d['start'] > d['end']:
            raise forms.ValidationError('Ngày kết thúc phải từ ngày bắt đầu trở đi.')
        if d.get('end') == dt.date.max:
            raise forms.ValidationError('Ngày kết thúc quá xa.')
        return d


class MoneyInput(forms.TextInput):
    def __init__(self,attrs=None):
        super().__init__({'inputmode':'decimal','data-dc-money':'',**(attrs or {})})

    def format_value(self,value):
        if value in (None,''): return ''
        try:
            d=Decimal(str(value)); s=format(d,',.3f').rstrip('0').rstrip('.')
            return s.replace(',','_').replace('.',',').replace('_','.')
        except Exception: return value

    def value_from_datadict(self,data,files,name):
        value=super().value_from_datadict(data,files,name)
        if data.get('money_format')=='vi' and isinstance(value,str):
            return value.replace('.','').replace(',','.').replace(' ','').replace('₫','')
        return value


class WeightInput(forms.TextInput):
    def __init__(self,attrs=None):
        super().__init__({'inputmode':'decimal','data-dc-weight':'',**(attrs or {})})

    def format_value(self,value):
        if value in (None,''): return ''
        try: return format(Decimal(str(value)),'.3f').replace('.',',')
        except Exception: return value

    def value_from_datadict(self,data,files,name):
        value=super().value_from_datadict(data,files,name)
        return value.replace(',','.') if isinstance(value,str) else value

    def get_context(self,name,value,attrs):
        context=super().get_context(name,value,attrs)
        if value not in (None,''):
            context['widget']['attrs'].update({'data-weight-exact':str(value),'data-weight-display':self.format_value(value)})
        return context


class DepositForm(forms.Form):
    CustID = forms.CharField(label='Mã khách hàng', max_length=15,
                            error_messages={'required': 'Chọn khách hàng trước khi lưu phiếu.'},
                            widget=forms.TextInput(attrs={'placeholder': 'Chọn khách bằng ô tìm kiếm bên dưới'}))
    EmpID = forms.ChoiceField(label='Nhân viên phụ trách', error_messages={
        'required': 'Chọn nhân viên phụ trách.', 'invalid_choice': 'Nhân viên không còn hoạt động. Chọn lại nhân viên.'})
    TienCoc = forms.DecimalField(label='Tiền cọc (₫)', min_value=0, max_digits=18, decimal_places=3,widget=MoneyInput)
    Description = forms.CharField(label='Ghi chú phiếu', required=False, max_length=500,
                                  widget=forms.Textarea(attrs={'rows': 2}))
    NoteEntries = forms.JSONField(required=False,widget=forms.HiddenInput,initial=list)
    NoteText = forms.CharField(required=False,max_length=500,widget=forms.TextInput(attrs={'placeholder':'Nhập ghi chú mới…','autocomplete':'off'}))
    PromiseDate = forms.DateField(label='Ngày hẹn lấy hàng', required=True,
                                 error_messages={'required': 'Nhập ngày hẹn trước khi lưu phiếu.'},
                                 widget=forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'))
    Estimate = forms.DecimalField(label='Tổng tiền tạm tính (₫)', required=False, min_value=0, max_digits=18, decimal_places=3)
    CashDeposit = forms.DecimalField(label='Cọc tiền mặt',required=False,min_value=0,max_digits=18,decimal_places=3,widget=MoneyInput)
    BankDeposit = forms.DecimalField(label='Cọc chuyển khoản',required=False,min_value=0,max_digits=18,decimal_places=3,widget=MoneyInput)

    def clean(self):
        data = super().clean()
        self.raw_description=data.get('Description','')
        if self.data.get('description_format')=='json':
            try:
                self.raw_description=O.validate_notes(data.get('NoteEntries') or [])
                if data.get('NoteText'): self.raw_description.append({'date':timezone.localdate().isoformat(),'text':data['NoteText']})
            except ValueError as exc: self.add_error('NoteEntries',str(exc))
        if (self.data.get('editor_version')=='2' or any(k in self.data for k in ('CashDeposit','BankDeposit'))) and not getattr(self,'restricted_edit',False):
            for key in ('CashDeposit','BankDeposit'):
                if data.get(key) is None: self.add_error(key,'Nhập số tiền, hoặc 0 nếu không có.')
            if data.get('CashDeposit') is not None and data.get('BankDeposit') is not None:
                try: data['TienCoc']=self.fields['TienCoc'].clean(data['CashDeposit']+data['BankDeposit'])
                except forms.ValidationError as exc: self.add_error('TienCoc',exc)
        if data.get('TienCoc') is not None and data['TienCoc'] <= Decimal('100000'):
            self.add_error('TienCoc', 'Tổng tiền mặt + chuyển khoản phải lớn hơn 100.000₫.')
        if 'Description' in data:
            packed = O.pack_description(data.get('PromiseDate'), self.raw_description, data.get('Estimate'))
            if O.description_length(packed) > 500:
                self.add_error('NoteEntries' if self.data.get('description_format')=='json' else 'Description', 'Ghi chú, ngày hẹn và tạm tính vượt giới hạn 500 ký tự của PMV. Rút gọn nội dung trước khi lưu.')
            data['Description'] = packed
        return data


class OrderLineForm(forms.Form):
    Mode = forms.ChoiceField(label='Nguồn hàng', required=False, initial='new',
                            choices=[('new', 'Không có sẵn · Đặt mới'), ('stock', 'Có sẵn trong kho')])
    ProductCode = forms.RegexField(r'^[^|\r\n]+$', label='Mã hàng trong kho', required=False, max_length=80,
                                  widget=forms.TextInput(attrs={'placeholder': 'Tìm mã hoặc tên hàng trong kho…', 'autocomplete': 'off'}))
    ProductDesc = forms.CharField(label='Món đặt hàng', max_length=500)
    GoldCode = forms.ChoiceField(label='Loại vàng')
    SL = forms.IntegerField(label='Số lượng', min_value=1, max_value=9999, initial=1)
    TotalWeight = forms.DecimalField(label='Tổng TL', min_value=0, max_digits=19, decimal_places=8, initial=0)
    DiamondWeight = forms.DecimalField(label='TL hột', min_value=0, max_digits=19, decimal_places=8, initial=0)
    GoldWeight = forms.DecimalField(label='TL vàng dự kiến', required=False, initial=0, min_value=0, max_digits=19,
                                   decimal_places=8, widget=WeightInput)
    TaskPrice = forms.DecimalField(label='Tiền công (₫)', min_value=0, max_digits=18, decimal_places=3, initial=0,widget=MoneyInput)
    Size = forms.CharField(label='Ni / kích thước', required=False, max_length=200)
    Notes = forms.CharField(label='Yêu cầu chế tác', required=False, max_length=1000)
    DisplayUnits = forms.BooleanField(required=False,widget=forms.HiddenInput,initial=True)

    def clean(self):
        d = super().clean()
        mode = d.get('Mode') or 'new'
        if mode == 'stock' and not d.get('ProductCode'):
            self.add_error('ProductCode', 'Chọn sản phẩm có sẵn trong kho.')
        if mode == 'new':
            d['ProductCode'] = 'Khách đặt'
            if d.get('GoldWeight') is None or d['GoldWeight'] <= 0:
                self.add_error('GoldWeight', 'Nhập TL vàng lớn hơn 0 cho hàng đặt.')
        d['Mode'] = mode
        if d.get('ProductDesc') and d.get('ProductCode'):
            try:
                O.product_description(d)
            except ValueError as exc:
                self.add_error('ProductDesc', str(exc))
        if d.get('DisplayUnits') and d.get('GoldWeight') is not None and d.get('DiamondWeight') is not None:
            d['TotalWeight']=d['GoldWeight']+d['DiamondWeight']
        if d.get('TotalWeight') is not None and d.get('DiamondWeight') is not None:
            if d['DiamondWeight'] > d['TotalWeight']:
                raise forms.ValidationError('Trọng lượng hột không được lớn hơn tổng trọng lượng.')
            if d.get('GoldWeight') is None:
                d['GoldWeight'] = d['TotalWeight'] - d['DiamondWeight']
        return d


LineSet = formset_factory(OrderLineForm, extra=0, can_delete=True, min_num=1, validate_min=True,
                         max_num=50, validate_max=True, absolute_max=60)


def header(c, pk):
    rows = c.query('SELECT d.*, k.CustName, k.Phone, k.CMND, k.Address, e.EmpName FROM TRN_DATCOC d WITH (NOLOCK) '
                   'LEFT JOIN I_CUSTOMER k WITH (NOLOCK) ON k.CustID=d.CustID '
                   'LEFT JOIN T_EMPLOYEE e WITH (NOLOCK) ON e.EmpID=d.EmpID WHERE d.TrnID=?', (pk,))
    return rows[0] if rows else None


def lines(c, pk):
    # appMobile lưu trực tiếp TL theo chỉ cho TRC; phiếu vendor theo TRN_DATCOC_Get.
    mobile = "ISNULL(h.ShopID,'')='' AND h.TrnID LIKE 'TRC%'"
    rows = c.query("SELECT d.TrnDTID, d.ProductDesc, d.GoldCode, d.SL, d.TaskPrice, d.Size, d.Notes, "
                   f"CASE WHEN {mobile} THEN d.TotalWeight WHEN g.WeightUnit='L' THEN d.TotalWeight/dbo.fun_GetHS() ELSE d.TotalWeight END TotalWeight, "
                   f"CASE WHEN {mobile} THEN d.DiamondWeight WHEN g.WeightUnit='L' THEN d.DiamondWeight/dbo.fun_GetHS() ELSE d.DiamondWeight END DiamondWeight, "
                   f"CASE WHEN {mobile} THEN d.GoldWeight WHEN g.WeightUnit='L' THEN d.GoldWeight/dbo.fun_GetHS() ELSE d.GoldWeight END GoldWeight "
                   "FROM TRN_DATCOC_DT d WITH (NOLOCK) JOIN TRN_DATCOC h WITH (NOLOCK) ON h.TrnID=d.TrnID "
                   "LEFT JOIN I_GOLD g WITH (NOLOCK) ON g.GoldCode=d.GoldCode "
                   "WHERE d.TrnID=? ORDER BY d.TrnDTID", (pk,))
    return [O.item_info(row) for row in rows]


def decorate(row, order=False):
    row['state_name'] = STATES.get(row.get('Status'), 'Trạng thái ' + str(row.get('Status') or '—'))
    return O.enrich(row, order=order)


def listing(c, data, page):
    from .deposit_operations import overlay
    snapshot = W.read_snapshot(c, refresh=data.get('refresh', False))
    rows = W.classify(overlay(W.prepare(snapshot),c.target))
    return {**W.paginate(rows, data, page, data.get('emp_id', '')), 'as_of': snapshot['as_of']}


@require_GET
def index(request):
    authorize(request)
    if request.GET.get('tab') == 'notifications':
        from .deposit_messages import listing as messages_listing
        return messages_listing(request)
    params = request.GET.copy()
    params.setdefault('tab', 'all')
    if params['tab'] in ('money', 'orders', 'pending', 'delivered'):
        params['tab'] = 'all'
    today = timezone.localdate()
    params['status'] = O.TAB_STATUS.get(params['tab'], '')
    params.setdefault('sort', 'priority' if params['tab'] in ('ready','pending') else 'delivered' if params['tab'] == 'delivered' else 'newest')
    form = FilterForm(params)
    ctx = dict(nav_active='datcoc', form=form, tab=params['tab'], tabs=O.TABS,
               default_start=(today - dt.timedelta(days=90)).isoformat(), today=today.isoformat(),
               can_edit=allowed(request.user, 'can_edit'), can_delete=allowed(request.user, 'can_delete'))
    if form.is_valid():
        try:
            pu = pmv_user_for_web_user(request.user)
            data = {**form.cleaned_data, 'emp_id': pu.emp_id if pu else '', 'refresh': params.get('refresh') == '1'}
            ctx.update(listing(PmvClient(tag='datcoc-list'), data, params.get('page', 1)))
        except Exception:
            log.exception('Không tải được danh sách đặt cọc')
            ctx['error'] = 'Không kết nối được dữ liệu đặt cọc. Vui lòng thử lại.'
    return render(request, 'pos/_dat_coc_list.html' if request.headers.get('HX-Request') else 'pos/dat_coc.html', ctx)


@require_GET
def report(request):
    authorize(request)
    params = request.GET.copy(); params['tab'] = 'all'
    form = FilterForm(params)
    ctx = {'title': 'Báo cáo đặt hàng · Tiền cọc', 'report_form': form,
           'export_query': request.GET.urlencode()}
    if not form.is_valid():
        ctx['error'] = 'Khoảng ngày hoặc bộ lọc báo cáo chưa hợp lệ.'
        return render(request, 'pos/_dat_coc_report.html', ctx)
    try:
        c = PmvClient(tag='datcoc-report')
        snapshot = W.read_snapshot(c)
        from .deposit_operations import overlay
        rows = [r for r in W.classify(overlay(W.prepare(snapshot),c.target)) if W.matches(r, {**form.cleaned_data, 'tab':'scope', 'date_field':'created'})]
        ctx.update(W.operations_report(rows), stats=W.summarize(rows), as_of=snapshot['as_of'])
        if request.GET.get('export') == 'csv':
            response = HttpResponse(content_type='text/csv; charset=utf-8')
            response['Content-Disposition'] = 'attachment; filename="dat-hang-cong-viec.csv"'
            response.write('\ufeff')
            writer = csv.writer(response)
            writer.writerow(['Mã phiếu cọc','Số chứng từ cọc','Hóa đơn liên kết','Ngày đặt','Khách','SĐT','Tiến độ','Ngày hẹn','Phụ trách','Việc tiếp theo','Cọc ghi trên phiếu (VND)','Trạng thái đối soát','Dữ liệu đến'])
            def cell(value):
                text = str(value or '')
                return "'" + text if text.lstrip().startswith(('=','+','-','@')) or text.startswith(('\t','\r')) else text
            for r in rows:
                writer.writerow([cell(r.get(k)) for k in ('TrnID','BillCode','application_codes','TrnDate','CustName','Phone','state_name','promise_date','responsible','next_task')]
                                + [format(Decimal(r.get('TienCoc') or 0),'f'),r['money_name'],snapshot['as_of'].isoformat()])
            return response
    except Exception:
        log.exception('Báo cáo đặt hàng')
        ctx['error'] = 'Không tải được dữ liệu báo cáo. Không có số liệu ước đoán thay thế.'
    return render(request, 'pos/_dat_coc_report.html', ctx)


@require_GET
def customers(request):
    authorize(request, 'can_edit')
    from . import services as S
    raw=request.GET.get('customer_q','').strip()[:8192]
    q=S.cccd_tu_qr(raw) or raw[:100]
    rows = []
    error = ''
    if len(q) >= 2 or request.GET.get('cust_id'):
        try:
            term = '%' + q.replace('[', '[[]').replace('%', '[%]').replace('_', '[_]') + '%'
            c=PmvClient(tag='datcoc-customer')
            columns='SELECT TOP 12 CustID,CustName,Phone,CMND,Address FROM I_CUSTOMER WITH (NOLOCK) '
            if request.GET.get('cust_id'):
                rows=c.query(columns+'WHERE CustID=?',(request.GET['cust_id'][:15],))
            else:
                rows=c.query(columns+'WHERE CustName LIKE ? OR Phone LIKE ? OR CustID LIKE ? OR CMND LIKE ? ORDER BY CustName',(term,term,term,term))
        except Exception:
            log.exception('Tìm khách đặt cọc'); error = 'Không tải được khách hàng.'
    if request.GET.get('format')=='json': return JsonResponse({'rows':rows,'error':error},status=503 if error else 200)
    return render(request, 'pos/_dat_coc_customers.html', {'customers': rows, 'query': q, 'error': error})


@require_GET
def products(request):
    authorize(request, 'can_edit')
    term = request.GET.get('q', '').strip()[:80]
    if len(term) < 2:
        return JsonResponse({'rows': []})
    c = PmvClient(tag='datcoc-stock-search')
    try:
        search = '%' + term.replace('[', '[[]').replace('%', '[%]').replace('_', '[_]') + '%'
        rows = c.query('SELECT TOP 12 p.ProductCode,p.ProductDesc,p.GoldCode,p.TotalWeight,p.DiamondWeight,p.TaskPrice, '
                       'p.RingSize,g.WeightUnit FROM T_PRODUCT p WITH (NOLOCK) '
                       'LEFT JOIN I_GOLD g WITH (NOLOCK) ON g.GoldCode=p.GoldCode '
                       + ("WHERE p.Status='I' AND p.ProductCode=?" if request.GET.get('exact')=='1' else
                        "WHERE p.Status='I' AND (p.ProductCode LIKE ? OR p.ProductDesc LIKE ?) ORDER BY p.ProductCode"),
                       (term,) if request.GET.get('exact')=='1' else (search, search))
        factor = Decimal('100') if request.GET.get('units') in ('mobile','display') else Decimal(c.query('SELECT dbo.fun_GetHS() factor')[0]['factor'])
        for row in rows:
            scale = factor if row['WeightUnit'] == 'L' else Decimal(1)
            for key in ('TotalWeight', 'DiamondWeight'):
                row[key] = (Decimal(row[key] or 0) / scale).quantize(Decimal('.00000001'))
            row['GoldWeight'] = row['TotalWeight'] - row['DiamondWeight']
            row['TaskPrice']=(Decimal(row.get('TaskPrice') or 0)*1000).quantize(Decimal('.001'))
        return JsonResponse({'rows': rows})
    except Exception:
        log.exception('Không tìm được hàng trong kho')
        return JsonResponse({'error': 'Không tải được hàng trong kho. Vui lòng thử lại.'}, status=503)


def version(c, h):
    return {'target': c.target, 'id': h['TrnID'] if h else '',
            'stamp': str(h['TrnDateTime_Upd']) if h else ''}


def xml_lines(cleaned, pk=''):
    root = Element('NewDataSet')
    for row in cleaned:
        if not row or row.get('DELETE'):
            continue
        node = SubElement(root, 'TRN_DATCOC_DT')
        row = O.item_info(row)
        for key in ('ProductDesc', 'GoldCode', 'TotalWeight', 'DiamondWeight', 'GoldWeight', 'TaskPrice', 'Size', 'SL', 'Notes'):
            value = row.get(key) or '' if key in ('Size', 'Notes') else row[key]
            if key == 'ProductDesc':
                value = O.product_description(row)
            SubElement(node, key).text = format(value, 'f') if isinstance(value, Decimal) else str(value)
        SubElement(node, 'TrnID').text = pk
    return tostring(root, encoding='unicode')


def save(c, data, cleaned_lines, user, old=None):
    data=dict(data)
    if O._json_description(data.get('Description')) is None:
        legacy=O.parse_description(data.get('Description'))
        data['Description']=O.pack_description(legacy['promise_date'],O.note_entries(data.get('Description')),legacy['estimate'])
    if old:
        try: zalo=json.loads(old.get('Description') or '{}').get('zalo')
        except (ValueError,AttributeError): zalo=None
        if zalo is not None: data['Description']=O.merge_zalo(data['Description'],zalo)
    if O.description_length(data['Description'])>500:
        raise ValueError('Nội dung JSON vượt giới hạn 500 ký tự của thủ tục PMV; chưa ghi phiếu.')
    if not c.query('SELECT CustID FROM I_CUSTOMER WITH (NOLOCK) WHERE CustID=?', (data['CustID'],)):
        raise ValueError('Mã khách hàng không tồn tại. Hãy tìm và chọn khách trong danh sách.')
    pk = old['TrnID'] if old else ''
    stock_codes = {r.get('ProductCode') for r in cleaned_lines if r and not r.get('DELETE') and r.get('Mode') == 'stock'}
    old_codes = {r.get('ProductCode') for r in lines(c, pk) if r.get('Mode') == 'stock'} if stock_codes and old else set()
    for code in stock_codes - old_codes:
        if not c.query("SELECT ProductCode FROM T_PRODUCT WITH (NOLOCK) WHERE ProductCode=? AND Status='I'", (code,)):
            raise ValueError(f'Sản phẩm {code} không còn sẵn trong kho. Hãy chọn lại sản phẩm.')
    xml_rows = cleaned_lines
    if old and O.is_mobile_order(old) and any(r and not r.get('DELETE') for r in cleaned_lines):
        hs = Decimal(c.query('SELECT dbo.fun_GetHS() factor')[0]['factor'])
        gold_units = {r['GoldCode']: r['WeightUnit'] for r in c.query('SELECT GoldCode,WeightUnit FROM I_GOLD WITH (NOLOCK)')}
        xml_rows = []
        for original in cleaned_lines:
            row = dict(original)
            if row and not row.get('DELETE') and gold_units.get(row['GoldCode']) == 'L':
                for key in ('TotalWeight', 'DiamondWeight', 'GoldWeight'):
                    row[key] = row[key] / hs
            xml_rows.append(row)
    params = dict(p_CustID=data['CustID'], p_TienCoc=str(data['TienCoc']), p_EmpID=data['EmpID'],
                  p_Description=data['Description'], p_XML=xml_lines(xml_rows, pk))
    if old:
        params.update(p_TrnID=pk, p_UserUpd=user.user_id, p_TrnDateTime_Upd=old['TrnDateTime_Upd'])
    else:
        params.update(p_CreatedBy=user.user_id, p_ShopID=user.shop_id)
    _, sets = c.call('TRN_DATCOC_Upd' if old else 'TRN_DATCOC_Ins', write=True, **params)
    result = next((r for rs in sets for r in rs if 'ErrCode' in r), None)
    if not result or result.get('ErrCode') != 0 or not result.get('TrnID'):
        raise ValueError('PMV chưa xác nhận lưu phiếu. Hãy kiểm tra danh sách trước khi thử lại.')
    pk = result['TrnID']
    actual = header(c, pk)
    if (not actual or Decimal(actual['TienCoc']) != data['TienCoc'] or
            any((actual.get(k) or '') != data[k] for k in ('CustID', 'EmpID', 'Description'))):
        raise ValueError('Chưa xác minh được phiếu sau khi lưu. Hãy tải lại danh sách để kiểm tra.')
    expected = [O.item_info({**O.item_info(r), 'ProductDesc': O.product_description(r)})
                for r in cleaned_lines if r and not r.get('DELETE')]
    saved = lines(c, pk)
    if len(saved) != len(expected):
        raise ValueError('Số món lưu trên PMV chưa khớp. Hãy mở lại phiếu để kiểm tra.')
    for wanted, got in zip(expected, saved):
        if (any((got.get(k) or '') != (wanted.get(k) or '') for k in ('ProductDesc', 'ProductCode', 'Mode', 'GoldCode', 'Size', 'Notes')) or
                any(Decimal(got.get(k) or 0) != Decimal(wanted[k]) for k in
                    ('SL', 'TotalWeight', 'DiamondWeight', 'GoldWeight', 'TaskPrice'))):
            raise ValueError('Chi tiết món trên PMV chưa khớp. Hãy mở lại phiếu để kiểm tra.')
    return pk


@require_http_methods(['GET', 'POST'])
def xoa(request, pk):
    """🗑 Nút XÓA ở danh sách: popup xác nhận + Passcode rồi xóa hẳn phiếu (GĐ chốt 11/09/2026).

    Cùng khuôn popup xác nhận của màn bán: nói rõ phiếu/khách/tiền/người thao tác/hậu quả, nhập
    Passcode mới xóa. Không đi qua popup phiếu đầy đủ nên người dùng bấm một nhát là xong.
    """
    from .ban_coc import da_dung
    from .deposit_models import DepositEvent, DepositMoneyOperation

    authorize(request, 'can_delete')
    c = PmvClient(tag='datcoc-xoa:' + request.user.username)
    h = header(c, pk)
    if not h:
        raise Http404
    ctx = {'pk': pk, 'h': h, 'so_mon': len(lines(c, pk)), 'nguoi': request.user.get_full_name() or request.user.username,
           'username': request.user.username, 'luc': timezone.localtime(),
           'has_money': Decimal(h.get('CashPay') or 0) != 0 or Decimal(h.get('CardPay') or 0) != 0,
           'has_activity': DepositEvent.objects.filter(target=c.target, trn_id=pk).exists()
                           or DepositMoneyOperation.objects.filter(target=c.target, trn_id=pk).exists()}
    ctx['chan'] = _chan_xoa(c, pk, h, da_dung)
    if request.method == 'POST' and not ctx['chan']:
        try:
            kiem_passcode(request, pk, ctx)
            pu = pmv_user_for_web_user(request.user)
            if not pu:
                raise ValueError('Tài khoản chưa liên kết PMV.')
            with SAVE_LOCK:
                hien = header(c, pk)                       # đọc lại ngay trước khi ghi
                if not hien:
                    raise ValueError('Phiếu vừa bị xóa ở nơi khác.')
                ctx['chan'] = _chan_xoa(c, pk, hien, da_dung)
                if ctx['chan']:
                    raise ValueError(ctx['chan'])
                c.call('TRN_DATCOC_Del', write=True, p_TrnID=pk, p_UserUpd=pu.user_id,
                       p_TrnDateTime_Upd=hien['TrnDateTime_Upd'])
                if header(c, pk):
                    raise ValueError('PMV chưa xóa phiếu. Tải lại danh sách để kiểm tra.')
                don_sau_khi_xoa(c, pk, hien, request.user)
        except ValueError as exc:
            ctx['loi'] = str(exc)
        else:
            W.invalidate(c.target)
            phan_hoi = HttpResponse('')
            phan_hoi['HX-Trigger'] = json.dumps({'depositSaved': {
                'message': f"Đã xóa phiếu {h.get('BillCode') or pk}."}})
            return phan_hoi
    return render(request, 'pos/_dat_coc_xoa.html', ctx)


def _chan_xoa(c, pk, h, da_dung):
    """Câu chặn (rỗng = cho xóa). Hai chốt này là vật lý dữ liệu, Passcode không mở được."""
    from .deposit_models import DepositMoneyOperation

    if da_dung(c.target, pk):
        return 'Phiếu cọc đã được dùng cho hóa đơn bán — tiền cọc đã cấn vào đơn đó, không xóa được.'
    if DepositMoneyOperation.objects.filter(target=c.target, trn_id=pk, active_key__isnull=False).exists():
        return 'Phiếu đang có thao tác tiền chờ xử lý. Kiểm tra hoặc hủy yêu cầu đó trước.'
    if (h.get('Status') or '').strip() != 'W':
        return ('PMV chỉ xóa được phiếu đang Lưu tạm. Phiếu đã thu tiền vào két: vào Chứng từ cọc → '
                'Hoàn/Hủy thu cho tiền về két trước, rồi xóa.')
    return ''


@require_http_methods(['GET', 'POST'])
def popup(request, action, pk=None):
    if action not in ('view', 'add', 'edit', 'delete') or (action != 'add' and not pk) or (action == 'add' and pk):
        raise Http404
    if action == 'view' and request.method != 'GET':
        return HttpResponse(status=405)
    authorize(request, {'view': 'can_view', 'delete': 'can_delete'}.get(action, 'can_edit'))
    c = PmvClient(tag='datcoc:' + request.user.username)
    ctx = {'action': action, 'pk': pk, 'title': {'view': 'Chi tiết phiếu đặt hàng', 'add': 'Lập phiếu đặt hàng',
            'edit': 'Sửa phiếu đặt hàng', 'delete': 'Xóa phiếu đặt hàng'}[action]}
    ctx['can_delete'] = allowed(request.user, 'can_delete')
    ctx['can_approve'] = allowed(request.user, 'can_approve')
    try:
        h = header(c, pk) if pk else None
        if pk and not h:
            raise Http404
        ctx['h'] = decorate(h, order=True) if h else None
        ctx['has_money'] = bool(h and (Decimal(h.get('CashPay') or 0) != 0 or Decimal(h.get('CardPay') or 0) != 0))
        from .deposit_models import DepositEvent, DepositMoneyOperation
        ctx['has_activity'] = bool(h and (DepositEvent.objects.filter(target=c.target,trn_id=pk).exists() or DepositMoneyOperation.objects.filter(target=c.target,trn_id=pk).exists()))
        if h and action=='delete' and DepositMoneyOperation.objects.filter(target=c.target,trn_id=pk,active_key__isnull=False).exists():
            raise ValueError('Phiếu có thao tác tiền đang xử lý. Kiểm tra hoặc hủy yêu cầu chờ trước khi sửa/xóa.')
        # GĐ chốt 11/09/2026: phiếu đã có lịch sử vận hành hoặc đã ghi tiền vẫn XÓA ĐƯỢC, nhưng phải
        # XÁC NHẬN PASSCODE. Trước đây chặn cứng và bắt dùng "Hủy đặt hàng" — nay chỉ còn là cảnh báo.
        ctx['xoa_can_pass'] = action=='delete' and bool(ctx['has_activity'] or ctx['has_money'])
        # 11/09/2026: phiếu đã áp vào hóa đơn bán = KHÓA, chỉ còn xem (tiền cọc đã cấn vào đơn đó)
        from .ban_coc import da_dung
        ctx['da_ap_hoa_don'] = bool(h and da_dung(c.target, pk))
        if ctx['da_ap_hoa_don'] and action == 'delete':
            raise ValueError('Phiếu cọc đang áp dụng hóa đơn; gỡ áp dụng trước khi xóa phiếu.')
        if h and not h['estimate']:
            h['estimate'] = None
        old_lines = lines(c, pk) if pk else []
        ctx['lines'] = [O.item_info(r) for r in old_lines]
        if h:
            from .deposit_operations import overlay
            ctx['application_links']=c.query('SELECT b.TrnID,b.BillCode invoice_bill_code,b.Status '
                'FROM TRN_RT_BUYSELL_DatCoc l WITH (NOLOCK) JOIN TRN_RT_BUYSELL b WITH (NOLOCK) ON b.TrnID=l.TrnID '
                "WHERE l.DatCocID=? AND b.IsDel='0'",(pk,))
            work = W.classify(overlay(W.prepare({'headers':[h],'items':{pk:ctx['lines']}}),c.target))[0]
            if ctx['da_ap_hoa_don']:
                work.update(fulfilment='applied',state_name=W.FULFILMENT['applied'],state_css='applied',application_applied=True,active=False)
            ctx['work']=work
            ctx['can_operate']=allowed(request.user,'can_edit')
        ctx['unit'] = c.sys_param('UnitWeight') or 'đơn vị PMV'
        if h and h['mobile_order']:
            ctx['unit'] = 'chỉ (phiếu appMobile)'
        ctx['token'] = request.POST.get('token') if request.method == 'POST' else signing.dumps(
            {**version(c, h), 'nonce': uuid.uuid4().hex}, salt='datcoc')
        if action == 'delete' and h['Status'] != 'W':
            # Không phải luật của KHBL: proc vendor TRN_DATCOC_Del chỉ nhận phiếu W. Phiếu đã thu tiền
            # vào két (P) phải Hoàn/Hủy thu bên Chứng từ cọc cho tiền về trước, rồi mới xóa được.
            raise ValueError('PMV chỉ xóa được phiếu đang Lưu tạm. Phiếu đã thu tiền vào két: vào '
                             'Chứng từ cọc → Hoàn/Hủy thu cho tiền về két trước, rồi xóa.')
        initial_header = {**h, 'TienCoc': Decimal(h.get('TienCoc') or 0).quantize(Decimal('0.001')),
                          'Description': h['note'], 'PromiseDate': h['promise_date'], 'Estimate': h['estimate']} if h else None
        from .deposit_models import DepositOrderState
        state=DepositOrderState.objects.filter(target=c.target,trn_id=pk).first() if pk else None
        from . import deposit_editing as X
        edit_unlocked=action=='edit' and request.method=='POST' and X.unlocked(request,c,pk)
        restricted=action=='edit' and not edit_unlocked
        if edit_unlocked and (h['Status']!='W' or DepositMoneyOperation.objects.filter(target=c.target,trn_id=pk,active_key__isnull=False).exists()):
            raise ValueError('Phiếu đã khóa hoặc đang xử lý tiền. Chỉ cập nhật thông tin vận hành.')
        ctx.update(edit_locked=restricted,is_edit=action=='edit',can_unlock=bool(h and h['Status']=='W'),
                   edit_grant=request.POST.get('edit_grant','') if edit_unlocked else '',
                   saved_notes_count=len(X.entries(h,state)) if h else 0)
        if h and (ctx['da_ap_hoa_don'] or state and state.fulfilment=='applied'):
            from .deposit_completion import sync
            sync(c,pk,username=request.user.username)
            state=DepositOrderState.objects.filter(target=c.target,trn_id=pk).first()
            if state: ctx['work'].update(fulfilment=state.fulfilment,state_name=W.FULFILMENT[state.fulfilment])
        previous_progress=X.current_progress(state,ctx.get('work',{}))
        from . import deposit_photos as P
        ctx['photos']=P.context(state,pk)
        if action in ('add','edit') and request.method=='GET':
            ctx['token']=signing.dumps({**version(c,h),'source':X.source_stamp(h),'local':state.version if state else 0,'nonce':uuid.uuid4().hex},salt='datcoc')
        plan=(state.payment_plan if state else {}) or {}
        if h:
            initial_header.update(CashDeposit=(h.get('CashPay') or 0) if ctx['has_money'] else plan.get('cash',h.get('TienCoc') or 0),
                                  BankDeposit=(h.get('CardPay') or 0) if ctx['has_money'] else plan.get('bank',0))
            if not h.get('TienCoc'): initial_header.update(CashDeposit=0,BankDeposit=0)
            if state: initial_header.update(PromiseDate=state.promise,EmpID=state.employee_id)
        else:
            pu=pmv_user_for_web_user(request.user)
            initial_header={'TienCoc':0,'CashDeposit':0,'BankDeposit':0,'EmpID':pu.emp_id if pu else ''}
        form = DepositForm(request.POST if request.method == 'POST' else None, initial=initial_header)
        form.initial['NoteEntries']=X.entries(h,state) if h else []
        X.configure(form,state,ctx.get('work',{}))
        form.restricted_edit=restricted
        if restricted:
            for key in ('CustID','TienCoc','CashDeposit','BankDeposit','Estimate'): form.fields[key].disabled=True
            if h['Status']!='W': form.initial['Estimate']=None
        form.fields['Description'].widget=forms.HiddenInput()
        if request.method=='GET' or request.POST.get('description_format')=='json':
            form.fields['Description'].disabled=True
            form.initial['Description']=''
        form.fields['CustID'].widget=forms.HiddenInput()
        form.fields['TienCoc'].widget.attrs['readonly']=True
        form.fields['Estimate'].widget=forms.HiddenInput()
        if request.POST.get('editor_version')=='2' or request.method=='GET':
            form.fields['TienCoc'].required=False
            form.fields['Estimate'].disabled=True
        if action == 'edit' and ctx['has_money']:
            form.fields['TienCoc'].widget.attrs['readonly'] = True
            form.fields['TienCoc'].help_text = 'Phiếu đã ghi tiền: thu bổ sung hoặc hoàn cọc cần chứng từ riêng.'
            for key in ('CashDeposit','BankDeposit'): form.fields[key].widget.attrs['readonly']=True
        initial_lines = [{**O.item_info(row), **{k: row.get(k) if row.get(k) is not None else (1 if k == 'SL' else 0)
                           for k in ('SL', 'TotalWeight', 'DiamondWeight', 'GoldWeight', 'TaskPrice')}} for row in old_lines]
        for row in initial_lines:
            for key in ('TotalWeight', 'DiamondWeight', 'GoldWeight', 'TaskPrice'):
                row[key] = Decimal(row[key]).quantize(Decimal('0.001') if key == 'TaskPrice' else Decimal('0.00000001'))
        if action=='view':
            from . import deposit_editor as E
            pricing,_=E.price_context(c,bool(h['mobile_order']),state.pricing if state else None)
            view_lines=[]
            for row in initial_lines:
                row=dict(row)
                meta=pricing['rates'].get(row.get('GoldCode'),{})
                scale=Decimal(meta.get('native_per_display',1))
                row['GoldWeight']=row['GoldWeight']/scale
                row['unit']=meta.get('unit',ctx['unit'])
                row['amount']=None
                view_lines.append(row)
            totals=E.calculate(view_lines,pricing['rates']) if view_lines and all(r['GoldCode'] in pricing['rates'] for r in view_lines) else None
            if totals:
                for row,amount in zip(view_lines,totals['lines']): row['amount']=amount['amount']
            view_notes=[{**n,'display_date':dt.date.fromisoformat(n['date']) if n.get('date') else None} for n in X.entries(h,state)]
            ctx.update(view_lines=view_lines,view_totals=totals,view_notes=view_notes)
        if action in ('add', 'edit'):
            from . import deposit_editor as E
            pricing,price_token=E.price_context(c,bool(h and h['mobile_order']),state.pricing if state else None)
            if request.method=='POST' and not restricted and request.POST.get('editor_version')=='2':
                pricing=signing.loads(request.POST.get('pricing_token',''),salt='dc-pricing',max_age=7200)
                if pricing['target']!=c.target or pricing['mobile']!=bool(h and h['mobile_order']): raise ValueError('Đích hoặc đơn vị đã đổi. Mở lại phiếu.')
                price_token=request.POST['pricing_token']
            for row in initial_lines:
                scale=Decimal(pricing['rates'].get(row.get('GoldCode'),{}).get('native_per_display',1))
                for key in ('TotalWeight','DiamondWeight','GoldWeight'): row[key]=(row[key]/scale).quantize(Decimal('0.00000001'))
            line_data=request.POST if request.method=='POST' and not restricted else None
            stock_errors={}
            if request.method=='POST' and not restricted and request.POST.get('row_version')=='3':
                line_data,stock_errors=E.compact_data(c,request.POST,old_lines)
                line_data=E.keep_weight_precision(line_data,initial_lines)
            line_set = LineSet(line_data, initial=initial_lines, prefix='items')
            if restricted or (action=='add' and request.method=='GET'): line_set.min_num=0
            # DS nhân viên xếp theo TÊN GỌI, dùng chung toàn hệ (services.nhan_vien_ban — GĐ chốt 13/09/2026)
            from . import services as _S
            form.fields['EmpID'].choices = [('', 'Chọn nhân viên')] + [(r['EmpID'], r['EmpName'])
                                                                       for r in _S.nhan_vien_ban()]
            gold = [('', 'Loại vàng')] + [(code,code) for code in pricing['rates']]
            for f in line_set:
                f.fields['GoldCode'].choices = gold
            empty = line_set.empty_form
            empty.fields['GoldCode'].choices = gold
            for f in [*line_set,empty]:
                for key in ('Mode','TotalWeight','DiamondWeight','SL','Notes'): f.fields[key].widget=forms.HiddenInput()
                f.fields['GoldWeight'].required=request.method=='GET' or request.POST.get('editor_version')=='2'
                f.fields['GoldWeight'].widget.attrs['placeholder']='0'
                f.fields['ProductCode'].widget.attrs['placeholder']='Mã / tìm hàng…'
            for index,error in stock_errors.items(): line_set.forms[index].add_error('ProductCode',error)
            ctx.update(form=form, line_set=line_set, empty_line=empty,editor_rates=pricing['rates'],pricing_token=price_token,
                       created_date=h['TrnDate'] if h else timezone.localdate(),price_at=pricing['at'],
                       today_iso=timezone.localdate().isoformat(),
                       customer_initial={k:h.get(k) for k in ('CustID','CustName','Phone','CMND','Address')} if h else {},
                       can_receive=not restricted and ctx['can_approve'] and not ctx['has_money'] and not (h and h['mobile_order']))
            if h:
                from .deposit_money import financial
                money=financial(c,pk)
                ctx['application_text']='Đã áp dụng phiếu' if money['applied'] else 'Liên kết phiếu · cần đối soát' if money['links'] or money['changes'] else 'Chờ áp dụng phiếu'
                ctx['local_metadata']=h['Status']!='W'
        if request.method == 'POST':
            receive_on_save = action == 'add' or request.POST.get('payment_action') == 'receive'
            if restricted:
                if form.is_valid(): return X.save_direct(request,c,h,state,form,ctx['work'],old_lines,money)
                ctx['error']='Vui lòng kiểm tra các ô được đánh dấu bên dưới.'
            elif action in ('add', 'edit') and not (form.is_valid() & line_set.is_valid()):
                ctx['error'] = 'Vui lòng kiểm tra các ô được đánh dấu bên dưới.'
            else:
                prepared_photos=P.prepare(request.FILES) if action!='delete' else {}
                if action=='edit':
                    form.raw_description=X.check_notes(X.entries(h,state),form.raw_description)
                progress=form.cleaned_data.get('Progress','new') if action!='delete' else ''
                progress_notes=form.raw_description[len(X.entries(h,state)) if h else 0:] if action!='delete' else []
                if progress.startswith('cancel_') and receive_on_save:
                    raise ValueError('Không thu cọc cho đơn đã hủy. Chọn Lưu phiếu để cập nhật tiến độ hủy.')
                if action!='delete':
                    candidate=state or X.OP.base_state(c,h,ctx['lines']) if h else DepositOrderState(target=c.target)
                    X.set_progress(candidate,progress,ctx['lines'] if h else [O.item_info(i) for i in line_set.cleaned_data if i and not i.get('DELETE')],request.user,ctx.get('work',{}),progress_notes)
                quoted=None; save_lines=line_set.cleaned_data if action!='delete' else []
                if action!='delete' and request.POST.get('editor_version')=='2':
                    quoted=E.calculate(line_set.cleaned_data,pricing['rates'])
                    try: form.cleaned_data['Estimate']=form.fields['Estimate'].clean(quoted['total'])
                    except forms.ValidationError as exc: raise ValueError('Tạm tính vượt giới hạn lưu phiếu; kiểm tra số lượng và trọng lượng.') from exc
                    form.cleaned_data['Description']=O.pack_description(form.cleaned_data.get('PromiseDate'),form.raw_description,quoted['total'])
                    if O.description_length(form.cleaned_data['Description'])>500: raise ValueError('Ghi chú, ngày hẹn và tạm tính vượt giới hạn 500 ký tự của PMV. Rút gọn nội dung trước khi lưu.')
                    save_lines=E.native_lines(line_set.cleaned_data,pricing['rates'])
                    if receive_on_save and action != 'add' and not ctx['can_receive']:
                        raise ValueError('Phiếu hoặc tài khoản không đủ điều kiện thu cọc. Chọn Lưu phiếu.')
                with SAVE_LOCK:
                    current = header(c, pk) if pk else None
                    current_has_money = bool(current and (Decimal(current.get('CashPay') or 0) != 0 or Decimal(current.get('CardPay') or 0) != 0))
                    if current_has_money and action != 'delete' and form.cleaned_data['TienCoc'] != Decimal(current['TienCoc']):
                        raise ValueError('Không sửa đè tiền cọc đã ghi nhận. Cần xử lý bằng chứng từ thu/hoàn cọc.')
                    if action == 'delete' and ctx['xoa_can_pass']:
                        # GĐ chốt 11/09/2026: phiếu đã ghi tiền / đã có lịch sử vận hành thì XÓA phải qua Passcode
                        kiem_passcode(request, pk, ctx)
                    if current_has_money and quoted is not None and any(form.cleaned_data[k]!=Decimal(current.get(column) or 0) for k,column in [('CashDeposit','CashPay'),('BankDeposit','CardPay')]):
                        raise ValueError('Không sửa phân bổ cọc đã ghi; cần chứng từ thu/hoàn riêng.')
                    try:
                        submitted = signing.loads(request.POST.get('token', ''), salt='datcoc', max_age=7200)
                    except signing.BadSignature:
                        raise ValueError('Phiên thao tác đã hết hạn. Hãy đóng và mở lại popup.')
                    submitted.pop('nonce', None)
                    source=submitted.pop('source',None)
                    if source is not None and source!=X.source_stamp(current): raise ValueError('Trạng thái hoặc số tiền vừa thay đổi. Mở lại phiếu.')
                    local=submitted.pop('local',None)
                    if local is not None:
                        latest=DepositOrderState.objects.filter(target=c.target,trn_id=pk).first() if pk else None
                        if local!=(latest.version if latest else 0): raise ValueError('Phiếu vừa được cập nhật tiến độ hoặc lịch hẹn. Mở lại trước khi sửa.')
                    if submitted != version(c, current):
                        raise ValueError('Dữ liệu hoặc đích kết nối đã thay đổi. Hãy đóng và mở lại phiếu.')
                    if current and current['Status'] != 'W':
                        raise ValueError('Phiếu đã chuyển trạng thái; không thể sửa hoặc xóa.')
                    pu = pmv_user_for_web_user(request.user)
                    if not pu or not pu.shop_id:
                        raise ValueError('Tài khoản chưa được liên kết với nhân viên / cửa hàng PMV.')
                    if action!='delete' and receive_on_save:
                        if not pu.till_id or form.cleaned_data['TienCoc']<=0:
                            raise ValueError('Thu cọc cần số tiền lớn hơn 0 và tài khoản có két PMV.')
                    if action == 'delete':
                        c.call('TRN_DATCOC_Del', write=True, p_TrnID=pk, p_UserUpd=pu.user_id,
                               p_TrnDateTime_Upd=current['TrnDateTime_Upd'])
                        if header(c, pk):
                            raise ValueError('PMV chưa xóa phiếu. Hãy tải lại để kiểm tra.')
                        don_sau_khi_xoa(c, pk, current, request.user)
                    else:
                        # Token một lần được lưu bền trong MySQL, tránh gửi trùng phiếu mới.
                        from django.db import transaction
                        from apps.pos.models import DepositSubmission
                        with transaction.atomic():
                            token_hash = hashlib.sha256(request.POST['token'].encode()).hexdigest()
                            record, _ = DepositSubmission.objects.select_for_update().get_or_create(token=token_hash)
                            if record.completed:
                                raise ValueError('Yêu cầu này đã được xử lý. Hãy đóng popup và tải lại danh sách.')
                            record.completed = True
                            record.save(update_fields=['completed'])
                        # Khi kết nối có kết quả không chắc chắn, giữ token đã dùng để không tạo cọc hai lần.
                        saved_pk=save(c, form.cleaned_data, save_lines, pu, current)
                        W.invalidate(c.target)
                        from .deposit_models import DepositOrderState
                        with transaction.atomic():
                            state=DepositOrderState.objects.select_for_update().filter(target=c.target,trn_id=saved_pk).first()
                            if not state:
                                from .deposit_operations import base_state
                                state=base_state(c,header(c,saved_pk),[O.item_info(i) for i in lines(c,saved_pk)])
                            if state:
                                progress_items=[O.item_info(i) for i in lines(c,saved_pk)]
                                X.set_progress(state,progress,progress_items,request.user,ctx.get('work',{}),progress_notes)
                                state.extra_notes=[]
                                state.status='W'
                                state.promise=form.cleaned_data.get('PromiseDate')
                                state.employee_id=form.cleaned_data['EmpID']
                                state.employee_name=dict(form.fields['EmpID'].choices).get(state.employee_id,'')
                                if quoted is not None:
                                    state.pricing={**pricing,'lines':quoted['lines']}
                                    state.payment_plan={'cash':str(form.cleaned_data['CashDeposit']),'bank':str(form.cleaned_data['BankDeposit'])}
                                    if state.quote_amount!=quoted['total']: state.quote_status='estimate'
                                    state.quote_amount=quoted['total']
                                state.version+=1
                                P.save(state,prepared_photos,request.POST)
                                X.finish_progress(state,request.user)
                                DepositEvent.objects.create(target=c.target,trn_id=saved_pk,action='edit_info' if h else 'create',
                                    username=request.user.username,token=hashlib.sha256((request.POST['token']+':info').encode()).hexdigest(),
                                    data={'progress':progress,'passcode_confirmed':bool(edit_unlocked)})
                        if receive_on_save:
                            from . import deposit_money as F
                            actual=header(c,saved_pk)
                            op=DepositMoneyOperation(target=c.target,trn_id=saved_pk,kind='receive',amount=form.cleaned_data['TienCoc'],
                                cash=form.cleaned_data.get('CashDeposit') if form.cleaned_data.get('CashDeposit') is not None else form.cleaned_data['TienCoc'],
                                bank=form.cleaned_data.get('BankDeposit') or Decimal(0),user_id=pu.user_id,till_id=pu.till_id,
                                username=request.user.username,active_key=c.target+':'+saved_pk,token=hashlib.sha256((request.POST['token']+':receive').encode()).hexdigest(),
                                evidence={'stamp':F.money_stamp(c,actual),'note':'Nhân viên xác nhận đã nhận tiền khi lưu phiếu',
                                          'staff_confirmed': True, 'reconcile_later': True, 'money_policy': 'fixed_total_v1'})
                            try:
                                F.validate_operation(op,F.financial(c,saved_pk)); op.save(); F.log_event(op); op=F.execute(op)
                            except Exception:
                                log.exception('Phiếu đã lưu nhưng thu cọc cần kiểm tra: %s',saved_pk)
                                W.invalidate(c.target)
                                from .deposit_messages import saved
                                return saved('Đã lưu phiếu '+saved_pk+'. Thu cọc chưa hoàn tất; mở Chứng từ cọc để kiểm tra trước khi thu lại.','warning')
                            from .deposit_messages import saved
                            return saved('Đã lưu phiếu. '+F.STATES[op.status]+': '+op.message,'success' if op.status=='done' else 'warning' if op.status=='queued' else 'error')
                W.invalidate(c.target)
                response = HttpResponse('')
                import json
                response['HX-Trigger'] = json.dumps({'depositSaved': {'message': 'Đã xóa phiếu cọc.' if action == 'delete' else 'Đã lưu phiếu cọc.'}})
                return X.saved_response(saved_pk,progress if progress!=previous_progress else '',request) if action!='delete' else response
    except Http404:
        raise
    except signing.BadSignature:
        ctx['error']='Phiên giá đã hết hạn hoặc thay đổi. Đóng và mở lại phiếu.'
    except ValueError as exc:
        ctx['error'] = str(exc)
    except Exception:
        log.exception('Thao tác đặt cọc không thành công')
        ctx['error'] = 'Không thể hoàn tất thao tác. Kiểm tra kết nối, quyền ghi và tải lại danh sách trước khi thử lại.'
    if action == 'view' and request.GET.get('print') == '1':
        from . import deposit_print_layout as PL
        ctx['deposit_print_calibration'] = PL.css()
        if not ctx.get('error'):
            import segno
            ctx['receipt_qr'] = segno.make(pk, micro=False).svg_data_uri(scale=3)
        response = render(request, 'pos/dat_coc_print.html', ctx)
        response['Cache-Control'] = 'private, no-store'
        response['X-Frame-Options'] = 'SAMEORIGIN'
        return response
    return render(request, 'pos/_dat_coc_popup.html', ctx)
