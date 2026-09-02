import time

from django.contrib import messages
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from . import diff as diffmod
from .models import PmvAudit, PmvSnapshot, PmvState


def status(request):
    """Trang trạng thái GĐ0: backup gần nhất, sức khỏe PMV, nhật ký + cảnh báo vượt quyền."""
    state = {s.key: s.value for s in PmvState.objects.all()}
    audits = PmvAudit.objects.all()[:30]
    so_blocked = PmvAudit.objects.filter(kind=PmvAudit.Kind.BLOCKED).count()
    so_canhbao = PmvAudit.objects.filter(kind=PmvAudit.Kind.CANHBAO).count()
    return render(request, "pmv/status.html", {
        "state": state,
        "audits": audits,
        "so_blocked": so_blocked,
        "so_canhbao": so_canhbao,
        "write_lock": state.get("pmv_write_lock") == "1",
    })


def _resolve_snapshot(token):
    """token: 'pmv' | 'sandbox' | 'snap:<id>' → (data, mô tả). Live thì chụp fast."""
    if token and token.startswith("snap:"):
        snap = PmvSnapshot.objects.filter(pk=int(token[5:])).first()
        if not snap:
            return None, "(snapshot đã xóa)"
        return snap.payload, f"#{snap.pk} {snap.label} · {snap.created_at:%d/%m %H:%M} · {snap.mode}"
    if token in ("pmv", "sandbox"):
        data = diffmod.snapshot(token, mode="fast")
        nhan = "PMV thật (live)" if token == "pmv" else "Sandbox (live)"
        return data, f"{nhan} · {timezone.localtime():%d/%m %H:%M:%S} · fast"
    return None, "(chưa chọn)"


def diff_view(request):
    """So sánh 2 nguồn SQL, tô màu bảng khác nhau."""
    snapshots = PmvSnapshot.objects.all()[:50]
    a_tok = request.GET.get("a", "pmv")
    b_tok = request.GET.get("b", "sandbox")
    only_diff = request.GET.get("only") == "1"

    rows = summary = None
    desc_a = desc_b = ""
    if "so" in request.GET:
        data_a, desc_a = _resolve_snapshot(a_tok)
        data_b, desc_b = _resolve_snapshot(b_tok)
        if data_a is None or data_b is None:
            messages.error(request, "Nguồn không hợp lệ hoặc snapshot đã xóa.")
        else:
            rows = diffmod.diff(data_a, data_b)
            summary = {
                "diff": sum(1 for r in rows if r["status"] == "diff"),
                "only_a": sum(1 for r in rows if r["status"] == "only_a"),
                "only_b": sum(1 for r in rows if r["status"] == "only_b"),
                "same": sum(1 for r in rows if r["status"] == "same"),
                "total": len(rows),
            }
            if only_diff:
                rows = [r for r in rows if r["status"] != "same"]

    return render(request, "pmv/diff.html", {
        "snapshots": snapshots,
        "a_tok": a_tok, "b_tok": b_tok, "only_diff": only_diff,
        "desc_a": desc_a, "desc_b": desc_b,
        "rows": rows, "summary": summary, "ran": "so" in request.GET,
    })


@require_POST
def snapshot_create(request):
    """Chụp snapshot 1 nguồn và lưu (sandbox mặc định FULL để so trước/sau proc)."""
    source = request.POST.get("source", "sandbox")
    mode = request.POST.get("mode", "full" if source == "sandbox" else "fast")
    label = (request.POST.get("label") or "").strip() or f"{source} {timezone.localtime():%d/%m %H:%M}"
    if source not in ("pmv", "sandbox"):
        messages.error(request, "Nguồn không hợp lệ.")
        return redirect("pmv:diff")
    try:
        t0 = time.monotonic()
        data = diffmod.snapshot(source, mode=mode)
        snap = PmvSnapshot.objects.create(
            label=label, source=source, mode=("fast" if source == "pmv" else mode),
            table_count=len(data), payload=data,
        )
        messages.success(
            request,
            f"Đã chụp snapshot #{snap.pk} “{label}” — {len(data)} bảng, "
            f"{snap.mode}, {time.monotonic() - t0:.1f}s.",
        )
    except Exception as exc:
        messages.error(request, f"Chụp snapshot lỗi: {exc}")
    return redirect(f"{reverse('pmv:diff')}?a=snap:{PmvSnapshot.objects.first().pk if PmvSnapshot.objects.exists() else ''}&b=sandbox")


@require_POST
def sync_now(request):
    """Nút ⟳ SYNC: KK → Mr Giang (backup PMV thật, hút về, restore đè sandbox). ~1 phút."""
    from io import StringIO

    from django.core.management import call_command

    out = StringIO()
    try:
        call_command("sync_sandbox", stdout=out, stderr=out)
        last = PmvState.get("pmv_last_sync")
        messages.success(request, f"⟳ SYNC xong — {last}. Bảng dưới so PMV thật ↔ Sandbox ngay sau sync.")
    except Exception as exc:
        messages.error(request, f"SYNC LỖI: {exc} — xem nhật ký trang Trạng thái.")
        return redirect("pmv:status")
    return redirect(f"{reverse('pmv:diff')}?a=pmv&b=sandbox&only=1&so=1")


CATEGORY_COLORS = {
    "HĐ bán": "#B3402A", "HĐ thâu": "#C07A1A", "HĐ đổi": "#7C3AED", "Đặt cọc": "#9A3412",
    "Khách hàng": "#2E7D46", "Bảng giá": "#C9A02C", "Sản phẩm/kho": "#0F766E", "Sổ quỹ": "#1D4ED8",
    "Nhật ký ngày": "#6B7280", "Đồng bộ": "#0891B2", "HĐ điện tử": "#BE123C", "Tin nhắn": "#A16207",
    "Hệ thống": "#78716C", "Nhân viên": "#4338CA", "Báo cáo": "#57534E", "Khác": "#A8A29E",
}


def behavior_view(request):
    """Trang HÀNH VI PMVGoldRT: bộ lọc, thẻ tổng quan, thanh nhóm, dòng thời gian."""
    import datetime

    from django.core.paginator import Paginator
    from django.db.models import Count, Sum

    from .management.commands.pmv_trace import trace_status
    from .models import PmvBehavior

    today = timezone.localdate()
    d1 = request.GET.get("d1") or today.isoformat()
    d2 = request.GET.get("d2") or today.isoformat()
    try:
        start = timezone.make_aware(datetime.datetime.fromisoformat(d1))
        end = timezone.make_aware(datetime.datetime.fromisoformat(d2)) + datetime.timedelta(days=1)
    except ValueError:
        start = timezone.make_aware(datetime.datetime.combine(today, datetime.time.min))
        end = start + datetime.timedelta(days=1)
        d1 = d2 = today.isoformat()
    cat = request.GET.get("cat", "")
    src = request.GET.get("src", "")
    act = request.GET.get("act", "")
    q = (request.GET.get("q") or "").strip()
    auto = request.GET.get("auto") == "1"

    qs = PmvBehavior.objects.filter(event_time__gte=start, event_time__lt=end)
    if cat:
        qs = qs.filter(category=cat)
    if src:
        qs = qs.filter(source=src)
    if act:
        qs = qs.filter(action=act)
    if q:
        from django.db.models import Q
        qs = qs.filter(Q(proc_name__icontains=q) | Q(text__icontains=q) | Q(host__icontains=q))

    base = PmvBehavior.objects.filter(event_time__gte=start, event_time__lt=end)
    by_cat = list(base.values("category").annotate(n=Count("id"), calls=Sum("exec_delta")).order_by("-n"))
    max_n = max((c["n"] for c in by_cat), default=1)
    for c in by_cat:
        c["pct"] = int(c["n"] * 100 / max_n)
        c["color"] = CATEGORY_COLORS.get(c["category"], "#A8A29E")
    top_procs = list(base.exclude(proc_name="").values("proc_name").annotate(n=Count("id"), calls=Sum("exec_delta")).order_by("-n")[:12])
    cards = {
        "trace_rows": base.filter(source="TRACE").count(),
        "stats_rows": base.filter(source="STATS").count(),
        "ghi": base.filter(action="ghi").count(),
        "hd": base.filter(category__in=["HĐ bán", "HĐ thâu", "HĐ đổi"], action="ghi", source="TRACE").count(),
        "hosts": list(base.exclude(host="").values_list("host", flat=True).distinct()),
    }
    page = Paginator(qs, 100).get_page(request.GET.get("page"))
    for r in page:
        r.color = CATEGORY_COLORS.get(r.category, "#A8A29E")

    try:
        tstat = trace_status()
    except Exception as exc:
        tstat = {"error": str(exc)}
    state = {s.key: s.value for s in PmvState.objects.filter(key__in=["pmv_behavior_last", "pmv_trace_started", "pmv_trace_stopped"])}

    return render(request, "pmv/behavior.html", {
        "d1": d1, "d2": d2, "cat": cat, "src": src, "act": act, "q": q, "auto": auto,
        "categories": list(CATEGORY_COLORS.keys()), "colors": CATEGORY_COLORS,
        "by_cat": by_cat, "top_procs": top_procs, "cards": cards, "page": page,
        "tstat": tstat, "state": state, "total": qs.count(),
        "qstring": request.GET.urlencode(),
    })


@require_POST
def trace_toggle(request, action):
    from django.core.management import call_command

    if action not in ("start", "stop"):
        messages.error(request, "Hành động không hợp lệ.")
        return redirect("pmv:behavior")
    try:
        call_command("pmv_trace", action)
        messages.success(request, "Đã BẬT trace chi tiết trên SQL KK." if action == "start" else "Đã TẮT trace chi tiết.")
    except Exception as exc:
        messages.error(request, f"Trace {action} lỗi: {exc}")
    return redirect("pmv:behavior")


@require_POST
def collect_now(request):
    from io import StringIO

    from django.core.management import call_command

    out = StringIO()
    try:
        call_command("collect_pmv_behavior", stdout=out, stderr=out)
        messages.success(request, out.getvalue().strip() or "Đã thu thập.")
    except Exception as exc:
        messages.error(request, f"Thu thập lỗi: {exc}")
    return redirect(f"{reverse('pmv:behavior')}?{request.POST.get('qstring', '')}")


@require_POST
def snapshot_delete(request, pk):
    PmvSnapshot.objects.filter(pk=pk).delete()
    messages.success(request, f"Đã xóa snapshot #{pk}.")
    return redirect("pmv:diff")
