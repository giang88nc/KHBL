from apps.pmv.models import PmvState, PmvUser

from . import services as S

# Nhãn phiên bản hiện ở chân trang — đổi khi lên giai đoạn mới
KHBL_VER = "GĐ0 · Track C"


def khbl(request):
    """Dữ liệu dùng chung mọi trang: tài khoản PMV gắn với user web, cờ khóa ghi,
    thông tin tiệm cho chân trang."""
    pu = None
    if getattr(request, "user", None) and request.user.is_authenticated:
        pu = PmvUser.objects.filter(django_user=request.user).first()
    try:
        tiem = S.thong_tin_tiem()
    except Exception:
        tiem = {}
    return {
        "pmv_user": pu,
        "tiem": tiem,
        "khbl_ver": KHBL_VER,
        "write_lock": PmvState.get("pmv_write_lock") == "1",
    }
