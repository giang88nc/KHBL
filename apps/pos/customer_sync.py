"""khj_cd.customer → PMV: chỉ PHONE, chỉ thêm, biên nhận bền vững cho retry.

CCCD chỉ chép vào CMND khi thêm mới, không dùng làm khóa đối chiếu.
Khóa MySQL liên tiến trình + SAVE_LOCK dùng chung form khách.
Kết quả không chắc chắn không tự INSERT lại. Biên nhận có CustID chỉ được hoàn
tất sổ điểm, tuyệt đối không gọi I_CUSTOMER_Upd để ghi đè khách đã có.
"""
import hashlib
import json
import re
import unicodedata
from collections import Counter, defaultdict
from contextlib import contextmanager

from django.db import connection
from django.http import JsonResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST

from apps.pmv import gateway
from apps.pmv.client import PmvClient, PmvProcError
from . import customer as C, quyen as Q
from .customer_sync_models import CustomerSyncReceipt
from . import customer_phones as P

PHONE_SEPARATORS = P.SEPARATORS
ISSUED_BY = "Cục Cảnh Sát QLHC về TTXH"

def phone_key(value):
    return P.phone_key(value)


def phone_expression(column):
    return P.expression(column)


def source_rows(source_id=None):
    sql = "SELECT id, name, phone, addr, cccd FROM khj_cd.customer"
    params = ()
    if source_id is not None:
        sql += " WHERE id=%s"
        params = (source_id,)
    sql += " ORDER BY id"
    with connection.cursor() as cursor:
        cursor.execute(sql, params)
        return [dict(zip(("id", "name", "phone", "addr", "cccd"), row)) for row in cursor.fetchall()]


def fingerprint(row, *, legacy=False):
    fields = ("id", "phone", "name", "addr") if legacy else ("id", "phone", "name", "addr", "cccd")
    raw = json.dumps([row.get(k) for k in fields], ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def receipt_matches_source(receipt, source):
    # Biên nhận trước khi bổ sung CCCD vẫn hợp lệ; không tạo lại khách đã sync.
    return receipt.fingerprint == fingerprint(source, legacy=receipt.payload.get("_sync_schema", 1) < 2)


def prepared(row):
    phone = phone_key(row.get("phone"))
    name = unicodedata.normalize("NFC", str(row.get("name") or "").strip())
    address = unicodedata.normalize("NFC", str(row.get("addr") or "").strip())
    cmnd = re.sub(r"\s", "", str(row.get("cccd") or ""))
    errors = []
    if not P.valid(phone):
        errors.append("Thiếu PHONE hoặc PHONE không gồm đúng 10 chữ số")
    if not name:
        errors.append("Thiếu TÊN khách")
    if cmnd and not re.fullmatch(r"(?:[0-9]{9}|[0-9]{12})", cmnd):
        errors.append("CCCD/CMND nguồn phải gồm 9 hoặc 12 chữ số; cần sửa trước khi thêm PMV")
    for label, value in (("Tên", name), ("Địa chỉ", address)):
        if len(value) > 500 or re.search(r"[\x00-\x1f\x7f\ufffd]", value):
            errors.append(label + " có ký tự hỏng/điều khiển hoặc dài quá 500 ký tự")
    return {"id": row["id"], "name": name, "phone": phone, "address": address, "cmnd": cmnd,
            "fingerprint": fingerprint(row)}, errors


def pmv_rows(client):
    return client.query("SELECT CustID, CustCode, CustName, Phone, GhiChu2, GhiChu3 FROM I_CUSTOMER WITH (NOLOCK) "
                        "WHERE ISNULL(Phone,'')<>'' OR ISNULL(GhiChu2,'')<>'' OR ISNULL(GhiChu3,'')<>''")


def classify(source, pmv, receipts=()):
    counts = Counter(phone_key(r.get("phone")) for r in source)
    matches = defaultdict(list)
    for row in pmv:
        for key in P.values(row):
            matches[key].append(row)
    receipts = {r.source_id: r for r in receipts}
    output = []
    for source_row in source:
        row, errors = prepared(source_row)
        found = matches.get(row["phone"], [])
        receipt = receipts.get(row["id"])
        row.update(status="ready", message="Đủ PHONE + TÊN", cust_id="", retry=False)
        if receipt and not receipt_matches_source(receipt, source_row):
            row.update(status="attention", message="Nguồn đã đổi sau lần sync trước; cần đối chiếu lại", cust_id=receipt.cust_id)
        elif receipt and receipt.status not in ("done", "failed"):
            row.update(status="attention", message=receipt.message or "Lần ghi trước chưa xác nhận hoàn tất",
                       cust_id=receipt.cust_id, retry=True)
        elif len(found) > 1:
            row.update(status="attention", message="PHONE khớp nhiều khách PMV; cần đối chiếu")
        elif found:
            row.update(status="existing", message="Đã có PHONE trong PMV", cust_id=found[0]["CustID"],
                       pmv_name=found[0].get("CustName") or "")
        elif receipt and receipt.status == "done":
            row.update(status="attention", message="Khách từng sync đã đổi PHONE hoặc không còn trong PMV", cust_id=receipt.cust_id)
        elif errors:
            row.update(status="attention", message="; ".join(errors))
        elif counts[row["phone"]] > 1:
            row.update(status="attention", message="PHONE lặp trong bảng nguồn; cần xử lý để PHONE duy nhất")
        elif receipt and receipt.status == "failed":
            row.update(message=receipt.message + " — có thể thử lại")
        output.append(row)
    return output


@contextmanager
def sync_lock(target):
    name = "khbl:customer-sync:" + target
    with C.SAVE_LOCK:
        with connection.cursor() as cursor:
            cursor.execute("SELECT GET_LOCK(%s, 0)", (name,))
            if cursor.fetchone()[0] != 1:
                raise C.CustomerSaveError("Một lượt sync đang ghi. Vui lòng thử lại sau.")
        try:
            yield
        finally:
            with connection.cursor() as cursor:
                cursor.execute("SELECT RELEASE_LOCK(%s)", (name,))


def matching_phone(client, phone):
    # Cùng phép chuẩn hóa với phone_key; SQL 2005 không có REGEXP_REPLACE.
    return client.query("SELECT CustID, CustCode, CustName, Phone, GhiChu2, GhiChu3 FROM I_CUSTOMER WITH (NOLOCK) WHERE "
                        + P.exact_sql(), (phone,) * 3)


def source_phone_count(phone):
    with connection.cursor() as cursor:
        cursor.execute("SELECT COUNT(*) FROM khj_cd.customer WHERE " + phone_expression("phone") + "=%s", (phone,))
        return cursor.fetchone()[0]


def finish_receipt(receipt, client):
    saved = C._current(client, receipt.cust_id)
    expected = receipt.payload
    if not saved or phone_key(saved.get("Phone")) != receipt.phone:
        raise C.CustomerSaveError("Khách đã ghi không còn đúng PHONE; cần đối chiếu thủ công")
    if (saved.get("CustName") != expected["name"] or (saved.get("Address") or "") != expected["address"]
            or (saved.get("CMND") or "") != expected.get("cmnd", "")
            or (saved.get("NoiCap") or "") != expected.get("issued_by", "")
            or str(saved.get("BirthDate"))[:10] != "1900-01-01" or saved.get("Gender") is not None):
        raise C.CustomerSaveError("Dữ liệu PMV đọc lại khác thông tin sync; cần đối chiếu thủ công")
    if not C._has_points(client, receipt.cust_id):
        client.call("I_DiemTichLuy_InsFromGT", write=True, day_du=True, p_CustID=receipt.cust_id)
    if not C._has_points(client, receipt.cust_id):
        raise C.CustomerSaveError("Đã tạo khách nhưng chưa tạo được sổ điểm; bấm Hoàn tất để thử lại")
    receipt.status, receipt.message = "done", "Đã thêm khách và mở sổ điểm"
    receipt.save()
    return {"status": "existing", "message": receipt.message, "cust_id": receipt.cust_id, "created": True}


def insert_one(source_id, expected_fingerprint, target, user_id, shop_id):
    if target not in ("kk", "sandbox") or target != gateway.dich_hien_tai():
        raise C.CustomerSaveError("Đích PMV đã đổi. Hãy tải lại danh sách trước khi sync.")
    client = PmvClient(target, tag="customer_sync")
    with sync_lock(target):
        if target != gateway.dich_hien_tai():
            raise C.CustomerSaveError("Đích PMV đã đổi. Hãy tải lại danh sách.")
        source = source_rows(source_id)
        if not source:
            raise C.CustomerSaveError("Khách nguồn không còn tồn tại")
        source = source[0]
        row, errors = prepared(source)
        if row["fingerprint"] != expected_fingerprint:
            raise C.CustomerSaveError("PHONE/TÊN/địa chỉ/CCCD nguồn đã đổi. Hãy tải lại danh sách.")
        receipt = CustomerSyncReceipt.objects.filter(target=target, source_id=source_id).first()
        if receipt and not receipt_matches_source(receipt, source):
            raise C.CustomerSaveError("Dòng nguồn đã được xử lý với dữ liệu khác; cần đối chiếu thủ công")
        if receipt and receipt.cust_id:
            if receipt.status == "done":
                saved = C._current(client, receipt.cust_id)
                if not saved or phone_key(saved.get("Phone")) != receipt.phone:
                    raise C.CustomerSaveError("Khách từng sync đã đổi PHONE hoặc không còn trong PMV; cần đối chiếu")
                return {"status": "existing", "message": "Khách này đã sync trước đó", "cust_id": receipt.cust_id, "created": False}
            try:
                return finish_receipt(receipt, client)
            except Exception as exc:
                receipt.status, receipt.message = "partial", C.error_message(exc)
                receipt.save()
                raise
        found = matching_phone(client, row["phone"]) if row["phone"] else []
        if receipt and receipt.status in ("writing", "uncertain"):
            # Không biết chắc Ins đã COMMIT: chỉ nhận lại khi toàn bộ payload khớp.
            if len(found) == 1:
                saved = C._current(client, found[0]["CustID"])
                if saved and saved.get("CustName") == receipt.payload.get("name") and (saved.get("Address") or "") == receipt.payload.get("address"):
                    receipt.cust_id = found[0]["CustID"]
                    receipt.save()
                    return finish_receipt(receipt, client)
            raise C.CustomerSaveError("Lần ghi trước chưa rõ kết quả. Chưa INSERT lại; cần kiểm tra PMV.")
        if len(found) > 1:
            raise C.CustomerSaveError("PHONE khớp nhiều khách PMV; cần đối chiếu")
        if found:
            return {"status": "existing", "message": "PHONE đã có trong PMV", "cust_id": found[0]["CustID"], "created": False}
        if errors:
            raise C.CustomerSaveError("; ".join(errors))
        if source_phone_count(row["phone"]) > 1:
            raise C.CustomerSaveError("PHONE lặp trong bảng nguồn; cần xử lý để PHONE duy nhất")
        data, errors, _ = C.clean_form({"CustName": row["name"], "Phone": row["phone"],
                                      "Address": row["address"], "BirthDate": "1900-01-01",
                                      "CMND": row["cmnd"], "NoiCap": ISSUED_BY}, allow_unknown_gender=True)
        if errors:
            raise C.CustomerSaveError("; ".join(errors))
        # Đây là kiểm tra ràng buộc ghi của PMV; không dùng CCCD để phân loại ĐÃ CÓ.
        duplicates = C.duplicate_errors(client, data)
        if duplicates:
            raise C.CustomerSaveError("; ".join(duplicates))
        data["_sync_schema"] = 2
        receipt, _ = CustomerSyncReceipt.objects.update_or_create(target=target, source_id=source_id,
            defaults={"phone": row["phone"], "fingerprint": expected_fingerprint, "payload": data,
                      "status": "writing", "message": "Đang chờ xác nhận PMV", "user_id": user_id})

        def remember(cust_id):
            receipt.cust_id = cust_id
            receipt.status = "partial"
            receipt.save()

        try:
            result = C.upsert(data, {}, shop_id=shop_id, client=client, on_created=remember)
            if not result.get("complete"):
                raise C.CustomerSaveError("; ".join(result.get("errors") or ["PMV chưa hoàn tất"]))
            return finish_receipt(receipt, client)
        except Exception as exc:
            receipt.status = ("partial" if receipt.cust_id else
                              "failed" if isinstance(exc, (gateway.PmvBlocked, PmvProcError)) else "uncertain")
            receipt.message = C.error_message(exc)
            receipt.save()
            raise


@require_GET
@never_cache
def listing(request):
    Q.chan(request, "KHACH_HANG")
    try:
        client = PmvClient(tag="customer_sync_preview")
        rows = classify(source_rows(), pmv_rows(client), CustomerSyncReceipt.objects.filter(target=client.target))
        return JsonResponse({"rows": rows, "target": client.target,
                             "can_edit": Q.duoc(request.user, "KHACH_HANG", "can_edit")})
    except Exception as exc:
        return JsonResponse({"error": C.error_message(exc)}, status=503)


@require_POST
@never_cache
def insert(request):
    Q.chan(request, "KHACH_HANG", "can_edit")
    try:
        body = json.loads(request.body)
        if not isinstance(body, dict) or not isinstance(body.get("id"), int) or isinstance(body.get("id"), bool):
            raise ValueError("Dữ liệu yêu cầu không hợp lệ")
        if not re.fullmatch(r"[a-f0-9]{64}", str(body.get("fingerprint", ""))):
            raise ValueError("Thiếu bản đối chiếu; hãy tải lại danh sách")
    except (ValueError, TypeError):
        return JsonResponse({"error": "Dữ liệu yêu cầu không hợp lệ. Hãy tải lại danh sách."}, status=400)
    try:
        from .views import _phien
        result = insert_one(body["id"], body["fingerprint"], body.get("target"), request.user.pk,
                            _phien(request)["shop_id"])
        return JsonResponse(result)
    except Exception as exc:
        return JsonResponse({"error": C.error_message(exc)}, status=409 if isinstance(exc, C.CustomerSaveError) else 503)
