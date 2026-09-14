# -*- coding: utf-8 -*-
"""Bộ kiểm XÓA PHIẾU ĐẶT-CỌC BẰNG PASSCODE (GĐ chốt 11/09/2026). CHỈ chạy trên bản thử:

    manage.py smoke_xoa_coc

Dựng phiếu thật trên sandbox rồi đi đủ đường: nút XÓA cuối dòng · popup đòi Passcode khi phiếu đã ghi
tiền / đã có lịch sử · sai mã thì KHÔNG xóa · đúng mã thì xóa hẳn trên PMV, trả hàng đang giữ, giữ lại
nhật ký · phiếu đã áp vào hóa đơn bán thì chặn · phiếu đã thu tiền vào két thì bắt hoàn thu trước.
"""
from decimal import Decimal
from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.test import Client

from apps.pmv import gateway
from apps.pmv.client import PmvClient
from apps.pmv.models import PmvWebUser, PmvUser, UserModuleAccess
from apps.pos import deposits as D
from apps.pos import passcode as PC
from apps.pos.deposit_models import DepositEvent, DepositMoneyOperation, DepositOrderState, DepositStockHold

MA = "9182"


class Command(BaseCommand):
    help = "Smoke xóa phiếu đặt-cọc bằng Passcode (sandbox, tự dọn)"

    def handle(self, *args, **o):
        self.loi = []
        c = PmvClient("sandbox", tag="smoke-xoa-coc")
        assert c.target == "sandbox", "Bộ kiểm này CHỈ chạy trên bản thử"
        u = c.query("SELECT UserID,EmpID,ShopID FROM SYS_USERS WITH (NOLOCK) WHERE UserName='admin'")[0]
        till = c.query("SELECT TillID FROM T_TILL WITH (NOLOCK) WHERE TillCode='ad'")[0]["TillID"]
        pmv = SimpleNamespace(user_id=u["UserID"], emp_id=u["EmpID"], shop_id=u["ShopID"], till_id=till)
        cust = c.query("SELECT TOP 1 CustID FROM I_CUSTOMER WITH (NOLOCK) ORDER BY CustID")[0]["CustID"]
        gold = c.query("SELECT TOP 1 GoldCode FROM I_GOLD WITH (NOLOCK) ORDER BY GoldCode")[0]["GoldCode"]
        web, nguoi, pass_cu = self.dang_nhap()
        goc = gateway.PROC_WRITE_ALLOW
        # Trang web chọn đích theo CÔNG TẮC dùng chung; ở đây chỉ đổi TRONG TIẾN TRÌNH bộ kiểm này
        # (không đụng PmvState) để các request thử đọc/ghi đúng bản thử, máy chủ thật không ảnh hưởng.
        goc_dich = gateway.dich_hien_tai
        gateway.dich_hien_tai = lambda: "sandbox"
        cocs = []
        try:
            line = {"ProductDesc": "TEST xóa phiếu", "GoldCode": gold, "SL": 1, "TotalWeight": Decimal(0),
                    "DiamondWeight": Decimal(0), "GoldWeight": Decimal(0), "TaskPrice": Decimal(0),
                    "Size": "", "Notes": "SP: TEST-XOA|hàng thử"}
            data = {"CustID": cust, "EmpID": pmv.emp_id, "TienCoc": Decimal("400000"),
                    "Description": "KHBL TEST xóa phiếu - tự dọn"}
            pk = D.save(c, data, [line], pmv)
            cocs.append(pk)
            DepositStockHold.objects.create(target=c.target, trn_id=pk, product_code="TEST-XOA",
                                            active_key=c.target + ":test-xoa", location="Tủ kiểm",
                                            username=nguoi.username)
            DepositEvent.objects.create(target=c.target, trn_id=pk, action="contact", username=nguoi.username,
                                        note="lịch sử giả để chặn xóa kiểu cũ", token="smoke-xoa-" + pk, data={})

            b = web.get(f"/banle/dat-coc/{pk}/xoa/").content.decode()
            self.ok("bấm XÓA ở danh sách → mở THẲNG popup xác nhận + Passcode (không qua popup phiếu)",
                    "XÁC NHẬN XÓA" in b and 'name="passcode"' in b and 'id="xn-form"' in b, b[:140])
            self.ok("popup nêu đủ phiếu · khách · tiền cọc · người thao tác · hậu quả",
                    all(x in b for x in ("Phiếu", "Khách", "Tiền cọc", "Người thao tác",
                                         "Xóa hẳn phiếu khỏi PMV", nguoi.username))
                    and "đã có lịch sử vận hành" in b, b[:140])

            r = web.post(f"/banle/dat-coc/{pk}/xoa/", {"passcode": "0000"})
            self.ok("Passcode SAI → phiếu còn nguyên trên PMV, popup báo lỗi",
                    D.header(c, pk) is not None and "Passcode không đúng" in r.content.decode(),
                    r.content.decode()[:120])

            r = web.post(f"/banle/dat-coc/{pk}/xoa/", {"passcode": MA})
            self.ok("Passcode ĐÚNG → PMV xóa hẳn phiếu", D.header(c, pk) is None, r.content.decode()[:150])
            self.ok("hàng đang giữ của phiếu được trả về kho",
                    not DepositStockHold.objects.filter(target=c.target, trn_id=pk,
                                                        active_key__isnull=False).exists())
            self.ok("tiến độ vận hành bị dọn, NHẬT KÝ giữ lại + có dòng 'delete' ghi mốc xóa",
                    not DepositOrderState.objects.filter(target=c.target, trn_id=pk).exists()
                    and DepositEvent.objects.filter(target=c.target, trn_id=pk, action="delete").exists()
                    and DepositEvent.objects.filter(target=c.target, trn_id=pk, action="contact").exists())

            # phiếu ĐÃ ÁP vào hóa đơn bán: chặn, không cho xóa kể cả có Passcode
            pk2 = D.save(c, data, [line], pmv)
            cocs.append(pk2)
            DepositMoneyOperation.objects.create(
                target=c.target, trn_id=pk2, kind="apply", amount=Decimal("400000"), cash=Decimal(0),
                bank=Decimal(0), invoice_id="TEST-HD", status="done", token="smoke-apply-" + pk2,
                user_id=pmv.user_id, till_id="", username=nguoi.username, evidence={})
            b2 = web.get(f"/banle/dat-coc/{pk2}/xoa/").content.decode()
            self.ok("phiếu đã dùng cho hóa đơn bán → KHÔNG cho xóa, dù có Passcode",
                    "đã được dùng cho hóa đơn bán" in b2 and 'name="passcode"' not in b2, b2[:140])
            r2 = web.post(f"/banle/dat-coc/{pk2}/xoa/", {"passcode": MA})
            self.ok("gửi thẳng lệnh xóa kèm Passcode đúng vẫn bị chặn ở server",
                    D.header(c, pk2) is not None and "đã được dùng cho hóa đơn bán" in r2.content.decode())
            DepositMoneyOperation.objects.filter(target=c.target, trn_id=pk2).delete()

            # phiếu đã THU TIỀN vào két (Status P): vendor không xóa được → báo đường xử lý đúng
            gateway.PROC_WRITE_ALLOW = goc | {"TRN_DATCOC_Complete"}
            c.call("TRN_DATCOC_Complete", write=True, p_TrnID=pk2, p_UserID=pmv.user_id)
            # đi trọn đường thu tiền (phân bổ + chốt két) — để trạng thái nửa vời thì lúc dọn
            # T_TILL_TXN_Del không có TillID mà hoàn, phiếu thử sẽ kẹt lại trên bản thử
            txn = c.query("SELECT TillTxnID FROM T_TILL_TXN WITH (NOLOCK) WHERE TrnRefID=?", (pk2,))[0]["TillTxnID"]
            c.call("CARDPAY_Ins", write=True, day_du=True, p_TillID=pmv.till_id, p_TillTxnID=txn, p_TrnID=pk2,
                   p_TypeTrade="TDC", p_ProductIDs=None, p_CardAmounts=None, p_Amount="0",
                   p_AmountTra="400000", p_List="<NewDataSet/>")
            c.call("T_TILL_TXN_Proc", write=True, p_TrnIDs=pk2, p_TillID=pmv.till_id, p_UserID=pmv.user_id)
            b3 = web.get(f"/banle/dat-coc/{pk2}/xoa/").content.decode()
            self.ok("phiếu đã thu tiền vào két → chỉ đường Hoàn/Hủy thu, không xóa liều",
                    "Hoàn/Hủy thu" in b3, b3[:140])
        finally:
            gateway.PROC_WRITE_ALLOW = goc
            self.don(c, cocs, pmv, nguoi, pass_cu)
            gateway.dich_hien_tai = goc_dich
        if self.loi:
            for x in self.loi:
                self.stderr.write(self.style.ERROR("FAIL " + x))
            raise SystemExit(1)
        self.stdout.write(self.style.SUCCESS("SMOKE XÓA PHIẾU CỌC: PASS toàn bộ (đã dọn dữ liệu thử)"))

    # ── tiện ích ──────────────────────────────────────────────────────────────
    def ok(self, ten, dieu_kien, chi_tiet=""):
        if dieu_kien:
            self.stdout.write(f"  ok   {ten}")
        else:
            self.loi.append(f"{ten} {chi_tiet}".strip())
            self.stdout.write(self.style.ERROR(f"  FAIL {ten} {chi_tiet}"))

    def dang_nhap(self):
        """Tài khoản admin + Passcode tạm; trả (client, user, hash passcode cũ để hoàn nguyên)."""
        nguoi = get_user_model().objects.filter(is_superuser=True).order_by("id").first()
        assert nguoi, "Chưa có tài khoản quản trị để chạy bộ kiểm"
        cu = PC.lay_hash(nguoi)
        PC.dat(nguoi, MA)
        if not PmvWebUser.objects.filter(web_user=nguoi).exists():
            pu = PmvUser.objects.order_by("id").first()
            if pu:
                PmvWebUser.objects.create(web_user=nguoi, pmv_user=pu)
        web = Client(SERVER_NAME="localhost")          # ALLOWED_HOSTS không có "testserver"
        web.force_login(nguoi)
        return web, nguoi, cu

    @staticmethod
    def lay_token(html):
        import re
        m = re.search(r'name="token" value="([^"]+)"', html)
        return m.group(1) if m else ""

    def don(self, c, cocs, pmv, nguoi, pass_cu):
        for pk in cocs:
            try:
                h = D.header(c, pk)
                if h and h["Status"] == "P":
                    c.call("T_TILL_TXN_Del", write=True, day_du=True, p_TrnRefID=pk, pType="TDC",
                           pCongNoBanLe=0, p_UserUpd=pmv.user_id,
                           p_TrnDateTime_Upd=h["TrnDateTime_Upd"], p_Type="0")
                    h = D.header(c, pk)
                if h:
                    c.call("TRN_DATCOC_Del", write=True, p_TrnID=pk, p_UserUpd=pmv.user_id,
                           p_TrnDateTime_Upd=h["TrnDateTime_Upd"])
            except Exception as exc:
                self.stdout.write(f"  (dọn) phiếu {pk}: {exc}")
        for model in (DepositEvent, DepositMoneyOperation, DepositOrderState, DepositStockHold):
            model.objects.filter(target=c.target, trn_id__in=cocs).delete()
        PC.gan_hash(nguoi, pass_cu)                   # trả lại đúng mã băm cũ (rỗng = chưa đặt)
        self.stdout.write("  (dọn) xong dữ liệu thử, passcode đã hoàn nguyên")
