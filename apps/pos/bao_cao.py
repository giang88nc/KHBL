# -*- coding: utf-8 -*-
"""BÁO CÁO bán lẻ — thuật toán tổng hợp (GĐ chốt 12/09/2026).

BA CON SỐ DOANH THU, GĐ chốt dùng CẢ BA, không thay nhau:

    · DOANH THU THỰC  = tiền HÀNG BÁN RA           → SUM(SellTotalAmount)
    · DOANH THU RÒNG  = tổng tiền, tính cả bù + dư  → SUM(PayAmount)   (đơn khách nhận lại tiền là SỐ ÂM)
    · DOANH THU HH    = tổng tiền, CHỈ phần > 0     → SUM(PayAmount khi > 0)  — dùng tính hoa hồng NV

  Ba số này khác nhau có cơ sở: "thực" là hàng bán được, "ròng" là tiền thực chạy qua quầy sau khi cấn
  vàng cũ, "HH" bỏ các đơn tiệm trả tiền ra để hoa hồng không bị âm. Tháng 8/2026: 98,7 tỷ · 71,9 tỷ · 60,5 tỷ.

QUY TẮC CHUNG cho MỌI con số trong tệp này:
  · chỉ lấy chứng từ THẬT: ``IsDel='0'`` và ``Status='C'`` — nháp, đã hủy không vào báo cáo;
  · mốc thời gian = ``CreatedDate`` (lúc lập phiếu tại quầy), KHÔNG phải TrnDate — phiếu ghi lùi ngày vẫn
    nằm đúng ngày làm việc thật. Lọc bằng khoảng ``>= d1 AND < d2+1`` vì SQL Server 2005 không có kiểu date;
  · trọng lượng vàng PMV lưu theo LY, 100 ly = 1 chỉ — mọi chỗ hiển thị đều quy ra CHỈ;
  · gộp bằng GROUP BY NGAY TRÊN máy KK, không kéo cả bảng về Python (6.000+ dòng hàng mỗi tháng).

Cột ``ThuHo`` là varchar trong PMV nên KHÔNG được đưa vào SUM (lỗi 8117).
"""
import datetime as dt
import logging

from django.core.cache import cache

from apps.pmv import money as M

log = logging.getLogger(__name__)
LY_MOI_CHI = M.dec(100)
NHO_TAM = 60            # kỳ có HÔM NAY: nhớ 1 phút cho đỡ hỏi KK liên tục
NHO_TAM_CU = 900        # kỳ đã đóng: số không đổi nữa, nhớ 15 phút


def chi(ly):
    """Ly → chỉ (100 ly = 1 chỉ) — đơn vị người trong nghề đọc được."""
    return M.dec(ly) / LY_MOI_CHI


def _khoang(d1, d2):
    """Trả (đầu, cuối-hở) dạng ISO để lọc: [d1 00:00, d2+1 00:00)."""
    return d1, (dt.date.fromisoformat(d2) + dt.timedelta(days=1)).isoformat()


def _mot(rows):
    return rows[0] if rows else {}


# ─────────────────────────── các mảng số ───────────────────────────
def ban_tong(c, d1, d2):
    """Bán/đổi trong kỳ: ba con số doanh thu + cấu phần tạo ra chúng."""
    dau, cuoi = _khoang(d1, d2)
    r = _mot(c.query(
        "SELECT COUNT(*) so, SUM(ISNULL(SellTotalAmount,0)) thuc, SUM(ISNULL(PayAmount,0)) rong, "
        "SUM(CASE WHEN PayAmount > 0 THEN PayAmount ELSE 0 END) hh, "
        "SUM(CASE WHEN PayAmount < 0 THEN PayAmount ELSE 0 END) tra_khach, "
        "SUM(CASE WHEN PayAmount < 0 THEN 1 ELSE 0 END) so_tra_khach, "
        "SUM(ISNULL(BuyTotalAmount,0)) vang_cu, SUM(ISNULL(Discount,0)) bot, "
        "SUM(ISNULL(TaskPriceAdd,0)) cong_them, SUM(ISNULL(AddMoney,0)) vang_them, "
        "SUM(ISNULL(CashPay,0)) tien_mat, SUM(ISNULL(CardPay,0)) chuyen_khoan, "
        "SUM(ISNULL(TienCoc,0)) coc, SUM(ISNULL(DebitAmount,0)) no "
        "FROM TRN_RT_BUYSELL WITH (NOLOCK) "
        "WHERE IsDel='0' AND Status='C' AND CreatedDate >= ? AND CreatedDate < ?", (dau, cuoi)))
    ra = {k: M.dec(r.get(k)) for k in ("thuc", "rong", "hh", "tra_khach", "vang_cu", "bot", "cong_them",
                                       "vang_them", "tien_mat", "chuyen_khoan", "coc", "no")}
    ra["so"] = int(r.get("so") or 0)
    ra["so_tra_khach"] = int(r.get("so_tra_khach") or 0)
    # Phần tiền chưa ghi rõ hình thức: PMV chỉ điền CashPay/CardPay khi quầy phân bổ. Hiện ra thành một
    # dòng riêng thay vì im lặng — người đọc báo cáo phải biết phần nào chưa có chứng từ hình thức trả.
    ra["chua_ro"] = ra["rong"] - ra["tien_mat"] - ra["chuyen_khoan"]
    return ra


def thau_tong(c, d1, d2):
    """Thâu vào trong kỳ. CardPay của phiếu thâu là tiền ĐI RA nên PMV ghi số âm — lấy trị tuyệt đối."""
    dau, cuoi = _khoang(d1, d2)
    r = _mot(c.query(
        "SELECT COUNT(*) so, SUM(ISNULL(TotalAmount,0)) tien, SUM(ISNULL(GoldWeight,0)) ly, "
        "SUM(ISNULL(CashPay,0)) tien_mat, SUM(ISNULL(CardPay,0)) chuyen_khoan "
        "FROM TRN_RT_BUYGOLD WITH (NOLOCK) "
        "WHERE IsDel='0' AND Status='C' AND CreatedDate >= ? AND CreatedDate < ?", (dau, cuoi)))
    return {"so": int(r.get("so") or 0), "tien": M.dec(r.get("tien")),
            "ly": M.dec(r.get("ly")), "chi": chi(r.get("ly")),
            "tien_mat": abs(M.dec(r.get("tien_mat"))), "chuyen_khoan": abs(M.dec(r.get("chuyen_khoan")))}


def theo_nhan_vien(c, d1, d2):
    """Bán theo NHÂN VIÊN — cùng ba con số, xếp theo doanh thu HH (cơ sở tính hoa hồng)."""
    dau, cuoi = _khoang(d1, d2)
    rows = c.query(
        "SELECT b.EmpID, e.EmpName, COUNT(*) so, SUM(ISNULL(b.SellTotalAmount,0)) thuc, "
        "SUM(ISNULL(b.PayAmount,0)) rong, SUM(CASE WHEN b.PayAmount > 0 THEN b.PayAmount ELSE 0 END) hh "
        "FROM TRN_RT_BUYSELL b WITH (NOLOCK) LEFT JOIN T_EMPLOYEE e WITH (NOLOCK) ON e.EmpID = b.EmpID "
        "WHERE b.IsDel='0' AND b.Status='C' AND b.CreatedDate >= ? AND b.CreatedDate < ? "
        "GROUP BY b.EmpID, e.EmpName ORDER BY 6 DESC", (dau, cuoi))
    return [{"ma": r["EmpID"], "ten": (r.get("EmpName") or "").strip() or "(chưa gán nhân viên)",
             "so": int(r["so"] or 0), "thuc": M.dec(r["thuc"]), "rong": M.dec(r["rong"]), "hh": M.dec(r["hh"])}
            for r in rows]


def ho_vang(c):
    """Ghép MÃ VÀNG BÁN RA với MÃ DẺ tương ứng, vì PMV để chúng thành hai mã khác nhau.

    ``I_GOLD.GoldType``: G = vàng hàng bán ra (N9999 · 18K · 24K · SJC · VT…), D = DẺ khách mang tới
    (D9999 · D18K · D24K · DSJC · DT…). Bỏ tiền tố D/N/V là hai bên về chung một họ: N9999 và D9999
    đều là "9999". Không ghép thì bảng cân đối vô nghĩa — mỗi bên nằm một dòng, không trừ được nhau.
    """
    ra = {}
    for r in c.query("SELECT GoldCode, GoldDesc, GoldType FROM I_GOLD WITH (NOLOCK)"):
        ma = (r["GoldCode"] or "").strip()
        loai = (r["GoldType"] or "").strip().upper()
        goc = ma.upper()
        if loai == "D" and goc.startswith("D"):
            goc = goc[1:]                       # DẺ: bỏ chữ D đứng đầu
        elif goc[:1] in ("N", "V") and len(goc) > 2:
            goc = goc[1:]                       # hàng: N9999 → 9999, VT → T
        ra[ma] = {"ho": goc or ma.upper(), "ten": (r.get("GoldDesc") or ma).strip(), "loai": loai}
    return ra


def vang_theo_tuoi(c, d1, d2):
    """Cân đối VÀNG theo tuổi: bán ra bao nhiêu chỉ, thâu vào bao nhiêu chỉ, chênh lệch.

    Bán ra đọc TỪNG DÒNG HÀNG (``TRN_RT_BUYSELL_SELL.GoldReal`` = vàng thật của món, đã trừ hột),
    thâu vào đọc ``TRN_RT_BUYGOLD.GoldWeight``; hai bên gộp về cùng HỌ VÀNG (xem ``ho_vang``).
    Đây là con số ngành vàng cần mà phần mềm bán hàng không dựng sẵn: ra nhiều hơn vào là kho đang vơi.
    """
    dau, cuoi = _khoang(d1, d2)
    ra = c.query(
        "SELECT s.GoldCode ma, COUNT(*) dong, SUM(ISNULL(s.GoldReal,0)) ly, SUM(ISNULL(s.SellAmount,0)) tien "
        "FROM TRN_RT_BUYSELL_SELL s WITH (NOLOCK) JOIN TRN_RT_BUYSELL b WITH (NOLOCK) ON b.TrnID = s.TrnID "
        "WHERE b.IsDel='0' AND b.Status='C' AND b.CreatedDate >= ? AND b.CreatedDate < ? "
        "GROUP BY s.GoldCode", (dau, cuoi))
    vao = c.query(
        "SELECT GoldCode ma, COUNT(*) dong, SUM(ISNULL(GoldWeight,0)) ly, SUM(ISNULL(TotalAmount,0)) tien "
        "FROM TRN_RT_BUYGOLD WITH (NOLOCK) "
        "WHERE IsDel='0' AND Status='C' AND CreatedDate >= ? AND CreatedDate < ? GROUP BY GoldCode", (dau, cuoi))
    ho = ho_vang(c)
    gom = {}

    def _o(ma):
        ma = (ma or "").strip() or "—"
        tin = ho.get(ma) or {"ho": ma.upper(), "ten": ma}
        return gom.setdefault(tin["ho"], {"ho": tin["ho"], "ma_ra": "", "ma_vao": "",
                                          "ra_chi": M.D0, "ra_tien": M.D0, "ra_dong": 0,
                                          "vao_chi": M.D0, "vao_tien": M.D0, "vao_dong": 0}), ma

    for r in ra:
        g, ma = _o(r["ma"])
        g.update(ma_ra=ma, ra_chi=g["ra_chi"] + chi(r["ly"]), ra_tien=g["ra_tien"] + M.dec(r["tien"]),
                 ra_dong=g["ra_dong"] + int(r["dong"] or 0))
    for r in vao:
        g, ma = _o(r["ma"])
        g.update(ma_vao=ma, vao_chi=g["vao_chi"] + chi(r["ly"]), vao_tien=g["vao_tien"] + M.dec(r["tien"]),
                 vao_dong=g["vao_dong"] + int(r["dong"] or 0))
    ds = sorted(gom.values(), key=lambda g: g["ra_chi"] + g["vao_chi"], reverse=True)
    for g in ds:
        g["lech_chi"] = g["ra_chi"] - g["vao_chi"]          # dương = kho vơi, âm = kho dày thêm
        g["ten"] = g["ma_ra"] or g["ma_vao"] or g["ho"]
    return ds


def hang_theo_nhom(c, d1, d2):
    """Hàng đã bán, chi tiết tới TỪNG NHÓM HÀNG × TỪNG LOẠI VÀNG: số món · chỉ · tiền (GĐ 12/09/2026).

    Nhóm hàng (Kiềng · Cara · Dây · Lắc · Bông…) nằm ở ``I_PRODUCT_GROUP``, ghép qua ``GroupID`` —
    cột ``GroupCode`` ngay trên dòng bán đang RỖNG nên không dùng được, phải join bảng nhóm.
    Mỗi dòng bán là một món (SL luôn = 1) nên số món đếm theo dòng.
    """
    dau, cuoi = _khoang(d1, d2)
    rows = c.query(
        "SELECT ISNULL(g.GroupName, N'(chưa xếp nhóm)') nhom, s.GoldCode ma, COUNT(*) mon, "
        "SUM(ISNULL(s.GoldReal,0)) ly, SUM(ISNULL(s.SellAmount,0)) tien "
        "FROM TRN_RT_BUYSELL_SELL s WITH (NOLOCK) JOIN TRN_RT_BUYSELL b WITH (NOLOCK) ON b.TrnID = s.TrnID "
        "LEFT JOIN I_PRODUCT_GROUP g WITH (NOLOCK) ON g.GroupID = s.GroupID "
        "WHERE b.IsDel='0' AND b.Status='C' AND b.CreatedDate >= ? AND b.CreatedDate < ? "
        "GROUP BY ISNULL(g.GroupName, N'(chưa xếp nhóm)'), s.GoldCode", (dau, cuoi))
    gom = {}
    for r in rows:
        ten = (r["nhom"] or "").strip() or "(chưa xếp nhóm)"
        n = gom.setdefault(ten, {"nhom": ten, "mon": 0, "chi": M.D0, "tien": M.D0, "vang": []})
        n["vang"].append({"ma": (r["ma"] or "").strip() or "—", "mon": int(r["mon"] or 0),
                          "chi": chi(r["ly"]), "tien": M.dec(r["tien"])})
        n["mon"] += int(r["mon"] or 0)
        n["chi"] += chi(r["ly"])
        n["tien"] += M.dec(r["tien"])
    ds = sorted(gom.values(), key=lambda n: n["tien"], reverse=True)
    for n in ds:
        n["vang"].sort(key=lambda v: v["tien"], reverse=True)
    return ds


def theo_ngay(c, d1, d2):
    """Chuỗi theo NGÀY để nhìn xu hướng; kỳ 1 ngày thì chỉ có 1 dòng."""
    dau, cuoi = _khoang(d1, d2)
    ban = {r["ngay"]: r for r in c.query(
        "SELECT CONVERT(varchar(10), CreatedDate, 23) ngay, COUNT(*) so, "
        "SUM(ISNULL(SellTotalAmount,0)) thuc, SUM(ISNULL(PayAmount,0)) rong, "
        "SUM(CASE WHEN PayAmount > 0 THEN PayAmount ELSE 0 END) hh "
        "FROM TRN_RT_BUYSELL WITH (NOLOCK) WHERE IsDel='0' AND Status='C' "
        "AND CreatedDate >= ? AND CreatedDate < ? GROUP BY CONVERT(varchar(10), CreatedDate, 23)", (dau, cuoi))}
    thau = {r["ngay"]: r for r in c.query(
        "SELECT CONVERT(varchar(10), CreatedDate, 23) ngay, COUNT(*) so, SUM(ISNULL(TotalAmount,0)) tien, "
        "SUM(ISNULL(GoldWeight,0)) ly FROM TRN_RT_BUYGOLD WITH (NOLOCK) WHERE IsDel='0' AND Status='C' "
        "AND CreatedDate >= ? AND CreatedDate < ? GROUP BY CONVERT(varchar(10), CreatedDate, 23)", (dau, cuoi))}
    ra, ngay = [], dt.date.fromisoformat(d1)
    het = dt.date.fromisoformat(d2)
    while ngay <= het:
        k = ngay.isoformat()
        b, t = ban.get(k) or {}, thau.get(k) or {}
        ra.append({"ngay": ngay, "so_ban": int(b.get("so") or 0), "thuc": M.dec(b.get("thuc")),
                   "rong": M.dec(b.get("rong")), "hh": M.dec(b.get("hh")),
                   "so_thau": int(t.get("so") or 0), "thau_tien": M.dec(t.get("tien")),
                   "thau_chi": chi(t.get("ly"))})
        ngay += dt.timedelta(days=1)
    return ra


def khach_moi(c, d1, d2):
    """Khách lập hồ sơ mới trong kỳ (I_CUSTOMER.DateOfJoining)."""
    dau, cuoi = _khoang(d1, d2)
    return int(_mot(c.query("SELECT COUNT(*) so FROM I_CUSTOMER WITH (NOLOCK) "
                            "WHERE DateOfJoining >= ? AND DateOfJoining < ?", (dau, cuoi))).get("so") or 0)


def chot_ky(c, d1, d2):
    """Gom trọn báo cáo một kỳ. Kỳ đã đóng thì số không đổi nữa → nhớ tạm lâu hơn."""
    hom_nay = dt.date.today().isoformat()
    khoa = f"khbl:bao_cao:v3:{c.target}:{d1}:{d2}"
    san = cache.get(khoa)
    if san is not None:
        return san
    ban = ban_tong(c, d1, d2)
    thau = thau_tong(c, d1, d2)
    kq = {
        "d1": d1, "d2": d2, "so_ngay": (dt.date.fromisoformat(d2) - dt.date.fromisoformat(d1)).days + 1,
        "ban": ban, "thau": thau, "nv": theo_nhan_vien(c, d1, d2), "vang": vang_theo_tuoi(c, d1, d2),
        "ngay": theo_ngay(c, d1, d2), "khach_moi": khach_moi(c, d1, d2),
        "nhom": hang_theo_nhom(c, d1, d2),
        # Tiền mặt thực nhận trong kỳ = khách trả tiền mặt khi mua − tiền mặt chi ra khi thâu vàng
        "tien_mat_rong": ban["tien_mat"] - thau["tien_mat"],
        "ck_rong": ban["chuyen_khoan"] - thau["chuyen_khoan"],
        "luc": dt.datetime.now(),
    }
    cache.set(khoa, kq, NHO_TAM if d2 >= hom_nay else NHO_TAM_CU)
    return kq


# ─────────────────────────── màn hình ───────────────────────────
KY = (("hom_nay", "Hôm nay"), ("hom_qua", "Hôm qua"), ("7ngay", "7 ngày"),
      ("thang_nay", "Tháng này"), ("thang_truoc", "Tháng trước"))


def khoang_theo_ky(ky, hom_nay=None):
    """Nút kỳ nhanh → (d1, d2). Kỳ lạ thì trả về HÔM NAY, không nổ."""
    n = hom_nay or dt.date.today()
    dau_thang = n.replace(day=1)
    if ky == "hom_qua":
        q = n - dt.timedelta(days=1)
        return q.isoformat(), q.isoformat()
    if ky == "7ngay":
        return (n - dt.timedelta(days=6)).isoformat(), n.isoformat()
    if ky == "thang_nay":
        return dau_thang.isoformat(), n.isoformat()
    if ky == "thang_truoc":
        cuoi = dau_thang - dt.timedelta(days=1)
        return cuoi.replace(day=1).isoformat(), cuoi.isoformat()
    return n.isoformat(), n.isoformat()


def duoc_xem(user):
    """Báo cáo là số liệu tiền bạc toàn tiệm → đi theo danh mục quyền BAO_CAO của ma trận."""
    from apps.pmv.models import UserModuleAccess

    return bool(user.is_authenticated and (user.is_superuser or UserModuleAccess.objects.filter(
        user=user, module="BAO_CAO", can_view=True).exists()))


def _ngay(v, mac_dinh):
    try:
        return dt.date.fromisoformat(str(v)[:10]).isoformat()
    except (TypeError, ValueError):
        return mac_dinh


def trang(request):
    """Trang BÁO CÁO. Lọc bằng kỳ nhanh hoặc hai ô ngày; HTMX chỉ thay phần số liệu."""
    from django.core.exceptions import PermissionDenied
    from django.shortcuts import render

    from . import services as S

    if not duoc_xem(request.user):
        raise PermissionDenied("Bạn chưa được cấp quyền xem BÁO CÁO.")
    hom_nay = dt.date.today()
    ky = request.GET.get("ky") or ""
    if ky:
        d1, d2 = khoang_theo_ky(ky, hom_nay)
    else:
        d1 = _ngay(request.GET.get("d1"), hom_nay.isoformat())
        d2 = _ngay(request.GET.get("d2"), hom_nay.isoformat())
        if d1 > d2:
            d1, d2 = d2, d1
    ctx = {"nav_active": "baocao", "ky": ky or "", "kys": KY, "d1": d1, "d2": d2,
           "hom_nay": hom_nay.isoformat()}
    try:
        ctx["bc"] = chot_ky(S.client("bao_cao"), d1, d2)
    except Exception as exc:                    # KK trục trặc thì báo rõ, không để trang trắng
        log.exception("Không dựng được báo cáo %s → %s", d1, d2)
        ctx["loi"] = S.error_message(exc)
    tep = "pos/_bao_cao_so_lieu.html" if request.headers.get("HX-Request") else "pos/bao_cao.html"
    return render(request, tep, ctx)
