"""
CÔNG THỨC TIỀN — bản DUY NHẤT của KHBL. Mọi màn hình phải gọi vào đây.

⚠ SQL KHÔNG TÍNH TIỀN HỘ: TRN_RT_BUYSELL_Ins/_Upd đọc thẳng SellAmount từ XML, nhận
SellTotalAmount/TotalAmount qua tham số. Sai công thức = hóa đơn thật sai tiền, lệch sổ
tồn và sổ quỹ, không có lưới an toàn phía SQL.

Đã kiểm chứng trên dữ liệu thật (khảo sát 03/09/2026):
- BÁN     28.678/28.678 dòng · SellAmount = tròn( GoldReal/HS × SellRate × 1000 + TaskPrice × 1000 )
          GoldReal = TotalWeight − DiamondWeight (hột trừ TRƯỚC khi nhân giá)
          TaskPrice tính THEO MÓN, KHÔNG nhân trọng lượng
- ĐỔI     3.634/3.634 dòng vàng cũ trong hóa đơn bán · BuyAmount = tròn( GW/HS × BuyRate × 1000 × Pct/100 )
          ⚠ dùng HẰNG 1000, KHÔNG dùng cột CcyRate (bảng này lưu CcyRate=1)
- THÂU    5.986/5.986 phiếu · TotalAmount = tròn( GW/HS × BuyRate × 1000 × Pct/100 + AddMoney )
          Pct nhân TRƯỚC, AddMoney cộng SAU
- TỔNG    18.307/18.307 hóa đơn · SellTotal = Σ(dòng đã tròn) · TotalAmount = SellTotal − BuyTotal
          (KHÔNG trừ Discount) · PayAmount = TotalAmount − Discount + TaskPriceAdd
          PayAmount < 0 nghĩa là TIỆM TRẢ LẠI khách.

Quy ước đơn vị: trọng lượng lưu theo LY. 1 phân = 10 · 1 chỉ = 100 · 1 lượng = 1000.
HS (hệ số chia) = 100 khi PriceUnit ∈ ('L','M') — giá niêm yết theo CHỈ; = 1 khi 'G' (theo GRAM).
Giá trong I_XRATE tính bằng NGHÌN đồng (14100 = 14.100.000 ₫/chỉ) → nhân 1000.

BẮT BUỘC dùng Decimal, cấm float: sai số float lệch đúng ở các ca nửa nghìn.
Làm tròn HALF-AWAY-FROM-ZERO như T-SQL ROUND (Python round() là banker's rounding — SAI).
"""
from decimal import ROUND_HALF_UP, Decimal

from django.core.cache import cache

D0 = Decimal("0")
_THOUSAND = Decimal("1E3")
_ONE = Decimal("1")
# Hệ số nghìn→đồng: cột XRate (dòng bán) và CcyRate (phiếu thâu) đều = 1000 trên 100% dữ liệu
RATE_SCALE = Decimal("1000")


def dec(x):
    """Về Decimal an toàn (None/'' → 0). Không bao giờ đi qua float."""
    if x is None or x == "":
        return D0
    if isinstance(x, Decimal):
        return x
    if isinstance(x, float):  # chỉ khi lỡ nhận float — chuyển qua str để khỏi nhiễu nhị phân
        return Decimal(repr(x))
    return Decimal(str(x))


def hs(price_unit):
    """Hệ số chia theo đơn vị niêm yết giá: 'L'/'M' → 100 (giá theo chỉ), 'G' → 1 (theo gram)."""
    return Decimal("100") if (price_unit or "L").upper() in ("L", "M") else _ONE


# ----------------------------- làm tròn -----------------------------

def _quy_cach_lam_tron(client=None):
    """Đọc SYS_PARAMETERS['QuyCachLamTronVND'] (cache 60s). '3@499@0' = tròn NGHÌN,
    '0@0@0' = tròn ĐỒNG. Đọc lúc chạy — GĐ đổi tham số là toàn bộ tiền đổi theo."""
    v = cache.get("khbl:quycach_lamtron")
    if v is None:
        v = "3@499@0"
        try:
            if client is None:
                from .client import PmvClient

                client = PmvClient(tag="money")
            v = client.sys_param("QuyCachLamTronVND") or v
        except Exception:
            pass  # mất kết nối KK → giữ mặc định đang dùng thực tế
        cache.set("khbl:quycach_lamtron", v, 60)
    return v


def round_vnd(x, client=None):
    """Làm tròn tiền y hệt dbo.fn_LamTronVND (T-SQL ROUND: nửa làm tròn RA XA số 0)."""
    x = dec(x)
    exp = _THOUSAND if str(_quy_cach_lam_tron(client)).startswith("3@") else _ONE
    return x.quantize(exp, rounding=ROUND_HALF_UP)


# ----------------------------- từng dòng -----------------------------

def gold_real(total_weight, diamond_weight):
    """Phần VÀNG của món = tổng trọng lượng − trọng lượng hột. Tiền chỉ tính trên phần này."""
    return dec(total_weight) - dec(diamond_weight)


def sell_amount(total_weight, diamond_weight, sell_rate, task_price=0, price_unit="L", client=None):
    """Thành tiền 1 dòng hàng BÁN RA."""
    gr = gold_real(total_weight, diamond_weight)
    tien_vang = gr / hs(price_unit) * dec(sell_rate) * RATE_SCALE
    tien_cong = dec(task_price) * RATE_SCALE
    return round_vnd(tien_vang + tien_cong, client)


def buy_amount_in_bill(gold_weight, buy_rate, percent_value=100, price_unit="L", client=None):
    """Tiền 1 dòng VÀNG CŨ khách đưa vào trong hóa đơn bán (TRN_RT_BUYSELL_BUYGOLD).
    ⚠ hằng 1000 — bảng này lưu CcyRate=1, dùng cột đó sẽ sai 1000 lần."""
    tien = dec(gold_weight) / hs(price_unit) * dec(buy_rate) * RATE_SCALE * dec(percent_value) / Decimal("100")
    return round_vnd(tien, client)


def buy_amount_standalone(gold_weight, buy_rate, percent_value=100, add_money=0, weight_unit="L", client=None):
    """Tiền PHIẾU THÂU độc lập (TRN_RT_BUYGOLD). % nhân TRƯỚC, tiền bù/bớt cộng SAU (có thể âm)."""
    tien = dec(gold_weight) / hs(weight_unit) * dec(buy_rate) * RATE_SCALE * dec(percent_value) / Decimal("100")
    return round_vnd(tien + dec(add_money), client)


# ----------------------------- ĐỔI NGANG VÀNG (GĐ chốt 05/09/2026) -----------------------------
# Khách đổi dẻ CÙNG TUỔI VÀNG lấy hàng mới: phần dẻ trong HẠN MỨC = trọng lượng vàng MỚI bán ra
# cùng loại được định GIÁ BÁN RA (ngang giá, tiệm không ăn chênh mua–bán); phần dẻ DƯ hạn mức
# định GIÁ THÂU (mua vào). Khớp THEO TỪNG LOẠI: dẻ D18K ↔ hàng 18K, D9999 ↔ N9999...
# Map dẻ → mã vàng bán (để gộp trọng lượng hàng bán cùng loại làm hạn mức). Giá bán ra thì
# lấy THẲNG SellRate của chính dòng dẻ trong bảng giá (đã kiểm: D18K.Sell = 18K.Sell = 8850).
DE_TO_BASE = {"D18K": "18K", "D24K": "24K", "D9999": "N9999", "DBK": "BK", "DBk": "BK",
              "DSJC": "SJC", "DT": "VT"}


def de_base(gold_code):
    """Mã vàng BÁN tương ứng của 1 mã dẻ (D18K→18K). Fallback: bỏ 'D' đầu."""
    c = (gold_code or "").strip()
    if c in DE_TO_BASE:
        return DE_TO_BASE[c]
    return c[1:] if c[:1].upper() == "D" and len(c) > 1 else c


# Nhãn TUỔI VÀNG hiển thị (GĐ chốt 05/09/2026): 18K→610 · 24K→980 · N9999→99.99 · BK · VT · SJC.
# Áp cho cả mã dẻ (quy về base trước). Không có trong map → trả base.
TUOI_LABEL = {"18K": "610", "24K": "980", "N9999": "99.99", "9999": "99.99",
              "BK": "BK", "VT": "VT", "SJC": "SJC"}


def tuoi(gold_code):
    base = de_base(gold_code)
    return TUOI_LABEL.get(base, base)


def weight_chi(w, price_unit="L"):
    """Trọng lượng quy về 1 ĐƠN VỊ, KHÔNG kèm chữ: 'L'/'M' → CHỈ (chính xác 0,001 chỉ = 0,1 ly),
    'G'/'K' → GRAM. Dùng cho các ô TL trên bảng bán hàng."""
    if (price_unit or "L").upper() in ("G", "K"):
        return _vn(w, 3)          # gram
    return _vn(dec(w) / Decimal("100"), 3)   # ly → chỉ


def chia_doi_ngang(gold_weight, tl_hot, han_muc, sell_rate, buy_rate, price_unit="L", client=None):
    """Chia 1 dẻ khi 'đổi ngang' theo hạn mức (trọng lượng vàng bán ra cùng loại còn lại).

    Trả list bản ghi phần: [{'w': TL vàng, 'hot': TL hột, 'rate': giá, 'tien': thành tiền, 'ngang': bool}]
    - Phần w_sell = min(TL vàng, hạn mức) → giá BÁN RA (đổi ngang).
    - Phần w_buy  = còn lại            → giá THÂU.
    Hột dồn vào phần CUỐI (không ảnh hưởng tiền — tiền chỉ tính trên TL vàng)."""
    w = dec(gold_weight)
    hot = dec(tl_hot)
    hm = max(dec(han_muc), D0)
    w_sell = min(w, hm)
    w_buy = w - w_sell
    phan = []
    if w_sell > 0:
        phan.append({"w": w_sell, "hot": D0, "rate": dec(sell_rate), "ngang": True,
                     "tien": buy_amount_in_bill(w_sell, sell_rate, 100, price_unit, client)})
    if w_buy > 0 or not phan:
        phan.append({"w": w_buy, "hot": D0, "rate": dec(buy_rate), "ngang": False,
                     "tien": buy_amount_in_bill(w_buy, buy_rate, 100, price_unit, client)})
    phan[-1]["hot"] = hot  # hột dồn vào dòng cuối, không đổi tiền
    return phan


# ----------------------------- tổng hóa đơn -----------------------------

def bill_totals(sell_amounts, buy_amounts=(), discount=0, task_price_add=0):
    """Gộp tổng 1 hóa đơn bán–đổi. Các dòng truyền vào PHẢI đã làm tròn từng dòng.

    Trả dict: sell_total · buy_total · total (Sell − Buy, KHÔNG trừ giảm giá) ·
              pay (total − giảm giá + công thêm) · tra_lai (|pay| khi pay < 0)."""
    sell_total = sum((dec(x) for x in sell_amounts), D0)
    buy_total = sum((dec(x) for x in buy_amounts), D0)
    total = sell_total - buy_total
    pay = total - dec(discount) + dec(task_price_add)
    return {
        "sell_total": sell_total,
        "buy_total": buy_total,
        "total": total,
        "pay": pay,
        "tra_lai": -pay if pay < 0 else D0,
    }


def bot_le(total, buoc=10000):
    """Số tiền bớt lẻ để tròn chục nghìn (nút F6): Discount = total mod 10.000."""
    t = dec(total)
    b = dec(buoc)
    if t <= 0 or b <= 0:
        return D0
    return t - (t // b) * b


def tron_ngan(x):
    """Làm tròn tiền về HÀNG NGHÌN (nửa làm tròn ra xa số 0) — mọi giá trị trên màn
    TÍNH TỔNG dùng hàm này để không lộ số lẻ dưới 1.000 ₫."""
    return dec(x).quantize(_THOUSAND, rounding=ROUND_HALF_UP)


def bot_le_goiy(total, buoc=10000, so_muc=3):
    """Gợi ý các mức TIỀN BỚT để tròn xuống bội số `buoc` (mặc định 10.000):
    lẻ hiện tại, lẻ + buoc, lẻ + 2·buoc… (lẻ=0 → buoc, 2·buoc…). Không vượt quá total.
    VD total 7.346.000 → [6.000, 16.000, 26.000]."""
    t = tron_ngan(total)
    b = dec(buoc)
    if t <= 0 or b <= 0:
        return []
    le = t - (t // b) * b
    goc = le if le > 0 else b
    ra = []
    muc = goc
    while len(ra) < so_muc and muc <= t:
        ra.append(muc)
        muc += b
    return ra


# ----------------------------- trọng lượng -----------------------------

def vn_so(x, max_dp=3):
    """Số kiểu VN dùng được từ ngoài (alias công khai của _vn)."""
    return _vn(x, max_dp)


def _vn(x, max_dp=3):
    """Số kiểu VN: bỏ số 0 thừa, dấu phẩy thập phân."""
    q = dec(x).quantize(Decimal(1).scaleb(-max_dp), rounding=ROUND_HALF_UP).normalize()
    s = format(q, "f")
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s.replace(".", ",")


def weight_screen(w, price_unit="L"):
    """Chuỗi trọng lượng NGẮN cho màn hình: '7 ly' · '5 phân' · '1,05 chỉ' · '1,037 lượng' · '8,11 g'."""
    w = dec(w)
    if (price_unit or "L").upper() == "G":
        return f"{_vn(w)} g"
    a = abs(w)
    if a >= 1000:
        return f"{_vn(a / 1000)} lượng"
    if a >= 100:
        return f"{_vn(a / 100)} chỉ"
    if a >= 10:
        return f"{_vn(a / 10)} phân"
    return f"{_vn(a)} ly"


def weight_bill(w, price_unit="L"):
    """Chuỗi ĐẦY ĐỦ để in giấy đảm bảo — chép ĐÚNG từng nhánh của dbo.GoldWeightToChar_Bill
    (đã đọc source + đối chiếu 120 giá trị thật), kể cả các nét riêng của vendor:
      1037→'1L0C3P7Ly' · 1100→'1L1C0P0Ly' · 100→'1C' · 101→'1C0P1Ly' · 285→'2C8P5Ly'
      50→'5P0Ly' (nhánh PHÂN luôn in 'Ly' vì dòng IF trong proc bị comment)
      3→'3Ly' · 0.5→'0Ly.5D' · 106.2→'1C0P6Ly.2D'
    Phần lẻ: lấy ĐÚNG 2 chữ số sau dấu chấm rồi bỏ số 0 đuôi, thêm hậu tố 'D'."""
    w = abs(dec(w))
    n = int(w)
    if (price_unit or "L").upper() in ("G", "K"):
        # vendor cắt hụt 1 ký tự phần lẻ (LEN − charindex) → 8.11 ra '8g1100000'
        frac8 = format(w.quantize(Decimal("1E-8")), "f").split(".")[1]
        hau_to = (price_unit or "G").lower()
        return f"{n}{hau_to}00" if not frac8.strip("0") else f"{n}{hau_to}{frac8[:-1]}"

    s = ""
    if n // 1000 > 0:
        s += f"{n // 1000}L"
        if n % 1000 > 0:
            d3 = str(n).rjust(3, "0")[-3:]
            s += f"{d3[0]}C{d3[1]}P{d3[2]}Ly"
    elif n // 100 > 0:
        s += f"{n // 100}C"
        if n % 100 > 0:
            d2 = str(n).rjust(2, "0")[-2:]
            s += f"{d2[0]}P{d2[1]}Ly"
    elif n // 10 > 0:
        s += f"{n // 10}P{n % 10}Ly"   # luôn có 'Ly' — đúng như proc
    else:
        s += f"{n}Ly"

    le2 = format(w.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP), "f").split(".")[1].rstrip("0")
    if le2:
        s += f".{le2}D"
    return s


def money_vn(x, suffix=" ₫"):
    """Tiền kiểu VN: 1.234.567 ₫ · số âm dùng dấu trừ thật U+2212."""
    x = dec(x).quantize(_ONE, rounding=ROUND_HALF_UP)
    am = x < 0
    s = f"{abs(int(x)):,}".replace(",", ".")
    return ("−" if am else "") + s + suffix
