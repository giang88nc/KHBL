"""CSDL SQLite trong bộ nhớ cho hồi quy bảng giá; không dùng MySQL/MSSQL thật."""
from .base import *  # noqa: F401,F403

DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': ':memory:'}}
PASSWORD_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']
