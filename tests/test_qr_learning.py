"""Explicit learning persists only in temporary files; no customer/PMV writes."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from apps.pos import cccd, cccd_learning as learning


class _LearningCase(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "learned_rules.json"
        self.env = patch.dict(os.environ, {"KHBL_QR_LEARNING_PATH": str(self.path)})
        self.env.start()
        self.addCleanup(self.env.stop)

    def teach(self, raw, output, scope="auto", **extra):
        return learning.save_rule(raw, output, scope, revision=learning.read_store()["revision"], actor="tester", **extra)

    def qr(self, name="Khách Thử A", address="Đường A"):
        return f"000000000001||{name}|01012000|Nam|{address}|01012024"


class LearningTests(_LearningCase):
    def test_store_created_before_waiting_list_gets_empty_drafts(self):
        self.path.write_text(json.dumps({"version": 1, "revision": 0, "rules": [], "history": []}), encoding="utf-8")
        self.assertEqual(learning.read_store()["drafts"], [])

    def test_draft_is_idempotent_and_moves_atomically_to_learned(self):
        state, message, draft_id = learning.save_draft(
            "Ngh)a", "ho_ten", revision=0, actor="tester")
        self.assertIn("CHỜ", message)
        self.assertEqual(len(state["drafts"]), 1)
        original = self.path.read_bytes()
        again, _, again_id = learning.save_draft(
            "Ngh)a", "ho_ten", revision=0, actor="tester")
        self.assertEqual(again_id, draft_id)
        self.assertEqual(again["revision"], 1)
        self.assertEqual(self.path.read_bytes(), original)
        state, message = learning.save_rule(
            "Ngh)a", "Nghĩa", "ho_ten", revision=1, actor="tester", draft_id=draft_id)
        self.assertEqual(state["drafts"], [])
        self.assertEqual(len(state["rules"]), 1)
        self.assertIn("CHỜ sang ĐÃ HỌC", message)
        self.assertIsNotNone(state["history"][-1]["draft_moved"])

    def test_learning_only_a_filtered_fragment_keeps_full_raw_waiting(self):
        full = "Kh≤m 4, Ngh)a"
        state, _, draft_id = learning.save_draft(full, revision=0, actor="tester")
        state, message = learning.save_rule(
            "Ngh)a", "Nghĩa", revision=state["revision"], actor="tester", draft_id=draft_id)
        self.assertEqual(state["drafts"][0]["raw"], full)
        self.assertIn("RAW đầy đủ vẫn", message)

    def test_draft_validates_content_conflicts_and_existing_learned_rule(self):
        for raw in (None, "", "<raw>", "x" * 8193):
            with self.assertRaises(learning.LearningError):
                learning.prepare_draft(raw)
        state, _, first_id = learning.save_draft("Ngh)a", revision=0, actor="tester")
        with self.assertRaises(learning.LearningConflict):
            learning.save_draft("Th╦", revision=0, actor="tester")
        state, _ = learning.save_rule("Ngh)a", "Nghĩa", revision=state["revision"], actor="tester",
                                      draft_id=first_id)
        state, message, draft_id = learning.save_draft(
            "Ngh)a", revision=state["revision"], actor="tester")
        self.assertEqual(draft_id, "")
        self.assertIn("ĐÃ HỌC", message)
        self.assertEqual(len(state["drafts"]), 0)

    def test_requested_fragment_shapes_apply_within_larger_fields(self):
        pairs = [("KhaV259nC226n", "Kha Vạn Cân", "dia_chi"),
                 ("ñp Tríi L≥n", "Ấp Trại Lớn", "dia_chi"),
                 ("Ngô Minh Ngh)a", "Ngô Minh Nghĩa", "ho_ten"),
                 ("Ph░íng, Sαi, Ph432417ng S417n", "Phương Sài, Phường Tây", "dia_chi")]
        for raw, output, scope in pairs:
            self.teach(raw, output, scope)
            qr = self.qr(name=raw) if scope == "ho_ten" else self.qr(address="Số 25, " + raw + ", Khu A")
            result = cccd.normalize_cccd_group([qr], context_model={})
            self.assertEqual(result["loi"], [])
            self.assertIn(output, result["data"][scope])
            self.assertTrue(result["learned_examples"])
        untouched = cccd.normalize_cccd_group([self.qr(address="Phương Sơn")], context_model={})
        self.assertEqual(untouched["data"]["dia_chi"], "Phương Sơn")
        self.assertFalse(untouched["learned_examples"])

    def test_approved_output_is_not_decoded_again_or_cascaded(self):
        self.teach("Ngh)a", "Phú", "dia_chi")
        self.teach("Phú", "Không áp dây chuyền", "dia_chi")
        result = cccd.normalize_cccd_group([self.qr(address="Ngh)a, Kh≤m 4")], context_model={})
        self.assertEqual(result["data"]["dia_chi"], "Phú, Khóm 4")

    def test_boundaries_scope_and_longest_phrase(self):
        self.teach("Ngh)a", "Nghĩa", "ho_ten")
        self.teach("Minh Ngh)a", "Minh An", "ho_ten")
        result = cccd.normalize_cccd_group([self.qr(name="Khách Minh Ngh)a", address="Ngh)a")], context_model={})
        self.assertEqual(result["data"]["ho_ten"], "Khách Minh An")
        self.assertNotEqual(result["data"]["dia_chi"], "Nghĩa")
        result = cccd.normalize_cccd_group([self.qr(name="XNgh)aY")], context_model={})
        self.assertFalse(result["learned_examples"])

    def test_persistence_idempotent_retry_conflict_edit_and_disable(self):
        state, _ = self.teach("Ngh)a", "Nghĩa", "ho_ten")
        original = self.path.read_bytes()
        again, _ = learning.save_rule("Ngh)a", "Nghĩa", "ho_ten", revision=0, actor="tester")
        self.assertEqual(state["revision"], again["revision"])
        self.assertEqual(original, self.path.read_bytes())
        with self.assertRaises(learning.LearningConflict):
            self.teach("Ngh)a", "Nghệ", "ho_ten")
        rule_id = state["rules"][0]["id"]
        state, _ = self.teach("Ngh)a", "Nghệ", "ho_ten", edit_id=rule_id)
        self.assertEqual(cccd.analyze_text("Ngh)a", "ho_ten")["output"], "Nghệ")
        state, _ = learning.save_rule(edit_id=rule_id, active=False, revision=state["revision"], actor="tester")
        self.assertNotEqual(cccd.analyze_text("Ngh)a", "ho_ten")["output"], "Nghệ")
        self.assertEqual(len(state["history"]), 3)

    def test_two_editors_cannot_silently_overwrite_each_other(self):
        def save(pair):
            try:
                learning.save_rule(*pair, revision=0, actor="tester")
                return "ok"
            except learning.LearningConflict:
                return "conflict"
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(save, [("Ngh)a", "Nghĩa"), ("Th╦", "Thị")]))
        self.assertCountEqual(results, ["ok", "conflict"])
        self.assertEqual(len(learning.read_store()["rules"]), 1)

    def test_full_qr_requires_all_fields_and_preserves_identity(self):
        raw, gold = self.qr(name="LÃ½ ThÆ¡i"), self.qr(name="Lý Thới")
        self.teach(raw, gold)
        self.assertEqual(cccd.normalize_cccd_group([raw], context_model={})["normalized_qr"], gold)
        changed = raw.replace("000000000001", "000000000002")
        self.assertFalse(cccd.normalize_cccd_group([changed], context_model={})["learned_examples"])
        with self.assertRaises(learning.LearningError):
            self.teach(raw, gold.replace("000000000001", "000000000002"))
        with self.assertRaises(learning.LearningError):
            self.teach(raw, gold.replace("01012000", "02012000"))

    def test_validation_write_failure_and_corrupt_store(self):
        for args in [("417", "ơ"), ("Ngh)a", "<img>"), ("Ngh)a", "&#60;img&#62;"),
                     ("Ngh)a", "Ngá»c"), (None, "Nghĩa"), ("Ngh)a", "Nghĩa", [])]:
            with self.assertRaises(learning.LearningError):
                learning.prepare_rule(*args)
        self.teach("Ngh)a", "Nghĩa")
        original = self.path.read_bytes()
        with patch.object(learning.os, "replace", side_effect=PermissionError("denied")):
            with self.assertRaises(learning.LearningError):
                self.teach("Th╦", "Thị")
        self.assertEqual(self.path.read_bytes(), original)
        self.path.write_text("{broken", encoding="utf-8")
        with self.assertRaises(learning.LearningError):
            self.teach("Th╦", "Thị")
        self.assertEqual(self.path.read_text(), "{broken")


class TextToolTests(_LearningCase):
    def test_translate_uses_live_rules_in_auto_scope_without_writing(self):
        from apps.pos import cccd_tools as tool
        self.teach("Ngô Minh Ngh)a", "Ngô Minh Nghĩa", "ho_ten")
        before = self.path.read_bytes()
        self.assertEqual(tool.translate("Ngô Minh Ngh)a")["output"], "Ngô Minh Nghĩa")
        self.assertEqual(self.path.read_bytes(), before)

    def test_filter_keeps_only_damaged_words_with_exact_raw_spans(self):
        from apps.pos import cccd_tools as tool
        raw = "Nguy7877n Ngh)a, Khu Th☃n, Kh≤m 4"
        output = "Nguyễn Ngh)a, Khu Th☃n, Khóm 4"
        pairs = tool.filter_errors(raw, output)["segments"]
        self.assertEqual([(p["raw"], p["output"]) for p in pairs], [("Ngh)a", "Ngh)a"), ("Th☃n", "Th☃n")])
        for pair in pairs:
            self.assertEqual(raw[pair["raw_start"]:pair["raw_end"]], pair["raw"])
            self.assertEqual(output[pair["output_start"]:pair["output_end"]], pair["output"])
        self.assertFalse(self.path.exists())

    def test_filter_correspondence_survives_words_expanding_and_manual_edits(self):
        from apps.pos import cccd_tools as tool
        self.teach("KhaV259nC226n", "Kha Vạn Cân", "dia_chi")
        pairs = tool.filter_errors("25 KhaV259nC226n, Ngh)a, Khu A", "25 Kha Vạn C☃n, Ngh)a, Khu A")["segments"]
        self.assertEqual(pairs[0]["raw"], "KhaV259nC226n, Ngh)a")
        self.assertEqual(pairs[0]["output"], "Kha Vạn C☃n, Ngh)a")

    def test_filter_preserves_numbers_valid_vietnamese_and_punctuation(self):
        from apps.pos import cccd_tools as tool
        text = "272A, B272, KP7889, 25/7A, P.5, K'Ho, Hòa-Bình, (Khu A), Nguyễn Ánh"
        self.assertEqual(tool.filter_errors(text, text)["segments"], [])
        # A semantic mistake written in valid Unicode is not a font error.
        self.assertEqual(tool.filter_errors("L≥n", "Lòn")["segments"], [])

    def test_filter_repeated_or_unanchored_tokens_keeps_context(self):
        from apps.pos import cccd_tools as tool
        for raw, output in [("A Ngh)a B Ngh)a C", "A Ngh)a C"), ("ABC DEF", "☃")]:
            pairs = tool.filter_errors(raw, output)["segments"]
            self.assertEqual(len(pairs), 1)
            self.assertEqual(pairs[0]["raw"], raw)
            self.assertEqual(pairs[0]["output"], output)
            self.assertEqual(pairs[0]["alignment"], "context")

    def test_filter_qr_with_extra_or_missing_delimiters_and_identity_guard(self):
        from apps.pos import cccd_tools as tool
        for raw in [self.qr(name="Khách Ngh)a") + "|EXTRA", self.qr(name="Khách Ngh)a").replace("||", "|")]:
            pairs = tool.filter_errors(raw, self.qr(name="Khách Ngh)a"))["segments"]
            self.assertEqual([(p["raw"], p["output"]) for p in pairs], [("Ngh)a", "Ngh)a")])
        with self.assertRaises(learning.LearningError):
            tool.filter_errors(self.qr(name="Ngh)a"), self.qr(name="Ngh)a").replace("000000000001", "000000000002"))

    def test_validation_and_learning_reject_unfixed_font_damage(self):
        from apps.pos import cccd_tools as tool
        for raw in (None, "", "x" * 8193):
            with self.assertRaises(learning.LearningError):
                tool.translate(raw)
        with self.assertRaises(learning.LearningError):
            tool.filter_errors("Raw", "")
        for output in ("Ngh)a", "Th☃n", "Trưíng"):
            with self.assertRaises(learning.LearningError):
                learning.prepare_rule("Khách A", output)


class LearningEndpointTests(_LearningCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.prod")
        import django
        django.setup()

    def setUp(self):
        super().setUp()
        # Ma trận quyền nằm trong DB; bộ kiểm này chạy KHÔNG DB nên thay bằng bộ quyền gắn trên user giả.
        # Nhánh không-đăng-nhập / superuser của chính customer.quyen được kiểm riêng ở test dưới.
        self.gia_quyen = patch("apps.pos.customer.quyen",
                               side_effect=lambda user, action="can_view": action in getattr(user, "quyen_kh", ()))
        self.addCleanup(self.gia_quyen.stop)
        self.gia_quyen.start()

    def request(self, data, admin=True, logged_in=True, quyen_kh=("can_view", "can_edit")):
        """User giả kèm quyền danh mục KHÁCH HÀNG mà công cụ QR kế thừa (GĐ chốt 11/09/2026)."""
        from django.test import RequestFactory
        request = RequestFactory().post("/banle/khach-hang/qr/hoc/ap-dung/", data, content_type="application/json")
        request.user = SimpleNamespace(is_authenticated=logged_in, is_superuser=admin,
                                       quyen_kh=quyen_kh, get_username=lambda: "tester")
        return request

    def test_preview_never_persists_and_apply_persists(self):
        from apps.pos.qr_learning_views import preview, save
        body = {"raw": "Ngh)a", "output": "Nghĩa", "scope": "ho_ten", "revision": 0}
        with patch("apps.pmv.client.PmvClient.call", side_effect=AssertionError("No PMV writes")), \
             patch("apps.pmv.client.PmvClient.query", side_effect=AssertionError("No PMV reads")):
            response = preview(self.request(body))
            self.assertEqual(response.status_code, 200)
            self.assertEqual(json.loads(response.content)["after"], "Nghĩa")
            self.assertFalse(self.path.exists())
            response = save(self.request(body))
            self.assertEqual(response.status_code, 200)
            self.assertIn("no-store", response["Cache-Control"])
            self.assertTrue(self.path.exists())
            self.assertEqual(cccd.analyze_text("Ngh)a", "ho_ten")["output"], "Nghĩa")

    def test_rights_inherit_customer_category_and_bad_payload_does_not_write(self):
        """Quyền CRUD kế thừa danh mục KHÁCH HÀNG (GĐ chốt 11/09/2026) — hết luật riêng "phải là quản trị".

        Tạo/sửa mẫu đi theo ô "Tạo / sửa" của Khách hàng; chỉ có ô "Xem" thì xem thử được, ghi thì 403.
        """
        from apps.pos.qr_learning_views import filter_errors, preview, save, save_draft
        body = {"raw": "Ngh)a", "output": "Nghĩa", "revision": 0}
        nhap = {"raw": "Ngh)a", "revision": 0}
        # nhân viên thường (không superuser) nhưng có quyền Tạo/sửa Khách hàng → GHI ĐƯỢC
        self.assertEqual(save(self.request(body, admin=False)).status_code, 200)
        self.assertTrue(self.path.exists())
        self.path.unlink()
        self.assertEqual(save_draft(self.request(nhap, admin=False)).status_code, 200)
        self.path.unlink()
        # chỉ có quyền XEM → xem thử/dịch được, nhưng không được ghi
        chi_xem = dict(admin=False, quyen_kh=("can_view",))
        self.assertEqual(preview(self.request(body, **chi_xem)).status_code, 200)
        self.assertEqual(save(self.request(body, **chi_xem)).status_code, 403)
        self.assertEqual(save_draft(self.request(nhap, **chi_xem)).status_code, 403)
        self.assertFalse(self.path.exists())
        # không có quyền nào trong danh mục Khách hàng → cả đọc lẫn ghi đều 403
        khong = dict(admin=False, quyen_kh=())
        for view, data in ((preview, body), (filter_errors, body), (save, body), (save_draft, nhap)):
            self.assertEqual(view(self.request(data, **khong)).status_code, 403)
        self.assertFalse(self.path.exists())
        # chưa đăng nhập: @login_required đá về trang đăng nhập trước khi tới luật quyền
        for view, data in ((save, body), (save_draft, nhap)):
            khach = self.request(data, logged_in=False)
            khach.META["HTTP_HOST"] = "127.0.0.1"
            self.assertEqual(view(khach).status_code, 302)
        self.assertFalse(self.path.exists())
        self.assertEqual(save(self.request([])).status_code, 400)
        self.assertFalse(self.path.exists())

    def test_customer_rights_helper_short_circuits(self):
        """customer.quyen: chưa đăng nhập luôn KHÔNG, superuser luôn CÓ — hai nhánh không cần chạm DB."""
        from apps.pos import customer
        self.gia_quyen.stop()                            # test này gọi hàm THẬT, không dùng bản giả
        try:
            self.assertFalse(customer.duoc_xem(SimpleNamespace(is_authenticated=False, is_superuser=False)))
            self.assertFalse(customer.duoc_sua(SimpleNamespace(is_authenticated=False, is_superuser=True)))
            self.assertTrue(customer.duoc_xem(SimpleNamespace(is_authenticated=True, is_superuser=True)))
            self.assertTrue(customer.duoc_sua(SimpleNamespace(is_authenticated=True, is_superuser=True)))
            self.assertEqual(customer.MODULE, "KHACH_HANG")
        finally:
            self.gia_quyen.start()

    def test_draft_endpoint_and_apply_move_do_not_call_pmv(self):
        from apps.pos.qr_learning_views import save, save_draft
        with patch("apps.pmv.client.PmvClient.call", side_effect=AssertionError("No PMV writes")), \
             patch("apps.pmv.client.PmvClient.query", side_effect=AssertionError("No PMV reads")):
            response = save_draft(self.request({"raw": "Ngh)a", "scope": "ho_ten", "revision": 0}))
            payload = json.loads(response.content)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(len(payload["drafts"]), 1)
            response = save(self.request({"raw": "Ngh)a", "output": "Nghĩa", "scope": "ho_ten",
                                          "revision": payload["revision"], "draft_id": payload["draft_id"]}))
            payload = json.loads(response.content)
            self.assertEqual(payload["drafts"], [])
            self.assertEqual(len(payload["rules"]), 1)

    def test_translate_and_filter_are_authenticated_read_only_actions(self):
        from apps.pos.qr_learning_views import translate, filter_errors
        with patch("apps.pmv.client.PmvClient.call", side_effect=AssertionError("No PMV writes")), \
             patch("apps.pmv.client.PmvClient.query", side_effect=AssertionError("No PMV reads")):
            response = translate(self.request({"raw": "Kh≤m 4"}, admin=False))
            self.assertEqual(response.status_code, 200)
            self.assertEqual(json.loads(response.content)["output"], "Khóm 4")
            self.assertIn("no-store", response["Cache-Control"])
            response = filter_errors(self.request({"raw": "Khách Ngh)a", "output": "Khách Ngh)a"}, admin=False))
            self.assertEqual(response.status_code, 200)
            self.assertEqual(json.loads(response.content)["segments"][0]["raw"], "Ngh)a")
            self.assertFalse(self.path.exists())
        for view in (translate, filter_errors):
            request = self.request({"raw": "Kh≤m 4"})
            request.user.is_authenticated = False
            request.META["HTTP_HOST"] = "127.0.0.1"
            self.assertEqual(view(request).status_code, 302)
            self.assertEqual(view(self.request({"raw": []})).status_code, 400)

    def test_page_authentication_and_csrf(self):
        from django.test import Client, override_settings
        with override_settings(ALLOWED_HOSTS=["testserver"]):
            response = Client().get("/banle/khach-hang/qr/cong-cu/")
            self.assertEqual(response.status_code, 302)
            response = Client(enforce_csrf_checks=True).post("/banle/khach-hang/qr/hoc/ap-dung/", {})
            self.assertIn(response.status_code, (302, 403))
