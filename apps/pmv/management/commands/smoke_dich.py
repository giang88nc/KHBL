"""
smoke_dich — bộ hồi quy CÔNG TẮC ĐÍCH + CHỐT AN TOÀN CẤM GHI MÁY KK (03/09/2026).

Chạy: manage.py smoke_dich

Phần A kiểm công tắc và chốt — thuần logic, không ghi gì.
Phần B chạy CRUD THẬT trên BẢN THỬ máy Mr Giang, đi ĐÚNG con đường lúc chạy thật
(PmvClient -> gateway -> allowlist -> nhật ký), rồi tự dọn rác của mình.

⚠ Bộ này TUYỆT ĐỐI không bật settings.PMV_GHI_KK. Mọi phép thử liên quan máy KK đều
là phép thử BỊ CHẶN — chặn xảy ra TRƯỚC khi mở kết nối, nên không byte nào chạm KK.
"""
import datetime

from django.core.management.base import BaseCommand
from django.test import override_settings

from apps.pmv import gateway, sandbox
from apps.pmv.models import PmvAudit, PmvState


def PmvAudit_dem():
    return PmvAudit.objects.filter(tag="doi_dich", summary__contains="ĐỔI ĐÍCH DỮ LIỆU").count()
from apps.pmv import money as M
from apps.pmv.client import PmvClient, PmvProcError
from apps.pmv.gateway import PmvBlocked

NGUOI = "US1806000000001"   # tài khoản kimhanh2 trong SYS_USERS
TIEM = "TSP141100000001"
KET = "TIL260500000001"
NV = "EMP150400000001"
KHACH_VANG_LAI = "CU0000000000000"
TEN_THU = "KIEM THU CONG TAC DICH"


class Command(BaseCommand):
    help = "Hồi quy công tắc đích dữ liệu + chốt cấm ghi máy KK"

    def handle(self, *a, **o):
        self.dat, self.truot = 0, []
        self.rac_cust = []
        cong_tac_cu = PmvState.get(gateway.DICH_KEY, "")   # trả lại y nguyên khi xong
        try:
            self._phan_a()
            self._phan_b()
            self._phan_c()
        finally:
            self._don_dep()
            if cong_tac_cu:
                gateway.dat_dich(cong_tac_cu)
            else:
                gateway.xoa_cong_tac()
            self.stdout.write(f"  -> trả công tắc về {gateway.mo_ta_dich()}")
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

    # ---------- A. công tắc + chốt ----------
    def _phan_a(self):
        self.stdout.write(self.style.MIGRATE_HEADING("\nA. CÔNG TẮC ĐÍCH + CHỐT AN TOÀN (không ghi gì)"))

        from django.conf import settings

        # Chưa bật công tắc thì .env quyết định.
        # (xoa_cong_tac vừa bỏ ghi đè vừa dọn bộ đệm 2s của gateway rồi đọc lại — nếu gọi
        #  dich_hien_tai() trần ở đây sẽ trúng đệm của phép thử trước, không thấy .env đổi)
        with override_settings(PMV_TARGET="sandbox"):
            self.ok("A1 chưa bật công tắc thì theo .env", gateway.xoa_cong_tac() == "sandbox")
        with override_settings(PMV_TARGET="kk"):
            self.ok("A1b .env đổi thì đích đổi theo", gateway.xoa_cong_tac() == "kk")

        gateway.dat_dich("sandbox")
        self.ok("A2 PmvClient() đi theo công tắc", PmvClient().target == "sandbox")
        self.ok("A3 PmvClient('pmv') = máy KK (tương thích tên cũ)", PmvClient("pmv").target == "kk")
        self.ok("A4 PmvClient('sandbox') ép bản thử", PmvClient("sandbox").target == "sandbox")

        # công tắc trên trang Hệ thống THẮNG .env
        with override_settings(PMV_TARGET="kk"):
            self.ok("A4b công tắc ghi đè .env", gateway.dich_hien_tai() == "sandbox")

        if True:
            gateway.dat_dich("kk")
            self.ok("A5 bật công tắc sang KK là đích đổi NGAY", gateway.dich_hien_tai() == "kk")
            self.ok("A6 nghiệp vụ tự bám đích mới, không sửa code", PmvClient().target == "kk")
            self.ok("A6b công tắc lưu vào PmvState (sống qua RESET)",
                    PmvState.get(gateway.DICH_KEY) == "kk")

            # ══ phép thử QUAN TRỌNG NHẤT ══ ghi vào KK phải bị chặn
            with override_settings(PMV_GHI_KK=False):
                self.ok("A7 chốt an toàn đang TẮT", gateway.duoc_ghi_kk() is False)
                bi_chan = ""
                try:
                    gateway.pmv_call("I_CUSTOMER_Ins", {"p_CustName": "x"},
                                     tag="smoke_dich", write=True)
                except PmvBlocked as e:
                    bi_chan = str(e)
                self.ok("A8 GHI vào máy KK BỊ CHẶN dù proc nằm trong allowlist",
                        "cấm GHI vào máy KK" in bi_chan, bi_chan[:90] or "KHÔNG bị chặn — NGUY HIỂM")

                bi_chan2 = ""
                try:
                    PmvClient(tag="smoke_dich").call("TRN_RT_BUYSELL_Ins", write=True, p_TrnID="")
                except PmvBlocked as e:
                    bi_chan2 = str(e)
                except Exception as e:      # chặn phải xảy ra TRƯỚC cả bước dò chữ ký proc
                    bi_chan2 = f"(sai loại lỗi) {e}"
                self.ok("A9 đi qua PmvClient cũng bị chặn y hệt",
                        "cấm GHI vào máy KK" in bi_chan2, bi_chan2[:90])

            # đọc máy KK vẫn bình thường (chỉ SELECT, không ghi)
            r = gateway.pmv_read("SELECT TOP 1 ShopID FROM T_SHOP WITH (NOLOCK)",
                                 tag="smoke_dich", audit=False, target="kk")
            self.ok("A10 ĐỌC máy KK vẫn chạy (chốt chỉ cấm ghi)", bool(r), str(r)[:60])

        # hạ tầng không theo công tắc
        gateway.dat_dich("sandbox")
        n = gateway.pmv_read("SELECT COUNT(*) AS n FROM T_SHOP WITH (NOLOCK)",
                             tag="smoke_dich", audit=False)  # không truyền target
        self.ok("A11 lệnh không nêu đích vẫn đọc máy KK (backup/kiểm tra/đồng bộ)", bool(n))

        # proc lạ luôn bị chặn, không phụ thuộc đích
        for dich in ("sandbox", "kk"):
            gateway.dat_dich(dich)
            try:
                gateway.pmv_call("T_SHOP_Upd", {}, tag="smoke_dich", write=True)
                lot = True
            except PmvBlocked:
                lot = False
            self.ok(f"A12 proc ngoài allowlist bị chặn (đích {dich})", not lot)
        gateway.dat_dich("sandbox")

        # bỏ công tắc thì quyền quyết định trả về .env
        gateway.xoa_cong_tac()
        self.ok("A12b bỏ công tắc thì hết ghi đè",
                PmvState.get(gateway.DICH_KEY, "") == "")
        gateway.dat_dich("sandbox")

        self.ok("A13 allowlist GHI không chứa proc nguy hiểm",
                not (gateway.PROC_WRITE_ALLOW & {"T_PRODUCT_Del", "SYS_USERS_Upd", "T_SHOP_Upd"}))

        n = PmvAudit.objects.filter(kind="BLOCKED", tag="smoke_dich",
                                    summary__contains="CHẶN GHI VÀO MÁY KK").count()
        self.ok("A14 mỗi phát chặn đều vào nhật ký", n >= 2, f"{n} dòng")

    # ---------- B. CRUD thật trên bản thử, qua đúng cổng ----------
    def _phan_b(self):
        self.stdout.write(self.style.MIGRATE_HEADING(
            "\nB. CRUD THẬT trên BẢN THỬ (qua cổng, như lúc chạy thật)"))
        gateway.dat_dich("sandbox")
        with override_settings(PMV_GHI_KK=False):
            c = PmvClient(tag="smoke_dich")
            self.ok("B0 đang đứng ở bản thử", c.target == "sandbox", c.mo_ta())

            # --- khách hàng ---
            _, sets = c.call("I_CUSTOMER_Ins", write=True, day_du=True,
                             p_CustID="", p_CustCode="", p_CustName=TEN_THU,
                             p_Address="Kiểm thử tự động", p_Phone="0900000009",
                             p_Notes="", p_Active="1", p_CMND="", p_CustGroupID="",
                             p_CustTypeID="", p_BirthDate="", p_Gender="1", p_Email="",
                             p_DateOfJoining="", p_LastTradingDate="", TuDongNangHang="1",
                             p_Company="", p_Company_Address="", p_Masothue="", ShopID="",
                             p_NoiCap="", p_GhiChu2="", p_GhiChu3="")
            cust = self._lay(sets, "CustID")
            if cust:
                self.rac_cust.append(cust)
            self.ok("B1 tạo khách qua cổng", bool(cust), cust or str(sets)[:100])

            ten = c.query("SELECT CustName, CustCode FROM I_CUSTOMER WITH (NOLOCK) WHERE CustID=?",
                          (cust,))
            self.ok("B2 phần mềm tự cấp mã khách", bool(ten and ten[0]["CustCode"]),
                    (ten[0]["CustCode"] if ten else ""))

            # --- món hàng còn trong kho ---
            item, ma = None, ""
            for r in c.query("SELECT TOP 40 ProductCode FROM T_PRODUCT WITH (NOLOCK) "
                             "WHERE Status='I' AND SellTrnID IS NULL ORDER BY ProductCode DESC"):
                _, s2 = c.call("T_PRODUCT_GetByCodeForSell", raise_on_rc=False,
                               p_ProductCode=r["ProductCode"], p_TaskPrice=0, p_ShopID="",
                               p_CheckRealSL=1, p_TillID=KET, p_CustID=KHACH_VANG_LAI,
                               p_ShopID_XRate="", p_RutGon="0")
                row = s2[0][0] if (s2 and s2[0]) else None
                if row and str(row.get("ErrorCode", "0")) == "0" and M.dec(row.get("SellRate")) > 0:
                    item, ma = row, r["ProductCode"]
                    break
            self.ok("B3 lấy được món hàng còn trong kho", bool(item), ma)
            if not item:
                return

            tw, dw = M.dec(item.get("TotalWeight")), M.dec(item.get("DiamondWeight"))
            rate, task = M.dec(item.get("SellRate")), M.dec(item.get("TaskPrice"))
            pu = (item.get("PriceUnit") or "L").upper()
            tien = M.sell_amount(tw, dw, rate, task, pu)

            xml = c.xml_dataset("TRN_RT_BUYSELL_SELL", [{
                "ProductID": item.get("ProductID"), "ProductCode": ma,
                "ProductDesc": item.get("ProductDesc") or "", "PriceCcy": "VND", "PriceUnit": pu,
                "CcyRate": "1000.000", "CcyRateTaskPrice": "1000.000",
                "TotalWeight": c.num(tw), "GoldWeight": c.num(tw - dw), "DiamondWeight": c.num(dw),
                "SectionID": item.get("SectionID") or "", "SectionName": item.get("SectionName") or "",
                "TaskPrice": c.num(task), "RingSize": c.num(item.get("RingSize"), 2),
                "InPrice": "0.000", "GoldCode": item.get("GoldCode") or "",
                "GoldDesc": item.get("GoldDesc") or "", "SellRate": c.num(rate),
                "SellAmount": c.money(tien), "CatNi": "0", "GoldReal": c.num(tw - dw),
                "SL": "1", "SL_Ban": "1", "A": "1", "DiamondPrice": "0.000", "GiaBanMon": "0.000",
                "DiamondTaskPrice": "0.000", "DiamondInPrice": "0.000", "GiaVon": "0.000",
                "ProductTypeCode": item.get("ProductTypeCode") or "TTM",
                "TienCongThemMonHang": "0", "LoaiTienChiTra": "VND",
                "TienQuyDoi": c.money(tien), "Discounts": "0", "CongBanTrenTL": "0.000",
                "OrderBy": "0", "TienBotTrenChi": "0.000",
            }])
            now = datetime.datetime.now()
            _, sets = c.call("TRN_RT_BUYSELL_Ins", write=True, day_du=True,
                             p_TrnID="", p_TrnDate=c.fmt_date(now.date()), p_TrnTime=c.fmt_time(now),
                             p_CustID=cust or KHACH_VANG_LAI, p_GoldCode="",
                             p_SellTotalAmount=c.money(tien), p_BuyTotalAmount="0",
                             p_TotalAmount=c.money(tien), p_Discount="0", p_PayAmount=c.money(tien),
                             p_Status="W", p_CreatedBy=NGUOI, p_DebitAmount=0, p_IsCatGIa="0",
                             p_Add="0", p_TaskPriceAdd="0", p_EmpID=NV, DiscountPercent="0",
                             OldDebitAmount="0", p_ShopID=TIEM, p_TillID=KET, p_Description="",
                             p_BanLeBanSi="BL", p_TotalPromotionAmount=0,
                             p_Trn_RT_BUYSELL=xml,
                             p_TrnDateTime_Upd_GDN=PmvClient.MOC_TRONG, p_TrnID_GDN="",
                             Desc1="", Desc2="", Desc3="", Desc4="", Desc5="",
                             p_IsSync="0", p_TienKhachTraThuc="0", p_TienTraLai="0", p_SoHDTuNhap="")
            hd = self._lay(sets, "TrnID")
            self.ok("B4 lập hóa đơn qua cổng", bool(hd), f"{hd} · {M.money_vn(tien)}")
            if not hd:
                return

            tt = c.query("SELECT BillCode, Status FROM TRN_RT_BUYSELL WITH (NOLOCK) WHERE TrnID=?",
                         (hd,))
            self.ok("B5 hóa đơn ở trạng thái chờ (W) + có số bill",
                    bool(tt) and tt[0]["Status"] == "W" and bool(tt[0]["BillCode"]),
                    str(tt and tt[0]))

            # --- chốt ---
            c.call("TRN_RT_BUYSELL_Complete", write=True, p_TrnID=hd, p_UserID=NGUOI, p_ThuHo="0")
            c.call("T_TILL_TXN_Proc", write=True, p_TrnIDs=hd, p_TillID=KET, p_UserID=NGUOI)
            st = c.query("SELECT Status FROM TRN_RT_BUYSELL WITH (NOLOCK) WHERE TrnID=?", (hd,))
            self.ok("B6 chốt xong hóa đơn thành C", bool(st) and st[0]["Status"] == "C",
                    str(st and st[0]))
            sp = c.query("SELECT Status, SellTrnID FROM T_PRODUCT WITH (NOLOCK) WHERE ProductCode=?",
                         (ma,))
            self.ok("B7 món hàng chuyển sang ĐÃ BÁN", bool(sp) and sp[0]["Status"] == "S",
                    str(sp and sp[0]))

            # --- khóa lạc quan: mốc CŨ phải bị bắt ---
            moc_cu = "Jan  2 1900 12:00:00:000AM"
            c.call("T_TILL_TXN_Del", write=True, raise_on_rc=False,
                   p_TrnRefID=hd, pType="SRT", pCongNoBanLe=0, p_UserUpd=NGUOI,
                   p_TrnDateTime_Upd=moc_cu, p_TrnDateTime_Upd_GDN=PmvClient.MOC_TRONG, p_Type="1")
            st = c.query("SELECT Status FROM TRN_RT_BUYSELL WITH (NOLOCK) WHERE TrnID=?", (hd,))
            self.ok("B8 mốc khóa SAI thì proc im lặng không làm gì (bẫy đã biết)",
                    bool(st) and st[0]["Status"] == "C", str(st and st[0]))

            bat_duoc = ""
            try:
                c.goi_co_khoa("T_TILL_TXN_Del", bang="TRN_RT_BUYSELL", cot_id="TrnID", gia_tri=hd,
                              kiem_tra=lambda cl: False,   # cố tình bảo "chưa đổi"
                              p_TrnRefID=hd, pType="SRT", pCongNoBanLe=0, p_UserUpd=NGUOI,
                              p_TrnDateTime_Upd_GDN=PmvClient.MOC_TRONG, p_Type="1")
            except PmvProcError as e:
                bat_duoc = str(e.sets[0].get("loi", ""))
            self.ok("B9 goi_co_khoa BẮT ĐƯỢC lệnh chạy mà không đổi gì",
                    "KHÔNG đổi" in bat_duoc, bat_duoc[:80])

            # --- hủy đúng cách ---
            st = c.query("SELECT Status FROM TRN_RT_BUYSELL WITH (NOLOCK) WHERE TrnID=?", (hd,))
            if st and st[0]["Status"] == "C":     # B9 có thể đã hủy phần két rồi
                c.goi_co_khoa("T_TILL_TXN_Del", bang="TRN_RT_BUYSELL", cot_id="TrnID", gia_tri=hd,
                              kiem_tra=lambda cl: (cl.query(
                                  "SELECT Status FROM TRN_RT_BUYSELL WITH (NOLOCK) WHERE TrnID=?",
                                  (hd,)) or [{}])[0].get("Status") != "C",
                              p_TrnRefID=hd, pType="SRT", pCongNoBanLe=0, p_UserUpd=NGUOI,
                              p_TrnDateTime_Upd_GDN=PmvClient.MOC_TRONG, p_Type="1")
            st = c.query("SELECT Status FROM TRN_RT_BUYSELL WITH (NOLOCK) WHERE TrnID=?", (hd,))
            self.ok("B10 hủy phần két thì hóa đơn về W", bool(st) and st[0]["Status"] == "W",
                    str(st and st[0]))

            c.goi_co_khoa("TRN_RT_BUYSELL_Del", bang="TRN_RT_BUYSELL", cot_id="TrnID", gia_tri=hd,
                          kiem_tra=lambda cl: not cl.query(
                              "SELECT TrnID FROM TRN_RT_BUYSELL WITH (NOLOCK) "
                              "WHERE TrnID=? AND IsDel='0'", (hd,)),
                          p_TrnID=hd, p_UserUpd=NGUOI,
                          p_TrnDateTime_Upd_GDN=PmvClient.MOC_TRONG, p_LogDel="0")
            con = c.query("SELECT TrnID FROM TRN_RT_BUYSELL WITH (NOLOCK) WHERE TrnID=?", (hd,))
            self.ok("B11 hóa đơn đã xóa hẳn", not con, str(con)[:60])
            sp = c.query("SELECT Status, SellTrnID FROM T_PRODUCT WITH (NOLOCK) WHERE ProductCode=?",
                         (ma,))
            self.ok("B12 món hàng TRẢ VỀ KHO (I, không còn gắn hóa đơn)",
                    bool(sp) and sp[0]["Status"] == "I" and not sp[0]["SellTrnID"], str(sp and sp[0]))
            ket = c.query("SELECT TillTxnID FROM T_TILL_TXN WITH (NOLOCK) WHERE TrnRefID=?", (hd,))
            self.ok("B13 phiếu két cũng sạch", not ket, str(ket)[:60])

    # ---------- C. công tắc trên trang Hệ thống ----------
    def _phan_c(self):
        self.stdout.write(self.style.MIGRATE_HEADING(
            "\nC. CÔNG TẮC TRÊN TRANG HỆ THỐNG (/he-thong/)"))
        from django.contrib.auth import get_user_model
        from django.test import Client

        u = get_user_model().objects.filter(username="kimhanh2").first() or get_user_model().objects.first()
        cl = Client()
        cl.force_login(u)
        with override_settings(ALLOWED_HOSTS=["testserver"], PMV_GHI_KK=False):
            gateway.dat_dich("sandbox")
            h = cl.get("/he-thong/").content.decode("utf-8", "replace")
            self.ok("C1 trang Hệ thống mở được + có khối công tắc", 'class="dich dich--sandbox"' in h)
            self.ok("C2 có link về Tổng quan", 'Về Tổng quan' in h and 'href="/"' in h)
            self.ok("C3 đang hiện đúng BẢN THỬ", "ĐANG DÙNG BẢN THỬ" in h)

            r = cl.post("/he-thong/doi-dich/", {"dich": "kk"})
            self.ok("C4 bấm chuyển sang KK → quay lại trang Hệ thống", r.status_code == 302)
            self.ok("C5 đích đã đổi NGAY, không cần RESET", gateway.dich_hien_tai() == "kk")

            h = cl.get("/he-thong/").content.decode("utf-8", "replace")
            self.ok("C6 khối công tắc chuyển ĐỎ + báo cổng đang cấm ghi",
                    'class="dich dich--kk"' in h and "CỔNG ĐANG CẤM GHI" in h)

            hb = cl.get("/banle/khach-hang/").content.decode("utf-8", "replace")
            self.ok("C7 đích ở chân trang màn bán lẻ cũng chuyển ĐỎ",
                    "khbl-foot__dich--kk" in hb and "DỮ LIỆU THẬT" in hb)

            # đang ở đích KK: ghi vẫn phải bị chặn
            try:
                gateway.pmv_call("I_CUSTOMER_Ins", {"p_CustName": "x"}, tag="smoke_dich", write=True)
                lot = True
            except PmvBlocked:
                lot = False
            self.ok("C8 ở đích KK, GHI vẫn bị chặn (chốt chỉ đổi được trong .env)", not lot)
            self.ok("C9 trang KHÔNG có chỗ bật chốt ghi KK từ web", "PMV_GHI_KK" not in h.split("<form")[0] or 'name="ghi_kk"' not in h)

            cl.post("/he-thong/doi-dich/", {"dich": "sandbox"})
            self.ok("C10 bấm về bản thử → đích quay lại", gateway.dich_hien_tai() == "sandbox")

            cl.post("/he-thong/doi-dich/", {"dich": "env"})
            self.ok("C11 bấm 'Theo .env' → bỏ ghi đè", PmvState.get(gateway.DICH_KEY, "") == "")

            n = PmvAudit_dem()
            self.ok("C12 mỗi lần đổi đích đều vào nhật ký", n >= 3, f"{n} dòng")

    # ---------- dọn ----------
    def _don_dep(self):
        if not self.rac_cust:
            return
        n = 0
        for cid in self.rac_cust:
            for bang in ("I_LICHSUTICHLUYDIEM", "T_CUSTOMER_DEBT", "I_DIEMTICHLUY", "I_CUSTOMER"):
                try:
                    n += sandbox.sandbox_don_dep(f"DELETE FROM {bang} WHERE CustID = ?", (cid,))
                except Exception:
                    pass
        self.stdout.write(f"\n  -> đã dọn {n} dòng rác kiểm thử trên bản thử")

    @staticmethod
    def _lay(sets, cot):
        for s in sets or []:
            for r in s or []:
                if cot in r and r[cot]:
                    return r[cot]
        return None
