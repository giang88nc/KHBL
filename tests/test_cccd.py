"""Hồi quy QR dùng dữ liệu giả, không truy cập CSDL. python -m unittest discover -s tests"""
import json
import os
from pathlib import Path
import unittest

from apps.pos import cccd
from apps.pos import cccd_context
from apps.pos.vn_text import chuan_hoa


class QRProfileTests(unittest.TestCase):
    def raw(self, name="Nguyễn Văn A", address="Đường 55, B1", gender="Nam"):
        return f"000000000001||{name}|01012000|{gender}|{address}|01012024"

    def test_byte_pipe_and_backslash_before_parsing(self):
        raw = self.raw("Nguyá»\\n Thá»‹ Kiá»|u", "Ä\x90Æ°á»\x9dng A", "Ná»¯") + "||||"
        result = cccd.phan_tich(raw)
        self.assertEqual(result["loi"], [])
        self.assertEqual(result["data"]["ho_ten"], "Nguyễn Thị Kiều")
        self.assertEqual(result["data"]["dia_chi"], "Đường A")
        self.assertEqual(result["raw_scans"], [raw])
        self.assertNotEqual(result["status"], "AUTO_ACCEPT")

    def test_mixed_c1_cp1252_and_nbsp_byte(self):
        result = cccd.phan_tich(self.raw("Tráº§n ThÃ\u00a0nh", "Khóm A, Ä\x90Æ°á»\x9dng B"))
        self.assertEqual(result["data"]["ho_ten"], "Trần Thành")
        self.assertEqual(result["data"]["dia_chi"], "Khóm A, Đường B")

    def test_oem_collisions_are_alternatives(self):
        result = cccd.phan_tich(self.raw("Hí", "S≤c"))
        values = {c["value"] for c in result["suggestions"]["ho_ten"]}
        self.assertTrue({"Hạ", "Hơ"}.issubset(values))
        self.assertEqual(result["status"], "MANUAL_REVIEW")

    def test_group_uses_independent_decoding_to_resolve_collision(self):
        result = cccd.normalize_cccd_group([self.raw("Háº¡", "SÃ³c"), self.raw("Hí", "S≤c")])
        self.assertEqual(result["data"]["ho_ten"], "Hạ")
        self.assertEqual(result["field_provenance"]["ho_ten"]["scans"], [1, 2])

    def test_conflicting_ids_or_dates_cannot_merge(self):
        for other in (self.raw().replace("000000000001", "000000000002"),
                      self.raw().replace("01012000", "02012000")):
            result = cccd.normalize_cccd_group([self.raw(), other])
            self.assertIsNone(result["data"])
            self.assertEqual(result["status"], "MANUAL_REVIEW")

    def test_repeat_scan_does_not_inflate_evidence(self):
        raw = self.raw("Nguy7877n V259n A")
        self.assertEqual(cccd.normalize_cccd_group([raw]), cccd.normalize_cccd_group([raw, raw]))

    def test_context_does_not_overwrite_card(self):
        result = cccd.normalize_cccd_group([self.raw("Lý Thơi")],
                    trusted_context={"cmnd": "000000000001", "ho_ten": "Lý Thới"})
        self.assertEqual(result["data"]["ho_ten"], "Lý Thơi")
        self.assertEqual(result["status"], "MANUAL_REVIEW")

    def test_address_punctuation_and_numbers_preserved(self):
        result = cccd.phan_tich(self.raw(address="115/21 Phương, Sài, B1, KP19, , TP.Nha Trang"))
        self.assertEqual(result["data"]["dia_chi"], "115/21 Phương, Sài, B1, KP19, TP. Nha Trang")

    def test_decimal_does_not_decode_new_unicode_again(self):
        result = cccd.phan_tich(self.raw("Nguy7877n Th7883 A", "Kh243m 4, N♥m C259n"))
        self.assertEqual(result["data"]["dia_chi"], "Khóm 4, Năm Căn")

    def test_house_numbers_that_match_unicode_are_preserved(self):
        result = cccd.phan_tich(self.raw("Nguy7877n A", "272A, B272, KP7889, 115/21"))
        self.assertEqual(result["data"]["dia_chi"], "272A, B272, KP7889, 115/21")

    def test_bounded_input_and_html(self):
        for scans in ([], [self.raw()] * 6, [None], ["A" * 8193], [self.raw("<img>")]):
            self.assertIsNone(cccd.normalize_cccd_group(scans)["data"])


class QRAdaptiveTests(unittest.TestCase):
    def raw(self, name="Nguyễn Văn A", address="Đường 55, B1", old=""):
        return f"000000000001|{old}|{name}|01012000|Nam|{address}|01012024"

    def analyze(self, raw, **kwargs):
        return cccd.normalize_cccd_group([raw], context_model=kwargs.pop("context_model", {}), **kwargs)

    def test_variable_delimiters_follow_date_and_identity_anchors(self):
        base = self.raw()
        variants = [base.replace("||", "|", 1), base.replace("|", "|||"),
                    base.replace("|", ""), base + "|||device-3|extension",
                    base.replace("A|0101", "A0101").replace("Nam|Đường", "NamĐường"),
                    self.raw(old="123456789").replace("|", "")]
        for raw in variants:
            with self.subTest(raw=raw):
                result = self.analyze(raw)
                self.assertEqual(result["loi"], [])
                self.assertEqual(result["data"]["ho_ten"], "Nguyễn Văn A")
                self.assertEqual(result["data"]["dia_chi"], "Đường 55, B1")
                self.assertEqual(result["data"]["ngay_sinh"], "2000-01-01")
                self.assertEqual(result["data"]["ngay_cap"], "2024-01-01")
                self.assertEqual(result["raw_scans"], [raw])
                self.assertTrue(result["canh_bao"])

    def test_extension_fields_stay_separate(self):
        result = self.analyze(self.raw() + "|device|value||")
        self.assertEqual(result["extra_fields"], [{"scan": 1, "values": ["device", "value"]}])
        self.assertEqual(result["data"]["dia_chi"], "Đường 55, B1")

    def test_ambiguous_dates_and_invalid_identifiers_do_not_shift_fields(self):
        for raw in (self.raw() + "|01012025", self.raw().replace("||", "|123|", 1),
                    self.raw().replace("01012000", "31022000"),
                    self.raw().replace("|01012024", "")):
            self.assertIsNone(self.analyze(raw)["data"])

    def test_long_oem_address_does_not_exhaust_candidates_before_last_word(self):
        raw = self.raw("Thúo A", ", ".join(["Thính"] * 12 + ["M∙", "H╙"]))
        model = {"lexicon": {"thạnh": 1, "mỹ": 1, "hồ": 1, "thảo": 1}}
        result = self.analyze(raw, context_model=model)
        self.assertEqual(result["data"]["ho_ten"], "Thảo A")
        self.assertEqual(result["data"]["dia_chi"], ", ".join(["Thạnh"] * 12 + ["Mỹ", "Hồ"]))
        self.assertEqual(result["status"], "MANUAL_REVIEW")

    def test_decoder_keeps_intact_unicode_and_unknown_names(self):
        raw = self.raw("Lý Thơi", "Bản Lòn, 272A, B272")
        model = {"lexicon": {"thới": 100, "lớn": 100}}
        result = self.analyze(raw, context_model=model)
        self.assertEqual(result["data"]["ho_ten"], "Lý Thơi")
        self.assertEqual(result["data"]["dia_chi"], "Bản Lòn, 272A, B272")

    def test_approved_example_requires_all_raw_fields_not_just_identity(self):
        raw = self.raw("LÃ½ ThÆ¡i")
        parts, _, errors = cccd.split_raw(raw)
        self.assertFalse(errors)
        gold = self.raw("Lý Thới").split("|")
        model = cccd_context.build_model([(1, parts, gold)], "synthetic")
        result = self.analyze(raw, context_model=model)
        self.assertEqual(result["data"]["ho_ten"], "Lý Thới")
        self.assertEqual(result["approved_examples"], [{"scan": 1, "row": 1}])
        for changed in (raw.replace("000000000001", "000000000002"),
                        raw.replace("Đường 55", "Đường 56"), raw.replace("01012000", "02012000")):
            result = self.analyze(changed, context_model=model)
            self.assertEqual(result["approved_examples"], [])
            self.assertEqual(result["data"]["ho_ten"], "Lý Thơi")
        result = self.analyze(raw, context_model=model, use_approved=False)
        self.assertEqual(result["data"]["ho_ten"], "Lý Thơi")

    def test_conflicting_approved_answers_are_rejected_at_import(self):
        parts = self.raw().split("|")
        other = self.raw("Nguyễn Văn B").split("|")
        with self.assertRaises(ValueError):
            cccd_context.build_model([(1, parts, parts), (2, parts, other)], "synthetic")

    def test_decoding_is_idempotent_and_preserves_numbers(self):
        first = self.analyze(self.raw("Nguy7877n A", "15/3b, KP7889, 272A, Kh243m 4"))
        second = self.analyze(first["normalized_qr"])
        self.assertEqual(first["normalized_qr"], second["normalized_qr"])
        self.assertEqual(first["data"]["dia_chi"], "15/3B, KP7889, 272A, Khóm 4")

    def test_bad_model_is_visible_and_never_blocks_basic_decoder(self):
        result = self.analyze(self.raw(), context_model={"load_error": True})
        self.assertIsNotNone(result["data"])
        self.assertEqual(result["status"], "MANUAL_REVIEW")

    def test_utf8_bytes_are_not_misclassified_as_oem_suggestions(self):
        result = self.analyze(self.raw("LÃ½ An", "TÃ¢n HÃ²a"))
        self.assertNotIn("oem", result["profiles"][0]["types"])
        for key in ("ho_ten", "dia_chi"):
            self.assertTrue(all(not cccd._damage(c["value"]) for c in result["suggestions"][key]))


class QRParserTests(unittest.TestCase):
    def test_shared_cases(self):
        cases = json.loads(Path(__file__).with_name("qr_cases.json").read_text(encoding="utf-8"))
        for case in cases:
            with self.subTest(case=case["name"]):
                result = cccd.phan_tich(case["raw"])
                if case.get("invalid"):
                    self.assertIsNone(result["data"])
                    self.assertTrue(result["loi"])
                else:
                    self.assertEqual(result["loi"], [])
                    for key, value in case["expect"].items():
                        self.assertEqual(result["data"][key], value)
                    self.assertEqual(bool(result["canh_bao"]), case["warning"])

    def test_number_runs_are_not_text(self):
        for value in ("012345678901", "01022000", "Số 0123", "Số 0198", "Số 022501870141", "0198A", "Giá trị"):
            with self.subTest(value=value):
                self.assertEqual(chuan_hoa(value), (value, []))

    def test_oversized_scan(self):
        self.assertIsNone(cccd.parse("0" * 8193))


class QRFormTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.prod")
        import django
        django.setup()

    def test_server_validation_without_database(self):
        from apps.pos.customer import clean_form
        base = {"CustName": "Khách thử", "CMND": "012345678901", "Gender": "1",
                "BirthDate": "2000-02-01", "NgayCap": "2023-09-15"}
        for changes, error in (({}, False), ({"Gender": "unknown"}, True),
                               ({"CMND": "0123x45678901"}, True),
                               ({"BirthDate": "1988-02-31"}, True),
                               ({"NgayCap": "1990-01-01"}, True)):
            with self.subTest(changes=changes):
                _, errors, _ = clean_form(base | changes)
                self.assertEqual(bool(errors), error)

    def test_analysis_endpoint_is_pure_and_does_not_cache_raw(self):
        from django.test import RequestFactory
        from apps.pos.views import khach_qr_phan_tich
        from unittest.mock import patch
        raw = QRProfileTests().raw("Nguyá»\\n A")
        with patch("apps.pmv.client.PmvClient.query", side_effect=AssertionError("No PMV reads")), \
             patch("apps.pmv.client.PmvClient.call", side_effect=AssertionError("No PMV writes")):
            request = RequestFactory().post("/banle/khach-hang/qr/phan-tich/",
                                           {"scans": [raw]}, content_type="application/json")
            response = khach_qr_phan_tich(request)
        self.assertEqual(response.status_code, 200)
        self.assertIn("no-store", response["Cache-Control"])
        self.assertEqual(json.loads(response.content)["data"]["ho_ten"], "Nguyễn A")

    def test_analysis_endpoint_requires_login(self):
        from django.test import Client, override_settings
        with override_settings(ALLOWED_HOSTS=["testserver"]):
            response = Client().post("/banle/khach-hang/qr/phan-tich/",
                                     {"scans": [QRProfileTests().raw()]}, content_type="application/json")
        self.assertEqual(response.status_code, 302)
        self.assertIn("dang-nhap", response["Location"])

    def test_template_loads_parser_and_qr_controls(self):
        from django.template.loader import render_to_string
        from django.conf import settings
        html = render_to_string("pos/_khach_form.html", {"loai_ds": [], "k": None,
                                                         "save_token": "a" * 24})
        self.assertIn('id="kh-qr-preview"', html)
        self.assertIn('id="kh-qr-discard"', html)
        self.assertIn('value="" selected', html)
        self.assertEqual(html.count("data-chup"), 3)
        self.assertEqual(html.count("data-photo-input"), 3)
        self.assertIn('id="kh-camera"', html)
        self.assertIn("data-camera-retake", html)
        self.assertIn("data-camera-use", html)
        self.assertIn('name="save_token"', html)
        self.assertIn('hx-sync="this:drop"', html)
        self.assertIn('id="kh-save-btn"', html)
        base = (settings.BASE_DIR / "templates/base.html").read_text(encoding="utf-8")
        self.assertLess(base.index("js/vn_text.js"), base.index("js/cccd.js"))
        self.assertLess(base.index("js/cccd.js"), base.index("js/khbl.js"))


if __name__ == "__main__":
    unittest.main()
