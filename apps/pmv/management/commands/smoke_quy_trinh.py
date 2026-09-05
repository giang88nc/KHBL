r"""
Bộ kiểm QUY TRÌNH — LƯU và HỌC (05/09/2026). Chạy: manage.py smoke_quy_trinh
File ghi vào THƯ MỤC TẠM, DB trong transaction ROLLBACK, không đụng KK.
"""
import json
import shutil
import tempfile
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction
from django.test import Client
from django.utils import timezone

from apps.pmv import quy_trinh as QT
from apps.pmv.models import PmvBehavior, PmvChange, PmvProcess, PmvProcessStep


class _Rollback(Exception):
    pass


class Command(BaseCommand):
    help = "Smoke test quy trình LƯU/HỌC (rollback, thư mục tạm)"

    def handle(self, *args, **opts):
        self.ok = self.fail = 0
        tmp = Path(tempfile.mkdtemp(prefix="khbl_qt_"))
        goc = QT.QUY_TRINH_DIR
        QT.QUY_TRINH_DIR = tmp
        if "testserver" not in settings.ALLOWED_HOSTS:
            settings.ALLOWED_HOSTS.append("testserver")
        try:
            with transaction.atomic():
                self._chay(tmp)
                raise _Rollback
        except _Rollback:
            pass
        finally:
            QT.QUY_TRINH_DIR = goc
            shutil.rmtree(tmp, ignore_errors=True)
        self.stdout.write("")
        if self.fail:
            self.stdout.write(self.style.ERROR(f"FAIL {self.fail}/{self.ok + self.fail} kịch bản"))
        else:
            self.stdout.write(self.style.SUCCESS(f"PASS toàn bộ {self.ok} kịch bản (đã rollback, thư mục tạm đã xóa)"))

    def kt(self, ten, dk, them=""):
        if dk:
            self.ok += 1
            self.stdout.write(f"  PASS  {ten}")
        else:
            self.fail += 1
            self.stdout.write(self.style.ERROR(f"  FAIL  {ten} {them}"))

    def _chay(self, tmp):
        H = self.style.MIGRATE_HEADING
        self.stdout.write(H("A. Seed BAN_HANG chuẩn + LƯU file"))
        PmvProcess.objects.filter(code="BAN_HANG").delete()
        p, moi = QT.seed_ban_hang()
        tk = QT.thong_ke(p)
        self.kt("seed FULL: 9 pha (0→8), ≥24 bước", moi and tk["pha"] == 9 and tk["buoc"] >= 24)
        self.kt("có đủ proc ghi: Ins/Upd/Complete/T_TILL_TXN_Proc/T_TILL_TXN_Del/TRN_RT_BUYSELL_Del/I_CUSTOMER_Ins",
                set(p.steps.filter(action="ghi").values_list("proc_name", flat=True))
                >= {"TRN_RT_BUYSELL_Ins", "TRN_RT_BUYSELL_Upd", "TRN_RT_BUYSELL_Complete", "T_TILL_TXN_Proc",
                    "T_TILL_TXN_Del", "TRN_RT_BUYSELL_Del", "I_CUSTOMER_Ins"})
        self.kt("6 điểm GĐ chốt có mặt (P-008 · p_Type='0' · 01/01/1900 · 2 xác nhận · dẻ theo mã đơn · nhắc đơn treo)",
                all(p.steps.filter(**{f: v}).exists() for f, v in (
                    ("note__icontains", "P-008"), ("params__icontains", "p_Type='0'"), ("note__icontains", "01/01/1900"),
                    ("title__icontains", "2 xác nhận"), ("note__icontains", "dẻ đi theo mã đơn"), ("title__icontains", "treo quá 30 phút"))))
        p2, moi2 = QT.seed_ban_hang()
        self.kt("seed lần 2 idempotent (không dựng lại)", not moi2 and p2.pk == p.pk and p2.version == p.version)
        md, js = tmp / "BAN_HANG.md", tmp / "BAN_HANG.json"
        self.kt("file .md + .json được ghi", md.exists() and js.exists())
        noi_dung = md.read_text("utf-8")
        self.kt("md có sơ đồ mermaid P0→P8 + bảng bước", "flowchart LR" in noi_dung and "P7 --> P8" in noi_dung and "`TRN_RT_BUYSELL_Complete`" in noi_dung)
        d = json.loads(js.read_text("utf-8"))
        self.kt("json đúng cấu trúc phases/steps", d["code"] == "BAN_HANG" and len(d["phases"]) == 9
                and any(s["proc_name"] == "TRN_RT_BUYSELL_Ins" for s in d["phases"][2]["steps"]))
        p3, _ = QT.seed_ban_hang(ghi_de=True)
        self.kt("seed --ghi-de dựng lại, version +1", p3.version == p.version + 1 and QT.thong_ke(p3)["buoc"] == tk["buoc"])

        self.stdout.write(H("B. Sắp thứ tự / đổi chỗ"))
        pha0 = p.steps.filter(parent=None).order_by("order").first()
        b = list(pha0.children.order_by("order"))
        self.kt("đổi chỗ bước 2 lên trên bước 1", len(b) >= 2 and QT.doi_cho(b[1], -1)
                and list(pha0.children.order_by("order").values_list("pk", flat=True))[:2] == [b[1].pk, b[0].pk])
        self.kt("bước đầu không lên được nữa", not QT.doi_cho(pha0.children.order_by("order").first(), -1))
        QT.sap_lai(p, pha0)
        self.kt("sap_lai đánh 10,20,30…", list(pha0.children.order_by("order").values_list("order", flat=True)) == [10 * i for i in range(1, len(b) + 1)])

        self.stdout.write(H("C. HỌC từ hành vi giả"))
        now = timezone.now()
        def tr(seq, proc, act="ghi", host="KK", cat="HĐ bán"):
            return PmvBehavior(event_time=now, source="TRACE", category=cat, action=act, proc_name=proc,
                               text=f"exec {proc} @p=1", login="sa", host=host, event_seq=seq)
        hv = [tr(1, "sp_procedure_params_managed", "đọc"), tr(2, "TRN_RT_BUYSELL_Get", "đọc"),
              tr(3, "T_TILL_TXN_Del"), tr(4, "TRN_RT_BUYSELL_Get", "đọc"), tr(5, "TRN_RT_BUYSELL_Del"),
              tr(6, "TRN_RT_BUYSELL_Del"), tr(7, "T_PRODUCT_GetByCodeForSell", "đọc", host="KHBL"), tr(8, "I_XRATE_GetAll", "đọc")]
        buoc = QT.gom_buoc(hv)
        self.kt("gom: bỏ DeriveParameters/NOISE/KHBL, gom Del liên tiếp ×2", [x["proc_name"] for x in buoc]
                == ["TRN_RT_BUYSELL_Get", "T_TILL_TXN_Del", "TRN_RT_BUYSELL_Get", "TRN_RT_BUYSELL_Del"] and buoc[3]["so_lan"] == 2)
        ch = [PmvChange(window_start=now, window_end=now, table="TRN_RT_BUYSELL"), PmvChange(window_start=now, window_end=now, table="T_PRODUCT")]
        PmvProcess.objects.filter(code="SMOKE_HOC").delete()
        q = QT.hoc("SMOKE_HOC", "Hủy hóa đơn (test)", hv, ch, source="test")
        pha = q.steps.filter(parent=None).order_by("-order", "-id").first()
        self.kt("hoc(): quy trình nháp + 1 pha 'chưa phân nhóm' + 4 bước, ghi ×N, bảng đổi trong ghi chú pha",
                q.status == "draft" and "chưa phân nhóm" in pha.title and pha.children.count() == 4
                and pha.children.filter(repeat=True, proc_name="TRN_RT_BUYSELL_Del").exists()
                and "T_PRODUCT" in pha.note and (tmp / "SMOKE_HOC.md").exists())
        self.kt("bước ghi mặc định 'cần kiểm', bước đọc 'KHBL làm'",
                pha.children.get(proc_name="T_TILL_TXN_Del").khbl == "kiem" and pha.children.filter(proc_name="TRN_RT_BUYSELL_Get").first().khbl == "lam")
        q2 = QT.hoc("", "", hv[:3], [], source="lần 2", process=q)
        self.kt("hoc() thêm vào quy trình sẵn có → pha học thứ 2", q2.pk == q.pk and q.steps.filter(parent=None).count() == 2 and "lần 2" in q.source)

        self.stdout.write(H("D. Web"))
        User = get_user_model()
        u = User.objects.filter(is_active=True).order_by("id").first()
        c = Client()
        c.force_login(u)
        r = c.get("/he-thong/quy-trinh/")
        b1 = r.content.decode("utf-8", "replace")
        self.kt("GET /he-thong/quy-trinh/ liệt kê BAN_HANG + SMOKE_HOC + form HỌC", r.status_code == 200 and "BAN_HANG" in b1 and "SMOKE_HOC" in b1 and "qt_hoc" not in b1 and "📚 HỌC" in b1)
        r = c.get("/he-thong/quy-trinh/BAN_HANG/")
        b2 = r.content.decode("utf-8", "replace")
        self.kt("GET chi tiết: sơ đồ pha + bảng bước + dialog", r.status_code == 200 and "Hoàn thành" in b2 and "TRN_RT_BUYSELL_Complete" in b2 and 'id="dlg-buoc"' in b2)
        v0 = PmvProcess.objects.get(code="BAN_HANG").version
        r = c.post("/he-thong/quy-trinh/BAN_HANG/them-buoc/", {"title": "Pha thử"}, follow=True)
        pt = PmvProcess.objects.get(code="BAN_HANG").steps.filter(parent=None, title="Pha thử").first()
        self.kt("POST thêm PHA → version +1, file ghi lại", r.status_code == 200 and pt and PmvProcess.objects.get(code="BAN_HANG").version == v0 + 1
                and "Pha thử" in (tmp / "BAN_HANG.md").read_text("utf-8"))
        r = c.post("/he-thong/quy-trinh/BAN_HANG/them-buoc/", {"parent": pt.pk, "title": "Bước thử", "proc_name": "X_Ins", "action": "ghi",
                                                                "repeat": "1", "khbl": "kiem", "doi_chieu": "lech", "params": "p=1", "tables": "T"}, follow=True)
        bt = pt.children.filter(title="Bước thử").first()
        self.kt("POST thêm BƯỚC vào pha với đủ trường", bt and bt.proc_name == "X_Ins" and bt.repeat and bt.khbl == "kiem" and bt.doi_chieu == "lech")
        r = c.post(f"/he-thong/quy-trinh/buoc/{bt.pk}/sua/", {"parent": pha0.pk, "title": "Bước thử đã dời", "proc_name": "X_Ins", "action": "ghi",
                                                              "khbl": "lam", "doi_chieu": "khop"}, follow=True)
        bt.refresh_from_db()
        self.kt("POST sửa: dời sang pha khác + đổi khbl/đối chiếu", bt.parent_id == pha0.pk and bt.khbl == "lam" and bt.doi_chieu == "khop" and bt.title == "Bước thử đã dời")
        r = c.post(f"/he-thong/quy-trinh/buoc/{bt.pk}/len/", follow=True)
        self.kt("POST đổi chỗ lên", r.status_code == 200 and list(pha0.children.order_by("order"))[-2].pk == bt.pk)
        r = c.post(f"/he-thong/quy-trinh/buoc/{bt.pk}/xoa/", follow=True)
        self.kt("POST xóa bước", r.status_code == 200 and not PmvProcessStep.objects.filter(pk=bt.pk).exists())
        r = c.post(f"/he-thong/quy-trinh/buoc/{pt.pk}/xoa/", follow=True)
        self.kt("POST xóa pha", not PmvProcessStep.objects.filter(pk=pt.pk).exists())
        r = c.post("/he-thong/quy-trinh/BAN_HANG/sua/", {"name": "Bán hàng chuẩn", "description": "mô tả", "status": "approved"}, follow=True)
        p = PmvProcess.objects.get(code="BAN_HANG")
        self.kt("POST sửa quy trình (tên/mô tả/trạng thái)", p.name == "Bán hàng chuẩn" and p.status == "approved" and "mô tả" in (tmp / "BAN_HANG.md").read_text("utf-8"))
        r = c.post("/he-thong/quy-trinh/tao/", {"code": "ban_sai", "name": "x"}, follow=True)
        self.kt("tạo mã sai (chữ thường→IN nhưng có dấu cách?) — mã hợp lệ sau upper vẫn tạo", PmvProcess.objects.filter(code="BAN_SAI").exists())
        r = c.post("/he-thong/quy-trinh/tao/", {"code": "sai ma", "name": "x"}, follow=True)
        self.kt("tạo mã có dấu cách → từ chối", "phải là CHỮ IN" in r.content.decode("utf-8", "replace") and not PmvProcess.objects.filter(code="SAI MA").exists())
        PmvBehavior.objects.bulk_create([tr(900000101, "TRN_RT_BUYSELL_Del"), tr(900000102, "TRN_RT_BUYSELL_Get", "đọc")])
        r = c.post("/he-thong/quy-trinh/hoc/", {"code": "XOA_HD", "name": "Xóa HĐ", "seq_tu": "900000100", "seq_den": "900000102"}, follow=True)
        x = PmvProcess.objects.filter(code="XOA_HD").first()
        self.kt("POST hoc theo seq (từ báo cáo đánh dấu) → quy trình nháp 2 bước", x and x.steps.filter(parent__isnull=False).count() == 2 and "seq 900000100" in x.source)
        r = c.post("/he-thong/quy-trinh/hoc/", {"code": "RONG", "name": "r", "tu": "2000-01-01T00:00", "den": "2000-01-01T00:01"}, follow=True)
        self.kt("hoc khung trống → cảnh báo, không tạo", "Không có lời gọi" in r.content.decode("utf-8", "replace") and not PmvProcess.objects.filter(code="RONG").exists())
        r = c.post("/he-thong/quy-trinh/XOA_HD/xoa/", follow=True)
        self.kt("POST xóa quy trình khỏi DB, file .md còn", not PmvProcess.objects.filter(code="XOA_HD").exists() and (tmp / "XOA_HD.md").exists())
        for url in ("/he-thong/", "/he-thong/so-sanh/", "/he-thong/hanh-vi/"):
            self.kt(f"nav {url} có link Quy trình", "/he-thong/quy-trinh/" in c.get(url).content.decode("utf-8", "replace"))
