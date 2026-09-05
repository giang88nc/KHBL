from apps.pmv import gateway
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
        # Đích dữ liệu — băng cảnh báo trên đầu mọi trang, để không bao giờ nhầm
        # đang thao tác trên bản thử hay trên dữ liệu thật của tiệm.
        "pmv_dich": gateway.dich_hien_tai(),
        "pmv_dich_mo_ta": gateway.mo_ta_dich(),
        "pmv_ghi_kk": gateway.duoc_ghi_kk(),
    }
