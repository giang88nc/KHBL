#!/usr/bin/env python
"""Điểm vào lệnh quản trị Django của KHBL (mặc định PROD — dev tạm: config.settings.dev)."""
import os
import sys


def main():
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.prod")
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Không import được Django — kiểm tra venv "
            "(D:\\PYTHON\\KHBL\\venv\\Scripts\\python.exe)."
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
