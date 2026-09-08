"""Ghi khách hàng PMV qua stored procedure vendor.

Luồng cố ý tách làm hai chặng: dữ liệu khách được UPSERT trước, ảnh chỉ được nén
và gửi sang ``I_CUSTOMER_Upd`` sau khi chặng UPSERT đã thành công. Vì vậy một
UPSERT bị từ chối do trùng hoặc sai dữ liệu không thể để lại tệp ảnh trên máy PMV.
"""
from io import BytesIO
from collections import OrderedDict
import re
import threading

from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from PIL import Image, ImageOps, UnidentifiedImageError

from apps.pmv.client import PmvClient, PmvProcError

from .vn_text import KY_TU_HONG, bo_dau, chuan_hoa, hoa_dau_tu


class CustomerSaveError(Exception):
    """Lỗi có thể trình bày trực tiếp trong popup khách hàng."""


# Waitress hiện chạy nhiều thread trong một process. Khóa này chặn hai request
# khách hàng cùng vượt qua bước kiểm tra trùng trước khi proc kịp COMMIT.
SAVE_LOCK = threading.Lock()
_SAVED_TOKENS = OrderedDict()
_MAX_SAVED_TOKENS = 256

IMAGE_FIELDS = {
    "anh_dai_dien": ("p_ImageData", "p_ImagePath", 1600, 380_000),
    "anh_truoc": ("p_ImageDataMatTruoc", "p_ImagePathMatTruoc", 2000, 650_000),
    "anh_sau": ("p_ImageDataMatSau", "p_ImagePathMatSau", 2000, 650_000),
}
SAVED_IMAGE_FIELDS = {
    "dai-dien": ("ImagePath", "Ảnh đại diện"),
    "mat-truoc": ("ImagePathMatTruoc", "CCCD mặt trước"),
    "mat-sau": ("ImagePathMatSau", "CCCD mặt sau"),
}
IMAGE_PARAM_PATHS = {
    "p_ImageData": ("ImagePath", "Ảnh đại diện"),
    "p_ImageDataMatTruoc": ("ImagePathMatTruoc", "CCCD mặt trước"),
    "p_ImageDataMatSau": ("ImagePathMatSau", "CCCD mặt sau"),
}
MAX_UPLOAD = 15 * 1024 * 1024
MAX_PIXELS = 25_000_000


def clean_form(post):
    """Chuẩn hóa và kiểm tra dữ liệu form. Trả ``(data, errors, warnings)``."""
    errors, warnings = [], []

    def text(name, label, limit, title=False):
        value, found = chuan_hoa(post.get(name) or "")
        if title:
            value = hoa_dau_tu(value)
        warnings.extend(f"{label}: {w}" for w in found)
        if KY_TU_HONG in value:
            errors.append(f"{label} còn ký tự hỏng mã; vui lòng đối chiếu và sửa trước khi lưu")
        if re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", value):
            errors.append(f"{label} chứa ký tự điều khiển không hợp lệ")
        if len(value) > limit:
            errors.append(f"{label} dài quá {limit} ký tự")
        return value

    name = text("CustName", "Họ tên", 500, title=True)
    if not name:
        errors.append("Chưa nhập họ tên khách")

    raw_phone = (post.get("Phone") or "").strip()
    phone = re.sub(r"[\s.()\-]", "", raw_phone)
    if phone and (not phone.isdigit() or not 9 <= len(phone) <= 11):
        errors.append("Số điện thoại phải gồm 9–11 chữ số")

    cmnd = re.sub(r"\s", "", post.get("CMND") or "")
    if cmnd and not re.fullmatch(r"(?:[0-9]{9}|[0-9]{12})", cmnd):
        errors.append("Số CCCD/CMND phải gồm đúng 9 hoặc 12 chữ số")

    gender = (post.get("Gender") or "").strip()
    if gender not in ("0", "1"):
        errors.append("Chưa xác định giới tính — chọn theo thông tin khách cung cấp")

    birth = _date_vn(post.get("BirthDate"), "Ngày sinh", errors)
    issued = _date_vn(post.get("NgayCap"), "Ngày cấp", errors)
    if birth and issued and _date_key(issued) < _date_key(birth):
        errors.append("Ngày cấp không được trước ngày sinh")

    email = (post.get("Email") or "").strip()
    if email:
        try:
            validate_email(email)
        except ValidationError:
            errors.append("Email không hợp lệ")
    if len(email) > 100:
        errors.append("Email dài quá 100 ký tự")

    cust_type = (post.get("CustType") or "").strip().upper()
    if cust_type not in ("", "VIP", "VVIP", "CANHBAO"):
        errors.append("Loại khách không hợp lệ")

    return {
        "cust_id": (post.get("CustID") or "").strip(),
        "name": name,
        "phone": phone,
        "cmnd": cmnd,
        "address": text("Address", "Địa chỉ", 500),
        "birth": birth,
        "gender": gender,
        "issued": issued,
        "issued_by": text("NoiCap", "Nơi cấp", 200),
        "email": email,
        "notes": text("Notes", "Ghi chú", 1000),
        "cust_type": cust_type,
        "active": "1" if post.get("Active", "1") == "1" else "0",
    }, list(dict.fromkeys(errors)), list(dict.fromkeys(warnings))


def prepare_images(files):
    """Đọc, xác thực, xoay đúng EXIF và nén mọi ảnh thành JPEG rõ nét."""
    result, info = {}, []
    for field, (data_param, path_param, max_edge, target_size) in IMAGE_FIELDS.items():
        upload = files.get(field)
        if not upload:
            continue
        if getattr(upload, "size", 0) > MAX_UPLOAD:
            raise CustomerSaveError(f"{upload.name}: ảnh lớn quá 15 MB")
        try:
            upload.seek(0)
            image = Image.open(upload)
            if image.width * image.height > MAX_PIXELS:
                raise CustomerSaveError(f"{upload.name}: ảnh có quá nhiều điểm ảnh")
            image.load()
            image = ImageOps.exif_transpose(image)
        except CustomerSaveError:
            raise
        except (UnidentifiedImageError, OSError, ValueError):
            raise CustomerSaveError(f"{upload.name}: tệp không phải ảnh hợp lệ")

        if image.mode in ("RGBA", "LA") or (image.mode == "P" and "transparency" in image.info):
            rgba = image.convert("RGBA")
            base = Image.new("RGB", rgba.size, "white")
            base.paste(rgba, mask=rgba.getchannel("A"))
            image = base
        else:
            image = image.convert("RGB")
        image.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)

        encoded = b""
        for quality in (88, 85, 82, 80, 78):
            out = BytesIO()
            image.save(out, "JPEG", quality=quality, optimize=True, progressive=True,
                       subsampling="4:2:0")
            encoded = out.getvalue()
            if len(encoded) <= target_size:
                break
        result[data_param] = encoded
        result[path_param] = ".jpg"  # proc chỉ đọc phần mở rộng để tự dựng đường dẫn
        info.append({"field": field, "bytes": len(encoded), "width": image.width,
                     "height": image.height})
    return result, info


def duplicate_errors(client, data):
    """Kiểm tra sớm để báo rõ khách nào đang giữ số điện thoại/CCCD."""
    clauses, params = [], []
    if data["phone"]:
        clauses.append("Phone = ?")
        params.append(data["phone"])
    if data["cmnd"]:
        clauses.append("CMND = ?")
        params.append(data["cmnd"])
    if not clauses:
        return []
    sql = ("SELECT CustID, CustCode, CustName, Phone, CMND FROM I_CUSTOMER WITH (NOLOCK) "
           "WHERE (" + " OR ".join(clauses) + ")")
    if data["cust_id"]:
        sql += " AND CustID <> ?"
        params.append(data["cust_id"])
    rows = client.query(sql, tuple(params))
    errors = []
    for row in rows:
        who = f"{row.get('CustCode') or row['CustID']} — {row.get('CustName') or 'khách đã có'}"
        if data["phone"] and row.get("Phone") == data["phone"]:
            errors.append(f"Số điện thoại {data['phone']} đã thuộc {who}")
        if data["cmnd"] and row.get("CMND") == data["cmnd"]:
            errors.append(f"CCCD/CMND {data['cmnd']} đã thuộc {who}")
    return list(dict.fromkeys(errors))


def upsert(data, images, shop_id="", client=None):
    """UPSERT khách, mở sổ điểm nếu mới, sau đó mới ghi ảnh. Phải gọi trong SAVE_LOCK."""
    client = client or PmvClient(tag="khach_luu")
    current = _current(client, data["cust_id"]) if data["cust_id"] else None
    if data["cust_id"] and not current:
        raise CustomerSaveError("Khách hàng không còn tồn tại; hãy đóng popup và tải lại danh sách")
    dup = duplicate_errors(client, data)
    if dup:
        raise CustomerSaveError("\n".join(dup))

    params = _params(data, current, shop_id, client)
    proc = "I_CUSTOMER_Upd" if current else "I_CUSTOMER_Ins"
    try:
        _, sets = client.call(proc, write=True, day_du=True, **params)
    except PmvProcError as exc:
        raise CustomerSaveError(_proc_error(exc)) from exc

    cust_id = data["cust_id"] or _result_value(sets, "CustID")
    cust_code = (current or {}).get("CustCode") or _result_value(sets, "CustCode")
    if not cust_id:
        raise CustomerSaveError(f"{proc} không trả về mã khách; chưa thể xác nhận đã lưu")
    saved = _current(client, cust_id)
    if not saved or saved.get("CustName") != data["name"] or (saved.get("Phone") or "") != data["phone"] \
            or (saved.get("CMND") or "") != data["cmnd"]:
        raise CustomerSaveError("PMV trả thành công nhưng dữ liệu đọc lại chưa khớp; vui lòng kiểm tra")

    warnings, incomplete = [], []
    if not _has_points(client, cust_id):
        try:
            client.call("I_DiemTichLuy_InsFromGT", write=True, day_du=True, p_CustID=cust_id)
            if not _has_points(client, cust_id):
                incomplete.append("PMV không tạo được sổ điểm tích lũy sau khi đã lưu thông tin khách")
        except Exception as exc:
            incomplete.append("Chưa mở được sổ điểm tích lũy: " + error_message(exc))

    if images:
        image_params = dict(params)
        image_params.update(images)
        image_params["p_CustID"] = cust_id
        # Proc vendor dùng p_CustCode chỉ để dựng tên tệp, không sửa mã khách trong bảng.
        image_params["p_CustCode"] = _image_prefix(data["name"])
        try:
            client.call("I_CUSTOMER_Upd", write=True, day_du=True, **image_params)
            after_image = _current(client, cust_id) or {}
            for data_param, (path_field, label) in IMAGE_PARAM_PATHS.items():
                if data_param not in images:
                    continue
                path = after_image.get(path_field)
                if not path:
                    raise CustomerSaveError(f"{label}: PMV chưa ghi đường dẫn tệp ảnh")
                _validated_image(client, path)
        except Exception as exc:
            incomplete.append("Chưa lưu/kiểm tra được ảnh: " + error_message(exc))

    return {"cust_id": cust_id, "cust_code": cust_code, "name": data["name"],
            "created": not bool(current), "warnings": warnings,
            "complete": not incomplete, "errors": incomplete}


# Guard XÓA (08/09/2026): proc vendor I_CUSTOMER_Del chỉ kiểm 4 bảng đầu, BỎ SÓT hóa đơn THÂU
# (TRN_RT_BUYGOLD) — kiểm chứng sandbox: 2.346 khách chỉ có thâu vẫn xóa được. Web kiểm đủ 5 bảng.
GD_TABLES = (("TRN_RT_BUYSELL", "hóa đơn bán"), ("TRN_RT_BUYGOLD", "hóa đơn thâu"),
             ("T_CUSTOMER_DEBT", "dòng công nợ"), ("TRN_RT_CHANGE", "hóa đơn đổi"),
             ("TRN_PO_MASTER", "đơn đặt hàng"))


def giao_dich(client, cust_id):
    """Đếm giao dịch của khách theo từng loại — {nhãn: số dòng}, rỗng = xóa được."""
    out = {}
    for table, label in GD_TABLES:
        n = client.query(f"SELECT COUNT(*) AS n FROM {table} WITH (NOLOCK) WHERE CustID = ?", (cust_id,))[0]["n"]
        if n:
            out[label] = n
    return out


def delete(cust_id, client=None):
    """XÓA khách qua I_CUSTOMER_Del (vendor tự dọn I_DIEMTICHLUY / I_GIAODICH_KHACHHANG / SHOP_CUSTOMER
    + ghi đè ảnh). Chỉ cho xóa khách CHƯA có giao dịch (guard bán + thâu + nợ + đổi + đặt hàng).
    Phải gọi trong SAVE_LOCK. Kiểm chứng đọc lại: proc trả 0 mà dòng còn → báo lỗi (bẫy im lặng)."""
    client = client or PmvClient(tag="khach_xoa")
    current = _current(client, cust_id)
    if not current:
        raise CustomerSaveError("Khách hàng không còn tồn tại; hãy tải lại danh sách")
    gd = giao_dich(client, cust_id)
    if gd:
        raise CustomerSaveError("Không xóa được — khách đã có giao dịch: "
                                + ", ".join(f"{n} {label}" for label, n in gd.items()))
    try:
        client.call("I_CUSTOMER_Del", write=True, p_CustID=cust_id)
    except PmvProcError as exc:
        raise CustomerSaveError(f"PMV từ chối xóa khách (Result={exc.rc}: còn ràng buộc dữ liệu)") from exc
    if _current(client, cust_id):
        raise CustomerSaveError("PMV trả thành công nhưng khách vẫn còn trong danh sách; vui lòng kiểm tra")
    return {"cust_id": cust_id, "cust_code": current.get("CustCode") or cust_id,
            "name": current.get("CustName") or ""}


def saved_image(cust_id, kind, client=None):
    """Đọc và kiểm tra một ảnh đã lưu, không nhận đường dẫn từ phía trình duyệt."""
    if kind not in SAVED_IMAGE_FIELDS:
        raise CustomerSaveError("Loại ảnh không hợp lệ")
    client = client or PmvClient(tag="khach_anh")
    current = _current(client, cust_id)
    if not current:
        raise CustomerSaveError("Không tìm thấy khách hàng")
    field, label = SAVED_IMAGE_FIELDS[kind]
    path = current.get(field)
    if not path:
        raise CustomerSaveError(f"{label}: chưa có ảnh đã lưu")
    return _validated_image(client, path)


def _validated_image(client, path):
    data = client.image_file(path)
    if not data:
        raise CustomerSaveError("Tệp ảnh rỗng hoặc không tồn tại trên máy PMV")
    if len(data) > MAX_UPLOAD:
        raise CustomerSaveError("Tệp ảnh đã lưu lớn quá 15 MB")
    try:
        with Image.open(BytesIO(data)) as image:
            fmt = (image.format or "").upper()
            image.verify()
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise CustomerSaveError("Tệp trên máy PMV không phải ảnh hợp lệ") from exc
    content_type = {"JPEG": "image/jpeg", "PNG": "image/png"}.get(fmt)
    if not content_type:
        raise CustomerSaveError(f"Định dạng ảnh đã lưu không được hỗ trợ ({fmt or 'không xác định'})")
    return data, content_type


def _has_points(client, cust_id):
    rows = client.query(
        "SELECT TOP 1 CustID FROM I_DIEMTICHLUY WITH (NOLOCK) WHERE CustID = ?", (cust_id,))
    return bool(rows)


def error_message(exc):
    """Giữ nguyên nguyên nhân hữu ích từ PMV/ODBC nhưng giới hạn độ dài hiển thị."""
    if isinstance(exc, PmvProcError):
        return _proc_error(exc)
    message = str(exc).strip() or exc.__class__.__name__
    return re.sub(r"\s+", " ", message)[:800]


def previous_save(token, fingerprint):
    """Kết quả của đúng lượt lưu đã xong, dùng khi trình duyệt gửi lặp."""
    item = _SAVED_TOKENS.get(token)
    if not item:
        return None
    if item[0] != fingerprint:
        raise CustomerSaveError("Phiên lưu đã được dùng với dữ liệu khác; hãy mở lại biểu mẫu")
    _SAVED_TOKENS.move_to_end(token)
    return item[1]


def remember_save(token, fingerprint, result):
    _SAVED_TOKENS[token] = (fingerprint, result)
    _SAVED_TOKENS.move_to_end(token)
    while len(_SAVED_TOKENS) > _MAX_SAVED_TOKENS:
        _SAVED_TOKENS.popitem(last=False)


def _params(data, current, shop_id, client):
    current = current or {}
    auto_rank = "1" if current.get("TuDongNangHang") in (None, True, 1, "1") else "0"
    return {
        "p_CustID": data["cust_id"],
        "p_CustCode": current.get("CustCode") or "",
        "p_CustName": data["name"], "p_Address": data["address"],
        "p_Phone": data["phone"], "p_Notes": data["notes"], "p_Active": data["active"],
        "p_CMND": data["cmnd"], "p_CustGroupID": current.get("CustGroupID") or "",
        "p_CustTypeID": data["cust_type"], "p_BirthDate": data["birth"] or "01/01/1900",
        "p_Gender": data["gender"], "p_Email": data["email"],
        "p_DateOfJoining": "", "p_LastTradingDate": "",
        "TuDongNangHang": auto_rank,
        "p_Image": None, "MaGD": _trade_xml(client, data["cust_id"]),
        "p_Company": current.get("Company") or "",
        "p_Company_Address": current.get("Company_Address") or "",
        "p_Masothue": current.get("Masothue") or "", "ShopID": shop_id or "",
        "p_ImageMatTruocCMND": None, "p_ImageMatSauCMND": None,
        "p_NgayCap": data["issued"] or ("01/01/1900" if data["cmnd"] else ""),
        "p_NoiCap": data["issued_by"], "p_GhiChu2": current.get("GhiChu2") or "",
        "p_GhiChu3": current.get("GhiChu3") or "", "p_ImagePath": None,
        "p_ImagePathMatTruoc": None, "p_ImagePathMatSau": None,
        "p_ImageData": None, "p_ImageDataMatTruoc": None, "p_ImageDataMatSau": None,
        "p_Passport": current.get("Passport") or "",
    }


def _current(client, cust_id):
    if not cust_id:
        return None
    rows = client.query(
        "SELECT TOP 1 CustID, CustCode, CustName, Address, Phone, Notes, Active, CMND, "
        "CustGroupID, CustTypeID, BirthDate, Gender, Email, TuDongNangHang, Company, "
        "Company_Address, Masothue, GhiChu2, GhiChu3, Passport, ImagePath, "
        "ImagePathMatTruoc, ImagePathMatSau FROM I_CUSTOMER WITH (NOLOCK) WHERE CustID = ?",
        (cust_id,))
    return rows[0] if rows else None


def _trade_xml(client, cust_id):
    if not cust_id:
        return ""
    rows = client.query("SELECT MaGD FROM I_GIAODICH_KHACHHANG WITH (NOLOCK) WHERE CustID = ?",
                        (cust_id,))
    return client.xml_dataset("I_GIAODICH_KHACHHANG",
                              [{"colChon": "True", "MaGD": r["MaGD"]} for r in rows]) if rows else ""


def _image_prefix(name):
    slug = re.sub(r"[^A-Za-z0-9]+", "_", bo_dau(name)).strip("_")[:70]
    return (slug or "Khach_hang") + "_"


def _date_vn(value, label, errors):
    import datetime
    value = (value or "").strip()
    if not value:
        return ""
    try:
        return datetime.date.fromisoformat(value).strftime("%d/%m/%Y")
    except ValueError:
        errors.append(f"{label} không hợp lệ")
        return ""


def _date_key(value):
    day, month, year = value.split("/")
    return year + month + day


def _result_value(sets, key):
    for rows in sets or []:
        for row in rows or []:
            if row.get(key):
                return str(row[key]).strip()
    return ""


def _proc_error(exc):
    for rows in exc.sets or []:
        for row in rows or []:
            message = row.get("ErrorDesc") or row.get("loi")
            if message:
                return str(message)
    return f"PMV từ chối lưu khách (mã lỗi {exc.rc})"
