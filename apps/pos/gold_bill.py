"""
gold_bill — ghi xuyên (write-through) + làm tươi + đối soát bảng `gold_bill` (apps/pos/models.GoldBill).

Nguyên tắc (GĐ chốt 08/09/2026): KK là sự thật. Mọi hàm ở đây NUỐT lỗi MySQL (log) — không bao giờ làm hỏng
một lượt bán chỉ vì bảng phụ. Dữ liệu chỉ KHBL có (emp_sup, tách tiền, bank, số lần in) được GIỮ khi làm tươi từ KK.
"""
import datetime
import logging

from django.utils import timezone

from apps.pmv import money as M

from . import bill as B, cart, services as S
from .models import GoldBill

logger = logging.getLogger(__name__)


def _d(x):
    return M.tron_ngan(M.dec(x)) if x not in (None, "") else M.D0


def _ngay(v):
    s = cart._ngay_iso(v)
    try:
        return datetime.date.fromisoformat(s) if s else None
    except ValueError:
        return None


def _items_tu_g(g):
    return [{"ma": x["row"].get("ProductCode"), "ten": x["row"].get("ProductDesc"), "vang": x["row"].get("GoldCode"),
             "tl": str(x["row"].get("GoldReal") or ""), "hot": str(x["row"].get("DiamondWeight") or ""),
             "gia": str(x["row"].get("SellRate") or ""), "cong": str(x["row"].get("TaskPrice") or ""),
             "tien": str(x["tien"])} for x in g.get("ban", [])]


def _doi_tu_g(g):
    return [{"vang": x["row"].get("GoldCode"), "tl": str(x["row"].get("GoldWeight") or ""),
             "hot": str(x["row"].get("DiamondWeight") or ""), "gia": str(x["row"].get("BuyRate") or ""),
             "ngang": str(x["row"].get("DoiNgang")) == "1", "tien": str(x["tien"])} for x in g.get("doi", [])]


def upsert_tu_gio(g, *, status=None, is_del=None, user=None, nguon="KHBL"):
    """Ghi/cập nhật dòng gold_bill từ GIỎ (dict cart) ngay sau khi KK đã nhận. Trả GoldBill hoặc None."""
    trn = (g.get("trn_id") or "").strip()
    if not trn:
        return None
    try:
        t = cart.tong_cua(g)
        pm = g.get("pay_method") or "cash"
        cash, ck = t.get("tien_mat", M.D0), t.get("tien_ck", M.D0)
        cust = g.get("cust") or {}
        d = {
            "bill_code": g.get("bill_code") or "", "trn_date": _ngay(g.get("ngay")) or datetime.date.today(),
            "trn_time": (g.get("gio") or "")[:8], "nguon": nguon,
            "emp_id": g.get("emp") or "", "emp_sup_id": g.get("emp_sup") or "",
            "cust_id": cust.get("id") or "", "cust_name": (cust.get("name") or "")[:200],
            "cust_phone": (cust.get("phone") or "")[:30],
            "tien_vang_moi": _d(t.get("vang_moi")), "tien_vang_cu": _d(t.get("vang_cu")),
            "tien_vang_them": _d(t.get("vang_them")), "tien_cong_them": _d(t.get("cong_them")),
            "tien_bot": _d(t.get("bot")), "tien_coc": _d(t.get("coc")), "tong": _d(t.get("khach_tra")),
            "pay_method": pm, "tien_mat": _d(cash),
            "tien_ck": _d(ck) if pm != "card" else M.D0, "tien_the": _d(ck) if pm == "card" else M.D0,
            "bank_id": g.get("bank_id") or "", "items": _items_tu_g(g), "doi": _doi_tu_g(g),
            "ma_sp": " ".join(str(x["row"].get("ProductCode") or "") for x in g.get("ban", [])),
            "kk_upd": g.get("upd") or "", "synced_at": timezone.now(),
        }
        if status:
            d["status"] = status
        elif g.get("status"):
            d["status"] = g["status"]
        if is_del is not None:
            d["is_del"] = bool(is_del)
        if user is not None and getattr(user, "username", ""):
            d["user_web"] = user.username
        obj, _ = GoldBill.objects.update_or_create(trn_id=trn, defaults=d)
        return obj
    except Exception:
        logger.exception("gold_bill: không ghi được %s (KK vẫn đúng, đối soát sẽ chữa)", trn)
        return None


def lam_tuoi_tu_kk(trn_id, c=None, phieu=None):
    """Làm tươi 1 dòng từ KK (bill.doc): trạng thái, tiền, dòng hàng, mốc kk_upd. GIỮ emp_sup / tách tiền / bank /
    số lần in của KHBL. Đơn không còn trên KK → is_del=1."""
    try:
        cu = GoldBill.objects.filter(trn_id=trn_id).first()
        phieu = phieu or B.doc(trn_id, c)
        if not phieu:
            if cu and not cu.is_del:
                cu.is_del = True
                cu.synced_at = timezone.now()
                cu.save(update_fields=["is_del", "synced_at", "updated_at"])
            return cu
        if cu and cu.kk_upd == (phieu.get("upd") or "") and cu.status == phieu.get("status") and not cu.is_del:
            return cu                                          # không đổi gì
        g = cart.tu_phieu(phieu)
        if cu:                                                 # giữ phần chỉ KHBL có
            g["emp_sup"], g["pay_method"], g["bank_id"] = cu.emp_sup_id, cu.pay_method, cu.bank_id
            g["tien_mat"] = str(cu.tien_mat) if cu.pay_method != "cash" or cu.tien_mat else ""
        return upsert_tu_gio(g, status=phieu.get("status"), is_del=False, nguon=cu.nguon if cu else "PMV")
    except Exception:
        logger.exception("gold_bill: làm tươi %s lỗi", trn_id)
        return None


def lam_tuoi_ds(rows, c=None):
    """Làm tươi các dòng DANH SÁCH đang xem (rows từ bill.danh_sach): chỉ đụng dòng có mốc/status lệch hoặc chưa có."""
    try:
        ids = [r["TrnID"] for r in rows]
        co = {b.trn_id: b for b in GoldBill.objects.filter(trn_id__in=ids)}
        n = 0
        for r in rows:
            b = co.get(r["TrnID"])
            if b and b.status == r.get("Status") and not b.is_del:
                continue
            if not b and r.get("Status") not in ("W", "C"):
                continue
            lam_tuoi_tu_kk(r["TrnID"], c)
            n += 1
        return n
    except Exception:
        logger.exception("gold_bill: làm tươi danh sách lỗi")
        return 0


def doi_soat_hom_nay(c=None):
    """Job 60' (scheduler) — đối soát đơn HÔM NAY giữa KK và gold_bill: đơn mới từ PMVGoldRT, đổi trạng thái,
    xóa. Trả dict thống kê."""
    c = c or S.client("gold_bill_doi_soat")
    hom_nay = datetime.date.today().isoformat()
    kk = c.query("SELECT TrnID, Status, IsDel, TrnDateTime_Upd FROM TRN_RT_BUYSELL WITH (NOLOCK) "
                 "WHERE TrnDate >= CAST(? AS datetime) AND TrnDate < DATEADD(day,1,CAST(? AS datetime))",
                 (hom_nay, hom_nay))
    co = {b.trn_id: b for b in GoldBill.objects.filter(trn_date=datetime.date.fromisoformat(hom_nay))}
    tk = {"kk": len(kk), "moi": 0, "doi": 0, "xoa": 0}
    for r in kk:
        b = co.pop(r["TrnID"], None)
        if str(r.get("IsDel")) != "0":
            if b and not b.is_del:
                b.is_del, b.synced_at = True, timezone.now()
                b.save(update_fields=["is_del", "synced_at", "updated_at"])
                tk["xoa"] += 1
            continue
        if not b:
            if lam_tuoi_tu_kk(r["TrnID"], c):
                tk["moi"] += 1
        elif b.kk_upd != str(r.get("TrnDateTime_Upd") or "") or b.status != r.get("Status") or b.is_del:
            lam_tuoi_tu_kk(r["TrnID"], c)
            tk["doi"] += 1
    for b in co.values():                                      # còn trong gold_bill mà KK không thấy → đã xóa
        if not b.is_del:
            b.is_del, b.synced_at = True, timezone.now()
            b.save(update_fields=["is_del", "synced_at", "updated_at"])
            tk["xoa"] += 1
    return tk


def emp_sup_cua(trn_id):
    b = GoldBill.objects.filter(trn_id=trn_id).only("emp_sup_id").first()
    return b.emp_sup_id if b else ""


def ten_nv(emp_id):
    if not emp_id:
        return ""
    return next((e["EmpName"] for e in S.nhan_vien_ban() if e["EmpID"] == emp_id), emp_id)


def thong_ke_ho_tro(trn_ids):
    """{emp_sup_id: {"ten", "so", "tong"}} cho các đơn đang xem (đơn C, không xóa) — giai đoạn GHI NHẬN."""
    out = {}
    for b in GoldBill.objects.filter(trn_id__in=list(trn_ids), is_del=False, status="C").exclude(emp_sup_id=""):
        x = out.setdefault(b.emp_sup_id, {"ten": ten_nv(b.emp_sup_id), "so": 0, "tong": M.D0})
        x["so"] += 1
        x["tong"] += M.dec(b.tong)
    return dict(sorted(out.items(), key=lambda kv: -kv[1]["tong"]))


def map_ho_tro(trn_ids):
    """{trn_id: tên NV hỗ trợ} cho cột HỖ TRỢ ở popup DANH SÁCH."""
    return {b.trn_id: ten_nv(b.emp_sup_id) for b in
            GoldBill.objects.filter(trn_id__in=list(trn_ids)).exclude(emp_sup_id="").only("trn_id", "emp_sup_id")}
