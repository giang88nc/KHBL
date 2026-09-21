# -*- coding: utf-8 -*-
"""
XUẤT DANH SÁCH KHÁCH của trang /thau-vao-2/ (GĐ chốt 10/09/2026) — hai tệp, cùng một danh sách:

* ⬇ XUẤT EXCEL → tệp .xlsx đúng khuôn "NCC_NHAP_CHUAN_KH2.xlsx" GĐ đưa, để nhập thẳng vào danh mục nhà cung cấp.
  11 cột: STT · Mã NCC · Tên NCC · Loại NCC · SĐT · ĐỊA CHỈ · Email · Mã số thuế · CCCD · Ghi chú · NGÀY TẠO.
  ⚠ Cột "Ghi chú" là SỐ TIỀN CHUYỂN KHOẢN tính theo NGHÌN ĐỒNG. Đối chiếu 8/8 khách trong tệp mẫu với số tiền
  thật trên máy KK: 82200 ↔ 82.200.000 đ, 13612 ↔ 13.612.000 đ… nên chia 1000 và làm tròn về số nguyên.
  STT · Mã NCC · Email · Mã số thuế để TRỐNG y như mẫu (phần mềm bên kia tự sinh).

* 🪪 IN CCCD → tệp .docx: mỗi khách MỘT DÒNG gồm ảnh CCCD mặt trước và mặt sau, in ra ĐÚNG CỠ THẬT
  85,6 × 53,98 mm (khổ ID-1 của thẻ căn cước) nên đặt thẻ lên tờ in là trùng khít. Mỗi trang đúng 4 khách,
  quá thì tự sang trang. Dòng chữ trên mỗi khách chỉ gồm TÊN · CCCD (GĐ chốt 10/09/2026).

Danh sách khách lấy từ chính bộ lọc đang xem trên trang (khoảng ngày · phương thức · khách · trạng thái), gom theo
khách chứ không theo phiếu: một khách bán nhiều lượt trong kỳ chỉ ra MỘT dòng, tiền chuyển khoản cộng dồn.
"""
import datetime as dt
import logging
from decimal import Decimal
from io import BytesIO

from django.contrib.auth.decorators import login_not_required
from django.http import HttpResponse
from django.views.decorators.http import require_GET

from apps.pmv import money as M
from apps.pmv.client import PmvClient

from . import customer as C
from . import thau_payments as TP

logger = logging.getLogger(__name__)

CCCD_RONG_MM = 85.6          # khổ chuẩn ID-1 của thẻ căn cước
CCCD_CAO_MM = 53.98
COT = ("STT", "Mã NCC", "Tên NCC", "Loại NCC", "SĐT", "ĐỊA CHỈ", "Email", "Mã số thuế", "CCCD", "Ghi chú", "NGÀY TẠO")
TIEN_FMT = '_(* #,##0_);_(* \\(#,##0\\);_(* "-"??_);_(@_)'      # đúng định dạng ô Ghi chú trong tệp mẫu


def _khoang(request):
    """Khoảng ngày đang xem trên trang, giới hạn 31 ngày như phần đối soát."""
    from .views_thau import _ngay

    hom_nay = dt.date.today().isoformat()
    d1, d2 = _ngay(request.GET.get("d1"), hom_nay), _ngay(request.GET.get("d2"), hom_nay)
    if d1 > d2:
        d1, d2 = d2, d1
    if (dt.date.fromisoformat(d2) - dt.date.fromisoformat(d1)).days > 31:
        raise ValueError("Chọn khoảng ngày tối đa 31 ngày.")
    return d1, d2


def khach_trong_ds(request):
    """Danh sách KHÁCH của bộ lọc đang xem, mỗi khách một dòng.

    Trả list dict: cust_id · ten · dt · dia_chi · cccd · tien_ck · ngay (lượt bán đầu tiên trong kỳ).
    Điều kiện lọc lặp lại đúng như thau_list.listing — hai nơi cùng đọc một bộ tham số trên URL nên danh sách
    xuất ra luôn khớp với những gì người dùng đang nhìn thấy.
    """
    d1, d2 = _khoang(request)
    orders, _live = TP.inspect(d1, d2)

    method = request.GET.get("method", "bank")
    if method not in ("all", "cash", "bank"):
        method = "bank"
    tim, trang_thai = request.GET.get("khach", "").strip(), request.GET.get("trang_thai", "")
    tt_ck = request.GET.get("payment_status", "")
    from .thau_list import loc_nghiep_vu
    nghiep_vu = loc_nghiep_vu(request)      # 19/09/2026: gồm cả hóa đơn bán-đổi tiệm trả khách (GĐ chốt)

    chon = []
    for o in orders:
        cach = "bank" if o["required"] > 0 else "cash"
        if method != "all" and cach != method:
            continue
        if nghiep_vu and o.get("nghiep_vu") != nghiep_vu:
            continue
        if tt_ck and o.get("payment_status") != tt_ck:
            continue
        if trang_thai and not any(m["Status"] == trang_thai and str(m["IsDel"]) == "0" for m in o["members"]):
            continue
        if tim and not any(tim.casefold() in str(m.get(k, "")).casefold()
                           for m in o["members"] for k in ("CustName", "Phone", "CMND")):
            continue
        chon.append(o)
    if not chon:
        return []

    # hoa_don_loc không trả CustID → lấy map TrnID→CustID bằng MỘT truy vấn chỉ-đọc trên máy KK
    c = PmvClient("kk", tag="thau_xuat")
    ids = [t for o in chon for t in o["ids"]]
    dau = dt.date.fromisoformat(d1).isoformat()
    cuoi = (dt.date.fromisoformat(d2) + dt.timedelta(days=1)).isoformat()
    ma_kh = {r["TrnID"]: r["CustID"] for r in c.query(      # SQL Server 2005: lọc bằng khoảng, không có kiểu date
        "SELECT TrnID, CustID FROM TRN_RT_BUYGOLD WITH (NOLOCK) WHERE CreatedDate>=? AND CreatedDate<?",
        (dau, cuoi)) if r["TrnID"] in set(ids)}

    gom = {}
    for o in chon:
        # hóa đơn bán-đổi không nằm trong TRN_RT_BUYGOLD → mã khách đã có sẵn trên dòng (cust_id, 19/09/2026)
        cust = next((ma_kh.get(t) for t in o["ids"] if ma_kh.get(t)), "") or o.get("cust_id") or ""
        m0 = o["members"][0]
        khoa = cust or ("ten:" + str(m0.get("CustName") or ""))
        g = gom.setdefault(khoa, {"cust_id": cust, "ten": (m0.get("CustName") or "").strip(),
                                  "dt": (m0.get("Phone") or "").strip(), "cccd": (m0.get("CMND") or "").strip(),
                                  "dia_chi": "", "tien_ck": Decimal(0), "ngay": None})
        g["tien_ck"] += M.dec(o["required"])
        ngay = m0.get("CreatedDate")
        if ngay and (g["ngay"] is None or str(ngay) < str(g["ngay"])):
            g["ngay"] = ngay

    # địa chỉ + số căn cước chuẩn lấy từ hồ sơ khách (một truy vấn cho tất cả)
    cust_ids = [g["cust_id"] for g in gom.values() if g["cust_id"]]
    if cust_ids:
        cho = ",".join("?" * len(cust_ids))
        for r in c.query(f"SELECT CustID, CustName, Phone, Address, CMND FROM I_CUSTOMER WITH (NOLOCK) "
                         f"WHERE CustID IN ({cho})", tuple(cust_ids)):
            for g in gom.values():
                if g["cust_id"] == r["CustID"]:
                    g["dia_chi"] = (r.get("Address") or "").strip()
                    g["ten"] = (r.get("CustName") or g["ten"]).strip()
                    g["dt"] = (r.get("Phone") or g["dt"]).strip()
                    g["cccd"] = (r.get("CMND") or g["cccd"]).strip()
    return sorted(gom.values(), key=lambda g: (str(g["ngay"] or ""), g["ten"]))


KHACH_MOI_TRANG = 4          # GĐ chốt 10/09/2026: mỗi trang Word đúng 4 khách rồi tự sang trang


def _ten_tep(dau, duoi):
    """Tên tệp mang NGÀY XUẤT, giữ đúng tên khuôn mẫu GĐ dùng để nhập vào phần mềm kế toán."""
    return f"{dau}_{dt.date.today():%Y%m%d}.{duoi}"


@login_not_required     # GĐ chốt 10/09/2026: mở cùng trang /thau-vao-2/
@require_GET
def xuat_ncc(request):
    """⬇ XUẤT EXCEL: danh sách khách trong kỳ theo đúng khuôn nhập nhà cung cấp của phần mềm kế toán."""
    import openpyxl
    from openpyxl.styles import Alignment, Font

    try:
        d1, d2 = _khoang(request)
        ds = khach_trong_ds(request)
    except ValueError as exc:
        return HttpResponse(str(exc), status=400)
    except Exception:
        logger.exception("Không dựng được danh sách khách để xuất Excel")
        return HttpResponse("Không đọc được danh sách khách. Hãy thử lại.", status=503)

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "NCC"
    ws.append(list(COT))
    for o in ws[1]:
        o.font = Font(bold=True)
        o.alignment = Alignment(horizontal="center", vertical="center")
    for g in ds:
        ngay = g["ngay"]
        if isinstance(ngay, str):
            ngay = dt.datetime.fromisoformat(ngay[:19]) if ngay else None
        ws.append(["", "", (g["ten"] or "").upper(), "Cá nhân", g["dt"], (g["dia_chi"] or "").upper(),
                   "", "", g["cccd"],
                   int(M.dec(g["tien_ck"]) / 1000) if g["tien_ck"] else None,      # Ghi chú = tiền CK theo NGHÌN đồng
                   ngay.replace(hour=0, minute=0, second=0, microsecond=0) if ngay else None])
    for hang in ws.iter_rows(min_row=2):
        hang[9].number_format = TIEN_FMT
        hang[10].number_format = "mm-dd-yy"
    for cot, rong in zip("ABCDEFGHIJK", (6, 12, 30, 11, 14, 42, 16, 13, 18, 12, 12)):
        ws.column_dimensions[cot].width = rong
    ws.freeze_panes = "A2"

    ra = BytesIO()
    wb.save(ra)
    kq = HttpResponse(ra.getvalue(), content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    kq["Content-Disposition"] = f'attachment; filename="{_ten_tep("NCC_NHAP_CHUAN_KH2", "xlsx")}"'
    return kq


@login_not_required     # GĐ chốt 10/09/2026: mở cùng trang /thau-vao-2/
@require_GET
def in_cccd(request):
    """🪪 IN CCCD: tệp Word, mỗi khách một dòng gồm ảnh CCCD trước và sau, in ra đúng cỡ thật của thẻ."""
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Mm, Pt

    try:
        d1, d2 = _khoang(request)
        ds = khach_trong_ds(request)
    except ValueError as exc:
        return HttpResponse(str(exc), status=400)
    except Exception:
        logger.exception("Không dựng được danh sách khách để in CCCD")
        return HttpResponse("Không đọc được danh sách khách. Hãy thử lại.", status=503)

    doc = Document()
    kho = doc.sections[0]
    kho.page_width, kho.page_height = Mm(210), Mm(297)
    for le in ("left_margin", "right_margin"):
        setattr(kho, le, Mm(11))
    kho.top_margin, kho.bottom_margin = Mm(10), Mm(10)

    tieu = doc.add_paragraph()
    tieu.alignment = WD_ALIGN_PARAGRAPH.CENTER
    ch = tieu.add_run(f"CCCD KHÁCH BÁN VÀNG · {d1[8:10]}/{d1[5:7]}/{d1[:4]}"
                      + (f" → {d2[8:10]}/{d2[5:7]}/{d2[:4]}" if d1 != d2 else "") + f" · {len(ds)} khách")
    ch.bold = True
    ch.font.size = Pt(12)

    for thu_tu, g in enumerate(ds):
        if thu_tu and thu_tu % KHACH_MOI_TRANG == 0:      # đủ 4 khách một trang thì tự ngắt sang trang mới
            doc.add_page_break()
        p = doc.add_paragraph()
        p.paragraph_format.space_before, p.paragraph_format.space_after = Pt(6), Pt(2)
        r = p.add_run((g["ten"] or "Khách lẻ").upper() + (f" · CCCD {g['cccd']}" if g["cccd"] else ""))
        r.bold = True
        r.font.size = Pt(9)

        bang = doc.add_table(rows=1, cols=2)
        bang.autofit = False
        for i, mat in enumerate(("mat-truoc", "mat-sau")):
            o = bang.rows[0].cells[i]
            o.width = Mm(CCCD_RONG_MM + 2)
            data = None
            if g["cust_id"]:
                try:
                    data, _ct = C.saved_image(g["cust_id"], mat)
                except Exception:
                    data = None
            khung = o.paragraphs[0]
            if data:
                khung.add_run().add_picture(BytesIO(data), width=Mm(CCCD_RONG_MM), height=Mm(CCCD_CAO_MM))
            else:
                thieu = khung.add_run("(chưa có ảnh %s)" % ("mặt trước" if i == 0 else "mặt sau"))
                thieu.italic = True
                thieu.font.size = Pt(8)

    ra = BytesIO()
    doc.save(ra)
    kq = HttpResponse(ra.getvalue(),
                      content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document")
    kq["Content-Disposition"] = f'attachment; filename="{_ten_tep("CCCD", "docx")}"'
    return kq
