"""Passcode mở khóa của tài khoản web.

Passcode là thuộc tính của ``auth_user`` vì người xác nhận là người đăng nhập web,
không phải tài khoản vận hành PMV/KK. Cột được thêm bằng migration PMV 0008;
không khai báo nó trên Django ``User`` để vẫn dùng được auth.User mặc định.
"""
from django.contrib.auth.hashers import check_password, make_password
from django.db import connection


def _table_name(user):
    return connection.ops.quote_name(user._meta.db_table)


def lay_hash(user):
    """Lấy mã băm passcode từ auth_user. Không bao giờ trả passcode gốc."""
    if not user or not user.is_authenticated:
        return ""
    with connection.cursor() as cursor:
        cursor.execute(f"SELECT passcode FROM {_table_name(user)} WHERE id = %s", [user.pk])
        row = cursor.fetchone()
    return str(row[0] or "") if row else ""


def da_dat(user):
    return bool(lay_hash(user))


def kiem(user, value):
    value = (value or "").strip()
    saved_hash = lay_hash(user)
    return bool(value and saved_hash and check_password(value, saved_hash))


def dat(user, value):
    """UPSERT passcode băm vào cột auth_user.passcode của chính user."""
    gan_hash(user, make_password(value))


def gan_hash(user, saved_hash):
    """Ghi mã băm có sẵn; dùng khi migration/kiểm thử cần hoàn nguyên trạng thái."""
    with connection.cursor() as cursor:
        cursor.execute(f"UPDATE {_table_name(user)} SET passcode = %s WHERE id = %s", [saved_hash, user.pk])


def xoa(user):
    gan_hash(user, "")
