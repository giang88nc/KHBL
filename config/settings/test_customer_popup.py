"""Isolated SQLite tests for the cross-app customer popup, including OA collations."""
from .test_customer_sync import *  # noqa: F401,F403
from django.db.backends.signals import connection_created


def _sqlite_collations(sender, connection, **kwargs):
    if connection.vendor == 'sqlite':
        connection.connection.create_collation('ascii_bin', lambda a, b: (a > b) - (a < b))


connection_created.connect(_sqlite_collations, weak=False, dispatch_uid='customer_popup_ascii_bin')
