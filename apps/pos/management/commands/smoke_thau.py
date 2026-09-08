"""Bộ hồi quy THÂU VÀNG (THAU_VANG) trên SANDBOX — không bao giờ ghi máy KK.

Chạy trọn vòng đời qua bill.py (Ins → Upd → CompleteMore [+CARDPAY_Ins khi CK] → T_TILL_TXN_Proc → hủy TT → xóa)
và qua VIEW web (test client, ép đích sandbox trong tiến trình), so số dòng/số dư két trước–sau. Tự dọn bằng chính
chuỗi hủy của vendor; lỗi giữa chừng → dọn tay.
    manage.py smoke_thau
"""
import datetime
import json
import re

from django.core.management.base import BaseCommand, CommandError
from django.test import Client
from django.contrib.auth import get_user_model

from apps.pmv import gateway, money as M
from apps.pmv.client import PmvClient, PmvProcError


TABLES = ("TRN_RT_BUYGOLD", "T_TILL_TXN", "T_TILL_TXN_DETAIL", "T_TILL_BAL", "I_LICHSUTICHLUYDIEM", "I_DIEMTICHLUY",
          "TRN_RT_BUYGOLD_CardPay", "TRN_RT_BUYGOLD_Log", "T_TILL_TXN_Log")


class Command(BaseCommand):
    help = "Smoke THAU_VANG trên sandbox: lập → sửa → chốt (tiền mặt & CK) → hủy TT → xóa; kiểm từng bảng"

    def handle(self, *args, **opts):
        gateway.dich_hien_tai = lambda: "sandbox"          # ép đích cho MỌI PmvClient() trong tiến trình này
        from apps.pos import bill as B, services as S, views as V
        c = PmvClient("sandbox", tag="smoke_thau")
        self.ok, self.fail = 0, []
        trn = ""
        try:
            till = c.query("SELECT TOP 1 TillID FROM T_TILL WITH (NOLOCK) WHERE Active='1' ORDER BY TillID DESC")[0]["TillID"]
            emp = c.query("SELECT TOP 1 EmpID FROM T_EMPLOYEE WITH (NOLOCK) WHERE ISNULL(Active,'1')='1' ORDER BY EmpID")[0]["EmpID"]
            user = c.query("SELECT TOP 1 UserID FROM SYS_USERS WITH (NOLOCK) ORDER BY UserID")[0]["UserID"]
            shop = c.query("SELECT TOP 1 ShopID FROM T_SHOP WITH (NOLOCK) WHERE Active='1'")[0]["ShopID"]
            cust = c.query("SELECT TOP 1 c.CustID FROM I_CUSTOMER c WITH (NOLOCK) JOIN I_DIEMTICHLUY d WITH (NOLOCK) ON d.CustID=c.CustID "
                           "WHERE c.CustID<>'CU0000000000000' ORDER BY c.CustID DESC")[0]["CustID"]
            rate = M.dec(c.query("SELECT BuyRate FROM I_XRATE WITH (NOLOCK) WHERE GoldCcy='D18K'")[0]["BuyRate"]) or M.dec(8700)
            self.stdout.write(f"  két {till} · NV {emp} · user {user} · khách {cust} · D18K {rate}")

            def counts():
                return {t: c.query(f"SELECT COUNT(*) n FROM {t} WITH (NOLOCK)")[0]["n"] for t in TABLES}

            def bal(ccy):
                r = c.query("SELECT ISNULL(TillBal,0) b FROM T_TILL_BAL WITH (NOLOCK) WHERE TillID=? AND GoldCcy=? AND ProductCode IS NULL", (till, ccy))
                return M.dec(r[0]["b"]) if r else M.dec(0)

            before = counts()
            bal0 = {"D18K": bal("D18K"), "VND": bal("VND")}
            last0 = c.query("SELECT LastTradingDate FROM I_CUSTOMER WITH (NOLOCK) WHERE CustID=?", (cust,))[0]["LastTradingDate"]

            # ── 1. LẬP (Ins) ──
            row = B.luu_thau(cust_id=cust, emp_id=emp, till_id=till, shop_id=shop, user_id=user, gold_code="D18K",
                             gw=990, dw=10, rate=rate, pct=100, add_money=0, notes="smoke_thau", c=c)
            trn = row["TrnID"]
            tien1 = M.round_vnd(M.dec(990) / 100 * rate * 1000)
            self._ok("Ins → W + BillCode + TotalAmount = 9,9 chỉ × giá", row["Status"] == "W" and bool(row["BillCode"])
                       and M.dec(row["TotalAmount"]) == tien1, f"{trn} {row['BillCode']} {row['TotalAmount']}")
            d1 = self.delta(before, counts())
            self._ok("Ins chỉ +1 TRN_RT_BUYGOLD", d1 == {"TRN_RT_BUYGOLD": 1}, str(d1))
            moc1 = row["TrnDateTime_Upd"]

            # ── 2. SỬA (Upd với mốc) ──
            row = B.luu_thau(trn_id=trn, cust_id=cust, emp_id=emp, till_id=till, shop_id=shop, user_id=user, gold_code="D18K",
                             gw=1000, dw=10, rate=rate, pct=100, add_money=-20000, notes="smoke_thau sửa", c=c)
            tien2 = M.round_vnd(M.dec(1000) / 100 * rate * 1000 - 20000)
            self._ok("Upd đổi TL + bớt → tiền mới, mốc đổi", M.dec(row["TotalAmount"]) == tien2 and row["TrnDateTime_Upd"] != moc1
                       and M.dec(row["AddMoney"]) == -20000, str(row["TotalAmount"]))
            # mốc CŨ cố tình → goi_co_khoa phải báo lỗi chứ không "thành công giả"
            try:
                p = B._tham_so_thau(cust_id=cust, emp_id=emp, till_id=till, shop_id=shop, user_id=user, gold_code="D18K",
                                    gw=1000, dw=10, rate=rate, pct=100, add_money=-20000, tien=tien2, notes="x")
                p.update(p_TrnID=trn, p_UserUpd=user, p_IsGiaoDichNhanh="0", p_CreatedDate=None)
                p.pop("p_TrnID_GDN", None)
                full = c.tham_so_day_du("TRN_RT_BUYGOLD_Upd", p)
                full["p_TrnDateTime_Upd"] = PmvClient.fmt_moc(moc1)
                rc, sets = c.call("TRN_RT_BUYGOLD_Upd", write=True, raise_on_rc=False, **full)
                self._ok("Upd mốc cũ → vendor từ chối (rc≠0)", rc not in (0, None), f"rc={rc}")
            except PmvProcError as exc:
                self._ok("Upd mốc cũ → vendor từ chối", True, str(exc)[:80])

            # ── 3. CHỐT TIỀN MẶT ──
            mid = counts()
            row = B.chot_thau(trn, till_id=till, user_id=user, tien_ck=0, c=c)
            txn = c.query("SELECT TillTxnID, TillID, Status, TrnTotalAmount, GoldCcy, TrnAmount FROM T_TILL_TXN WITH (NOLOCK) WHERE TrnRefID=?", (trn,))
            self._ok("chốt → C, két P đúng TillID", row["Status"] == "C" and txn and txn[0]["Status"] == "P" and txn[0]["TillID"] == till, str(txn))
            det = c.query("SELECT GoldCcy, CrDr, Amount, Amount_Total FROM T_TILL_TXN_DETAIL WITH (NOLOCK) WHERE TillTxnID=?", (txn[0]["TillTxnID"],))
            dmap = {x["GoldCcy"]: x for x in det}
            self._ok("DETAIL: dẻ + 1000 ly (tổng 1010 kể hột), VND − tiền", dmap.get("D18K", {}).get("CrDr") == "+" and M.dec(dmap["D18K"]["Amount"]) == 1000
                       and M.dec(dmap["D18K"]["Amount_Total"]) == 1010 and dmap.get("VND", {}).get("CrDr") == "-" and M.dec(dmap["VND"]["Amount"]) == tien2, str(det))
            self._ok("T_TILL_BAL: D18K +1000 ly · VND −tiền", bal("D18K") - bal0["D18K"] == 1000 and bal0["VND"] - bal("VND") == tien2,
                       f"D18K {bal0['D18K']}→{bal('D18K')} VND {bal0['VND']}→{bal('VND')}")
            d3 = self.delta(mid, counts())
            self._ok("chốt: +1 T_TILL_TXN · +2 DETAIL · +1 lịch sử điểm · 0 CardPay",
                       d3.get("T_TILL_TXN") == 1 and d3.get("T_TILL_TXN_DETAIL") == 2 and d3.get("I_LICHSUTICHLUYDIEM") == 1
                       and not d3.get("TRN_RT_BUYGOLD_CardPay"), str(d3))
            last1 = c.query("SELECT LastTradingDate FROM I_CUSTOMER WITH (NOLOCK) WHERE CustID=?", (cust,))[0]["LastTradingDate"]
            self._ok("I_CUSTOMER.LastTradingDate = hôm nay", last1 and last1.date() == datetime.date.today(), str(last1))
            try:
                B.chot_thau(trn, till_id=till, user_id=user, c=c)
                self._ok("chốt lại phiếu C → bị chặn", False)
            except ValueError as exc:
                self._ok("chốt lại phiếu C → bị chặn", "đã thanh toán" in str(exc).lower(), str(exc))
            try:
                c.call("TRN_RT_BUYGOLD_CompleteMore", write=True, p_TrnIDs=f"{trn}@")
                self._ok("CompleteMore trên phiếu C → BRT-001", False)
            except PmvProcError as exc:
                self._ok("CompleteMore trên phiếu C → BRT-001", "BRT-001" in str(exc.sets), str(exc.sets)[:100])

            # ── 4. HỦY THANH TOÁN → W, két hoàn ──
            B.mo_lai_thau(trn, user_id=user, c=c)
            row = B.phieu_thau(trn, c)
            self._ok("mo_lai → W, T_TILL_TXN mất, két về ban đầu", row["Status"] == "W"
                       and not c.query("SELECT 1 FROM T_TILL_TXN WITH (NOLOCK) WHERE TrnRefID=?", (trn,))
                       and bal("D18K") == bal0["D18K"] and bal("VND") == bal0["VND"], f"{bal('D18K')} {bal('VND')}")
            d4 = self.delta(mid, counts())
            self._ok("hủy TT ghi log: +1 TRN_RT_BUYGOLD_Log · +1 T_TILL_TXN_Log", d4.get("TRN_RT_BUYGOLD_Log") == 1 and d4.get("T_TILL_TXN_Log") == 1, str(d4))

            # ── 5. CHỐT CÓ CHUYỂN KHOẢN 30tr (GĐ chốt 2: CARDPAY_Ins DS thẻ rỗng) ──
            mid = counts()
            ck = M.dec(30_000_000)
            row = B.chot_thau(trn, till_id=till, user_id=user, tien_ck=ck, c=c)
            mat = tien2 - ck
            txn = c.query("SELECT TillTxnID, TrnTotalAmount FROM T_TILL_TXN WITH (NOLOCK) WHERE TrnRefID=?", (trn,))
            det = {x["GoldCcy"]: x for x in c.query("SELECT GoldCcy, CrDr, Amount FROM T_TILL_TXN_DETAIL WITH (NOLOCK) WHERE TillTxnID=?", (txn[0]["TillTxnID"],))}
            self._ok("CK: CardPay=−30tr, CashPay=−tiền mặt", M.dec(row["CardPay"]) == -ck and M.dec(row["CashPay"]) == -mat, f"{row['CardPay']} {row['CashPay']}")
            self._ok("CK: két VND chỉ trừ TIỀN MẶT (TrnTotalAmount + DETAIL)", M.dec(txn[0]["TrnTotalAmount"]) == mat and det["VND"]["CrDr"] == "-"
                       and M.dec(det["VND"]["Amount"]) == mat and bal0["VND"] - bal("VND") == mat, f"{txn} {det.get('VND')} {bal('VND')}")
            d5 = self.delta(mid, counts())
            self._ok("CK: KHÔNG có dòng TRN_RT_BUYGOLD_CardPay", not d5.get("TRN_RT_BUYGOLD_CardPay"), str(d5))
            try:
                B.chot_thau(trn, till_id=till, user_id=user, tien_ck=tien2 + 1, c=c)
                self._ok("CK vượt tổng → chặn", False)
            except ValueError:
                self._ok("CK vượt tổng → chặn", True)

            # ── 6. XÓA phiếu đã C: bill.huy_thau tự mo_lai rồi Del ──
            self._ok("Del phiếu C trần → vendor B-002", self._del_bi_chan(c, trn, user))
            B.huy_thau(trn, user_id=user, c=c)
            self._ok("huy_thau → dòng mất, két về ban đầu", not c.query("SELECT 1 FROM TRN_RT_BUYGOLD WITH (NOLOCK) WHERE TrnID=?", (trn,))
                       and bal("D18K") == bal0["D18K"] and bal("VND") == bal0["VND"])
            trn = ""
            d6 = self.delta(before, counts())
            self._ok("về số dòng ban đầu (trừ *_Log & lịch sử điểm đảo)",
                       all(d6.get(t, 0) == 0 for t in ("TRN_RT_BUYGOLD", "T_TILL_TXN", "T_TILL_TXN_DETAIL", "TRN_RT_BUYGOLD_CardPay")), str(d6))
            c.query("SELECT 1")  # giữ kết nối ấm cho phần view

            # ── 7. VIEW web (test client, đích sandbox) — trang cùng khung màn bán, giỏ session nhiều dòng ──
            web = Client(SERVER_NAME="localhost")
            web.force_login(get_user_model().objects.get(username="admin"))
            V._passcode_dung = lambda req, ma: ma == "SMOKE"
            V.PmvClient = lambda target=None, tag="client": PmvClient("sandbox", tag=tag)
            S.PmvClient = lambda target=None, tag="client": PmvClient("sandbox", tag=tag)   # hoa_don_loc ép "kk" cho hôm nay
            from apps.pos import views_thau as VT
            VT._passcode_dung = lambda req, ma: ma == "SMOKE"
            b = web.get("/banle/thau-vao/").content.decode()
            self._ok("trang thâu: khung pg-ban--thau + 3 khối + chân 4 nút", "pg-ban--thau" in b and 'id="pos-thau"' in b
                     and 'id="pos-tong"' in b and "THANH TOÁN &amp; IN" in b and 'name="kieu"' in b)
            ph = V._phien(web.get("/banle/thau-vao/").wsgi_request)
            if not ph["till_id"]:
                self._ok("admin có két PMV (sys_users)", False, "thiếu till_id → bỏ phần view")
            else:
                web.post("/banle/thau-vao/moi/")
                web.post("/banle/thau-vao/dat/", {"cust_id": cust})
                web.post("/banle/thau-vao/dat/", {"emp": emp})
                r = web.post("/banle/thau-vao/them/", {"gold": "D18K", "tong_tl": "500", "tl_hot": "10", "gia": "", "kieu": "thau"})
                b = r.content.decode()
                self._ok("＋ THÊM dòng 1 (D18K, giá thâu) → OOB có dòng", r.status_code == 200 and "thâu vào" in b and "1 dòng" in b)
                r = web.post("/banle/thau-vao/them/", {"gold": "D24K", "tong_tl": "200", "tl_hot": "0", "gia": "", "kieu": "ban"})
                b = r.content.decode()
                self._ok("＋ THÊM dòng 2 (D24K, giá BÁN RA) → 2 dòng, badge bán ra", "2 dòng" in b and "bán ra" in b)
                r = web.post("/banle/thau-vao/them/", {"gold": "D18K", "tong_tl": "100", "tl_hot": "100", "gia": "", "kieu": "thau"})
                self._ok("hột ≥ tổng → báo lỗi, không thêm", "nhỏ hơn" in r.content.decode())
                web.post("/banle/thau-vao/dat/", {"bot": "20.000"})
                web.post("/banle/thau-vao/dat/", {"pay_method": "bank"})
                r = web.post("/banle/thau-vao/dat/", {"tien_mat": "1.000.000"})
                b = r.content.decode()
                from apps.pos import thau_cart as TC
                g = web.session.get(TC.KEY)
                t = TC.tong_cua(g)
                self._ok("giỏ: 2 dòng, bớt 20k, CK = tiệm trả − 1tr", len(g["lines"]) == 2 and t["bot"] == 20000 and t["tien_mat"] == 1_000_000
                         and t["tien_ck"] == t["khach_tra"] - 1_000_000, str(t))
                r = web.post("/banle/thau-vao/thanh-toan/", {"in": "1"})
                b = r.content.decode()
                from apps.pos.models import ThauNhom
                nhom = ThauNhom.objects.order_by("-pk").first()
                trn_nhom = list(nhom.trn_ids) if nhom else []
                rows = [B.phieu_thau(x, c) for x in trn_nhom]
                self._ok("THANH TOÁN & IN → 2 dòng C cùng nhóm, tờ 110mm 2 dòng trong #pos-in, form trắng",
                         len(rows) == 2 and all(x and x["Status"] == "C" for x in rows) and "th-phieu" in b and "khblInThau" in b
                         and b.count("<tr>") >= 2 and "Chưa có dòng vàng nào" in b, b[:120].replace("\n", " "))
                tong_kk = sum((M.dec(x["TotalAmount"]) for x in rows), M.dec(0))
                ck_kk = sum((-M.dec(x["CardPay"]) for x in rows), M.dec(0))
                add_kk = sum((M.dec(x["AddMoney"]) for x in rows), M.dec(0))
                self._ok("KK: Σ TotalAmount = tiệm trả · Σ CardPay = CK · AddMoney = −bớt", tong_kk == t["khach_tra"] and ck_kk == t["tien_ck"]
                         and add_kk == -20000, f"{tong_kk} {ck_kk} {add_kk} vs {t['khach_tra']} {t['tien_ck']}")
                trn = trn_nhom[0] if trn_nhom else ""
                # mở lại từ DANH SÁCH → cả nhóm lên form (khóa) → 🔓 SỬA (passcode) → về W → thêm dòng → THANH TOÁN lại
                r = web.get("/banle/thau-vao/danh-sach/")
                self._ok("popup DANH SÁCH 80vw có 2 dòng nhóm ⧉ 2", r.status_code == 200 and "⧉ 2" in r.content.decode())
                r = web.post("/banle/thau-vao/mo/", {"trn_id": trn})
                g = web.session.get(TC.KEY)
                self._ok("MỞ → nạp cả nhóm 2 dòng, trạng thái C (khóa)", len(g["lines"]) == 2 and g["status"] == "C" and "🔒" in r.content.decode())
                r = web.post("/banle/thau-vao/thuc-hien/sua/", {"passcode": "SAI"})
                self._ok("SỬA sai passcode → popup báo, phiếu vẫn C", "Passcode không đúng" in r.content.decode()
                         and B.phieu_thau(trn, c)["Status"] == "C")
                r = web.post("/banle/thau-vao/thuc-hien/sua/", {"passcode": "SMOKE"})
                g = web.session.get(TC.KEY)
                self._ok("SỬA đúng passcode → 2 dòng về W, form ĐANG SỬA, két hoàn", all(B.phieu_thau(x, c)["Status"] == "W" for x in trn_nhom)
                         and g["status"] == "W" and g.get("sua_lai") and bal("VND") == bal0["VND"], f"{bal('VND')} vs {bal0['VND']}")
                r = web.post("/banle/thau-vao/them/", {"gold": "D9999", "tong_tl": "100", "tl_hot": "0", "gia": "", "kieu": "thau"})
                self._ok("thêm dòng 3 vào phiếu đang sửa", "3 dòng" in r.content.decode())
                r = web.post("/banle/thau-vao/thanh-toan/", {})
                nhom2 = ThauNhom.objects.order_by("-pk").first()
                rows2 = [B.phieu_thau(x, c) for x in nhom2.trn_ids]
                self._ok("THANH TOÁN lại → 3 dòng C (2 Upd + 1 Ins), nhóm mới, form giữ phiếu khóa", len(rows2) == 3
                         and all(x["Status"] == "C" for x in rows2) and set(trn_nhom) < set(nhom2.trn_ids)
                         and "🔒 ĐÃ CHỐT" in r.content.decode())
                r = web.get(f"/banle/thau-vao/in/?trn_id={nhom2.trn_ids[0]}")
                self._ok("trang in standalone: 3 dòng + bằng chữ", r.status_code == 200 and "Bằng chữ" in r.content.decode()
                         and r.content.decode().count("<tr>") >= 4)
                r = web.post("/banle/thau-vao/thuc-hien/huy_hd/", {"passcode": "SMOKE"})
                self._ok("XÓA PHIẾU (passcode) → 3 dòng mất, két về ban đầu", r.status_code == 200
                         and not any(B.phieu_thau(x, c) for x in nhom2.trn_ids) and bal("D18K") == bal0["D18K"] and bal("VND") == bal0["VND"],
                         f"{bal('D18K')} {bal('VND')}")
                trn = ""
                # phiếu nháp: thêm dòng rồi XÓA nháp (không passcode), không đụng KK
                web.post("/banle/thau-vao/them/", {"gold": "D18K", "tong_tl": "50", "tl_hot": "0", "gia": "", "kieu": "thau"})
                n0 = c.query("SELECT COUNT(*) n FROM TRN_RT_BUYGOLD WITH (NOLOCK)")[0]["n"]
                r = web.post("/banle/thau-vao/thuc-hien/xoa_nhap/", {})
                g = web.session.get(TC.KEY)
                self._ok("XÓA nháp → giỏ trắng, KK không đổi", not g["lines"] and c.query("SELECT COUNT(*) n FROM TRN_RT_BUYGOLD WITH (NOLOCK)")[0]["n"] == n0)
        finally:
            if trn:
                try:
                    from apps.pos import bill as B
                    B.huy_thau(trn, user_id=user, c=c)
                    self.stdout.write(f"  đã dọn {trn} bằng huy_thau")
                except Exception as exc:
                    self.stdout.write(self.style.WARNING(f"  KHÔNG dọn được {trn}: {exc}"))
        self.stdout.write(self.style.SUCCESS(f"SMOKE THAU: {self.ok} PASS / {len(self.fail)} FAIL {self.fail}"))
        if self.fail:
            raise CommandError("FAIL: " + "; ".join(self.fail))

    def _del_bi_chan(self, c, trn, user):
        try:
            c.goi_co_khoa("TRN_RT_BUYGOLD_Del", bang="TRN_RT_BUYGOLD", cot_id="TrnID", gia_tri=trn,
                          p_TrnID=trn, p_UserUpd=user, p_TrnDateTime_Upd_GDN=PmvClient.MOC_TRONG, p_TrnID_GDN="")
            return False
        except PmvProcError as exc:
            return "B-002" in str(exc.sets) or "thanh toán" in str(exc.sets)

    @staticmethod
    def delta(a, b):
        return {t: b[t] - a[t] for t in a if b[t] != a[t]}

    def _ok(self, label, cond, extra=""):
        if cond:
            self.ok += 1
            self.stdout.write(f"  PASS  {label}")
        else:
            self.fail.append(label)
            self.stdout.write(self.style.ERROR(f"  FAIL  {label} {extra}"))
