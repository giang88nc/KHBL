"""
CHUẨN HÓA CHỮ TIẾNG VIỆT BỊ HỎNG MÃ — dùng chung cho mọi chỗ nhận dữ liệu từ ngoài
(máy quét CCCD, dán từ Excel/clipboard, dữ liệu cũ trong CSDL).

Bản gốc của thuật toán + ví dụ + cách thử: skill `.claude/skills/chuan-hoa-tieng-viet/`.

4 kiểu hỏng đã gặp thật, xử lý theo thứ tự:

1. BYTE-SỐ  — mỗi byte UTF-8 ≥ 128 bị in ra thành số thập phân 4 chữ số bắt đầu bằng 0.
   'Ng022501870141c' → Ng + [225,187,141] + c → E1 BB 8D → 'ọ' → 'Ngọc'
   (Máy quét CCCD của tiệm đang trả kiểu này khi đi qua clipboard/Excel.)

2. MOJIBAKE — byte UTF-8 bị đọc nhầm bằng CP1252/Latin-1: 'TrÆ°Æ¡ng' → 'Trương'.

3. THỰC THỂ HTML / PERCENT — '&#7885;' hoặc '%E1%BB%8D' → 'ọ'.

4. TỔ HỢP DẤU RỜI — 'e' + U+0301 → 'é' (chuẩn NFC), để so khớp tên không lệch.

Nguyên tắc: KHÔNG BỊA. Byte nào mất hẳn trên đường truyền thì không đoán ký tự,
mà trả về ký tự thay thế '�' và báo qua `canh_bao` để người nhập tự sửa.
"""
import re
import unicodedata
import urllib.parse

# Chỉ byte 128..255; không đổi một phần chuỗi số thành chữ.
_BYTE_SO = re.compile(r"(?:0(?:12[89]|1[3-9]\d|2[0-4]\d|25[0-5]))+")
_THUC_THE = re.compile(r"&#(x[0-9a-fA-F]+|\d+);")
_PERCENT = re.compile(r"(?:%[0-9a-fA-F]{2})+")
KY_TU_HONG = "�"


def chuan_hoa(s, ve_nfc=True, sua_mat=True):
    """Trả (chuỗi đã sửa, danh sách cảnh báo). Chuỗi vốn đã đúng thì trả nguyên vẹn."""
    if not s:
        return "", []
    goc = str(s)
    canh_bao = []
    kq = goc

    kq = _sua_thuc_the(kq, canh_bao)
    kq = _sua_percent(kq, canh_bao)
    kq = _sua_byte_so(kq, canh_bao)
    kq = _sua_ma_unicode(kq, canh_bao)
    kq = _sua_oem(kq, canh_bao)
    kq = _sua_mojibake(kq, canh_bao)
    kq = _sua_byte_dau_mo_coi(kq, canh_bao)

    if sua_mat:
        kq = sua_byte_mat(kq, canh_bao)
    if ve_nfc:
        kq = unicodedata.normalize("NFC", kq)
    kq = re.sub(r"[ \t]+", " ", kq).strip()
    if KY_TU_HONG in kq:
        canh_bao.append(
            f"Còn {kq.count(KY_TU_HONG)} ký tự không khôi phục được (byte đã mất trên đường "
            "truyền) — chỗ hiện ‹?› cần gõ tay lại")
    return kq, canh_bao


def _sua_thuc_the(s, canh_bao):
    if "&#" not in s:
        return s
    def _1(m):
        v = m.group(1)
        try:
            n = int(v[1:], 16) if v[0] in "xX" else int(v)
            return m.group(0) if not 0 <= n <= 0x10FFFF or 0xD800 <= n <= 0xDFFF else chr(n)
        except ValueError:
            return m.group(0)
    kq = _THUC_THE.sub(_1, s)
    if kq != s:
        canh_bao.append("Đã đổi thực thể HTML (&#…;) về chữ")
    return kq


def _sua_percent(s, canh_bao):
    if "%" not in s:
        return s
    def _1(m):
        try:
            return urllib.parse.unquote(m.group(0), errors="strict")
        except (UnicodeDecodeError, ValueError):
            return m.group(0)
    kq = _PERCENT.sub(_1, s)
    if kq != s:
        canh_bao.append("Đã giải mã đoạn %XX")
    return kq


def _sua_ma_unicode(s, canh_bao):
    """Mã Unicode thập phân trong từ: 'Tr432417ng' → 'Trương'.

    Một lần bấm phím có thể nối nhiều code point (432 + 417). Chỉ nhận khi dãy số
    có chữ đứng sát bên và chỉ có đúng một cách tách thành chữ tiếng Việt.
    """
    allowed = _CHU_VIET | set("ăâêôơư")

    def _tach(cum):
        if len(cum) > 32:
            return None
        cach = []

        def tim(pos, out):
            if len(cach) > 1:
                return
            if pos == len(cum):
                cach.append(out)
                return
            for size in (3, 4):
                if pos + size > len(cum):
                    continue
                n = int(cum[pos:pos + size])
                c = chr(n) if n <= 0x10FFFF else ""
                if c and c.lower() in allowed:
                    tim(pos + size, out + c)

        tim(0, "")
        return cach[0] if len(cach) == 1 else None

    def _1(m):
        before = s[m.start() - 1] if m.start() else ""
        after = s[m.end()] if m.end() < len(s) else ""
        if not (before.isalpha() or after.isalpha()):
            return m.group(0)
        fixed = _tach(m.group(0))
        return fixed if fixed is not None else m.group(0)

    kq = re.sub(r"\d{3,}", _1, s)
    if kq != s:
        canh_bao.append("Đã đổi mã Unicode thập phân trong chữ tiếng Việt")
    return kq


def _sua_oem(s, canh_bao):
    """Phục hồi các dạng byte thấp đã quan sát từ đúng máy quét CCCD.

    ≤ và α có nhiều nghĩa nếu đứng riêng, nên chỉ sửa trong ngữ cảnh từ đã biết chắc.
    """
    changed = False

    def _1(m):
        nonlocal changed
        word = m.group(0)
        fixed = word
        if any(c in word for c in "░═♥"):
            fixed = word.translate(str.maketrans({"░": "ư", "═": "ọ", "♥": "ă"}))
        if "░" in word:
            fixed = fixed.replace("í", "ơ")
        # Có lượt quét/trình duyệt đã đổi byte đầu trước, để lại trạng thái nửa chừng.
        fixed = fixed.replace("Trưíng", "Trương").replace("TRƯÍNG", "TRƯƠNG")
        fixed = re.sub(r"^([Kk]h)≤m(?=\W|$)", r"\1óm", fixed)
        fixed = re.sub(r"^Cα(?=\W|$)", "Cà", fixed)
        changed |= fixed != word
        return fixed

    kq = re.sub(r"\S+", _1, s)
    if changed:
        canh_bao.append("Đã phục hồi ký tự DOS/OEM từ byte thấp — vui lòng đối chiếu với thẻ")
    return kq


def _sua_byte_so(s, canh_bao):
    """'0225' '0187' '0141' → byte 225,187,141 → giải mã UTF-8."""
    if not _BYTE_SO.search(s):
        return s
    def _1(m):
        cum = m.group(0)
        before = s[m.start() - 1] if m.start() else ""
        after = s[m.end()] if m.end() < len(s) else ""
        if any(c in "0123456789" for c in before + after):
            return cum
        bs = bytes(int(cum[i + 1:i + 4]) for i in range(0, len(cum), 4))
        try:
            decoded = bs.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            decoded = ""
        if (decoded and all(c.lower() in _CHU_VIET for c in decoded)
                and (before.isalpha() or after.isalpha() or cum == s)):
            return decoded
        if len(bs) == 1 and 0xC2 <= bs[0] <= 0xF4 and before.isalpha() and after.isalpha():
            return KY_TU_HONG
        return cum
    kq = _BYTE_SO.sub(_1, s)
    if kq != s:
        canh_bao.append("Đã dựng lại chữ từ mã số byte UTF-8")
    return kq


def _sua_mojibake(s, canh_bao):
    """'TrÆ°Æ¡ng' → 'Trương'. Không dò bằng danh sách ký tự (dễ sót) mà THỬ giải mã
    rồi CHẤM ĐIỂM: chỉ nhận bản mới khi nó nhiều chữ Việt có dấu hơn hẳn bản cũ."""
    if all(ord(c) < 0x80 for c in s):
        return s
    diem_goc = _diem_viet(s)
    # vòng 1: giải mã CHẶT (khôi phục trọn vẹn) — vòng 2: chấp nhận mất vài byte
    for nghiem in ("strict", "replace"):
        tot_nhat = None
        for bang_ma in ("cp1252", "latin-1"):
            try:
                thu = s.encode(bang_ma, errors="strict").decode("utf-8", errors=nghiem)
            except (UnicodeEncodeError, UnicodeDecodeError, LookupError):
                continue
            d = _diem_viet(thu)
            if d > diem_goc and (tot_nhat is None or d > _diem_viet(tot_nhat[1])):
                tot_nhat = (bang_ma, thu)
        if tot_nhat:
            canh_bao.append(f"Đã sửa lỗi mã kiểu mojibake ({tot_nhat[0]} ↔ utf-8)")
            return tot_nhat[1]
    return s


_CHU_VIET = set("àáảãạăằắẳẵặâầấẩẫậèéẻẽẹêềếểễệìíỉĩịòóỏõọôồốổỗộơờớởỡợ"
                "ùúủũụưừứửữựỳýỷỹỵđ")


# Ký tự RÁC đặc trưng của chuỗi hỏng mã (Ã Â Æ » ‹ … ™ °…). Phải TRỪ ĐIỂM chúng, vì
# bản mojibake cũng chứa sẵn á/â nên nếu chỉ cộng điểm thì hai bản hòa nhau và không sửa.
_KY_TU_RAC = set("ÃÂÄÅÆÐÑ×÷ØÞßÝ¡¢£¤¥¦§¨©ª«¬®¯°±²³´µ¶·¸¹º»¼½¾¿"
                 "€‚ƒ„…†‡ˆ‰Š‹ŒŽ‘’“”•–—˜™š›œžŸ") | {KY_TU_HONG}


def _diem_viet(s):
    """Điểm 'ra chất tiếng Việt': +1 mỗi chữ có dấu, −1 mỗi ký tự rác kiểu mojibake."""
    thap = s.lower()
    return sum(1 for c in thap if c in _CHU_VIET) - sum(1 for c in s if c in _KY_TU_RAC)


# ── khôi phục byte bị NUỐT ─────────────────────────────────────────────────
# Quy luật đã truy được (khớp 100% mẫu thật): đường truyền đọc byte bằng CP1252 rồi lọc
# bỏ ký tự ngoài ASCII. Byte 0x80–0x9F CÓ ký tự trong CP1252 thì bị nuốt (0x83 của 'ă');
# byte KHÔNG có (0x81 0x8D 0x8F 0x90 0x9D) thì sống sót thành số (0x8D của 'ọ').
_BYTE_BI_NUOT = set(range(0x82, 0x8D)) | set(range(0x91, 0x9D)) | {0x9E, 0x9F}
_NGUYEN_AM = set("aăâeêioôơuưy")
_PHU_AM = set("bcdđghklmnpqrstvx")


def _ung_vien(dau_byte):
    """Các chữ TIẾNG VIỆT có thể dựng từ 1 byte mồ côi + 1 byte đã bị nuốt."""
    ra = []
    for b2 in _BYTE_BI_NUOT:
        try:
            c = bytes([dau_byte, b2]).decode("utf-8")
        except UnicodeDecodeError:
            continue
        if c.lower() in _CHU_VIET or c.lower() in "ăâêôơư":
            ra.append(c)
    return ra


def _sua_byte_dau_mo_coi(s, canh_bao):
    """Sửa byte đầu UTF-8 còn lộ ra trong một chuỗi đã đúng một phần.

    Ví dụ ``NÄm CÄn`` giữ byte đầu C4 của ``ă`` nhưng mất byte sau 83. Không thể
    giải mã lại cả câu vì các đoạn ``Khóm``/``Năm`` khác vốn đã là Unicode đúng.
    Chỉ thay khi byte đầu + ngữ cảnh hoa/thường, nguyên âm/phụ âm còn đúng một
    ứng viên tiếng Việt; trường hợp mơ hồ được giữ nguyên để người dùng đối chiếu.
    """
    kq, doan = list(s), []
    for i, c in enumerate(kq):
        dau = ord(c)
        if dau not in (0xC3, 0xC4, 0xC5, 0xC6):
            continue
        truoc = kq[i - 1] if i else ""
        sau = kq[i + 1] if i + 1 < len(kq) else ""
        if not (truoc.isalpha() and sau.isalpha()):
            continue
        uv = _ung_vien(dau)
        if sau.islower() or truoc.islower():
            uv = [x for x in uv if x.islower()]
        elif truoc.isupper() and sau.isupper():
            uv = [x for x in uv if x.isupper()]
        if truoc.lower() in _PHU_AM and sau.lower() in _PHU_AM:
            uv = [x for x in uv if x.lower() in _NGUYEN_AM]
        uv = sorted(set(uv))
        if len(uv) == 1:
            kq[i] = uv[0]
            doan.append(truoc + uv[0] + sau)
    if doan:
        canh_bao.append("Đã phục hồi byte đầu UTF-8 bị sót: "
                        + ", ".join(dict.fromkeys(doan)) + " — vui lòng đối chiếu với thẻ")
    return "".join(kq)


def sua_byte_mat(s, canh_bao=None):
    """Điền lại ký tự ở chỗ '�' KHI VÀ CHỈ KHI còn đúng MỘT khả năng hợp lệ
    (lọc theo hoa/thường của chữ bên cạnh và theo vị trí nguyên âm/phụ âm).
    Còn từ 2 khả năng trở lên thì GIỮ '�' — không đoán bừa vào tên/địa chỉ khách."""
    if KY_TU_HONG not in s:
        return s
    kq, doan = list(s), []
    for i, c in enumerate(kq):
        if c != KY_TU_HONG:
            continue
        truoc = next((kq[j] for j in range(i - 1, -1, -1) if kq[j] != KY_TU_HONG), "")
        sau = kq[i + 1] if i + 1 < len(kq) else ""
        uv = []
        for db in (0xC3, 0xC4, 0xC5, 0xC6):
            uv += _ung_vien(db)
        # cùng kiểu hoa/thường với chữ liền kề
        if (sau and sau.islower()) or (truoc and truoc.islower()):
            uv = [x for x in uv if x.islower()]
        elif truoc and truoc.isupper() and sau and sau.isupper():
            uv = [x for x in uv if x.isupper()]
        # kẹp giữa 2 phụ âm ⇒ vị trí này phải là NGUYÊN ÂM
        if truoc.lower() in _PHU_AM and sau.lower() in _PHU_AM:
            uv = [x for x in uv if x.lower() in _NGUYEN_AM]
        uv = sorted(set(uv))
        if len(uv) == 1:
            kq[i] = uv[0]
            doan.append(f"{truoc}{uv[0]}{sau}")
    if doan and canh_bao is not None:
        canh_bao.append("Đã dựng lại " + str(len(doan)) + " ký tự mất byte (chỉ khi duy nhất 1 khả năng): "
                        + ", ".join(dict.fromkeys(doan)) + " — vui lòng nhìn lại cho chắc")
    return "".join(kq)


def bo_dau(s):
    """Bỏ dấu để so khớp/tìm kiếm: 'Trương' → 'Truong' (đ → d)."""
    s = unicodedata.normalize("NFD", str(s or ""))
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return s.replace("đ", "d").replace("Đ", "D")


def hoa_dau_tu(s):
    """Tên IN HOA từ máy quét → 'Trương Ngọc Giang'. Chuỗi đã đúng dạng thì giữ nguyên."""
    s = re.sub(r"\s+", " ", str(s or "").strip())
    if not s or s != s.upper():
        return s
    return " ".join(w[:1].upper() + w[1:].lower() if w else w for w in s.split(" "))
