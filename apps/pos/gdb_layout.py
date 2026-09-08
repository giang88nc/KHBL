"""
gdb_layout — BỐ CỤC IN GIẤY ĐẢM BẢO A5 tùy chỉnh được (GĐ chốt 08/09/2026).

Vấn đề: tờ GĐB in trên giấy mẫu in sẵn (static/img/gdb-a5-blank.jpg), từng khối đặt theo % tuyệt đối;
cỡ chữ lúc in bị ép 9px (~2,4 mm) → quá nhỏ so với bản gốc. Nay mỗi KHỐI có: left/top/width(/height) tính
theo % tờ giấy + cỡ chữ theo **pt** (đơn vị in, 1pt = 0,353 mm) → màn hình xem trước rộng đúng 148 mm nên
NHÌN SAO IN VẬY. Người dùng kéo-thả / nhập số ở trang /banle/giay-dam-bao/mau/, lưu vào PmvState['gdb_layout']
(JSON, không cần migration). Không có bản lưu → MẶC ĐỊNH bên dưới (đã tăng cỡ chữ ~1,3× so với trước).
CSS sinh ra chèn SAU khbl.css với !important nên thắng mọi rule cũ (kể cả .gdb-a5 table{font-size:9px}).
"""
import json

from apps.pmv.models import PmvState

KEY = "gdb_layout"
PAPER_W_MM, PAPER_H_MM = 148, 210

# thứ tự = thứ tự hiện ở bảng chỉnh; sel = selector khối trong _gdb_a5.html (đã gắn data-gdb cùng key)
BLOCKS = [
    {"key": "front_code",      "ten": "Mã vạch (giao khách)",   "sel": ".gdb-a5__front-code",
     "left": 53.5, "top": 19.6,  "w": 20.4, "h": 5.1,  "fs": None},
    {"key": "front_code_text", "ten": "Số dưới mã vạch",       "sel": ".gdb-a5__front-code-text",
     "left": 53.5, "top": 24.85, "w": 20.4, "h": None, "fs": 7},
    {"key": "front_info",      "ten": "Thông tin HĐ / khách",  "sel": ".gdb-a5__front-info",
     "left": 1.7,  "top": 20.7,  "w": 73,   "h": None, "fs": 8},
    {"key": "front_items",     "ten": "Bảng món bán",          "sel": ".gdb-a5__front-items",
     "left": 2,    "top": 26.45, "w": 96,   "h": 12.25, "fs": 7},
    {"key": "front_total",     "ten": "Tổng tiền + bằng chữ",  "sel": ".gdb-a5__front-total",
     "left": 28,   "top": 56.3,  "w": 68,   "h": None, "fs": 9.5},
    {"key": "front_date",      "ten": "Giờ, ngày (giao khách)", "sel": ".gdb-a5__front-date",
     "left": 52,   "top": 62.1,  "w": 45,   "h": None, "fs": 8},
    {"key": "front_emp",       "ten": "Người bán hàng",        "sel": ".gdb-a5__front-emp",
     "left": 67.6, "top": 66.7,  "w": 28,   "h": None, "fs": 8},
    {"key": "store_old",       "ten": "Bảng vàng khách (tiệm giữ)", "sel": ".gdb-a5__store-old",
     "left": 2.8,  "top": 77.15, "w": 63.5, "h": 6.2,  "fs": 6},
    {"key": "store_side",      "ten": "QR + thông tin + thanh toán (tiệm giữ)", "sel": ".gdb-a5__store-side",
     "left": 66.4, "top": 76.8,  "w": 31,   "h": 20.6, "fs": 6.5},
    {"key": "store_footer",    "ten": "Giờ ngày | Bán | Hỗ trợ (tiệm giữ)", "sel": ".gdb-a5__store-footer",
     "left": 4,    "top": 97.25, "w": 92,   "h": None, "fs": 6.5},
]
BLOCK_MAP = {b["key"]: b for b in BLOCKS}
# khối canh phải trong CSS gốc → khi đặt left phải bỏ right
CANH_PHAI = {"front_date", "front_emp", "store_side"}
GIOI_HAN = {"left": (0, 100), "top": (0, 100), "w": (1, 100), "h": (0.5, 100), "fs": (3, 30)}


def mac_dinh():
    return {b["key"]: {k: b[k] for k in ("left", "top", "w", "h", "fs") if b[k] is not None} for b in BLOCKS}


def _ep(v, lo, hi):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return max(lo, min(hi, round(f, 2)))


def load():
    """Bố cục hiện hành = mặc định ⊕ bản lưu (chỉ nhận key/thuộc tính hợp lệ, số trong giới hạn)."""
    out = mac_dinh()
    raw = PmvState.get(KEY, "")
    if raw:
        try:
            data = json.loads(raw)
        except ValueError:
            data = {}
        for key, vals in (data or {}).items():
            if key not in out or not isinstance(vals, dict):
                continue
            for p, v in vals.items():
                if p in out[key] and p in GIOI_HAN:
                    f = _ep(v, *GIOI_HAN[p])
                    if f is not None:
                        out[key][p] = f
    return out


def save(data):
    """Lưu bố cục (đi qua load-merge để chỉ giữ phần hợp lệ). data = dict hoặc None/{} = về mặc định."""
    if not data:
        PmvState.set(KEY, "")
        return mac_dinh()
    clean = mac_dinh()
    for key, vals in data.items():
        if key not in clean or not isinstance(vals, dict):
            continue
        for p, v in vals.items():
            if p in clean[key] and p in GIOI_HAN:
                f = _ep(v, *GIOI_HAN[p])
                if f is not None:
                    clean[key][p] = f
    PmvState.set(KEY, json.dumps(clean, ensure_ascii=False))
    return clean


def _so(v):
    """3.0 → '3', 20.4 → '20.4' (khớp cách JS in số ở trang chỉnh mẫu → CSS 2 bên giống nhau từng ký tự)."""
    return "%g" % float(v)


def css(layout=None):
    """CSS ghi đè vị trí + cỡ chữ từng khối. Tờ giấy trên màn = 148mm thật (WYSIWYG với @page A5)."""
    layout = layout or load()
    out = [".gdb-a5{width:148mm!important;max-width:100%;height:auto;aspect-ratio:148/210}"]
    for b in BLOCKS:
        v = layout[b["key"]]
        rule = [f"left:{_so(v['left'])}%!important", f"top:{_so(v['top'])}%!important", f"width:{_so(v['w'])}%!important"]
        if b["key"] in CANH_PHAI:
            rule.append("right:auto!important")
        if "h" in v:
            rule.append(f"height:{_so(v['h'])}%!important")
        if "fs" in v:
            rule.append(f"font-size:{_so(v['fs'])}pt!important")
        out.append(b["sel"] + "{" + ";".join(rule) + "}")
        if "fs" in v:      # bảng bên trong khối ăn theo khối (CSS gốc ép .gdb-a5 table 9px)
            out.append(b["sel"] + " table{font-size:inherit!important}")
    out.append(".gdb-a5__front-date{text-align:right}")
    return "\n".join(out)
