import datetime as dt
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.pos import money_flow as MF
from apps.pos import money_flow_payment as MFP
from apps.pos.models import GoldBill, MoneyFlow, MoneyFlowPayment, ThauNhom, ThauPaymentLink


class MoneyFlowPhaseOneTests(TestCase):
    @patch('apps.pos.services.nhan_vien_ban', return_value=[])
    def test_mobile_exact_code_and_rolling_windows(self, _employees):
        from django.utils import timezone
        now = timezone.make_aware(dt.datetime(2026, 9, 17, 0, 30))
        user = get_user_model().objects.create_superuser('window-admin', password='x')
        self.client.force_login(user)
        from django.conf import settings
        self.client.cookies['khbl_mobile_session']=self.client.cookies[settings.SESSION_COOKIE_NAME].value
        for code, age, amount in [('RECENT', 30, 100), ('OLDER', 90, 100),
                                  ('EXPIRED', 1441, 100), ('ZERO', 10, 0)]:
            stamp = now - dt.timedelta(minutes=age)
            bill = self.bill(code, trn_date=stamp.date(), trn_time=stamp.strftime('%H:%M:%S'),
                             bill_code=code, tong=amount, emp_id='NV1')
            MoneyFlow.objects.create(direction='IN', service='RETAIL', source_system='KHBL',
                source_type='gold_bill_retail', source_id=bill.trn_id, source_bill_code=code,
                expected_amount=amount, business_date=stamp.date(), source_snapshot={'mobile':{
                    'happened_at':stamp.replace(tzinfo=None).isoformat(),'employee_pmv':'NV1','phone':'0901234567'}})
        with patch('apps.pos.money_flow_views.timezone.localtime', return_value=now):
            def ids(key='', mine='0'):
                response = self.client.get('/banle/mobile/money-in/', {'key': key, 'mine':mine})
                self.assertEqual(response.status_code, 200)
                return [r.source_id for r in response.context['rows']]
            self.assertEqual(ids(), ['RECENT'])
            self.assertEqual(ids('OLDER'), ['OLDER'])
            self.assertEqual(ids('OLD'), [])
            self.assertEqual(ids('EXPIRED'), [])
            self.assertEqual(ids('ZERO'), [])
            from types import SimpleNamespace
            with patch('apps.pmv.models.pmv_user_for_web_user',return_value=SimpleNamespace(emp_id='NV1')):
                self.assertEqual(ids(mine='1'), ['RECENT'])
            with patch('apps.pmv.models.pmv_user_for_web_user',return_value=SimpleNamespace(emp_id='NV2')):
                self.assertEqual(ids(mine='1'), [])
            with patch('apps.pmv.models.pmv_user_for_web_user',return_value=None):
                self.assertEqual(ids(mine='1'), [])

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

    def test_doi_ngang_khong_bi_danh_void(self):
        """22/09/2026: HĐ tổng = 0 (đổi ngang) KHÔNG phải hóa đơn đã xóa — chỉ HĐ bị xóa mới VOID."""
        self.bill("TRB000000000003")
        MF.sync_gold_bills(self.today, self.today)                     # lần đầu: còn 1000 phải thu
        GoldBill.all_objects.filter(trn_id="TRB000000000003").update(tong=0, tien_mat=0)
        self.bill("TRB000000000004", tong=0, tien_mat=0, is_del=True)
        MF.sync_gold_bills(self.today, self.today)
        f = MoneyFlow.objects.get(source_id="TRB000000000003")
        self.assertEqual((f.is_void, f.expected_amount, f.payment_status, f.source_snapshot.get("doi_ngang")),
                         (False, Decimal("0"), MoneyFlow.CONFIRMED, True))
        self.assertFalse(MoneyFlow.objects.filter(source_id="TRB000000000004", is_void=False).exists())   # HĐ xóa: không có dòng sống

    def test_thau_group_owns_flow_and_bank_evidence_sets_confirmed(self):
        self.bill("TBG000000000001", tong=5000, tien_mat=1000, tien_ck=4000)
        group = ThauNhom.objects.create(trn_ids=["TBG000000000001"], bill_codes=["P1"],
            kieu=["thau"], tien_mat=1000, tien_ck=4000, pay_method="bank")
        ThauPaymentLink.objects.create(order_key="k", trn_ids=group.trn_ids, notification_id=9,
            active_notification_id=9, amount=4000, mode="manual")
        with patch.object(MF, "_khcd_rows", return_value=[]):
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

    def test_khcd_sessions_use_payment_direction_and_split(self):
        stamp = dt.datetime.combine(self.today, dt.time(9, 30))
        base = dict(log_id=81, loan_id=12, operation_id=1, happened_at=stamp,
            request_key="once", note="", principal_change=1000, interest=0,
            extra_amount=0, discount_amount=0, reverses_log_id=None, sku="KH226090000001",
            cust_id="CU1", phone="0900000000", loan_state="ACTIVE", version=1,
            customer_snapshot={"name": "Khách Cầm"}, bank_snapshot={})
        rows = [
            {**base, "payment_id": 1, "channel": "CASH", "direction": "OUT",
             "amount": Decimal(400), "reconciliation_state": "RECORDED"},
            {**base, "payment_id": 2, "channel": "BANK", "direction": "OUT",
             "amount": Decimal(600), "reconciliation_state": "RECORDED"},
        ]
        with patch.object(MF, "_khcd_rows", return_value=rows):
            self.assertEqual(MF.sync_khcd(self.today, self.today), 1)
        flow = MoneyFlow.objects.get(source_system="KHCD", source_id="81")
        self.assertEqual((flow.service, flow.direction, flow.cash_amount, flow.bank_amount),
                         ("PAWN", "OUT", Decimal(400), Decimal(600)))
        self.assertEqual(flow.payment_status, "waiting")

    def test_khcd_missing_session_is_void_not_deleted(self):
        MoneyFlow.objects.create(direction="IN", service="REDEEM", source_system="KHCD",
            source_type="cd_loan_log", source_id="99", business_date=self.today,
            expected_amount=100, payment_status="recorded")
        with patch.object(MF, "_khcd_rows", return_value=[]):
            MF.sync_khcd(self.today, self.today)
        flow = MoneyFlow.objects.get(source_id="99")
        self.assertTrue(flow.is_void)
        self.assertEqual(flow.payment_status, "void")

    @patch('apps.pos.money_in.check_source')
    def test_phase_two_in_qr_uses_company_account_without_confirming_money(self, _source):
        user = get_user_model().objects.create_user("cashier")
        flow = MoneyFlow.objects.create(direction="IN", service="RETAIL", source_system="KHBL",
            source_type="gold_bill_retail", source_id="IN-1", source_bill_code="2609-01",
            business_date=self.today, expected_amount=7000, bank_amount=7000)
        instruction = MFP.save_instruction(flow, "BANK", user, company_bank={
            "bank_bin": "VCB", "bank_number": "123456", "bank_user": "KIM HANH",
            "bank_name": "Vietcombank"})
        self.assertEqual((instruction.amount, instruction.bank_account), (Decimal("7000"), "123456"))
        self.assertIn("A000000727", instruction.qr_payload)
        flow.refresh_from_db()
        self.assertEqual(flow.payment_status, "waiting")

    def test_phase_two_out_qr_uses_customer_account_and_is_idempotent(self):
        user = get_user_model().objects.create_user("accountant")
        flow = MoneyFlow.objects.create(direction="OUT", service="PAWN", source_system="KHCD",
            source_type="cd_loan_log", source_id="81", source_group_id="12", source_bill_code="KH22609",
            business_date=self.today, expected_amount=9000, bank_amount=9000)
        first = MFP.save_instruction(flow, "BANK", user, bank_code="TCB",
                                     bank_account="998877", bank_owner="NGUYEN VAN A")
        second = MFP.save_instruction(flow, "BANK", user, bank_code="TCB",
                                      bank_account="998877", bank_owner="NGUYEN VAN A")
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(MoneyFlowPayment.objects.filter(flow=flow, method="BANK").count(), 1)

    @patch("apps.pos.money_flow_views.QR.active_banks", return_value=[])
    def test_phase_two_mobile_in_and_payment_popup_are_read_only_on_get(self, _banks):
        user = get_user_model().objects.create_superuser("phase2-admin", password="x")
        flow = MoneyFlow.objects.create(direction="IN", service="RETAIL", source_system="KHBL",
            source_type="gold_bill_retail", source_id="POPUP-1", source_bill_code="P01",
            business_date=self.today, expected_amount=3000, bank_amount=3000)
        self.client.force_login(user)
        from django.conf import settings
        self.client.cookies['khbl_mobile_session']=self.client.cookies[settings.SESSION_COOKIE_NAME].value
        home = self.client.get("/banle/mobile/dashboard/")
        self.assertEqual(home.status_code, 200)
        self.assertContains(home, "Tạo QR")
        self.assertEqual(self.client.get("/banle/mobile/money-in/").status_code, 200)
        partial = self.client.get("/banle/mobile/money-in/", HTTP_HX_REQUEST="true")
        self.assertNotContains(partial, "mapp-shell")
        response = self.client.get(f"/banle/check-money-flow/{flow.pk}/payment/BANK/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(MoneyFlowPayment.objects.count(), 0)
