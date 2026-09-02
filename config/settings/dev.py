"""Dev tạm: set DJANGO_SETTINGS_MODULE=config.settings.dev"""
from .base import *  # noqa: F401,F403

DEBUG = True
ALLOWED_HOSTS = ["*"]
