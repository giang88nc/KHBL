"""
Phân loại 1 lời gọi proc/câu SQL của PMVGoldRT thành NHÓM nghiệp vụ + HÀNH ĐỘNG
(để trang Hành vi tô màu và lọc). Dựa trên tên proc — bản đồ ở skill pmv-proc-map.
"""
import re

# Proc "tiếng ồn": app poll liên tục (bảng giá, đăng nhập, khuyến mãi...) — lớp TRACE
# không lưu từng dòng (lớp THỐNG KÊ vẫn đếm tổng), tránh ngập log.
NOISE = {
    "I_XRATE_GetAll", "I_GOLD_GetAll", "I_GOLDCCY_GetAll", "tbh_NguoiDungDangNhap_Load",
    "SYS_PROMOTION_GetLstProductAppr", "SYS_PROMOTION_GetLstBillAppr",
    "sp_datatype_info_90", "sp_datatype_info", "GET_NGAYGIO_HETHONG",
    "BangGia_Lst", "BangGiaDienTu_Lst", "BangGiaLoaiHang_Lst", "sp_reset_connection",
    "SYS_PARAMETERS_Lst", "SYS_PARAMETERS_Get", "I_XRATE_Lst",
    "sp_unprepare", "sp_cursorclose", "sp_cursorfetch",
}

# RPC "bọc ngoài" của driver (pyodbc/ADO.NET gửi câu có tham số qua sp_prepexec/sp_execute...).
# Tên hành vi thật nằm BÊN TRONG: 'exec sp_prepexec @p1 output, N'@P1 int', N'DECLARE @__rc INT;
# EXEC @__rc = [T_PRODUCT_GetByCodeForSell] @p_ProductCode=@P1...' → proc thật = T_PRODUCT_GetByCodeForSell.
# Bọc một SELECT trần → trả '' (không phải hành vi nghiệp vụ; collect bỏ với lời gọi của KHBL).
SP_WRAPPERS = {"sp_prepexec", "sp_prepare", "sp_execute", "sp_executesql", "sp_cursoropen", "sp_cursorprepexec"}
_SP_WRAPPERS = SP_WRAPPERS
_INNER_EXEC = re.compile(r"(?i)\bexec(?:ute)?\s+(?:@\w+\s*=\s*)?(?:\[?dbo\]?\.)?\[?([A-Za-z_][A-Za-z0-9_]*)\]?")

_CATEGORY_RULES = (
    (re.compile(r"(?i)BUYSELL|RT_SELL"), "HĐ bán"),
    (re.compile(r"(?i)BUYGOLD"), "HĐ thâu"),
    (re.compile(r"(?i)RT_CHANGE"), "HĐ đổi"),
    (re.compile(r"(?i)DATCOC"), "Đặt cọc"),
    (re.compile(r"(?i)CUSTOMER|DIEMTICHLUY|KHACHHANG|TICHLUY|CONGNO|DEBT"), "Khách hàng"),
    (re.compile(r"(?i)XRATE|BangGia|GOLDCCY|VangChuan|GiaSi"), "Bảng giá"),
    (re.compile(r"(?i)T_PRODUCT|PRODUCT_IN|PRODUCT_OUT|TonKho|TRACKING|SECTION|KHAY"), "Sản phẩm/kho"),
    (re.compile(r"(?i)TILL|CARDPAY|PhieuThuChi|ThuHo"), "Sổ quỹ"),
    (re.compile(r"(?i)DAILY_LOG|GOLD_BAL|RetailLog"), "Nhật ký ngày"),
    (re.compile(r"(?i)DongBo|Sync|ToaHang|ThiTruong"), "Đồng bộ"),
    (re.compile(r"(?i)HoaDonDienTu|HDDT|INVOICE"), "HĐ điện tử"),
    (re.compile(r"(?i)TinNhan|SMS"), "Tin nhắn"),
    (re.compile(r"(?i)^SYS_|^tbh_|CodeMasters|BILL_COUNTER|PREFIX|RIGHTS|MENUS|USERS|GROUP|ErrorLog|Version"), "Hệ thống"),
    (re.compile(r"(?i)EMPLOYEE|WORKER|NguoiDung"), "Nhân viên"),
    (re.compile(r"(?i)^rpt|Report|_Rpt|BaoCao"), "Báo cáo"),
)

_WRITE_VERBS = re.compile(r"(?i)_Ins\b|_Upd\b|_Del\b|_Complete|_Approve|_Appr\b|Cancel|CANCELED|^INS_|^UPD_|^DEL_|_Save|_Gen\b|_Ins_|Sync\b|Upd[A-Z]|_Update|NangHang|Generate")


def classify(name: str, text: str = ""):
    """Trả (category, action) — action: 'ghi' | 'đọc' | 'hệ thống'."""
    n = (name or "").strip().strip("[]")
    if not n and text:
        n = proc_name_from_text(text)
    category = "Khác"
    for rx, cat in _CATEGORY_RULES:
        if rx.search(n):
            category = cat
            break
    if not n and text:
        t = text.lstrip()[:12].upper()
        action = "ghi" if t.startswith(("INSERT", "UPDATE", "DELETE", "MERGE")) else "đọc"
        return category, action
    if _WRITE_VERBS.search(n):
        action = "ghi"
    elif re.search(r"(?i)_Lst\b|_Get|GetAll|_Load|_Lst_|_Check|Check_|_Search|_Find|^fun_|^fn_|_Exists", n):
        action = "đọc"
    elif category == "Hệ thống":
        action = "hệ thống"
    else:
        action = "đọc"
    return category, action


def proc_name_from_text(text: str) -> str:
    """Bóc tên proc từ TextData của trace SQL 2005: 'exec X @p=..' HOẶC dạng RPC trần
    '[X]' / 'X @p1=..' (app gọi CommandType.StoredProcedure — không có chữ exec)."""
    t = (text or "").strip()
    m = re.search(r"(?i)\bexec(?:ute)?\s+(?:\[?dbo\]?\.)?\[?([A-Za-z0-9_]+)\]?", t)
    if m:
        name = m.group(1)
        if name.lower() in _SP_WRAPPERS:
            return _proc_trong_wrapper(t[m.end():])
        return name
    m = re.match(r"^\[?(?:dbo\]?\.\[?)?([A-Za-z_][A-Za-z0-9_]*)\]?\s*(?:@|$|;)", t)
    return m.group(1) if m else ""


def _proc_trong_wrapper(phan_sau):
    """Tên proc thật bên trong sp_prepexec/sp_execute…; '' nếu bên trong chỉ là SELECT/khác."""
    m = _INNER_EXEC.search(phan_sau)
    if not m:
        return ""
    name = m.group(1)
    return "" if name.lower() in _SP_WRAPPERS else name
