"""Hồi quy lưu khách bằng dữ liệu giả; không truy cập PMV."""
from io import BytesIO
import json
import os
import unittest
from unittest.mock import Mock, patch

from PIL import Image

from apps.pmv.client import PmvProcError
from apps.pmv.gateway import _proc_sql
from apps.pos import customer


class CustomerValidationTests(unittest.TestCase):
    def test_binary_proc_markers_are_explicitly_typed(self):
        sql, values = _proc_sql("I_CUSTOMER_Upd", {
            "p_CustID": "CU_TEST", "p_ImageData": None,
            "p_ImageDataMatTruoc": b"front", "p_ImageDataMatSau": None,
        })
        self.assertEqual(sql.count("VARBINARY(MAX)"), 3)
        self.assertEqual(sql.count("SET @__bin"), 3)
        self.assertEqual(sql.count("=NULL"), 2)
        self.assertIn("@p_CustID=?", sql)
        self.assertIn("@p_ImageData=@__bin0", sql)
        self.assertEqual(values, [b"front", "CU_TEST"])

    def test_normalizes_scanner_text_and_phone(self):
        data, errors, _ = customer.clean_form({
            "CustName": "Trưíng Ngọc Giang", "Phone": "0960 880 906",
            "CMND": "096088009068", "Gender": "1",
        })
        self.assertEqual(errors, [])
        self.assertEqual(data["name"], "Trương Ngọc Giang")
        self.assertEqual(data["phone"], "0960880906")

    def test_rejects_bad_email_and_control_character(self):
        _, errors, _ = customer.clean_form({
            "CustName": "Khách\x00 thử", "Email": "khong-phai-email", "Gender": "0",
        })
        self.assertTrue(any("điều khiển" in x for x in errors))
        self.assertTrue(any("Email" in x for x in errors))

    def test_image_is_jpeg_resized_and_compressed(self):
        source = BytesIO()
        Image.new("RGB", (3200, 2400), (230, 210, 170)).save(source, "PNG")
        source.name = "cccd.png"
        source.size = len(source.getvalue())
        source.seek(0)
        images, info = customer.prepare_images({"anh_truoc": source})
        result = images["p_ImageDataMatTruoc"]
        self.assertTrue(result.startswith(b"\xff\xd8"))
        self.assertLessEqual(max(info[0]["width"], info[0]["height"]), 2000)
        self.assertLess(len(result), source.size)


class CustomerUpsertTests(unittest.TestCase):
    def _client(self):
        client = Mock()
        client.call.side_effect = [
            (0, [[{"CustID": "CU_TEST", "CustCode": "KH_TEST"}]]),
            (0, []),
            (0, [[{"CustID": "CU_TEST"}]]),
        ]
        return client

    @patch("apps.pos.customer._current")
    @patch("apps.pos.customer.duplicate_errors", return_value=[])
    def test_images_are_sent_only_after_successful_upsert(self, _duplicates, current):
        current.return_value = {"CustID": "CU_TEST", "CustCode": "KH_TEST",
                                "CustName": "Khách Thử", "Phone": "0900000001", "CMND": ""}
        data = {"cust_id": "", "name": "Khách Thử", "phone": "0900000001", "cmnd": "",
                "address": "", "birth": "", "gender": "1", "issued": "", "issued_by": "",
                "email": "", "notes": "", "cust_type": "", "active": "1"}
        client = self._client()
        result = customer.upsert(data, {"p_ImageData": b"jpg", "p_ImagePath": ".jpg"},
                                 client=client)
        self.assertTrue(result["created"])
        self.assertEqual([c.args[0] for c in client.call.call_args_list],
                         ["I_CUSTOMER_Ins", "I_DiemTichLuy_InsFromGT", "I_CUSTOMER_Upd"])
        self.assertIsNone(client.call.call_args_list[0].kwargs["p_ImageData"])
        self.assertEqual(client.call.call_args_list[2].kwargs["p_ImageData"], b"jpg")

    @patch("apps.pos.customer._current", return_value=None)
    @patch("apps.pos.customer.duplicate_errors", return_value=[])
    def test_failed_upsert_never_calls_image_update(self, _duplicates, _current):
        data = {"cust_id": "", "name": "Khách Thử", "phone": "0900000001", "cmnd": "",
                "address": "", "birth": "", "gender": "1", "issued": "", "issued_by": "",
                "email": "", "notes": "", "cust_type": "", "active": "1"}
        client = Mock()
        client.call.side_effect = PmvProcError("I_CUSTOMER_Ins", -1,
                                               [[{"ErrorDesc": "Trùng số điện thoại"}]])
        with self.assertRaises(customer.CustomerSaveError):
            customer.upsert(data, {"p_ImageData": b"jpg", "p_ImagePath": ".jpg"}, client=client)
        self.assertEqual(client.call.call_count, 1)

    def test_success_header_keeps_vietnamese_json_valid(self):
        os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.prod")
        import django
        django.setup()
        from django.test import RequestFactory
        from apps.pos import views

        post = {"CustName": "Trương Ngọc Giang", "Phone": "0960880906", "Gender": "1",
                "save_token": "a" * 24}
        saved = {"cust_id": "CU_TEST", "cust_code": "KH_TEST", "name": "Trương Ngọc Giang",
                 "created": True, "warnings": []}
        with patch("apps.pos.views._phien", return_value={"shop_id": ""}), \
             patch("apps.pos.views.C.previous_save", return_value=None), \
             patch("apps.pos.views.C.upsert", return_value=saved), \
             patch("apps.pos.views.C.remember_save"):
            response = views.khach_luu(RequestFactory().post("/banle/khach-hang/luu/", post))
        self.assertEqual(response.status_code, 204)
        header = response["HX-Trigger"]
        header.encode("ascii")
        self.assertIn("Trương Ngọc Giang", json.loads(header)["khachSaved"]["message"])


if __name__ == "__main__":
    unittest.main()
