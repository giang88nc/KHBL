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


class LearningEndpointTests(_LearningCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.prod")
        import django
        django.setup()

    def request(self, data, admin=True):
        from django.test import RequestFactory
        request = RequestFactory().post("/banle/khach-hang/qr/hoc/ap-dung/", data, content_type="application/json")
        request.user = SimpleNamespace(is_authenticated=True, is_superuser=admin, get_username=lambda: "tester")
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

    def test_save_requires_admin_and_bad_payload_does_not_write(self):
        from apps.pos.qr_learning_views import save
        body = {"raw": "Ngh)a", "output": "Nghĩa", "revision": 0}
        self.assertEqual(save(self.request(body, admin=False)).status_code, 403)
        self.assertFalse(self.path.exists())
        self.assertEqual(save(self.request([])).status_code, 400)
        self.assertFalse(self.path.exists())

    def test_page_authentication_and_csrf(self):
        from django.test import Client, override_settings
        with override_settings(ALLOWED_HOSTS=["testserver"]):
            response = Client().get("/banle/khach-hang/qr/cong-cu/")
            self.assertEqual(response.status_code, 302)
            response = Client(enforce_csrf_checks=True).post("/banle/khach-hang/qr/hoc/ap-dung/", {})
            self.assertIn(response.status_code, (302, 403))
