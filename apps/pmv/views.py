from django.shortcuts import render

from .models import PmvAudit, PmvState


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
