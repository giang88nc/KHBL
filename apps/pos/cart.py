"""
PHIẾU BÁN ĐANG LÀM DỞ — giữ trong Django session.

Mỗi dòng hàng lưu nguyên bộ cột mà proc vendor cần (khóa "row"), chứ không lưu vài
trường rồi dựng lại: nhờ vậy mở hóa đơn cũ ra sửa và quét mã mới đi CHUNG một đường.
Mọi giá trị ép về CHUỖI — session đi qua JSON, Decimal không sống sót.
⚠ Chuỗi phải là dạng thập phân thường: str(Decimal('0E-8')) ra '0E-8' làm chết proc
  (xem PmvClient._xml_val). Dùng _chuoi() bên dưới, đừng str() thẳng.

trn_id rỗng = phiếu MỚI chưa lưu · có trn_id = đang sửa hóa đơn đã lưu.
"""
from collections import OrderedDict
from decimal import Decimal

from apps.pmv import money as M

KEY = "phieu"


def _chuoi(v):
    if isinstance(v, Decimal):
        return format(v, "f")
    return "" if v is None else str(v)


def _rong(emp=""):
    return {"trn_id": "", "bill_code": "", "status": "", "ngay": "", "cust": None, "emp": emp,
            "ban": [], "doi": [], "bot": "0", "cong_them": "0", "vang_them": "0",
            "coc": "0", "ghi_chu": ""}


def get(request):
    g = request.session.get(KEY)
    if not isinstance(g, dict):
        g = {}
    for k, v in _rong().items():
        g.setdefault(k, v)
    return g


def save(request, g):
    request.session[KEY] = g
    request.session.modified = True


def clear(request, giu_nv=True):
    """Phiếu mới. Giữ lại nhân viên bán đang chọn — người bán không phải chọn lại mỗi phiếu."""
    save(request, _rong(get(request).get("emp", "") if giu_nv else ""))


# ─────────────── dòng hàng ───────────────
def _dong(tien, row):
    return {"tien": _chuoi(tien), "row": {k: _chuoi(v) for k, v in row.items()}}


def them_ban(request, tien, row):
    g = get(request)
    ma = row.get("ProductCode")
    if any(x["row"].get("ProductCode") == ma for x in g["ban"]):
        return False, f"Mã {ma} đã có trong phiếu"
    g["ban"].append(_dong(tien, row))
    _danh_so(g)
    save(request, g)
    return True, ""


def xoa_ban(request, ma):
    g = get(request)
    g["ban"] = [x for x in g["ban"] if x["row"].get("ProductCode") != ma]
    _danh_so(g)
    save(request, g)


def them_doi(request, tien, row):
    g = get(request)
    g["doi"].append(_dong(tien, row))
    save(request, g)


def xoa_doi(request, i):
    g = get(request)
    if 0 <= i < len(g["doi"]):
        g["doi"].pop(i)
    save(request, g)


def dat_doi(request, rows):
    """Thay TOÀN BỘ danh sách vàng đổi bằng rows = [(tiền, row_dict), ...]."""
    g = get(request)
    g["doi"] = [_dong(t, r) for t, r in rows]
    save(request, g)


def _danh_so(g):
    for i, x in enumerate(g["ban"], 1):
        x["row"]["STT"] = str(i)


# ─────────────── nạp hóa đơn đã lưu ───────────────
def nap(request, phieu):
    """Đổ hóa đơn đọc từ PMV (bill.doc) vào phiếu đang làm để xem/sửa."""
    g = _rong(get(request).get("emp", ""))
    g.update(trn_id=phieu["trn_id"], bill_code=phieu["bill_code"], status=phieu["status"],
             ngay=_ngay_iso(phieu.get("ngay")), emp=phieu.get("emp_id") or "",
             ghi_chu=phieu.get("ghi_chu") or "",
             bot=_chuoi(phieu["tong"]["bot"]), cong_them=_chuoi(phieu["tong"]["cong_them"]),
             vang_them=_chuoi(phieu["tong"]["vang_them"]), coc=_chuoi(phieu["tong"]["coc"]),
             ban=[_dong(t, r) for t, r in phieu["ban"]],
             doi=[_dong(t, r) for t, r in phieu["doi"]])
    if phieu.get("cust_id") and phieu["cust_id"] != "CU0000000000000":
        g["cust"] = {"id": phieu["cust_id"], "name": phieu.get("khach") or "",
                     "code": "", "phone": "", "diem": "0"}
    save(request, g)
    return g


def _ngay_iso(v):
    if not v:
        return ""
    return v.strftime("%Y-%m-%d") if hasattr(v, "strftime") else str(v)[:10]


# ─────────────── dựng lại để lưu / để hiện ───────────────
def dong_ban(g):
    """[(tiền, row)] đúng dạng apps/pos/bill.luu cần."""
    return [(M.dec(x["tien"]), x["row"]) for x in g["ban"]]


def dong_doi(g):
    return [(M.dec(x["tien"]), x["row"]) for x in g["doi"]]


def tong(request):
    """Tổng kết phiếu — dùng ĐÚNG công thức trong apps/pos/bill.py, không tính lại."""
    from . import bill as B

    g = get(request)
    t = B.tinh_tong(sum((M.dec(x["tien"]) for x in g["ban"]), M.D0),
                    sum((M.dec(x["tien"]) for x in g["doi"]), M.D0),
                    g.get("bot"), g.get("cong_them"), g.get("vang_them"), g.get("coc"))
    t["so_mon"] = len(g["ban"])
    t["so_doi"] = len(g["doi"])
    t["tl_ban"] = sum((M.dec(x["row"].get("GoldReal")) for x in g["ban"]), M.D0)
    t["tl_doi"] = sum((M.dec(x["row"].get("TotalGoldWeight")) for x in g["doi"]), M.D0)
    # Chi tiết TL vàng theo TỪNG LOẠI (tuổi vàng) cho chân bảng 2 khối
    t["tl_ban_loai"] = _tl_theo_loai(g["ban"], "GoldReal")
    t["tl_doi_loai"] = _tl_theo_loai(g["doi"], "GoldWeight")
    return t


def _tl_theo_loai(rows, field):
    """Gộp trọng lượng vàng theo TUỔI VÀNG (nhãn) + đơn vị giá, giữ khối lượng THÔ
    để filter tl_chi quy về chỉ/gram khi hiển thị. Trả list giữ thứ tự xuất hiện."""
    out = OrderedDict()
    for x in rows:
        r = x["row"]
        w = M.dec(r.get(field))
        if not w:
            continue
        key = (M.tuoi(r.get("GoldCode")), (r.get("PriceUnit") or "L"))
        out[key] = out.get(key, M.D0) + w
    return [{"loai": k[0], "unit": k[1], "tl": v} for k, v in out.items()]
