import datetime
import json
import time

from django.contrib.auth import get_user_model
from django.conf import settings
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from . import behavior_log as BL
from . import diff as diffmod
from . import gateway
from . import quy_trinh as QT
from apps.pos import passcode as PC
from .models import (
    PmvAudit, PmvBehavior, PmvChange, PmvProcess, PmvProcessStep, PmvSnapshot,
    PmvState, PmvUser, PmvWebUser, UserModuleAccess,
)

DANH_DAU_KEY = "pmv_danh_dau"   # JSON {snap_id, luc, seq} khi đang ở giữa TRƯỚC và SAU


def _require_user_administrator(request):
    """Quản trị tài khoản là quyền kỹ thuật, chỉ Superuser mới được thực hiện."""
    if not request.user.is_authenticated or not request.user.is_superuser:
        raise PermissionDenied("Chỉ tài khoản Superuser được quản trị người dùng.")


def _rights_for_user(user):
    """Ma trận quyền sẵn để render form, một phần tử cho mỗi danh mục."""
    current = {item.module: item for item in user.module_accesses.all()} if user else {}
    return [
        {
            "code": code,
            "label": label,
            "can_view": user.is_superuser or bool(current.get(code) and current[code].can_view),
            "can_edit": user.is_superuser or bool(current.get(code) and current[code].can_edit),
            "can_delete": user.is_superuser or bool(current.get(code) and current[code].can_delete),
            "can_approve": user.is_superuser or bool(current.get(code) and current[code].can_approve),
        }
        for code, label in UserModuleAccess.Module.choices
    ]


def _user_form_context(target, *, error="", form_data=None):
    selected_pmv_id = PmvWebUser.objects.filter(web_user=target).values_list("pmv_user_id", flat=True).first() if target else ""
    form_data = form_data or {}

    def initial_value(name):
        return form_data.get(name, "") if form_data else getattr(target, name, "") if target else ""

    return {
        "target_user": target,
        "pmv_users": PmvUser.objects.order_by("full_name", "user_name"),
        "rights": _rights_for_user(target) if target else [
            {"code": code, "label": label, "can_view": False, "can_edit": False,
             "can_delete": False, "can_approve": False}
            for code, label in UserModuleAccess.Module.choices
        ],
        "error": error,
        "form_data": form_data,
        "username_value": initial_value("username"),
        "email_value": initial_value("email"),
        "first_name_value": initial_value("first_name"),
        "last_name_value": initial_value("last_name"),
        "selected_pmv_id": selected_pmv_id,
        "target_user_id": target.pk if target else None,
        "has_unlock_passcode": PC.da_dat(target),
        "is_active_value": "is_active" in form_data if form_data else target.is_active if target else True,
        "is_staff_value": "is_staff" in form_data if form_data else target.is_staff if target else False,
        "is_superuser_value": "is_superuser" in form_data if form_data else target.is_superuser if target else False,
        "nav_active": "hethong",
    }


def user_list(request):
    """Danh sách tài khoản đăng nhập KHBL và trạng thái phân quyền từng người."""
    _require_user_administrator(request)
    User = get_user_model()
    users = list(User.objects.prefetch_related("module_accesses", "pmv_mappings__pmv_user").order_by("username"))
    for item in users:
        item.right_count = sum(1 for access in item.module_accesses.all() if access.can_view)
        mapping = next(iter(item.pmv_mappings.all()), None)
        item.pmv_link = mapping.pmv_user if mapping else None
    return render(request, "pmv/user_list.html", {"users": users, "nav_active": "hethong"})


def user_form(request, user_id=None):
    """Tạo hoặc sửa tài khoản; ma trận quyền luôn được UPSERT theo từng danh mục."""
    _require_user_administrator(request)
    User = get_user_model()
    target = User.objects.filter(pk=user_id).prefetch_related("module_accesses").first() if user_id else None
    if user_id and target is None:
        raise PermissionDenied("Không tìm thấy tài khoản cần chỉnh sửa.")

    if request.method != "POST":
        return render(request, "pmv/user_form.html", _user_form_context(target))

    username = request.POST.get("username", "").strip()
    first_name = request.POST.get("first_name", "").strip()
    last_name = request.POST.get("last_name", "").strip()
    email = request.POST.get("email", "").strip()
    password = request.POST.get("password", "")
    password_confirm = request.POST.get("password_confirm", "")
    unlock_passcode = request.POST.get("unlock_passcode", "").strip()
    remove_unlock_passcode = request.POST.get("remove_unlock_passcode") == "on"
    pmv_user_id = request.POST.get("pmv_user_id", "").strip()
    form_data = request.POST

    error = ""
    if not username:
        error = "Cần nhập tên đăng nhập."
    elif len(username) > 150 or not all(char.isalnum() or char in "@.+-_" for char in username):
        error = "Tên đăng nhập chỉ dùng chữ, số và các ký tự @ . + - _."
    elif User.objects.exclude(pk=target.pk if target else None).filter(username__iexact=username).exists():
        error = "Tên đăng nhập này đã tồn tại."
    elif not target and not password:
        error = "Tài khoản mới cần có mật khẩu."
    elif password and len(password) < 6:
        error = "Mật khẩu cần ít nhất 6 ký tự."
    elif password != password_confirm:
        error = "Nhập lại mật khẩu chưa khớp."
    elif unlock_passcode and not 4 <= len(unlock_passcode) <= 20:
        error = "Passcode mở khóa cần từ 4 đến 20 ký tự."
    elif unlock_passcode and remove_unlock_passcode:
        error = "Chọn một trong hai: đặt passcode mới hoặc xóa passcode riêng."

    pmv_user = PmvUser.objects.filter(pk=pmv_user_id).first() if pmv_user_id else None
    if not error and pmv_user_id and pmv_user is None:
        error = "Không tìm thấy tài khoản PMV đã chọn."
    requested_superuser = request.POST.get("is_superuser") == "on"
    if not error and target == request.user and not requested_superuser:
        error = "Không thể tự bỏ quyền Superuser của tài khoản đang đăng nhập."

    if error:
        return render(request, "pmv/user_form.html", _user_form_context(target, error=error, form_data=form_data))

    with transaction.atomic():
        created = target is None
        if target is None:
            target = User(username=username)
        target.username = username
        target.first_name = first_name
        target.last_name = last_name
        target.email = email
        target.is_active = request.POST.get("is_active") == "on"
        target.is_staff = request.POST.get("is_staff") == "on"
        target.is_superuser = requested_superuser
        if password:
            target.set_password(password)
        target.save()

        # Một Web User chọn một hồ sơ PMV; nhiều Web User được phép dùng chung hồ sơ này.
        if pmv_user:
            PmvWebUser.objects.update_or_create(
                web_user=target, defaults={"pmv_user": pmv_user, "updated_by": request.user},
            )
        else:
            PmvWebUser.objects.filter(web_user=target).delete()

        # Chỉ có mã băm được lưu. Ô text trên form là để nhập mã MỚI hoặc đổi mã.
        if remove_unlock_passcode:
            PC.xoa(target)
        elif unlock_passcode:
            PC.dat(target, unlock_passcode)

        for code, _label in UserModuleAccess.Module.choices:
            can_edit = request.POST.get(f"right_{code}_edit") == "on"
            can_delete = request.POST.get(f"right_{code}_delete") == "on"
            can_approve = request.POST.get(f"right_{code}_approve") == "on"
            can_view = request.POST.get(f"right_{code}_view") == "on" or can_edit or can_delete or can_approve
            if target.is_superuser:
                can_view = can_edit = can_delete = can_approve = True
            UserModuleAccess.objects.update_or_create(
                user=target,
                module=code,
                defaults={
                    "can_view": can_view,
                    "can_edit": can_edit,
                    "can_delete": can_delete,
                    "can_approve": can_approve,
                    "updated_by": request.user,
                },
            )

    messages.success(request, f"Đã {'tạo' if created else 'cập nhật'} tài khoản {target.username} và quyền theo danh mục.")
    return redirect("pmv:user_list")


@require_POST
def user_delete(request, user_id):
    _require_user_administrator(request)
    User = get_user_model()
    target = User.objects.filter(pk=user_id).first()
    if target is None:
        messages.error(request, "Tài khoản không còn tồn tại.")
    elif target == request.user:
        messages.error(request, "Không thể xóa tài khoản đang đăng nhập.")
    elif target.is_superuser and User.objects.filter(is_superuser=True, is_active=True).count() <= 1:
        messages.error(request, "Cần giữ lại ít nhất một Superuser đang hoạt động.")
    else:
        username = target.username
        target.delete()
        messages.success(request, f"Đã xóa tài khoản {username}.")
    return redirect("pmv:user_list")


def status(request):
    """Trang trạng thái GĐ0: backup gần nhất, sức khỏe PMV, nhật ký + cảnh báo vượt quyền."""
    from apps.pos.quyen import chan

    chan(request, "HE_THONG")
    state = {s.key: s.value for s in PmvState.objects.all()}
    audits = PmvAudit.objects.all()[:30]
    so_blocked = PmvAudit.objects.filter(kind=PmvAudit.Kind.BLOCKED).count()
    so_canhbao = PmvAudit.objects.filter(kind=PmvAudit.Kind.CANHBAO).count()
    dich = gateway.dich_hien_tai()
    return render(request, "pmv/status.html", {
        "state": state,
        "audits": audits,
        "so_blocked": so_blocked,
        "so_canhbao": so_canhbao,
        "write_lock": state.get("pmv_write_lock") == "1",
        # Công tắc đích dữ liệu (xem CLAUDE.md mục 4b)
        "dich": dich,
        "dich_mo_ta": gateway.mo_ta_dich(dich),
        "dich_env": gateway._chuan_dich(settings.PMV_TARGET),
        "dich_do_cong_tac": bool(state.get(gateway.DICH_KEY)),
        "ghi_kk": gateway.duoc_ghi_kk(),
    })


@require_POST
def doi_dich(request):
    """Công tắc ĐÍCH DỮ LIỆU trên trang Hệ thống — đổi được ngay, không cần RESET.

    Đổi sang máy KK CHỈ mở đường ĐỌC dữ liệu thật; ghi vẫn bị gateway từ chối chừng nào
    PMV_GHI_KK trong .env còn tắt. Chốt ghi cố ý KHÔNG đưa lên web — thứ nguy hiểm phải
    nằm trong file, có chủ đích mới sửa được."""
    chon = request.POST.get("dich", "")
    cu = gateway.dich_hien_tai()
    if chon == "env":
        moi = gateway.xoa_cong_tac()
        messages.success(request, f"Đã bỏ công tắc — quay về mặc định trong .env: {gateway.mo_ta_dich(moi)}.")
    else:
        moi = gateway.dat_dich(chon)
        if moi == "kk":
            them = ("Kênh GHI đang MỞ — mọi thao tác ghi vào sổ sách thật!"
                    if gateway.duoc_ghi_kk() else "Chỉ tra cứu — cổng vẫn cấm ghi vào máy KK.")
            messages.warning(request, f"⚠ Đã chuyển sang DỮ LIỆU THẬT máy KK. {them}")
        else:
            messages.success(request, "Đã chuyển về BẢN THỬ máy Mr Giang — ghi thoải mái, không đụng dữ liệu tiệm.")
    if moi != cu:
        gateway.canh_bao("doi_dich", f"ĐỔI ĐÍCH DỮ LIỆU: {cu} → {moi} "
                                     f"(người đổi: {request.user.username}, ghi KK={gateway.duoc_ghi_kk()})")
    return redirect("pmv:status")


def _resolve_snapshot(token):
    """token: 'pmv' | 'sandbox' | 'snap:<id>' → (data, mô tả). Live thì chụp fast."""
    if token and token.startswith("snap:"):
        snap = PmvSnapshot.objects.filter(pk=int(token[5:])).first()
        if not snap:
            return None, "(snapshot đã xóa)"
        return snap.payload, f"#{snap.pk} {snap.label} · {timezone.localtime(snap.created_at):%d/%m %H:%M} · {snap.mode}"
    if token in ("pmv", "sandbox"):
        data = diffmod.snapshot(token, mode="fast")
        nhan = "PMV thật (live)" if token == "pmv" else "Sandbox (live)"
        return data, f"{nhan} · {timezone.localtime():%d/%m %H:%M:%S} · fast"
    return None, "(chưa chọn)"


def diff_view(request):
    """So sánh 2 nguồn SQL, tô màu bảng khác nhau."""
    snapshots = PmvSnapshot.objects.all()[:50]
    a_tok = request.GET.get("a", "pmv")
    b_tok = request.GET.get("b", "sandbox")
    only_diff = request.GET.get("only") == "1"

    rows = summary = None
    desc_a = desc_b = ""
    if "so" in request.GET:
        data_a, desc_a = _resolve_snapshot(a_tok)
        data_b, desc_b = _resolve_snapshot(b_tok)
        if data_a is None or data_b is None:
            messages.error(request, "Nguồn không hợp lệ hoặc snapshot đã xóa.")
        else:
            rows = diffmod.diff(data_a, data_b)
            summary = {
                "diff": sum(1 for r in rows if r["status"] == "diff"),
                "only_a": sum(1 for r in rows if r["status"] == "only_a"),
                "only_b": sum(1 for r in rows if r["status"] == "only_b"),
                "same": sum(1 for r in rows if r["status"] == "same"),
                "total": len(rows),
            }
            if only_diff:
                rows = [r for r in rows if r["status"] != "same"]

    return render(request, "pmv/diff.html", {
        "snapshots": snapshots,
        "a_tok": a_tok, "b_tok": b_tok, "only_diff": only_diff,
        "desc_a": desc_a, "desc_b": desc_b,
        "rows": rows, "summary": summary, "ran": "so" in request.GET,
        "danh_dau": _danh_dau_hien_tai(),
        "thay_doi_last": PmvState.get(BL.THAY_DOI_LAST_KEY),
        "log_dir": str(BL.LOG_DIR),
    })


# ─────────────────────── ĐÁNH DẤU TRƯỚC / SAU (Bước 2 thủ công, GĐ duyệt 05/09/2026) ───────────────────────

def _danh_dau_hien_tai():
    try:
        return json.loads(PmvState.get(DANH_DAU_KEY) or "null")
    except Exception:
        return None


def _thu_trace():
    from .management.commands.collect_pmv_behavior import thu_thap_trace

    rows = thu_thap_trace()
    BL.ghi(rows)
    return rows


@require_POST
def danh_dau_truoc(request):
    """Chụp FULL KK + ghim mốc trace. Sau đó GĐ làm ĐÚNG 1 thao tác trên PMVGoldRT rồi bấm SAU.
    GĐ chốt 06/09/2026: job 2 phút đã TẮT, trace KK bình thường TẮT → TRƯỚC tự BẬT trace, SAU tự TẮT."""
    from django.core.management import call_command
    from .management.commands.pmv_trace import trace_status

    try:
        st = trace_status() or {}
        if str(st.get("status")) != "1":
            call_command("pmv_trace", "start")
        _thu_trace()
        seq = int(PmvState.get("pmv_trace_last_seq") or 0)
        snap = BL.chup_kk()
    except Exception as exc:
        messages.error(request, f"Không đánh dấu được: {exc}")
        return redirect("pmv:diff")
    now = timezone.now()
    s = PmvSnapshot.objects.create(label=f"ĐÁNH DẤU TRƯỚC {timezone.localtime(now):%d/%m %H:%M:%S}",
                                   source="pmv", mode="full", table_count=len(snap), payload=snap)
    PmvState.set(DANH_DAU_KEY, json.dumps({"snap_id": s.pk, "luc": now.isoformat(), "seq": seq}))
    messages.success(request, f"Đã đánh dấu TRƯỚC lúc {timezone.localtime(now):%H:%M:%S} ({len(snap)} bảng). "
                              "Giờ làm ĐÚNG 1 thao tác trên PMVGoldRT rồi quay lại bấm ĐÁNH DẤU SAU.")
    return redirect("pmv:diff")


@require_POST
def danh_dau_sau(request):
    """Chụp lại, so với mốc TRƯỚC, ghép proc trace giữa 2 mốc + kéo dòng vừa đổi → báo cáo 1 thao tác."""
    mark = _danh_dau_hien_tai()
    if not mark:
        messages.error(request, "Chưa có mốc TRƯỚC — bấm ĐÁNH DẤU TRƯỚC rồi làm thao tác đã.")
        return redirect("pmv:diff")
    truoc = PmvSnapshot.objects.filter(pk=mark["snap_id"]).first()
    if not truoc:
        PmvState.objects.filter(key=DANH_DAU_KEY).delete()
        messages.error(request, "Snapshot TRƯỚC đã bị xóa — đánh dấu lại.")
        return redirect("pmv:diff")
    try:
        _thu_trace()
        snap = BL.chup_kk()
    except Exception as exc:
        messages.error(request, f"Không chụp được trạng thái SAU: {exc}")
        return redirect("pmv:diff")
    now = timezone.now()
    ws = datetime.datetime.fromisoformat(mark["luc"])
    sau = PmvSnapshot.objects.create(label=f"ĐÁNH DẤU SAU {timezone.localtime(now):%d/%m %H:%M:%S}",
                                     source="pmv", mode="full", table_count=len(snap), payload=snap)
    khac = BL.bang_doi(truoc.payload, snap)
    hanh_vi = list(PmvBehavior.objects.filter(source=PmvBehavior.Source.TRACE, event_seq__gt=mark["seq"])
                   .order_by("event_seq", "id"))
    procs = BL.chuoi_proc(BL.proc_ghi_trong(hanh_vi))
    objs = BL.tao_pmv_change(ws, now, khac, procs, mark=f"danhdau:{truoc.pk}")

    # mức dòng: bảng đổi có cột mốc thời gian → kéo dòng có mốc trong khung (trừ biên 2 phút
    # vì đồng hồ KK chậm ~35s so máy web)
    mau = []
    try:
        cot = BL.cot_moc_kk()
        tu = ws - datetime.timedelta(minutes=2)
        for r in khac:
            cols = cot.get(r["tbl"])
            if cols:
                mau.append({"bang": r["tbl"], "cot": cols, "dong": BL.mau_dong_doi(r["tbl"], cols, tu)})
    except Exception as exc:
        messages.warning(request, f"Không kéo được dòng chi tiết: {exc}")

    ngay = timezone.localtime(now).date()
    ten = f"danh_dau_{timezone.localtime(ws):%H%M%S}"
    lines = [f"=== ĐÁNH DẤU {timezone.localtime(ws):%d/%m/%Y %H:%M:%S} → {timezone.localtime(now):%H:%M:%S} "
             f"| {len(hanh_vi)} lời gọi | {len(khac)} bảng đổi | người đánh dấu: {request.user.username} ==="]
    lines += ["--- lời gọi (trace, mọi máy kể cả KHBL) ---"] + [BL.dong(b) for b in hanh_vi]
    lines += ["--- bảng đổi ---"] + BL.dong_thay_doi(ws, now, objs, procs)
    for m in mau:
        lines.append(f"--- dòng đổi trong {m['bang']} (theo {', '.join(m['cot'])}) ---")
        for d in m["dong"]:
            lines.append("    " + " | ".join(f"{k}={v}" for k, v in d.items()))
    BL.ghi_thay_doi(ngay, lines, ten=ten)
    PmvState.objects.filter(key=DANH_DAU_KEY).delete()
    # Học xong thì TẮT trace để KK nhẹ (TRƯỚC sẽ bật lại khi cần) — GĐ chốt 06/09/2026
    try:
        from django.core.management import call_command
        call_command("pmv_trace", "stop")
    except Exception as exc:
        messages.warning(request, f"Không tắt được trace KK: {exc}")
    return render(request, "pmv/danh_dau.html", {
        "ws": ws, "we": now, "hanh_vi": hanh_vi, "khac": khac, "procs": procs, "mau": mau,
        "truoc": truoc, "sau": sau, "file": str(BL.thu_muc_ngay(ngay) / f"{ten}.log"),
        # để nút "HỌC thành quy trình" trên báo cáo
        "seq_tu": mark["seq"], "seq_den": int(PmvState.get("pmv_trace_last_seq") or 0),
        "quy_trinh_ds": PmvProcess.objects.all(),
    })


# ─────────────────────── QUY TRÌNH — LƯU và HỌC (GĐ chốt 05/09/2026) ───────────────────────

def _qt(code):
    return PmvProcess.objects.filter(code=code).first()


def _ma_hop_le(code):
    import re as _re
    return bool(_re.fullmatch(r"[A-Z][A-Z0-9_]{1,39}", code or ""))


def quy_trinh_list(request):
    ds = []
    for p in PmvProcess.objects.all():
        ds.append({"p": p, "tk": QT.thong_ke(p), "file": QT.QUY_TRINH_DIR / f"{p.code}.md"})
    return render(request, "pmv/quy_trinh.html", {
        "ds": ds, "log_dir": str(QT.QUY_TRINH_DIR), "hom_nay": timezone.localdate().isoformat(),
    })


def quy_trinh_detail(request, code):
    p = _qt(code)
    if not p:
        messages.error(request, f"Không có quy trình {code}.")
        return redirect("pmv:quy_trinh")
    cay = QT.cay(p)
    return render(request, "pmv/quy_trinh_chi_tiet.html", {
        "p": p, "cay": cay, "tk": QT.thong_ke(p), "phas": [x[0] for x in cay],
        "khbl_choices": PmvProcessStep.Khbl.choices, "dc_choices": PmvProcessStep.DoiChieu.choices,
        "status_choices": PmvProcess.Status.choices,
        "file_md": QT.QUY_TRINH_DIR / f"{p.code}.md",
    })


@require_POST
def qt_tao(request):
    code = (request.POST.get("code") or "").strip().upper()
    name = (request.POST.get("name") or "").strip()
    if not _ma_hop_le(code) or not name:
        messages.error(request, "Mã quy trình phải là CHỮ IN/số/gạch dưới (vd BAN_HANG, HUY_HOA_DON) và phải có tên.")
        return redirect("pmv:quy_trinh")
    if _qt(code):
        messages.error(request, f"Quy trình {code} đã có.")
        return redirect("pmv:quy_trinh_detail", code=code)
    p = PmvProcess.objects.create(code=code, name=name, description=(request.POST.get("description") or "").strip())
    PmvProcessStep.objects.create(process=p, parent=None, order=10, title="Điều kiện trước")
    QT.luu_file(p)
    messages.success(request, f"Đã tạo quy trình {code} — thêm pha/bước rồi bấm LƯU.")
    return redirect("pmv:quy_trinh_detail", code=code)


@require_POST
def qt_sua(request, code):
    p = _qt(code)
    if not p:
        return redirect("pmv:quy_trinh")
    p.name = (request.POST.get("name") or p.name).strip()
    p.description = (request.POST.get("description") or "").strip()
    st = request.POST.get("status")
    if st in dict(PmvProcess.Status.choices):
        p.status = st
    p.save()
    QT.tang_ban(p)
    gateway.canh_bao("quy_trinh", f"SỬA quy trình {code} → v{p.version} ({request.user.username})")
    messages.success(request, f"Đã lưu quy trình {code} v{p.version} ra file.")
    return redirect("pmv:quy_trinh_detail", code=code)


@require_POST
def qt_xoa(request, code):
    p = _qt(code)
    if p:
        p.delete()
        gateway.canh_bao("quy_trinh", f"XÓA quy trình {code} khỏi DB (file docs/quy_trinh/{code}.md GIỮ LẠI) — {request.user.username}")
        messages.success(request, f"Đã xóa quy trình {code} khỏi DB — file docs/quy_trinh/{code}.md vẫn giữ làm lịch sử.")
    return redirect("pmv:quy_trinh")


@require_POST
def qt_luu(request, code):
    p = _qt(code)
    if not p:
        return redirect("pmv:quy_trinh")
    md, js = QT.luu_file(p)
    messages.success(request, f"Đã LƯU: {md.name} + {js.name} trong {QT.QUY_TRINH_DIR}")
    return redirect("pmv:quy_trinh_detail", code=code)


@require_POST
def qt_hoc(request):
    """HỌC quy trình từ hành vi đã đo: theo khung giờ (trang Quy trình) hoặc theo seq (báo cáo đánh dấu)."""
    code = (request.POST.get("code") or "").strip().upper()
    name = (request.POST.get("name") or "").strip()
    them_vao = (request.POST.get("them_vao") or "").strip().upper()   # thêm vào quy trình sẵn có
    if them_vao:
        p = _qt(them_vao)
        if not p:
            messages.error(request, f"Không có quy trình {them_vao}.")
            return redirect("pmv:quy_trinh")
        code = p.code
    elif not _ma_hop_le(code):
        messages.error(request, "Mã quy trình phải là CHỮ IN/số/gạch dưới (vd HUY_HOA_DON).")
        return redirect("pmv:quy_trinh")
    else:
        p = None
    try:
        if request.POST.get("seq_tu"):
            seq_tu, seq_den = int(request.POST["seq_tu"]), int(request.POST.get("seq_den") or 0) or None
            hv = QT.hanh_vi_theo_seq(seq_tu, seq_den)
            if hv:
                tu, den = hv[0].event_time - datetime.timedelta(minutes=3), hv[-1].event_time + datetime.timedelta(minutes=3)
                ch = QT.thay_doi_theo_gio(tu, den)
            else:
                ch = []
            nguon = f"HỌC từ đánh dấu seq {seq_tu}→{seq_den or '…'} ({timezone.localtime():%d/%m/%Y %H:%M}, {request.user.username})"
        else:
            tz = timezone.get_current_timezone()
            tu = timezone.make_aware(datetime.datetime.fromisoformat(request.POST["tu"]), tz)
            den = timezone.make_aware(datetime.datetime.fromisoformat(request.POST["den"]), tz)
            hv = QT.hanh_vi_theo_gio(tu, den)
            ch = QT.thay_doi_theo_gio(tu, den)
            nguon = f"HỌC từ khung {timezone.localtime(tu):%d/%m/%Y %H:%M}→{timezone.localtime(den):%H:%M} ({request.user.username})"
    except (KeyError, ValueError) as exc:
        messages.error(request, f"Khung giờ không hợp lệ: {exc}")
        return redirect("pmv:quy_trinh")
    if not hv:
        messages.warning(request, "Không có lời gọi nào trong khung này (trace tắt? khung sai?). Không tạo gì.")
        return redirect("pmv:quy_trinh")
    p = QT.hoc(code, name, hv, ch, source=nguon, process=p)
    messages.success(request, f"Đã HỌC {len(QT.gom_buoc(hv))} bước từ {len(hv)} lời gọi vào quy trình {p.code} "
                              f"(pha 'Đã học — chưa phân nhóm') — kéo từng bước vào pha đúng rồi duyệt.")
    return redirect("pmv:quy_trinh_detail", code=p.code)


def _doc_form_buoc(request, p):
    parent = None
    pid = request.POST.get("parent")
    if pid:
        parent = p.steps.filter(pk=pid, parent=None).first()
    return {
        "parent": parent,
        "title": (request.POST.get("title") or "").strip()[:150],
        "proc_name": (request.POST.get("proc_name") or "").strip()[:128],
        "action": request.POST.get("action") if request.POST.get("action") in ("ghi", "đọc", "") else "",
        "repeat": request.POST.get("repeat") == "1",
        "params": (request.POST.get("params") or "").strip(),
        "tables": (request.POST.get("tables") or "").strip(),
        "khbl": request.POST.get("khbl") if request.POST.get("khbl") in dict(PmvProcessStep.Khbl.choices) else PmvProcessStep.Khbl.LAM,
        "doi_chieu": request.POST.get("doi_chieu") if request.POST.get("doi_chieu") in dict(PmvProcessStep.DoiChieu.choices) else PmvProcessStep.DoiChieu.CHUA,
        "note": (request.POST.get("note") or "").strip(),
    }


@require_POST
def buoc_them(request, code):
    p = _qt(code)
    if not p:
        return redirect("pmv:quy_trinh")
    d = _doc_form_buoc(request, p)
    if not d["title"]:
        messages.error(request, "Chưa nhập tên pha/bước.")
        return redirect("pmv:quy_trinh_detail", code=code)
    n = p.steps.filter(parent=d["parent"]).count()
    PmvProcessStep.objects.create(process=p, order=(n + 1) * 10, **d)
    QT.tang_ban(p)
    messages.success(request, f"Đã thêm {'bước' if d['parent'] else 'pha'} “{d['title']}” — v{p.version} đã lưu file.")
    return redirect("pmv:quy_trinh_detail", code=code)


@require_POST
def buoc_sua(request, pk):
    s = PmvProcessStep.objects.filter(pk=pk).select_related("process").first()
    if not s:
        return redirect("pmv:quy_trinh")
    p = s.process
    d = _doc_form_buoc(request, p)
    if not d["title"]:
        messages.error(request, "Chưa nhập tên pha/bước.")
        return redirect("pmv:quy_trinh_detail", code=p.code)
    if s.parent_id is None:
        d["parent"] = None            # pha không thể thành bước
    elif d["parent"] is None:
        d["parent"] = s.parent        # bước phải thuộc 1 pha
    doi_pha = d["parent"] != s.parent
    for k, v in d.items():
        setattr(s, k, v)
    if doi_pha:
        s.order = (p.steps.filter(parent=d["parent"]).count() + 1) * 10
    s.save()
    QT.sap_lai(p, s.parent)
    QT.tang_ban(p)
    messages.success(request, f"Đã sửa “{s.title}” — v{p.version} đã lưu file.")
    return redirect("pmv:quy_trinh_detail", code=p.code)


@require_POST
def buoc_xoa(request, pk):
    s = PmvProcessStep.objects.filter(pk=pk).select_related("process").first()
    if not s:
        return redirect("pmv:quy_trinh")
    p, ten, parent = s.process, s.title, s.parent
    s.delete()
    QT.sap_lai(p, parent)
    QT.tang_ban(p)
    messages.success(request, f"Đã xóa “{ten}” — v{p.version} đã lưu file.")
    return redirect("pmv:quy_trinh_detail", code=p.code)


@require_POST
def buoc_doi_cho(request, pk, huong):
    s = PmvProcessStep.objects.filter(pk=pk).select_related("process").first()
    if not s:
        return redirect("pmv:quy_trinh")
    if QT.doi_cho(s, -1 if huong == "len" else 1):
        QT.tang_ban(s.process)
    return redirect("pmv:quy_trinh_detail", code=s.process.code)


@require_POST
def snapshot_create(request):
    """Chụp snapshot 1 nguồn và lưu (sandbox mặc định FULL để so trước/sau proc)."""
    source = request.POST.get("source", "sandbox")
    mode = request.POST.get("mode", "full" if source == "sandbox" else "fast")
    label = (request.POST.get("label") or "").strip() or f"{source} {timezone.localtime():%d/%m %H:%M}"
    if source not in ("pmv", "sandbox"):
        messages.error(request, "Nguồn không hợp lệ.")
        return redirect("pmv:diff")
    try:
        t0 = time.monotonic()
        data = diffmod.snapshot(source, mode=mode)
        snap = PmvSnapshot.objects.create(
            label=label, source=source, mode=("fast" if source == "pmv" else mode),
            table_count=len(data), payload=data,
        )
        messages.success(
            request,
            f"Đã chụp snapshot #{snap.pk} “{label}” — {len(data)} bảng, "
            f"{snap.mode}, {time.monotonic() - t0:.1f}s.",
        )
    except Exception as exc:
        messages.error(request, f"Chụp snapshot lỗi: {exc}")
    return redirect(f"{reverse('pmv:diff')}?a=snap:{PmvSnapshot.objects.first().pk if PmvSnapshot.objects.exists() else ''}&b=sandbox")


@require_POST
def sync_now(request):
    """Nút ⟳ SYNC: KK → Mr Giang (backup PMV thật, hút về, restore đè sandbox). ~1 phút."""
    from io import StringIO

    from django.core.management import call_command

    out = StringIO()
    try:
        call_command("sync_sandbox", stdout=out, stderr=out)
        last = PmvState.get("pmv_last_sync")
        messages.success(request, f"⟳ SYNC xong — {last}. Bảng dưới so PMV thật ↔ Sandbox ngay sau sync.")
    except Exception as exc:
        messages.error(request, f"SYNC LỖI: {exc} — xem nhật ký trang Trạng thái.")
        return redirect("pmv:status")
    return redirect(f"{reverse('pmv:diff')}?a=pmv&b=sandbox&only=1&so=1")


CATEGORY_COLORS = {
    "HĐ bán": "#B3402A", "HĐ thâu": "#C07A1A", "HĐ đổi": "#7C3AED", "Đặt cọc": "#9A3412",
    "Khách hàng": "#2E7D46", "Bảng giá": "#C9A02C", "Sản phẩm/kho": "#0F766E", "Sổ quỹ": "#1D4ED8",
    "Nhật ký ngày": "#6B7280", "Đồng bộ": "#0891B2", "HĐ điện tử": "#BE123C", "Tin nhắn": "#A16207",
    "Hệ thống": "#78716C", "Nhân viên": "#4338CA", "Báo cáo": "#57534E", "Khác": "#A8A29E",
}


def behavior_view(request):
    """Trang HÀNH VI PMVGoldRT: bộ lọc, thẻ tổng quan, thanh nhóm, dòng thời gian."""
    import datetime

    from django.core.paginator import Paginator
    from django.db.models import Count, Sum

    from .management.commands.pmv_trace import trace_status
    from .models import PmvBehavior

    today = timezone.localdate()
    d1 = request.GET.get("d1") or today.isoformat()
    d2 = request.GET.get("d2") or today.isoformat()
    try:
        start = timezone.make_aware(datetime.datetime.fromisoformat(d1))
        end = timezone.make_aware(datetime.datetime.fromisoformat(d2)) + datetime.timedelta(days=1)
    except ValueError:
        start = timezone.make_aware(datetime.datetime.combine(today, datetime.time.min))
        end = start + datetime.timedelta(days=1)
        d1 = d2 = today.isoformat()
    cat = request.GET.get("cat", "")
    src = request.GET.get("src", "")
    act = request.GET.get("act", "")
    q = (request.GET.get("q") or "").strip()
    auto = request.GET.get("auto") == "1"

    qs = PmvBehavior.objects.filter(event_time__gte=start, event_time__lt=end)
    if cat:
        qs = qs.filter(category=cat)
    if src:
        qs = qs.filter(source=src)
    if act:
        qs = qs.filter(action=act)
    if q:
        from django.db.models import Q
        qs = qs.filter(Q(proc_name__icontains=q) | Q(text__icontains=q) | Q(host__icontains=q))

    base = PmvBehavior.objects.filter(event_time__gte=start, event_time__lt=end)
    by_cat = list(base.values("category").annotate(n=Count("id"), calls=Sum("exec_delta")).order_by("-n"))
    max_n = max((c["n"] for c in by_cat), default=1)
    for c in by_cat:
        c["pct"] = int(c["n"] * 100 / max_n)
        c["color"] = CATEGORY_COLORS.get(c["category"], "#A8A29E")
    top_procs = list(base.exclude(proc_name="").values("proc_name").annotate(n=Count("id"), calls=Sum("exec_delta")).order_by("-n")[:12])
    cards = {
        "trace_rows": base.filter(source="TRACE").count(),
        "stats_rows": base.filter(source="STATS").count(),
        "ghi": base.filter(action="ghi").count(),
        "hd": base.filter(category__in=["HĐ bán", "HĐ thâu", "HĐ đổi"], action="ghi", source="TRACE").count(),
        "hosts": list(base.exclude(host="").order_by().values_list("host", flat=True).distinct()),
    }
    page = Paginator(qs, 100).get_page(request.GET.get("page"))
    for r in page:
        r.color = CATEGORY_COLORS.get(r.category, "#A8A29E")

    try:
        tstat = trace_status()
    except Exception as exc:
        tstat = {"error": str(exc)}
    state = {s.key: s.value for s in PmvState.objects.filter(
        key__in=["pmv_behavior_last", "pmv_trace_started", "pmv_trace_stopped", BL.THAY_DOI_LAST_KEY])}
    state["log_dir"] = str(BL.LOG_DIR)

    return render(request, "pmv/behavior.html", {
        "d1": d1, "d2": d2, "cat": cat, "src": src, "act": act, "q": q, "auto": auto,
        "categories": list(CATEGORY_COLORS.keys()), "colors": CATEGORY_COLORS,
        "by_cat": by_cat, "top_procs": top_procs, "cards": cards, "page": page,
        "tstat": tstat, "state": state, "total": qs.count(),
        "qstring": request.GET.urlencode(),
    })


@require_POST
def trace_toggle(request, action):
    from django.core.management import call_command

    if action not in ("start", "stop"):
        messages.error(request, "Hành động không hợp lệ.")
        return redirect("pmv:behavior")
    try:
        call_command("pmv_trace", action)
        messages.success(request, "Đã BẬT trace chi tiết trên SQL KK." if action == "start" else "Đã TẮT trace chi tiết.")
    except Exception as exc:
        messages.error(request, f"Trace {action} lỗi: {exc}")
    return redirect("pmv:behavior")


@require_POST
def collect_now(request):
    from io import StringIO

    from django.core.management import call_command

    out = StringIO()
    try:
        call_command("collect_pmv_behavior", stdout=out, stderr=out)
        messages.success(request, out.getvalue().strip() or "Đã thu thập.")
    except Exception as exc:
        messages.error(request, f"Thu thập lỗi: {exc}")
    return redirect(f"{reverse('pmv:behavior')}?{request.POST.get('qstring', '')}")


@require_POST
def snapshot_delete(request, pk):
    PmvSnapshot.objects.filter(pk=pk).delete()
    messages.success(request, f"Đã xóa snapshot #{pk}.")
    return redirect("pmv:diff")


# ─────────────────────────── KHO LỊCH SỬ PMV_KH2_HIST (Phase 3, 06/09/2026) ───────────────────────────
def _hist_ctx():
    from . import hist_sync as H

    try:
        rows = H.status()
        tt = H.tom_tat(rows)
    except Exception as exc:
        rows, tt = [], {"loi": str(exc)}
    return {"rows": rows, "tt": tt, "dang_chay": H.dang_chay(),
            "log": PmvState.get("hist_sync_log") or "",
            "void_days": settings.PMV_HIST_VOID_DAYS, "hist_db": settings.PMV_HIST_DB,
            "hist_host": settings.PMV_HIST_MSSQL}


def hist_view(request):
    """Trang báo cáo kho lịch sử: tóm tắt · bảng từng bảng · nút Đồng bộ/Đối soát/Chép lại (chạy nền)."""
    return render(request, "pmv/hist.html", _hist_ctx())


def hist_bang(request):
    """Mảnh bảng để trang poll 4s khi đang có lượt chạy nền."""
    return render(request, "pmv/_hist_bang.html", _hist_ctx())


@require_POST
def hist_action(request):
    from . import hist_sync as H

    mode = request.POST.get("mode", "sync")
    if mode not in ("sync", "reconcile", "backfill"):
        mode = "sync"
    table = (request.POST.get("table") or "").strip()
    ten = {"sync": "Đồng bộ", "reconcile": "Đối soát",
           "backfill": f"Chép lại {table or 'TẤT CẢ'}"}[mode]
    if H.chay_nen(mode, [table] if table else None):
        messages.success(request, f"▶ {ten} đã bắt đầu chạy nền — bảng dưới tự cập nhật.")
    else:
        messages.warning(request, "Đang có lượt chạy khác — chờ xong rồi bấm lại.")
    return redirect("pmv:hist")
