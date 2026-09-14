# -*- coding: utf-8 -*-
"""Bộ kiểm ÁP PHIẾU ĐẶT-CỌC VÀO ĐƠN BÁN (GĐ chốt 11/09/2026). CHỈ chạy trên bản thử:

    manage.py smoke_ban_coc

Dựng thật trên sandbox: khách → 2 phiếu cọc → thu tiền cọc vào két → quét danh sách chờ → gắn CẢ HAI
vào một hóa đơn bán → chốt → kiểm phiếu đã xong, hàng giữ được giải phóng, phiếu bị khóa và không còn
hiện ở lần bán sau. Cuối bài dọn sạch: gỡ liên kết, hủy hóa đơn, hoàn két, xóa phiếu cọc.
"""
from decimal import Decimal
from types import SimpleNamespace

from django.core.management.base import BaseCommand

from apps.pmv import gateway
from apps.pmv.client import PmvClient
from apps.pos import ban_coc as BC
from apps.pos import bill as B
from apps.pos import deposits as D
from apps.pos import deposit_application as A
from apps.pos.deposit_models import DepositEvent, DepositMoneyOperation, DepositOrderState, DepositStockHold
from apps.pos.deposit_money import chung_tu_cu_du_dung, financial

TIEN = (Decimal("500000"), Decimal("300000"))
MA_SP = "TEST-SP-COC"                       # mã hàng gắn vào dòng phiếu để thử dấu 💸/⛔ bên VÀNG BÁN


class Command(BaseCommand):
    help = "Smoke áp phiếu đặt-cọc vào hóa đơn bán (sandbox, tự dọn)"

    def handle(self, *args, **o):
        self.loi = []
        c = PmvClient("sandbox", tag="smoke-ban-coc")
        assert c.target == "sandbox", "Bộ kiểm này CHỈ chạy trên bản thử"
        u = c.query("SELECT UserID,EmpID,ShopID FROM SYS_USERS WITH (NOLOCK) WHERE UserName='admin'")[0]
        till = c.query("SELECT TillID FROM T_TILL WITH (NOLOCK) WHERE TillCode='ad'")[0]["TillID"]
        user = SimpleNamespace(user_id=u["UserID"], emp_id=u["EmpID"], shop_id=u["ShopID"], till_id=till,
                               username="smoke-ban-coc", is_authenticated=True, pk=None)
        cust = c.query("SELECT TOP 1 CustID FROM I_CUSTOMER WITH (NOLOCK) ORDER BY CustID")[0]["CustID"]
        gold = c.query("SELECT TOP 1 GoldCode FROM I_GOLD WITH (NOLOCK) ORDER BY GoldCode")[0]["GoldCode"]
        cocs, hoa_don = [], []
        try:
            self.kiem_chung_tu_cu()
            line = {"ProductDesc": "TEST áp cọc", "GoldCode": gold, "SL": 1, "TotalWeight": Decimal(0),
                    "DiamondWeight": Decimal(0), "GoldWeight": Decimal(0), "TaskPrice": Decimal(0),
                    "Size": "", "Notes": "MA_DAT:" + MA_SP + "|hàng đặt thử"}
            for tien in TIEN:
                pk = D.save(c, {"CustID": cust, "EmpID": user.emp_id, "TienCoc": tien,
                                "Description": "KHBL TEST áp cọc vào đơn - tự dọn"}, [line], user)
                cocs.append(pk)
                created=D.header(c,pk)
                self.ok('PMV cấp mã TDC và giữ ShopID của tài khoản',pk.startswith('TDC') and created['ShopID']==user.shop_id)
                detail_ids=c.query('SELECT TrnID FROM TRN_DATCOC_DT WITH (NOLOCK) WHERE TrnID=?',(pk,))
                self.ok('phiếu chính và chi tiết cùng TrnID',len(detail_ids)==1 and detail_ids[0]['TrnID']==pk)
                _, unpaid_error = BC.kiem_truoc_khi_gan(c,pk,cust)
                self.ok('phiếu mới chưa thu bị chặn trước khi liên kết',bool(unpaid_error),unpaid_error)
                self.thu_tien(c, pk, tien, user)
                _, not_ready_error = BC.kiem_truoc_khi_gan(c,pk,cust)
                self.ok('đã thu nhưng chưa sẵn sàng vẫn chưa áp dụng',bool(not_ready_error),not_ready_error)
                DepositOrderState.objects.update_or_create(target=c.target,trn_id=pk,defaults={'fulfilment':'ready'})
            receipts={pk:(D.header(c,pk)['BillCode'],c.query('SELECT TillTxnID,BillCode,TrnDate,TrnTotalAmount FROM T_TILL_TXN WITH (NOLOCK) WHERE TrnRefID=?',(pk,))) for pk in cocs}
            DepositStockHold.objects.create(target=c.target, trn_id=cocs[0], product_code="TEST-COC",
                                            active_key=c.target + ":test-coc", location="Tủ kiểm",
                                            username=user.username)

            ban_do = BC.sp_dang_coc(c)
            self.ok('hàng đặt JSON không bị nhận nhầm thành mã kho',
                    'KHÁCH ĐẶT' not in ban_do and MA_SP not in ban_do)

            ds = BC.phieu_cho(c, cust)
            ma = [p["id"] for p in ds]
            self.ok("quét thấy CẢ HAI phiếu vừa thu tiền trong danh sách chờ của khách",
                    all(x in ma for x in cocs), f"{cocs} vs {ma[:6]}")
            mot = next(p for p in ds if p["id"] == cocs[0])
            self.ok("mỗi dòng đủ thông tin để chọn: ngày cọc · số món · tiền cọc",
                    bool(mot["ngay"]) and mot["so_mon"] == 1 and mot["tien"] == TIEN[0], str(mot))
            self.ok("áp nhiều phiếu thì tiền CỘNG DỒN = 800.000",
                    BC.tong_tien(ds, cocs) == sum(TIEN), str(BC.tong_tien(ds, cocs)))

            trn = B.luu(trn_id="", ban=[], doi=[], ngay=c.fmt_date(), gio=c.fmt_time(),
                        cust_id=cust, emp_id=user.emp_id, till_id=user.till_id, shop_id=user.shop_id,
                        user_id=user.user_id, ghi_chu="KHBL TEST áp cọc", vang_them=Decimal("1000000"),
                        coc=sum(TIEN), c=c)["trn_id"]
            hoa_don.append(trn)
            da, loi = BC.lien_ket(c, trn, cocs, cust, user)
            self.ok("gắn được CẢ HAI phiếu vào hóa đơn còn lưu tạm", da == cocs and not loi, f"{da} · {loi}")
            self.ok('chưa chốt không ghi đã sử dụng',not DepositMoneyOperation.objects.filter(
                target=c.target,invoice_id=trn,kind='apply',status='done').exists() and not BC.da_dung(c.target,cocs[0]))
            da_lai,loi_lai=BC.lien_ket(c,trn,cocs,cust,user)
            self.ok('bấm lại cùng danh sách đọc lại liên kết, không tạo thao tác mới',da_lai==cocs and not loi_lai and
                DepositMoneyOperation.objects.filter(target=c.target,invoice_id=trn,kind='apply').count()==2)
            A.release(c,trn,user,A.release_snapshot(c,trn))
            restored=A.invoice(c,trn)
            self.ok('phục hồi bản nháp gỡ cọc và tính lại tiền khách trả',not A.linked_ids(c,trn) and
                restored['TienCoc']==0 and restored['PayAmount']==Decimal('1000000'))
            self.ok('phục hồi không thu/hoàn cọc',all(financial(c,pk)['valid'] for pk in cocs))
            self.ok('gỡ áp dụng chuyển hàng sẵn sàng',DepositOrderState.objects.filter(target=c.target,trn_id__in=cocs,fulfilment='ready').count()==2)
            B.luu(trn_id=trn,ban=[],doi=[],ngay=c.fmt_date(),gio=c.fmt_time(),cust_id=cust,
                  emp_id=user.emp_id,till_id=user.till_id,shop_id=user.shop_id,user_id=user.user_id,
                  vang_them=Decimal('1000000'),coc=sum(TIEN),c=c)
            BC.lien_ket(c,trn,cocs,cust,user)
            self.ok("tổng phiếu gắn vào hóa đơn = tiền cọc của đơn",
                    BC.tong_da_gan(c, trn) == sum(TIEN), str(BC.tong_da_gan(c, trn)))

            lai = [p["id"] for p in BC.phieu_cho(c, cust)]
            self.ok("phiếu đã gắn KHÔNG còn ở danh sách chờ (một cọc chỉ áp một đơn)",
                    not [x for x in cocs if x in lai])
            _, loi2 = BC.lien_ket(c, trn, [cocs[0]], cust, user)
            self.ok("gắn lại phiếu đã dùng → bị từ chối", bool(loi2), loi2)

            B.chot(trn, till_id=user.till_id, user_id=user.user_id, c=c)
            before_tx=c.query('SELECT TillTxnID,Status,TrnTotalAmount FROM T_TILL_TXN WITH (NOLOCK) WHERE TrnRefID=?',(trn,))
            B.chot(trn, till_id=user.till_id, user_id=user.user_id, c=c)
            self.ok('chốt lại không tạo hoặc đổi chứng từ quỹ',before_tx==c.query(
                'SELECT TillTxnID,Status,TrnTotalAmount FROM T_TILL_TXN WITH (NOLOCK) WHERE TrnRefID=?',(trn,)))
            BC.hoan_tat(c, cocs, user, trn_id=trn, bill_code="SMOKE")
            h = c.query("SELECT Status, TienCoc, PayAmount FROM TRN_RT_BUYSELL WITH (NOLOCK) WHERE TrnID=?", (trn,))[0]
            self.ok("hóa đơn chốt mang đúng tiền cọc 800.000, khách chỉ trả 200.000",
                    h["Status"] == "C" and Decimal(h["TienCoc"]) == sum(TIEN)
                    and Decimal(h["PayAmount"]) == Decimal("200000"), str(h))
            self.ok("PMV công nhận phiếu cọc ĐÃ DÙNG (financial.applied)",
                    all(financial(c, pk)["applied"] for pk in cocs))
            self.ok('áp cọc không đổi số chứng từ, ngày thu hoặc ghi thu cọc lần nữa',all(
                receipts[pk]==(D.header(c,pk)['BillCode'],c.query('SELECT TillTxnID,BillCode,TrnDate,TrnTotalAmount FROM T_TILL_TXN WITH (NOLOCK) WHERE TrnRefID=?',(pk,))) for pk in cocs))
            invoice_cash=c.query('SELECT TrnTotalAmount FROM T_TILL_TXN WITH (NOLOCK) WHERE TrnRefID=?',(trn,))
            self.ok('quỹ hóa đơn chỉ thu phần còn lại 200.000',len(invoice_cash)==1 and invoice_cash[0]['TrnTotalAmount']==Decimal('200000'))
            self.ok("phiếu cọc đánh dấu ĐÃ XONG sau khi chốt",
                    DepositOrderState.objects.filter(target=c.target, trn_id__in=cocs,
                                                     fulfilment="applied").count() == 2)
            self.ok("hàng đang giữ của phiếu được giải phóng",
                    not DepositStockHold.objects.filter(target=c.target, trn_id=cocs[0],
                                                        active_key__isnull=False).exists())
            self.ok("phiếu được nhận diện đang áp dụng, bảo vệ tiền và sản phẩm",
                    all(BC.da_dung(c.target, pk) for pk in cocs))
            self.ok("phiếu đã gắn hóa đơn → mã hàng của nó hết bị đánh dấu ở màn bán",
                    MA_SP not in BC.sp_dang_coc(c))
            self.ok("có nhật ký + chứng từ thao tác cho từng phiếu",
                    DepositEvent.objects.filter(target=c.target, trn_id__in=cocs).count() >= 2
                    and DepositMoneyOperation.objects.filter(target=c.target, trn_id__in=cocs,
                                                             kind="apply", status="done").count() == 2)
            # GĐ chốt: áp là áp TRỌN phiếu, dư ra thì tiệm trả lại khách → hóa đơn khách trả ÂM
            du = D.save(c, {"CustID": cust, "EmpID": user.emp_id, "TienCoc": Decimal("800000"),
                            "Description": "KHBL TEST cọc dư - tự dọn"}, [line], user)
            cocs.append(du)
            self.thu_tien(c, du, Decimal("800000"), user)
            DepositOrderState.objects.update_or_create(target=c.target,trn_id=du,defaults={'fulfilment':'ready'})
            trn2 = B.luu(trn_id="", ban=[], doi=[], ngay=c.fmt_date(), gio=c.fmt_time(), cust_id=cust,
                         emp_id=user.emp_id, till_id=user.till_id, shop_id=user.shop_id,
                         user_id=user.user_id, vang_them=Decimal("500000"), coc=Decimal("800000"), c=c)["trn_id"]
            hoa_don.append(trn2)
            _, loi3 = BC.lien_ket(c, trn2, [du], cust, user)
            B.chot(trn2, till_id=user.till_id, user_id=user.user_id, c=c)
            h2 = c.query("SELECT Status, PayAmount FROM TRN_RT_BUYSELL WITH (NOLOCK) WHERE TrnID=?", (trn2,))[0]
            self.ok("cọc 800.000 cho đơn 500.000 → chốt được, khách trả −300.000 (tiệm trả lại phần dư)",
                    not loi3 and h2["Status"] == "C" and Decimal(h2["PayAmount"]) == Decimal("-300000"), str(h2))
            B.mo_lai(trn,user_id=user.user_id,c=c)
            B.huy(trn,user_id=user.user_id,c=c)
            hoa_don.remove(trn)
            self.ok('xóa hóa đơn đã từng áp cọc chuyển hai phiếu về hàng sẵn sàng',
                DepositOrderState.objects.filter(target=c.target,trn_id__in=cocs[:2],fulfilment='ready').count()==2)
            self.ok('xóa hóa đơn không làm mất tiền cọc đã thu',all(financial(c,pk)['valid'] and not financial(c,pk)['links'] for pk in cocs[:2]))
        finally:
            self.don(c, hoa_don, cocs, user)
        if self.loi:
            for x in self.loi:
                self.stderr.write(self.style.ERROR("FAIL " + x))
            raise SystemExit(1)
        self.stdout.write(self.style.SUCCESS("SMOKE ÁP CỌC: PASS toàn bộ (đã dọn dữ liệu thử)"))

    # ── tiện ích ──────────────────────────────────────────────────────────────
    def ok(self, ten, dieu_kien, chi_tiet=""):
        if dieu_kien:
            self.stdout.write(f"  ok   {ten}")
        else:
            self.loi.append(f"{ten} {chi_tiet}".strip())
            self.stdout.write(self.style.ERROR(f"  FAIL {ten} {chi_tiet}"))

    def kiem_chung_tu_cu(self):
        """Đường nhận phiếu cọc CŨ (appMobile: có tiền trên phiếu, không có chứng từ két)."""
        cu = {"tx": [], "amount": Decimal("500000"), "cash": Decimal("500000"), "bank": Decimal(0),
              "h": {"Status": "W"}}
        self.ok("nhận diện cọc cũ tiền khớp để đối soát (không cấp quyền chốt)", chung_tu_cu_du_dung(cu))
        legacy={**cu,'valid':False,'links':[],'changes':[],'h':{'Status':'W','CustID':'C1'}}
        self.ok('cọc cũ tiền khớp vẫn phải đối soát trước khi chốt PMV',bool(A.reason(legacy,'C1')))
        self.ok("phiếu cũ mà tiền KHÔNG khớp → từ chối",
                not chung_tu_cu_du_dung({**cu, "cash": Decimal("100000")}))
        self.ok("phiếu đã giao khách (C) → từ chối", not chung_tu_cu_du_dung({**cu, "h": {"Status": "C"}}))
        self.ok("phiếu có chứng từ két thì phải đi đường kiểm chặt, không nhận đường cũ",
                not chung_tu_cu_du_dung({**cu, "tx": [{"TillTxnID": "x"}]}))

    def thu_tien(self, c, pk, tien, user):
        """Thu tiền cọc vào két đúng luồng đang chạy (Complete → phân bổ → chốt két)."""
        c.call("TRN_DATCOC_Complete", write=True, p_TrnID=pk, p_UserID=user.user_id)
        bank = tien / 2  # Áp nhiều phiếu có TM/CK, không qua CARDPAY.
        c.update_deposit_money(D.header(c, pk), tien-bank, bank, till_id=user.till_id)
        c.call("T_TILL_TXN_Proc", write=True, p_TrnIDs=pk, p_TillID=user.till_id, p_UserID=user.user_id)

    def don(self, c, hoa_don, cocs, user):
        """Dọn sạch dấu vết: mở lại + gỡ liên kết + hủy hóa đơn → hoàn két → xóa phiếu cọc → xóa dữ liệu MySQL."""
        for trn in hoa_don:
            try:
                con = c.query("SELECT Status FROM TRN_RT_BUYSELL WITH (NOLOCK) WHERE TrnID=?", (trn,))
                if con and con[0]["Status"] == "C":
                    B.mo_lai(trn, user_id=user.user_id, c=c)
                c.call("TRN_RT_BUYSELL_DatCoc_Ins", write=True, p_TrnID=trn, p_IDCoc="")
                B.huy(trn, user_id=user.user_id, c=c)
            except Exception as exc:
                self.stdout.write(f"  (dọn) hóa đơn {trn}: {exc}")
        for pk in cocs:
            try:
                h = D.header(c, pk)
                if h and h["Status"] == "P":
                    c.call("T_TILL_TXN_Del", write=True, day_du=True, p_TrnRefID=pk, pType="TDC",
                           pCongNoBanLe=0, p_UserUpd=user.user_id,
                           p_TrnDateTime_Upd=h["TrnDateTime_Upd"], p_Type="0")
                    h = D.header(c, pk)
                if h and h["Status"] == "W":
                    c.call("TRN_DATCOC_Del", write=True, p_TrnID=pk, p_UserUpd=user.user_id,
                           p_TrnDateTime_Upd=h["TrnDateTime_Upd"])
            except Exception as exc:
                self.stdout.write(f"  (dọn) phiếu cọc {pk}: {exc}")
        for model in (DepositEvent, DepositMoneyOperation, DepositOrderState, DepositStockHold):
            model.objects.filter(target=c.target, trn_id__in=cocs).delete()
        self.stdout.write("  (dọn) xong dữ liệu thử")
