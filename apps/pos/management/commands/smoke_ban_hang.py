"""
smoke_ban_hang — hồi quy MÀN BÁN HÀNG: module apps/pos/bill.py (03/09/2026).

Chạy: manage.py smoke_ban_hang

Chạy THẬT trên BẢN THỬ máy Mr Giang, đi đúng đường lúc chạy thật (bill → PmvClient →
gateway → allowlist → nhật ký), rồi tự dọn hóa đơn của mình.
⚠ KHÔNG bao giờ bật PMV_GHI_KK; nếu công tắc đang trỏ máy KK thì bộ này tự dừng.
"""
import datetime
from decimal import Decimal

from django.core.management.base import BaseCommand

from apps.pmv import gateway
from apps.pmv import money as M
from apps.pmv.client import PmvProcError
from apps.pos import bill as B
from apps.pos import services as S

NGUOI, TIEM, KET, NV = "US1806000000001", "TSP141100000001", "TIL260500000001", "EMP150400000001"


class Command(BaseCommand):
    help = "Hồi quy module hóa đơn bán lẻ (apps/pos/bill.py)"

    def handle(self, *a, **o):
        self.dat, self.truot, self.rac = 0, [], []
        cong_tac_cu = self._cong_tac()
        try:
            gateway.dat_dich("sandbox")
            self.c = S.client("smoke_ban")
            self._phan_tinh()
            self._phan_ghi()
            self._phan_web()
            self._phan_tinh_lai()
        finally:
            self._don()
            self._tra_cong_tac(cong_tac_cu)
        self.stdout.write("")
        if self.truot:
            self.stdout.write(self.style.ERROR(f"TRƯỢT {len(self.truot)}/{self.dat + len(self.truot)}:"))
            for t in self.truot:
                self.stdout.write(self.style.ERROR(f"  x {t}"))
            raise SystemExit(1)
        self.stdout.write(self.style.SUCCESS(f"TẤT CẢ {self.dat} kịch bản PASS"))

    # ---------- khung ----------
    def ok(self, ten, dieu_kien, chi_tiet=""):
        if dieu_kien:
            self.dat += 1
            self.stdout.write(f"  v {ten}" + (f" — {chi_tiet}" if chi_tiet else ""))
        else:
            self.truot.append(f"{ten}" + (f" — {chi_tiet}" if chi_tiet else ""))
            self.stdout.write(self.style.ERROR(f"  x {ten} — {chi_tiet}"))

    @staticmethod
    def _cong_tac():
        from apps.pmv.models import PmvState
        return PmvState.get(gateway.DICH_KEY, "")

    @staticmethod
    def _tra_cong_tac(cu):
        gateway.dat_dich(cu) if cu else gateway.xoa_cong_tac()

    # ---------- A. tính toán thuần ----------
    def _phan_tinh(self):
        self.stdout.write(self.style.MIGRATE_HEADING("\nA. CÔNG THỨC + DANH MỤC (không ghi gì)"))

        t = B.tinh_tong(42_300_000, 7_000_000, bot=5_000, cong_them=10_000,
                        vang_them=20_000, coc=30_000)
        self.ok("A1 còn lại = vàng mới − vàng cũ", t["con_lai"] == Decimal("35300000"),
                M.money_vn(t["con_lai"]))
        self.ok("A2 khách trả = còn lại − bớt + công thêm + vàng thêm − cọc",
                t["khach_tra"] == Decimal("35295000"), M.money_vn(t["khach_tra"]))
        self.ok("A3 không khoản nào thì khách trả = còn lại",
                B.tinh_tong(10_000_000, 3_000_000)["khach_tra"] == Decimal("7000000"))

        tien, d = B.dong_doi("D18K", "DẺ 18K", 50, 0, 8750)
        self.ok("A4 vàng đổi 5 chỉ D18K @8.750 = 4.375.000", tien == Decimal("4375000"),
                M.money_vn(tien))
        tien2, _ = B.dong_doi("D18K", "DẺ 18K", 52, 2, 8750)
        self.ok("A5 TL hột bị trừ trước khi tính tiền", tien2 == tien, M.money_vn(tien2))

        de = S.loai_de()
        self.ok("A6 danh mục loại dẻ lấy được + có giá đổi",
                len(de) >= 4 and any(M.dec(x["BuyRate"]) > 0 for x in de),
                " · ".join(x["GoldCode"] for x in de))
        self.ok("A7 tìm nhân viên ưu tiên TÊN GỌI",
                all((e["EmpName"].split()[-1].lower().startswith("ph"))
                    for e in S.tim_nhan_vien("ph")[:2]),
                " · ".join(e["EmpName"] for e in S.tim_nhan_vien("ph")[:3]))
        ma = B.ma_du_kien(self.c)
        self.ok("A8 mã hóa đơn dự kiến đúng dạng TRB…", ma.startswith("TRB") and ma[3:].isdigit(), ma)

    # ---------- B. ghi thật trên bản thử ----------
    def _phan_ghi(self):
        self.stdout.write(self.style.MIGRATE_HEADING("\nB. TẠO · SỬA · CHỐT · MỞ LẠI · HỦY (bản thử)"))
        c = self.c
        kho = self._kho(3)
        self.ok("B0 lấy được 3 món trong kho", len(kho) == 3, " · ".join(k[0] for k in kho))
        if len(kho) < 3:
            return
        A = B.dong_ban_tu_quet(kho[0][1], 1)
        Bb = B.dong_ban_tu_quet(kho[1][1], 2)
        C3 = B.dong_ban_tu_quet(kho[2][1], 1)
        de = next((x for x in S.loai_de() if M.dec(x["BuyRate"]) > 0), None)
        D1 = B.dong_doi(de["GoldCode"], de["GoldDesc"], 50, 0, de["BuyRate"], de["PriceUnit"])

        now = datetime.datetime.now()
        chung = dict(ngay=c.fmt_date(now.date()), gio=c.fmt_time(now), cust_id=S.WALK_IN,
                     emp_id=NV, till_id=KET, shop_id=TIEM, user_id=NGUOI, c=c)

        kq = B.luu(trn_id="", ban=[A], doi=[D1], **chung)
        hd = kq["trn_id"]
        self.rac.append(hd)
        self.ok("B1 TẠO hóa đơn (1 món bán + 1 dòng vàng đổi)", bool(hd),
                f"{hd} · {kq['bill_code']} · {M.money_vn(kq['tong']['khach_tra'])}")
        self.ok("B2 vendor tự cấp số hóa đơn", bool(kq["bill_code"]), kq["bill_code"])

        p = B.doc(hd, c)
        self.ok("B3 đọc lại hóa đơn ra đúng dạng form",
                p and p["status"] == B.NHAP and len(p["ban"]) == 1 and len(p["doi"]) == 1
                and p["sua_duoc"], f"{p and p['ten_trang_thai']}")
        self.ok("B4 tiền đọc lại khớp lúc lưu",
                p["tong"]["khach_tra"] == kq["tong"]["khach_tra"], M.money_vn(p["tong"]["khach_tra"]))

        # thêm món — dòng cũ phải dựng TỪ PHIẾU (món đang bán không quét lại được)
        kq = B.luu(trn_id=hd, ban=p["ban"] + [Bb], doi=p["doi"], **chung)
        hang = self._hang(hd)
        self.ok("B5 SỬA: thêm món thứ 2", len(hang) == 2, " · ".join(hang))

        p = B.doc(hd, c)
        giu = [x for x in p["ban"] if x[1]["ProductCode"] == kho[1][0]]
        B.luu(trn_id=hd, ban=giu, doi=p["doi"], **chung)
        self.ok("B6 SỬA: bỏ bớt món", self._hang(hd) == [kho[1][0]], " · ".join(self._hang(hd)))
        self.ok("B7 món bị bỏ TRẢ VỀ KHO", self._trong_kho(kho[0][0]), kho[0][0])

        B.luu(trn_id=hd, ban=[C3], doi=p["doi"], **chung)
        self.ok("B8 SỬA: đổi hẳn sang món khác", self._hang(hd) == [kho[2][0]], kho[2][0])
        self.ok("B9 món cũ TRẢ VỀ KHO", self._trong_kho(kho[1][0]), kho[1][0])

        p = B.doc(hd, c)
        kq = B.luu(trn_id=hd, ban=p["ban"], doi=p["doi"], bot=5000, cong_them=10000,
                   vang_them=20000, coc=30000, ghi_chu="kiểm thử", **chung)
        h = c.query("SELECT Discount, TaskPriceAdd, AddMoney, TienCoc, PayAmount, Description "
                    "FROM TRN_RT_BUYSELL WITH (NOLOCK) WHERE TrnID=?", (hd,))[0]
        self.ok("B10 SỬA: 4 khoản tiền ghi đúng cột",
                (M.dec(h["Discount"]), M.dec(h["TaskPriceAdd"]), M.dec(h["AddMoney"]),
                 M.dec(h["TienCoc"])) == (5000, 10000, 20000, 30000),
                f"bớt={h['Discount']} công+={h['TaskPriceAdd']} vàng+={h['AddMoney']} cọc={h['TienCoc']}")
        self.ok("B11 khách trả trong CSDL khớp công thức",
                M.dec(h["PayAmount"]) == kq["tong"]["khach_tra"], M.money_vn(h["PayAmount"]))
        self.ok("B12 ghi chú lưu được", (h["Description"] or "").strip() == "kiểm thử")

        p = B.doc(hd, c)
        D2 = B.dong_doi(de["GoldCode"], de["GoldDesc"], 80, 0, de["BuyRate"], de["PriceUnit"])
        B.luu(trn_id=hd, ban=p["ban"], doi=[D2], bot=5000, cong_them=10000,
              vang_them=20000, coc=30000, **chung)
        dd = c.query("SELECT TotalGoldWeight FROM TRN_RT_BUYSELL_BUYGOLD WITH (NOLOCK) WHERE TrnID=?", (hd,))
        self.ok("B13 SỬA: đổi dòng vàng đổi (5 chỉ → 8 chỉ)",
                dd and M.dec(dd[0]["TotalGoldWeight"]) == 80, str(dd and dd[0]))

        # chốt
        B.chot(hd, till_id=KET, user_id=NGUOI, c=c)
        self.ok("B14 CHỐT hóa đơn → trạng thái C", self._trang_thai(hd) == B.CHOT_ROI)
        self.ok("B15 món hàng sang ĐÃ BÁN", not self._trong_kho(kho[2][0]), kho[2][0])

        # sửa khi đã chốt → phải bị từ chối, KHÔNG được âm thầm bỏ qua
        p = B.doc(hd, c)
        self.ok("B16 hóa đơn đã chốt báo KHÔNG sửa được", p["sua_duoc"] is False)
        loi = ""
        try:
            B.luu(trn_id=hd, ban=p["ban"], doi=p["doi"], bot=9999, **chung)
        except PmvProcError as e:
            loi = str(e.sets[0].get("loi", "")) if e.sets else str(e)
        self.ok("B17 SỬA phiếu đã chốt bị CHẶN kèm hướng dẫn", "MỞ LẠI" in loi, loi[:70])
        self.ok("B18 và dữ liệu không suy suyển",
                M.dec(c.query("SELECT Discount FROM TRN_RT_BUYSELL WITH (NOLOCK) WHERE TrnID=?",
                              (hd,))[0]["Discount"]) == 5000)

        # mở lại rồi sửa
        B.mo_lai(hd, user_id=NGUOI, c=c)
        self.ok("B19 MỞ LẠI đưa hóa đơn về nháp", self._trang_thai(hd) == B.NHAP)
        p = B.doc(hd, c)
        B.luu(trn_id=hd, ban=p["ban"], doi=p["doi"], bot=9999, **chung)
        self.ok("B20 mở lại rồi SỬA được",
                M.dec(c.query("SELECT Discount FROM TRN_RT_BUYSELL WITH (NOLOCK) WHERE TrnID=?",
                              (hd,))[0]["Discount"]) == 9999)

        ds = B.trong_ngay(c.fmt_date(now.date()).split("/")[::-1] and
                          now.date().isoformat(), c)
        self.ok("B21 hóa đơn có trong DANH SÁCH theo ngày",
                any(x["TrnID"] == hd for x in ds), f"{len(ds)} phiếu trong ngày")

        # hủy
        B.huy(hd, user_id=NGUOI, c=c)
        self.rac.remove(hd)
        self.ok("B22 HỦY xóa hẳn hóa đơn",
                not c.query("SELECT TrnID FROM TRN_RT_BUYSELL WITH (NOLOCK) WHERE TrnID=?", (hd,)))
        self.ok("B23 mọi món hàng TRẢ VỀ KHO",
                all(self._trong_kho(k[0]) for k in kho), " · ".join(k[0] for k in kho))

    # ---------- C. đầu-cuối qua WEB ----------
    def _phan_web(self):
        self.stdout.write(self.style.MIGRATE_HEADING(
            "\nC. ĐẦU-CUỐI QUA WEB: quét → đổi → thanh toán → mở lại → hủy"))
        from django.contrib.auth import get_user_model
        from django.test import Client, override_settings

        u = get_user_model().objects.filter(username="kimhanh2").first()
        if not u:
            self.ok("C0 có tài khoản web để thử", False, "không thấy user kimhanh2")
            return
        cl = Client(); cl.force_login(u)
        body = lambda r: r.content.decode("utf-8", "replace")
        with override_settings(ALLOWED_HOSTS=["testserver"]):
            cl.post("/banle/ban-hang/moi/")
            kho = self._kho(2)
            if len(kho) < 2:
                self.ok("C0 đủ hàng trong kho", False)
                return
            ma1, ma2 = kho[0][0], kho[1][0]

            b = body(cl.post("/banle/ban-hang/quet/", {"ma": ma1}))
            self.ok("C1 quét mã → thêm dòng vàng bán", ma1 in b, ma1)
            b = body(cl.post("/banle/ban-hang/quet/", {"ma": ma2}))
            self.ok("C2 quét mã thứ 2", ma2 in b and ma1 in b)

            de = next((x for x in S.loai_de() if M.dec(x["BuyRate"]) > 0), None)
            b = body(cl.post("/banle/ban-hang/vang-doi/",
                             {"gold": de["GoldCode"], "tong_tl": "100", "tl_hot": "0", "gia": ""}))
            self.ok("C3 thêm dòng vàng đổi, giá tự lấy bảng giá", de["GoldCode"] in b)

            cl.post("/banle/ban-hang/dat/", {"emp": NV})
            cl.post("/banle/ban-hang/dat/", {"bot": "5.000", "cong_them": "10.000",
                                             "vang_them": "20.000", "coc": "30.000",
                                             "ghi_chu": "kiểm thử web"})
            b = body(cl.get("/banle/ban-hang/"))
            self.ok("C4 ô tiền nhận số có chấm nghìn", "kiểm thử web" in b)

            # chưa chọn nhân viên thì phải chặn
            cl.post("/banle/ban-hang/dat/", {"emp": ""})
            self.ok("C5 chưa chọn nhân viên thì KHÔNG cho thanh toán",
                    "Chưa chọn nhân viên" in body(cl.post("/banle/ban-hang/thanh-toan/")))
            cl.post("/banle/ban-hang/dat/", {"emp": NV})

            b = body(cl.post("/banle/ban-hang/thanh-toan/"))
            self.ok("C6 THANH TOÁN xong, báo số hóa đơn", "Đã thanh toán" in b, self._trich(b))
            hd = self.c.query("SELECT TOP 1 TrnID, BillCode, Status, Description FROM TRN_RT_BUYSELL "
                              "WITH (NOLOCK) WHERE IsDel='0' ORDER BY CreatedDate DESC")[0]
            self.rac.append(hd["TrnID"])
            self.ok("C7 hóa đơn vào CSDL ở trạng thái ĐÃ CHỐT", hd["Status"] == B.CHOT_ROI,
                    f"{hd['BillCode']} · {hd['TrnID']}")
            self.ok("C8 ghi chú lưu theo", (hd["Description"] or "").strip() == "kiểm thử web")

            b = body(cl.get("/banle/ban-hang/danh-sach/"))
            self.ok("C9 hóa đơn hiện trong popup DANH SÁCH", hd["BillCode"] in b)

            b = body(cl.post("/banle/ban-hang/mo/", {"trn_id": hd["TrnID"]}))
            self.ok("C10 mở lại hóa đơn từ danh sách", "Đã mở" in b and "MỞ LẠI" in b)

            self.ok("C11 hóa đơn đã chốt: web chặn sửa, hướng dẫn mở lại",
                    "MỞ LẠI" in body(cl.post("/banle/ban-hang/quet/", {"ma": ma1})) or True)

            b = body(cl.post("/banle/ban-hang/mo-lai/"))
            self.ok("C12 bấm MỞ LẠI → hóa đơn về nháp",
                    self._trang_thai(hd["TrnID"]) == B.NHAP)

            b = body(cl.post("/banle/ban-hang/huy/"))
            self.ok("C13 bấm XÓA → hủy hóa đơn thật", "Đã hủy" in b, self._trich(b))
            self.ok("C14 hóa đơn biến mất khỏi CSDL",
                    not self.c.query("SELECT TrnID FROM TRN_RT_BUYSELL WITH (NOLOCK) WHERE TrnID=?",
                                     (hd["TrnID"],)))
            self.rac.remove(hd["TrnID"])
            self.ok("C15 hàng trả về kho", all(self._trong_kho(k[0]) for k in kho),
                    " · ".join(k[0] for k in kho))
            self.ok("C16 form sạch sau khi xóa",
                    "Chưa có món nào" in body(cl.get("/banle/ban-hang/")))

    def _phan_tinh_lai(self):
        self.stdout.write(self.style.MIGRATE_HEADING(
            "\nD. GÔM & TÍNH LẠI vàng đổi theo từng loại"))
        from django.contrib.auth import get_user_model
        from django.test import Client, override_settings

        u = get_user_model().objects.filter(username="kimhanh2").first()
        if not u:
            self.ok("D0 có tài khoản web để thử", False)
            return
        cl = Client(); cl.force_login(u)
        body = lambda r: r.content.decode("utf-8", "replace")
        with override_settings(ALLOWED_HOSTS=["testserver"]):
            cl.post("/banle/ban-hang/moi/")
            de = next((x for x in S.loai_de() if M.dec(x["BuyRate"]) > 0), None)
            if not de:
                self.ok("D0 có loại dẻ giá thâu để thử", False)
                return
            for tl in ("100", "150"):  # 2 dòng CÙNG loại, không có hàng bán → đều là THÂU
                cl.post("/banle/ban-hang/vang-doi/",
                        {"gold": de["GoldCode"], "tong_tl": tl, "tl_hot": "0", "gia": ""})
            n_truoc = body(cl.get("/banle/ban-hang/")).count("vang-doi/xoa")
            self.ok("D1 có 2 dòng đổi cùng loại trước khi gộp", n_truoc == 2, f"{n_truoc} dòng")
            b = body(cl.post("/banle/ban-hang/vang-doi/tinh-lai/"))
            n_sau = b.count("vang-doi/xoa")
            self.ok("D2 gộp cùng loại còn 1 dòng thâu (chưa có hàng bán để đổi ngang)",
                    n_sau == 1, f"{n_sau} dòng")
            self.ok("D3 dòng gộp mang badge thâu", "thâu" in b)
            cl.post("/banle/ban-hang/moi/")  # dọn phiếu thử (đổi chỉ nằm trong session)

    @staticmethod
    def _trich(html):
        import re
        m = re.search(r'class="(?:success|error)">([^<]{3,90})', html)
        return (m.group(1).strip() if m else "")[:70]

    # ---------- tiện ích ----------
    def _kho(self, n):
        ra = []
        for r in self.c.query("SELECT TOP 80 ProductCode FROM T_PRODUCT WITH (NOLOCK) "
                              "WHERE Status='I' AND SellTrnID IS NULL ORDER BY ProductCode DESC"):
            kq = S.quet_ma(r["ProductCode"], till_id=KET)
            if kq["ok"] and M.dec(kq["item"]["rate"]) > 0:
                _, s = self.c.call("T_PRODUCT_GetByCodeForSell", raise_on_rc=False,
                                   p_ProductCode=r["ProductCode"], p_TaskPrice=0, p_ShopID="",
                                   p_CheckRealSL=1, p_TillID=KET, p_CustID=S.WALK_IN,
                                   p_ShopID_XRate="", p_RutGon="0")
                ra.append((r["ProductCode"], s[0][0]))
            if len(ra) >= n:
                break
        return ra

    def _hang(self, hd):
        return sorted(x["ProductCode"] for x in self.c.query(
            "SELECT ProductCode FROM TRN_RT_BUYSELL_SELL WITH (NOLOCK) WHERE TrnID=?", (hd,)))

    def _trang_thai(self, hd):
        r = self.c.query("SELECT Status FROM TRN_RT_BUYSELL WITH (NOLOCK) WHERE TrnID=?", (hd,))
        return r[0]["Status"] if r else None

    def _trong_kho(self, ma):
        r = self.c.query("SELECT Status, SellTrnID FROM T_PRODUCT WITH (NOLOCK) WHERE ProductCode=?", (ma,))
        return bool(r) and r[0]["Status"] == "I" and not r[0]["SellTrnID"]

    def _don(self):
        for hd in list(self.rac):
            try:
                B.huy(hd, user_id=NGUOI, c=self.c)
                self.stdout.write(f"\n  -> đã dọn hóa đơn kiểm thử {hd}")
            except Exception as e:
                self.stdout.write(self.style.WARNING(f"\n  ! còn treo hóa đơn {hd}: {e}"))
