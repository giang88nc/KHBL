from apps.pmv.models import PmvState, PmvUser


def khbl(request):
    """Thông tin dùng chung mọi trang: tài khoản PMV gắn với user web + cờ khóa ghi."""
    pu = None
    if getattr(request, "user", None) and request.user.is_authenticated:
        pu = PmvUser.objects.filter(django_user=request.user).first()
    return {
        "pmv_user": pu,
        "write_lock": PmvState.get("pmv_write_lock") == "1",
    }
