"""
KHBL — Webapp BÁN LẺ Kim Hạnh 2, chạy song song với app desktop PMVGoldRT.
Settings nền. Mọi secret nằm trong .env (django-environ) — KHÔNG bao giờ commit.
"""
from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parents[2]

env = environ.Env(
    DEBUG=(bool, False),
    PMV_BACKUP_RETENTION_KK=(int, 14),
    PMV_BACKUP_RETENTION_LOCAL=(int, 30),
    SERVER_PORT=(int, 8100),
)
environ.Env.read_env(BASE_DIR / ".env")

SECRET_KEY = env("SECRET_KEY")
DEBUG = env("DEBUG")
ALLOWED_HOSTS = [h.strip() for h in env("ALLOWED_HOSTS", default="127.0.0.1,localhost").split(",") if h.strip()]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "apps.pmv",
    "apps.pos",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    # Mọi trang phải đăng nhập (trừ view gắn @login_not_required) — Track A2
    "django.contrib.auth.middleware.LoginRequiredMiddleware",
]

LOGIN_URL = "/dang-nhap/"
LOGIN_REDIRECT_URL = "/"
LOGOUT_REDIRECT_URL = "/dang-nhap/"

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "apps.pos.context_processors.khbl",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

# MySQL 8 (instance chung 3308 với KHJ HR) — DB RIÊNG khj_bl (GĐ chốt 02/09/2026)
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.mysql",
        "NAME": env("DB_NAME"),
        "USER": env("DB_USER"),
        "PASSWORD": env("DB_PASSWORD"),
        "HOST": env("DB_HOST", default="127.0.0.1"),
        "PORT": env("DB_PORT", default="3308"),
        "OPTIONS": {"charset": "utf8mb4"},
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 6}},
]

LANGUAGE_CODE = "vi"
TIME_ZONE = "Asia/Ho_Chi_Minh"
USE_I18N = True
USE_TZ = True
USE_L10N = True
# Định dạng ngày/số VN cho template; số trần trong data-*/value= vẫn giữ nguyên
FORMAT_MODULE_PATH = ["config.formats"]
USE_THOUSAND_SEPARATOR = False

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- PMV: SQL Server 2005 Express @ PC KK — dữ liệu THẬT của phần mềm bán vàng ---
# MỌI lệnh sang PMV BẮT BUỘC đi qua apps/pmv/gateway.py (allowlist + audit +
# cảnh báo vượt quyền). KHÔNG import pyodbc ở bất kỳ chỗ nào khác. Xem CLAUDE.md RULE 1.
PMV_MSSQL_HOST = env("PMV_MSSQL_HOST")
PMV_MSSQL_DB = env("PMV_MSSQL_DB", default="PMV_BANLE_KH2")
PMV_MSSQL_USER = env("PMV_MSSQL_USER")
PMV_MSSQL_PASSWORD = env("PMV_MSSQL_PASSWORD")
PMV_MSSQL_ODBC_DRIVER = env("PMV_MSSQL_ODBC_DRIVER", default="ODBC Driver 18 for SQL Server")

# Backup PMV (job đêm 02:00 — config/scheduler.py, lệnh manage.py backup_pmv)
PMV_BACKUP_DIR_KK = env("PMV_BACKUP_DIR_KK", default=r"D:\KHJ_PMV_BACKUP")       # thư mục trên Ổ ĐĨA PC KK
PMV_BACKUP_RETENTION_KK = env("PMV_BACKUP_RETENTION_KK")                          # giữ bao nhiêu NGÀY trên PC KK
PMV_BACKUP_SHARE = env("PMV_BACKUP_SHARE", default="")                            # UNC tới thư mục backup PC KK (trống = chưa kéo về được)
PMV_BACKUP_DIR_LOCAL = env("PMV_BACKUP_DIR_LOCAL", default=r"D:\KHBL_BACKUP\pmv") # nơi chứa bản kéo về máy Mr Giang
PMV_BACKUP_RETENTION_LOCAL = env("PMV_BACKUP_RETENTION_LOCAL")                    # giữ bao nhiêu BẢN ở máy Mr Giang
PMV_LOCAL_MSSQL = env("PMV_LOCAL_MSSQL", default="")                              # vd "localhost\\SQL2014" — trống = chưa restore sandbox
PMV_SANDBOX_DB = env("PMV_SANDBOX_DB", default="PMV_SANDBOX")

# ─────────── KHO LỊCH SỬ (GIANG MSSQL) — bản sao đầy đủ, giữ mãi (Phase 1, 06/09/2026) ───────────
# DB riêng CỦA MÌNH trên chính instance chứa sandbox (localhost\SQL2014). KK chỉ ĐỌC; kho này
# tha hồ ghi (DDL/DML) nhưng gateway.hist_* CHỈ kết nối tới đây, KHÔNG bao giờ chạm KK.
# Instance 2014 dùng Windows auth (như sandbox); để trống USER = Trusted_Connection.
PMV_HIST_MSSQL = env("PMV_HIST_MSSQL", default=PMV_LOCAL_MSSQL)
PMV_HIST_DB = env("PMV_HIST_DB", default="PMV_KH2_HIST")
PMV_HIST_USER = env("PMV_HIST_USER", default="")                                  # trống = Trusted (Windows)
PMV_HIST_PASSWORD = env("PMV_HIST_PASSWORD", default="")
PMV_HIST_VOID_DAYS = env.int("PMV_HIST_VOID_DAYS", default=60)                    # cửa sổ phát hiện đơn bị xóa/void
# ─────────── ĐÍCH DỮ LIỆU NGHIỆP VỤ + CHỐT AN TOÀN GHI ───────────
# PMV_TARGET quyết định màn bán lẻ ĐỌC/GHI vào đâu:
#   "sandbox" = bản sao trên máy Mr Giang (mặc định — tha hồ thử)
#   "kk"      = máy KK, DỮ LIỆU THẬT của tiệm
# Đổi 1 dòng trong .env rồi RESET_KHBL.bat là chuyển, KHÔNG phải sửa code.
# ⚠ Việc backup / kiểm tra / trace / đồng bộ LUÔN nói chuyện với máy KK, không theo cờ này.
PMV_TARGET = env("PMV_TARGET", default="sandbox")

# CHỐT AN TOÀN — cho phép GHI vào máy KK. MẶC ĐỊNH TẮT.
# Khi tắt, gateway TỪ CHỐI mọi lệnh ghi có đích là KK dù proc nằm trong allowlist.
# Chỉ bật khi GĐ duyệt go-live từng nghiệp vụ, và bật xong phải RESET.
PMV_GHI_KK = env.bool("PMV_GHI_KK", default=False)

# Nút SYNC (KK -> Mr Giang), cơ chế mặc định KHÔNG CẦN SHARE (chốt 02/09/2026):
#   SQL KK backup ra PMV_BACKUP_DIR_KK\KHBL_PMV_SYNC.bak (đĩa local KK — luôn ghi được)
#   -> Mr Giang HÚT file về qua kết nối SQL (OPENROWSET BULK SINGLE_BLOB, ~11MB/s LAN)
#   -> restore đè PMV_SANDBOX.
# PMV_SYNC_BAK_READ (tùy chọn): nếu sau này có share đọc được file đó (UNC) thì copy
# thay vì hút qua SQL — nhanh hơn. Trống = hút qua SQL.
PMV_SYNC_BAK_READ = env("PMV_SYNC_BAK_READ", default="")

SERVER_PORT = env("SERVER_PORT")

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "khbl": {"format": "[{asctime}] {levelname} {name}: {message}", "style": "{"},
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "khbl"},
    },
    "root": {"handlers": ["console"], "level": "INFO"},
}
