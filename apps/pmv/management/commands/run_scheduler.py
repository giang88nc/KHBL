"""Tiến trình scheduler RIÊNG (RESET_KHBL.bat / ops/vanhanh tự bật) — job khai ở config/scheduler.py.

KHÓA CHẠY ĐƠN (07/10/2026): tiến trình chiếm cổng 127.0.0.1:8109 suốt đời — bản thứ 2
(bật từ bất kỳ đâu, ở bất kỳ mức quyền nào) không chiếm được cổng thì THOÁT NGAY, không
bao giờ có 2 scheduler chạy job đôi (đối soát, ghi KK…). Tiến trình chết — kể cả bị giết
hay mất điện — Windows tự nhả cổng. Động cơ vận hành nhìn cổng này để biết scheduler sống.
Đổi cổng: KHBL_SCHEDULER_KHOA_CONG trong .env VÀ ops/vanhanh/cauhinh_khbl.ps1.
"""
import os
import socket
import sys

from django.core.management.base import BaseCommand

KHOA_CONG = int(os.environ.get("KHBL_SCHEDULER_KHOA_CONG", "8109"))


def giu_khoa_chay_don(cong):
    """Chiếm 127.0.0.1:<cong>. Trả socket (phải giữ tham chiếu tới hết đời tiến trình) hoặc
    None nếu đã có tiến trình khác giữ. Socket Python mặc định KHÔNG kế thừa sang tiến
    trình con (PEP 446)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
        s.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
    try:
        s.bind(("127.0.0.1", cong))
        s.listen(1)
    except OSError:
        s.close()
        return None
    return s


class Command(BaseCommand):
    help = "Chạy APScheduler (BlockingScheduler) cho job nền KHBL"

    def handle(self, *args, **opts):
        # Bài học KHJ: chữ Việt ra stdout bị redirect → cp1252 crash âm thầm
        for stream in (sys.stdout, sys.stderr):
            try:
                stream.reconfigure(errors="replace")
            except Exception:
                pass
        self._khoa = giu_khoa_chay_don(KHOA_CONG)
        if self._khoa is None:
            self.stderr.write(
                f"Đã có scheduler KHBL khác đang chạy (giữ cổng khóa 127.0.0.1:{KHOA_CONG}) "
                "— THOÁT, không chạy bản thứ 2."
            )
            return
        from config.scheduler import start

        start()
