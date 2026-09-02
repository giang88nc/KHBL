"""
PmvClient — lớp nói chuyện với PMV bằng ĐÚNG "giọng" của app PMVGoldRT (Track A3).

Quy ước rút từ giải phẫu (docs/PHAN_TICH_HOAT_DONG_PMVGOLDRT.md):
- Tham số toàn chuỗi: ngày 'dd/MM/yyyy', giờ 'HH:mm:ss', tiền chuỗi số nguyên.
- Dòng hàng / danh sách = XML  <NewDataSet><TÊN_BẢNG>…</TÊN_BẢNG></NewDataSet>.
- Tên tham số lấy từ sys.parameters (app cũng DeriveParameters) — gọi sai tên là lỗi sớm.
- Proc trả RETURN 0 = OK; lỗi nghiệp vụ ghi RetailLog / ErrorLog.
- Trước _Upd/_Del phải _Get để lấy TrnDateTime_Upd (khóa lạc quan).

target="pmv" đi qua gateway.pmv_call (allowlist + audit + write-lock);
target="sandbox" gọi thẳng bản sao local (để thử + diff).
"""
import datetime
from decimal import Decimal
from xml.sax.saxutils import escape

from . import gateway
from .sandbox import sandbox_call, sandbox_query


class PmvProcError(Exception):
    """Proc trả RETURN ≠ 0 (lỗi nghiệp vụ của vendor)."""

    def __init__(self, proc, rc, sets):
        self.proc, self.rc, self.sets = proc, rc, sets
        super().__init__(f"{proc} trả về rc={rc}")


class PmvClient:
    def __init__(self, target="pmv", tag="client"):
        assert target in ("pmv", "sandbox")
        self.target = target
        self.tag = tag
        self._param_cache = {}

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
    def xml_dataset(table, rows):
        """<NewDataSet><table><col>v</col>…</table>…</NewDataSet> — None bị bỏ, giá trị escape."""
        parts = ["<NewDataSet>"]
        for row in rows:
            parts.append(f"<{table}>")
            for k, v in row.items():
                if v is None:
                    continue
                parts.append(f"<{k}>{escape(str(v))}</{k}>")
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
            rows = (sandbox_query(sql, (proc,)) if self.target == "sandbox"
                    else gateway.pmv_read(sql, (proc,), tag=self.tag, audit=False))
            self._param_cache[proc] = [(r["name"].lstrip("@"), r["typ"]) for r in rows]
        return self._param_cache[proc]

    def _check_params(self, proc, params):
        known = {n.lower() for n, _ in self.params_of(proc)}
        if not known:
            raise ValueError(f"Proc {proc} không tồn tại trên {self.target}")
        bad = [k for k in params if k.lower() not in known]
        if bad:
            raise ValueError(f"{proc}: tham số không có trong chữ ký proc: {bad}")

    # ---------- gọi ----------
    def call(self, proc, write=False, raise_on_rc=True, **params):
        """Gọi proc với tham số đặt tên. Trả (rc, sets). rc≠0 → PmvProcError (mặc định)."""
        self._check_params(proc, params)
        if self.target == "sandbox":
            rc, sets = sandbox_call(proc, params)
        else:
            rc, sets = gateway.pmv_call(proc, params, tag=self.tag, write=write)
        if raise_on_rc and rc not in (0, None):
            raise PmvProcError(proc, rc, sets)
        return rc, sets

    def query(self, sql, params=()):
        if self.target == "sandbox":
            return sandbox_query(sql, params)
        return gateway.pmv_read(sql, params, tag=self.tag, audit=False)

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

    def sys_param(self, key):
        r = self.query("SELECT ParaValue FROM SYS_PARAMETERS WITH (NOLOCK) WHERE ParaKey = ?", (key,))
        return r[0]["ParaValue"] if r else None
