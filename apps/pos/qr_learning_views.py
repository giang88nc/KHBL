"""Authenticated local tool for explicitly teaching QR corrections. No PMV calls.

Quyền CRUD của công cụ này KẾ THỪA danh mục KHÁCH HÀNG (GĐ chốt 11/09/2026): trang nằm trong
``/banle/khach-hang/…`` và chỉ phục vụ việc nhập khách, nên XEM đi theo quyền Xem của Khách hàng,
THÊM/SỬA mẫu đi theo quyền Tạo/sửa của Khách hàng. Luật nằm ở ``customer.quyen`` (ma trận
``UserModuleAccess`` module ``KHACH_HANG``) — đừng đặt luật riêng ở đây.
"""
import json
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET, require_POST
from . import cccd, cccd_learning as learning, cccd_tools, customer


XEM_CAN_QUYEN = "Bạn chưa được cấp quyền xem danh mục Khách hàng."


def _json(data, status=200):
    response = JsonResponse(data, status=status)
    response["Cache-Control"] = "private, no-store"
    return response


def _public(state):
    return {"revision": state["revision"], "rules": [{k: rule.get(k) for k in
            ("id", "raw", "output", "scope", "active", "updated_at", "updated_by")} for rule in state["rules"]],
            "drafts": [{k: draft.get(k) for k in
            ("id", "raw", "scope", "created_at", "created_by")} for draft in state["drafts"]]}


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
    if not customer.duoc_xem(request.user):
        raise PermissionDenied("Bạn chưa được cấp quyền xem danh mục Khách hàng.")
    error = ""
    try:
        state = learning.read_store()
    except learning.LearningError as exc:
        state, error = learning.empty_store(), str(exc)
    if request.GET.get("format") == "json":
        return _json({**_public(state), "error": error}, 503 if error else 200)
    response = render(request, "pos/qr_learning.html", {
        "nav_active": "khach", "learning_state": _public(state), "learning_error": error,
        "can_teach": customer.duoc_sua(request.user), "scopes": learning.SCOPES,
    })
    response["Cache-Control"] = "private, no-store"
    return response


@login_required
@require_POST
def translate(request):
    if not customer.duoc_xem(request.user):
        return _json({"error": XEM_CAN_QUYEN}, 403)
    try:
        data = _payload(request)
        return _json(cccd_tools.translate(data.get("raw"), data.get("scope", "auto")))
    except learning.LearningError as exc:
        return _json({"error": str(exc)}, 400)


@login_required
@require_POST
def filter_errors(request):
    if not customer.duoc_xem(request.user):
        return _json({"error": XEM_CAN_QUYEN}, 403)
    try:
        data = _payload(request)
        return _json(cccd_tools.filter_errors(data.get("raw"), data.get("output"), data.get("scope", "auto")))
    except learning.LearningError as exc:
        return _json({"error": str(exc)}, 400)


@login_required
@require_POST
def save_draft(request):
    if not customer.duoc_sua(request.user):
        return _json({"error": "Bạn không có quyền cập nhật danh sách CHỜ (theo quyền danh mục Khách hàng)."}, 403)
    try:
        data = _payload(request)
        state, message, draft_id = learning.save_draft(
            data.get("raw"), data.get("scope", "auto"), revision=data.get("revision"),
            actor=request.user.get_username())
        return _json({**_public(state), "message": message, "draft_id": draft_id})
    except learning.LearningConflict as exc:
        return _json({"error": str(exc)}, 409)
    except learning.LearningError as exc:
        return _json({"error": str(exc)}, 400)


@login_required
@require_POST
def preview(request):
    if not customer.duoc_xem(request.user):
        return _json({"error": XEM_CAN_QUYEN}, 403)
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
    if not customer.duoc_sua(request.user):
        return _json({"error": "Bạn không có quyền cập nhật bộ mẫu dùng chung (theo quyền danh mục Khách hàng)."}, 403)
    try:
        data = _payload(request)
        state, message = learning.save_rule(data.get("raw", ""), data.get("output", ""), data.get("scope", "auto"),
                    revision=data.get("revision"), actor=request.user.get_username(),
                    edit_id=data.get("edit_id", ""), active=data.get("active"),
                    draft_id=data.get("draft_id", ""))
        applied_id = data.get("edit_id") if data.get("active") is not None else learning.prepare_rule(
            data.get("raw", ""), data.get("output", ""), data.get("scope", "auto"))["id"]
        return _json({**_public(state), "message": message, "applied_id": applied_id})
    except learning.LearningConflict as exc:
        return _json({"error": str(exc)}, 409)
    except learning.LearningError as exc:
        return _json({"error": str(exc)}, 400)
