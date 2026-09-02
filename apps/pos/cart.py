"""Giỏ hàng phiên bán (Django session) — số lưu dạng CHUỖI để qua JSON không mất Decimal."""
from apps.pmv import money as M

KEY = "gio"


def get(request):
    g = request.session.get(KEY)
    if not isinstance(g, dict):
        g = {}
    g.setdefault("ban", [])
    g.setdefault("mua", [])
    g.setdefault("cust", None)
    g.setdefault("emp", "")
    g.setdefault("discount", "0")
    g.setdefault("task_add", "0")
    g.setdefault("khach_dua", "0")
    return g


def save(request, g):
    request.session[KEY] = g
    request.session.modified = True


def clear(request):
    request.session[KEY] = {"ban": [], "mua": [], "cust": None, "emp": g_emp(request),
                            "discount": "0", "task_add": "0", "khach_dua": "0"}
    request.session.modified = True


def g_emp(request):
    return (request.session.get(KEY) or {}).get("emp", "")


def them_ban(request, item):
    g = get(request)
    if any(x["code"] == item["code"] for x in g["ban"]):
        return False, "trung"
    g["ban"].append(item)
    save(request, g)
    return True, ""


def xoa_ban(request, code):
    g = get(request)
    g["ban"] = [x for x in g["ban"] if x["code"] != code]
    save(request, g)


def them_mua(request, line):
    g = get(request)
    g["mua"].append(line)
    save(request, g)


def xoa_mua(request, i):
    g = get(request)
    if 0 <= i < len(g["mua"]):
        g["mua"].pop(i)
    save(request, g)


def tong(request):
    """Tổng kết phiếu — dùng ĐÚNG apps/pmv/money.py, không tính lại ở đâu khác."""
    g = get(request)
    ban = [M.dec(x["amount"]) for x in g["ban"]]
    mua = [M.dec(x["amount"]) for x in g["mua"]]
    t = M.bill_totals(ban, mua, g.get("discount") or 0, g.get("task_add") or 0)
    t["so_mon"] = len(g["ban"])
    t["so_mua"] = len(g["mua"])
    t["tl_ban"] = sum((M.dec(x["gr"]) for x in g["ban"]), M.D0)
    kd = M.dec(g.get("khach_dua") or 0)
    t["khach_dua"] = kd
    t["thoi_lai"] = (kd - t["pay"]) if (kd > 0 and t["pay"] > 0) else M.D0
    return t
