r"""
NHẬT KÝ HÀNH VI PMVGoldRT RA FILE + DÒ THAY ĐỔI TRẠNG THÁI SQL KK (GĐ duyệt 05/09/2026).

Bước 1 — mỗi lời gọi proc/câu SQL bắt được từ trace SQL KK (bảng pmv_behavior_logs) được
ghi thêm 1 dòng text vào  logs\pmv\<YYYY-MM-DD>\ALL.log  và file của NHÓM nghiệp vụ
(ban_hang.log, thau_vang.log, khach_hang.log...). Tham số ghi ĐẦY ĐỦ, 1 dòng/lời gọi.
Lời gọi của chính KHBL (login kimhanh2) gắn nhãn máy "KHBL" để so cùng dòng thời gian.

Bước 2 — mỗi chu kỳ thu thập (2 phút) chụp FULL trạng thái KK (số dòng + checksum 251 bảng,
đo 05/09: ~1,5s, WITH (NOLOCK)) → so với lần trước → bảng nào đổi ghi vào thay_doi.log kèm
các proc GHI bắt được trong cùng khung → thành bản đồ proc → bảng cho GĐ3.
Hạn chế: BINARY_CHECKSUM bỏ qua cột text/ntext/image/xml; số dòng từ sys.partitions có thể
trễ vài giây; khung 2 phút có thể gom nhiều thao tác — cần chế độ ĐÁNH DẤU TRƯỚC/SAU để tách.

Giữ file 90 ngày, giữ bảng MySQL 30 ngày (don_file / don_db, chạy 1 lần/ngày trong collect).
"""
import json
import re
import shutil
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

from django.conf import settings
from django.utils import timezone

from . import diff as diffmod
from .classify import _CATEGORY_RULES
from .models import PmvBehavior, PmvChange, PmvState

LOG_DIR = Path(settings.BASE_DIR) / "logs" / "pmv"
GIU_FILE_NGAY = 90
GIU_DB_NGAY = 30

# nhóm nghiệp vụ (classify.py) → tên file ASCII
NHOM_FILE = {
    "HĐ bán": "ban_hang", "HĐ thâu": "thau_vang", "HĐ đổi": "hd_doi", "Đặt cọc": "dat_coc",
    "Khách hàng": "khach_hang", "Bảng giá": "bang_gia", "Sản phẩm/kho": "san_pham_kho",
    "Sổ quỹ": "so_quy", "Nhật ký ngày": "nhat_ky_ngay", "Đồng bộ": "dong_bo",
    "HĐ điện tử": "hd_dien_tu", "Tin nhắn": "tin_nhan", "Hệ thống": "he_thong",
    "Nhân viên": "nhan_vien", "Báo cáo": "bao_cao", "Khác": "khac",
}
assert {c for _, c in _CATEGORY_RULES} <= set(NHOM_FILE), "classify.py có nhóm chưa khai tên file"

SNAP_KEY = "pmv_kk_snapshot"          # JSON vân tay FULL lần chụp gần nhất
SNAP_LUC_KEY = "pmv_kk_snapshot_luc"  # ISO giờ máy web lúc chụp
THAY_DOI_LAST_KEY = "pmv_thay_doi_last"
DON_LAST_KEY = "pmv_log_don_last"


# ─────────────────────────── định dạng 1 dòng ───────────────────────────
def may_cua(b):
    """Máy phát sinh: KK/QQ (host trong trace) hoặc 'KHBL' khi là chính webapp."""
    if (b.login or "") and b.login == settings.PMV_MSSQL_USER:
        return "KHBL"
    return (b.host or "").strip() or "-"


def _mot_dong(s):
    return " ".join((s or "").split())


def dong(b):
    """1 dòng text cho 1 hành vi. Mọi xuống dòng trong tham số ép về 1 dòng."""
    t = timezone.localtime(b.event_time)
    gio = f"{t:%H:%M:%S}.{t.microsecond // 1000:03d}"
    if b.source == PmvBehavior.Source.STATS:
        chi_tiet = f"(+{b.exec_delta or 0} lần gọi — bộ đếm SQL, không có tham số)"
        return (f"{gio} STATS | {'-':5} | - | {b.category} | {b.action} | {b.proc_name or '-'} "
                f"| - | - | {chi_tiet}")
    ms = f"{b.duration_ms}ms" if b.duration_ms is not None else "-"
    rw = f"r={b.reads if b.reads is not None else '-'} w={b.writes if b.writes is not None else '-'} n={b.row_count if b.row_count is not None else '-'}"
    return (f"{gio} TRACE | {may_cua(b):5} | {b.login or '-'} | {b.category} | {b.action} "
            f"| {b.proc_name or '-'} | {ms} | {rw} | {_mot_dong(b.text)}")


def thu_muc_ngay(ngay):
    return LOG_DIR / ngay.isoformat()


def _append(path, lines):
    if not lines:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")


def ghi(rows):
    """Ghi các PmvBehavior vừa thu vào ALL.log + file nhóm theo NGÀY sự kiện. Trả số dòng."""
    theo_file = {}
    for b in sorted(rows, key=lambda x: (x.event_time, x.event_seq or 0)):
        ngay = timezone.localtime(b.event_time).date()
        line = dong(b)
        theo_file.setdefault((ngay, "ALL"), []).append(line)
        theo_file.setdefault((ngay, NHOM_FILE.get(b.category, "khac")), []).append(line)
    n = 0
    for (ngay, ten), lines in theo_file.items():
        _append(thu_muc_ngay(ngay) / f"{ten}.log", lines)
        if ten == "ALL":
            n += len(lines)
    return n


def xuat_lai(ngay):
    """Xóa các file .log của 1 ngày rồi dựng lại từ bảng MySQL (hành vi + thay đổi)."""
    d = thu_muc_ngay(ngay)
    if d.exists():
        for f in d.glob("*.log"):
            if not f.name.startswith("danh_dau_"):
                f.unlink()
    tz = timezone.get_current_timezone()
    start = timezone.make_aware(timezone.datetime.combine(ngay, timezone.datetime.min.time()), tz)
    end = start + timedelta(days=1)
    rows = list(PmvBehavior.objects.filter(event_time__gte=start, event_time__lt=end).order_by("event_time", "id"))
    n = ghi(rows)
    thay_doi = PmvChange.objects.filter(window_end__gte=start, window_end__lt=end, mark="").order_by("window_end", "id")
    for (ws, we), grp in _gom_theo_khung(thay_doi).items():
        ghi_thay_doi(ngay, dong_thay_doi(ws, we, grp, grp[0].procs if grp else ""))
    return n, len(thay_doi)


def _gom_theo_khung(changes):
    out = {}
    for c in changes:
        out.setdefault((c.window_start, c.window_end), []).append(c)
    return out


# ─────────────────────────── dọn dẹp ───────────────────────────
def don_file(giu_ngay=GIU_FILE_NGAY, hom_nay=None):
    """Xóa thư mục ngày cũ hơn giu_ngay. Trả list thư mục đã xóa."""
    hom_nay = hom_nay or timezone.localdate()
    moc = hom_nay - timedelta(days=giu_ngay)
    xoa = []
    if not LOG_DIR.exists():
        return xoa
    for d in LOG_DIR.iterdir():
        if not d.is_dir() or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", d.name):
            continue
        if date.fromisoformat(d.name) < moc:
            shutil.rmtree(d, ignore_errors=True)
            xoa.append(d.name)
    return xoa


def don_db(giu_ngay=GIU_DB_NGAY, bay_gio=None):
    moc = (bay_gio or timezone.now()) - timedelta(days=giu_ngay)
    n1, _ = PmvBehavior.objects.filter(collected_at__lt=moc).delete()
    n2, _ = PmvChange.objects.filter(created_at__lt=moc).delete()
    return n1, n2


def don_hang_ngay():
    """Gọi mỗi chu kỳ; chỉ thật sự dọn 1 lần/ngày."""
    hom_nay = timezone.localdate().isoformat()
    if PmvState.get(DON_LAST_KEY) == hom_nay:
        return None
    kq = (don_file(), don_db())
    PmvState.set(DON_LAST_KEY, hom_nay)
    return kq


# ─────────────────────────── Bước 2: dò thay đổi ───────────────────────────
def proc_ghi_trong(rows):
    """Đếm proc GHI trong các hành vi vừa thu (TRACE ưu tiên, STATS bù khi trace tắt)."""
    c = Counter()
    for b in rows:
        if b.action != "ghi" or not b.proc_name:
            continue
        c[b.proc_name] += (b.exec_delta or 1) if b.source == PmvBehavior.Source.STATS else 1
    return c


def chuoi_proc(counter):
    return " ".join(f"{k}({v})" for k, v in sorted(counter.items(), key=lambda kv: (-kv[1], kv[0])))


def bang_doi(prev, snap):
    return [r for r in diffmod.diff(prev, snap) if r["status"] != "same"]


def dong_thay_doi(ws, we, changes, procs):
    """Khối text cho thay_doi.log: 1 dòng tóm tắt + mỗi bảng 1 dòng thụt vào."""
    ws, we = timezone.localtime(ws), timezone.localtime(we)
    lines = [f"{ws:%H:%M:%S} → {we:%H:%M:%S} | {len(changes)} bảng đổi | proc ghi trong khung: {procs or '(không bắt được — trace tắt?)'}"]
    for c in changes:
        ra = c.rows_before if c.rows_before is not None else "-"
        rb = c.rows_after if c.rows_after is not None else "-"
        d = f"{c.delta:+d}" if c.delta else ("0" if c.delta == 0 else "-")
        lines.append(f"    {c.table:32} {ra!s:>8} → {rb!s:<8} {d:>5}  {'checksum đổi' if c.cs_changed else ''}".rstrip())
    return lines


def ghi_thay_doi(ngay, lines, ten="thay_doi"):
    _append(thu_muc_ngay(ngay) / f"{ten}.log", lines)


def tao_pmv_change(ws, we, rows_diff, procs, mark=""):
    objs = [PmvChange(window_start=ws, window_end=we, table=r["tbl"], rows_before=r["rows_a"],
                      rows_after=r["rows_b"], delta=r["delta"], cs_changed=r["cs_changed"],
                      procs=procs, mark=mark) for r in rows_diff]
    PmvChange.objects.bulk_create(objs, batch_size=500)
    return objs


def chup_kk():
    """Vân tay FULL của KK (rows + checksum). ~1,5s, chỉ đọc NOLOCK."""
    return diffmod.snapshot("pmv", mode="full", force=True)


def do_thay_doi(rows_vua_thu, snap=None):
    """So KK(t) với KK(t−1 chu kỳ). Lần đầu chỉ lấy mốc. Trả list PmvChange đã ghi."""
    now = timezone.now()
    snap = snap if snap is not None else chup_kk()
    prev_raw = PmvState.get(SNAP_KEY)
    prev_luc = PmvState.get(SNAP_LUC_KEY)
    PmvState.set(SNAP_KEY, json.dumps(snap))
    PmvState.set(SNAP_LUC_KEY, now.isoformat())
    if not prev_raw:
        return []
    prev = json.loads(prev_raw)
    ws = timezone.datetime.fromisoformat(prev_luc) if prev_luc else now
    khac = bang_doi(prev, snap)
    if not khac:
        PmvState.set(THAY_DOI_LAST_KEY, f"{timezone.localtime(now):%d/%m/%Y %H:%M} | 0 bảng đổi")
        return []
    procs = chuoi_proc(proc_ghi_trong(rows_vua_thu))
    objs = tao_pmv_change(ws, now, khac, procs)
    ghi_thay_doi(timezone.localtime(now).date(), dong_thay_doi(ws, now, objs, procs))
    PmvState.set(THAY_DOI_LAST_KEY,
                 f"{timezone.localtime(now):%d/%m/%Y %H:%M} | {len(khac)} bảng đổi | {procs[:120]}")
    return objs


def dat_moc(snap, luc=None):
    """Đặt lại mốc so sánh (sau SYNC = baseline)."""
    PmvState.set(SNAP_KEY, json.dumps(snap))
    PmvState.set(SNAP_LUC_KEY, (luc or timezone.now()).isoformat())


# ─────────────────────────── mức DÒNG cho chế độ ĐÁNH DẤU ───────────────────────────
# 30/251 bảng có cột mốc thời gian (đo 05/09/2026) → khi bảng đổi, kéo các dòng có mốc
# trong khung để xem GIÁ TRỊ. Bảng không có cột mốc chỉ báo ở mức bảng.
COT_MOC = ("TrnDateTime_Upd", "CreatedDate", "UpdatedDate", "ModifiedDate", "DateLog", "TrnDateTime", "LastUpdate")
_COT_AN = ("password", "pwd", "matkhau")


def cot_moc_kk():
    """{bảng: [cột mốc]} đọc từ INFORMATION_SCHEMA của KK (1 truy vấn nhẹ)."""
    from .gateway import pmv_read

    ds = ",".join(f"'{c}'" for c in COT_MOC)
    rows = pmv_read(
        "SELECT TABLE_NAME, COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS "
        f"WHERE DATA_TYPE IN ('datetime','smalldatetime') AND COLUMN_NAME IN ({ds})",
        tag="danh_dau", audit=False)
    out = {}
    for r in rows:
        out.setdefault(r["TABLE_NAME"], []).append(r["COLUMN_NAME"])
    return out


def mau_dong_doi(bang, cols, tu_luc, limit=30):
    """Các dòng của `bang` có cột mốc ≥ tu_luc (giờ máy web − biên an toàn). Trả list dict
    đã ép chuỗi, bỏ None và cột nhạy cảm. Tên bảng/cột lấy từ INFORMATION_SCHEMA nên an toàn."""
    from .gateway import pmv_read

    if not cols or not re.fullmatch(r"[A-Za-z0-9_]+", bang):
        return []
    dk = " OR ".join(f"[{c}] >= CAST(? AS datetime)" for c in cols)
    if timezone.is_aware(tu_luc):                   # máy KK ghi GIỜ VN — mốc aware (vd UTC) phải quy về giờ VN (sửa 20/09/2026)
        tu_luc = timezone.localtime(tu_luc)
    moc = tu_luc.strftime("%Y-%m-%dT%H:%M:%S")     # ISO có 'T' — SQL Server hiểu bất kể ngôn ngữ phiên
    rows = pmv_read(f"SELECT TOP {int(limit)} * FROM [{bang}] WITH (NOLOCK) WHERE {dk} ORDER BY [{cols[0]}] DESC",
                    tuple(moc for _ in cols), tag="danh_dau", audit=False)
    out = []
    for r in rows:
        out.append({k: str(v) for k, v in r.items()
                    if v is not None and v != "" and not any(a in k.lower() for a in _COT_AN)})
    return out
