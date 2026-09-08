"""
Đồng bộ 2 tài khoản app KK (admin, kimhanh2 — GĐ chốt 03/09/2026) từ PMV SYS_USERS
sang MySQL bảng sys_users (PmvUser) + tạo/cập nhật user Django để đăng nhập web.

- Cột chép: UserID, UserName, FirstName, LastName, FullName, IsAdmin, Active, ShopID, EmpID
  + TillID/TillCode (két của user, tra T_TILL.OpenUserID — proc bán cần).
- Mật khẩu: đọc từ app CHỈ để băm (Django make_password) — không in, không lưu plain.
  Lần đầu tạo user Django mới đặt mật khẩu; lần sau giữ nguyên (trừ --reset-password).
- 'admin' → superuser web; user khác → staff thường (ma trận quyền web làm sau).

Chạy: manage.py sync_pmv_users [--usernames admin kimhanh2] [--reset-password]
"""
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from apps.pmv.gateway import pmv_read
from apps.pmv.models import PmvUser, PmvWebUser


class Command(BaseCommand):
    help = "Đồng bộ tài khoản app KK (SYS_USERS) → MySQL sys_users + user Django"

    def add_arguments(self, parser):
        parser.add_argument("--usernames", nargs="+", default=["admin", "kimhanh2"])
        parser.add_argument("--reset-password", action="store_true",
                            help="Đặt lại mật khẩu web (mặc định chỉ đặt khi tạo mới)")
        parser.add_argument("--password", default="123456",
                            help="Mật khẩu web khi tạo mới / --reset-password (GĐ chốt 03/09: mặc định 123456, KHÔNG lấy từ app)")

    def handle(self, *args, **opts):
        names = opts["usernames"]
        marks = ",".join("?" * len(names))
        rows = pmv_read(
            "SELECT u.UserID, u.UserName, u.FirstName, u.LastName, u.FullName, "
            "u.IsAdmin, u.Active, u.ShopID, u.EmpID, t.TillID, t.TillCode "
            "FROM SYS_USERS u WITH (NOLOCK) LEFT JOIN T_TILL t WITH (NOLOCK) ON t.OpenUserID = u.UserID AND t.Active = '1' "
            f"WHERE u.UserName IN ({marks})",
            tuple(names), tag="sync_pmv_users", audit=False,
        )
        if not rows:
            raise CommandError(f"Không thấy user nào trong SYS_USERS: {names}")
        User = get_user_model()
        for r in rows:
            uname = r["UserName"].strip()
            dj, created = User.objects.get_or_create(username=uname, defaults={
                "first_name": (r["FullName"] or "")[:150], "is_staff": True,
                "is_superuser": uname == "admin", "is_active": (r["Active"] or "1") == "1",
            })
            if created or opts["reset_password"]:
                dj.set_password(opts["password"])
            dj.first_name = (r["FullName"] or "")[:150]
            dj.is_active = (r["Active"] or "1") == "1"
            dj.is_staff = True
            dj.is_superuser = dj.is_superuser or uname == "admin"
            dj.save()
            pmv_user, _ = PmvUser.objects.update_or_create(user_id=r["UserID"], defaults={
                "user_name": uname, "password": dj.password,
                "first_name": r["FirstName"] or "", "last_name": r["LastName"] or "",
                "full_name": r["FullName"] or "", "is_admin": r["IsAdmin"] or "0",
                "active": r["Active"] or "1", "shop_id": r["ShopID"] or "", "emp_id": r["EmpID"] or "",
                "till_id": r["TillID"] or "", "till_code": r["TillCode"] or "", "django_user": dj,
            })
            PmvWebUser.objects.update_or_create(web_user=dj, defaults={"pmv_user": pmv_user})
            self.stdout.write(
                f"  {uname:<10} UserID={r['UserID']} EmpID={r['EmpID']} Till={r['TillID']} ({r['TillCode']}) "
                f"→ web user {'TẠO MỚI' if created else 'cập nhật'}{' + đặt lại mật khẩu' if (created or opts['reset_password']) else ''}"
            )
        self.stdout.write(self.style.SUCCESS(f"Đồng bộ {len(rows)} tài khoản xong."))
