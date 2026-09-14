import datetime
import json
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.core.exceptions import PermissionDenied
from django.test import SimpleTestCase, RequestFactory

from apps.pos import customer_sync as S, customer as C, services


class CustomerSyncTests(SimpleTestCase):
    def setUp(self):
        self.source = {"id": 17, "name": "Nguyễn Văn An", "phone": "0901 234 567", "addr": "Địa chỉ"}
        self.client_pmv = Mock(target="sandbox")
        self.manager = Mock()
        self.manager.filter.return_value.first.return_value = None
        self.receipt = SimpleNamespace(cust_id="", status="writing", payload={}, phone="0901234567", save=Mock())
        self.manager.update_or_create.return_value = (self.receipt, True)
        for name, value in (("sync_lock", lambda target: nullcontext()),
                            ("source_rows", lambda source_id=None: [self.source]),
                            ("source_phone_count", lambda phone: 1),
                            ("matching_phone", lambda client, phone: [])):
            p = patch.object(S, name, value); p.start(); self.addCleanup(p.stop)
        for path, kwargs in (("apps.pos.customer_sync.gateway.dich_hien_tai", {"return_value": "sandbox"}),
                             ("apps.pos.customer_sync.C.duplicate_errors", {"return_value": []}),
                             ("apps.pos.customer_sync.PmvClient", {"return_value": self.client_pmv}),
                             ("apps.pos.customer_sync.CustomerSyncReceipt.objects", {"new": self.manager})):
            p = patch(path, **kwargs); p.start(); self.addCleanup(p.stop)

    def run_insert(self, **kw):
        return S.insert_one(17, kw.get("fingerprint", S.fingerprint(self.source)), kw.get("target", "sandbox"), 1, "SHOP")

    def saved(self, **kw):
        return {"CustID": "CU_TEST", "Phone": "0901234567", "CustName": "Nguyễn Văn An", "Address": "Địa chỉ",
                "BirthDate": datetime.datetime(1900, 1, 1), "Gender": None, "CMND": "", "NoiCap": S.ISSUED_BY, **kw}

    def prior(self, **kw):
        return SimpleNamespace(source_id=17, phone="0901234567", fingerprint=S.fingerprint(self.source),
                               payload={"name": "Nguyễn Văn An", "address": "Địa chỉ", "cmnd": "", "issued_by": S.ISSUED_BY, "_sync_schema": 2}, save=Mock(), **kw)

    def test_only_phone_is_identity_even_when_cccd_matches_another_customer(self):
        source = [{**self.source, "cccd": "111111111111"}]
        rows = S.classify(source, [{"CustID": "CU_1", "Phone": "0901234567", "CMND": "different"}])
        self.assertEqual(rows[0]["status"], "existing")
        self.assertNotIn("cccd", rows[0])
        self.assertEqual(S.classify(source, [{"CustID": "CU_1", "Phone": "0987654321", "CMND": "111111111111"}])[0]["status"], "ready")

    def test_three_groups_and_phone_duplicates(self):
        src = [self.source, {"id": 18, "name": "B", "phone": "090-123-4567"},
               {"id": 19, "name": "C", "phone": "0987654321"}, {"id": 20, "name": "D", "phone": "0961234567"},
               {"id": 21, "name": "", "phone": "0912345678"}, {"id": 22, "name": "E", "phone": "abc"}]
        rows = S.classify(src, [{"Phone": "0987.654.321", "CustID": "CU_EXIST"}])
        self.assertEqual([r["status"] for r in rows], ["attention", "attention", "existing", "ready", "attention", "attention"])

    def test_unknown_gender_not_fabricated_and_normal_form_stays_required(self):
        data, errors, _ = C.clean_form({"CustName": "Khách", "Phone": "0901234567"}, allow_unknown_gender=True)
        self.assertIsNone(data["gender"]); self.assertFalse(errors)
        self.assertTrue(C.clean_form({"CustName": "Khách"})[1])
        self.assertEqual(services._dep_khach({"CustName": "Anh An", "Gender": None})["gioi_tinh"], "Chưa xác định")
        self.assertFalse(services._dep_khach({"CustName": "Anh An", "Gender": None})["gt_lech"])

    def test_wrong_target_and_stale_source_never_write(self):
        with patch.object(C, "upsert") as upsert:
            with self.assertRaises(C.CustomerSaveError): self.run_insert(target="kk")
            with self.assertRaises(C.CustomerSaveError): self.run_insert(fingerprint="f" * 64)
            upsert.assert_not_called()

    def test_existing_formatted_phone_never_inserts(self):
        with patch.object(S, "matching_phone", return_value=[{"CustID": "CU_OLD"}]), patch.object(C, "upsert") as upsert:
            self.assertEqual(self.run_insert()["cust_id"], "CU_OLD")
            upsert.assert_not_called(); self.manager.update_or_create.assert_not_called()

    def test_multiple_pmv_matches_and_source_duplicates_block(self):
        with patch.object(C, "upsert") as upsert:
            with patch.object(S, "matching_phone", return_value=[{}, {}]):
                with self.assertRaises(C.CustomerSaveError): self.run_insert()
            with patch.object(S, "source_phone_count", return_value=2):
                with self.assertRaises(C.CustomerSaveError): self.run_insert()
            upsert.assert_not_called()

    def test_success_records_id_before_postchecks_and_defaults(self):
        def upsert(data, images, **kw):
            self.assertIsNone(data["gender"])
            self.assertEqual(data["birth"], "01/01/1900")
            self.assertEqual(data["cmnd"], "")
            self.assertEqual(data["issued_by"], S.ISSUED_BY)
            self.assertEqual(images, {})
            self.receipt.payload = data
            kw["on_created"]("CU_TEST")
            self.assertEqual(self.receipt.cust_id, "CU_TEST")
            return {"complete": True}
        with patch.object(C, "upsert", side_effect=upsert), patch.object(C, "_current", return_value=self.saved()), patch.object(C, "_has_points", return_value=True):
            self.assertTrue(self.run_insert()["created"])
            self.assertEqual(self.receipt.status, "done")

    def test_insert_copies_source_cccd_and_issued_by(self):
        self.source["cccd"] = " 999 123456789 "
        def upsert(data, images, **kw):
            self.assertEqual(data["cmnd"], "999123456789")
            self.assertEqual(data["issued_by"], "Cục Cảnh Sát QLHC về TTXH")
            self.receipt.payload = data
            kw["on_created"]("CU_TEST")
            return {"complete": True}
        with patch.object(C, "upsert", side_effect=upsert), patch.object(C, "_current", return_value=self.saved(CMND="999123456789")), patch.object(C, "_has_points", return_value=True):
            self.assertTrue(self.run_insert()["created"])

    def test_invalid_cccd_or_changed_cccd_never_silently_dropped(self):
        previous = S.fingerprint(self.source)
        self.source["cccd"] = "123"
        with patch.object(C, "upsert") as upsert:
            with self.assertRaises(C.CustomerSaveError): self.run_insert(fingerprint=previous)
            with self.assertRaisesRegex(C.CustomerSaveError, "CCCD/CMND"): self.run_insert()
            upsert.assert_not_called()
        self.assertEqual(S.classify([self.source], [])[0]["status"], "attention")

    def test_pmv_cccd_conflict_reports_error_without_uncertain_receipt(self):
        self.source["cccd"] = "999123456789"
        with patch.object(C, "duplicate_errors", return_value=["Trùng CCCD/CMND"]), patch.object(C, "upsert") as upsert:
            with self.assertRaisesRegex(C.CustomerSaveError, "Trùng CCCD"): self.run_insert()
            upsert.assert_not_called(); self.manager.update_or_create.assert_not_called()

    def test_old_receipt_survives_cccd_upgrade_without_inserting_again(self):
        receipt = self.prior(status="done", cust_id="CU_TEST", message="Đã sync")
        receipt.fingerprint = S.fingerprint(self.source, legacy=True)
        receipt.payload.pop("_sync_schema")
        receipt.payload["issued_by"] = ""
        self.source["cccd"] = "999123456789"
        self.manager.filter.return_value.first.return_value = receipt
        with patch.object(C, "upsert") as upsert, patch.object(C, "_current", return_value=self.saved(NoiCap="")):
            self.assertFalse(self.run_insert()["created"])
            upsert.assert_not_called()
        self.assertEqual(S.classify([self.source], [{"Phone": self.source["phone"], "CustID": "CU_TEST"}], [receipt])[0]["status"], "existing")

    def test_readback_checks_cmnd_and_noicap(self):
        receipt = self.prior(status="partial", cust_id="CU_TEST")
        for fields in ({"CMND": "999123456789"}, {"NoiCap": "Sai nơi cấp"}):
            with patch.object(C, "_current", return_value=self.saved(**fields)):
                with self.assertRaises(C.CustomerSaveError): S.finish_receipt(receipt, self.client_pmv)
        self.client_pmv.call.assert_not_called()

    def test_double_click_reuses_durable_receipt(self):
        self.manager.filter.return_value.first.return_value = self.prior(status="done", cust_id="CU_TEST")
        with patch.object(C, "upsert") as upsert, patch.object(C, "_current", return_value=self.saved()):
            self.assertFalse(self.run_insert()["created"])
            self.assertFalse(self.run_insert()["created"])
            upsert.assert_not_called()

    def test_completed_receipt_with_deleted_customer_is_not_false_success(self):
        self.manager.filter.return_value.first.return_value = self.prior(status="done", cust_id="CU_TEST")
        with patch.object(C, "_current", return_value=None), patch.object(C, "upsert") as upsert:
            with self.assertRaises(C.CustomerSaveError): self.run_insert()
            upsert.assert_not_called()

    def test_partial_only_finishes_points_never_updates_customer(self):
        self.manager.filter.return_value.first.return_value = self.prior(status="partial", cust_id="CU_TEST")
        with patch.object(C, "upsert") as upsert, patch.object(C, "_current", return_value=self.saved()), patch.object(C, "_has_points", side_effect=[False, True]):
            self.assertTrue(self.run_insert()["created"])
            self.client_pmv.call.assert_called_once_with("I_DiemTichLuy_InsFromGT", write=True, day_du=True, p_CustID="CU_TEST")
            upsert.assert_not_called()

    def test_unknown_outcome_never_reinserts_when_missing(self):
        self.manager.filter.return_value.first.return_value = self.prior(status="uncertain", cust_id="")
        with patch.object(C, "upsert") as upsert:
            with self.assertRaises(C.CustomerSaveError): self.run_insert()
            upsert.assert_not_called()

    def test_unknown_outcome_can_recover_exact_saved_payload(self):
        self.manager.filter.return_value.first.return_value = self.prior(status="uncertain", cust_id="")
        with patch.object(C, "upsert") as upsert, patch.object(S, "matching_phone", return_value=[{"CustID": "CU_TEST"}]), patch.object(C, "_current", return_value=self.saved()), patch.object(C, "_has_points", return_value=True):
            self.assertEqual(self.run_insert()["cust_id"], "CU_TEST")
            upsert.assert_not_called()

    def test_partial_changed_customer_not_overwritten(self):
        self.manager.filter.return_value.first.return_value = self.prior(status="partial", cust_id="CU_TEST")
        with patch.object(C, "_current", return_value=self.saved(Address="Địa chỉ đã thay")), patch.object(C, "upsert") as upsert:
            with self.assertRaises(C.CustomerSaveError): self.run_insert()
            self.client_pmv.call.assert_not_called(); upsert.assert_not_called()

    def test_network_error_preserves_uncertain_and_stops_safe_retry(self):
        with patch.object(C, "upsert", side_effect=ConnectionError("Mất kết nối")):
            with self.assertRaises(ConnectionError): self.run_insert()
        self.assertEqual(self.receipt.status, "uncertain")

    def test_write_lock_failure_remains_retryable(self):
        with patch.object(C, "upsert", side_effect=S.gateway.PmvBlocked("Khóa ghi")):
            with self.assertRaises(S.gateway.PmvBlocked): self.run_insert()
        self.assertEqual(self.receipt.status, "failed")

    def test_source_changes_after_success_is_attention(self):
        receipt = self.prior(status="done", cust_id="CU_TEST", message="Đã sync")
        changed = {**self.source, "phone": "0987654321"}
        self.assertEqual(S.classify([changed], [], [receipt])[0]["status"], "attention")

    def test_permissions_and_invalid_requests_never_touch_sources(self):
        request = RequestFactory().post('/banle/khach-hang/sync/them/', data='{}', content_type='application/json')
        request.user = SimpleNamespace(is_authenticated=False)
        with self.assertRaises(PermissionDenied): S.insert(request)
        request.user = SimpleNamespace(is_authenticated=True, is_superuser=True)
        self.assertEqual(S.insert(request).status_code, 400)
        request = RequestFactory().get('/banle/khach-hang/sync/them/')
        self.assertEqual(S.insert(request).status_code, 405)

    def test_html_escapes_source_in_json_not_raw_markup(self):
        row = {**self.source, "name": '<img src=x onerror="alert(1)">'}
        result = S.classify([row], [])
        self.assertEqual(result[0]["name"], row["name"])
        # UI uses textContent for every value; it never interpolates rows into HTML.
        from pathlib import Path
        script = (Path(__file__).resolve().parents[1] / 'static/js/customer_sync.js').read_text(encoding='utf-8')
        self.assertNotIn('innerHTML', script)
