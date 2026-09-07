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
        # 07/09/2026: giá bán món quét = giá MySQL gold_prices theo tuổi vàng (nguồn chính)
        r0 = kho[0][1]
        gmy = S.gia_mysql().get((r0.get("GoldCode") or "").strip())
        self.ok("B0b SellRate món quét = MySQL sell theo tuổi vàng",
                bool(gmy) and M.dec(r0.get("SellRate")) == gmy["SellRate"] and r0.get("gia_mysql") is True,
                f"{r0.get('GoldCode')} → {r0.get('SellRate')}")
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
            b5 = body(cl.post("/banle/ban-hang/thanh-toan/"))
            self.ok("C5 chưa chọn nhân viên thì KHÔNG cho thanh toán + lệnh FOCUS ô NV (08/09)",
                    "Chưa chọn nhân viên" in b5 and 'khblFocusLoi("' in b5
                    and "nv" in b5.split('khblFocusLoi("')[1][:24],   # "#o-nv" (escapejs đổi '-')
                    self._trich(b5))
            cl.post("/banle/ban-hang/dat/", {"emp": NV})

            b = body(cl.post("/banle/ban-hang/thanh-toan/"))
            self.ok("C6 THANH TOÁN xong, báo số hóa đơn", "Đã thanh toán" in b, self._trich(b))
            hd = self.c.query("SELECT TOP 1 TrnID, BillCode, Status, Description FROM TRN_RT_BUYSELL "
                              "WITH (NOLOCK) WHERE IsDel='0' ORDER BY CreatedDate DESC")[0]
            self.rac.append(hd["TrnID"])
            self.ok("C7 hóa đơn vào CSDL ở trạng thái ĐÃ CHỐT", hd["Status"] == B.CHOT_ROI,
                    f"{hd['BillCode']} · {hd['TrnID']}")
            self.ok("C8 ghi chú lưu theo", (hd["Description"] or "").strip() == "kiểm thử web")
            # 08/09/2026: thanh toán xong GIỮ ĐƠN CHỐT trên form (chế độ xem): chỉ IN bật, TT/TT&IN/… tắt
            self.ok("C8b thanh toán xong giữ đơn chốt trên form: badge ĐÃ CHỐT + IN bật + TT tắt",
                    "ĐÃ CHỐT" in b and f"hoa-don/xem/?trn_id={hd['TrnID']}" in b and "in=1" in b
                    and b.count('disabled title="Đơn đã thanh toán"') == 2 and 'pg-khoa-fs" title' in b)
            b3 = body(cl.post("/banle/ban-hang/in/dem/", {"trn_id": hd["TrnID"]}))
            self.ok("C8c nút 🖨 IN trong popup → đếm 'IN lần 1' vào bill_audit", "IN lần 1" in b3)
            self.ok("C8d ĐƠN MỚI xóa cả nhân viên bán",
                    'id="o-nv"' in body(cl.post("/banle/ban-hang/moi/")))

            b = body(cl.get("/banle/ban-hang/danh-sach/"))
            self.ok("C9 hóa đơn hiện trong popup DANH SÁCH", hd["BillCode"] in b)

            b = body(cl.post("/banle/ban-hang/mo/", {"trn_id": hd["TrnID"]}))
            self.ok("C10 mở hóa đơn đã chốt HÔM NAY → 🔒 + chân trang: XÓA · SỬA · IN bật, TT & TT&IN tắt",
                    "Đã mở" in b and "pg-khoa--dong" in b and "pg-ban__foot--khoa" in b
                    and all(x in b for x in ("🗑 XÓA", "🔓 SỬA", "IN HÓA ĐƠN", "xac-nhan/huy_hd", "xac-nhan/sua"))
                    and b.count('disabled title="Đơn đã thanh toán"') == 2 and 'pg-khoa-fs" title' in b
                    and "pg-ban__foot-tra" not in b)

            # 07/09/2026: đơn KHÓA → server chặn mọi thao tác giỏ, không chỉ ẩn nút
            b = body(cl.post("/banle/ban-hang/quet/", {"ma": ma1}))
            self.ok("C11 đơn khóa: quét món bị CHẶN kèm hướng dẫn passcode", "đang KHÓA" in b)
            self.ok("C11b đơn khóa: đổi NV / dẻ / bớt lẻ đều bị CHẶN",
                    all("đang KHÓA" in body(cl.post(u, d)) for u, d in (
                        ("/banle/ban-hang/dat/", {"emp": ""}),
                        ("/banle/ban-hang/vang-doi/", {"gold": de["GoldCode"], "tong_tl": "10", "tl_hot": "0", "gia": ""}),
                        ("/banle/ban-hang/bot-le/", {}))))
            bx = body(cl.get("/banle/ban-hang/xac-nhan/huy_hd/"))
            self.ok("C11c popup xác nhận chung: hành động · mã HĐ · người thao tác · hậu quả · ô passcode",
                    all(x in bx for x in ("HỦY HÓA ĐƠN", hd["BillCode"], "Người thao tác", u.username,
                                          "Hậu quả", 'name="passcode"')))
            self.ok("C11c2 URL cũ /mo-khoa/ = popup SỬA ĐƠN", "SỬA ĐƠN" in body(cl.get("/banle/ban-hang/mo-khoa/")))
            ng = cl.session["phieu"].get("ngay") or ""
            self.ok("C11c4 ngày đơn mở lên là ISO (proc trả dd/mm/yyyy → cart đổi)",
                    len(ng) == 10 and ng[4] == "-" and ng[7] == "-" and ng[:4].isdigit(), ng)
            self.ok("C11c3 badge 🔒 ĐÃ CHỐT ghi dd/mm/yyyy HH:MM; IN mở popup GĐB theo trn_id + nguon=live",
                    f"hoa-don/xem/?trn_id={hd['TrnID']}" in b and ("nguon=live&in=1" in b or "nguon=live&amp;in=1" in b)
                    and cl.session["phieu"].get("gio", "")[:2].isdigit(),
                    f"gio={cl.session['phieu'].get('gio')}")
            # đơn ngày cũ → chỉ xem: giả lập bằng cách lùi ngày trong session
            s0 = cl.session; ng0 = s0["phieu"]["ngay"]; s0["phieu"]["ngay"] = "2020-01-01"; s0.save()
            bc = body(cl.get("/banle/ban-hang/"))
            self.ok("C11c5 đơn chốt NGÀY CŨ → cả 5 nút (kể cả IN) đều tắt, 🔒 không bấm được",
                    bc.count('disabled title="Hóa đơn ngày khác') == 3 and "pg-khoa--cu" in bc and "xac-nhan/" not in bc
                    and "hoa-don/xem/" not in bc)
            self.ok("C11c6 server cũng chặn sửa/hủy đơn ngày cũ",
                    "NGÀY KHÁC" in body(cl.post("/banle/ban-hang/thuc-hien/sua/", {"passcode": "x"})))
            s0 = cl.session; s0["phieu"]["ngay"] = ng0; s0.save()
            # passcode RIÊNG từng user (bảng unlock_passcodes) — đặt tạm cho user smoke rồi trả lại như cũ
            from apps.pos.models import UnlockPasscode
            cu = UnlockPasscode.objects.filter(user=u).first()
            hash_cu = cu.hash if cu else None
            pc, _ = UnlockPasscode.objects.get_or_create(user=u, defaults={"hash": "!"})
            pc.dat("smoke-1276")
            try:
                with override_settings(KHBL_UNLOCK_PASSCODE="env-khac"):
                    b = body(cl.post("/banle/ban-hang/mo-lai/", {"passcode": "env-khac"}))
                    self.ok("C11d có passcode RIÊNG thì .env KHÔNG còn tác dụng (ưu tiên bản ghi user)",
                            "không đúng" in b and self._trang_thai(hd["TrnID"]) == B.CHOT_ROI)
                b = body(cl.post("/banle/ban-hang/mo-lai/", {"passcode": "sai"}))
                self.ok("C11e passcode SAI → báo lỗi, đơn vẫn ĐÃ CHỐT",
                        "không đúng" in b and self._trang_thai(hd["TrnID"]) == B.CHOT_ROI)
                # popup 🔑 đổi passcode: sai hiện tại → chặn; đúng → đổi được; đổi xong mở khóa bằng mã mới
                b = body(cl.post("/tai-khoan/passcode/luu/", {"user": u.pk, "hien_tai": "sai", "moi": "9999", "moi2": "9999"}))
                self.ok("C11f đổi passcode: nhập hiện tại SAI → từ chối", "không đúng" in b)
                b = body(cl.post("/tai-khoan/passcode/luu/", {"user": u.pk, "hien_tai": "smoke-1276", "moi": "9999", "moi2": "9998"}))
                self.ok("C11g đổi passcode: 2 lần mới không khớp → từ chối", "không khớp" in b)
                b = body(cl.post("/tai-khoan/passcode/luu/", {"user": u.pk, "hien_tai": "smoke-1276", "moi": "9999", "moi2": "9999"}))
                self.ok("C11h đổi passcode đúng → lưu (băm, không plain)",
                        "Đã lưu" in b and UnlockPasscode.objects.get(user=u).kiem("9999")
                        and "9999" not in UnlockPasscode.objects.get(user=u).hash)
                # chống 2 người cùng sửa: mốc trong session lệch mốc KK → từ chối, không đụng KK
                s = cl.session; upd_that = s["phieu"].get("upd"); s["phieu"]["upd"] = "1900-01-01 00:00:00"; s.save()
                b = body(cl.post("/banle/ban-hang/thuc-hien/sua/", {"passcode": "9999"}))
                self.ok("C11i mốc TrnDateTime_Upd lệch → báo 'người khác sửa', đơn vẫn CHỐT",
                        "người khác sửa" in b and self._trang_thai(hd["TrnID"]) == B.CHOT_ROI and bool(upd_that))
                s = cl.session; s["phieu"]["upd"] = upd_that; s.save()

                from apps.pos.models import BillAudit
                n0 = BillAudit.objects.filter(trn_id=hd["TrnID"]).count()
                b = body(cl.post("/banle/ban-hang/mo-lai/", {"passcode": "9999"}))
                self.ok("C12 SỬA ĐƠN (passcode mới) → về nháp, Ở LẠI form, icon 🔓",
                        self._trang_thai(hd["TrnID"]) == B.NHAP and "pg-khoa--mo" in b and hd["BillCode"] in b,
                        f"tt={self._trang_thai(hd['TrnID'])} | {self._trich(b)}")
                a = BillAudit.objects.filter(trn_id=hd["TrnID"]).order_by("-id").first()
                self.ok("C12b audit SUA ghi kèm ảnh chụp đơn + người + không sửa được",
                        a and a.action == "SUA" and a.username == u.username and a.before.get("status") == "C"
                        and BillAudit.objects.filter(trn_id=hd["TrnID"]).count() == n0 + 1)
                try:
                    a.note = "x"; a.save(); sua_duoc = True
                except PermissionError:
                    sua_duoc = False
                self.ok("C12c bill_audit append-only: save() sửa dòng cũ bị từ chối", not sua_duoc)
                self.ok("C12d đơn đang SỬA → KHÔNG in được (403 trang in cũ + in/dem từ chối)",
                        cl.get(f"/banle/ban-hang/in/?trn_id={hd['TrnID']}").status_code == 403
                        and "chưa thanh toán" in body(cl.post("/banle/ban-hang/in/dem/", {})))
                self.ok("C12e audit có dòng CHOT (lúc C6) + IN lần 1 (lúc C8c)",
                        BillAudit.objects.filter(trn_id=hd["TrnID"], action="CHOT").exists()
                        and BillAudit.objects.filter(trn_id=hd["TrnID"], action="IN", version=1).exists())

                # thanh toán lại → HỦY THANH TOÁN (passcode) → về nháp, form TRẮNG, đơn ở DS chờ
                bf = body(cl.post("/banle/ban-hang/thanh-toan/", {"in": "1"}))
                self.ok("C12f THANH TOÁN & IN → CHỐT + lệnh in thẳng iframe ẩn (khblInThang, raw=1&auto=1), KHÔNG popup",
                        self._trang_thai(hd["TrnID"]) == B.CHOT_ROI and "khblInThang(" in bf
                        and "raw=1&auto=1" in bf and 'htmx.ajax("GET"' not in bf and "{#" not in bf
                        and cl.post("/banle/ban-hang/in/dem/?im=1", {"trn_id": hd["TrnID"]}).status_code == 204)
                braw = body(cl.get(f"/banle/hoa-don/xem/?trn_id={hd['TrnID']}&loai=BAN&nguon=live&raw=1&auto=1"))
                self.ok("C12f2 trang in thẳng raw=1: standalone (không modal), có tờ GĐB, tự window.print()",
                        'class="gdb-a5"' in braw and "khbl-modal" not in braw and "window.print()" in braw
                        and hd["BillCode"] in braw)
                cl.post("/banle/ban-hang/mo/", {"trn_id": hd["TrnID"]})
                b = body(cl.post("/banle/ban-hang/thuc-hien/huy_tt/", {"passcode": "9999"}))
                self.ok("C12g HỦY THANH TOÁN → nháp + form trắng",
                        self._trang_thai(hd["TrnID"]) == B.NHAP and "Chưa có món nào" in b
                        and BillAudit.objects.filter(trn_id=hd["TrnID"], action="HUY_TT").exists())
                # thanh toán lại → HỦY HÓA ĐƠN 1 bước (passcode) → hoàn két + xóa
                cl.post("/banle/ban-hang/mo/", {"trn_id": hd["TrnID"]})
                cl.post("/banle/ban-hang/thanh-toan/")
                cl.post("/banle/ban-hang/mo/", {"trn_id": hd["TrnID"]})
                self.ok("C12h nháp/chốt đúng trước khi hủy HĐ", self._trang_thai(hd["TrnID"]) == B.CHOT_ROI)
                b = body(cl.post("/banle/ban-hang/thuc-hien/huy_hd/", {"passcode": "9999"}))
                self.ok("C13 HỦY HÓA ĐƠN đã chốt 1 bước (passcode) → 'Đã HỦY', audit HUY_HD",
                        "Đã HỦY HÓA ĐƠN" in b and BillAudit.objects.filter(trn_id=hd["TrnID"], action="HUY_HD").exists(),
                        self._trich(b))
            finally:
                if hash_cu:
                    UnlockPasscode.objects.filter(user=u).update(hash=hash_cu)
                else:
                    UnlockPasscode.objects.filter(user=u).delete()
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
        self._phan_ngang()
        self._phan_botle()

    def _phan_ngang(self):
        """F. ĐỔI NGANG theo a = TL vàng khách − TL vàng bán cùng loại (GĐ chốt 07/09/2026):
        a>0 → 2 dòng (hạn mức×bán ra + a×THÂU MySQL, dù ô GIÁ đang là giá bán ra) · a≤0 → 1 dòng bán ra ·
        TÍNH LẠI theo trạng thái tick. Quét món thật trên sandbox → tạo đơn W → dọn bằng XÓA ĐƠN."""
        self.stdout.write(self.style.MIGRATE_HEADING("\nF. ĐỔI NGANG: a = vàng khách − vàng bán cùng loại"))
        from django.contrib.auth import get_user_model
        from django.test import Client, override_settings
        u = get_user_model().objects.filter(username="kimhanh2").first()
        if not u:
            return
        cl = Client(); cl.force_login(u)
        body = lambda r: r.content.decode("utf-8", "replace")
        de_map = {M.de_base(x["GoldCode"]): x for x in S.loai_de()
                  if M.dec(x["BuyRate"]) > 0 and M.dec(x["SellRate"]) > 0}
        # món có dẻ cùng loại trong bảng giá
        # ưu tiên loại có giá MUA ≠ BÁN (18K/24K/9999) để F3 thật sự bắt được lỗi "dư ăn giá bán ra"
        cands = [(code, r) for code, r in self._kho(40) if (r.get("GoldCode") or "").strip() in de_map]
        khac = [(c, r) for c, r in cands
                if M.dec(de_map[r["GoldCode"].strip()]["SellRate"]) != M.dec(de_map[r["GoldCode"].strip()]["BuyRate"])]
        mon = (khac or cands or [None])[0]
        if not mon:
            self.ok("F0 có món bán mà bảng giá có dẻ cùng loại (giá mua & bán > 0)", False)
            return
        code, r = mon
        de = de_map[(r.get("GoldCode") or "").strip()]
        w_ban = M.dec(r.get("TotalWeight")) - M.dec(r.get("DiamondWeight"))
        sell, buy = M.dec(de["SellRate"]), M.dec(de["BuyRate"])
        doi = lambda: [x["row"] for x in cl.session["phieu"]["doi"]]
        with override_settings(ALLOWED_HOSTS=["testserver"]):
            cl.post("/banle/ban-hang/moi/")
            cl.post("/banle/ban-hang/quet/", {"ma": code})
            self.ok("F0 quét món cùng loại dẻ", len(cl.session["phieu"]["ban"]) == 1,
                    f"{code} {r.get('GoldCode')} TL vàng {w_ban} · dẻ {de['GoldCode']} bán {sell} / thâu {buy}")
            try:
                # a > 0: khách đưa nhiều hơn 50 đơn vị; ô GIÁ gửi đúng GIÁ BÁN RA như UI đang điền khi tick
                gia_ui = str(int(sell * M.RATE_SCALE))
                cl.post("/banle/ban-hang/vang-doi/", {"gold": de["GoldCode"], "tong_tl": str(w_ban + 50),
                                                       "tl_hot": "0", "gia": gia_ui, "doi_ngang": "1"})
                d = doi()
                ng = [x for x in d if x["DoiNgang"] == "1"]; th = [x for x in d if x["DoiNgang"] != "1"]
                self.ok("F1 a>0 → 2 dòng: 1 ngang + 1 thâu", len(ng) == 1 and len(th) == 1, f"{len(d)} dòng")
                self.ok("F2 dòng ngang = hạn mức (TL vàng bán) × giá BÁN RA",
                        ng and M.dec(ng[0]["GoldWeight"]) == w_ban and M.dec(ng[0]["BuyRate"]) == sell)
                self.ok("F3 dòng dư a=50 tính giá THÂU MySQL (không bị ăn giá bán ra từ ô GIÁ — lỗi cũ)",
                        th and M.dec(th[0]["GoldWeight"]) == 50 and M.dec(th[0]["BuyRate"]) == buy,
                        f"rate dư = {th[0]['BuyRate'] if th else '?'}")
                # bỏ tick + TÍNH LẠI → mỗi loại 1 dòng giá thâu
                b = body(cl.post("/banle/ban-hang/vang-doi/tinh-lai/", {"doi_ngang": ""}))
                d = doi()
                self.ok("F4 bỏ tick + TÍNH LẠI → 1 dòng toàn bộ giá thâu",
                        len(d) == 1 and d[0]["DoiNgang"] != "1" and M.dec(d[0]["BuyRate"]) == buy
                        and M.dec(d[0]["GoldWeight"]) == w_ban + 50)
                self.ok("F4b checkbox trả về trạng thái BỎ TICK", 'id="o-doingang" name="doi_ngang_ui" value="1" >' in b
                        or ('id="o-doingang"' in b and "checked" not in b.split('id="o-doingang"')[1][:60]))
                # tick lại + TÍNH LẠI → 2 dòng như F1
                cl.post("/banle/ban-hang/vang-doi/tinh-lai/", {"doi_ngang": "1"})
                d = doi()
                self.ok("F5 tick + TÍNH LẠI → lại 2 dòng ngang/thâu theo bảng giá",
                        len(d) == 2 and sum(1 for x in d if x["DoiNgang"] == "1") == 1)
                # a ≤ 0: khách đưa ÍT hơn hàng bán → 1 dòng toàn bộ giá bán ra (ô GIÁ để trống → MySQL sell)
                cl.post("/banle/ban-hang/vang-doi/tinh-lai/", {"doi_ngang": ""})  # gom về 1 dòng
                for i in range(len(doi())):
                    cl.post("/banle/ban-hang/vang-doi/xoa/", {"i": "0"})
                it = max(w_ban - 10, M.dec(1))
                cl.post("/banle/ban-hang/vang-doi/", {"gold": de["GoldCode"], "tong_tl": str(it),
                                                       "tl_hot": "0", "gia": "", "doi_ngang": "1"})
                d = doi()
                self.ok("F6 a≤0 → 1 dòng ngang, toàn bộ × giá bán ra",
                        len(d) == 1 and d[0]["DoiNgang"] == "1" and M.dec(d[0]["BuyRate"]) == sell)
                # hột không tính tiền: tổng 60 hột 10 (đơn vị nhập) → TL vàng 50
                cl.post("/banle/ban-hang/vang-doi/xoa/", {"i": "0"})
                cl.post("/banle/ban-hang/vang-doi/", {"gold": de["GoldCode"], "tong_tl": str(w_ban + 60),
                                                       "tl_hot": "60", "gia": "", "doi_ngang": "1"})
                d = doi()
                self.ok("F7 hột không tính tiền: tổng = bán+60, hột 60 → TL vàng = hạn mức → 1 dòng ngang",
                        len(d) == 1 and d[0]["DoiNgang"] == "1" and M.dec(d[0]["GoldWeight"]) == w_ban)
            finally:
                cl.post("/banle/ban-hang/huy/")   # xóa đơn W sandbox, hàng về kho
                cl.post("/banle/ban-hang/moi/")

    def _phan_botle(self):
        """Gợi ý BỚT LẺ phải là dict {tien, val} — val = số nguyên cho hx-vals (đừng để rỗng → bot=0)."""
        self.stdout.write(self.style.MIGRATE_HEADING("\nE. GỢI Ý BỚT LẺ (cart.tong)"))
        from django.test import RequestFactory
        from django.contrib.sessions.backends.db import SessionStore
        from apps.pos import cart
        req = RequestFactory().get("/"); req.session = SessionStore()
        g = cart.get(req)
        g["ban"] = [{"tien": "7346000", "row": {"GoldReal": "0", "GoldCode": "18K"}}]
        cart.save(req, g)
        goiy = cart.tong(req)["botle_goiy"]
        self.ok("E1 đúng 3 mức [6.000·16.000·26.000]",
                [x["val"] for x in goiy] == ["6000", "16000", "26000"], str(goiy))
        self.ok("E2 val = số nguyên khác rỗng (hx-vals không ra bot=0)",
                all(x["val"] and x["val"] == str(int(x["tien"])) for x in goiy))

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
                # dòng thô đi qua đúng đường web (services._quet → phủ giá MySQL 07/09/2026)
                ra.append((r["ProductCode"], S.quet_ma_row(r["ProductCode"], till_id=KET)))
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
                # v5 chốt 2: đơn ĐÃ THANH TOÁN phải HỦY THANH TOÁN (mo_lai) trước rồi mới XÓA
                if self._trang_thai(hd) == B.CHOT_ROI:
                    B.mo_lai(hd, user_id=NGUOI, c=self.c)
                B.huy(hd, user_id=NGUOI, c=self.c)
                self.stdout.write(f"\n  -> đã dọn hóa đơn kiểm thử {hd}")
            except Exception as e:
                self.stdout.write(self.style.WARNING(f"\n  ! còn treo hóa đơn {hd}: {e}"))
