"""Hồi quy QR dùng dữ liệu giả, không truy cập CSDL. python -m unittest discover -s tests"""
import json
import os
from pathlib import Path
import unittest

from apps.pos import cccd
from apps.pos.vn_text import chuan_hoa


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
