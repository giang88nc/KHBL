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
    "apps.oa",          # SỔ TIN NHẮN ZALO OA tập trung của 3 hệ (GĐ chốt 14/09/2026) — xem apps/oa/models.py
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "apps.pos.mobile_auth.MobileSessionMiddleware",
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
                "apps.pmv.hist_read.nguon",   # nhãn "kho lịch sử · dữ liệu tới HH:mm" (Phase 4)
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
        # Cột datetime lưu GIỜ VN naive (đổi từ UTC đêm 20/09/2026 — docs/KE_HOACH_DOI_GIO_VN.md).
        # ⚠ Phải ĐÚNG chuỗi này (trùng TIME_ZONE bên dưới): khác tên là Django sinh CONVERT_TZ,
        # MySQL máy này chưa nạp bảng múi giờ ⇒ lọc theo ngày ra RỖNG.
        "TIME_ZONE": "Asia/Ho_Chi_Minh",
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

# Bản sao ảnh hồ sơ khách do web quản lý. Đây là kho RIÊNG, không phải MEDIA_URL
# và không được web server public trực tiếp: CCCD chỉ đi ra qua view đã đăng nhập.
# Tên tệp mirror chính xác ImagePath* trên PMV; chỉ khác thư mục gốc trên máy Giang.
CUSTOMER_IMAGE_ARCHIVE_ROOT = Path(env(
    "CUSTOMER_IMAGE_ARCHIVE_ROOT", default=str(BASE_DIR / "media" / "cccd")
))

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- PMV: SQL Server 2005 Express @ PC KK — dữ liệu THẬT của phần mềm bán vàng ---
# MỌI lệnh sang PMV BẮT BUỘC đi qua apps/pmv/gateway.py (allowlist + audit +
# cảnh báo vượt quyền). KHÔNG import pyodbc ở bất kỳ chỗ nào khác. Xem CLAUDE.md RULE 1.
PMV_MSSQL_HOST = env("PMV_MSSQL_HOST")
PMV_MSSQL_DB = env("PMV_MSSQL_DB", default="PMV_BANLE_KH2")
PMV_MSSQL_USER = env("PMV_MSSQL_USER")
PMV_MSSQL_PASSWORD = env("PMV_MSSQL_PASSWORD")
PMV_MSSQL_ODBC_DRIVER = env("PMV_MSSQL_ODBC_DRIVER", default="ODBC Driver 18 for SQL Server")

# Bảng giá công khai của ứng dụng UPSERT chính. Ứng dụng đó đọc MySQL:3306
# (pmv_report.gold_prices); KHBL chỉ đọc API này rồi ghi vào MySQL:3308 của mình.
# Không đặt user/password MySQL:3306 trong KHBL.
PMV_REPORT_GOLD_PRICES_URL = env(
    "PMV_REPORT_GOLD_PRICES_URL",
    default="http://127.0.0.1:1276/bang-gia-v%C3%A0ng-kim-hanh-2/api/prices",
)
PMV_REPORT_GOLD_PRICES_TIMEOUT = env.int("PMV_REPORT_GOLD_PRICES_TIMEOUT", default=8)

# Backup PMV (job đêm 02:00 — config/scheduler.py, lệnh manage.py backup_pmv)
PMV_BACKUP_DIR_KK = env("PMV_BACKUP_DIR_KK", default=r"D:\KHJ_PMV_BACKUP")       # thư mục trên Ổ ĐĨA PC KK
PMV_BACKUP_RETENTION_KK = env("PMV_BACKUP_RETENTION_KK")                          # giữ bao nhiêu NGÀY trên PC KK
PMV_BACKUP_SHARE = env("PMV_BACKUP_SHARE", default="")                            # UNC tới thư mục backup PC KK (trống = chưa kéo về được)
PMV_BACKUP_DIR_LOCAL = env("PMV_BACKUP_DIR_LOCAL", default=r"D:\KHBL_BACKUP\pmv") # nơi chứa bản kéo về máy Mr Giang
PMV_BACKUP_RETENTION_LOCAL = env("PMV_BACKUP_RETENTION_LOCAL")                    # giữ bao nhiêu BẢN ở máy Mr Giang
PMV_LOCAL_MSSQL = env("PMV_LOCAL_MSSQL", default="")                              # vd "localhost\\SQL2014" — trống = chưa restore sandbox
PMV_SANDBOX_DB = env("PMV_SANDBOX_DB", default="PMV_SANDBOX")

# PASSCODE mở khóa hóa đơn ĐÃ CHỐT trên màn bán (GĐ chốt 07/09/2026): bấm 🔒 → nhập đúng mới cho
# UPDATE (thêm món/dẻ, đổi NV/khách). Thứ tự: passcode RIÊNG từng user (bảng unlock_passcodes, đặt
# qua nút 🔑 topbar) > giá trị này > TRỐNG = MẬT KHẨU WEB của chính người đang đăng nhập.
KHBL_UNLOCK_PASSCODE = env("KHBL_UNLOCK_PASSCODE", default="")

# ─────────── KHO LỊCH SỬ (GIANG MSSQL) — bản sao đầy đủ, giữ mãi (Phase 1, 06/09/2026) ───────────
# DB riêng CỦA MÌNH trên chính instance chứa sandbox (localhost\SQL2014). KK chỉ ĐỌC; kho này
# tha hồ ghi (DDL/DML) nhưng gateway.hist_* CHỈ kết nối tới đây, KHÔNG bao giờ chạm KK.
# Instance 2014 dùng Windows auth (như sandbox); để trống USER = Trusted_Connection.
PMV_HIST_MSSQL = env("PMV_HIST_MSSQL", default=PMV_LOCAL_MSSQL)
PMV_HIST_DB = env("PMV_HIST_DB", default="PMV_KH2_HIST")
PMV_HIST_USER = env("PMV_HIST_USER", default="")                                  # trống = Trusted (Windows)
PMV_HIST_PASSWORD = env("PMV_HIST_PASSWORD", default="")
PMV_HIST_VOID_DAYS = env.int("PMV_HIST_VOID_DAYS", default=60)                    # cửa sổ phát hiện đơn bị xóa/void
# Backup kho lịch sử (GĐ chốt 06/09/2026): DB nằm ổ C (default path instance) → .bak sang Ổ KHÁC (D).
# Lệnh backup_hist tự từ chối nếu thư mục backup cùng ổ với file .mdf.
PMV_HIST_BACKUP_DIR = env("PMV_HIST_BACKUP_DIR", default=r"D:\KHBL_BACKUP\hist")
PMV_HIST_BACKUP_RETENTION = env.int("PMV_HIST_BACKUP_RETENTION", default=30)      # giữ bao nhiêu BẢN
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

# ĐẶT-CỌC: token OA chỉ ở máy chủ; mẫu phải được OA duyệt trước khi gửi.
DATCOC_OA_ACCESS_TOKEN = env('DATCOC_OA_ACCESS_TOKEN', default='')
