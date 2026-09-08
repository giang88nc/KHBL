from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.pmv.models import PmvUser, PmvWebUser, UserModuleAccess, pmv_user_for_web_user
from apps.pos import passcode as PC


class UserAdministrationTests(TestCase):
    def setUp(self):
        self.User = get_user_model()
        self.admin = self.User.objects.create_superuser("root-admin", "root@example.com", "secret123")
        self.staff = self.User.objects.create_user("staff", password="secret123")

    def test_only_superuser_can_open_user_administration(self):
        self.client.force_login(self.staff)
        response = self.client.get(reverse("pmv:user_list"))
        self.assertEqual(response.status_code, 403)

    def test_user_pages_render_for_superuser(self):
        self.client.force_login(self.admin)
        listing = self.client.get(reverse("pmv:user_list"))
        create = self.client.get(reverse("pmv:user_create"))
        self.assertContains(listing, "Người dùng & quyền")
        self.assertContains(create, "Quyền theo danh mục")

    def test_create_user_upserts_module_permissions(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse("pmv:user_create"), {
            "username": "nguyen",
            "first_name": "Nguyện",
            "last_name": "Bùi Thị",
            "email": "nguyen@example.com",
            "password": "123456",
            "password_confirm": "123456",
            "is_active": "on",
            "right_BAN_HANG_edit": "on",  # thao tác tự bật cả quyền xem
            "right_HOA_DON_view": "on",
            "right_HOA_DON_approve": "on",
        })
        self.assertRedirects(response, reverse("pmv:user_list"))
        account = self.User.objects.get(username="nguyen")
        self.assertTrue(account.check_password("123456"))
        sale = UserModuleAccess.objects.get(user=account, module=UserModuleAccess.Module.BAN_HANG)
        self.assertTrue(sale.can_view)
        self.assertTrue(sale.can_edit)
        bill = UserModuleAccess.objects.get(user=account, module=UserModuleAccess.Module.HOA_DON)
        self.assertTrue(bill.can_view)
        self.assertTrue(bill.can_approve)

        # Lưu lại cùng danh mục phải cập nhật đúng dòng cũ, không tạo bản ghi trùng.
        response = self.client.post(reverse("pmv:user_edit", args=[account.pk]), {
            "username": "nguyen",
            "first_name": "Nguyện",
            "last_name": "Bùi Thị",
            "email": "nguyen@example.com",
            "is_active": "on",
            "right_BAN_HANG_view": "on",
        })
        self.assertRedirects(response, reverse("pmv:user_list"))
        self.assertEqual(UserModuleAccess.objects.filter(user=account).count(), len(UserModuleAccess.Module.choices))
        sale.refresh_from_db()
        self.assertTrue(sale.can_view)
        self.assertFalse(sale.can_edit)

    def test_cannot_delete_current_or_only_active_superuser(self):
        self.client.force_login(self.admin)
        current = self.client.post(reverse("pmv:user_delete", args=[self.admin.pk]))
        self.assertRedirects(current, reverse("pmv:user_list"))
        self.assertTrue(self.User.objects.filter(pk=self.admin.pk).exists())

    def test_user_form_upserts_visible_text_passcode_as_a_hash(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse("pmv:user_create"), {
            "username": "passcode-user", "password": "123456", "password_confirm": "123456",
            "is_active": "on", "unlock_passcode": "2468",
        })
        self.assertRedirects(response, reverse("pmv:user_list"))
        account = self.User.objects.get(username="passcode-user")
        self.assertTrue(PC.kiem(account, "2468"))
        self.assertNotIn("2468", PC.lay_hash(account))

    def test_multiple_web_users_can_share_one_pmv_profile(self):
        self.client.force_login(self.admin)
        pmv = PmvUser.objects.create(
            user_id="US-SHARED", user_name="kk-shared", full_name="Két chung",
            emp_id="EMP-SHARED", till_id="TILL-SHARED", till_code="KET-01",
        )
        for username in ("web-a", "web-b"):
            response = self.client.post(reverse("pmv:user_create"), {
                "username": username, "password": "123456", "password_confirm": "123456",
                "is_active": "on", "pmv_user_id": pmv.pk,
            })
            self.assertRedirects(response, reverse("pmv:user_list"))
        web_a = self.User.objects.get(username="web-a")
        web_b = self.User.objects.get(username="web-b")
        self.assertEqual(PmvWebUser.objects.filter(pmv_user=pmv).count(), 2)
        self.assertEqual(pmv_user_for_web_user(web_a).pk, pmv.pk)
        self.assertEqual(pmv_user_for_web_user(web_b).pk, pmv.pk)
