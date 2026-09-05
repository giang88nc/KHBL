r"""
QUY TRÌNH NGHIỆP VỤ PMVGoldRT — LƯU và HỌC (GĐ chốt 05/09/2026).

- HỌC: từ các lời gọi trace trong 1 khung (event_seq hoặc khung giờ) → cây quy trình nháp
  (1 pha "Đã học — chưa phân nhóm", lời gọi liên tiếp cùng proc gom thành 1 bước ×N,
  bỏ sp_procedure_params_managed/NOISE/lời gọi của KHBL) + bảng đổi trong khung từ pmv_change_logs.
- LƯU: mỗi lần đổi ghi lại  docs\quy_trinh\<CODE>.md  (người đọc, có sơ đồ mermaid)
  và  docs\quy_trinh\<CODE>.json  (máy đọc) — repo giữ lịch sử phiên bản.
- Quy trình chuẩn BÁN HÀNG seed từ thao tác thật 05/09/2026 (xem docs/LUONG_BAN_HANG_PMV_20260905.md).
"""
import json
from pathlib import Path

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .classify import NOISE
from .models import PmvBehavior, PmvChange, PmvProcess, PmvProcessStep

QUY_TRINH_DIR = Path(settings.BASE_DIR) / "docs" / "quy_trinh"
_BO_QUA = {"sp_procedure_params_managed", "sp_prepexec", "sp_unprepare", "sp_execute", "sp_prepare"}


# ─────────────────────────── đọc cây ───────────────────────────
def cay(process):
    """[(pha, [bước...]), ...] theo order."""
    steps = list(process.steps.all().order_by("order", "id"))
    phas = [s for s in steps if s.parent_id is None]
    out = []
    for p in phas:
        out.append((p, [s for s in steps if s.parent_id == p.pk]))
    return out


def thong_ke(process):
    buoc = [s for s in process.steps.all() if s.parent_id is not None]
    return {
        "pha": sum(1 for s in process.steps.all() if s.parent_id is None),
        "buoc": len(buoc),
        "ghi": sum(1 for s in buoc if s.action == "ghi"),
        "lam": sum(1 for s in buoc if s.khbl == PmvProcessStep.Khbl.LAM),
        "kiem": sum(1 for s in buoc if s.khbl == PmvProcessStep.Khbl.KIEM),
        "bo": sum(1 for s in buoc if s.khbl == PmvProcessStep.Khbl.BO),
        "khop": sum(1 for s in buoc if s.doi_chieu == PmvProcessStep.DoiChieu.KHOP),
        "lech": sum(1 for s in buoc if s.doi_chieu == PmvProcessStep.DoiChieu.LECH),
    }


def sap_lai(process, parent=None):
    """Đánh lại order 10,20,30… cho anh em cùng cha (chèn/đổi chỗ không bị trùng)."""
    qs = process.steps.filter(parent=parent).order_by("order", "id")
    for i, s in enumerate(qs, 1):
        if s.order != i * 10:
            PmvProcessStep.objects.filter(pk=s.pk).update(order=i * 10)


def doi_cho(step, huong):
    """Đổi chỗ với anh em liền kề (huong = -1 lên / +1 xuống). Trả True nếu đổi."""
    anh_em = list(step.process.steps.filter(parent=step.parent).order_by("order", "id"))
    i = next(k for k, s in enumerate(anh_em) if s.pk == step.pk)
    j = i + huong
    if j < 0 or j >= len(anh_em):
        return False
    a, b = anh_em[i], anh_em[j]
    a.order, b.order = b.order, a.order
    if a.order == b.order:
        b.order += huong
    PmvProcessStep.objects.filter(pk=a.pk).update(order=a.order)
    PmvProcessStep.objects.filter(pk=b.pk).update(order=b.order)
    sap_lai(step.process, step.parent)
    return True


# ─────────────────────────── LƯU ra file ───────────────────────────
def _md(process):
    tk = thong_ke(process)
    lines = [f"# QUY TRÌNH {process.name} (`{process.code}`) — v{process.version}",
             "",
             f"> Trạng thái: **{process.get_status_display()}** · cập nhật {timezone.localtime(process.updated_at):%d/%m/%Y %H:%M} · "
             f"{tk['pha']} pha · {tk['buoc']} bước ({tk['ghi']} ghi) · KHBL làm {tk['lam']} · cần kiểm {tk['kiem']} · bỏ {tk['bo']}",
             ""]
    if process.description:
        lines += [process.description, ""]
    if process.source:
        lines += ["Nguồn học: " + process.source, ""]
    lines += ["```mermaid", "flowchart LR"]
    phas = cay(process)
    for i, (p, _) in enumerate(phas):
        lines.append(f'  P{i}["{i}. {p.title}"]')
    for i in range(len(phas) - 1):
        lines.append(f"  P{i} --> P{i + 1}")
    lines += ["```", ""]
    for i, (p, buoc) in enumerate(phas):
        lines += [f"## {i}. {p.title}", ""]
        if p.note:
            lines += [p.note, ""]
        if buoc:
            lines += ["| # | Bước | Proc | Ghi/đọc | Tham số quan trọng | Bảng đổi | KHBL | Đối chiếu | Ghi chú |",
                      "|---|---|---|---|---|---|---|---|---|"]
            for k, s in enumerate(buoc, 1):
                proc = f"`{s.proc_name}`" + (" ×N" if s.repeat else "") if s.proc_name else "—"
                lines.append("| " + " | ".join([
                    f"{i}.{k}", s.title, proc, s.action or "—", _o(s.params), _o(s.tables),
                    s.get_khbl_display(), s.get_doi_chieu_display(), _o(s.note)]) + " |")
            lines.append("")
    return "\n".join(lines) + "\n"


def _o(s):
    return " ".join((s or "").split()).replace("|", "\\|") or "—"


def _json(process):
    return {
        "code": process.code, "name": process.name, "description": process.description,
        "status": process.status, "version": process.version, "source": process.source,
        "updated_at": timezone.localtime(process.updated_at).isoformat(),
        "phases": [{
            "title": p.title, "note": p.note,
            "steps": [{k: getattr(s, k) for k in ("title", "proc_name", "action", "repeat", "params",
                                                   "tables", "khbl", "doi_chieu", "note")} for s in buoc],
        } for p, buoc in cay(process)],
    }


def luu_file(process):
    """Ghi <CODE>.md + <CODE>.json. Trả (path_md, path_json)."""
    QUY_TRINH_DIR.mkdir(parents=True, exist_ok=True)
    md = QUY_TRINH_DIR / f"{process.code}.md"
    js = QUY_TRINH_DIR / f"{process.code}.json"
    md.write_text(_md(process), encoding="utf-8", newline="\n")
    js.write_text(json.dumps(_json(process), ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
    return md, js


def tang_ban(process):
    """Mỗi lần sửa: +1 version rồi LƯU file."""
    PmvProcess.objects.filter(pk=process.pk).update(version=process.version + 1, updated_at=timezone.now())
    process.refresh_from_db()
    return luu_file(process)


# ─────────────────────────── HỌC từ hành vi ───────────────────────────
def gom_buoc(hanh_vi):
    """Lời gọi trace (đã sắp theo seq) → list dict bước: gom liên tiếp cùng proc thành 1 (×N),
    bỏ lớp bọc/DeriveParameters/NOISE/lời gọi của KHBL."""
    out = []
    for b in hanh_vi:
        n = (b.proc_name or "").strip()
        if not n or n in _BO_QUA or n in NOISE or b.host == "KHBL":
            continue
        if out and out[-1]["proc_name"] == n:
            out[-1]["so_lan"] += 1
            continue
        out.append({"proc_name": n, "action": b.action or "đọc", "so_lan": 1, "category": b.category,
                    "params": " ".join((b.text or "").split())[:400]})
    return out


def hoc(code, name, hanh_vi, changes=(), source="", process=None):
    """Tạo (hoặc thêm vào) quy trình từ hành vi đã đo. Trả process. Bước mới nằm trong 1 pha
    'Đã học — chưa phân nhóm' để GĐ kéo vào pha đúng."""
    buoc = gom_buoc(hanh_vi)
    bang = sorted({c.table for c in changes})
    with transaction.atomic():
        if process is None:
            process, _ = PmvProcess.objects.get_or_create(
                code=code, defaults={"name": name or code, "status": PmvProcess.Status.DRAFT})
        if source:
            process.source = (process.source + "\n" if process.source else "") + source
            process.save(update_fields=["source"])
        n_pha = process.steps.filter(parent=None).count()
        pha = PmvProcessStep.objects.create(
            process=process, parent=None, order=(n_pha + 1) * 10,
            title=f"Đã học {timezone.localtime():%d/%m %H:%M} — chưa phân nhóm",
            note="Bảng đổi trong khung: " + (", ".join(bang) if bang else "(không dò được)"))
        for i, s in enumerate(buoc, 1):
            PmvProcessStep.objects.create(
                process=process, parent=pha, order=i * 10,
                title=f"{s['category']}: {s['proc_name']}" + (f" ×{s['so_lan']}" if s["so_lan"] > 1 else ""),
                proc_name=s["proc_name"], action=s["action"], repeat=s["so_lan"] > 1,
                params=s["params"], tables="", khbl=PmvProcessStep.Khbl.KIEM if s["action"] == "ghi" else PmvProcessStep.Khbl.LAM)
    luu_file(process)
    return process


def hanh_vi_theo_seq(seq_tu, seq_den=None):
    qs = PmvBehavior.objects.filter(source=PmvBehavior.Source.TRACE, event_seq__gt=seq_tu)
    if seq_den is not None:
        qs = qs.filter(event_seq__lte=seq_den)
    return list(qs.order_by("event_seq", "id"))


def hanh_vi_theo_gio(tu, den):
    return list(PmvBehavior.objects.filter(source=PmvBehavior.Source.TRACE, event_time__gte=tu, event_time__lt=den)
                .order_by("event_seq", "id"))


def thay_doi_theo_gio(tu, den):
    return list(PmvChange.objects.filter(window_end__gte=tu, window_start__lte=den))


# ─────────────────────────── QUY TRÌNH CHUẨN BÁN HÀNG (seed) ───────────────────────────
# Dữ liệu FULL nằm ở quy_trinh_ban_hang.py (GĐ chốt 05/09/2026) — đổi nội dung quy trình chuẩn ở đó.
from .quy_trinh_ban_hang import BAN_HANG  # noqa: E402


def seed_ban_hang(ghi_de=False):
    """Tạo quy trình chuẩn BÁN HÀNG (idempotent — có rồi thì thôi, ghi_de=True mới dựng lại)."""
    d = BAN_HANG
    with transaction.atomic():
        p = PmvProcess.objects.filter(code=d["code"]).first()
        if p and not ghi_de:
            return p, False
        if p:
            p.steps.all().delete()
            p.name, p.description, p.source = d["name"], d["description"], d["source"]
            p.version += 1
            p.save()
        else:
            p = PmvProcess.objects.create(code=d["code"], name=d["name"], description=d["description"],
                                          source=d["source"], status=PmvProcess.Status.APPROVED)
        for i, (ten, ghi_chu, buoc) in enumerate(d["phases"]):
            pha = PmvProcessStep.objects.create(process=p, parent=None, order=(i + 1) * 10, title=ten, note=ghi_chu)
            for k, (title, proc, act, rep, params, tables, khbl, dc, note) in enumerate(buoc, 1):
                PmvProcessStep.objects.create(process=p, parent=pha, order=k * 10, title=title, proc_name=proc,
                                              action=act, repeat=rep, params=params, tables=tables,
                                              khbl=khbl, doi_chieu=dc, note=note)
    luu_file(p)
    return p, True
