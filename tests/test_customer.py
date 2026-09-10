"""Hồi quy lưu khách bằng dữ liệu giả; không truy cập PMV."""
from io import BytesIO
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from PIL import Image

from apps.pmv.client import PmvProcError
from apps.pmv.gateway import PmvBlocked, _proc_sql, pmv_image_read
from apps.pos import customer


class CustomerValidationTests(unittest.TestCase):
    def test_image_reader_rejects_paths_outside_pmv_folder(self):
        with self.assertRaises(PmvBlocked):
            pmv_image_read(r"D:\KHJ_PMV_BACKUP\secret.bak", tag="test", target="sandbox")

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

    def test_normalizes_partially_repaired_address(self):
        data, errors, warnings = customer.clean_form({
            "CustName": "Trương Ngọc Giang", "Gender": "1",
            "Address": "Khóm 4, TT. NÄm CÄn, Năm Căn, Cà Mau",
        })
        self.assertEqual(errors, [])
        self.assertEqual(data["address"], "Khóm 4, TT. Năm Căn, Năm Căn, Cà Mau")
        self.assertTrue(warnings)

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
        saved = {"CustID": "CU_TEST", "CustCode": "KH_TEST",
                 "CustName": "Khách Thử", "Phone": "0900000001", "CMND": ""}
        current.side_effect = [saved, {**saved, "ImagePath": r"D:\PHANMEMVANG\HINHANHKH\Khach_Thu.jpg"}]
        data = {"cust_id": "", "name": "Khách Thử", "phone": "0900000001", "cmnd": "",
                "address": "", "birth": "", "gender": "1", "issued": "", "issued_by": "",
                "email": "", "notes": "", "cust_type": "", "active": "1"}
        client = self._client()
        client.query.side_effect = [[], [{"CustID": "CU_TEST"}]]
        jpeg = BytesIO(); Image.new("RGB", (20, 20), "white").save(jpeg, "JPEG")
        client.image_file.return_value = jpeg.getvalue()
        with tempfile.TemporaryDirectory() as temp, \
             patch("apps.pos.customer._archive_root", return_value=Path(temp)):
            result = customer.upsert(data, {"p_ImageData": jpeg.getvalue(), "p_ImagePath": ".jpg"},
                                     client=client)
        self.assertTrue(result["created"])
        self.assertTrue(result["complete"])
        self.assertEqual([c.args[0] for c in client.call.call_args_list],
                         ["I_CUSTOMER_Ins", "I_DiemTichLuy_InsFromGT", "I_CUSTOMER_Upd"])
        self.assertIsNone(client.call.call_args_list[0].kwargs["p_ImageData"])
        self.assertEqual(client.call.call_args_list[2].kwargs["p_ImageData"], jpeg.getvalue())

    @patch("apps.pos.customer._current")
    def test_archive_uses_customer_name_and_read_first(self, current):
        jpeg = BytesIO(); Image.new("RGB", (20, 20), "white").save(jpeg, "JPEG")
        pmv_path = r"D:\PHANMEMVANG\HINHANHKH\Khach_Thu_MT.jpg"
        current.return_value = {"CustID": "CU_TEST", "CustName": "Nguyễn Văn A",
                                "ImagePathMatTruoc": pmv_path}
        client = Mock(target="kk")
        client.image_file.return_value = jpeg.getvalue()
        with tempfile.TemporaryDirectory() as temp, \
             patch("apps.pos.customer._archive_root", return_value=Path(temp)):
            customer.archive_images({"p_ImageDataMatTruoc": jpeg.getvalue()}, current.return_value, client)
            data, content_type = customer.saved_image("CU_TEST", "mat-truoc", client=client)
            self.assertTrue(data.startswith(b"\xff\xd8"))
            self.assertEqual(content_type, "image/jpeg")
            self.assertTrue((Path(temp) / "CU_TEST_nguyen_van_a_MT.jpg").is_file())

    @patch("apps.pos.customer._current")
    def test_legacy_pmv_image_is_cached_on_first_read(self, current):
        jpeg = BytesIO(); Image.new("RGB", (20, 20), "white").save(jpeg, "JPEG")
        current.return_value = {"CustID": "CU_TEST", "CustName": "Khách Cũ",
                                "ImagePathMatTruoc": r"D:\\PHANMEMVANG\\HINHANHKH\\legacy.jpg"}
        client = Mock(target="kk")
        client.image_file.return_value = jpeg.getvalue()
        with tempfile.TemporaryDirectory() as temp, \
             patch("apps.pos.customer._archive_root", return_value=Path(temp)):
            self.assertTrue(customer.saved_image("CU_TEST", "mat-truoc", client=client)[0].startswith(b"\xff\xd8"))
            self.assertTrue((Path(temp) / "CU_TEST_khach_cu_MT.jpg").is_file())
            cached_client = Mock(target="kk")
            self.assertTrue(customer.saved_image("CU_TEST", "mat-truoc", client=cached_client)[0].startswith(b"\xff\xd8"))
            cached_client.query.assert_not_called()

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

    def test_partial_image_failure_keeps_popup_open_for_retry(self):
        os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.prod")
        import django
        django.setup()
        from django.test import RequestFactory
        from apps.pos import views

        post = {"CustName": "Khách Thử", "Phone": "0900000001", "Gender": "1",
                "save_token": "b" * 24}
        partial = {"cust_id": "CU_TEST", "cust_code": "KH_TEST", "name": "Khách Thử",
                   "created": True, "warnings": [], "complete": False,
                   "errors": ["CCCD mặt trước: tệp không tồn tại"]}
        with patch("apps.pos.views._phien", return_value={"shop_id": ""}), \
             patch("apps.pos.views.C.previous_save", return_value=None), \
             patch("apps.pos.views.C.upsert", return_value=partial), \
             patch("apps.pos.views.C.remember_save") as remember:
            response = views.khach_luu(RequestFactory().post("/banle/khach-hang/luu/", post))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("HX-Trigger", response)
        self.assertIn(b"CU_TEST", response.content)
        self.assertIn("tệp không tồn tại", response.content.decode("utf-8"))
        remember.assert_not_called()


if __name__ == "__main__":
    unittest.main()
