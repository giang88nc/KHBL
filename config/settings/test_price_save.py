"""SQLite riêng cho TransactionTestCase; tên test_ tuân thủ chốt dọn dữ liệu."""
from .test_prices import *  # noqa: F401,F403

DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': ':memory:',
                         'TEST': {'NAME': 'test_price_save.sqlite3'}}}
