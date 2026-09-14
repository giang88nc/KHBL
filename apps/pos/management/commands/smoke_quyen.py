# -*- coding: utf-8 -*-
"""Bộ kiểm QUYỀN THEO DANH MỤC (GĐ chốt 13/09/2026):

    manage.py smoke_quyen

GĐ chốt hai luật sau sự cố máy quầy ăn 403: hệ thống làm ĐÚNG ma trận đã gán, và mục nào tick XEM
mới hiện trên thanh menu. Bài kiểm dựng một tài khoản thử rồi bật/tắt từng danh mục:

  · không tick XEM → mục BIẾN MẤT khỏi menu và trang đích trả 403 có hướng dẫn xin quyền;
  · tick XEM      → mục hiện lại và trang mở được ngay, không cần đăng nhập lại;
  · superuser luôn thấy đủ; chưa đăng nhập thì menu trống.

Tài khoản thử tạo rồi XÓA trong cùng lượt chạy, không đụng tài khoản thật của tiệm.
"""
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.test import Client

from apps.pmv.models import UserModuleAccess
from apps.pos import quyen as Q

TRANG = {                                   # danh mục → URL trang đích của nó
    "DASHBOARD": "/banle/", "BAN_HANG": "/banle/ban-hang/", "THAU_VAO": "/banle/thau-vao/",
    "DAT_COC": "/banle/dat-coc/", "BANG_GIA": "/banle/bang-gia/", "KHACH_HANG": "/banle/khach-hang/",
    "HOA_DON": "/banle/hoa-don/", "BAO_CAO": "/banle/bao-cao/", "CHUYEN_KHOAN": "/banle/chuyen-khoan/",
    "HE_THONG": "/he-thong/",
}


class Command(BaseCommand):
    help = "Smoke quyền theo danh mục: menu chỉ hiện mục được tick XEM, trang đích chặn đúng"

    def handle(self, *args, **o):
        self.loi = []
        U = get_user_model()
        ten = "smoke-quyen-tam"
        U.objects.filter(username=ten).delete()
        u = U.objects.create_user(ten, password=U.objects.make_random_password()
                                  if hasattr(U.objects, "make_random_password") else "x8Kd93jfQ2mZ")
        web = Client(SERVER_NAME="localhost")       # ALLOWED_HOSTS không có "testserver"
        web.force_login(u)
        try:
            self.ok("mọi mục menu đều có danh mục trong ma trận",
                    set(Q.MUC) == set(TRANG) and set(Q.MUC) <= {m for m, _ in UserModuleAccess.Module.choices},
                    str(set(Q.MUC) ^ set(TRANG)))
            b = web.get("/banle/hoa-don/")
            self.ok("tài khoản chưa có quyền nào → trang đích trả 403 kèm hướng dẫn xin quyền",
                    b.status_code == 403 and "HỆ THỐNG → Người dùng" in b.content.decode())

            UserModuleAccess.objects.update_or_create(user=u, module="BAN_HANG",
                                                      defaults={"can_view": True})
            html = web.get("/banle/ban-hang/").content.decode()
            self.ok("tick XEM một mục → vào được trang đó ngay, không cần đăng nhập lại",
                    "BÁN HÀNG" in html)
            thieu = [ten_muc for ma, (_, ten_muc) in Q.MUC.items()
                     if ma != "BAN_HANG" and f"<span>{ten_muc}</span>" in html]
            self.ok("menu CHỈ hiện mục đã tick, các mục còn lại ẩn hết", not thieu, "còn hiện: " + ", ".join(thieu))

            for ma, url in TRANG.items():
                if ma == "BAN_HANG":
                    continue
                self.ok(f"chưa tick {Q.MUC[ma][1]} → {url} bị chặn", web.get(url).status_code in (403, 302),
                        f"HTTP {web.get(url).status_code}")

            for ma in TRANG:
                UserModuleAccess.objects.update_or_create(user=u, module=ma, defaults={"can_view": True})
            html = web.get("/banle/ban-hang/").content.decode()
            con_thieu = [t for _, (_, t) in Q.MUC.items() if f"<span>{t}</span>" not in html]
            self.ok("tick đủ → menu hiện lại đủ 10 mục", not con_thieu, "thiếu: " + ", ".join(con_thieu))
            hong = [url for ma, url in TRANG.items() if web.get(url).status_code != 200]
            self.ok("tick đủ → mở được mọi trang đích", not hong, "còn lỗi: " + ", ".join(hong))

            UserModuleAccess.objects.filter(user=u, module="BAO_CAO").update(can_view=False)
            html = web.get("/banle/ban-hang/").content.decode()
            self.ok("bỏ tick một mục → mục đó biến khỏi menu và trang đích chặn lại",
                    "<span>BÁO CÁO</span>" not in html and web.get("/banle/bao-cao/").status_code == 403)

            sep = U.objects.filter(is_superuser=True, is_active=True).first()
            if sep:
                w2 = Client(SERVER_NAME="localhost")
                w2.force_login(sep)
                h2 = w2.get("/banle/ban-hang/").content.decode()
                self.ok("superuser luôn thấy đủ mục dù không có dòng quyền nào",
                        all(f"<span>{t}</span>" in h2 for _, (_, t) in Q.MUC.items()))
            self.ok("chưa đăng nhập → không mục nào", Q.cua_user(None) == {})
        finally:
            UserModuleAccess.objects.filter(user=u).delete()
            u.delete()
            self.stdout.write("  (dọn) đã xóa tài khoản thử")
        if self.loi:
            for x in self.loi:
                self.stderr.write(self.style.ERROR("FAIL " + x))
            raise SystemExit(1)
        self.stdout.write(self.style.SUCCESS("SMOKE QUYỀN: PASS toàn bộ (đã dọn tài khoản thử)"))

    def ok(self, ten, dieu_kien, chi_tiet=""):
        if dieu_kien:
            self.stdout.write(f"  ok   {ten}")
        else:
            self.loi.append(f"{ten} {chi_tiet}".strip())
            self.stdout.write(self.style.ERROR(f"  FAIL {ten} {chi_tiet}"))
