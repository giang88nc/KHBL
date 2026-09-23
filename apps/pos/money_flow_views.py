import datetime as dt

from django import forms
from decimal import Decimal

from django.db.models import Count, Sum, Value, DecimalField, Max
from django.db.models import Q
from django.db.models.functions import Coalesce
from django.http import Http404
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods

from . import money_flow as MF
from . import money_flow_payment as MFP
from . import vietqr as QR
from .models import MoneyFlow, MoneyFlowPayment


class FilterForm(forms.Form):
    d1 = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    d2 = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    direction = forms.ChoiceField(required=False, choices=[("", "IN + OUT"), *MoneyFlow.DIRECTIONS])
    source_system = forms.ChoiceField(required=False, choices=[("", "KHBL + KHCD"), ("KHBL", "KHBL"), ("KHCD", "KHCD")])
    service = forms.ChoiceField(required=False, choices=[("", "Tất cả dịch vụ"), *MF.SERVICES.items()])
    payment_status = forms.ChoiceField(required=False, choices=[("", "Tất cả trạng thái"), *MoneyFlow.PAYMENT_STATUSES])
    key = forms.CharField(required=False, max_length=100)

    def clean(self):
        data = super().clean()
        if data.get("d1") and data.get("d2"):
            if data["d1"] > data["d2"]:
                raise forms.ValidationError("Ngày kết thúc phải từ ngày bắt đầu trở đi.")
            if (data["d2"] - data["d1"]).days > 31:
                raise forms.ValidationError("GĐ1 giới hạn mỗi lần xem trong 31 ngày.")
        return data


@require_GET
def listing(request):
    from .quyen import chan
    chan(request, "CHUYEN_KHOAN")
    today = timezone.localdate()
    params = request.GET.copy()
    params.setdefault("d1", (today - dt.timedelta(days=6)).isoformat())
    params.setdefault("d2", today.isoformat())
    form = FilterForm(params)
    ctx = {"form": form, "nav_active": "chuyenkhoan", "services": MF.SERVICES, "phase": 2}
    if form.is_valid():
        data = form.cleaned_data
        qs = MoneyFlow.objects.filter(business_date__range=(data["d1"], data["d2"]))
        for field in ("direction", "source_system", "service", "payment_status"):
            if data.get(field):
                qs = qs.filter(**{field: data[field]})
        if data.get("key"):
            key = data["key"]
            qs = qs.filter(source_bill_code__icontains=key) | qs.filter(customer_name__icontains=key) | qs.filter(source_id__icontains=key)
        zero = Value(Decimal(0), output_field=DecimalField(max_digits=18, decimal_places=3))
        active = qs.filter(is_void=False)
        stats = active.aggregate(n=Count("id"), total=Coalesce(Sum("expected_amount"), zero),
                             cash=Coalesce(Sum("cash_amount"), zero), bank=Coalesce(Sum("bank_amount"), zero))
        by_direction = {r["direction"]: r for r in active.values("direction").annotate(
            n=Count("id"), total=Sum("expected_amount"), cash=Sum("cash_amount"), bank=Sum("bank_amount"))}
        rows = list(qs[:500])
        instructions = {(x.flow_id, x.method): x for x in MoneyFlowPayment.objects.filter(
            flow_id__in=[row.pk for row in rows], status=MoneyFlowPayment.READY)}
        for row in rows:
            row.service_label = MF.SERVICES.get(row.service, row.service)
            row.operation_label = (row.source_snapshot or {}).get("operation", "")
            row.cash_instruction = instructions.get((row.pk, MoneyFlowPayment.CASH))
            row.bank_instruction = instructions.get((row.pk, MoneyFlowPayment.BANK))
        sync_marks = MoneyFlow.objects.values("source_system").annotate(last=Max("synced_at"))
        ctx.update(rows=rows, stats=stats, by_direction=by_direction, sync_marks=sync_marks,
                   truncated=qs.count() > 500, d1=data["d1"], d2=data["d2"])
    return render(request, "pos/money_flow.html", ctx)


@require_http_methods(["GET", "POST"])
def payment(request, pk, method):
    from .quyen import chan
    chan(request, "CHUYEN_KHOAN", "can_edit" if request.method == "POST" else "can_view")
    flow = get_object_or_404(MoneyFlow, pk=pk)
    from .mobile_customers import hydrate_customers
    hydrate_customers([flow])
    method = method.upper()
    if method not in {MoneyFlowPayment.CASH, MoneyFlowPayment.BANK}:
        raise Http404("Phương thức thanh toán không hợp lệ.")
    amount = MFP.channel_amount(flow, method)
    banks = QR.active_banks() if method == MoneyFlowPayment.BANK and flow.direction == MoneyFlow.IN else []
    in_bank = method == MoneyFlowPayment.BANK and flow.direction == MoneyFlow.IN
    preferred = 'pawn' if flow.source_system == 'KHCD' or flow.service in ('PAWN', 'REDEEM', 'DEPOSIT') else 'gold'
    import re
    for bank in banks:
        types = set(re.split(r'[^a-z]+', str(bank.get('type') or '').lower()))
        bank['preferred'] = bool(types & ({'pawn'} if preferred == 'pawn' else {'gold', 'cty'}))
    banks.sort(key=lambda b: not b['preferred'])
    selected_bank = next((b for b in banks if str(b['id']) == request.POST.get('bank_id')), banks[0] if banks else None)
    existing = MoneyFlowPayment.objects.filter(flow=flow, method=method,status__in=['ready','success']).order_by('-id').first()
    outgoing = MFP.outgoing_bank(flow)
    initial = {
        "bank_code": (existing.bank_code if existing else "") or outgoing["bank_code"],
        "bank_account": (existing.bank_account if existing else "") or outgoing["bank_account"],
        "bank_owner": (existing.bank_owner if existing else "") or outgoing["bank_owner"],
    }
    error = ""
    instruction = existing if (existing and existing.status == MoneyFlowPayment.READY
                               and existing.amount == amount and not flow.is_void) else None
    if in_bank:
        instruction = None
        if request.method == 'GET' and request.GET.get('qr_id'):
            qr_id = request.GET['qr_id']
            if not qr_id.isdecimal():
                raise Http404('QR không hợp lệ.')
            instruction = get_object_or_404(MoneyFlowPayment, pk=qr_id, flow=flow, method=method)
        if instruction and instruction.transfer_content!=MFP.transfer_content(flow):instruction=None
        amount = flow.expected_amount
        if request.method=='GET':
            from .money_in import check_source
            try:check_source(flow,creating=not instruction or instruction.status=='ready')
            except Exception as exc:
                instruction=None
                error=str(exc) if isinstance(exc,ValueError) else 'Chưa kiểm tra được nguồn. Vui lòng thử lại trước khi tạo QR.'
    if request.method == "POST" and in_bank and request.POST.get('reopen_qr'):
        qr_id = request.POST['reopen_qr']
        if not qr_id.isdecimal():
            raise Http404('QR không hợp lệ.')
        try:
            instruction = MFP.reopen_instruction(flow, qr_id, request.user)
        except ValueError as exc:
            error = str(exc)
            instruction = None
    elif request.method == "POST":
        try:
            company_bank = None
            if method == MoneyFlowPayment.BANK and flow.direction == MoneyFlow.IN:
                company_bank = next((b for b in banks if str(b['id']) == request.POST.get('bank_id')), None)
            instruction = MFP.save_instruction(
                flow, method, request.user, company_bank=company_bank,
                bank_code=(request.POST.get("bank_code") or "").strip().upper(),
                bank_account=(request.POST.get("bank_account") or "").strip(),
                bank_owner=(request.POST.get("bank_owner") or "").strip(),
                custom_bank=request.POST.get('bank_amount', '') if in_bank else None,
                custom_cash=request.POST.get('cash_amount') if in_bank else None,
                note=request.POST.get('note') if in_bank else None)
        except ValueError as exc:
            error = str(exc)
    ctx = {"flow": flow, "method": method, "amount": amount, "banks": banks,
           "existing": existing, "initial": initial, "instruction": instruction, "error": error,
           "service_label": MF.SERVICES.get(flow.service, flow.service), "banks_vn": QR.BANKS}
    if instruction and instruction.qr_payload:
        ctx["qr_img"] = MFP.qr_image(instruction.qr_payload)
    if in_bank:
        received = sum((r.amount for r in flow.bank_receipts.filter(status='applied')), Decimal(0))
        remaining = max(Decimal(0), flow.expected_amount-received)
        history = MoneyFlowPayment.objects.filter(flow=flow, method='BANK').filter(
            Q(status='success') | Q(status__in=['ready', 'superseded'], amount__gt=0, amount__lte=remaining)
        ).order_by('-id')
        ctx.update(qr_history=history,
                   split_total=remaining, received_before=received,
                   bank_group=preferred, keep_post=request.method == 'POST', selected_bank=selected_bank, bank_value=request.POST.get('bank_amount', str(int(flow.expected_amount-received))),
                   cash_value=request.POST.get('cash_amount', '0'), note_value=request.POST.get('note', MFP.transfer_content(flow)))
        return render(request, 'pos/_money_flow_in_transfer.html', ctx)
    return render(request, "pos/_money_flow_payment.html", ctx)


@require_http_methods(['POST'])
def reconcile_pawn(request, pk):
    from django.http import JsonResponse
    from .quyen import chan
    from .money_in import reconcile, Busy, AmbiguousReceipt
    chan(request, 'CHUYEN_KHOAN', 'can_edit')
    instruction = get_object_or_404(MoneyFlowPayment, pk=pk, flow__direction='IN')
    try:
        result = reconcile(instruction.flow_id, instruction.pk)
    except Busy:
        result = {'status':'waiting','received':'0','message':'Đang đối soát.'}
    except AmbiguousReceipt:
        result = {'status':'waiting','received':'0','code':'ambiguous_receipt'}
    except ValueError as exc:
        result = {'status':'review','message':str(exc)}
    response = JsonResponse(result)
    response['Cache-Control'] = 'no-store'
    return response


@require_http_methods(['POST'])
def mobile_check(request, pk):
    from django.http import JsonResponse
    from .quyen import chan
    from .money_in import reconcile, Busy, AmbiguousReceipt
    chan(request, 'CHUYEN_KHOAN', 'can_edit')
    flow=get_object_or_404(MoneyFlow,pk=pk,direction='IN',is_void=False)
    reference=MFP.transfer_content(flow)
    recent=MoneyFlowPayment.objects.filter(flow=flow,method='BANK',
        transfer_content=reference,created_at__gte=timezone.now()-dt.timedelta(hours=1),
        status__in=['ready','success']).order_by('-id').first()
    result={'status':'idle'}
    if recent:
        try:result=reconcile(flow.pk,recent.pk)
        except Busy:result={'status':'waiting'}
        except AmbiguousReceipt:result={'status':'waiting','code':'ambiguous_receipt'}
        except ValueError as exc:result={'status':'review','message':str(exc)}
    flow.refresh_from_db()
    received=sum((r.amount for r in flow.bank_receipts.filter(status='applied')),Decimal(0))
    remaining=max(Decimal(0),flow.expected_amount-received)
    result.update(received=str(received),remaining=str(remaining),
        history=list(MoneyFlowPayment.objects.filter(flow=flow,method='BANK').values('id','status','amount')))
    response=JsonResponse(result);response['Cache-Control']='no-store';return response


@require_GET
def mobile_qr_image(request, pk):
    from django.http import HttpResponse
    import base64
    from .quyen import chan
    chan(request,'CHUYEN_KHOAN')
    qr=get_object_or_404(MoneyFlowPayment,pk=pk,method='BANK',status='ready',flow__direction='IN',flow__is_void=False)
    if not qr.qr_payload:raise Http404('QR không có dữ liệu.')
    response=HttpResponse(base64.b64decode(MFP.qr_image(qr.qr_payload).split(',',1)[1]),content_type='image/png')
    response['Content-Disposition']=f'attachment; filename="KH2-QR-{qr.pk}.png"'
    response['Cache-Control']='no-store, private'
    response['X-Content-Type-Options']='nosniff'
    return response


@require_GET
def mobile_qr_success(request, pk):
    from .quyen import chan
    chan(request,'CHUYEN_KHOAN')
    qr=get_object_or_404(MoneyFlowPayment.objects.select_related('flow'),pk=pk,method='BANK',status='success',flow__direction='IN',flow__is_void=False)
    return render(request,'pos/mobile_qr_success.html',{'flow':qr.flow,'instruction':qr,'method':'BANK'})


def normalize_mobile_bill_key(value):
    import re
    value = value.strip()
    if not re.fullmatch(r'(?:\d{9}|\d{12}|\d{2}-\d{2}-\d{2}-\d{6})', value):
        return value
    match = re.fullmatch(r'(\d{2})-?(\d{2})-?(\d{2})-?(\d{1,6})', value)
    if match:
        try:
            dt.date(2000+int(match[1]), int(match[2]), int(match[3]))
        except ValueError:
            return value
        return '-'.join((*match.groups()[:3], match[4].zfill(6)))
    return value


@require_GET
def mobile_in(request):
    """Danh sách IN gọn cho điện thoại; tạo QR dùng chung endpoint với trang kế toán."""
    from .quyen import chan
    chan(request, "CHUYEN_KHOAN")
    key = normalize_mobile_bill_key(request.GET.get("key") or "")
    mine = request.GET.get('mine', '1') != '0'
    from apps.pmv.models import pmv_user_for_web_user
    employee = pmv_user_for_web_user(request.user) if mine else None
    employee_id = str(employee.emp_id or '').strip() if employee else ''
    mobile_employee = request.session.get('mobile_employee') or {}
    if mobile_employee:
        employee_id = str(mobile_employee.get('employee_pmv') or '').strip()
    service = (request.GET.get("service") or "").strip()
    mobile_groups = {'RETAIL': ('Bán hàng', ('RETAIL', 'GOLD_BUY')),
                     'DEPOSIT': ('Đặt cọc', ('DEPOSIT',)),
                     'PAWN': ('Cầm đồ', ('PAWN', 'REDEEM'))}
    service = {'GOLD_BUY': 'RETAIL', 'REDEEM': 'PAWN'}.get(service, service)
    now = timezone.localtime()
    window_start = now - dt.timedelta(hours=24 if key else 1)
    # Giữ cả dòng đã xóa trong cửa sổ hiển thị: nhân viên cần thấy rõ hóa đơn vừa
    # biến mất khỏi KK, thay vì thẻ âm thầm biến mất và dễ thao tác nhầm.
    # 22/09/2026: HĐ ĐỔI NGANG (tổng 0, không xóa) vẫn hiện — nhãn "ĐỔI NGANG · không thu tiền", thay vì biến mất/nhãn ĐÃ XÓA
    qs = MoneyFlow.objects.filter(Q(expected_amount__gt=0) | Q(expected_amount=0, is_void=False, source_snapshot__doi_ngang=True),
        direction=MoneyFlow.IN, business_date__range=(window_start.date(), now.date())).order_by("-business_date", "-id")
    if key:
        import re
        cd_key=key
        match=re.fullmatch(r'(\d{4})-(\d{1,5})-(\d{1,5})',key)
        if match:cd_key=match[1]+match[2].zfill(5)+match[3].zfill(5)
        qs = qs.filter(Q(source_bill_code=key)|Q(source_system='KHCD',source_bill_code=cd_key))
        key=cd_key if match else key
    if service in mobile_groups:
        qs = qs.filter(service__in=mobile_groups[service][1])
    rows = list(qs)
    timestamps = {}
    for row in rows:
        meta = (row.source_snapshot or {}).get('mobile', {})
        try:
            stamp = dt.datetime.fromisoformat(meta.get('happened_at', ''))
            if timezone.is_aware(stamp):
                stamp = timezone.localtime(stamp).replace(tzinfo=None)
            timestamps[(row.source_system, row.source_id)] = stamp
        except (TypeError, ValueError):
            continue
    start_local, end_local = window_start.replace(tzinfo=None), now.replace(tzinfo=None)
    rows = [r for r in rows if (stamp := timestamps.get((r.source_system, r.source_id))) is not None
            and start_local <= stamp <= end_local]
    rows.sort(key=lambda r: (timestamps[(r.source_system, r.source_id)], r.pk), reverse=True)
    if mine:
        rows = [r for r in rows if employee_id and
                str((r.source_snapshot or {}).get('mobile', {}).get('employee_pmv') or '').strip() == employee_id]
    ready = {x.flow_id: x for x in MoneyFlowPayment.objects.filter(
        flow_id__in=[r.pk for r in rows], method=MoneyFlowPayment.BANK,
        status=MoneyFlowPayment.READY)}
    from .mobile_receipts import amounts_by_flow
    receipts = amounts_by_flow(rows)
    # 21/09/2026: KHÔNG hỏi KK khi tải trang — tiền thối lại đã được job nền ghi vào money_flow (mobile_projection).
    from .mobile_projection import tender_of
    for row in rows:
        row.customer_change, row.customer_received_exact = tender_of(row)
        row.service_label = MF.SERVICES.get(row.service, row.service)
        row.bank_instruction = ready.get(row.pk)
        snap = row.source_snapshot or {}
        meta = snap.get('mobile', {})
        row.phone_label = meta.get('phone', '')
        row.transfer_amounts = receipts[row.pk]
        row.transfer_total = sum(row.transfer_amounts, Decimal(0))
        row.qr_paid = row.expected_amount > 0 and row.transfer_total == row.expected_amount
        row.time_label = timestamps[(row.source_system, row.source_id)].strftime('%H:%M')
        row.staff_label = meta.get('employee_name') or meta.get('employee_pmv') or 'Chưa có NV'
        row.operation_label = snap.get('operation') or row.service_label
        row.is_deleted = bool(row.is_void)
        row.doi_ngang = not row.is_void and row.expected_amount == 0 and bool(snap.get('doi_ngang'))   # 22/09/2026
        row.mobile_group = 'PAWN' if row.source_system == 'KHCD' else {'GOLD_BUY': 'RETAIL', 'REDEEM': 'PAWN'}.get(row.service, row.service)
        row.mobile_label = mobile_groups.get(row.mobile_group, (row.service_label, ()))[0]
    ctx = {"rows": rows, "key": key, "service": service, "mine": mine, "window_hours": 24 if key else 1,
           "services": {k: v[0] for k, v in mobile_groups.items()},
           "nav_active": "chuyenkhoan", "mobile_section": "qr"}
    template = "pos/_money_flow_mobile_in.html" if request.headers.get("HX-Request") else "pos/money_flow_mobile_in.html"
    return render(request, template, ctx)


@require_GET
def mobile_home(request):
    """App shell mobile; các danh mục con thay nội dung bằng HTMX, không reload."""
    from .quyen import chan
    chan(request, "CHUYEN_KHOAN")
    today = timezone.localdate()
    incoming = MoneyFlow.objects.filter(direction=MoneyFlow.IN, is_void=False)
    ctx = {
        "nav_active": "chuyenkhoan", "mobile_section": "home",
        "today_count": incoming.filter(business_date=today).count(),
        "waiting_count": incoming.filter(payment_status__in=[MoneyFlow.WAITING, MoneyFlow.REVIEW]).count(),
        "ready_count": MoneyFlowPayment.objects.filter(
            flow__direction=MoneyFlow.IN, flow__is_void=False,
            method=MoneyFlowPayment.BANK, status=MoneyFlowPayment.READY).count(),
    }
    return render(request, "pos/money_flow_mobile.html", ctx)
