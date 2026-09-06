"""ĐỊNH TUYẾN ĐỌC quá khứ → KHO LỊCH SỬ + FAILOVER khi KK tắt (Phase 4, 06/09/2026).

Quy tắc (GĐ chốt):
- Truy vấn NGÀY QUÁ KHỨ (ngày < hôm nay) → đọc `PMV_KH2_HIST` trước (KK có thể đã prune;
  kho là bản đầy đủ). HIST lỗi → lùi về live.
- Truy vấn HÔM NAY / không gắn ngày → đọc LIVE (theo PMV_TARGET) trước; KK không đọc được
  → lùi về HIST + đánh cờ `failover` (template hiện "dữ liệu tới HH:mm").
Cùng câu SQL chạy được ở cả 2 kho vì schema HIST mirror KK (chỉ thêm 2 cột kỹ thuật).
Cờ nguồn giữ ở thread-local, context processor `nguon` đọc rồi XÓA (consume-once) để không
rò sang request kế tiếp cùng thread.
"""
import datetime
import threading
import time

from apps.pmv import gateway as G
from apps.pmv.client import PmvClient

_tl = threading.local()
_asof_cache = {"gia": None, "luc": 0.0}


def _dat(nguon, ly_do=""):
    _tl.nguon, _tl.ly_do = nguon, ly_do


def reset():
    _dat("live", "")


def co_hien_tai():
    return getattr(_tl, "nguon", "live"), getattr(_tl, "ly_do", "")


def la_qua_khu(ngay_iso):
    try:
        return datetime.date.fromisoformat(str(ngay_iso)[:10]) < datetime.date.today()
    except (TypeError, ValueError):
        return False


def asof():
    """Thời điểm đồng bộ HIST gần nhất (MAX last_run bảng điều khiển), cache 30 giây."""
    now = time.monotonic()
    if _asof_cache["gia"] is not None and now - _asof_cache["luc"] < 30:
        return _asof_cache["gia"]
    try:
        r = G.hist_query("SELECT MAX(last_run) AS m FROM [_hist_sync_state]")
        v = r[0]["m"] if r else None
    except Exception:
        v = None
    _asof_cache.update(gia=v, luc=now)
    return v


def doc(fn, *, ngay_iso=None, tag="hist_read"):
    """Chạy fn(client) trên kho phù hợp. fn chỉ được dùng client.query (SELECT)."""
    reset()
    live = PmvClient(tag=tag)
    hist = PmvClient("hist", tag=tag)
    if ngay_iso and la_qua_khu(ngay_iso):
        try:
            kq = fn(hist)
            _dat("hist", "qua_khu")
            return kq
        except Exception as exc:
            G.canh_bao(tag, f"Kho lịch sử không đọc được ({str(exc)[:80]}) → đọc live")
            kq = fn(live)
            _dat("live", "")
            return kq
    try:
        kq = fn(live)
        _dat("live", "")
        return kq
    except Exception as exc:
        G.canh_bao(tag, f"KK không đọc được ({str(exc)[:80]}) → lùi về kho lịch sử")
        kq = fn(hist)
        _dat("hist", "failover")
        return kq


def nguon(request):
    """Context processor: {nguon_hist, nguon_ly_do, hist_asof}. Đọc xong thì xóa cờ."""
    ng, ly = co_hien_tai()
    reset()
    return {
        "nguon_hist": ng == "hist",
        "nguon_ly_do": ly,
        "hist_asof": asof() if ng == "hist" else None,
    }
