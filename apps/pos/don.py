"""
ĐƠN CHỜ v5 — giỏ trên màn hình LUÔN là 1 hóa đơn W thật trên PMV (GĐ chốt 1, quy trình BAN_HANG v5).

Trước: gom giỏ trong session, chỉ ghi KK lúc THANH TOÁN. Nay: quét món đầu = TRN_RT_BUYSELL_Ins ngay
(SP bị khóa P-008 trên mọi máy như app PMVGoldRT); mọi thêm/bỏ SP, thêm/bỏ dẻ, đổi khách/NV/tiền = 1 Upd.

Cách nối: views._pos_oob (điểm ra của MỌI thao tác) gọi dong_bo(). dong_bo so VÂN TAY giỏ với lần ghi
trước — không đổi thì không ghi (idempotent), nên gọi thừa không tốn lệnh KK. Ghi lỗi → giỏ QUAY VỀ
sự thật trên KK (đọc lại đơn) hoặc rỗng (Ins lỗi) + trả câu lỗi để hiện toast; không bao giờ để giỏ
lệch KK âm thầm.
"""
import datetime
import hashlib
import json

from django.core.cache import cache

from apps.pmv import money as M
from apps.pmv.models import PmvUser

from . import bill as B, cart, services as S

TREO_PHUT = 30      # GĐ chốt 05/09/2026: đơn W treo quá 30 phút thì nhắc


def phien(request):
    pu = PmvUser.objects.filter(django_user=request.user).first()
    tiem = S.thong_tin_tiem() or {}
    return {"user_id": (pu.user_id if pu else "") or "", "till_id": (pu.till_id if pu else "") or "",
            "shop_id": tiem.get("ShopID") or ""}


def van_tay(g):
    """Những gì làm đổi hóa đơn trên KK (KHÔNG gồm cách thanh toán/bank — chỉ ở app)."""
    goc = {
        "ban": [(x["row"].get("ProductCode"), x["tien"]) for x in g["ban"]],
        "doi": [(x["row"].get("GoldCode"), x["row"].get("TotalGoldWeight"), x["row"].get("DiamondWeight"),
                 x["row"].get("BuyRate"), x["tien"]) for x in g["doi"]],
        "cust": (g.get("cust") or {}).get("id") or "", "emp": g.get("emp") or "",
        "bot": g.get("bot"), "cong_them": g.get("cong_them"), "vang_them": g.get("vang_them"),
        "coc": g.get("coc"), "ghi_chu": g.get("ghi_chu") or "",
    }
    return hashlib.sha1(json.dumps(goc, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def dong_bo(request):
    """Đồng bộ giỏ → đơn W trên KK. Trả None nếu ổn / câu lỗi nếu ghi hỏng (giỏ đã được quay về sự thật)."""
    g = cart.get(request)
    fp = van_tay(g)
    if fp == g.get("_fp"):
        return None
    if g.get("status") == B.CHOT_ROI:          # đơn đã chốt: luật 1 — không tự Upd, chờ MỞ LẠI
        return None
    ph = phien(request)
    trn = g.get("trn_id") or ""
    c = S.client("don_cho")

    # giỏ trống mà đang có đơn W → xóa đơn (đơn W không món là vô nghĩa, SP đã tự do)
    if not g["ban"] and not g["doi"]:
        if trn:
            try:
                B.huy(trn, user_id=ph["user_id"], c=c)
            except Exception as exc:
                return f"Không xóa được đơn chờ {g.get('bill_code') or trn}: {_loi_goi(exc)}"
            g.update(trn_id="", bill_code="", status="")
        g["_fp"] = fp
        cart.save(request, g)
        return None

    if not ph["user_id"] or not ph["till_id"]:
        # không ghi được KK → không giữ món trong giỏ (tránh giỏ "ảo" khác sự thật)
        g["ban"], g["doi"] = [], []
        g["_fp"] = van_tay(g)
        cart.save(request, g)
        return "Tài khoản web chưa gắn tài khoản PMV / KÉT — chưa lập đơn được (vào Hệ thống đồng bộ user)"

    now = datetime.datetime.now()
    try:
        kq = B.luu(trn_id=trn, ban=cart.dong_ban(g), doi=cart.dong_doi(g),
                   ngay=c.fmt_date(now.date()), gio=c.fmt_time(now),
                   cust_id=(g.get("cust") or {}).get("id") or S.WALK_IN,
                   emp_id=g.get("emp") or "", till_id=ph["till_id"], shop_id=ph["shop_id"],
                   user_id=ph["user_id"], ghi_chu=g.get("ghi_chu") or "",
                   bot=g.get("bot"), cong_them=g.get("cong_them"),
                   vang_them=g.get("vang_them"), coc=g.get("coc"), c=c)
    except Exception as exc:
        loi = _loi_goi(exc)
        if trn:                                    # quay về đúng những gì KK đang giữ
            try:
                cart.nap(request, B.doc(trn, c))
            except Exception:
                pass
        else:                                      # Ins hỏng → giỏ chưa có gì trên KK
            g["ban"], g["doi"] = [], []
            g["_fp"] = van_tay(g)
            cart.save(request, g)
        return f"Không ghi được đơn lên PMV: {loi}"
    g.update(trn_id=kq["trn_id"], bill_code=kq["bill_code"], status=B.NHAP, _fp=fp)
    cart.save(request, g)
    return None


def _loi_goi(exc):
    sets = getattr(exc, "sets", None)
    if sets:
        for s in sets:
            if isinstance(s, dict) and s.get("loi"):
                return s["loi"]
            for r in (s if isinstance(s, list) else []):
                if isinstance(r, dict) and r.get("ErrorDesc"):
                    return r["ErrorDesc"]
    return str(exc)


# ─────────────── kiểm soát trùng & đơn treo (v5 pha 9) ───────────────
def don_giu_sp(ma):
    """Đơn nào đang giữ SP (P-008) → hiện 'đang trong đơn chờ <mã> · user · két · giờ'."""
    r = S.client("giu_sp").query(
        "SELECT TOP 1 b.TrnID, b.BillCode, b.Status, b.CreatedBy, b.TillID, b.CreatedDate "
        "FROM TRN_RT_BUYSELL_SELL s WITH (NOLOCK) JOIN TRN_RT_BUYSELL b WITH (NOLOCK) ON b.TrnID = s.TrnID "
        "WHERE s.ProductCode = ? AND b.IsDel = '0' ORDER BY b.CreatedDate DESC", (ma,))
    return r[0] if r else None


def don_treo(force=False):
    """Đơn W tạo quá TREO_PHUT phút (mọi máy) — badge trên màn bán + popup DANH SÁCH. Cache 60s."""
    key = "khbl:don_treo"
    rows = None if force else cache.get(key)
    if rows is None:
        try:
            rows = S.client("don_treo").query(
                "SELECT b.TrnID, b.BillCode, b.CreatedDate, b.CreatedBy, b.PayAmount, ISNULL(e.EmpName,'') AS EmpName "
                "FROM TRN_RT_BUYSELL b WITH (NOLOCK) LEFT JOIN T_EMPLOYEE e WITH (NOLOCK) ON e.EmpID = b.EmpID "
                "WHERE b.Status = 'W' AND b.IsDel = '0' AND b.CreatedDate < DATEADD(minute, -?, GETDATE()) "
                "ORDER BY b.CreatedDate", (TREO_PHUT,))
        except Exception:
            rows = []
        cache.set(key, rows, 60)
    return rows
