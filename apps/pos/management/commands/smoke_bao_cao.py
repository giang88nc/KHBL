# -*- coding: utf-8 -*-
"""Bộ kiểm BÁO CÁO (GĐ chốt 12/09/2026):

    manage.py smoke_bao_cao [--ngay 2026-09-11]

CHỈ ĐỌC máy KK — không ghi gì, chạy lúc nào cũng được. Kiểm những ĐẲNG THỨC phải luôn đúng, vì báo
cáo sai thì không ai biết: số vẫn hiện ra, chỉ là sai. Cụ thể:

  · doanh thu THỰC − vàng cũ − bớt + công thêm + vàng thêm = doanh thu RÒNG (công thức hóa đơn PMV);
  · RÒNG = HH + phần trả khách (các đơn số âm) — ba con số GĐ chốt phải khớp nhau, không tự trôi;
  · tổng của bảng TỪNG NGÀY = tổng của cả kỳ;
  · ly → chỉ đúng 100:1; dẻ D9999 và vàng N9999 về cùng một họ để trừ được nhau;
  · trang BÁO CÁO chặn đúng người chưa được cấp quyền.
"""
import datetime as dt
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.test import Client

from apps.pmv import money as M
from apps.pmv.models import UserModuleAccess
from apps.pos import bao_cao as BC
from apps.pos import services as S


class Command(BaseCommand):
    help = "Smoke báo cáo bán lẻ (chỉ đọc máy KK)"

    def add_arguments(self, p):
        p.add_argument("--ngay", default="", help="Ngày cần kiểm, mặc định ngày gần nhất có hóa đơn")

    def handle(self, *args, **o):
        self.loi = []
        c = S.client("smoke-bao-cao")
        ngay = o["ngay"] or self.ngay_co_so_lieu(c)
        self.stdout.write(f"— Kiểm trên ngày {ngay} —")
        bc = BC.chot_ky(c, ngay, ngay)
        b = bc["ban"]

        self.ok("công thức hóa đơn: THỰC − vàng cũ − bớt + công thêm + vàng thêm = RÒNG",
                b["thuc"] - b["vang_cu"] - b["bot"] + b["cong_them"] + b["vang_them"] == b["rong"],
                f'{b["thuc"]} − {b["vang_cu"]} − {b["bot"]} + {b["cong_them"]} + {b["vang_them"]} ≠ {b["rong"]}')
        self.ok("RÒNG = HH + phần trả khách (đơn âm)", b["rong"] == b["hh"] + b["tra_khach"],
                f'{b["rong"]} ≠ {b["hh"]} + {b["tra_khach"]}')
        self.ok("HH không bao giờ nhỏ hơn RÒNG (đã bỏ đơn âm)", b["hh"] >= b["rong"])
        self.ok("tiền mặt + chuyển khoản + phần chưa rõ = RÒNG",
                b["tien_mat"] + b["chuyen_khoan"] + b["chua_ro"] == b["rong"])

        ng = bc["ngay"]
        self.ok("bảng TỪNG NGÀY có đúng số dòng bằng số ngày của kỳ", len(ng) == bc["so_ngay"])
        self.ok("tổng từng ngày = tổng cả kỳ (thực · ròng · HH · số HĐ)",
                sum((x["thuc"] for x in ng), M.D0) == b["thuc"]
                and sum((x["rong"] for x in ng), M.D0) == b["rong"]
                and sum((x["hh"] for x in ng), M.D0) == b["hh"]
                and sum(x["so_ban"] for x in ng) == b["so"])
        self.ok("tổng thâu từng ngày = tổng thâu cả kỳ",
                sum((x["thau_tien"] for x in ng), M.D0) == bc["thau"]["tien"]
                and sum(x["so_thau"] for x in ng) == bc["thau"]["so"])

        self.ok("100 ly = 1 chỉ", BC.chi(100) == Decimal(1) and BC.chi(9131.9) == Decimal("91.319"))
        self.ok("vàng thâu quy chỉ khớp số ly đọc từ PMV", bc["thau"]["chi"] == BC.chi(bc["thau"]["ly"]))
        ho = BC.ho_vang(c)
        self.ok("dẻ và vàng cùng tuổi về CHUNG một họ (D9999↔N9999 · D18K↔18K · D24K↔24K)",
                ho.get("D9999", {}).get("ho") == ho.get("N9999", {}).get("ho")
                and ho.get("D18K", {}).get("ho") == ho.get("18K", {}).get("ho")
                and ho.get("D24K", {}).get("ho") == ho.get("24K", {}).get("ho"), str(ho.get("D9999")))
        self.ok("mỗi dòng cân đối vàng: chênh lệch = bán ra − thâu vào",
                all(g["lech_chi"] == g["ra_chi"] - g["vao_chi"] for g in bc["vang"]))
        self.ok("tiền bán theo tuổi vàng không vượt doanh thu THỰC của kỳ",
                sum((g["ra_tien"] for g in bc["vang"]), M.D0) <= b["thuc"],
                f'{sum((g["ra_tien"] for g in bc["vang"]), M.D0)} > {b["thuc"]}')
        self.ok("chi tiết theo nhóm hàng: tổng món & tiền của mọi nhóm = tổng bán ra theo tuổi vàng",
                sum(n["mon"] for n in bc["nhom"]) == sum(g["ra_dong"] for g in bc["vang"])
                and sum((n["tien"] for n in bc["nhom"]), M.D0) == sum((g["ra_tien"] for g in bc["vang"]), M.D0),
                f'{sum(n["mon"] for n in bc["nhom"])} món vs {sum(g["ra_dong"] for g in bc["vang"])}')
        self.ok("mỗi nhóm: tổng dòng loại vàng = tổng của nhóm (món · chỉ · tiền)",
                all(sum(v["mon"] for v in n["vang"]) == n["mon"]
                    and sum((v["chi"] for v in n["vang"]), M.D0) == n["chi"]
                    and sum((v["tien"] for v in n["vang"]), M.D0) == n["tien"] for n in bc["nhom"]))
        self.ok("tổng theo nhân viên = tổng cả kỳ",
                sum((n["hh"] for n in bc["nv"]), M.D0) == b["hh"]
                and sum(n["so"] for n in bc["nv"]) == b["so"])

        n = dt.date(2026, 9, 12)
        self.ok("nút kỳ nhanh cắt đúng khoảng ngày",
                BC.khoang_theo_ky("hom_nay", n) == ("2026-09-12", "2026-09-12")
                and BC.khoang_theo_ky("hom_qua", n) == ("2026-09-11", "2026-09-11")
                and BC.khoang_theo_ky("7ngay", n) == ("2026-09-06", "2026-09-12")
                and BC.khoang_theo_ky("thang_nay", n) == ("2026-09-01", "2026-09-12")
                and BC.khoang_theo_ky("thang_truoc", n) == ("2026-08-01", "2026-08-31")
                and BC.khoang_theo_ky("tào lao", n) == ("2026-09-12", "2026-09-12"))
        self.kiem_trang(ngay)

        if self.loi:
            for x in self.loi:
                self.stderr.write(self.style.ERROR("FAIL " + x))
            raise SystemExit(1)
        self.stdout.write(self.style.SUCCESS("SMOKE BÁO CÁO: PASS toàn bộ (chỉ đọc, không đụng dữ liệu)"))

    # ── tiện ích ──────────────────────────────────────────────────────────────
    def ok(self, ten, dieu_kien, chi_tiet=""):
        if dieu_kien:
            self.stdout.write(f"  ok   {ten}")
        else:
            self.loi.append(f"{ten} {chi_tiet}".strip())
            self.stdout.write(self.style.ERROR(f"  FAIL {ten} {chi_tiet}"))

    def ngay_co_so_lieu(self, c):
        """Ngày gần nhất thật sự có hóa đơn — kiểm trên ngày rỗng thì đẳng thức nào cũng đúng."""
        r = c.query("SELECT TOP 1 CONVERT(varchar(10), CreatedDate, 23) ngay FROM TRN_RT_BUYSELL WITH (NOLOCK) "
                    "WHERE IsDel='0' AND Status='C' ORDER BY CreatedDate DESC")
        return r[0]["ngay"] if r else dt.date.today().isoformat()

    def kiem_trang(self, ngay):
        web = Client(SERVER_NAME="localhost")      # ALLOWED_HOSTS không có "testserver"
        sep = get_user_model().objects.filter(is_superuser=True, is_active=True).first()
        thuong = get_user_model().objects.filter(is_superuser=False, is_active=True).exclude(
            module_accesses__module="BAO_CAO", module_accesses__can_view=True).first()
        if sep:
            web.force_login(sep)
            b = web.get(f"/banle/bao-cao/?d1={ngay}&d2={ngay}").content.decode()
            self.ok("trang BÁO CÁO mở được, đủ ba con số + cân đối vàng + nhân viên",
                    all(x in b for x in ("DOANH THU THỰC", "DOANH THU RÒNG", "DOANH THU HH",
                                         "CÂN ĐỐI VÀNG", "THEO NHÂN VIÊN")))
            self.ok("menu có mục BÁO CÁO dùng icon ảnh", ">BÁO CÁO<" in b and "ico/baocao.png" in b)
            self.ok("đổi kỳ qua HTMX chỉ trả mảnh số liệu, không dựng lại cả trang",
                    "<html" not in web.get("/banle/bao-cao/?ky=7ngay",
                                           HTTP_HX_REQUEST="true").content.decode().lower())
        if thuong:
            w = Client(SERVER_NAME="localhost")
            w.force_login(thuong)
            self.ok(f"tài khoản chưa cấp quyền ({thuong.username}) bị chặn",
                    w.get("/banle/bao-cao/").status_code in (403, 302))
            UserModuleAccess.objects.update_or_create(user=thuong, module="BAO_CAO",
                                                      defaults={"can_view": True})
            try:
                self.ok("cấp quyền Xem BÁO CÁO là vào được ngay",
                        w.get("/banle/bao-cao/").status_code == 200)
            finally:
                UserModuleAccess.objects.filter(user=thuong, module="BAO_CAO").delete()
