"""
Đọc chuỗi QR trên thẻ CCCD gắn chip (máy quét hoạt động như bàn phím, gõ thẳng vào ô nhập).

Khuôn 7 trường ngăn bằng dấu |:
    số CCCD | số CMND cũ | họ tên | ngày sinh ddmmyyyy | giới tính | địa chỉ | ngày cấp ddmmyyyy
Ví dụ: 012345678901|123456789|Nguyễn Văn A|07041988|Nam|Khóm 4, TT. Năm Căn, Cà Mau|15092023

Máy chủ nhận dạng RAW và xếp hạng; popup gọi API qua CCCD.analyze(), sau đó
người dùng đối chiếu gợi ý trước khi điền dữ liệu đã phục hồi vào biểu mẫu.
"""
import datetime
from collections import defaultdict
import re
import unicodedata

from . import vn_text as VN
from . import cccd_context as context
from . import cccd_learning as learning
from .vn_text import chuan_hoa, hoa_dau_tu


def parse(raw):
    """Giữ giao diện cũ: dữ liệu đã kiểm tra, hoặc None nếu chuỗi không hợp lệ."""
    return phan_tich(raw)["data"]


def _ngay(s):
    """ddmmyyyy → yyyy-mm-dd (rỗng nếu không hợp lệ)."""
    if not re.fullmatch(r"[0-9]{8}", s or ""):
        return ""
    try:
        return datetime.date(int(s[4:]), int(s[2:4]), int(s[:2])).isoformat()
    except ValueError:
        return ""


# Nhận diện dạng dữ liệu, không gắn cứng với số thứ tự/hãng máy quét.
PROFILE_LABELS = {
    "unicode": "Unicode", "utf8_mojibake": "UTF-8 bị đọc sai bảng mã",
    "scanner_bytes": "Byte UTF-8 bị đổi ký tự", "decimal": "Mã Unicode dạng số",
    "oem": "Ký tự OEM/byte thấp", "byte_numbers": "Byte UTF-8 dạng số",
    "escaped": "HTML/percent", "unknown": "Chưa nhận diện chắc chắn",
}
_VN = VN._CHU_VIET | set("ăâêôơư")
_LETTERS = _VN | {c.upper() for c in _VN}
_DOS = {3: "♥", 16: "►", 17: "◄"}
_OEM = defaultdict(set)
for _letter in sorted(_LETTERS):
    _byte = ord(_letter) % 256
    if _byte >= 128 or _byte in _DOS:
        _glyph = _DOS.get(_byte) or bytes([_byte]).decode("cp437")
        if _glyph != _letter:
            _OEM[_glyph].add(_letter)
_OEM_SIGNAL = re.compile("[" + re.escape("".join(c for c in _OEM if not c.isalpha())) + "ΓΩαπφτñÑúí]")
_MOJI = re.compile(r"[ÃÄÆ][^ ]|á[º»]|[\x80-\x9f]")
_STRUCTURE = re.compile(
    r"^([0-9]{12})\|((?:[0-9]{9})?)\|(.*?)\|([0-9]{8}|)\|([^|]*)\|(.*?)\|([0-9]{8}|)\|*$",
    re.S,
)
_FIELD_KEYS = ("ho_ten", "gioi_tinh", "dia_chi")


def split_raw(raw):
    """Find logical fields by identity/date anchors, never by pipe count.

    The strict layout also supports intentionally empty date/gender fields.
    Recovery allows missing separators, repeated pipes and extension fields;
    ambiguous date boundaries are rejected rather than shifting customer data.
    """
    structural = raw.lstrip("\ufeff").strip(" \r\n\t").replace("á»|", "á»\x81")
    match = _STRUCTURE.fullmatch(structural)
    if match and "|" not in match[3] and "|" not in match[6]:
        return list(match.groups()), [], []
    cid = re.match(r"^([0-9]{12})(?![0-9])", structural)
    # With every separator missing the old ID can immediately follow the ID.
    if not cid:
        cid = re.match(r"^([0-9]{12})(?=[0-9]{9}[^0-9])", structural)
    if not cid:
        return None, [], ["Không xác định được số CCCD gồm đúng 12 chữ số ASCII."]
    tail = structural[cid.end():].lstrip("| \t")
    old = ""
    if tail[:1].isdigit():
        old_match = re.match(r"([0-9]{9})(?![0-9])", tail)
        if not old_match:
            return None, [], ["Số CMND cũ sai định dạng; không tự dịch chuyển trường."]
        old, tail = old_match[1], tail[old_match.end():].lstrip("| \t")
    dates = list(re.finditer(r"(?<![0-9])([0-9]{8})(?![0-9])", tail))
    ending = re.search(r"([0-9]{8})$", tail)
    if ending and _ngay(ending[1]) and not any(d.start() == ending.start() for d in dates):
        dates.append(ending)
    if len(dates) < 2:
        return None, [], ["Chưa tìm đủ mốc ngày sinh/ngày cấp để phân chia QR; hãy quét lại."]
    birth = dates[0]
    if not _ngay(birth[1]):
        return None, [], ["Ngày sinh trong QR không hợp lệ."]
    name = tail[:birth.start()].strip("| \t")
    if not name or re.search(r"[0-9]{8}", name):
        return None, [], ["Không xác định được họ tên trước ngày sinh."]
    # Recognized gender is required only when recovering damaged boundaries.
    after = tail[birth.end():].lstrip("| \t")
    gender = re.match(r"(Ná»¯|N7919|N∩|Nữ|NỮ|Nam|NAM|Nu|NU|N)", after)
    if gender and after[gender.end():]:
        next_char = after[gender.end()]
        if not (next_char in "| \t" or next_char.isupper() or next_char.isdigit()):
            gender = None
    if not gender:
        return None, [], ["Không xác định được ranh giới giới tính/địa chỉ; hãy quét lại."]
    address_start = len(tail) - len(after) + gender.end()
    possible = []
    for issued in dates[1:]:
        if issued.start() < address_start or not _ngay(issued[1]):
            continue
        remainder = tail[issued.end():]
        if remainder and not remainder.startswith("|"):
            continue
        possible.append(issued)
    if len(possible) != 1:
        return None, [], ["Mốc ngày cấp bị thiếu hoặc có nhiều cách hiểu; không tự đoán ranh giới."]
    issued = possible[0]
    address = tail[address_start:issued.start()].strip("| \t")
    if "|" in name:
        name = re.sub(r"\|+", " ", name)
    if "|" in address:
        address = re.sub(r"\|+", ", ", address)
    extra = tail[issued.end():].strip("|").split("|") if tail[issued.end():].strip("|") else []
    return [cid[1], old, name, birth[1], gender[1], address, issued[1]], extra, []


def detect_profiles(raw):
    profiles = []
    if _MOJI.search(raw):
        profiles.append("utf8_mojibake")
    if "á»\\" in raw or "á»|" in raw:
        profiles.append("scanner_bytes")
    if VN._sua_byte_so(raw, []) != raw:
        profiles.append("byte_numbers")
    if _decimal_field(raw, "dia_chi") != raw:
        profiles.append("decimal")
    # Latin characters may be valid Vietnamese. Only use the OEM profile when
    # a non-letter glyph or decimal run corroborates the device output mode.
    # Mojibake byte characters (e.g. ½, ¤, ») are not independent OEM evidence.
    # Inspect what remains after complete UTF-8 byte sequences are accounted for.
    oem_probe = _repair_segments(raw)
    if any(c in oem_probe for c in _OEM if not c.isalpha()) or ("decimal" in profiles and _OEM_SIGNAL.search(oem_probe)):
        profiles.append("oem")
    if "&#" in raw or re.search(r"%[0-9a-fA-F]{2}", raw):
        profiles.append("escaped")
    return profiles or (["unknown"] if _damage(raw) else ["unicode"])


def _byte_value(char):
    if ord(char) <= 255:
        return ord(char)
    try:
        return char.encode("cp1252", errors="strict")[0]
    except UnicodeEncodeError:
        return None


def _repair_segments(value):
    """Strict UTF-8 per sequence. Preserve intact Unicode and failed bytes.

    Decode before whitespace cleanup: U+00A0 can represent byte A0 in C3 A0.
    Undefined CP1252 C1 characters are mapped by byte value, not discarded.
    """
    value = value.replace("á»\\", "á»\x85").replace("á»|", "á»\x81")
    out, index = [], 0
    while index < len(value):
        lead = _byte_value(value[index])
        size = 2 if lead is not None and 0xC2 <= lead <= 0xDF else 3 if lead is not None and 0xE0 <= lead <= 0xEF else 0
        chunk = [_byte_value(c) for c in value[index:index + size]]
        if size and len(chunk) == size and all(b is not None for b in chunk):
            try:
                text = bytes(chunk).decode("utf-8", errors="strict")
                if text in _LETTERS:
                    out.append(text)
                    index += size
                    continue
            except UnicodeDecodeError:
                pass
        out.append(value[index])
        index += 1
    return "".join(out)


def _clean_text(value, key):
    value = unicodedata.normalize("NFC", value)
    value = re.sub(r"[ \t]+", " ", value).strip()
    if key == "ho_ten":
        value = hoa_dau_tu(value)
    if key == "dia_chi":
        value = re.sub(r",[ \t]*(?:,[ \t]*)+", ", ", value)
        value = re.sub(r",[ \t]*", ", ", value)
        value = re.sub(r"\b(TP|Tp|tp)\.(?=\S)", r"\1. ", value)
    return value


def _scanner_typography(value, key):
    """Formatting candidates for damaged scanner text, not a global name fixer."""
    # A scanner/keyboard can leave terminal capitals (TháI, KhảI, LáI).
    value = re.sub(r"(?<=[a-zà-ỹ])[A-Z](?=\W|$)", lambda m: m[0].lower(), value)
    if key == "dia_chi":
        value = re.sub(r"\bTP(?:\.|(?=\s))\s*", "TP. ", value, flags=re.I)
        value = re.sub(r"\bKP(?=\s*[0-9])", "KP", value, flags=re.I)
        value = re.sub(r"(?<!\w)([0-9]+(?:/[0-9]+)*)([a-z])(?=\W|$)",
                       lambda m: m[1] + m[2].upper(), value)
        value = re.sub(r"(?<=[a-zà-ỹ])(?=[A-ZĐ][a-zà-ỹ])", " ", value)
        value = re.sub(r"^([0-9]+(?:/[0-9A-Za-z]+)*),\s*(?!KP[0-9\s]|B[0-9])(?=[A-ZĐ])", r"\1 ", value)
    # Equivalent Vietnamese tone-placement conventions, same word/meaning.
    for source, target in (("oà", "òa"), ("oá", "óa"), ("oả", "ỏa"), ("oã", "õa"), ("oạ", "ọa")):
        value = re.sub(source + r"\b", target, value)
    return value


def _damage(value):
    value = re.sub(r"(?<!\w)(?:[0-9]{1,4}[A-Z]|(?:B|[A-Z]{2,3})[0-9]{1,4})(?!\w)", "", value)
    return (len(_MOJI.findall(value)) * 4 + sum(c in VN._KY_TU_RAC for c in value)
            + sum(c.isalpha() and c not in _LETTERS and not c.isascii() for c in value) * 2
            + sum(c in _OEM and not c.isalpha() for c in value)
            + len(re.findall(r"[�?|\\]|(?<=[^\W\d_])[0-9]{3,}|[0-9]{3,}(?=[^\W\d_])|%[0-9a-fA-F]{2}|&#", value)) * 3)


def _oem_candidates(value):
    """Bounded beam; collisions (í = ơ/ạ) remain explicit alternatives."""
    beam = [""]
    ambiguous = False
    for index, char in enumerate(value):
        choices = sorted(_OEM.get(char, ()))
        if choices and ((index and value[index - 1].islower()) or
                        (index + 1 < len(value) and value[index + 1].islower())):
            choices = [c for c in choices if c.islower()] or choices
        if not choices:
            choices = [char]
        elif len(choices) > 1:
            ambiguous = True
        beam = [prefix + suffix for prefix in beam for suffix in choices][:16]
    return beam, ambiguous


def _plausibility(value):
    # Orthographic tie breaker only; never sufficient to clear review status.
    lower = value.lower()
    mixed_case = sum(a.islower() and b.isupper() for a, b in zip(value, value[1:]))
    return -12 * sum(lower.count(s) for s in ("ưạ", "ưí", "ưă", "ạngh")) - 8 * mixed_case


def _lattice_candidates(value, key, profiles, model):
    """Decode at token boundaries, then beam-search word/phrase context.

    A long address no longer exhausts the beam on early ambiguous characters.
    OEM and decimal are alternative edges over ORIGINAL tokens, so Unicode
    recovered by one edge is never accidentally decoded a second time.
    """
    tokens = re.split(r"([ \t\r\n,./]+)", value)
    if len(tokens) > 512:
        return []
    beam = [("", 0, False)]
    count = 0
    for token in tokens:
        if not token or re.fullmatch(r"[ \t\r\n,./]+", token):
            beam = [(text + token, score, uncertain) for text, score, uncertain in beam]
            continue
        count += 1
        forms = {}

        def add(text, uncertain=False, channel_bonus=0):
            # Never transform standalone house numbers or known block codes.
            if re.fullmatch(r"[0-9]+|[0-9]{1,4}[A-Z]|(?:B|[A-Z]{2,3})[0-9]{1,4}", token) and text != token:
                return
            text = re.sub(r"(?<=[a-zà-ỹ])[A-Z](?=\W|$)", lambda m: m[0].lower(), text)
            old = forms.get(text)
            if old is None or channel_bonus > old[1] or (channel_bonus == old[1] and not uncertain):
                forms[text] = (uncertain, channel_bonus)

        repaired = _repair_segments(token)
        add(repaired, channel_bonus=4 if repaired != token else 0)
        safe = repaired
        for helper in (VN._sua_thuc_the, VN._sua_percent, VN._sua_byte_so):
            safe = helper(safe, [])
        decoded = _decimal_field(safe, key)
        add(decoded, channel_bonus=4 if decoded != token else 0)
        orphan = VN.sua_byte_mat(VN._sua_byte_dau_mo_coi(safe, []), [])
        if orphan != safe:
            add(_decimal_field(orphan, key), True, 2)
        if "oem" in profiles and repaired == token:
            variants, ambiguous = _oem_candidates(token)
            for variant in variants:
                add(_decimal_field(variant, key), ambiguous, 4 if variant != token else 0)
        if "Trưíng" in token:
            add(token.replace("Trưíng", "Trương"), True)
        expanded = []
        for prefix, score, uncertain in beam:
            for text, (unsure, channel_bonus) in forms.items():
                proposed = prefix + text
                local = channel_bonus - 14 * _damage(text) + _plausibility(text)
                # Incremental context, not frequency of the customer's full name.
                language = context.language_score(proposed, model) - context.language_score(prefix, model)
                expanded.append((proposed, score + local + language, uncertain or unsure))
        expanded.sort(key=lambda item: (-item[1], item[0]))
        beam = expanded[:32]
    return [(_scanner_typography(text, key), 99 + score / max(count, 1), uncertain)
            for text, score, uncertain in beam[:8]]


def _field_candidates(value, key, profiles, model=None):
    model = model or {}
    entries = {}
    protected_numbers = re.findall(r"(?<!\w)(?:[0-9]{1,4}[A-Z]|(?:B|[A-Z]{2,3})[0-9]{1,4})(?!\w)", value) if key == "dia_chi" else []

    def add(text, method, weight, uncertain=False, reasons=()):
        if any(token not in text for token in protected_numbers):
            return
        text = _clean_text(text, key)
        if key == "gioi_tinh":
            text = {"nam": "1", "nữ": "0", "nu": "0"}.get(text.lower(), text)
        score = weight - _damage(text) * 14 + _plausibility(text)
        item = {"value": text, "score": score, "methods": [method],
                "uncertain": uncertain or bool(_damage(text)), "reasons": list(reasons)}
        old = entries.get(text)
        if old is None or item["score"] > old["score"]:
            entries[text] = item

    add(value, "identity", 85 if profiles == ["unicode"] else 25)
    repaired = _repair_segments(value)
    if repaired != value:
        add(repaired, "strict_utf8_segments", 95)
    # Existing byte-number / HTML / percent logic remains shared with vn_text.
    safe = repaired
    for helper in (VN._sua_thuc_the, VN._sua_percent, VN._sua_byte_so):
        safe = helper(safe, [])
    safe = _decimal_field(safe, key)
    if safe != repaired:
        add(safe, "numeric_or_escape", 94)
    # Restore an orphan lead only as a suggestion: assumptions about lost bytes
    # must not be promoted to a verified decode simply because text looks valid.
    orphan = VN._sua_byte_dau_mo_coi(safe, [])
    orphan = VN.sua_byte_mat(orphan, [])
    if orphan != safe:
        add(orphan, "missing_byte_context", 82, True, ["Có byte đã mất; cần đối chiếu ký tự được dựng lại."])
    if "oem" in profiles:
        # OEM first, then decimal: never decode a newly recovered Unicode
        # character a second time as if it were another OEM byte.
        variants, ambiguous = _oem_candidates(value)
        for variant in variants:
            variant = _decimal_field(variant, key)
            add(variant, "oem_low_byte", 90, ambiguous,
                ["Ký hiệu OEM có nhiều cách khôi phục."] if ambiguous else [])
    # Preserve previously observed partial repairs, but mark them as suggestions.
    legacy, warnings = chuan_hoa(value)
    if warnings:
        add(legacy, "legacy_context", 105 if "Trưíng" in value else 72, True, warnings)
    if key == "gioi_tinh" and value in ("N", "N∩", "N7919"):
        add("0", "gender_field_profile", 90, value == "N",
            ["Giới tính bị thiếu ký tự; đối chiếu với thẻ."] if value == "N" else [])
    if key != "gioi_tinh" and profiles != ["unicode"]:
        for text, score, uncertain in _lattice_candidates(value, key, profiles, model):
            add(text, "token_lattice_context", score, uncertain,
                ["Chọn ứng viên theo mã ký tự và ngữ cảnh từ/cụm từ đã được duyệt."])
    ranked = sorted(entries.values(), key=lambda c: (-c["score"], c["value"]))
    return ranked[:16]


def _decimal_field(value, key):
    if key != "dia_chi":
        return VN._sua_ma_unicode(value, [])

    def token(match):
        word = match.group()
        # House/block numbers must not become Unicode merely because the digits
        # coincide with a codepoint (272A, B272, KP7889).
        if re.fullmatch(r"(?:[0-9]{1,4}[A-Z]|(?:B|[A-Z]{2,3})[0-9]{1,4})", word):
            return word
        return VN._sua_ma_unicode(word, [])

    return re.sub(r"[^\s,/]+", token, value)


def _decode_field(value, key, profiles, model, rules):
    candidates = _field_candidates(value, key, profiles, model)
    if key not in ("ho_ten", "dia_chi"):
        return candidates, []
    masked, replacements, used = learning.mask_fragments(value, key, rules)
    if used:
        for candidate in _field_candidates(masked, key, profiles, model):
            restored = learning.restore_fragments(candidate["value"], replacements)
            candidates.append({**candidate, "value": restored,
                               "score": 300 + candidate["score"] - 14 * _damage(restored),
                               "methods": ["user_taught_phrase"], "uncertain": bool(_damage(restored))})
    candidates.sort(key=lambda c: (-c["score"], c["value"]))
    return candidates, used


def analyze_text(raw, scope="auto", *, learned_rules=None):
    """Teaching-tool preview, using the same engine as customer scans."""
    if re.match(r"^[0-9]{12}", raw) or scope == "qr":
        result = normalize_cccd_group([raw], learned_rules=learned_rules)
        return {"output": result.get("normalized_qr", ""), "warnings": result["loi"] + result["canh_bao"]}
    if learned_rules is None:
        learned_rules = learning.read_store()["rules"]
    key = "ho_ten" if scope == "ho_ten" else "dia_chi"
    candidates, used = _decode_field(learning._framing_text(raw), key, detect_profiles(raw), context.load_model(), learned_rules)
    if scope in ("auto", "text") and not used:
        name_candidates, name_used = _decode_field(learning._framing_text(raw), "ho_ten", detect_profiles(raw), context.load_model(), learned_rules)
        if name_used:
            candidates, used = name_candidates, name_used
    return {"output": candidates[0]["value"], "warnings": ["Đã dùng mẫu được dạy."] if used else []}


def _scan(raw, model=None, use_approved=True, learned_rules=()):
    model = model or {}
    parts, extras, errors = split_raw(raw)
    if errors:
        return None, " ".join(errors)
    cid, old, name, birth, gender, address, issued = parts
    for value in (name, gender, address):
        decoded_escape = VN._sua_percent(VN._sua_thuc_the(value, []), [])
        if re.search(r"[\x00-\x1f\x7f<>|]", decoded_escape):
            return None, "Nội dung QR có ký tự điều khiển hoặc ký tự không hợp lệ trong trường."
    profiles = detect_profiles(name + " " + gender + " " + address)
    birth_iso, issued_iso = _ngay(birth), _ngay(issued)
    if (birth and not birth_iso) or (issued and not issued_iso):
        return None, "Ngày sinh/ngày cấp trong QR không hợp lệ."
    if birth_iso and issued_iso and issued_iso < birth_iso:
        return None, "Ngày cấp không được trước ngày sinh."
    if not name.strip():
        return None, "QR thiếu họ tên khách."
    fields, learned_ids = {}, []
    for key, value in zip(_FIELD_KEYS, (name, gender, address)):
        fields[key], used = _decode_field(value, key, profiles, model, learned_rules)
        learned_ids.extend(used)
    approved = model.get("approved", {}).get(context.fingerprint(parts)) if use_approved else None
    if approved:
        for key in _FIELD_KEYS:
            fields[key].insert(0, {"value": approved["fields"][key], "score": 200,
                                  "methods": ["approved_column_d"], "uncertain": False, "reasons": []})
    whole = next((r for r in learned_rules if r["active"] and r["scope"] == "qr"
                  and r.get("fingerprint") == context.fingerprint(parts)), None)
    if whole:
        learned_ids.append(whole["id"])
        for key in _FIELD_KEYS:
            fields[key].insert(0, {"value": whole["fields"][key], "score": 600,
                                  "methods": ["user_taught_qr"], "uncertain": False, "reasons": []})
    strict = _STRUCTURE.fullmatch(raw.strip(" \r\n\t").replace("á»|", "á»\x81"))
    framing_changed = bool(extras) or not strict or "|" in strict[3] or "|" in strict[6]
    return {"profiles": profiles, "fields": fields, "extras": extras, "learned_ids": list(dict.fromkeys(learned_ids)),
            "framing_changed": framing_changed, "approved_row": approved.get("row") if approved else None, "fixed": {
        "cmnd": cid, "cmnd_cu": old, "ngay_sinh": birth_iso, "ngay_cap": issued_iso},
        "dates": (birth, issued)}, None


def normalize_cccd_group(scans, trusted_context=None, *, context_model=None, use_approved=True, learned_rules=None):
    """Canonical ranked QR pipeline (pure data, no network/database/writes).

    Up to five distinct scans of ONE card. Confidence is a ranking score, not a
    calibrated probability. The UI must explicitly confirm decoded suggestions.
    """
    result = {"data": None, "loi": [], "canh_bao": [], "thieu": [], "profiles": [],
              "suggestions": {}, "field_provenance": {}, "raw_scans": [],
              "status": "MANUAL_REVIEW", "confidence": 0, "version": "qr-lattice-2",
              "approved_examples": [], "extra_fields": [], "learned_examples": []}
    if not isinstance(scans, (list, tuple)) or not 1 <= len(scans) <= 5:
        result["loi"].append("Cần từ 1 đến 5 lượt quét cho cùng một thẻ.")
        return result
    if any(not isinstance(s, str) or not s or len(s) > 8192 for s in scans):
        result["loi"].append("Mỗi lượt quét cần là chuỗi có tối đa 8192 ký tự.")
        return result
    result["raw_scans"] = list(dict.fromkeys(scans))
    model = context.load_model() if context_model is None else context_model
    learning_error = ""
    if learned_rules is None:
        try:
            learned_rules = learning.read_store()["rules"]
        except learning.LearningError as exc:
            learned_rules, learning_error = [], str(exc)
    parsed = []
    for index, raw in enumerate(result["raw_scans"]):
        scan, error = _scan(raw, model, use_approved, learned_rules)
        if error:
            result["loi"].append(f"Lượt {index + 1}: {error}")
        else:
            scan["source"] = index + 1
            parsed.append(scan)
            result["profiles"].append({"scan": index + 1, "types": scan["profiles"],
                                       "labels": [PROFILE_LABELS[p] for p in scan["profiles"]]})
            if scan["approved_row"]:
                result["approved_examples"].append({"scan": index + 1, "row": scan["approved_row"]})
            if scan["extras"]:
                result["extra_fields"].append({"scan": index + 1, "values": scan["extras"]})
            if scan["learned_ids"]:
                result["learned_examples"].append({"scan": index + 1, "rules": scan["learned_ids"]})
    if result["loi"]:
        return result
    for key in ("cmnd", "cmnd_cu", "ngay_sinh", "ngay_cap"):
        if len({s["fixed"][key] for s in parsed}) > 1:
            result["loi"].append("Các lượt quét mâu thuẫn số CCCD/CMND hoặc ngày; không hợp nhất.")
            return result
    data = dict(parsed[0]["fixed"])
    warnings, review = [], False
    if learning_error:
        warnings.append(learning_error)
        review = True
    if result["learned_examples"]:
        warnings.append("Đã áp dụng mẫu RAW → kết quả đúng được dạy trong công cụ QR.")
    if model.get("load_error"):
        warnings.append("Không đọc được bộ ngữ cảnh đã duyệt; đang chỉ dùng bộ giải mã cơ bản.")
        review = True
    if any(s["framing_changed"] for s in parsed):
        warnings.append("Đã xác định trường theo số CCCD và mốc ngày do dấu phân cách thay đổi; cần đối chiếu.")
        review = True
    if result["extra_fields"]:
        warnings.append("QR có trường mở rộng sau ngày cấp; giữ trong RAW, không tự điền vào thông tin khách.")
    if result["approved_examples"]:
        rows = sorted({item["row"] for item in result["approved_examples"]})
        warnings.append("Khớp mẫu RAW có đáp án cột D đã được duyệt (dòng " + ", ".join(map(str, rows)) + ").")
    for key in _FIELD_KEYS:
        merged = {}
        for scan in parsed:
            for candidate in scan["fields"][key]:
                value = candidate["value"]
                signature = value.casefold()
                if signature not in merged:
                    merged[signature] = {**candidate, "sources": [], "support": []}
                item = merged[signature]
                if scan["source"] not in item["sources"]:
                    item["sources"].append(scan["source"])
                item["support"].append(candidate)
        ranked = []
        for item in merged.values():
            support = item.pop("support")
            strongest = max(support, key=lambda c: c["score"])
            # Two scans count as evidence only once per raw source, not per
            # transformation. A decoder producing alternatives is not a vote.
            item.update(value=strongest["value"], score=strongest["score"] + 22 * (len(item["sources"]) - 1),
                        uncertain=all(c["uncertain"] for c in support),
                        methods=sorted({m for c in support for m in c["methods"]}))
            ranked.append(item)
        ranked.sort(key=lambda c: (-c["score"], c["value"]))
        best = ranked[0]
        near = [c for c in ranked[1:] if c["score"] >= best["score"] - 12 and c["value"].casefold() != best["value"].casefold()]
        ambiguous = best["uncertain"] or bool(near)
        data[key] = best["value"]
        if key == "gioi_tinh" and data[key] not in ("0", "1"):
            data[key] = ""
        if re.search(r"[\x00-\x1f\x7f<>|]", data[key]):
            result["loi"].append("Nội dung sau giải mã còn ký tự không hợp lệ; cần quét lại.")
        if ambiguous:
            review = True
            warnings.append({"ho_ten": "Họ tên", "dia_chi": "Địa chỉ", "gioi_tinh": "Giới tính"}[key] + ": cần đối chiếu; còn cách hiểu khác hoặc thiếu byte.")
        display_candidates = [best] + [c for c in ranked[1:]
                                      if not _damage(c["value"]) and c["score"] >= min(best["score"], 110) - 24]
        result["suggestions"][key] = [{"value": c["value"], "score": round(max(0, min(100, c["score"]))),
                                         "sources": c["sources"], "methods": c["methods"],
                                         "uncertain": c["uncertain"] or bool(near)} for c in display_candidates[:4]]
        result["field_provenance"][key] = {"scans": best["sources"], "methods": best["methods"]}
    if result["loi"]:
        return result
    if trusted_context:
        # Reference data are not automatically authoritative, even with same ID.
        if trusted_context.get("cmnd") != data["cmnd"]:
            warnings.append("Nguồn tham chiếu khác số CCCD; không sử dụng.")
        elif any(trusted_context.get(k) and trusted_context[k] != data[k] for k in data):
            warnings.append("Nguồn tham chiếu khác nội dung quét; cần đối chiếu thẻ gốc.")
        review = True
    for label, key in (("Ngày sinh", "ngay_sinh"), ("Ngày cấp", "ngay_cap"),
                       ("Địa chỉ", "dia_chi"), ("Giới tính", "gioi_tinh")):
        if not data[key]:
            result["thieu"].append(label)
    if result["thieu"]:
        warnings.append("Chưa xác định: " + ", ".join(result["thieu"]) + ".")
        review = True
    changed = any(s["profiles"] != ["unicode"] for s in parsed) or bool(result["learned_examples"])
    if changed:
        warnings.append("Đã phục hồi dữ liệu quét. Gợi ý đứng đầu có bằng chứng mạnh nhất, không phải bảo đảm đúng với thẻ.")
    if changed and len(parsed) == 1:
        warnings.append("Chỉ có một lượt quét: nên đối chiếu với thẻ hoặc quét thêm bằng máy khác.")
    result["canh_bao"] = list(dict.fromkeys(warnings))
    data["canh_bao"] = result["canh_bao"]
    result["data"] = data
    result["status"] = "MANUAL_REVIEW" if review else "ACCEPT_WITH_WARNING" if changed else "AUTO_ACCEPT"
    result["confidence"] = 0.6 if review else 0.85 if changed else 0.95
    result["normalized_qr"] = "|".join((data["cmnd"], data["cmnd_cu"], data["ho_ten"],
        parsed[0]["dates"][0], {"0": "Nữ", "1": "Nam"}.get(data["gioi_tinh"], ""),
        data["dia_chi"], parsed[0]["dates"][1]))
    return result


def phan_tich(raw):
    """Backwards-compatible field keys, now with profile routing and suggestions."""
    return normalize_cccd_group([str(raw or "")])
