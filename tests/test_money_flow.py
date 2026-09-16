import datetime as dt
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.pos import money_flow as MF
from apps.pos.models import GoldBill, MoneyFlow, ThauNhom, ThauPaymentLink


class MoneyFlowPhaseOneTests(TestCase):
    def setUp(self):
        self.today = dt.date.today()

    def bill(self, trn_id, **values):
        defaults = dict(trn_date=self.today, status="C", bill_code=trn_id[-4:],
                        tong=1000, tien_mat=1000, tien_ck=0, bill_kind="retail")
        defaults.update(values)
        return GoldBill.all_objects.create(target="kk", trn_id=trn_id, **defaults)

    def test_retail_positive_and_negative_become_in_and_out(self):
        self.bill("TRB000000000001")
        self.bill("TRB000000000002", tong=-2500, tien_mat=-500, tien_ck=-2000)
        MF.sync_gold_bills(self.today, self.today)
        incoming = MoneyFlow.objects.get(source_id="TRB000000000001")
        outgoing = MoneyFlow.objects.get(source_id="TRB000000000002")
        self.assertEqual((incoming.direction, incoming.expected_amount), ("IN", Decimal("1000")))
        self.assertEqual((outgoing.direction, outgoing.expected_amount, outgoing.bank_amount),
                         ("OUT", Decimal("2500"), Decimal("2000")))

    def test_thau_group_owns_flow_and_bank_evidence_sets_confirmed(self):
        self.bill("TBG000000000001", tong=5000, tien_mat=1000, tien_ck=4000)
        group = ThauNhom.objects.create(trn_ids=["TBG000000000001"], bill_codes=["P1"],
            kieu=["thau"], tien_mat=1000, tien_ck=4000, pay_method="bank")
        ThauPaymentLink.objects.create(order_key="k", trn_ids=group.trn_ids, notification_id=9,
            active_notification_id=9, amount=4000, mode="manual")
        MF.sync(self.today, self.today)
        flow = MoneyFlow.objects.get(service="GOLD_BUY", source_type="thau_nhom", source_id=str(group.pk))
        self.assertEqual((flow.direction, flow.expected_amount, flow.payment_status),
                         ("OUT", Decimal("5000"), "confirmed"))
        self.assertFalse(MoneyFlow.objects.filter(source_type="gold_bill_ungrouped").exists())

    def test_deposit_uses_payment_plan_without_writing_source(self):
        bill = self.bill("TDC000000000001", bill_kind="deposit", status="P", tong=0, tien_mat=0,
                         payment_plan={"cash": "300", "bank": "700", "bank_status": "matched"})
        MF.sync_gold_bills(self.today, self.today)
        flow = MoneyFlow.objects.get(source_id=bill.trn_id)
        self.assertEqual((flow.service, flow.direction, flow.expected_amount, flow.payment_status),
                         ("DEPOSIT", "IN", Decimal("1000"), "confirmed"))
        bill.refresh_from_db()
        self.assertEqual(bill.tong, 0)

    def test_page_is_permission_protected_and_readable(self):
        user = get_user_model().objects.create_superuser("mf-admin", password="x")
        self.client.force_login(user)
        response = self.client.get("/banle/check-money-flow/", {"d1": self.today, "d2": self.today})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Kiểm soát IN / OUT")
