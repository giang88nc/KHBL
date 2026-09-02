"""
Vận hành LAN (mặc định của manage.py + wsgi.py) — DEBUG luôn False.

SỬA-LÀ-THẤY (KHBL_LIVE_EDIT, mặc định BẬT — kế thừa KHJ): sửa TEMPLATE / CSS / JS
rồi F5 là thấy ngay, KHÔNG cần RESET_KHBL.bat, KHÔNG cần collectstatic.
Sửa file .py thì VẪN PHẢI RESET_KHBL.bat (Python đã nạp vào tiến trình waitress).
"""
from .base import *  # noqa: F401,F403
from .base import MIDDLEWARE, TEMPLATES, env

DEBUG = False

# waitress không tự phục vụ static khi DEBUG=False → WhiteNoise đứng ngay sau SecurityMiddleware
MIDDLEWARE = MIDDLEWARE[:1] + ["whitenoise.middleware.WhiteNoiseMiddleware"] + MIDDLEWARE[1:]
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedStaticFilesStorage"},
}

SESSION_COOKIE_HTTPONLY = True
CSRF_COOKIE_HTTPONLY = True
X_FRAME_OPTIONS = "DENY"
# LAN nội bộ chạy HTTP → không bật cookie secure (bật khi nào có HTTPS nội bộ)
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False
CSRF_TRUSTED_ORIGINS = env.list("CSRF_TRUSTED_ORIGINS", default=[])

LIVE_EDIT = env.bool("KHBL_LIVE_EDIT", default=True)
if LIVE_EDIT:
    TEMPLATES[0]["APP_DIRS"] = False
    TEMPLATES[0]["OPTIONS"]["loaders"] = [
        "django.template.loaders.filesystem.Loader",
        "django.template.loaders.app_directories.Loader",
    ]
    WHITENOISE_USE_FINDERS = True
    WHITENOISE_AUTOREFRESH = True
    WHITENOISE_MAX_AGE = 0
