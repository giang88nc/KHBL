"""Vận hành LAN (mặc định của manage.py + wsgi.py) — DEBUG luôn False."""
from .base import *  # noqa: F401,F403

DEBUG = False

SESSION_COOKIE_HTTPONLY = True
CSRF_COOKIE_HTTPONLY = True
X_FRAME_OPTIONS = "DENY"
