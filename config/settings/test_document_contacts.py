"""Isolated SQLite, including strict TransactionTestCase cleanup guard."""
from .test_customer_popup import *
DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': ':memory:',
                         'TEST': {'NAME': 'test_document_contacts.sqlite3'}}}
