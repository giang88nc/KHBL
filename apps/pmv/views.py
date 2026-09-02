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
def snapshot_delete(request, pk):
    PmvSnapshot.objects.filter(pk=pk).delete()
    messages.success(request, f"Đã xóa snapshot #{pk}.")
    return redirect("pmv:diff")
