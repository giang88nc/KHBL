from contextlib import nullcontext
from unittest.mock import Mock, patch
from django.test import SimpleTestCase, RequestFactory
from apps.pos import customer as C, customer_phones as P, customer_sync as Sync, services as S, views


class CustomerPhonesTests(SimpleTestCase):
    def data(self, **kwargs):
        result, errors, _ = C.clean_form({"CustName": "Khách Thử", "Gender": "1", "Phone": "0901234567", **kwargs})
        self.assertFalse(errors)
        return result

    def test_exactly_ten_ascii_digits_and_same_row_duplicates(self):
        for value in ("123456789", "12345678901", "０９０１２３４５６７", "09012345a7"):
            self.assertTrue(C.clean_form({"CustName": "A", "Gender": "1", "GhiChu3": value})[1])
        self.assertTrue(C.clean_form({"CustName": "A", "Gender": "1", "Phone": "0901234567", "GhiChu2": "090 123 4567"})[1])
        self.assertEqual(self.data(GhiChu2="091 234 5678")["phone2"], "0912345678")

    def test_all_nine_cross_column_conflicts_block(self):
        for incoming in P.COLUMNS:
            for stored in P.COLUMNS:
                data = self.data(Phone="", **({incoming: "0901234567"} if incoming != "Phone" else {})) if incoming != "Phone" else self.data()
                client = Mock()
                client.query.return_value = [{"CustID": "CU_OTHER", stored: "090 123 4567"}]
                self.assertTrue(any("0901234567" in e for e in C.duplicate_errors(client, data)), (incoming, stored))

    def test_update_excludes_only_own_custid(self):
        client = Mock()
        client.query.return_value = []
        C.duplicate_errors(client, self.data(CustID="CU_SELF", GhiChu3="0987654321"))
        sql, params = client.query.call_args.args
        self.assertIn("CustID <> ?", sql)
        self.assertEqual(params[-1], "CU_SELF")
        for column in P.COLUMNS: self.assertIn(column, sql)

    def test_sync_recognizes_secondary_phone_without_inserting(self):
        row = {"id": 1, "name": "A", "phone": "0901234567"}
        for column in P.COLUMNS:
            found = Sync.classify([row], [{"CustID": "CU_OLD", column: row["phone"]}])
            self.assertEqual(found[0]["status"], "existing")
        found = Sync.classify([row], [{"CustID": "CU_OLD", "Phone": row["phone"], "GhiChu2": row["phone"]}])
        self.assertEqual(found[0]["status"], "existing")

    def test_search_exact_phone_is_one_deterministic_id_but_cccd_and_name_are_not(self):
        sql, params = P.search("090 123 4567")
        self.assertIn("SELECT TOP 1", sql)
        self.assertIn("p.CustID", sql)
        self.assertEqual(params[1:], ("0901234567",) * 5)
        for term in ("096088009068", "381472415", "Nguyễn Văn An"):
            sql, params = P.search(term)
            self.assertNotIn("TOP 1", sql)
        self.assertNotIn("LIKE", P.search("096088009068")[0])

    def test_upsert_saves_and_verifies_both_secondary_phones(self):
        data = self.data(GhiChu2="0912345678", GhiChu3="0987654321")
        client = Mock(target="sandbox")
        client.call.return_value = (0, [[{"CustID": "CU_TEST"}]])
        saved = {"CustID": "CU_TEST", "CustName": data["name"], "Phone": data["phone"], "CMND": "",
                 "GhiChu2": data["phone2"], "GhiChu3": data["phone3"]}
        with patch.object(C, "phone_write_lock", return_value=nullcontext()), patch.object(C, "duplicate_errors", return_value=[]), \
             patch.object(C, "_has_points", return_value=True), patch.object(C, "_current", return_value=saved):
            self.assertTrue(C.upsert(data, {}, client=client)["complete"])
            self.assertEqual(client.call.call_args.kwargs["p_GhiChu2"], "0912345678")
            self.assertEqual(client.call.call_args.kwargs["p_GhiChu3"], "0987654321")
        with patch.object(C, "phone_write_lock", return_value=nullcontext()), patch.object(C, "duplicate_errors", return_value=[]), \
             patch.object(C, "_current", return_value={**saved, "GhiChu3": ""}):
            with self.assertRaises(C.CustomerSaveError): C.upsert(data, {}, client=client)

    def test_duplicate_rejected_before_any_proc_or_image_write(self):
        client = Mock(target="sandbox")
        client.query.return_value = [{"CustID": "CU_OTHER", "GhiChu3": "0901234567"}]
        with patch.object(C, "phone_write_lock", return_value=nullcontext()):
            with self.assertRaises(C.CustomerSaveError): C.upsert(self.data(), {"p_ImageData": b"image"}, client=client)
        client.call.assert_not_called()

    def test_lookup_requires_permission_and_returns_all_conflicts(self):
        request = RequestFactory().get("/", {"phone": "0901234567", "cust_id": "CU_SELF"})
        with patch.object(views.Q, "chan") as permission, patch.object(S, "client") as client:
            client.return_value.query.return_value = [{"CustID": "CU_OTHER", "CustName": "A"}]
            response = views.khach_kiem_sdt(request)
            self.assertEqual(response.status_code, 200)
            permission.assert_called_once_with(request, "KHACH_HANG")
            self.assertIn(b"CU_OTHER", response.content)
            self.assertEqual(response["Cache-Control"], "no-store")
