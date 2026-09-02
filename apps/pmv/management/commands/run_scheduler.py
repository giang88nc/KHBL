"""Tiến trình scheduler RIÊNG (TURN_ON_KHBL.bat tự bật) — job khai ở config/scheduler.py."""
import sys

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Chạy APScheduler (BlockingScheduler) cho job nền KHBL"

    def handle(self, *args, **opts):
        # Bài học KHJ: chữ Việt ra stdout bị redirect → cp1252 crash âm thầm
        for stream in (sys.stdout, sys.stderr):
            try:
                stream.reconfigure(errors="replace")
            except Exception:
                pass
        from config.scheduler import start

        start()
