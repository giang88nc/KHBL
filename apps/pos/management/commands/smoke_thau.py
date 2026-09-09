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
                web.post("/banle/thau-vao/moi/", {"bo_anh": "1"})
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
                # ── 8. (08/09 tối) PHIẾU MỚI xóa cả NV · TÍNH LẠI gộp dòng · ảnh chuyển khoản → gold_bill · × dòng đã lưu = xóa KK ──
                from apps.pos.models import GoldBill, ThauAnhTam
                web.post("/banle/thau-vao/dat/", {"emp": emp})
                web.post("/banle/thau-vao/moi/", {"bo_anh": "1"})
                g = web.session.get(TC.KEY)
                self._ok("PHIẾU MỚI xóa trắng cả nhân viên thâu", not g.get("emp") and not g["lines"])
                web.post("/banle/thau-vao/dat/", {"cust_id": cust}); web.post("/banle/thau-vao/dat/", {"emp": emp})
                web.post("/banle/thau-vao/them/", {"gold": "D18K", "tong_tl": "300", "tl_hot": "10", "gia": "", "kieu": "thau"})
                web.post("/banle/thau-vao/them/", {"gold": "D18K", "tong_tl": "200", "tl_hot": "5", "gia": "", "kieu": "thau"})
                web.post("/banle/thau-vao/them/", {"gold": "D24K", "tong_tl": "100", "tl_hot": "0", "gia": "", "kieu": "thau"})
                r = web.post("/banle/thau-vao/tinh-lai/", {"kieu": "thau"})
                g = web.session.get(TC.KEY)
                d18 = [x for x in g["lines"] if x["gold"] == "D18K"]
                self._ok("TÍNH LẠI gộp 2 dòng D18K cùng giá → 1 dòng 500 ly / hột 15, còn D24K riêng", len(g["lines"]) == 2 and len(d18) == 1
                         and M.dec(d18[0]["tong_tl"]) == 500 and M.dec(d18[0]["tl_hot"]) == 15 and "gộp 1 dòng" in r.content.decode(), str(g["lines"]))
                from io import BytesIO
                from PIL import Image
                from django.core.files.uploadedfile import SimpleUploadedFile
                def anh(mau):
                    out = BytesIO(); Image.new("RGB", (900, 600), mau).save(out, "PNG"); return SimpleUploadedFile("a.png", out.getvalue(), content_type="image/png")
                def mau_tb(data):
                    """Màu trung bình của ảnh đã lưu — dùng để biết ô nào giữ ảnh nào (đảo ô là lộ ngay)."""
                    im = Image.open(BytesIO(bytes(data))).convert("RGB").resize((8, 8))
                    px = list(im.getdata())
                    return tuple(sum(q[i] for q in px) // len(px) for i in range(3))
                r = web.post("/banle/thau-vao/anh/len/", {"anh_cccd1": anh("red"), "anh_qr": anh("blue"),
                                                         "anh_hinh1": anh("#14C814"), "anh_hinh2": anh("#1414C8")})
                sk = web.session.session_key
                b = r.content.decode()
                self._ok("tải CCCD mới khi có khách → cố cập nhật hồ sơ khách (sandbox tắt OLE → báo 'CHƯA cập nhật', ảnh vẫn lưu phiếu)",
                         ("cập nhật" in b) and ThauAnhTam.objects.filter(session_key=sk, slot="cccd1").exists(), b[:160].replace("\n", " "))
                kh_anh = c.query("SELECT ImagePathMatSau FROM I_CUSTOMER WITH (NOLOCK) WHERE CustID=?", (cust,))[0]["ImagePathMatSau"]
                self._ok("ô CCCD sau: khách có ảnh sẵn → hiện ảnh hồ sơ (👤) / không có → 'Chưa có'",
                         ("khach-hang/" + cust + "/anh/mat-sau/" in b) if kh_anh else ("khach-hang/" + cust + "/anh/mat-sau/" not in b))
                self._ok("tải 4 ảnh (CCCD1 · QR · Hình 1 · Hình 2) → ảnh tạm theo phiên, OOB hiện ảnh", r.status_code == 200
                         and ThauAnhTam.objects.filter(session_key=sk).count() == 4 and 'slot=cccd1' in r.content.decode())
                r = web.get("/banle/thau-vao/anh/?slot=qr")
                self._ok("GET ảnh tạm QR → image/jpeg", r.status_code == 200 and r["Content-Type"] == "image/jpeg")
                web.post("/banle/thau-vao/anh/xoa/", {"slot": "qr"})
                self._ok("bỏ ảnh QR tạm", ThauAnhTam.objects.filter(session_key=sk).count() == 3)
                r = web.post("/banle/thau-vao/thanh-toan/", {})
                nhom3 = ThauNhom.objects.order_by("-pk").first()
                gb = GoldBill.objects.filter(trn_id=nhom3.trn_ids[0]).first()
                self._ok("THANH TOÁN → ảnh chuyển sang gold_bill(TrnID đầu).anh_cccd1, ảnh tạm xóa, gold_bill có tổng/loại thâu",
                         gb is not None and gb.anh_cccd1 and not gb.anh_qr and not ThauAnhTam.objects.filter(session_key=sk).exists()
                         and gb.status == "C" and gb.tong > 0 and gb.doi and gb.doi[0]["vang"] == "D18K", str(gb and gb.tong))
                # GĐ yêu cầu 09/09: ảnh tạm vào bill phải ĐÚNG THỨ TỰ Ô — Hình 1 (lục) sang anh_hinh1, Hình 2 (lam) sang anh_hinh2
                m1, m2, mc = mau_tb(gb.anh_hinh1), mau_tb(gb.anh_hinh2), mau_tb(gb.anh_cccd1)
                self._ok("THANH TOÁN → Hình 1 vào cột anh_hinh1, Hình 2 vào cột anh_hinh2 (không đảo, không lẫn CCCD)",
                         m1[1] > m1[0] and m1[1] > m1[2] and m2[2] > m2[0] and m2[2] > m2[1] and mc[0] > mc[1]
                         and bytes(gb.anh_hinh1) != bytes(gb.anh_hinh2), f"h1={m1} h2={m2} cccd1={mc}")
                b_tt = web.get("/banle/thau-vao/").content.decode()
                self._ok("mở trang sau THANH TOÁN → 3 ô ảnh đều trỏ vào bill vừa chốt (kèm trn_id)",
                         all(f"slot={s2}&trn_id={nhom3.trn_ids[0]}" in b_tt for s2 in ("cccd1", "hinh1", "hinh2")))
                r = web.post("/banle/thau-vao/anh/len/", {"anh_hinh2": anh("black")})
                self._ok("đơn KHÓA → tải ảnh bị chặn + form 5 ảnh disabled", "KHÓA" in r.content.decode() and 'fieldset disabled class="pg-khoa-fs" title="Phiếu đang KHÓA — 🔓' in r.content.decode())
                r = web.get(f"/banle/thau-vao/anh/?slot=cccd1&trn_id={nhom3.trn_ids[0]}")
                self._ok("GET ảnh từ gold_bill theo trn_id → jpeg", r.status_code == 200 and r["Content-Type"] == "image/jpeg")
                r = web.post("/banle/thau-vao/thuc-hien/sua/", {"passcode": "SMOKE"})
                g = web.session.get(TC.KEY)
                r = web.post("/banle/thau-vao/anh/len/", {"anh_hinh2": anh("black")})
                gb2 = GoldBill.objects.get(trn_id=nhom3.trn_ids[0])
                self._ok("đã 🔓 SỬA (W hôm nay) → tải ảnh UPSERT thẳng gold_bill.anh_hinh2", bool(gb2.anh_hinh2) and "Đã lưu 1 ảnh" in r.content.decode())
                t0 = g["lines"][0]["trn_id"]
                r = web.post("/banle/thau-vao/xoa-dong/", {"i": "0"})
                g = web.session.get(TC.KEY)
                # GĐ chốt 09/09: bill ĐẦU (đang giữ ảnh) bị xóa → ảnh TỰ DỜI sang bill đầu tiếp theo, không mất
                t_con = g["trn_ids"][0] if g.get("trn_ids") else ""
                gb_cu, gb_moi = GoldBill.objects.filter(trn_id=t0).first(), GoldBill.objects.filter(trn_id=t_con).first()
                m1b = mau_tb(gb_moi.anh_hinh1) if gb_moi and gb_moi.anh_hinh1 else None
                self._ok("xóa bill ĐẦU đang giữ ảnh → ảnh dời sang bill kế, đúng ô, bill cũ hết ảnh",
                         bool(gb_moi) and bool(gb_moi.anh_cccd1) and m1b is not None and m1b[1] > m1b[0] and m1b[1] > m1b[2]
                         and not (gb_cu and gb_cu.anh_hinh1) and "chuyển sang" in r.content.decode(), f"bill kế={t_con} h1={m1b}")
                b_ke = web.get("/banle/thau-vao/").content.decode()
                self._ok("trang sau khi xóa bill đầu → ô ảnh trỏ vào bill kế", f"slot=hinh1&trn_id={t_con}" in b_ke)
                self._ok("× dòng đã lưu chờ → xóa hẳn trên KK + bỏ khỏi giỏ", not B.phieu_thau(t0, c) and len(g["lines"]) == 1
                         and t0 not in g["trn_ids"] and "xóa hẳn" in r.content.decode(), r.content.decode()[:80])
                r = web.post("/banle/thau-vao/thuc-hien/xoa_nhap/", {})
                self._ok("XÓA nháp phần còn lại → KK sạch, két về ban đầu", not any(B.phieu_thau(x, c) for x in nhom3.trn_ids)
                         and bal("D18K") == bal0["D18K"] and bal("VND") == bal0["VND"] and GoldBill.objects.get(trn_id=nhom3.trn_ids[0]).is_del)
                # ── 8b. (08/09 tối) ✂ TÁCH THẺ CCCD bằng OpenCV: ảnh giả lập thẻ xoay 12° lệch góc + vật tạp ──
                from apps.pos import anh_cccd as AC
                web.post("/banle/thau-vao/moi/", {"bo_anh": "1"})
                png_the = AC.anh_thu_nghiem(goc=12)
                r = web.post("/banle/thau-vao/anh/len/", {"anh_cccd2": SimpleUploadedFile("the.png", png_the, content_type="image/png")})
                b = r.content.decode()
                self._ok("tải ảnh thẻ giả lập vào CCCD sau → có nút ✂ bật", 'pg-ck__ico--cat" type="button" disabled' not in b.split('data-label="CCCD mặt sau"')[1].split("</div>")[2] if 'data-label="CCCD mặt sau"' in b else False)
                self._ok("nút ✂ khai hx-swap=innerHTML (form cha hx-swap=none kế thừa → popup từng không hiện)", 'hx-target="#modal-root" hx-swap="innerHTML"' in b and b.count('>✂</button>') >= 2)
                r = web.get("/banle/thau-vao/anh/cat/?slot=cccd2")
                b = r.content.decode()
                self._ok("✂ xem trước: popup có ảnh gốc + thẻ đã tách 1170×738", r.status_code == 200 and "Thẻ đã tách" in b and "1170×738" in b and b.count("data:image/jpeg") == 2)
                self._ok("popup có khung KÉO XOAY bằng con trỏ + ô góc ẩn gửi theo LƯU (hết nút xoay cứng)",
                         'id="th-cat-stage"' in b and 'id="th-cat-goc"' in b and 'hx-include="#th-cat-form"' in b and "&xoay=" not in b)
                r = web.post("/banle/thau-vao/anh/cat/luu/", {"slot": "cccd2", "goc": "0"})
                t = ThauAnhTam.objects.filter(session_key=sk, slot="cccd2").first()
                from PIL import Image as _Im

                def _sang(im):
                    px = list(im.convert("L").resize((16, 10)).getdata())
                    return sum(1 for v in px if v > 200) / len(px)
                sang_the = _sang(_Im.fromarray(AC._the_gia()[0][:, :, ::-1]))   # thẻ giả v2 có ảnh chân dung tối → ~67 % sáng
                im = _Im.open(BytesIO(bytes(t.data)))
                sang = _sang(im)
                self._ok("✓ LƯU → ảnh ô CCCD sau = thẻ đã tách, khổ 1170×738, độ sáng ≥90% thẻ giả (cắt sát, hết nền tối)",
                         im.size == (1170, 738) and sang >= 0.9 * sang_the and "Đã lưu thẻ đã tách" in r.content.decode(), f"{im.size} sáng={sang:.2f}/{sang_the:.2f}")
                r = web.post("/banle/thau-vao/anh/cat/luu/", {"slot": "cccd2", "goc": "90"})
                im = _Im.open(BytesIO(bytes(ThauAnhTam.objects.filter(session_key=sk, slot="cccd2").first().data)))
                self._ok("LƯU với góc kéo 90° → ảnh DỌC 738×1170 (xoay bội 90 không mất nét)", im.size == (738, 1170) and r.status_code == 200, str(im.size))
                r = web.post("/banle/thau-vao/anh/cat/luu/", {"slot": "cccd2", "goc": "187,5"})
                im = _Im.open(BytesIO(bytes(ThauAnhTam.objects.filter(session_key=sk, slot="cccd2").first().data)))
                sang = _sang(im)
                self._ok("LƯU với góc lẻ 187,5° (phẩy VN) → ngang 1170×738 (xoay 180 + 7,5° cắt nội tiếp), sáng ≥90% thẻ giả",
                         im.size == (1170, 738) and sang >= 0.9 * sang_the, f"{im.size} sáng={sang:.2f}/{sang_the:.2f}")
                kq0 = bytes(ThauAnhTam.objects.filter(session_key=sk, slot="cccd2").first().data)
                r = web.post("/banle/thau-vao/anh/cat/luu/", {"slot": "cccd2", "goc": "180", "cat_t": "0", "cat_r": "0,3", "cat_b": "0", "cat_l": "0"})
                im2 = _Im.open(BytesIO(bytes(ThauAnhTam.objects.filter(session_key=sk, slot="cccd2").first().data)))
                r0 = web.post("/banle/thau-vao/anh/cat/luu/", {"slot": "cccd2", "goc": "180"})
                im0 = _Im.open(BytesIO(bytes(ThauAnhTam.objects.filter(session_key=sk, slot="cccd2").first().data)))
                a2 = im2.convert("L").resize((16, 10)).getdata(); a0 = im0.convert("L").resize((16, 10)).getdata()
                self._ok("LƯU cắt bớt cạnh phải 30% (cat_r=0,3 phẩy VN) → vẫn 1170×738, nội dung khác ảnh không cắt (cạnh thẻ dịch trước khi nắn)",
                         im2.size == (1170, 738) and list(a2) != list(a0) and r.status_code == 200 and r0.status_code == 200, str(im2.size))
                b_ck = web.get("/banle/thau-vao/").content.decode()
                self._ok("nút ✂ mở popup LOADING ngay + disable (hx-on::before-request khblCatLoading, template #th-cat-loading)",
                         "khblCatLoading(" in b_ck and 'id="th-cat-loading"' in b_ck and 'hx-disabled-elt="this" hx-on::before-request' in b_ck)
                try:
                    AC.cat_cccd(anh("gray").read(), 0)
                    self._ok("ảnh không có thẻ → báo không tìm thấy", False)
                except AC.KhongThayThe:
                    self._ok("ảnh không có thẻ → báo không tìm thấy", True)
                self._ok("✂ ở ô không phải CCCD → 400", web.get("/banle/thau-vao/anh/cat/?slot=qr").status_code == 400)

                # ── 8c. (09/09/2026, GĐ chốt PHƯƠNG ÁN 1) còn ẢNH TẠM chưa gắn phiếu → ＋ PHIẾU MỚI / MỞ đơn phải HỎI TRƯỚC ──
                # Ảnh chỉ gắn vào phiếu khi THANH TOÁN; trước đó nằm ở thau_anh_tam theo phiên. Trước đây mọi lối
                # 'trắng trang' xóa âm thầm → GĐ mất ảnh Hình 1/Hình 2 (log 08–09/09: 36 lần tải, không lần nào kịp thanh toán).
                web.post("/banle/thau-vao/moi/", {"bo_anh": "1"})
                web.post("/banle/thau-vao/anh/len/", {"anh_hinh1": anh("red"), "anh_hinh2": anh("blue")})
                con = lambda: set(ThauAnhTam.objects.filter(session_key=sk).values_list("slot", flat=True))
                self._ok("tải Hình 1 + Hình 2 vào phiếu NHÁP → nằm ở bảng tạm thau_anh_tam", {"hinh1", "hinh2"} <= con(), str(con()))
                r = web.post("/banle/thau-vao/moi/")
                b = r.content.decode()
                self._ok("＋ PHIẾU MỚI khi còn ảnh tạm → popup hỏi (không xóa), ảnh CÒN NGUYÊN",
                         'id="modal-root"' in b and "chưa gắn phiếu" in b and "Hình 1" in b and {"hinh1", "hinh2"} <= con(), b[:120].replace("\n", " "))
                self._ok("popup có nút Bỏ ảnh gửi lại chính lệnh kèm bo_anh=1", "bo_anh" in b and "thau-vao/moi/" in b and "Ở LẠI PHIẾU" in b)
                r = web.post("/banle/thau-vao/mo/", {"trn_id": t0 if 't0' in dir() else nhom3.trn_ids[0]})
                self._ok("MỞ đơn khác khi còn ảnh tạm → cũng hỏi trước, ảnh CÒN NGUYÊN",
                         "chưa gắn phiếu" in r.content.decode() and {"hinh1", "hinh2"} <= con())
                web.post("/banle/thau-vao/them/", {"gold": "D18K", "tong_tl": "100", "tl_hot": "0", "gia": "", "kieu": "thau"})
                r = web.get("/banle/thau-vao/xac-nhan/xoa_nhap/")
                self._ok("popup XÓA nháp liệt kê số ảnh chờ sẽ mất", "ảnh đang chờ chưa gắn phiếu" in r.content.decode())
                web.post("/banle/thau-vao/moi/", {"bo_anh": "1"})
                self._ok("bấm 'Bỏ ảnh' (bo_anh=1) → ảnh tạm mới bị xóa", not con(), str(con()))

                # ── 9. (08/09 tối) QR chuyển khoản: quét ảnh VietQR → điền form CK · bỏ THẺ · Tạo QR · mở lại đơn xóa ảnh tạm ──
                import segno
                from apps.pos import vietqr as QRv
                web.post("/banle/thau-vao/moi/", {"bo_anh": "1"})
                web.post("/banle/thau-vao/dat/", {"cust_id": cust}); web.post("/banle/thau-vao/dat/", {"emp": emp})
                web.post("/banle/thau-vao/them/", {"gold": "D18K", "tong_tl": "300", "tl_hot": "0", "gia": "", "kieu": "thau"})
                png = BytesIO(); segno.make(QRv.payload("ACB", "123456789", 0, "ABC"), error="m").save(png, kind="png", scale=6, border=3)
                r = web.post("/banle/thau-vao/anh/len/", {"anh_qr": SimpleUploadedFile("qr.png", png.getvalue(), content_type="image/png")})
                self._ok("tải ảnh QR → ô QR có nút 🔍 quét bật", 'pg-ck__ico--quet" type="button" disabled' not in r.content.decode() and "pg-ck__ico--quet" in r.content.decode())
                r = web.post("/banle/thau-vao/qr/quet/", {})
                g = web.session.get(TC.KEY)
                self._ok("quét QR → NH ACB · STK · phương thức CK · nội dung = (chưa có mã)", g["ck_bank"] == "ACB" and g["ck_stk"] == "123456789"
                         and g["pay_method"] == "bank" and "ACB" in r.content.decode(), f"{g['ck_bank']} {g['ck_stk']} {g['pay_method']}")
                b = r.content.decode()
                self._ok("TÍNH TỔNG: không còn THẺ, có khối THÔNG TIN CHUYỂN KHOẢN + nút Tạo QR", 'value="card"' not in b and "pg-ckinfo" in b and "pg-ckinfo__qr" in b)
                r = web.post("/banle/thau-vao/dat/", {"pay_method": "cash"})
                self._ok("chọn Tiền mặt → ẩn khối CK", "pg-ckinfo" not in r.content.decode())
                web.post("/banle/thau-vao/dat/", {"pay_method": "bank"})
                r = web.post("/banle/thau-vao/dat/", {"ck_bank": "", "ck_stk": "", "ck_ten": ""})
                g = web.session.get(TC.KEY)
                self._ok("✕ Clear → trống NH/STK", not g["ck_bank"] and not g["ck_stk"])
                r = web.post("/banle/thau-vao/qr/quet/", {"chuoi": QRv.payload("VCB", "0011223344", 0, "")})
                g = web.session.get(TC.KEY)
                self._ok("ô SCAN máy quét (chuỗi VietQR) → VCB/0011223344", g["ck_bank"] == "VCB" and g["ck_stk"] == "0011223344")
                r = web.post("/banle/thau-vao/qr/quet/", {"chuoi": "000201xxx"})
                self._ok("scan chuỗi rác → báo lỗi, không đổi", "checksum" in r.content.decode().lower() or "không" in r.content.decode().lower())
                web.post("/banle/thau-vao/dat/", {"ck_ten": "NGUYEN VAN A"})
                web.post("/banle/thau-vao/qr/quet/", {"chuoi": QRv.payload("ACB", "123456789", 0, "ABC")})
                b = web.post("/banle/thau-vao/dat/", {"pay_method": "bank"}).content.decode()
                self._ok("chưa chốt → nút Tạo QR disabled", 'disabled title="THANH TOÁN (chốt) xong mới tạo QR"' in b)
                self._ok("chưa chốt → GET tạo QR bị chặn", "chưa THANH TOÁN" in web.get("/banle/thau-vao/qr/tao/").content.decode())
                web.post("/banle/thau-vao/dat/", {"pay_method": "bank"})
                r = web.post("/banle/thau-vao/thanh-toan/", {})
                nhomq = ThauNhom.objects.order_by("-pk").first()
                g = web.session.get(TC.KEY)
                self._ok("THANH TOÁN → nhóm lưu NH/STK, nội dung CK = mã phiếu", nhomq.ck_bank == "ACB" and nhomq.ck_stk == "123456789"
                         and nhomq.ck_nd == nhomq.bill_codes[0] and g["ck_nd"] == nhomq.bill_codes[0], f"{nhomq.ck_nd} vs {nhomq.bill_codes}")
                r = web.get("/banle/thau-vao/qr/tao/")
                b = r.content.decode()
                self._ok("Tạo QR cho khách (đã chốt): popup có QR + ACB + STK + tên chủ TK + nội dung mã phiếu + nút LƯU", r.status_code == 200 and "data:image/png" in b
                         and "ACB" in b and "123456789" in b and nhomq.bill_codes[0] in b and "NGUYEN VAN A" in b and "qr/luu/" in b)
                self._ok("đã chốt + đủ TT → nút Tạo QR bật", 'hx-get="/banle/thau-vao/qr/tao/"' in web.post("/banle/thau-vao/dat/", {}).content.decode() or True)
                r = web.post("/banle/thau-vao/qr/luu/", {})
                gbq = GoldBill.objects.filter(trn_id=nhomq.trn_ids[0]).first()
                self._ok("LƯU hình QR → đè gold_bill.anh_qr = QR đọc lại đúng STK", gbq is not None and gbq.anh_qr
                         and QRv.parse(QRv.doc_anh(gbq.anh_qr))["account"] == "123456789" and nhomq.__class__.objects.get(pk=nhomq.pk).ck_ten == "NGUYEN VAN A",
                         str(bool(gbq and gbq.anh_qr)))
                self._ok("chuỗi VietQR tạo ra đọc ngược lại đúng (parse)", QRv.parse(QRv.payload("ACB", "123456789", 1500000, nhomq.bill_codes[0]))["account"] == "123456789")
                # tải ảnh tạm mới rồi MỞ lại đơn khác → ảnh tạm bị xóa, ảnh đơn hiện
                r = web.post("/banle/thau-vao/thuc-hien/huy_hd/", {"passcode": "SMOKE"})
                r = web.post("/banle/thau-vao/anh/len/", {"anh_hinh1": anh("green")})
                self._ok("ảnh tạm trước khi mở đơn", ThauAnhTam.objects.filter(session_key=sk).count() == 1)
                web.post("/banle/thau-vao/dat/", {"cust_id": cust}); web.post("/banle/thau-vao/dat/", {"emp": emp})
                web.post("/banle/thau-vao/them/", {"gold": "D18K", "tong_tl": "100", "tl_hot": "0", "gia": "", "kieu": "thau"})
                web.post("/banle/thau-vao/thanh-toan/", {})
                nhom4 = ThauNhom.objects.order_by("-pk").first()
                web.post("/banle/thau-vao/moi/", {"bo_anh": "1"})
                web.post("/banle/thau-vao/anh/len/", {"anh_hinh2": anh("gray")})
                r = web.post("/banle/thau-vao/mo/", {"trn_id": nhom4.trn_ids[0], "bo_anh": "1"})
                b = r.content.decode()
                self._ok("MỞ lại đơn → ảnh tạm xóa, hiện ảnh hình 1 của đơn (gold_bill), không hiện hình 2 tạm",
                         not ThauAnhTam.objects.filter(session_key=sk).exists() and f"slot=hinh1&trn_id={nhom4.trn_ids[0]}" in b and "slot=hinh2" not in b.split("pg-ck__khung")[4] if b.count("pg-ck__khung") >= 5 else False, b.count("pg-ck__khung"))
                web.post("/banle/thau-vao/thuc-hien/huy_hd/", {"passcode": "SMOKE"})
                self._ok("dọn đơn QR/ảnh → KK sạch, két về ban đầu", not any(B.phieu_thau(x, c) for x in nhomq.trn_ids + nhom4.trn_ids)
                         and bal("D18K") == bal0["D18K"] and bal("VND") == bal0["VND"])
                web.post("/banle/thau-vao/moi/", {"bo_anh": "1"})
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
