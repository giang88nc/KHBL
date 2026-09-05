"""VietQR (NAPAS EMVCo) — dựng chuỗi payload chuyển khoản + danh sách ngân hàng.

Không gọi mạng: chuỗi payload dựng thuần Python, mã QR do `segno` vẽ offline (xem view
`ban_qr`). Chuẩn EMVCo QR + phụ lục VietQR: GUID A000000727, dịch vụ QRIBFTTA (tới TÀI KHOẢN).
"""
from apps.pmv import money as M

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
    return _BIN.get((code or "").strip().upper(), "")


def bank_ten(code):
    return _TEN.get((code or "").strip().upper(), "")


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
    info = "".join(c for c in str(info or "") if c.isalnum() or c == " ").strip()[:25]
    if info:
        body += _tlv("62", _tlv("08", info))
    body += "6304"                                            # ID+len của CRC, rồi tính CRC
    return body + _crc16(body)
