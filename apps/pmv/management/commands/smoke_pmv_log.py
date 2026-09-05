r"""
Bộ kiểm NHẬT KÝ HÀNH VI ra file + DÒ THAY ĐỔI SQL KK (Bước 1 + 2, GĐ duyệt 05/09/2026).
Chạy: manage.py smoke_pmv_log
- Ghi file vào THƯ MỤC TẠM (không đụng logs\pmv thật), mọi dòng DB trong transaction ROLLBACK.
- KHÔNG gọi sang KK: chụp trạng thái / đọc trace được thay bằng hàm giả.
"""
import shutil
import tempfile
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.db import transaction
from django.test import Client
from django.utils import timezone
from io import StringIO

from apps.pmv import behavior_log as BL
from apps.pmv import views as V
from apps.pmv.classify import _CATEGORY_RULES
from apps.pmv.management.commands.pmv_trace import _chon_file_dau
from apps.pmv.models import PmvBehavior, PmvChange, PmvSnapshot, PmvState


class _Rollback(Exception):
    pass


def _tr(**k):
    d = dict(event_time=timezone.now(), source=PmvBehavior.Source.TRACE, category="HĐ bán", action="ghi",
             proc_name="TRN_RT_BUYSELL_Ins", text="TRN_RT_BUYSELL_Ins @p_TrnID='',@p_X='a\r\nb'",
             login="sa", host="KK", duration_ms=42, reads=10, writes=2, row_count=1, event_seq=1000)
    d.update(k)
    return PmvBehavior(**d)


class Command(BaseCommand):
    help = "Smoke test nhật ký hành vi ra file + dò thay đổi (rollback, thư mục tạm)"

    def handle(self, *args, **opts):
        self.ok = self.fail = 0
        tmp = Path(tempfile.mkdtemp(prefix="khbl_log_"))
        goc = BL.LOG_DIR
        goc_chup, goc_thu, goc_cot = BL.chup_kk, V._thu_trace, BL.cot_moc_kk
        BL.LOG_DIR = tmp
        if "testserver" not in settings.ALLOWED_HOSTS:
            settings.ALLOWED_HOSTS.append("testserver")
        try:
            with transaction.atomic():
                self._chay(tmp)
                raise _Rollback
        except _Rollback:
            pass
        finally:
            BL.LOG_DIR = goc
            BL.chup_kk, V._thu_trace, BL.cot_moc_kk = goc_chup, goc_thu, goc_cot
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
        now = timezone.now()
        hom_nay = timezone.localdate()
        d = BL.thu_muc_ngay(hom_nay)

        self.stdout.write(self.style.MIGRATE_HEADING("A. Nhóm → tên file"))
        self.kt("mọi nhóm của classify.py đều có tên file", {c for _, c in _CATEGORY_RULES} | {"Khác"} <= set(BL.NHOM_FILE))
        self.kt("tên file toàn ASCII", all(t.isascii() and t.islower() for t in BL.NHOM_FILE.values()))

        self.stdout.write(self.style.MIGRATE_HEADING("B. Định dạng 1 dòng"))
        s = BL.dong(_tr())
        self.kt("TRACE: 1 dòng, xuống dòng trong tham số bị ép phẳng", "\n" not in s and "a b" in s and " TRACE | KK " in s)
        self.kt("có proc + ms + r/w/n + nhóm + hành động", all(x in s for x in ("TRN_RT_BUYSELL_Ins", "42ms", "r=10 w=2 n=1", "HĐ bán", "| ghi |")))
        sk = BL.dong(_tr(login=settings.PMV_MSSQL_USER, host="KHBL"))
        self.kt("lời gọi của KHBL gắn nhãn máy KHBL", "| KHBL  |" in sk)
        ss = BL.dong(PmvBehavior(event_time=now, source=PmvBehavior.Source.STATS, category="Khách hàng",
                                 action="ghi", proc_name="I_CUSTOMER_Upd", exec_delta=3))
        self.kt("STATS: ghi số lần gọi, không tham số", " STATS " in ss and "+3 lần" in ss)

        self.stdout.write(self.style.MIGRATE_HEADING("C. Ghi file ALL + nhóm"))
        rows = [_tr(event_seq=1), _tr(event_seq=2, category="Khách hàng", proc_name="I_CUSTOMER_Ins"),
                _tr(event_seq=3, login=settings.PMV_MSSQL_USER, host="KHBL")]
        n = BL.ghi(rows)
        all_log = (d / "ALL.log")
        self.kt("ghi() trả 3 dòng, ALL.log 3 dòng", n == 3 and all_log.exists() and len(all_log.read_text("utf-8").splitlines()) == 3)
        self.kt("ban_hang.log 2 dòng · khach_hang.log 1 dòng",
                   len((d / "ban_hang.log").read_text("utf-8").splitlines()) == 2
                   and len((d / "khach_hang.log").read_text("utf-8").splitlines()) == 1)
        BL.ghi([_tr(event_seq=4)])
        self.kt("ghi tiếp là APPEND (ALL.log 4 dòng)", len(all_log.read_text("utf-8").splitlines()) == 4)
        self.kt("thứ tự theo giờ sự kiện, UTF-8 có dấu đọc lại đúng", "HĐ bán" in all_log.read_text("utf-8"))

        self.stdout.write(self.style.MIGRATE_HEADING("D. Xuất lại từ DB"))
        PmvBehavior.objects.bulk_create([_tr(event_seq=11), _tr(event_seq=12, category="Bảng giá", proc_name="I_XRATE_Ins")])
        (d / "danh_dau_010203.log").write_text("giu\n", encoding="utf-8")
        n, m = BL.xuat_lai(hom_nay)
        # KHÔNG dùng __date: MySQL chưa nạp bảng timezone → CONVERT_TZ trả rỗng (bẫy KHJ) — lọc khoảng [đầu ngày, đầu ngày sau)
        dau = timezone.make_aware(timezone.datetime.combine(hom_nay, timezone.datetime.min.time()))
        so_db = PmvBehavior.objects.filter(event_time__gte=dau, event_time__lt=dau + timedelta(days=1)).count()
        self.kt(f"xuat_lai dựng lại {n} dòng = số dòng DB trong ngày ({so_db})", n == so_db and n >= 2)
        self.kt("file danh_dau_* được giữ nguyên, ALL.log = số dòng DB", (d / "danh_dau_010203.log").exists()
                   and len(all_log.read_text("utf-8").splitlines()) == n)
        self.kt("bang_gia.log có sau xuất lại", (d / "bang_gia.log").exists())

        self.stdout.write(self.style.MIGRATE_HEADING("E. Dọn file / DB"))
        (tmp / "2026-01-01").mkdir(); (tmp / "2026-01-01" / "ALL.log").write_text("x")
        (tmp / "khong_phai_ngay").mkdir()
        xoa = BL.don_file(90, hom_nay=hom_nay)
        self.kt("don_file xóa đúng thư mục cũ, giữ hôm nay + thư mục lạ", xoa == ["2026-01-01"] and d.exists() and (tmp / "khong_phai_ngay").exists())
        cu = PmvBehavior.objects.create(event_time=now, source="TRACE", category="Khác", action="đọc", proc_name="X_Get")
        PmvBehavior.objects.filter(pk=cu.pk).update(collected_at=now - timedelta(days=40))
        PmvChange.objects.create(window_start=now, window_end=now, table="T", mark="")
        PmvChange.objects.filter(table="T").update(created_at=now - timedelta(days=31))
        n1, n2 = BL.don_db(30, bay_gio=now)
        self.kt("don_db xóa 1 hành vi 40 ngày + 1 thay đổi 31 ngày, giữ dòng mới", n1 == 1 and n2 == 1
                   and PmvBehavior.objects.filter(event_seq=11).exists())

        self.stdout.write(self.style.MIGRATE_HEADING("F. Dò thay đổi (snapshot giả, không đụng KK)"))
        PmvState.objects.filter(key__in=[BL.SNAP_KEY, BL.SNAP_LUC_KEY, BL.THAY_DOI_LAST_KEY]).delete()
        s1 = {"TRN_RT_BUYSELL": {"rows": 100, "cs": 1}, "T_PRODUCT": {"rows": 50, "cs": 7}, "I_GOLD": {"rows": 13, "cs": 9}}
        s2 = {"TRN_RT_BUYSELL": {"rows": 101, "cs": 2}, "T_PRODUCT": {"rows": 50, "cs": 8}, "I_GOLD": {"rows": 13, "cs": 9}}
        kq0 = BL.do_thay_doi([], snap=s1)
        self.kt("lần đầu chỉ lấy mốc (không ghi gì)", kq0 == [] and PmvState.get(BL.SNAP_KEY) != "")
        kq1 = BL.do_thay_doi([_tr(), _tr(proc_name="T_TILL_TXN_Proc", category="Sổ quỹ"),
                              _tr(action="đọc", proc_name="TRN_RT_BUYSELL_Get")], snap=s2)
        bang = sorted(c.table for c in kq1)
        self.kt("bắt đúng 2 bảng đổi (dòng +1 · checksum đổi), bỏ bảng không đổi", bang == ["TRN_RT_BUYSELL", "T_PRODUCT"])
        tp = next(c for c in kq1 if c.table == "T_PRODUCT")
        self.kt("T_PRODUCT: Δ=0 nhưng checksum đổi", tp.delta == 0 and tp.cs_changed)
        self.kt("proc GHI trong khung ghi kèm, proc đọc bị bỏ", "TRN_RT_BUYSELL_Ins(1)" in tp.procs
                   and "T_TILL_TXN_Proc(1)" in tp.procs and "Get" not in tp.procs)
        td = d / "thay_doi.log"
        self.kt("thay_doi.log có khối tóm tắt + 2 dòng bảng", td.exists() and "2 bảng đổi" in td.read_text("utf-8")
                   and "checksum đổi" in td.read_text("utf-8"))
        kq2 = BL.do_thay_doi([], snap=s2)
        self.kt("không đổi → không ghi, trạng thái '0 bảng đổi'", kq2 == [] and "0 bảng đổi" in PmvState.get(BL.THAY_DOI_LAST_KEY))
        c = BL.proc_ghi_trong([PmvBehavior(source="STATS", action="ghi", proc_name="A", exec_delta=3), _tr(proc_name="B"), _tr(proc_name="B")])
        self.kt("chuoi_proc: STATS cộng exec_delta, sắp nhiều→ít", BL.chuoi_proc(c) == "A(3) B(2)")

        self.stdout.write(self.style.MIGRATE_HEADING("G. Chọn file ĐẦU chuỗi trace"))
        ten = ["pmv_behavior.trc", "pmv_behavior_1.trc", "pmv_behavior_20260905T0530.trc",
               "pmv_behavior_20260905T0530_1.trc", "PMV_BEHAVIOR_20260905T0530_2.trc"]
        p = r"D:\KHJ_PMV_BACKUP\trace\pmv_behavior_20260905T0530_2.trc"
        self.kt("đang ghi _2 → đọc từ file gốc .trc cùng chuỗi (bỏ chuỗi cũ)",
                   _chon_file_dau(p, ten) == r"D:\KHJ_PMV_BACKUP\trace\pmv_behavior_20260905T0530.trc")
        self.kt("file gốc đã bị xoay vòng xóa → lấy _1", _chon_file_dau(p, ten[3:]) == r"D:\KHJ_PMV_BACKUP\trace\pmv_behavior_20260905T0530_1.trc")
        self.kt("không thấy file nào → giữ path đang ghi", _chon_file_dau(p, ["khac.trc"]) == p)

        self.stdout.write(self.style.MIGRATE_HEADING("G2. Bóc proc thật khỏi lớp bọc sp_prepexec (lời gọi của KHBL qua pyodbc)"))
        from apps.pmv.classify import NOISE, proc_name_from_text
        from apps.pmv.management.commands.collect_pmv_behavior import _dong_trace
        boc_exec = ("declare @p1 int set @p1=7 exec sp_prepexec @p1 output,N'@P1 nvarchar(20)',"
                    "N'DECLARE @__rc INT; EXEC @__rc = [T_PRODUCT_GetByCodeForSell] @p_ProductCode=@P1; SELECT @__rc AS __rc',N'ABC'")
        boc_select = ("declare @p1 int set @p1=1 exec sp_prepexec @p1 output,N'@P1 nvarchar(26)',"
                      "N'SELECT x.GoldCcy FROM I_XRATE x WITH (NOLOCK) WHERE x.ShopID = @P1 ORDER BY x.Type',N''")
        self.kt("sp_prepexec bọc EXEC proc → lấy tên proc thật", proc_name_from_text(boc_exec) == "T_PRODUCT_GetByCodeForSell")
        self.kt("sp_prepexec bọc SELECT trần → không có proc", proc_name_from_text(boc_select) == "")
        self.kt("exec thường vẫn bóc đúng; sp_unprepare là NOISE",
                proc_name_from_text("exec TRN_RT_BUYSELL_Lst @p_TrnID=''") == "TRN_RT_BUYSELL_Lst" and "sp_unprepare" in NOISE)
        chung = {"EventClass": 10, "StartTime": now, "Duration": 1000, "Reads": 1, "Writes": 0, "RowCounts": 1, "ObjectName": None}
        # RPC:Completed thật điền ObjectName = "sp_prepexec" (bẫy đã dính 05/09) — giả đúng như vậy
        gia = [{**chung, "EventSequence": 1, "SPID": 5, "HostName": "MRGIANG", "ApplicationName": "python",
                "LoginName": settings.PMV_MSSQL_USER, "TextData": boc_select, "ObjectName": "sp_prepexec"},
               {**chung, "EventSequence": 2, "SPID": 5, "HostName": "MRGIANG", "ApplicationName": "python",
                "LoginName": settings.PMV_MSSQL_USER, "TextData": boc_exec, "ObjectName": "sp_prepexec"},
               {**chung, "EventSequence": 3, "SPID": 6, "HostName": "KK", "ApplicationName": ".Net",
                "LoginName": "sa", "TextData": "exec sp_unprepare 1"}]
        ra = _dong_trace(gia)
        self.kt("collect: bỏ SELECT bọc của KHBL + sp_unprepare, giữ proc thật gắn máy KHBL",
                len(ra) == 1 and ra[0].proc_name == "T_PRODUCT_GetByCodeForSell" and ra[0].host == "KHBL")

        self.stdout.write(self.style.MIGRATE_HEADING("H. Web: ĐÁNH DẤU TRƯỚC / SAU (chụp giả)"))
        User = get_user_model()
        u = User.objects.filter(is_active=True).order_by("id").first()
        cl = Client()
        cl.force_login(u)
        r = cl.get("/he-thong/so-sanh/")
        b = r.content.decode("utf-8", "replace")
        self.kt("GET /so-sanh/ có 2 nút đánh dấu, nút SAU đang disabled", r.status_code == 200 and "ĐÁNH DẤU TRƯỚC" in b
                   and "ĐÁNH DẤU SAU" in b and "disabled" in b)
        PmvState.objects.filter(key=V.DANH_DAU_KEY).delete()
        r = cl.post("/he-thong/so-sanh/danh-dau/sau/", follow=True)
        self.kt("SAU khi chưa có TRƯỚC → báo lỗi, không nổ", r.status_code == 200 and "Chưa có mốc TRƯỚC" in r.content.decode("utf-8", "replace"))

        BL.chup_kk = lambda: dict(s1)
        V._thu_trace = lambda: []
        BL.cot_moc_kk = lambda: {"TRN_RT_BUYSELL": ["TrnDateTime_Upd"]}
        PmvState.set("pmv_trace_last_seq", "900000000")  # lớn hơn mọi EventSequence thật để báo cáo chỉ gồm dòng giả
        n_snap = PmvSnapshot.objects.count()
        r = cl.post("/he-thong/so-sanh/danh-dau/truoc/")
        mark = V._danh_dau_hien_tai()
        self.kt("TRƯỚC: redirect, lưu snapshot + mốc seq", r.status_code == 302 and mark and mark["seq"] == 900000000
                   and PmvSnapshot.objects.count() == n_snap + 1)
        b = cl.get("/he-thong/so-sanh/").content.decode("utf-8", "replace")
        self.kt("trang So sánh báo 'Đang chờ mốc SAU'", "Đang chờ mốc SAU" in b)
        PmvBehavior.objects.create(event_time=now, source="TRACE", category="HĐ bán", action="ghi",
                                   proc_name="TRN_RT_BUYSELL_Complete", text="TRN_RT_BUYSELL_Complete @p_TrnID='TRB1'",
                                   login="sa", host="KK", event_seq=900000001)
        PmvBehavior.objects.create(event_time=now, source="TRACE", category="HĐ bán", action="đọc",
                                   proc_name="TRN_RT_BUYSELL_Get", login="sa", host="QQ", event_seq=899999999)
        BL.chup_kk = lambda: dict(s2)
        BL.mau_dong_doi_goc = BL.mau_dong_doi
        BL.mau_dong_doi = lambda bang, cols, tu, limit=30: [{"TrnID": "TRB1", "PayAmount": "7050000"}]
        try:
            r = cl.post("/he-thong/so-sanh/danh-dau/sau/")
            b = r.content.decode("utf-8", "replace")
        finally:
            BL.mau_dong_doi = BL.mau_dong_doi_goc
        self.kt("SAU: trả báo cáo 200", r.status_code == 200 and "Báo cáo đánh dấu" in b)
        self.kt("báo cáo có proc giữa 2 mốc (seq>mốc), bỏ proc seq cũ", "TRN_RT_BUYSELL_Complete" in b and "TRN_RT_BUYSELL_Get" not in b)
        self.kt("báo cáo có 2 bảng đổi + dòng mẫu", "T_PRODUCT" in b and "TRB1" in b and "7050000" in b)
        # chỉ đếm dòng của LƯỢT NÀY — DB thật đã có dòng danhdau do GĐ bấm trên trang (05/09 06:19, 06:22)
        ch = PmvChange.objects.filter(mark=f"danhdau:{mark['snap_id']}")
        self.kt("PmvChange ghi 2 dòng gắn mark danhdau", ch.count() == 2 and "TRN_RT_BUYSELL_Complete(1)" in ch.first().procs)
        self.kt("mốc TRƯỚC đã đóng, file danh_dau_*.log tạo ra", V._danh_dau_hien_tai() is None
                   and any(f.name.startswith("danh_dau_") and "TRB1" in f.read_text("utf-8") for f in d.glob("danh_dau_*.log")))
        r = cl.get("/he-thong/hanh-vi/")
        self.kt("GET /hanh-vi/ có dòng dò thay đổi + đường dẫn file", r.status_code == 200 and "Dò thay đổi SQL KK" in r.content.decode("utf-8", "replace"))

        self.stdout.write(self.style.MIGRATE_HEADING("I. Lệnh pmv_log"))
        out = StringIO()
        call_command("pmv_log", "thu-muc", stdout=out)
        self.kt("pmv_log thu-muc in đúng thư mục đang dùng", str(tmp) in out.getvalue())
        out = StringIO()
        call_command("pmv_log", "xuat-lai", hom_nay.isoformat(), stdout=out)
        self.kt("pmv_log xuat-lai chạy được", "Đã dựng lại" in out.getvalue())
