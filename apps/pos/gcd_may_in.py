# -*- coding: utf-8 -*-
"""
gcd_may_in — TÌM · CHỌN · LƯU MÁY IN cho GIẤY CẦM ĐỒ (GĐ chốt 15/09/2026):
    "tìm -> chọn -> lưu máy in vào cấu hình để sử dụng -> nếu không gọi được máy in đó
     -> thì trình duyệt tự điều hướng chọn máy in"

AI GHI — AI ĐỌC (một chiều, không HTTP — y như gcd_layout):
    KHBL /he-thong/mau-in-gcd/ --(PmvState.set)--> khj_bl.pmv_state['gcd_may_in']  ← NGƯỜI GHI DUY NHẤT
                                                             |  (SELECT chéo DB, chỉ đọc)
    KHCD /camdo/bien-nhan/<lid>/giay  <-------------------- đọc khoá này rồi quyết định in ------+

VÌ SAO LÀ KHOÁ RIÊNG `gcd_may_in`, KHÔNG gộp vào `gcd_layout` và KHÔNG gộp vào `may_in_ds`:
  1. `gcd_layout` là BỐ CỤC TỜ GIẤY — Giám đốc căn từng milimet bằng thước. Mỗi lần ghi vào khoá đó
     là một lần có cơ hội làm hỏng công căn chỉnh (POST thiếu khoá "layout" từng suýt xoá sạch, xem
     ghi chú trong gcd_print_config). Chọn máy in là việc bấm vài lần mỗi tháng — cho nó ghi vào một
     khoá riêng thì chọn máy in KHÔNG BAO GIỜ chạm tới bố cục, kể cả khi mã có lỗi.
  2. `may_in_ds` (SỔ MÁY IN) là bản KHAI TAY các đường in dự kiến — ipp 631, raw 9100, đại lý in —
     toàn bộ đang "chưa nối", và dùng chung cho cả 3 mẫu in. Khoá này ngược lại: là MỘT dòng, chứa
     TÊN MÁY IN WINDOWS THẬT đọc được từ máy chủ, chỉ dành cho Giấy cầm đồ.
  3. Hợp đồng cho KHCD gọn nhất có thể: ĐỌC MỘT KHOÁ, LẤY MỘT CHUỖI `ten`. Không phải tra id chéo
     hai khoá rồi suy ra tên máy in — càng ít bước thì hai hệ càng khó hiểu nhau sai.

CẤU TRÚC JSON (phiên bản 1):
    {"phien_ban": 1,
     "ten": "HP LaserJet M404",   # TÊN MÁY IN WINDOWS CHÍNH XÁC. "" = KHÔNG chọn máy in nào
     "cong": "USB001",            # cổng lúc chọn — chỉ để đối chiếu cho người, không dùng để in
     "ao": false,                 # lúc chọn, đây có phải máy in ẢO không (PDF/XPS/OneNote/Remote Desktop)
     "ghi_chu": "",
     "luc": "15/09/2026 20:30", "boi": "admin"}

KHCD ĐỌC THẾ NÀO (đường chạy thật hôm nay):
    ten rỗng            → không thử gì cả, trả cờ cho trang tự mở hộp thoại in của trình duyệt.
    ten có, in hụt      → CŨNG trả đúng cờ đó. Người dùng luôn in được, chỉ khác là phải tự chọn máy in.
  ⚠ Hôm nay máy chủ CHƯA đẩy bản in ra máy in được (xem tinh_trang() bên dưới) nên nhánh thứ hai là
    đường chạy HẰNG NGÀY, không phải phương án dự phòng hiếm gặp. Nó phải mượt và không báo lỗi đỏ.

TÌM MÁY IN = gọi PowerShell `Get-Printer` có sẵn của Windows — KHÔNG cài thêm gói, KHÔNG tải gì.
Máy chủ không có máy in vật lý nào thì danh sách toàn MÁY IN ẢO; đó là bình thường, không phải lỗi.
"""
import datetime as dt
import importlib.util
import json
import logging
import os
import shutil
import subprocess

from apps.pmv.models import PmvState

logger = logging.getLogger(__name__)

KEY = "gcd_may_in"
PHIEN_BAN = 1
MAC_DINH = {"phien_ban": PHIEN_BAN, "ten": "", "cong": "", "ao": False,
            "ghi_chu": "", "luc": "", "boi": ""}

GIOI_HAN = {"ten": 120, "cong": 60, "ghi_chu": 200, "luc": 20, "boi": 60}

# ── NHẬN DẠNG MÁY IN ẢO ───────────────────────────────────────────────────────────────────────
# Máy chủ này (đo 15/09/2026) KHÔNG có máy in vật lý: Get-Printer chỉ trả Print to PDF, XPS, OneNote
# và mấy cổng TS00x của phiên Remote Desktop. Chọn nhầm một trong số đó thì "in" ra tệp chứ không ra
# giấy — nên đánh dấu rõ ngay trên danh sách thay vì để Giám đốc tự đoán.
AO_TEN = ("print to pdf", "xps document writer", "onenote", "fax", "adobe pdf", "pdfcreator",
          "cutepdf", "bullzip", "dopdf", "pdf24", "foxit reader pdf printer", "snagit")
AO_DRIVER = ("remote desktop easy print", "microsoft xps", "microsoft print to pdf",
             "send to microsoft onenote", "microsoft software printer driver",
             "microsoft shared fax", "pdf")
AO_CONG = ("portprompt", "nul", "xpsport", "shrfax", "microsoft.office.onenote", "onenote", "pdf")


def _thap(v):
    return str(v or "").strip().lower()


def la_ao(ten, cong="", driver=""):
    """Trả (True/False, lý do bằng tiếng Việt). Chỉ để CẢNH BÁO — vẫn cho chọn nếu Giám đốc muốn."""
    t, c, d = _thap(ten), _thap(cong), _thap(driver)
    if c.startswith("ts0") or "remote desktop" in d:
        return True, "Máy in của phiên Remote Desktop (cổng %s) — biến mất khi đóng phiên" % (cong or "?")
    if any(x in t for x in AO_TEN) or any(x in d for x in AO_DRIVER) or any(x in c for x in AO_CONG):
        return True, "Máy in ảo — in ra TỆP (PDF/XPS/OneNote), không ra giấy"
    return False, ""


# ── TÌM MÁY IN (PowerShell Get-Printer) ───────────────────────────────────────────────────────
# ⚠ ConvertTo-Json của Windows PowerShell 5.1 BỎ LỚP MẢNG khi mảng chỉ có 1 phần tử ⇒ máy chủ có
#   đúng 1 máy in sẽ trả {"ds":{...}} chứ không phải {"ds":[{...}]}. Phía Python nhận CẢ HAI DẠNG.
# ⚠ Get-Printer KHÔNG có cột "máy in mặc định" — cờ đó nằm ở Win32_Printer.Default, nên lấy riêng.
PS = (
    "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8;"
    "$ErrorActionPreference='SilentlyContinue';"
    "$md=(Get-CimInstance Win32_Printer | Where-Object {$_.Default -eq $true}"
    " | Select-Object -First 1 -ExpandProperty Name);"
    "$o=@(Get-Printer | ForEach-Object { [pscustomobject]@{ten=[string]$_.Name;"
    "cong=[string]$_.PortName;driver=[string]$_.DriverName;trang_thai=[string]$_.PrinterStatus;"
    "chia_se=[bool]$_.Shared;mac_dinh=($_.Name -eq $md)} });"
    "ConvertTo-Json -InputObject @{ds=@($o)} -Depth 4 -Compress"
)
PS_EXE_DUPHONG = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"),
                              "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
TRANG_THAI_VN = {"normal": "Sẵn sàng", "offline": "Đang tắt / mất kết nối", "paused": "Đang tạm dừng",
                 "error": "Đang lỗi", "printing": "Đang in", "busy": "Đang bận",
                 "paperjam": "Kẹt giấy", "paperout": "Hết giấy", "outofmemory": "Hết bộ nhớ",
                 "dooropen": "Mở nắp", "notavailable": "Không dùng được"}


def _ps_exe():
    return shutil.which("powershell") or shutil.which("powershell.exe") or PS_EXE_DUPHONG


def tim(cho=8):
    """Hỏi Windows xem máy chủ đang có những máy in nào.

    LUÔN trả dict, KHÔNG BAO GIỜ ném lỗi ra ngoài: máy chủ không có máy in, PowerShell bị chặn, hay
    lệnh chạy quá lâu — tất cả đều ra danh sách rỗng kèm một câu giải thích cho người đọc.
    """
    out = {"ok": False, "ds": [], "loi": "", "giai_thich": ""}
    exe = _ps_exe()
    try:
        cf = getattr(subprocess, "CREATE_NO_WINDOW", 0)     # đừng chớp cửa sổ đen trên máy chủ
        r = subprocess.run([exe, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                            "-Command", PS],
                           capture_output=True, timeout=cho, creationflags=cf)
    except subprocess.TimeoutExpired:
        out["loi"] = ("Windows không trả lời trong %d giây. Thường là dịch vụ Spooler (bộ đệm in) "
                      "đang treo — khởi động lại dịch vụ Print Spooler rồi tìm lại." % cho)
        return out
    except (OSError, ValueError) as exc:                     # không tìm thấy powershell.exe, v.v.
        out["loi"] = "Không gọi được PowerShell trên máy chủ (%s)." % exc
        return out
    except Exception as exc:                                 # noqa: BLE001 — tuyệt đối không nổ 500
        logger.warning("gcd_may_in.tim lỗi lạ: %s", exc)
        out["loi"] = "Không đọc được danh sách máy in (%s)." % exc
        return out

    txt = (r.stdout or b"").decode("utf-8", "replace").strip()
    if not txt:
        loi = (r.stderr or b"").decode("utf-8", "replace").strip()
        out["ok"] = True          # chạy được, chỉ là KHÔNG CÓ máy in nào — đây không phải lỗi
        out["giai_thich"] = ("Máy chủ hiện chưa cài máy in nào. Cài máy in (hoặc thêm máy in mạng) "
                             "lên máy chủ rồi bấm Tìm máy in lại là nó hiện ra.")
        if loi:
            out["loi"] = loi[:300]
            out["ok"] = False
        return out
    try:
        data = json.loads(txt)
    except ValueError:
        out["loi"] = "Windows trả về dữ liệu không đọc được."
        return out

    ds = (data or {}).get("ds") if isinstance(data, dict) else data
    if isinstance(ds, dict):        # PowerShell 5.1 bỏ lớp mảng khi chỉ có 1 máy in
        ds = [ds]
    if not isinstance(ds, list):
        ds = []
    for row in ds:
        if not isinstance(row, dict):
            continue
        ten = _chu(row.get("ten"), GIOI_HAN["ten"])
        if not ten:
            continue
        cong = _chu(row.get("cong"), GIOI_HAN["cong"])
        driver = _chu(row.get("driver"), 120)
        ao, ly_do = la_ao(ten, cong, driver)
        tt = _thap(row.get("trang_thai"))
        out["ds"].append({
            "ten": ten, "cong": cong, "driver": driver,
            "trang_thai": TRANG_THAI_VN.get(tt, _chu(row.get("trang_thai"), 40) or "—"),
            "chia_se": bool(row.get("chia_se")),
            "mac_dinh": bool(row.get("mac_dinh")),
            "ao": ao, "ly_do_ao": ly_do,
        })
    out["ok"] = True
    that = [x for x in out["ds"] if not x["ao"]]
    if not out["ds"]:
        out["giai_thich"] = ("Máy chủ hiện chưa cài máy in nào. Cài máy in (hoặc thêm máy in mạng) "
                             "lên máy chủ rồi bấm Tìm máy in lại là nó hiện ra.")
    elif not that:
        out["giai_thich"] = ("Máy chủ chỉ đang có MÁY IN ẢO (in ra tệp PDF/XPS/OneNote và máy in của "
                             "phiên Remote Desktop) — đây là bình thường vì chưa có máy in nào cắm "
                             "vào máy chủ. Khi cài máy in thật lên máy chủ thì nó tự hiện ra ở đây.")
    return out


# ── TÌNH TRẠNG THẬT CỦA ĐƯỜNG IN PHÍA MÁY CHỦ ────────────────────────────────────────────────
# Nói thẳng bằng lời dễ hiểu: hôm nay chọn máy in xong thì lần in VẪN mở hộp thoại của trình duyệt.
# Muốn máy chủ tự đẩy bản in ra máy in thì cần ĐỦ hai mảnh:
#   (1) dựng được bản in thành PDF  — Microsoft Edge chạy ẩn làm được, MÁY NÀY CÓ SẴN;
#   (2) đẩy tệp PDF đó ra máy in    — cần một công cụ in tệp (SumatraPDF / Acrobat / PDFtoPrinter)
#       hoặc thư viện pywin32. MÁY NÀY CHƯA CÓ MẢNH NÀY ⇒ đường in phía máy chủ còn hụt.
EDGE = (r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe")
DAY_PDF = (
    (r"C:\Program Files\SumatraPDF\SumatraPDF.exe", "SumatraPDF"),
    (r"C:\Program Files (x86)\SumatraPDF\SumatraPDF.exe", "SumatraPDF"),
    (r"C:\Program Files\Adobe\Acrobat DC\Acrobat\Acrobat.exe", "Adobe Acrobat"),
    (r"C:\Program Files (x86)\Foxit Software\Foxit PDF Reader\FoxitPDFReader.exe", "Foxit Reader"),
)
DAY_PDF_PATH = ("SumatraPDF.exe", "PDFtoPrinter.exe")


def _tim_tep(duong_dan):
    return next((p for p in duong_dan if os.path.isfile(p)), "")


def tinh_trang():
    """Máy chủ có tự đẩy bản in ra máy in được chưa — và còn THIẾU đúng những gì."""
    edge = _tim_tep(EDGE)
    day = _tim_tep([p for p, _ in DAY_PDF]) or next(
        (shutil.which(x) for x in DAY_PDF_PATH if shutil.which(x)), "")
    pywin = importlib.util.find_spec("win32print") is not None

    co, thieu = [], []
    (co if edge else thieu).append("Microsoft Edge — dựng đúng bản in trên màn hình thành tệp PDF")
    (co if day else thieu).append("công cụ in tệp PDF ra máy in (SumatraPDF hoặc PDFtoPrinter — "
                                  "một tệp chạy, không cần cài đặt)")
    (co if pywin else thieu).append("thư viện pywin32 của Python (cách thứ hai để gọi máy in Windows)")

    san_sang = bool(edge and (day or pywin))
    return {
        "san_sang": san_sang,
        "edge": edge, "day_pdf": day, "pywin32": pywin,
        "co": co, "thieu": thieu,
        "loi_nhan": ("Máy chủ ĐÃ ĐỦ công cụ để tự đẩy bản in ra máy in đã chọn."
                     if san_sang else
                     "Máy chủ hiện CHƯA có công cụ đẩy bản in ra máy in. Vì vậy dù đã chọn máy in, "
                     "mỗi lần in vẫn mở hộp thoại in của trình duyệt để bấm chọn máy in và bấm In — "
                     "phiếu vẫn ra giấy bình thường, chỉ là phải bấm thêm một bước."),
    }


# ── ĐỌC / GHI ─────────────────────────────────────────────────────────────────────────────────
def _chu(v, n):
    """Cắt ngắn + bỏ ký tự điều khiển (xuống dòng, tab) — tên máy in luôn nằm gọn MỘT dòng."""
    s = "".join(c for c in str(v or "") if c >= " " and c != "\x7f").strip()
    return s[:n]


def mac_dinh():
    return dict(MAC_DINH)


def _gop(data):
    """Nhận bản lưu: chỉ giữ đúng 6 trường, cắt độ dài, ép kiểu. Rác vào — sạch ra."""
    out = mac_dinh()
    if not isinstance(data, dict):
        return out
    for k in ("ten", "cong", "ghi_chu", "luc", "boi"):
        if k in data:
            out[k] = _chu(data.get(k), GIOI_HAN[k])
    out["ao"] = bool(data.get("ao"))
    if not out["ten"]:          # không chọn máy in thì mọi thứ còn lại vô nghĩa
        out.update({"cong": "", "ao": False})
    return out


def load():
    raw = PmvState.get(KEY, "")
    if raw:
        try:
            return _gop(json.loads(raw))
        except (ValueError, TypeError):
            pass          # dữ liệu hỏng = coi như CHƯA CHỌN máy in → rơi về hộp thoại trình duyệt
    return mac_dinh()


def save(data, boi=""):
    """Lưu máy in đang chọn. data rỗng / ten rỗng = BỎ CHỌN (xoá hàng, luôn dùng hộp thoại in)."""
    clean = _gop(data)
    if not clean["ten"]:
        PmvState.set(KEY, "")
        return mac_dinh()
    clean["luc"] = dt.datetime.now().strftime("%d/%m/%Y %H:%M")
    if boi:
        clean["boi"] = _chu(boi, GIOI_HAN["boi"])
    PmvState.set(KEY, json.dumps(clean, ensure_ascii=False))
    return clean
