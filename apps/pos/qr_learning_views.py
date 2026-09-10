"""Authenticated local tool for explicitly teaching QR corrections. No PMV calls."""
import json
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET, require_POST
from . import cccd, cccd_learning as learning


def _json(data, status=200):
    response = JsonResponse(data, status=status)
    response["Cache-Control"] = "private, no-store"
    return response


def _public(state):
    return {"revision": state["revision"], "rules": [{k: rule.get(k) for k in
            ("id", "raw", "output", "scope", "active", "updated_at", "updated_by")} for rule in state["rules"]]}


def _payload(request):
    if len(request.body) > 70_000:
        raise learning.LearningError("Nội dung quá dài.")
    try:
        value = json.loads(request.body)
    except (ValueError, TypeError) as exc:
        raise learning.LearningError("Dữ liệu gửi lên không hợp lệ.") from exc
    if not isinstance(value, dict):
        raise learning.LearningError("Dữ liệu gửi lên không hợp lệ.")
    return value


@login_required
@require_GET
def tool(request):
    error = ""
    try:
        state = learning.read_store()
    except learning.LearningError as exc:
        state, error = learning.empty_store(), str(exc)
    if request.GET.get("format") == "json":
        return _json({**_public(state), "error": error}, 503 if error else 200)
    response = render(request, "pos/qr_learning.html", {
        "nav_active": "khach", "learning_state": _public(state), "learning_error": error,
        "can_teach": request.user.is_superuser, "scopes": learning.SCOPES,
    })
    response["Cache-Control"] = "private, no-store"
    return response


@login_required
@require_POST
def preview(request):
    try:
        data = _payload(request)
        raw, output, scope = data.get("raw"), data.get("output"), data.get("scope", "auto")
        rule = learning.prepare_rule(raw, output, scope)
        state = learning.read_store()
        before = cccd.analyze_text(raw, rule["scope"], learned_rules=state["rules"])
        replacement = [r for r in state["rules"] if r["id"] not in (rule["id"], data.get("edit_id"))] + [rule]
        after = cccd.analyze_text(raw, rule["scope"], learned_rules=replacement)
        return _json({"before": before["output"], "after": after["output"],
                      "warnings": before["warnings"], "scope": learning.SCOPES[rule["scope"]],
                      "revision": state["revision"]})
    except learning.LearningError as exc:
        return _json({"error": str(exc)}, 400)


@login_required
@require_POST
def save(request):
    if not request.user.is_superuser:
        return _json({"error": "Chỉ tài khoản quản trị được cập nhật bộ mẫu dùng chung."}, 403)
    try:
        data = _payload(request)
        state, message = learning.save_rule(data.get("raw", ""), data.get("output", ""), data.get("scope", "auto"),
                    revision=data.get("revision"), actor=request.user.get_username(),
                    edit_id=data.get("edit_id", ""), active=data.get("active"))
        applied_id = data.get("edit_id") if data.get("active") is not None else learning.prepare_rule(
            data.get("raw", ""), data.get("output", ""), data.get("scope", "auto"))["id"]
        return _json({**_public(state), "message": message, "applied_id": applied_id})
    except learning.LearningConflict as exc:
        return _json({"error": str(exc)}, 409)
    except learning.LearningError as exc:
        return _json({"error": str(exc)}, 400)
