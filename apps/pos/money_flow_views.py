import datetime as dt

from django import forms
from decimal import Decimal

from django.db.models import Count, Sum, Value, DecimalField
from django.db.models.functions import Coalesce
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_GET

from . import money_flow as MF
from .models import MoneyFlow


class FilterForm(forms.Form):
    d1 = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    d2 = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    direction = forms.ChoiceField(required=False, choices=[("", "IN + OUT"), *MoneyFlow.DIRECTIONS])
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
    ctx = {"form": form, "nav_active": "chuyenkhoan", "services": MF.SERVICES, "phase": 1}
    if form.is_valid():
        data = form.cleaned_data
        # Chỉ refresh bản chiếu trong khoảng ngày đang xem; không ghi PMV/KHCD.
        ctx["sync_result"] = MF.sync(data["d1"], data["d2"])
        qs = MoneyFlow.objects.filter(business_date__range=(data["d1"], data["d2"]))
        for field in ("direction", "service", "payment_status"):
            if data.get(field):
                qs = qs.filter(**{field: data[field]})
        if data.get("key"):
            key = data["key"]
            qs = qs.filter(source_bill_code__icontains=key) | qs.filter(customer_name__icontains=key) | qs.filter(source_id__icontains=key)
        zero = Value(Decimal(0), output_field=DecimalField(max_digits=18, decimal_places=3))
        stats = qs.aggregate(n=Count("id"), total=Coalesce(Sum("expected_amount"), zero),
                             cash=Coalesce(Sum("cash_amount"), zero), bank=Coalesce(Sum("bank_amount"), zero))
        by_direction = {r["direction"]: r for r in qs.values("direction").annotate(
            n=Count("id"), total=Sum("expected_amount"), cash=Sum("cash_amount"), bank=Sum("bank_amount"))}
        rows = list(qs[:500])
        for row in rows:
            row.service_label = MF.SERVICES.get(row.service, row.service)
        ctx.update(rows=rows, stats=stats, by_direction=by_direction,
                   truncated=qs.count() > 500, d1=data["d1"], d2=data["d2"])
    return render(request, "pos/money_flow.html", ctx)
