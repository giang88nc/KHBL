"""Locally approved QR examples. No automatic learning from customer scans.

The portable model contains a word/phrase lexicon and SHA-256 keyed, explicitly
approved examples. It never matches a customer by name or ID alone. Request-time
access is read-only; the skill CLI builds the model after approval of column D.
"""
from collections import Counter
from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path
import re
import unicodedata

DEFAULT_PATH = Path(__file__).resolve().parents[2] / "var/private/qr/approved_context.json"
MODEL_VERSION = 1
TEXT_KEYS = ("ho_ten", "gioi_tinh", "dia_chi")


def fingerprint(parts):
    # Only framing whitespace is insignificant. All fields (including identity,
    # dates and damaged characters) participate; changed content cannot match.
    payload = json.dumps([p.strip(" \r\n\t") for p in parts], ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def words(text):
    return re.findall(r"[^\W\d_]+", unicodedata.normalize("NFC", text).casefold())


@lru_cache(maxsize=4)
def _read(path, mtime, size):
    with open(path, encoding="utf-8") as stream:
        model = json.load(stream)
    if not isinstance(model, dict) or model.get("version") != MODEL_VERSION:
        raise ValueError("Unsupported QR context version")
    for key in ("lexicon", "bigrams", "trigrams", "approved"):
        if not isinstance(model.get(key), dict):
            raise ValueError("Invalid QR context structure")
    for sample in model["approved"].values():
        if (not isinstance(sample, dict) or not isinstance(sample.get("row"), int)
                or not isinstance(sample.get("fields"), dict)
                or any(not isinstance(sample["fields"].get(k), str) for k in TEXT_KEYS)):
            raise ValueError("Invalid approved QR example")
    return model


def load_model():
    path = Path(os.environ.get("KHBL_QR_CONTEXT_PATH", DEFAULT_PATH))
    try:
        stat = path.stat()
        return _read(str(path), stat.st_mtime_ns, stat.st_size)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError, TypeError):
        return {"load_error": True}


def build_model(records, source_digest):
    """records: validated raw parts and literal gold parts, supplied by the CLI."""
    lexicon, pairs, triples = Counter(), Counter(), Counter()
    approved, seen_gold = {}, set()
    for row, raw_parts, gold in records:
        if tuple(gold) not in seen_gold:
            seen_gold.add(tuple(gold))
            for index in (2, 5):
                tokens = words(gold[index])
                lexicon.update(tokens)
                pairs.update(" ".join(tokens[i:i + 2]) for i in range(len(tokens) - 1))
                triples.update(" ".join(tokens[i:i + 3]) for i in range(len(tokens) - 2))
        key = fingerprint(raw_parts)
        value = {"ho_ten": gold[2], "gioi_tinh": {"Nam": "1", "Nữ": "0"}.get(gold[4], ""),
                 "dia_chi": gold[5]}
        if key in approved and approved[key]["fields"] != value:
            raise ValueError(f"Column D conflicts for the same RAW at row {row}")
        approved[key] = {"fields": value, "row": row}
    return {"version": MODEL_VERSION, "source_sha256": source_digest,
            "gold_rows": len(seen_gold), "lexicon": dict(lexicon),
            "bigrams": dict(pairs), "trigrams": dict(triples), "approved": approved}


def language_score(text, model):
    tokens = words(text)
    return (sum(5 for word in tokens if word in model.get("lexicon", {}))
            + sum(5 for i in range(len(tokens) - 1)
                  if " ".join(tokens[i:i + 2]) in model.get("bigrams", {}))
            + sum(6 for i in range(len(tokens) - 2)
                  if " ".join(tokens[i:i + 3]) in model.get("trigrams", {})))
