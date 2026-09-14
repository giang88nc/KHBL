"""Kiểm đơn vị khách hàng: SQLite, template không gọi context PMV/MySQL thật."""
from copy import deepcopy
from .test_prices import *  # noqa: F401,F403

TEMPLATES = deepcopy(TEMPLATES)
TEMPLATES[0]["OPTIONS"]["context_processors"] = [
    p for p in TEMPLATES[0]["OPTIONS"]["context_processors"] if not p.startswith("apps.")
]
