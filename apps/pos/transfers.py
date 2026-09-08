"""Tra cứu thông báo chuyển khoản và quản lý tài khoản MySQL."""
import datetime as dt
import hashlib
import json
import logging

from django import forms
from django.db import connection, DatabaseError
from django.http import HttpResponse, Http404
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods

logger = logging.getLogger(__name__)


class TransferFilter(forms.Form):
    d1 = forms.DateField(label="Từ ngày", widget=forms.DateInput(attrs={"type": "date"}))
    d2 = forms.DateField(label="Đến ngày", widget=forms.DateInput(attrs={"type": "date"}))
    account = forms.CharField(required=False, max_length=100, label="Tài khoản")
    direction = forms.ChoiceField(required=False, label="Nguồn", choices=[("", "Tất cả"), ("in", "Tiền vào (in)"), ("out", "Tiền ra (out)")])
    amount1 = forms.DecimalField(required=False, min_value=0, max_digits=18, decimal_places=2, label="Số tiền từ")
    amount2 = forms.DecimalField(required=False, min_value=0, max_digits=18, decimal_places=2, label="Đến số tiền")
    key = forms.CharField(required=False, max_length=255, label="Nội dung")

    def clean(self):
        data = super().clean()
        if data.get("d2") == dt.date.max:
            self.add_error("d2", "Ngày kết thúc quá xa.")
        for a, b, message in [("d1", "d2", "Ngày kết thúc phải từ ngày bắt đầu trở đi."), ("amount1", "amount2", "Số tiền đến phải lớn hơn hoặc bằng số tiền từ.")]:
            if data.get(a) is not None and data.get(b) is not None and data[a] > data[b]:
                raise forms.ValidationError(message)
        return data


class BankForm(forms.Form):
    bank_bin = forms.CharField(label="Mã NH / BIN NAPAS", max_length=22)
    bank_number = forms.RegexField(r"^[0-9]+$", label="Số tài khoản", max_length=33)
    bank_name = forms.CharField(label="Tên ngân hàng", max_length=33)
    bank_user = forms.CharField(label="Chủ tài khoản", max_length=255)
    bank_addr = forms.CharField(label="Chi nhánh / địa chỉ", max_length=255, required=False)
    type = forms.CharField(label="Loại", max_length=11, required=False)
    Active = forms.BooleanField(label="Đang sử dụng", required=False, initial=True)

    def clean_bank_bin(self):
        from .vietqr import bank_bin
        value = self.cleaned_data["bank_bin"].upper()
        if not bank_bin(value):
            raise forms.ValidationError("Nhập mã ngân hàng được hỗ trợ hoặc BIN NAPAS 6 số.")
        return value


def query(sql, params=()):
    with connection.cursor() as cur:
        cur.execute(sql, params)
        return [dict(zip([c[0] for c in cur.description], row)) for row in cur.fetchall()]


def banks():
    return query("SELECT id, bank_bin, bank_number, bank_name, bank_user, bank_addr, type, Active FROM gold_bank ORDER BY id")


def listing(data, page):
    where = ["transaction_time >= %s", "transaction_time < %s"]
    params = [data["d1"].isoformat(), (data["d2"] + dt.timedelta(days=1)).isoformat()]
    for field, column, op in [("account", "bank_number", "="), ("direction", "direction", "="), ("amount1", "trans_amount", ">="), ("amount2", "trans_amount", "<=")]:
        if data.get(field) not in (None, ""):
            where.append(f"{column} {op} %s")
            params.append(data[field])
    if data.get("key"):
        where.append("description LIKE %s")
        params.append("%" + connection.ops.prep_for_like_query(data["key"]) + "%")
    clause = " WHERE " + " AND ".join(where)
    stats = query(
        "SELECT COUNT(*) AS n, "
        "COALESCE(SUM(CASE WHEN direction = 'in' THEN trans_amount ELSE 0 END), 0) AS tien_vao, "
        "COALESCE(SUM(CASE WHEN direction = 'out' THEN trans_amount ELSE 0 END), 0) AS tien_ra "
        "FROM bank_notifications" + clause, params)[0]
    total = stats["n"]
    pages = max(1, (total + 99) // 100)
    page = min(max(1, page), pages)
    rows = query("SELECT id, transaction_time, bank_number, bank_name, direction, bill_code_raw, trans_amount, description, ref_code FROM bank_notifications" + clause + " ORDER BY transaction_time DESC, id DESC LIMIT 100 OFFSET %s", params + [(page - 1) * 100])
    signature = hashlib.sha256(json.dumps([stats, page, rows], default=str, ensure_ascii=False).encode()).hexdigest()
    return dict(rows=rows, total=total, tien_vao=stats["tien_vao"], tien_ra=stats["tien_ra"],
                page=page, pages=pages, signature=signature)


@require_GET
def transfers(request):
    params = request.GET.copy()
    for name in ("d1", "d2"):
        if name not in params:
            params[name] = timezone.localdate().isoformat()
    form = TransferFilter(params)
    context = dict(nav_active="chuyenkhoan", form=form)
    try:
        accounts = query("SELECT DISTINCT bank_number, bank_name FROM gold_bank WHERE Active = 1 AND bank_number IS NOT NULL AND bank_number <> '' ORDER BY bank_number")
        form.fields["account"].widget = forms.Select(choices=[("", "Tất cả tài khoản")] + [(r["bank_number"], f'{r["bank_name"] or "—"} - {r["bank_number"]}') for r in accounts])
        if form.is_valid():
            try:
                page = int(params.get("page", "1"))
            except ValueError:
                page = 1
            context.update(listing(form.cleaned_data, page))
            if request.headers.get("HX-Request") and params.get("signature") == context["signature"]:
                return HttpResponse(status=204)
    except DatabaseError:
        logger.exception("Không tải được chuyển khoản")
        context["error"] = "Không tải được dữ liệu ngân hàng. Vui lòng thử lại."
    template = "pos/_chuyen_khoan_bang.html" if request.headers.get("HX-Request") else "pos/chuyen_khoan.html"
    return render(request, template, context)


@require_GET
def bank_list(request):
    return render(request, "pos/_bank_list.html", {"banks": banks()})


@require_http_methods(["GET", "POST"])
def bank_edit(request, bank_id=None):
    bank = None
    if bank_id is not None:
        bank = next((b for b in banks() if b["id"] == bank_id), None)
        if bank is None:
            raise Http404
    form = BankForm(request.POST if request.method == "POST" else None, initial=bank)
    if request.method == "POST" and form.is_valid():
        fields = list(form.fields)
        values = [form.cleaned_data[f] for f in fields]
        try:
            with connection.cursor() as cur:
                if bank:
                    cur.execute("UPDATE gold_bank SET " + ", ".join(f"`{f}` = %s" for f in fields) + " WHERE id = %s", values + [bank_id])
                else:
                    cur.execute("INSERT INTO gold_bank (" + ", ".join(f"`{f}`" for f in fields) + ") VALUES (" + ", ".join(["%s"] * len(fields)) + ")", values)
            return render(request, "pos/_bank_list.html", {"banks": banks(), "notice": "Đã lưu tài khoản ngân hàng."})
        except DatabaseError:
            logger.exception("Không lưu được ngân hàng")
            form.add_error(None, "Không lưu được tài khoản. Vui lòng thử lại.")
    return render(request, "pos/_bank_form.html", {"form": form, "bank": bank})


@require_http_methods(["GET", "POST"])
def bank_delete(request, bank_id):
    bank = next((b for b in banks() if b["id"] == bank_id), None)
    if bank is None:
        raise Http404
    error = ""
    if request.method == "POST":
        try:
            with connection.cursor() as cur:
                cur.execute("DELETE FROM gold_bank WHERE id = %s", [bank_id])
            return render(request, "pos/_bank_list.html", {"banks": banks(), "notice": "Đã xóa tài khoản ngân hàng."})
        except DatabaseError:
            logger.exception("Không xóa được ngân hàng")
            error = "Không xóa được tài khoản. Vui lòng thử lại."
    return render(request, "pos/_bank_delete.html", {"bank": bank, "error": error})
