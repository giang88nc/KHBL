"""
Kiểm chứng apps/pmv/money.py với DỮ LIỆU THẬT (chỉ đọc, mặc định chạy trên sandbox).

Chạy: manage.py smoke_pmv_money [--target sandbox|pmv] [--rows 400]
LUẬT: bộ này chưa PASS thì cấm mở kênh ghi và cấm tin màn bán hàng.
"""
from decimal import Decimal

from django.core.management.base import BaseCommand

from apps.pmv import money as M
from apps.pmv.client import PmvClient


class Command(BaseCommand):
    help = "Đối chiếu công thức tiền của KHBL với hóa đơn thật trong PMV"

    def add_arguments(self, parser):
        parser.add_argument("--target", default="sandbox", choices=["sandbox", "pmv"])
        parser.add_argument("--rows", type=int, default=400)

    def handle(self, *args, **opts):
        c = PmvClient(opts["target"], tag="smoke_money")
        q = c.query
        n_ok = n_fail = 0

        def check(name, cond, chi_tiet=""):
            nonlocal n_ok, n_fail
            if cond:
                n_ok += 1
                self.stdout.write(f"  PASS  {name}")
            else:
                n_fail += 1
                self.stdout.write(self.style.ERROR(f"  FAIL  {name} {chi_tiet}"))

        # --- 0. làm tròn kiểu T-SQL (half away from zero), không phải banker's ---
        moc = [("15714200", "15714000"), ("3132500", "3133000"), ("24501500", "24502000"),
               ("-3514200", "-3514000"), ("29114900", "29115000"), ("-3132500", "-3133000")]
        sai = [(a, str(M.round_vnd(Decimal(a))), b) for a, b in moc if M.round_vnd(Decimal(a)) != Decimal(b)]
        check(f"làm tròn nghìn half-away-from-zero ({len(moc)} mốc)", not sai, str(sai))

        # --- 1. DÒNG BÁN: đối chiếu SellAmount ---
        rows = q(
            f"SELECT TOP {int(opts['rows'])} s.TrnID, s.ProductCode, s.TotalWeight, s.DiamondWeight, "
            "s.SellRate, s.TaskPrice, s.SellAmount, ISNULL(g.PriceUnit,'L') AS PriceUnit "
            "FROM TRN_RT_BUYSELL_SELL s WITH (NOLOCK) "
            "LEFT JOIN I_GOLD g WITH (NOLOCK) ON g.GoldCode = s.GoldCode "
            "ORDER BY s.TrnID DESC"
        )
        lech = [r for r in rows
                if M.sell_amount(r["TotalWeight"], r["DiamondWeight"], r["SellRate"],
                                 r["TaskPrice"], r["PriceUnit"], client=c) != M.dec(r["SellAmount"])]
        check(f"SellAmount khớp {len(rows) - len(lech)}/{len(rows)} dòng bán", not lech,
              str([(r["ProductCode"], str(r["SellAmount"])) for r in lech[:3]]))

        # --- 2. DÒNG VÀNG CŨ trong hóa đơn bán (hằng 1000, KHÔNG dùng CcyRate) ---
        rows = q(
            f"SELECT TOP {int(opts['rows'])} b.TrnID, b.GoldCode, b.GoldWeight, b.BuyRate, "
            "b.PercentValue, b.BuyAmount, ISNULL(g.PriceUnit,'L') AS PriceUnit "
            "FROM TRN_RT_BUYSELL_BUYGOLD b WITH (NOLOCK) "
            "LEFT JOIN I_GOLD g WITH (NOLOCK) ON g.GoldCode = b.GoldCode ORDER BY b.TrnID DESC"
        )
        lech = [r for r in rows
                if M.buy_amount_in_bill(r["GoldWeight"], r["BuyRate"], r["PercentValue"],
                                        r["PriceUnit"], client=c) != M.dec(r["BuyAmount"])]
        check(f"BuyAmount (vàng cũ trong HĐ) khớp {len(rows) - len(lech)}/{len(rows)}", not lech,
              str([(r["TrnID"], str(r["BuyAmount"])) for r in lech[:3]]))

        # --- 3. PHIẾU THÂU độc lập ---
        rows = q(
            f"SELECT TOP {int(opts['rows'])} t.TrnID, t.GoldCode, t.GoldWeight, t.BuyRate, "
            "t.PercentValue, t.AddMoney, t.TotalAmount, ISNULL(g.PriceUnit,'L') AS PriceUnit "
            "FROM TRN_RT_BUYGOLD t WITH (NOLOCK) "
            "LEFT JOIN I_GOLD g WITH (NOLOCK) ON g.GoldCode = t.GoldCode ORDER BY t.TrnID DESC"
        )
        lech = [r for r in rows
                if M.buy_amount_standalone(r["GoldWeight"], r["BuyRate"], r["PercentValue"],
                                           r["AddMoney"], r["PriceUnit"], client=c) != M.dec(r["TotalAmount"])]
        check(f"Phiếu thâu khớp {len(rows) - len(lech)}/{len(rows)}", not lech,
              str([(r["TrnID"], str(r["TotalAmount"])) for r in lech[:3]]))

        # --- 4. TỔNG HÓA ĐƠN: Sell/Buy/Total/Pay ---
        bills = q(
            f"SELECT TOP {min(int(opts['rows']), 200)} TrnID, SellTotalAmount, BuyTotalAmount, "
            "TotalAmount, Discount, TaskPriceAdd, PayAmount, TienTraLai "
            "FROM TRN_RT_BUYSELL WITH (NOLOCK) WHERE IsDel = '0' ORDER BY CreatedDate DESC"
        )
        bad_total = bad_pay = bad_sum = 0
        for b in bills:
            st, bt = M.dec(b["SellTotalAmount"]), M.dec(b["BuyTotalAmount"])
            t = M.bill_totals([st], [bt], b["Discount"], b["TaskPriceAdd"])
            if t["total"] != M.dec(b["TotalAmount"]):
                bad_total += 1
            if t["pay"] != M.dec(b["PayAmount"]):
                bad_pay += 1
            lines = q("SELECT SellAmount FROM TRN_RT_BUYSELL_SELL WITH (NOLOCK) WHERE TrnID = ?", (b["TrnID"],))
            if sum((M.dec(x["SellAmount"]) for x in lines), M.D0) != st:
                bad_sum += 1
        check(f"TotalAmount = Sell − Buy (KHÔNG trừ giảm giá) — {len(bills) - bad_total}/{len(bills)}", not bad_total)
        check(f"PayAmount = Total − giảm giá + công thêm — {len(bills) - bad_pay}/{len(bills)}", not bad_pay)
        check(f"SellTotal = Σ dòng đã làm tròn — {len(bills) - bad_sum}/{len(bills)}", not bad_sum)

        # --- 5. hóa đơn PayAmount ÂM (tiệm trả lại khách) ---
        am = q("SELECT TOP 5 TrnID, SellTotalAmount, BuyTotalAmount, Discount, TaskPriceAdd, "
               "PayAmount, TienTraLai FROM TRN_RT_BUYSELL WITH (NOLOCK) "
               "WHERE PayAmount < 0 AND IsDel = '0' ORDER BY CreatedDate DESC")
        bad = [r for r in am
               if M.bill_totals([r["SellTotalAmount"]], [r["BuyTotalAmount"]], r["Discount"], r["TaskPriceAdd"])["tra_lai"]
               != M.dec(r["TienTraLai"])]
        check(f"Hóa đơn ÂM: tra_lai = TienTraLai ({len(am) - len(bad)}/{len(am)})", not bad and bool(am),
              str([r["TrnID"] for r in bad[:3]]))

        # --- 6. chuỗi trọng lượng đối chiếu hàm SQL của vendor ---
        mau = q("SELECT TOP 120 s.TotalWeight AS w, ISNULL(g.PriceUnit,'L') AS u, "
                "dbo.GoldWeightToChar(s.TotalWeight, ISNULL(g.PriceUnit,'L')) AS ngan, "
                "dbo.GoldWeightToChar_Bill(s.TotalWeight, ISNULL(g.PriceUnit,'L')) AS daydu "
                "FROM TRN_RT_BUYSELL_SELL s WITH (NOLOCK) "
                "LEFT JOIN I_GOLD g WITH (NOLOCK) ON g.GoldCode = s.GoldCode ORDER BY s.TrnID DESC")
        lech_bill = [(str(r["w"]), r["daydu"], M.weight_bill(r["w"], r["u"]))
                     for r in mau if r["u"] == "L" and M.weight_bill(r["w"], r["u"]) != (r["daydu"] or "").strip()]
        check(f"weight_bill khớp dbo.GoldWeightToChar_Bill ({len(mau) - len(lech_bill)}/{len(mau)})",
              not lech_bill, str(lech_bill[:4]))

        # --- 7. bớt lẻ ---
        check("bot_le(3.133.000) = 3.000", M.bot_le(3133000) == Decimal("3000"))
        check("bot_le(198.302.000) = 2.000", M.bot_le(198302000) == Decimal("2000"))

        self.stdout.write((self.style.SUCCESS if not n_fail else self.style.ERROR)(
            f"KẾT QUẢ: {n_ok} PASS / {n_fail} FAIL"))
