"""Quyền thao tác hóa đơn theo ngày: nhân viên chỉ hôm nay, Admin toàn quyền."""
import datetime
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase

from apps.pos import views


class HoaDonAccessTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.yesterday = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
        self.header = {
            "TrnID": "TBG_TEST", "BillCode": "TEST", "TrnDate": self.yesterday,
            "Status": "C", "IsDel": "0",
        }

    def request(self, is_superuser, action="thanh_toan"):
        request = self.factory.get("/banle/hoa-don/xac-nhan/", {
            "trn_id": "TBG_TEST", "loai": "THAU", "action": action,
        })
        request.user = get_user_model().objects.create(
            username="admin" if is_superuser else "staff", is_superuser=is_superuser,
        )
        return request

    def test_staff_can_only_view_another_day(self):
        request = self.request(False)
        with patch("apps.pos.views._doc_hd_kk", return_value=(None, self.header, "TRN_RT_BUYGOLD")):
            response = views.hoa_don_xac_nhan(request)
        self.assertEqual(response.status_code, 409)
        self.assertIn("ngoài ngày hôm nay", response.content.decode())

    def test_admin_can_confirm_another_day(self):
        request = self.request(True)
        with patch("apps.pos.views._doc_hd_kk", return_value=(None, self.header, "TRN_RT_BUYGOLD")):
            response = views.hoa_don_xac_nhan(request)
        self.assertEqual(response.status_code, 200)
        self.assertIn("Hủy thanh toán", response.content.decode())

    def test_only_admin_can_confirm_direct_cancel_for_closed_bill(self):
        staff = self.request(False, "hoa_don")
        with patch("apps.pos.views._doc_hd_kk", return_value=(None, self.header, "TRN_RT_BUYGOLD")):
            self.assertEqual(views.hoa_don_xac_nhan(staff).status_code, 409)
        admin = self.request(True, "hoa_don")
        with patch("apps.pos.views._doc_hd_kk", return_value=(None, self.header, "TRN_RT_BUYGOLD")):
            response = views.hoa_don_xac_nhan(admin)
        self.assertEqual(response.status_code, 200)
        self.assertIn("Hủy hóa đơn", response.content.decode())

    def test_admin_direct_cancel_reopens_then_deletes_sale_after_passcode(self):
        request = self.factory.post("/banle/hoa-don/huy/", {
            "trn_id": "TBG_TEST", "loai": "BAN", "action": "hoa_don", "passcode": "1234",
        })
        request.user = get_user_model().objects.create(username="admin-write", is_superuser=True)
        with patch("apps.pos.views._passcode_dung", return_value=True), \
             patch("apps.pos.views._doc_hd_kk", return_value=(None, self.header, "TRN_RT_BUYSELL")), \
             patch("apps.pos.views._phien", return_value={"user_id": "U_ADMIN"}), \
             patch("apps.pos.views.B.mo_lai") as reopen, \
             patch("apps.pos.views.B.huy") as delete:
            response = views.hoa_don_huy(request)
        self.assertEqual(response.status_code, 204)
        reopen.assert_called_once()
        delete.assert_called_once()

    def test_wrong_passcode_returns_the_alert_inside_modal(self):
        request = self.factory.post("/banle/hoa-don/huy/", {
            "trn_id": "TBG_TEST", "loai": "THAU", "action": "thanh_toan",
            "bill_code": "26-09-07-000203", "passcode": "sai",
        })
        request.user = get_user_model().objects.create(username="staff-passcode")
        with patch("apps.pos.views._passcode_dung", return_value=False):
            response = views.hoa_don_huy(request)
        self.assertEqual(response.status_code, 200)
        self.assertIn("Passcode không đúng", response.content.decode())
        self.assertIn("26-09-07-000203", response.content.decode())
