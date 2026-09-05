"""
PmvClient — lớp nói chuyện với PMV bằng ĐÚNG "giọng" của app PMVGoldRT (Track A3).

Quy ước rút từ giải phẫu (docs/PHAN_TICH_HOAT_DONG_PMVGOLDRT.md):
- Tham số toàn chuỗi: ngày 'dd/MM/yyyy', giờ 'HH:mm:ss', tiền chuỗi số nguyên.
- Dòng hàng / danh sách = XML  <NewDataSet><TÊN_BẢNG>…</TÊN_BẢNG></NewDataSet>.
- Tên tham số lấy từ sys.parameters (app cũng DeriveParameters) — gọi sai tên là lỗi sớm.
- Proc trả RETURN 0 = OK; lỗi nghiệp vụ ghi RetailLog / ErrorLog.
- Trước _Upd/_Del phải _Get để lấy TrnDateTime_Upd (khóa lạc quan).

ĐÍCH (03/09/2026): PmvClient() KHÔNG tham số = đi theo settings.PMV_TARGET — đây là
cách nghiệp vụ bán lẻ dùng, nhờ đó đổi .env là đổi đích, không sửa code. Truyền rõ
"kk" / "sandbox" chỉ khi công cụ cần ép đích (smoke, đối chiếu).
MỌI đích đều đi qua gateway (allowlist + nhật ký + khóa ghi + chốt cấm ghi KK) — tập
dượt trên sandbox vì thế đi đúng con đường lúc chạy thật, không phải đường tắt.
"""
import datetime
from decimal import Decimal
from xml.sax.saxutils import escape

from . import gateway


class PmvProcError(Exception):
    """Proc trả RETURN ≠ 0 (lỗi nghiệp vụ của vendor)."""

    def __init__(self, proc, rc, sets):
        self.proc, self.rc, self.sets = proc, rc, sets
        super().__init__(f"{proc} trả về rc={rc}")


class PmvClient:
    def __init__(self, target=None, tag="client"):
        """target: None = theo settings.PMV_TARGET (cách dùng chuẩn của nghiệp vụ);
        "kk"/"pmv" = ép máy KK; "sandbox" = ép bản thử máy Mr Giang."""
        if target is None:
            target = gateway.dich_hien_tai()
        elif target == "pmv":
            target = "kk"
        if target not in ("kk", "sandbox"):
            raise ValueError(f"Đích không hợp lệ: {target!r}")
        self.target = target
        self.tag = tag
        self._param_cache = {}

    @property
    def la_that(self):
        """True khi đang trỏ vào máy KK (dữ liệu thật của tiệm)."""
        return self.target == "kk"

    def mo_ta(self):
        return gateway.mo_ta_dich(self.target)

    # ---------- định dạng theo app ----------
    @staticmethod
    def fmt_date(d=None):
        d = d or datetime.date.today()
        return d.strftime("%d/%m/%Y")

    @staticmethod
    def fmt_time(t=None):
        t = t or datetime.datetime.now()
        return t.strftime("%H:%M:%S")

    @staticmethod
    def money(x):
        """Tiền dạng chuỗi số nguyên như app gửi ('7050000')."""
        return str(int(Decimal(str(x or 0)).quantize(Decimal("1"))))

    @staticmethod
    def num(x, places=3):
        return str(Decimal(str(x or 0)).quantize(Decimal(1).scaleb(-places)))

    @staticmethod
    def _xml_val(v):
        """⚠ BẪY 03/09/2026: str(Decimal('0E-8')) ra '0E-8' (ký pháp khoa học) — proc vendor
        CONVERT chuỗi đó sang numeric là CHẾT ("Error converting data type varchar to numeric").
        Dính khi đọc dòng hàng bằng TRN_RT_BUYSELL_Get rồi đẩy ngược vào _Upd: cột trọng lượng
        hột bằng 0 trả về đúng dạng ấy. Mọi số PHẢI viết dạng thập phân thường."""
        if isinstance(v, Decimal):
            return format(v, "f")
        return str(v)

    @classmethod
    def xml_dataset(cls, table, rows):
        """<NewDataSet><table><col>v</col>…</table>…</NewDataSet> — None bị bỏ, giá trị escape."""
        return cls.xml_nhieu_bang([(table, rows)])

    @classmethod
    def xml_nhieu_bang(cls, bang_dong):
        """Nhiều bảng trong CÙNG một <NewDataSet> — đúng cách app gửi hóa đơn:
        vàng bán (TRN_RT_BUYSELL_SELL) và vàng đổi (TRN_RT_BUYSELL_BUYGOLD) đi chung
        MỘT tham số @p_Trn_RT_BUYSELL. bang_dong = [(tên_bảng, [dòng...]), ...]"""
        parts = ["<NewDataSet>"]
        for table, rows in bang_dong:
            for row in rows or []:
                parts.append(f"<{table}>")
                for k, v in row.items():
                    if v is None:
                        continue
                    parts.append(f"<{k}>{escape(cls._xml_val(v))}</{k}>")
                parts.append(f"</{table}>")
        parts.append("</NewDataSet>")
        return "".join(parts)

    # ---------- chữ ký proc ----------
    def params_of(self, proc):
        """[(tên_không_@, kiểu)] từ sys.parameters — cache theo phiên."""
        if proc not in self._param_cache:
            sql = ("SELECT p.name, t.name AS typ FROM sys.parameters p "
                   "JOIN sys.types t ON t.user_type_id = p.user_type_id "
                   "WHERE p.object_id = OBJECT_ID(?) ORDER BY p.parameter_id")
            rows = gateway.pmv_read(sql, (proc,), tag=self.tag, audit=False, target=self.target)
            self._param_cache[proc] = [(r["name"].lstrip("@"), r["typ"]) for r in rows]
        return self._param_cache[proc]

    def _check_params(self, proc, params):
        known = {n.lower() for n, _ in self.params_of(proc)}
        if not known:
            raise ValueError(f"Proc {proc} không tồn tại trên {self.target}")
        bad = [k for k in params if k.lower() not in known]
        if bad:
            raise ValueError(f"{proc}: tham số không có trong chữ ký proc: {bad}")

    # Proc vendor KHÔNG đặt giá trị mặc định cho tham số nào — thiếu 1 cái là SQL Server
    # báo "expects parameter '@X', which was not supplied". Hàm này điền nốt phần còn
    # thiếu theo đúng KIỂU trong chữ ký, để chỗ gọi chỉ phải nêu thứ mình quan tâm.
    _RONG_THEO_KIEU = {
        "image": None, "varbinary": None, "binary": None,
        "datetime": None, "smalldatetime": None,
        "xml": "",
    }

    def tham_so_day_du(self, proc, params):
        """Trả dict đủ MỌI tham số của proc: cái đã nêu giữ nguyên, cái thiếu điền rỗng."""
        # SQL Server không phân biệt hoa/thường ở tên tham số; một số proc vendor lại
        # viết cùng tham số khác kiểu chữ (p_Company_Address / p_company_Address).
        # Chuẩn hóa theo chính chữ ký proc để caller dùng chung một bộ dữ liệu cho Ins/Upd.
        supplied = {str(k).lower(): v for k, v in params.items()}
        day_du = {}
        for ten, kieu in self.params_of(proc):
            if ten.lower() in supplied:
                day_du[ten] = supplied[ten.lower()]
                continue
            k = (kieu or "").lower()
            if k in self._RONG_THEO_KIEU:
                day_du[ten] = self._RONG_THEO_KIEU[k]
            elif k in ("int", "bigint", "smallint", "tinyint", "bit", "decimal",
                       "numeric", "money", "smallmoney", "float", "real"):
                day_du[ten] = 0
            else:
                day_du[ten] = ""
        known = {ten.lower() for ten, _ in self.params_of(proc)}
        thua = [k for k in params if str(k).lower() not in known]
        if thua:
            raise ValueError(f"{proc}: tham số không có trong chữ ký proc: {thua}")
        return day_du

    # ---------- gọi ----------
    def call(self, proc, write=False, raise_on_rc=True, day_du=False, **params):
        """Gọi proc với tham số đặt tên. Trả (rc, sets). rc≠0 → PmvProcError (mặc định).
        day_du=True: tự điền nốt các tham số không nêu (xem tham_so_day_du)."""
        if day_du:
            params = self.tham_so_day_du(proc, params)
        self._check_params(proc, params)
        rc, sets = gateway.pmv_call(proc, params, tag=self.tag, write=write, target=self.target)
        if raise_on_rc and rc not in (0, None):
            raise PmvProcError(proc, rc, sets)
        return rc, sets

    def query(self, sql, params=()):
        return gateway.pmv_read(sql, params, tag=self.tag, audit=False, target=self.target)

    # ---------- tiện ích nghiệp vụ ----------
    def retail_log(self, trn_id, limit=10):
        return self.query(
            f"SELECT TOP {int(limit)} DateLog, StoreName, LogMsg, ErrorMessage "
            "FROM RetailLog WITH (NOLOCK) WHERE TrnID = ? ORDER BY DateLog DESC", (trn_id,))

    def bill(self, trn_id):
        """TRN_RT_BUYSELL_Get → dict(header, lines, old_gold) — cũng là nguồn TrnDateTime_Upd."""
        _, sets = self.call("TRN_RT_BUYSELL_Get", p_TrnID=trn_id, p_ShopID_XRate="")
        return {
            "header": sets[0][0] if sets and sets[0] else None,
            "lines": sets[1] if len(sets) > 1 else [],
            "old_gold": sets[2] if len(sets) > 2 else [],
        }

    # ---------- KHÓA LẠC QUAN (bài học 03/09/2026) ----------
    # Proc _Upd/_Del của vendor so TrnDateTime_Upd truyền vào với giá trị trong bảng.
    # KHÔNG khớp thì proc trả rc=0 nhưng KHÔNG LÀM GÌ CẢ — hỏng im lặng, nguy hiểm hơn
    # báo lỗi. Mốc còn ĐỔI sau MỖI bước, nên phải đọc lại giữa các bước.
    MOC_TRONG = "Jan  1 1900 12:00:00:000AM"

    @staticmethod
    def fmt_moc(dt):
        """datetime → chuỗi mốc đúng giọng app ('Sep 03 2026 09:46:51:660AM')."""
        if not dt:
            return PmvClient.MOC_TRONG
        return (dt.strftime("%b %d %Y %I:%M:%S:") + f"{dt.microsecond // 1000:03d}"
                + dt.strftime("%p"))

    def moc_khoa(self, bang, cot_id, gia_tri, cot_moc="TrnDateTime_Upd"):
        """Đọc mốc khóa lạc quan HIỆN TẠI của 1 dòng. Không thấy dòng → None."""
        r = self.query(f"SELECT {cot_moc} AS moc FROM {bang} WITH (NOLOCK) WHERE {cot_id} = ?",
                       (gia_tri,))
        return r[0]["moc"] if r else None

    def goi_co_khoa(self, proc, *, bang, cot_id, gia_tri, ten_tham_so="p_TrnDateTime_Upd",
                    kiem_tra=None, **params):
        """Gọi proc _Upd/_Del đúng cách: đọc mốc hiện tại → gọi → KIỂM CHỨNG kết quả.

        kiem_tra: hàm nhận PmvClient, trả True nếu dòng đã thay đổi như mong muốn.
        Truyền vào thì lệnh nào "chạy xong mà không đổi gì" sẽ bị bắt ngay tại đây
        thay vì âm thầm trôi qua."""
        moc = self.moc_khoa(bang, cot_id, gia_tri)
        if moc is None:
            raise PmvProcError(proc, -1, [{"loi": f"Không thấy {bang}.{cot_id}={gia_tri}"}])
        params[ten_tham_so] = self.fmt_moc(moc)
        rc, sets = self.call(proc, write=True, **params)
        if kiem_tra is not None and not kiem_tra(self):
            raise PmvProcError(proc, rc if rc else -2, [{
                "loi": f"{proc} chạy xong (rc={rc}) nhưng dữ liệu KHÔNG đổi — nhiều khả năng "
                       f"mốc khóa lạc quan đã cũ (dòng vừa bị người khác sửa). Đọc lại rồi gọi lại."
            }])
        return rc, sets

    def sys_param(self, key):
        r = self.query("SELECT ParaValue FROM SYS_PARAMETERS WITH (NOLOCK) WHERE ParaKey = ?", (key,))
        return r[0]["ParaValue"] if r else None
