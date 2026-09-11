"""Read-only translation and source-aligned filtering for the QR teaching form."""
from difflib import SequenceMatcher
from collections import Counter
import re
import unicodedata

from . import cccd, cccd_context, cccd_learning as learning

_TOKEN = re.compile(r"[^\s,./|]+")
_IN_WORD_SYMBOL = re.compile(r"[^\W\d_][()\[\]@#~=+_]+[^\W\d_]", re.UNICODE)


def _input(value, label):
    if not isinstance(value, str) or not value.strip() or len(value) > 8192:
        raise learning.LearningError(f"{label} cần có từ 1 đến 8192 ký tự.")
    return value


def _scope(scope):
    if not isinstance(scope, str) or scope not in ("auto", *learning.SCOPES):
        raise learning.LearningError("Phạm vi áp dụng không hợp lệ.")
    return scope


def translate(raw, scope="auto"):
    raw, scope = _input(raw, "RAW"), _scope(scope)
    result = cccd.analyze_text(raw.strip(), scope)
    if not result["output"]:
        raise learning.LearningError(" ".join(result["warnings"]) or "Chưa dịch được RAW này.")
    return result


def _tokens(text):
    # This observed fake delimiter is one byte of a Vietnamese character.
    # Replacing one code point keeps every source offset valid.
    return list(_TOKEN.finditer(text.replace("á»|", "á»\x81")))


def has_font_errors(word):
    text = unicodedata.normalize("NFC", word)
    return bool(cccd._damage(text) or _IN_WORD_SYMBOL.search(text)
                or any(not c.isascii() and unicodedata.category(c)[0] in "SC" for c in text)
                or any(part in text.lower() for part in ("ưạ", "ưí", "ưă", "ạngh")))


def _signature(text):
    return unicodedata.normalize("NFC", text).casefold()


def filter_errors(raw, output, scope="auto"):
    """Keep damaged output words and their original, contiguous RAW spans.

    Align decoded token hints, not token indices: a single scanner token can
    expand into several words. Unmatched blocks stay whole between shared
    anchors; an unanchored region falls back to the full pair, never a guess.
    Neither this function nor translate persists training/customer data.
    """
    raw, output, scope = _input(raw, "RAW"), _input(output, "Kết quả"), _scope(scope)
    source, target = _tokens(raw), _tokens(output)
    if max(len(source), len(target)) > 512:
        raise learning.LearningError("Đoạn quá dài để ghép chính xác; hãy lọc từng QR hoặc từng đoạn ngắn.")
    if not source or not target:
        raise learning.LearningError("Cần giữ ít nhất một từ hoặc đoạn chữ ở mỗi ô để đối chiếu.")
    bad = {i for i, token in enumerate(target) if has_font_errors(token[0])}
    note = "Lọc dấu hiệu lỗi mã ký tự; chữ đúng mã nhưng sai tên hoặc địa danh vẫn cần đối chiếu."
    if not bad:
        return {"segments": [], "warnings": [note]}

    raw_is_qr = bool(re.match(r"^[0-9]{12}", raw.strip())) or scope == "qr"
    if raw_is_qr:
        before, _, errors = cccd.split_raw(raw)
        after, _, target_errors = cccd.split_raw(output)
        if errors or target_errors or any(before[i] != after[i] for i in (0, 1, 3, 6)):
            raise learning.LearningError("Hai ô chưa khớp cùng QR hoặc mốc số/ngày. Bấm Dịch lại trước khi lọc.")

    model, rules = cccd_context.load_model(), learning.read_store()["rules"]
    key = "ho_ten" if scope == "ho_ten" else "dia_chi"
    hints, owners = [], []
    for i, token in enumerate(source):
        candidates, _ = cccd._decode_field(token[0], key, cccd.detect_profiles(token[0]), model, rules)
        words = _tokens(candidates[0]["value"])
        # Every expanded word points to the same complete original token.
        for word in words or [token]:
            hints.append(_signature(word[0]))
            owners.append(i)
    actual = [_signature(token[0]) for token in target]
    counts, target_counts = Counter(hints), Counter(actual)
    if any(counts[actual[i]] > 1 and counts[actual[i]] != target_counts[actual[i]] for i in bad):
        return {"segments": [{"raw": raw, "output": output, "scope": "qr" if raw_is_qr else scope if scope in ("ho_ten", "dia_chi") else "text",
                               "raw_start": 0, "raw_end": len(raw), "output_start": 0, "output_end": len(output), "alignment": "context"}],
                "warnings": ["Từ bị lỗi lặp nhiều lần và số lần hai bên khác nhau; giữ cả đoạn để tránh ghép nhầm.", note]}
    blocks = SequenceMatcher(None, hints, actual, autojunk=False).get_opcodes()
    regions = []
    for kind, a, b, c, d in blocks:
        indices = sorted(bad.intersection(range(c, d)))
        if not indices:
            continue
        if kind == "equal":
            regions.extend([owners[a + j - c], owners[a + j - c] + 1, j, j + 1, False] for j in indices)
        elif a < b:
            regions.append([owners[a], owners[b - 1] + 1, c, d, True])
        else:
            # Inserted output has no proven source token. Keep surrounding
            # context, or the full text at an edge, so no invented pair is taught.
            left = owners[a - 1] if a else 0
            right = owners[a] + 1 if a < len(owners) else len(source)
            regions.append([left, right, max(0, c - 1), min(len(target), d + 1), True])

    # Expand all output words owned by a RAW token (one-to-many), then merge
    # touching regions. Never pair the middle word with only part of its code.
    equal_links = [(owners[a + j - c], j) for kind, a, b, c, d in blocks
                   if kind == "equal" for j in range(c, d)]
    for region in regions:
        linked = [j for owner, j in equal_links if region[0] <= owner < region[1]]
        if linked:
            region[2], region[3] = min(region[2], min(linked)), max(region[3], max(linked) + 1)
    merged = []
    for region in regions:
        if merged and (region[0] <= merged[-1][1] and region[2] <= merged[-1][3]):
            old = merged[-1]
            old[1], old[3], old[4] = max(old[1], region[1]), max(old[3], region[3]), old[4] or region[4]
        else:
            merged.append(region)
    segments = []
    for a, b, c, d, broad in merged:
        if a >= b or c >= d:
            continue
        start, end = source[a].start(), source[b - 1].end()
        out_start, out_end = target[c].start(), target[d - 1].end()
        pair_scope = scope if scope in ("ho_ten", "dia_chi") else "text"
        # A broad region crossing QR fields remains a full QR for safe editing.
        if raw_is_qr and ("|" in raw[start:end].replace("á»|", "á»\x81") or "|" in output[out_start:out_end].replace("á»|", "á»\x81")):
            start, end, out_start, out_end, pair_scope, broad = 0, len(raw), 0, len(output), "qr", True
        pair = {"raw": raw[start:end], "output": output[out_start:out_end], "scope": pair_scope,
                "raw_start": start, "raw_end": end, "output_start": out_start, "output_end": out_end,
                "alignment": "context" if broad else "token"}
        if not any(p["raw_start"] == start and p["raw_end"] == end for p in segments):
            segments.append(pair)
    return {"segments": segments, "warnings": [note]}
