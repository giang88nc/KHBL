"""Explicitly taught QR corrections, stored locally and applied without retraining.

Only the teaching tool writes this file. Normal scans read it. Fragment rules
match complete literal phrases, longest first, in name/address fields only.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import threading
import time
import unicodedata

DEFAULT_PATH = Path(__file__).resolve().parents[2] / "var/private/qr/learned_rules.json"
SCOPES = {"text": "Họ tên và địa chỉ", "ho_ten": "Họ tên", "dia_chi": "Địa chỉ", "qr": "Toàn bộ QR"}
_LOCK = threading.RLock()


class LearningError(ValueError):
    pass


class LearningConflict(LearningError):
    pass


def store_path():
    return Path(os.environ.get("KHBL_QR_LEARNING_PATH", DEFAULT_PATH))


def empty_store():
    return {"version": 1, "revision": 0, "rules": [], "drafts": [], "history": []}


def read_store():
    try:
        value = json.loads(store_path().read_text(encoding="utf-8"))
    except FileNotFoundError:
        return empty_store()
    except (OSError, ValueError) as exc:
        raise LearningError("Không đọc được bộ mẫu đã học. Kiểm tra file hoặc quyền truy cập thư mục QR.") from exc
    if (not isinstance(value, dict) or value.get("version") != 1
            or type(value.get("revision")) is not int or not isinstance(value.get("rules"), list)
            or not isinstance(value.get("history"), list)):
        raise LearningError("Bộ mẫu đã học sai định dạng; chưa thực hiện thay đổi.")
    # Version 1 stores created before the waiting list remain valid.
    value.setdefault("drafts", [])
    if not isinstance(value["drafts"], list):
        raise LearningError("Danh sách mẫu chờ sai định dạng; chưa thực hiện thay đổi.")
    for rule in value["rules"]:
        if (not isinstance(rule, dict) or rule.get("scope") not in SCOPES
                or any(not isinstance(rule.get(k), str) for k in ("id", "raw", "output"))
                or type(rule.get("active")) is not bool):
            raise LearningError("Có mẫu học sai định dạng; cần khôi phục file mẫu.")
    for draft in value["drafts"]:
        if (not isinstance(draft, dict) or draft.get("scope") not in SCOPES
                or any(not isinstance(draft.get(k), str) for k in
                       ("id", "raw", "created_at", "created_by"))):
            raise LearningError("Có mẫu chờ sai định dạng; cần khôi phục file mẫu.")
    return value


def _framing_text(text):
    return unicodedata.normalize("NFC", text).strip(" \r\n\t").replace("á»|", "á»\x81")


def prepare_rule(raw, output, scope="auto"):
    from . import cccd
    from .cccd_tools import has_font_errors
    from .cccd_context import fingerprint
    if not isinstance(raw, str) or not isinstance(output, str):
        raise LearningError("Nhập RAW và kết quả đúng dưới dạng chữ.")
    raw, output = _framing_text(raw), unicodedata.normalize("NFC", output).strip(" \r\n\t")
    if not raw or not output or max(len(raw), len(output)) > 8192:
        raise LearningError("Cần nhập đủ RAW và kết quả đúng, tối đa 8192 ký tự mỗi ô.")
    if not isinstance(scope, str):
        raise LearningError("Phạm vi áp dụng không hợp lệ.")
    if scope == "auto":
        scope = "qr" if re.match(r"^[0-9]{12}", raw) else "text"
    if scope not in SCOPES:
        raise LearningError("Phạm vi áp dụng không hợp lệ.")
    if re.search(r"[\x00-\x1f\x7f<>]", raw + output):
        raise LearningError("Mẫu không được chứa HTML hoặc ký tự điều khiển.")
    rule = {"scope": scope, "raw": raw, "output": output, "active": True}
    if scope == "qr":
        source, _, errors = cccd.split_raw(raw)
        target, extras, target_errors = cccd.split_raw(output)
        if errors or target_errors or extras:
            raise LearningError("Chưa xác định được đầy đủ QR nguồn/kết quả. Kết quả cần đúng các trường nghiệp vụ.")
        if any(source[i] != target[i] for i in (0, 1, 3, 6)):
            raise LearningError("Mẫu học không được đổi số CCCD/CMND, ngày sinh hoặc ngày cấp.")
        checked = cccd.normalize_cccd_group([output], context_model={}, learned_rules=[])
        if (not checked["data"] or checked["thieu"] or has_font_errors(target[2] + " " + target[5])
                or target[4] not in ("Nam", "Nữ")):
            raise LearningError("Kết quả QR phải có họ tên, ngày, giới tính, địa chỉ hợp lệ và không còn lỗi mã ký tự.")
        rule["fingerprint"] = fingerprint(source)
        rule["fields"] = {"ho_ten": target[2], "gioi_tinh": {"Nam": "1", "Nữ": "0"}[target[4]], "dia_chi": target[5]}
        rule["output"] = "|".join(target)
        key = rule["fingerprint"]
    else:
        if max(len(raw), len(output)) > 500 or min(len(raw), len(output)) < 3:
            raise LearningError("Mẫu cụm chữ cần từ 3 đến 500 ký tự; chọn nguyên từ/cụm để tránh sửa nhầm.")
        if "|" in raw + output or not any(c.isalpha() for c in raw) or not any(c.isalpha() for c in output):
            raise LearningError("Mẫu cụm chữ chỉ áp cho họ tên/địa chỉ, không dùng để thay số hoặc phân cách QR.")
        decoded = cccd.VN._sua_percent(cccd.VN._sua_thuc_the(output, []), [])
        if re.search(r"[\x00-\x1f\x7f<>|]", decoded) or has_font_errors(output):
            raise LearningError("Kết quả đúng còn dấu hiệu lỗi mã ký tự; hãy kiểm tra lại trước khi dạy.")
        key = raw
    rule["id"] = hashlib.sha256((scope + "\0" + key).encode("utf-8")).hexdigest()[:24]
    return rule


def prepare_draft(raw, scope="auto"):
    if not isinstance(raw, str):
        raise LearningError("Nhập RAW từ máy quét dưới dạng chữ.")
    raw = _framing_text(raw)
    if not raw or len(raw) > 8192:
        raise LearningError("RAW cần có từ 1 đến 8192 ký tự.")
    if not isinstance(scope, str):
        raise LearningError("Phạm vi áp dụng không hợp lệ.")
    if scope == "auto":
        scope = "qr" if re.match(r"^[0-9]{12}", raw) else "text"
    if scope not in SCOPES:
        raise LearningError("Phạm vi áp dụng không hợp lệ.")
    if re.search(r"[\x00-\x1f\x7f<>]", raw):
        raise LearningError("RAW không được chứa HTML hoặc ký tự điều khiển.")
    return {"id": hashlib.sha256(("draft\0" + scope + "\0" + raw).encode("utf-8")).hexdigest()[:24],
            "raw": raw, "scope": scope}


@contextmanager
def _file_lock(path):
    """Serialize across Waitress threads and simultaneous local worker processes."""
    with _LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.with_suffix(".lock").open("a+b") as handle:
            if handle.seek(0, 2) == 0:
                handle.write(b"0")
                handle.flush()
            deadline = time.monotonic() + 5
            while True:
                try:
                    handle.seek(0)
                    if os.name == "nt":
                        import msvcrt
                        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise LearningConflict("Bộ mẫu đang được cập nhật ở cửa sổ khác. Hãy thử lại.")
                    time.sleep(0.05)
            try:
                yield
            finally:
                handle.seek(0)
                if os.name == "nt":
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _write_state(path, state):
    fd, temporary = tempfile.mkstemp(prefix="qr-learn-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(state, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def save_draft(raw, scope="auto", *, revision, actor):
    if type(revision) is not int:
        raise LearningError("Thiếu phiên bản bộ mẫu; tải lại công cụ rồi thử lại.")
    proposed = prepare_draft(raw, scope)
    path = store_path()
    try:
        with _file_lock(path):
            state = read_store()
            old = next((item for item in state["drafts"] if item["id"] == proposed["id"]), None)
            if old:
                return state, "RAW này đã nằm trong danh sách CHỜ; không tạo bản trùng.", old["id"]
            if any(rule["raw"] == proposed["raw"] and rule["scope"] == proposed["scope"]
                   for rule in state["rules"]):
                return state, "RAW này đã có trong danh sách ĐÃ HỌC.", ""
            if revision != state["revision"]:
                raise LearningConflict("Danh sách vừa thay đổi ở cửa sổ khác. Tải lại rồi lưu nháp lại.")
            now = datetime.now(timezone.utc).isoformat()
            proposed.update(created_at=now, created_by=str(actor)[:150])
            state["drafts"].append(proposed)
            state["revision"] += 1
            state["history"].append({"revision": state["revision"], "at": now,
                                     "actor": proposed["created_by"], "action": "draft_created",
                                     "draft": proposed})
            state["history"] = state["history"][-1000:]
            _write_state(path, state)
            return state, "Đã lưu RAW vào danh sách CHỜ xử lý.", proposed["id"]
    except OSError as exc:
        raise LearningError("Chưa lưu được nháp: kiểm tra dung lượng ổ đĩa và quyền ghi thư mục var/private/qr.") from exc


def save_rule(raw="", output="", scope="auto", *, revision, actor, edit_id="", active=None,
              draft_id=""):
    if not isinstance(edit_id, str) or not isinstance(draft_id, str):
        raise LearningError("Mã mẫu cần sửa không hợp lệ.")
    if type(revision) is not int:
        raise LearningError("Thiếu phiên bản bộ mẫu; tải lại công cụ rồi thử lại.")
    proposed = prepare_rule(raw, output, scope) if active is None else None
    if active is not None and (type(active) is not bool or not edit_id):
        raise LearningError("Yêu cầu bật/tắt mẫu không hợp lệ.")
    path = store_path()
    try:
        with _file_lock(path):
            state = read_store()
            old = next((r for r in state["rules"] if r["id"] == (edit_id or proposed["id"])), None)
            draft = next((item for item in state["drafts"] if item["id"] == draft_id), None) if draft_id else None
            draft_matches = bool(draft and proposed and draft["raw"] == proposed["raw"])
            # Safe retry after a lost response/double click must not create a duplicate.
            same_rule = bool(old and proposed and all(old[k] == proposed[k] for k in ("raw", "output", "scope")) and old["active"])
            if same_rule and not draft_matches:
                return state, "Mẫu này đã được học; không tạo bản trùng."
            if old and active is not None and old["active"] == active:
                return state, "Trạng thái mẫu đã được cập nhật."
            if revision != state["revision"]:
                raise LearningConflict("Bộ mẫu vừa thay đổi ở cửa sổ khác. Tải lại danh sách rồi áp dụng lại.")
            if draft_id and not draft and not same_rule:
                raise LearningConflict("Mẫu CHỜ không còn tồn tại. Tải lại danh sách trước khi áp dụng.")
            if edit_id and not old:
                raise LearningConflict("Mẫu cần sửa không còn tồn tại. Tải lại danh sách.")
            if old and not edit_id:
                raise LearningConflict("RAW này đã có kết quả khác. Chọn Sửa ở mẫu đã học để cập nhật có chủ đích.")
            if proposed and any(r["id"] == proposed["id"] and r is not old for r in state["rules"]):
                raise LearningConflict("RAW/phạm vi này đã được lưu trong một mẫu khác.")
            updated = dict(old, active=active) if active is not None else old if same_rule else proposed
            now = datetime.now(timezone.utc).isoformat()
            if not same_rule or active is not None:
                updated = dict(updated)
                updated.update(updated_at=now, updated_by=str(actor)[:150])
                state["rules"] = [updated if r is old else r for r in state["rules"]] if old else state["rules"] + [updated]
            if draft_matches:
                state["drafts"] = [item for item in state["drafts"] if item["id"] != draft_id]
            state["revision"] += 1
            state["history"].append({"revision": state["revision"], "at": now,
                                     "actor": str(actor)[:150], "before": old, "after": updated,
                                     "draft_moved": draft if draft_matches else None})
            state["history"] = state["history"][-1000:]
            _write_state(path, state)
            message = "Đã học mẫu và chuyển RAW từ CHỜ sang ĐÃ HỌC." if draft_matches else \
                "Đã học một phần; RAW đầy đủ vẫn ở danh sách CHỜ." if draft else \
                "Đã học mẫu. Các lượt quét tiếp theo dùng ngay kết quả này."
            return state, message
    except OSError as exc:
        raise LearningError("Chưa lưu được mẫu: kiểm tra dung lượng ổ đĩa và quyền ghi thư mục var/private/qr.") from exc


def mask_fragments(value, key, rules):
    """Literal bounded matches; a single pass prevents cascading replacements."""
    hits = []
    for rule in rules:
        if not rule["active"] or rule["scope"] not in ("text", key):
            continue
        pattern = r"(?<!\w)" + re.escape(rule["raw"]).replace(r"\ ", r"[ \t]+") + r"(?!\w)"
        for match in re.finditer(pattern, value):
            hits.append((match.start(), match.end(), rule))
    hits.sort(key=lambda hit: (hit[0], -(hit[1] - hit[0]), hit[2]["scope"] == "text"))
    end, out, replacements, used = 0, [], {}, []
    marker = "qrliteralx"
    while marker in value:
        marker += "x"
    for start, stop, rule in hits:
        if start < end:
            continue
        placeholder = marker + chr(97 + len(replacements) % 26) + "x" * (1 + len(replacements) // 26)
        out.extend((value[end:start], placeholder))
        replacements[placeholder] = rule["output"]
        used.append(rule["id"])
        end = stop
    out.append(value[end:])
    return "".join(out), replacements, list(dict.fromkeys(used))


def restore_fragments(value, replacements):
    if not replacements:
        return value
    # One pass: approved output is literal and cannot trigger another rule/decoder.
    return re.sub("|".join(re.escape(k) for k in sorted(replacements, key=len, reverse=True)),
                  lambda match: replacements[match[0]], value)
