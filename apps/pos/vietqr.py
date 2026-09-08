"""VietQR (NAPAS EMVCo) — dựng chuỗi payload chuyển khoản + danh sách ngân hàng.

Không gọi mạng: chuỗi payload dựng thuần Python, mã QR do `segno` vẽ offline (xem view
`ban_qr`). Chuẩn EMVCo QR + phụ lục VietQR: GUID A000000727, dịch vụ QRIBFTTA (tới TÀI KHOẢN).
"""
from django.db import connection

from apps.pmv import money as M

# Số tài khoản nhận MẶC ĐỊNH khi chưa chọn (GĐ chốt 06/09/2026 — TK công ty Kim Hạnh 2).
DEFAULT_BANK_NUMBER = "666141168"

# (mã hiển thị, BIN napas, tên ngân hàng) — các ngân hàng phổ biến VN.
BANKS = [
    ("VCB", "970436", "Vietcombank"),
    ("TCB", "970407", "Techcombank"),
    ("MB", "970422", "MB Bank"),
    ("BIDV", "970418", "BIDV"),
    ("ICB", "970415", "VietinBank"),
    ("VBA", "970405", "Agribank"),
    ("ACB", "970416", "ACB"),
    ("VPB", "970432", "VPBank"),
    ("STB", "970403", "Sacombank"),
    ("TPB", "970423", "TPBank"),
    ("HDB", "970437", "HDBank"),
    ("VIB", "970441", "VIB"),
    ("SHB", "970443", "SHB"),
    ("EIB", "970431", "Eximbank"),
    ("MSB", "970426", "MSB"),
    ("OCB", "970448", "OCB"),
    ("SEAB", "970440", "SeABank"),
    ("NAB", "970428", "Nam A Bank"),
    ("PVCB", "970412", "PVcomBank"),
    ("SCB", "970429", "SCB"),
    ("LPB", "970449", "LPBank"),
    ("ABB", "970425", "ABBANK"),
    ("BAB", "970409", "Bac A Bank"),
    ("VAB", "970427", "VietABank"),
    ("BVB", "970454", "BVBank"),
    ("NCB", "970419", "NCB"),
    ("KLB", "970452", "KienLongBank"),
    ("SGB", "970400", "SaigonBank"),
    ("VIETBANK", "970433", "VietBank"),
    ("PGB", "970430", "PGBank"),
]
_BIN = {code: bin_ for code, bin_, _ in BANKS}
_TEN = {code: ten for code, _, ten in BANKS}


def bank_bin(code):
    """Napas BIN 6 số từ mã NH ('ACB'→970416). Nếu đã là BIN 6 số thì dùng thẳng."""
    c = (code or "").strip().upper()
    if c.isdigit() and len(c) == 6:
        return c
    return _BIN.get(c, "")


def bank_ten(code):
    return _TEN.get((code or "").strip().upper(), code or "")


# ───────── tài khoản nhận đọc từ MySQL app: bảng gold_bank (Active=1) ─────────
def active_banks():
    """Các tài khoản nhận ĐANG BẬT (gold_bank.Active=1). bank_bin = mã NH ('ACB'),
    bank_number = số tài khoản, bank_user = chủ tài khoản."""
    with connection.cursor() as cur:
        cur.execute("SELECT id, bank_bin, bank_number, bank_name, bank_user, type "
                    "FROM gold_bank WHERE Active = 1 ORDER BY id")
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


def get_bank(bank_id):
    if not bank_id:
        return None
    with connection.cursor() as cur:
        cur.execute("SELECT id, bank_bin, bank_number, bank_name, bank_user "
                    "FROM gold_bank WHERE id = %s", [bank_id])
        r = cur.fetchone()
        if not r:
            return None
        return dict(zip([d[0] for d in cur.description], r))


def default_bank_id():
    """id của TK mặc định (số 666141168) trong các TK đang bật; không có → TK bật đầu tiên."""
    banks = active_banks()
    for b in banks:
        if (b.get("bank_number") or "").strip() == DEFAULT_BANK_NUMBER:
            return b["id"]
    return banks[0]["id"] if banks else None


def _tlv(idx, val):
    """1 trường EMV: ID(2) + độ dài(2, đệm 0) + giá trị."""
    val = str(val)
    return f"{idx}{len(val):02d}{val}"


def _crc16(s):
    """CRC-16/CCITT-FALSE (poly 0x1021, init 0xFFFF) — chuẩn checksum EMVCo QR."""
    crc = 0xFFFF
    for ch in s.encode("utf-8"):
        crc ^= ch << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return f"{crc:04X}"


def bank_code_tu_bin(bin_):
    """BIN napas → mã NH ('970416'→'ACB'); không có trong danh mục → trả lại BIN."""
    for code, b, _ in BANKS:
        if b == str(bin_ or "").strip():
            return code
    return str(bin_ or "").strip()


def _tach_tlv(s):
    """Chuỗi EMV TLV → list (id, giá trị). Sai độ dài → dừng."""
    out, i = [], 0
    while i + 4 <= len(s):
        idx, ln = s[i:i + 2], s[i + 2:i + 4]
        if not ln.isdigit():
            break
        n = int(ln)
        out.append((idx, s[i + 4:i + 4 + n]))
        i += 4 + n
    return out


def parse(chuoi):
    """Phân tích chuỗi VietQR (EMVCo). Trả dict {bin, bank_code, bank_ten, account, amount, info, hop_le, loi}.
    Chỉ nhận QR CHUYỂN KHOẢN chuẩn NAPAS: tag 38 chứa GUID A000000727 (QRIBFTTA) — QR khác → hop_le=False."""
    s = str(chuoi or "").strip()
    kq = {"bin": "", "bank_code": "", "bank_ten": "", "account": "", "amount": 0, "info": "", "hop_le": False, "loi": ""}
    if not s.startswith("000201"):
        kq["loi"] = "Không phải mã QR thanh toán EMVCo"
        return kq
    if len(s) >= 4 and _crc16(s[:-4]) != s[-4:].upper():
        kq["loi"] = "Mã QR sai checksum (ảnh mờ / thiếu góc) — chụp lại rõ hơn"
        return kq
    for idx, val in _tach_tlv(s):
        if idx == "38":
            con = dict(_tach_tlv(val))
            if con.get("00", "").upper() != "A000000727":
                kq["loi"] = "QR không thuộc hệ VietQR/NAPAS (không phải chuyển khoản ngân hàng VN)"
                return kq
            ben = dict(_tach_tlv(con.get("01", "")))
            kq["bin"], kq["account"] = ben.get("00", ""), ben.get("01", "")
        elif idx == "54":
            try:
                kq["amount"] = int(float(val))
            except ValueError:
                pass
        elif idx == "62":
            kq["info"] = dict(_tach_tlv(val)).get("08", "")
    if not kq["bin"] or not kq["account"]:
        kq["loi"] = "QR thiếu BIN ngân hàng / số tài khoản"
        return kq
    kq["bank_code"] = bank_code_tu_bin(kq["bin"])
    kq["bank_ten"] = bank_ten(kq["bank_code"]) if kq["bank_code"] != kq["bin"] else f"BIN {kq['bin']}"
    kq["hop_le"] = True
    return kq


def doc_anh(data):
    """Giải mã QR trong ảnh (bytes JPEG/PNG) bằng OpenCV QRCodeDetector — thử cả ảnh gốc, phóng to, xám cân bằng.
    Trả chuỗi hoặc ''. Không có OpenCV → ném RuntimeError."""
    try:
        import cv2
        import numpy as np
    except ImportError as exc:                       # pragma: no cover
        raise RuntimeError("Chưa cài opencv-python-headless") from exc
    arr = np.frombuffer(bytes(data), dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        return ""
    det = cv2.QRCodeDetector()
    ung_vien = [img]
    h, w = img.shape[:2]
    if max(h, w) < 900:
        ung_vien.append(cv2.resize(img, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC))
    xam = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    ung_vien.append(cv2.equalizeHist(xam))
    ung_vien.append(cv2.threshold(xam, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1])
    for uv in ung_vien:
        try:
            s, _, _ = det.detectAndDecode(uv)
        except cv2.error:
            s = ""
        if s:
            return s
    return ""


def payload(bank_code, account_no, amount=0, info=""):
    """Chuỗi VietQR chuyển khoản TỚI TÀI KHOẢN. amount ≤ 0 → QR không kèm số tiền
    (người chuyển tự nhập). Ném ValueError nếu thiếu BIN/số tài khoản."""
    bin_ = bank_bin(bank_code)
    account_no = "".join(ch for ch in str(account_no or "") if ch.isalnum())
    if not bin_:
        raise ValueError("Chưa chọn ngân hàng hợp lệ")
    if not account_no:
        raise ValueError("Chưa nhập số tài khoản")

    ben = _tlv("00", bin_) + _tlv("01", account_no)          # tổ chức thụ hưởng
    mai = _tlv("00", "A000000727") + _tlv("01", ben) + _tlv("02", "QRIBFTTA")
    amt = int(M.tron_ngan(amount)) if M.dec(amount) > 0 else 0

    body = _tlv("00", "01")
    body += _tlv("01", "12" if amt else "11")                 # có tiền = one-time
    body += _tlv("38", mai)
    body += _tlv("53", "704")                                 # VND
    if amt:
        body += _tlv("54", str(amt))
    body += _tlv("58", "VN")
    info = "".join(c for c in str(info or "") if c.isalnum() or c in " -").strip()[:25]   # 08/09: giữ '-' cho mã phiếu 26-09-08-000167
    if info:
        body += _tlv("62", _tlv("08", info))
    body += "6304"                                            # ID+len của CRC, rồi tính CRC
    return body + _crc16(body)
