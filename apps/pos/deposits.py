"""Phiếu cọc PMV: đọc header/chi tiết, CRUD phiếu W qua proc vendor."""
import datetime as dt
import hashlib
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


class FilterForm(forms.Form):
    tab = forms.ChoiceField(choices=O.TABS)
    q = forms.CharField(required=False, max_length=100, label='Tìm phiếu / khách / SĐT / nhân viên',
                        widget=forms.TextInput(attrs={'placeholder': 'Mã phiếu, tên khách, SĐT hoặc nhân viên…'}))
    start = forms.DateField(required=False, label='Từ ngày', widget=forms.DateInput(attrs={'type': 'date'}))
    end = forms.DateField(required=False, label='Đến ngày', widget=forms.DateInput(attrs={'type': 'date'}))
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


class DepositForm(forms.Form):
    CustID = forms.CharField(label='Mã khách hàng', max_length=15,
                            widget=forms.TextInput(attrs={'placeholder': 'Chọn khách bằng ô tìm kiếm bên dưới'}))
    EmpID = forms.ChoiceField(label='Nhân viên phụ trách')
    TienCoc = forms.DecimalField(label='Tiền cọc (₫)', min_value=0, max_digits=18, decimal_places=3)
    Description = forms.CharField(label='Ghi chú phiếu', required=False, max_length=500,
                                  widget=forms.Textarea(attrs={'rows': 2}))
    PromiseDate = forms.DateField(label='Ngày hẹn lấy hàng', required=False,
                                 widget=forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'))
    Estimate = forms.DecimalField(label='Tổng tiền tạm tính (₫)', required=False, min_value=0, max_digits=18, decimal_places=3)
    CashDeposit = forms.DecimalField(label='Cọc tiền mặt',required=False,min_value=0,max_digits=18,decimal_places=3)
    BankDeposit = forms.DecimalField(label='Cọc chuyển khoản',required=False,min_value=0,max_digits=18,decimal_places=3)

    def clean(self):
        data = super().clean()
        self.raw_description=data.get('Description','')
        if self.data.get('editor_version')=='2':
            for key in ('CashDeposit','BankDeposit'):
                if data.get(key) is None: self.add_error(key,'Nhập số tiền, hoặc 0 nếu không có.')
            if data.get('CashDeposit') is not None and data.get('BankDeposit') is not None:
                try: data['TienCoc']=self.fields['TienCoc'].clean(data['CashDeposit']+data['BankDeposit'])
                except forms.ValidationError as exc: self.add_error('TienCoc',exc)
        if data.get('Estimate') and not data.get('PromiseDate'):
            self.add_error('PromiseDate', 'Nhập ngày hẹn để lưu thông tin đặt hàng và tiền tạm tính.')
        if 'Description' in data:
            packed = O.pack_description(data.get('PromiseDate'), data['Description'], data.get('Estimate'))
            if len(packed) > 500:
                self.add_error('Description', 'Ngày hẹn, ghi chú và tiền tạm tính gộp lại tối đa 500 ký tự.')
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
                                   decimal_places=8, widget=forms.NumberInput(attrs={'placeholder': 'Tự tính nếu bỏ trống'}))
    TaskPrice = forms.DecimalField(label='Tiền công (₫)', min_value=0, max_digits=18, decimal_places=3, initial=0)
    Size = forms.CharField(label='Ni / kích thước', required=False, max_length=200)
    Notes = forms.CharField(label='Yêu cầu chế tác', required=False, max_length=1000)
    DisplayUnits = forms.BooleanField(required=False,widget=forms.HiddenInput,initial=True)

    def clean(self):
        d = super().clean()
        mode = d.get('Mode') or 'new'
        if mode == 'stock' and not d.get('ProductCode'):
            self.add_error('ProductCode', 'Chọn sản phẩm có sẵn trong kho.')
        if mode == 'stock' and d.get('ProductCode'):
            d['Notes'] = f"SP:{d['ProductCode']} | {d.get('Notes') or ''}"
        elif d.get('ProductCode'):
            d['Notes'] = f"MA_DAT:{d['ProductCode']} | {d.get('Notes') or ''}"
        d['Mode'] = mode
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
    return c.query("SELECT d.TrnDTID, d.ProductDesc, d.GoldCode, d.SL, d.TaskPrice, d.Size, d.Notes, "
                   f"CASE WHEN {mobile} THEN d.TotalWeight WHEN g.WeightUnit='L' THEN d.TotalWeight/dbo.fun_GetHS() ELSE d.TotalWeight END TotalWeight, "
                   f"CASE WHEN {mobile} THEN d.DiamondWeight WHEN g.WeightUnit='L' THEN d.DiamondWeight/dbo.fun_GetHS() ELSE d.DiamondWeight END DiamondWeight, "
                   f"CASE WHEN {mobile} THEN d.GoldWeight WHEN g.WeightUnit='L' THEN d.GoldWeight/dbo.fun_GetHS() ELSE d.GoldWeight END GoldWeight "
                   "FROM TRN_DATCOC_DT d WITH (NOLOCK) JOIN TRN_DATCOC h WITH (NOLOCK) ON h.TrnID=d.TrnID "
                   "LEFT JOIN I_GOLD g WITH (NOLOCK) ON g.GoldCode=d.GoldCode "
                   "WHERE d.TrnID=? ORDER BY d.TrnDTID", (pk,))


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
    if params['tab'] in ('money', 'orders'):
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
        rows = [r for r in W.classify(overlay(W.prepare(snapshot),c.target)) if W.matches(r, form.cleaned_data)]
        ctx.update(W.operations_report(rows), stats=W.summarize(rows), as_of=snapshot['as_of'])
        if request.GET.get('export') == 'csv':
            response = HttpResponse(content_type='text/csv; charset=utf-8')
            response['Content-Disposition'] = 'attachment; filename="dat-hang-cong-viec.csv"'
            response.write('\ufeff')
            writer = csv.writer(response)
            writer.writerow(['Mã phiếu','Ngày đặt','Khách','SĐT','Tiến độ','Ngày hẹn','Phụ trách','Việc tiếp theo','Cọc ghi trên phiếu (VND)','Trạng thái đối soát','Dữ liệu đến'])
            def cell(value):
                text = str(value or '')
                return "'" + text if text.lstrip().startswith(('=','+','-','@')) or text.startswith(('\t','\r')) else text
            for r in rows:
                writer.writerow([cell(r.get(k)) for k in ('BillCode','TrnDate','CustName','Phone','state_name','promise_date','responsible','next_task')]
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
        rows = c.query('SELECT TOP 12 p.ProductCode,p.ProductDesc,p.GoldCode,p.TotalWeight,p.DiamondWeight, '
                       'p.RingSize,g.WeightUnit FROM T_PRODUCT p WITH (NOLOCK) '
                       'LEFT JOIN I_GOLD g WITH (NOLOCK) ON g.GoldCode=p.GoldCode '
                       "WHERE p.Status='I' AND (p.ProductCode LIKE ? OR p.ProductDesc LIKE ?) ORDER BY p.ProductCode", (search, search))
        factor = Decimal('100') if request.GET.get('units') in ('mobile','display') else Decimal(c.query('SELECT dbo.fun_GetHS() factor')[0]['factor'])
        for row in rows:
            scale = factor if row['WeightUnit'] == 'L' else Decimal(1)
            for key in ('TotalWeight', 'DiamondWeight'):
                row[key] = Decimal(row[key] or 0) / scale
            row['GoldWeight'] = row['TotalWeight'] - row['DiamondWeight']
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
        for key in ('ProductDesc', 'GoldCode', 'TotalWeight', 'DiamondWeight', 'GoldWeight', 'TaskPrice', 'Size', 'SL', 'Notes'):
            value = row.get(key) or '' if key in ('Size', 'Notes') else row[key]
            SubElement(node, key).text = format(value, 'f') if isinstance(value, Decimal) else str(value)
        SubElement(node, 'TrnID').text = pk
    return tostring(root, encoding='unicode')


def save(c, data, cleaned_lines, user, old=None):
    if not c.query('SELECT CustID FROM I_CUSTOMER WITH (NOLOCK) WHERE CustID=?', (data['CustID'],)):
        raise ValueError('Mã khách hàng không tồn tại. Hãy tìm và chọn khách trong danh sách.')
    pk = old['TrnID'] if old else ''
    stock_codes = {r.get('ProductCode') for r in cleaned_lines if r and not r.get('DELETE') and r.get('Mode') == 'stock'}
    old_codes = {O.split_stock_note(r.get('Notes'))[0] for r in lines(c, pk)} if stock_codes and old else set()
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
    expected = [r for r in cleaned_lines if r and not r.get('DELETE')]
    saved = lines(c, pk)
    if len(saved) != len(expected):
        raise ValueError('Số món lưu trên PMV chưa khớp. Hãy mở lại phiếu để kiểm tra.')
    for wanted, got in zip(expected, saved):
        if (any((got.get(k) or '') != (wanted.get(k) or '') for k in ('ProductDesc', 'GoldCode', 'Size', 'Notes')) or
                any(Decimal(got.get(k) or 0) != Decimal(wanted[k]) for k in
                    ('SL', 'TotalWeight', 'DiamondWeight', 'GoldWeight', 'TaskPrice'))):
            raise ValueError('Chi tiết món trên PMV chưa khớp. Hãy mở lại phiếu để kiểm tra.')
    return pk


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
        if h and action in ('edit','delete') and DepositMoneyOperation.objects.filter(target=c.target,trn_id=pk,active_key__isnull=False).exists():
            raise ValueError('Phiếu có thao tác tiền đang xử lý. Kiểm tra hoặc hủy yêu cầu chờ trước khi sửa/xóa.')
        if action=='delete' and ctx['has_activity']:
            raise ValueError('Phiếu đã có lịch sử vận hành/chứng từ. Dùng Hủy đặt hàng để giữ lịch sử, không xóa phiếu.')
        if h and not h['estimate']:
            h['estimate'] = None
        old_lines = lines(c, pk) if pk else []
        ctx['lines'] = [O.item_info(r) for r in old_lines]
        if h:
            from .deposit_operations import overlay
            work = W.classify(overlay(W.prepare({'headers':[h],'items':{pk:ctx['lines']}}),c.target))[0]
            ctx['work']=work
            ctx['can_operate']=allowed(request.user,'can_edit')
        ctx['unit'] = c.sys_param('UnitWeight') or 'đơn vị PMV'
        if h and h['mobile_order']:
            ctx['unit'] = 'chỉ (phiếu appMobile)'
        ctx['token'] = request.POST.get('token') if request.method == 'POST' else signing.dumps(
            {**version(c, h), 'nonce': uuid.uuid4().hex}, salt='datcoc')
        if action in ('edit', 'delete') and h['Status'] != 'W':
            raise ValueError('Chỉ phiếu Lưu tạm được sửa hoặc xóa. Phiếu này đã chuyển trạng thái.')
        if action == 'delete' and ctx['has_money']:
            raise ValueError('Phiếu đã ghi tiền mặt/chuyển khoản. Cần xử lý cọc theo chứng từ trước khi hủy, không xóa phiếu.')
        initial_header = {**h, 'TienCoc': Decimal(h.get('TienCoc') or 0).quantize(Decimal('0.001')),
                          'Description': h['note'], 'PromiseDate': h['promise_date'], 'Estimate': h['estimate']} if h else None
        from .deposit_models import DepositOrderState
        state=DepositOrderState.objects.filter(target=c.target,trn_id=pk).first() if pk else None
        if action in ('add','edit') and request.method=='GET':
            ctx['token']=signing.dumps({**version(c,h),'local':state.version if state else 0,'nonce':uuid.uuid4().hex},salt='datcoc')
        plan=(state.payment_plan if state else {}) or {}
        if h:
            initial_header.update(CashDeposit=(h.get('CashPay') or 0) if ctx['has_money'] else plan.get('cash'),
                                  BankDeposit=(h.get('CardPay') or 0) if ctx['has_money'] else plan.get('bank'))
            if not h.get('TienCoc'): initial_header.update(CashDeposit=0,BankDeposit=0)
            if state: initial_header.update(PromiseDate=state.promise,EmpID=state.employee_id)
        else:
            pu=pmv_user_for_web_user(request.user)
            initial_header={'TienCoc':0,'CashDeposit':0,'BankDeposit':0,'EmpID':pu.emp_id if pu else ''}
        form = DepositForm(request.POST if request.method == 'POST' else None, initial=initial_header)
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
        if action in ('add', 'edit'):
            from . import deposit_editor as E
            pricing,price_token=E.price_context(c,bool(h and h['mobile_order']),state.pricing if state else None)
            if request.method=='POST' and request.POST.get('editor_version')=='2':
                pricing=signing.loads(request.POST.get('pricing_token',''),salt='dc-pricing',max_age=7200)
                if pricing['target']!=c.target or pricing['mobile']!=bool(h and h['mobile_order']): raise ValueError('Đích hoặc đơn vị đã đổi. Mở lại phiếu.')
                price_token=request.POST['pricing_token']
            for row in initial_lines:
                scale=Decimal(pricing['rates'].get(row.get('GoldCode'),{}).get('native_per_display',1))
                for key in ('TotalWeight','DiamondWeight','GoldWeight'): row[key]=(row[key]/scale).quantize(Decimal('0.00000001'))
            line_set = LineSet(request.POST if request.method == 'POST' else None, initial=initial_lines, prefix='items')
            if action=='add' and request.method=='GET': line_set.min_num=0
            form.fields['EmpID'].choices = [('', 'Chọn nhân viên')] + [(r['EmpID'], r['EmpName']) for r in c.query(
                'SELECT EmpID,EmpName FROM T_EMPLOYEE WITH (NOLOCK) ORDER BY EmpName')]
            gold = [('', 'Loại vàng')] + [(code,code) for code in pricing['rates']]
            for f in line_set:
                f.fields['GoldCode'].choices = gold
            empty = line_set.empty_form
            empty.fields['GoldCode'].choices = gold
            for f in [*line_set,empty]:
                for key in ('Mode','TotalWeight'): f.fields[key].widget=forms.HiddenInput()
                f.fields['GoldWeight'].required=request.method=='GET' or request.POST.get('editor_version')=='2'
                f.fields['GoldWeight'].widget.attrs['placeholder']='0'
                f.fields['ProductCode'].widget.attrs['placeholder']='Mã / tìm hàng…'
            ctx.update(form=form, line_set=line_set, empty_line=empty,editor_rates=pricing['rates'],pricing_token=price_token,
                       created_date=h['TrnDate'] if h else timezone.localdate(),price_at=pricing['at'],
                       customer_initial={k:h.get(k) for k in ('CustID','CustName','Phone','CMND','Address')} if h else {},
                       can_receive=ctx['can_approve'] and not ctx['has_money'] and not (h and h['mobile_order']))
        if request.method == 'POST':
            if action in ('add', 'edit') and not (form.is_valid() & line_set.is_valid()):
                ctx['error'] = 'Vui lòng kiểm tra các ô được đánh dấu bên dưới.'
            else:
                quoted=None; save_lines=line_set.cleaned_data if action!='delete' else []
                if action!='delete' and request.POST.get('editor_version')=='2':
                    quoted=E.calculate(line_set.cleaned_data,pricing['rates'])
                    try: form.cleaned_data['Estimate']=form.fields['Estimate'].clean(quoted['total'])
                    except forms.ValidationError as exc: raise ValueError('Tạm tính vượt giới hạn lưu phiếu; kiểm tra số lượng và trọng lượng.') from exc
                    form.cleaned_data['Description']=O.pack_description(form.cleaned_data.get('PromiseDate'),form.raw_description,quoted['total'])
                    if len(form.cleaned_data['Description'])>500: raise ValueError('Ghi chú cùng ngày hẹn/tạm tính vượt 500 ký tự.')
                    save_lines=E.native_lines(line_set.cleaned_data,pricing['rates'])
                    if request.POST.get('payment_action')=='receive' and not ctx['can_receive']:
                        raise ValueError('Phiếu hoặc tài khoản không đủ điều kiện thu cọc. Chọn Lưu phiếu.')
                with SAVE_LOCK:
                    current = header(c, pk) if pk else None
                    current_has_money = bool(current and (Decimal(current.get('CashPay') or 0) != 0 or Decimal(current.get('CardPay') or 0) != 0))
                    if current_has_money and (action == 'delete' or form.cleaned_data['TienCoc'] != Decimal(current['TienCoc'])):
                        raise ValueError('Không sửa đè hoặc xóa tiền cọc đã ghi nhận. Cần xử lý bằng chứng từ thu/hoàn cọc.')
                    if current_has_money and quoted is not None and any(form.cleaned_data[k]!=Decimal(current.get(column) or 0) for k,column in [('CashDeposit','CashPay'),('BankDeposit','CardPay')]):
                        raise ValueError('Không sửa phân bổ cọc đã ghi; cần chứng từ thu/hoàn riêng.')
                    try:
                        submitted = signing.loads(request.POST.get('token', ''), salt='datcoc', max_age=7200)
                    except signing.BadSignature:
                        raise ValueError('Phiên thao tác đã hết hạn. Hãy đóng và mở lại popup.')
                    submitted.pop('nonce', None)
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
                    if action!='delete' and request.POST.get('payment_action')=='receive':
                        if not pu.till_id or form.cleaned_data['TienCoc']<=0:
                            raise ValueError('Thu cọc cần số tiền lớn hơn 0 và tài khoản có két PMV.')
                    if action == 'delete':
                        c.call('TRN_DATCOC_Del', write=True, p_TrnID=pk, p_UserUpd=pu.user_id,
                               p_TrnDateTime_Upd=current['TrnDateTime_Upd'])
                        if header(c, pk):
                            raise ValueError('PMV chưa xóa phiếu. Hãy tải lại để kiểm tra.')
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
                            if not state and quoted is not None:
                                from .deposit_operations import base_state
                                state=base_state(c,header(c,saved_pk),[O.item_info(i) for i in lines(c,saved_pk)])
                            if state:
                                state.promise=form.cleaned_data.get('PromiseDate')
                                state.employee_id=form.cleaned_data['EmpID']
                                state.employee_name=dict(form.fields['EmpID'].choices).get(state.employee_id,'')
                                if quoted is not None:
                                    state.pricing={**pricing,'lines':quoted['lines']}
                                    state.payment_plan={'cash':str(form.cleaned_data['CashDeposit']),'bank':str(form.cleaned_data['BankDeposit'])}
                                    if state.quote_amount!=quoted['total']: state.quote_status='estimate'
                                    state.quote_amount=quoted['total']
                                state.version+=1; state.save()
                        if quoted is not None and request.POST.get('payment_action')=='receive':
                            from . import deposit_money as F
                            actual=header(c,saved_pk)
                            op=DepositMoneyOperation(target=c.target,trn_id=saved_pk,kind='receive',amount=form.cleaned_data['TienCoc'],
                                cash=form.cleaned_data['CashDeposit'],bank=form.cleaned_data['BankDeposit'],user_id=pu.user_id,till_id=pu.till_id,
                                username=request.user.username,active_key=c.target+':'+saved_pk,token=hashlib.sha256((request.POST['token']+':receive').encode()).hexdigest(),
                                evidence={'stamp':F.money_stamp(c,actual),'note':'Thu cọc từ popup đặt hàng'})
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
                return response
    except Http404:
        raise
    except signing.BadSignature:
        ctx['error']='Phiên giá đã hết hạn hoặc thay đổi. Đóng và mở lại phiếu.'
    except ValueError as exc:
        ctx['error'] = str(exc)
    except Exception:
        log.exception('Thao tác đặt cọc không thành công')
        ctx['error'] = 'Không thể hoàn tất thao tác. Kiểm tra kết nối, quyền ghi và tải lại danh sách trước khi thử lại.'
    return render(request, 'pos/_dat_coc_popup.html', ctx)
