"""Phiếu cọc PMV: đọc header/chi tiết, CRUD phiếu W qua proc vendor."""
import datetime as dt
import hashlib
import logging
import threading
import uuid
from decimal import Decimal
from xml.etree.ElementTree import Element, SubElement, tostring

from django import forms
from django.core import signing
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.forms import formset_factory
from django.http import Http404, HttpResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods

from apps.pmv.client import PmvClient
from apps.pmv.models import UserModuleAccess, pmv_user_for_web_user

log = logging.getLogger(__name__)
SAVE_LOCK = threading.Lock()
STATES = {'W': 'Lưu tạm', 'P': 'Đã thanh toán', 'C': 'Đã sử dụng',
          'D': 'Trạng thái D', 'R': 'Trạng thái R'}


def allowed(user, action):
    return user.is_authenticated and (user.is_superuser or UserModuleAccess.objects.filter(
        user=user, module='DAT_COC', can_view=True, **({action: True} if action != 'can_view' else {})).exists())


def authorize(request, action='can_view'):
    if not allowed(request.user, action):
        raise PermissionDenied('Bạn chưa được cấp quyền ĐẶT-CỌC.')


class FilterForm(forms.Form):
    tab = forms.ChoiceField(choices=[('money', 'Tiền cọc'), ('orders', 'Đặt hàng')])
    q = forms.CharField(required=False, max_length=100, label='Tìm phiếu / khách / SĐT',
                        widget=forms.TextInput(attrs={'placeholder': 'Số phiếu, tên khách, số điện thoại…'}))
    start = forms.DateField(required=False, label='Từ ngày', widget=forms.DateInput(attrs={'type': 'date'}))
    end = forms.DateField(required=False, label='Đến ngày', widget=forms.DateInput(attrs={'type': 'date'}))
    status = forms.ChoiceField(required=False, label='Trạng thái', choices=[('', 'Tất cả trạng thái'), *STATES.items()])

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
    TienCoc = forms.DecimalField(label='Tiền cọc (₫)', min_value=0, max_digits=18, decimal_places=0)
    Description = forms.CharField(label='Ghi chú phiếu', required=False, max_length=500,
                                  widget=forms.Textarea(attrs={'rows': 2}))


class OrderLineForm(forms.Form):
    ProductDesc = forms.CharField(label='Món đặt hàng', max_length=500)
    GoldCode = forms.ChoiceField(label='Loại vàng')
    SL = forms.IntegerField(label='Số lượng', min_value=1, max_value=9999, initial=1)
    TotalWeight = forms.DecimalField(label='Tổng TL', min_value=0, max_digits=19, decimal_places=8, initial=0)
    DiamondWeight = forms.DecimalField(label='TL hột', min_value=0, max_digits=19, decimal_places=8, initial=0)
    TaskPrice = forms.DecimalField(label='Tiền công (₫)', min_value=0, max_digits=18, decimal_places=3, initial=0)
    Size = forms.CharField(label='Ni / kích thước', required=False, max_length=200)
    Notes = forms.CharField(label='Yêu cầu chế tác', required=False, max_length=1000)

    def clean(self):
        d = super().clean()
        if d.get('TotalWeight') is not None and d.get('DiamondWeight') is not None:
            if d['DiamondWeight'] > d['TotalWeight']:
                raise forms.ValidationError('Trọng lượng hột không được lớn hơn tổng trọng lượng.')
            d['GoldWeight'] = d['TotalWeight'] - d['DiamondWeight']
        return d


LineSet = formset_factory(OrderLineForm, extra=0, can_delete=True, max_num=50, validate_max=True, absolute_max=60)


def header(c, pk):
    rows = c.query('SELECT d.*, k.CustName, k.Phone FROM TRN_DATCOC d WITH (NOLOCK) '
                   'LEFT JOIN I_CUSTOMER k WITH (NOLOCK) ON k.CustID=d.CustID WHERE d.TrnID=?', (pk,))
    return rows[0] if rows else None


def lines(c, pk):
    # Cùng quy đổi với TRN_DATCOC_Get, đối xứng Ins/Upd của vendor.
    return c.query("SELECT d.TrnDTID, d.ProductDesc, d.GoldCode, d.SL, d.TaskPrice, d.Size, d.Notes, "
                   "CASE WHEN g.WeightUnit='L' THEN d.TotalWeight/dbo.fun_GetHS() ELSE d.TotalWeight END TotalWeight, "
                   "CASE WHEN g.WeightUnit='L' THEN d.DiamondWeight/dbo.fun_GetHS() ELSE d.DiamondWeight END DiamondWeight, "
                   "CASE WHEN g.WeightUnit='L' THEN d.GoldWeight/dbo.fun_GetHS() ELSE d.GoldWeight END GoldWeight "
                   "FROM TRN_DATCOC_DT d WITH (NOLOCK) LEFT JOIN I_GOLD g WITH (NOLOCK) ON g.GoldCode=d.GoldCode "
                   "WHERE d.TrnID=? ORDER BY d.TrnDTID", (pk,))


def decorate(row):
    row['state_name'] = STATES.get(row.get('Status'), 'Trạng thái ' + str(row.get('Status') or '—'))
    return row


def listing(c, data, page):
    where, args = ['1=1'], []
    if data['tab'] == 'orders':
        where.append('EXISTS (SELECT 1 FROM TRN_DATCOC_DT x WITH (NOLOCK) WHERE x.TrnID=d.TrnID)')
    for field, op in [('start', '>='), ('end', '<')]:
        if data.get(field):
            value = data[field] + (dt.timedelta(days=1) if field == 'end' else dt.timedelta())
            where.append(f'd.TrnDate {op} ?')
            args.append(value.isoformat())
    if data.get('status'):
        where.append('d.Status=?')
        args.append(data['status'])
    if data.get('q'):
        value = '%' + data['q'].replace('[', '[[]').replace('%', '[%]').replace('_', '[_]') + '%'
        where.append('(d.BillCode LIKE ? OR k.CustName LIKE ? OR k.Phone LIKE ? OR d.TrnID LIKE ?)')
        args += [value] * 4
    source = (' FROM TRN_DATCOC d WITH (NOLOCK) LEFT JOIN I_CUSTOMER k WITH (NOLOCK) '
              'ON k.CustID=d.CustID WHERE ' + ' AND '.join(where))
    stats = c.query("SELECT COUNT(*) total, COALESCE(SUM(d.TienCoc),0) amount, "
                    "COALESCE(SUM(CASE WHEN d.Status='W' THEN 1 ELSE 0 END),0) drafts, "
                    "COALESCE(SUM(CASE WHEN d.Status='P' THEN d.TienCoc ELSE 0 END),0) available" + source, args)[0]
    pager = Paginator(range(stats['total']), 30).get_page(page)
    rows = c.query('SELECT * FROM (SELECT d.TrnID,d.BillCode,d.TrnDate,d.TienCoc,d.Status,d.Description, '
                   'k.CustName,k.Phone, (SELECT COUNT(*) FROM TRN_DATCOC_DT x WITH (NOLOCK) '
                   'WHERE x.TrnID=d.TrnID) line_count, ROW_NUMBER() OVER (ORDER BY d.TrnDate DESC,d.TrnID DESC) rn'
                   + source + ') results WHERE rn BETWEEN ? AND ? ORDER BY rn',
                   args + [pager.start_index(), pager.end_index()])
    return dict(rows=[decorate(r) for r in rows], stats=stats, pager=pager)


@require_GET
def index(request):
    authorize(request)
    params = request.GET.copy()
    params.setdefault('tab', 'money')
    form = FilterForm(params)
    ctx = dict(nav_active='datcoc', form=form, tab=params['tab'],
               can_edit=allowed(request.user, 'can_edit'), can_delete=allowed(request.user, 'can_delete'))
    if form.is_valid():
        try:
            ctx.update(listing(PmvClient(tag='datcoc-list'), form.cleaned_data, params.get('page', 1)))
        except Exception:
            log.exception('Không tải được danh sách đặt cọc')
            ctx['error'] = 'Không kết nối được dữ liệu đặt cọc. Vui lòng thử lại.'
    return render(request, 'pos/_dat_coc_list.html' if request.headers.get('HX-Request') else 'pos/dat_coc.html', ctx)


@require_GET
def customers(request):
    authorize(request, 'can_edit')
    q = request.GET.get('customer_q', '').strip()[:100]
    rows = []
    error = ''
    if len(q) >= 2:
        try:
            term = '%' + q.replace('[', '[[]').replace('%', '[%]').replace('_', '[_]') + '%'
            rows = PmvClient(tag='datcoc-customer').query(
                'SELECT TOP 12 CustID,CustName,Phone FROM I_CUSTOMER WITH (NOLOCK) '
                'WHERE CustName LIKE ? OR Phone LIKE ? OR CustID LIKE ? ORDER BY CustName', (term, term, term))
        except Exception:
            log.exception('Tìm khách đặt cọc'); error = 'Không tải được khách hàng.'
    return render(request, 'pos/_dat_coc_customers.html', {'customers': rows, 'query': q, 'error': error})


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
            SubElement(node, key).text = str(row.get(key) or '') if key in ('Size', 'Notes') else str(row[key])
        SubElement(node, 'TrnID').text = pk
    return tostring(root, encoding='unicode')


def save(c, data, cleaned_lines, user, old=None):
    if not c.query('SELECT CustID FROM I_CUSTOMER WITH (NOLOCK) WHERE CustID=?', (data['CustID'],)):
        raise ValueError('Mã khách hàng không tồn tại. Hãy tìm và chọn khách trong danh sách.')
    pk = old['TrnID'] if old else ''
    params = dict(p_CustID=data['CustID'], p_TienCoc=str(data['TienCoc']), p_EmpID=data['EmpID'],
                  p_Description=data['Description'], p_XML=xml_lines(cleaned_lines, pk))
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
    if not actual or Decimal(actual['TienCoc']) != data['TienCoc'] or actual['CustID'] != data['CustID']:
        raise ValueError('Chưa xác minh được phiếu sau khi lưu. Hãy tải lại danh sách để kiểm tra.')
    if len(lines(c, pk)) != sum(bool(r) and not r.get('DELETE', False) for r in cleaned_lines):
        raise ValueError('Số món lưu trên PMV chưa khớp. Hãy mở lại phiếu để kiểm tra.')
    return pk


@require_http_methods(['GET', 'POST'])
def popup(request, action, pk=None):
    if action not in ('view', 'add', 'edit', 'delete') or (action != 'add' and not pk):
        raise Http404
    if action == 'view' and request.method != 'GET':
        return HttpResponse(status=405)
    authorize(request, {'view': 'can_view', 'delete': 'can_delete'}.get(action, 'can_edit'))
    c = PmvClient(tag='datcoc:' + request.user.username)
    ctx = {'action': action, 'pk': pk, 'title': {'view': 'Chi tiết phiếu cọc', 'add': 'Thêm phiếu cọc',
            'edit': 'Sửa phiếu cọc', 'delete': 'Xóa phiếu cọc'}[action]}
    try:
        h = header(c, pk) if pk else None
        if pk and not h:
            raise Http404
        ctx['h'] = decorate(h) if h else None
        old_lines = lines(c, pk) if pk else []
        ctx['lines'] = old_lines
        ctx['unit'] = c.sys_param('UnitWeight') or 'đơn vị PMV'
        ctx['token'] = request.POST.get('token') if request.method == 'POST' else signing.dumps(
            {**version(c, h), 'nonce': uuid.uuid4().hex}, salt='datcoc')
        if action in ('edit', 'delete') and h['Status'] != 'W':
            raise ValueError('Chỉ phiếu Lưu tạm được sửa hoặc xóa. Phiếu này đã chuyển trạng thái.')
        form = DepositForm(request.POST if request.method == 'POST' else None, initial=h)
        line_set = LineSet(request.POST if request.method == 'POST' else None, initial=old_lines, prefix='items')
        if action in ('add', 'edit'):
            form.fields['EmpID'].choices = [('', 'Chọn nhân viên')] + [(r['EmpID'], r['EmpName']) for r in c.query(
                'SELECT EmpID,EmpName FROM T_EMPLOYEE WITH (NOLOCK) ORDER BY EmpName')]
            gold = [('', 'Chọn loại vàng')] + [(r['GoldCode'], r['GoldCode']) for r in c.query(
                'SELECT GoldCode FROM I_GOLD WITH (NOLOCK) ORDER BY GoldCode')]
            for f in line_set:
                f.fields['GoldCode'].choices = gold
            empty = line_set.empty_form
            empty.fields['GoldCode'].choices = gold
            ctx.update(form=form, line_set=line_set, empty_line=empty)
        if request.method == 'POST':
            if action in ('add', 'edit') and not (form.is_valid() & line_set.is_valid()):
                ctx['error'] = 'Vui lòng kiểm tra các ô được đánh dấu bên dưới.'
            else:
                with SAVE_LOCK:
                    current = header(c, pk) if pk else None
                    try:
                        submitted = signing.loads(request.POST.get('token', ''), salt='datcoc', max_age=7200)
                    except signing.BadSignature:
                        raise ValueError('Phiên thao tác đã hết hạn. Hãy đóng và mở lại popup.')
                    submitted.pop('nonce', None)
                    if submitted != version(c, current):
                        raise ValueError('Dữ liệu hoặc đích kết nối đã thay đổi. Hãy đóng và mở lại phiếu.')
                    if current and current['Status'] != 'W':
                        raise ValueError('Phiếu đã chuyển trạng thái; không thể sửa hoặc xóa.')
                    pu = pmv_user_for_web_user(request.user)
                    if not pu or not pu.shop_id:
                        raise ValueError('Tài khoản chưa được liên kết với nhân viên / cửa hàng PMV.')
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
                        save(c, form.cleaned_data, line_set.cleaned_data, pu, current)
                response = HttpResponse('')
                import json
                response['HX-Trigger'] = json.dumps({'depositSaved': {'message': 'Đã xóa phiếu cọc.' if action == 'delete' else 'Đã lưu phiếu cọc.'}})
                return response
    except Http404:
        raise
    except ValueError as exc:
        ctx['error'] = str(exc)
    except Exception:
        log.exception('Thao tác đặt cọc không thành công')
        ctx['error'] = 'Không thể hoàn tất thao tác. Kiểm tra kết nối, quyền ghi và tải lại danh sách trước khi thử lại.'
    return render(request, 'pos/_dat_coc_popup.html', ctx)
