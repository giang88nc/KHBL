"""
Kiểm tra sức khỏe PMV mỗi 30 phút (job scheduler):
  - kết nối được không, giờ server lệch bao nhiêu
  - VÂN TAY VERSION DB vendor (tbh_VersionDB): vendor nâng cấp schema → CẢNH BÁO
    + đặt pmv_write_lock=1 (nền tảng circuit-breaker cho giai đoạn GHI sau này)
"""
import datetime

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.pmv.gateway import canh_bao, pmv_read
from apps.pmv.models import PmvState


class Command(BaseCommand):
    help = "Kiểm tra kết nối PMV + vân tay version DB vendor (cảnh báo đổi schema)"

    def handle(self, *args, **opts):
        now = timezone.localtime()

        rows = pmv_read(
            "SELECT CONVERT(VARCHAR(19), GETDATE(), 120) AS server_time", tag="check_pmv"
        )
        server_time = datetime.datetime.strptime(rows[0]["server_time"], "%Y-%m-%d %H:%M:%S")
        lech = (now.replace(tzinfo=None) - server_time).total_seconds()
        PmvState.set("pmv_last_check", f"{now:%d/%m/%Y %H:%M} | OK | đồng hồ KK lệch {lech:+.0f}s")
        if abs(lech) > 120:
            canh_bao("check_pmv", f"Đồng hồ PC KK lệch {lech:+.0f}s so máy web — nên chỉnh giờ")

        # vân tay version: đếm dòng + checksum toàn bảng lịch sử nâng cấp của vendor
        fp_rows = pmv_read(
            "SELECT COUNT(*) AS n, ISNULL(CHECKSUM_AGG(BINARY_CHECKSUM(*)), 0) AS cs "
            "FROM tbh_VersionDB WITH (NOLOCK)",
            tag="check_pmv",
        )
        fp = f"{fp_rows[0]['n']}|{fp_rows[0]['cs']}"
        old = PmvState.get("pmv_version_fp")
        if old and old != fp:
            PmvState.set("pmv_write_lock", "1")
            canh_bao(
                "check_pmv",
                f"VENDOR VỪA NÂNG CẤP DB PMV (tbh_VersionDB đổi {old} → {fp}) — "
                "KHÓA GHI cho tới khi kiểm tra lại bản đồ proc (skill pmv-proc-map)",
            )
            self.stderr.write("CẢNH BÁO: vendor đổi version DB — đã đặt pmv_write_lock=1")
        PmvState.set("pmv_version_fp", fp)
        self.stdout.write(f"check_pmv OK — lệch giờ {lech:+.0f}s, version fp {fp}")
