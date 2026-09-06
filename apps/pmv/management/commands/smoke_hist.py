"""Kiểm thử kho lịch sử PMV_KH2_HIST (Phase 1): danh mục · schema · backfill · reconcile.

An toàn: chỉ ĐỌC KK; ghi vào PMV_KH2_HIST (kho của mình). Backfill lại đúng 1 bảng NHỎ
(T_PRODUCT_TYPE) để kiểm cơ chế — không đụng KK, không đụng bảng lớn.
"""
from django.core.management.base import BaseCommand, CommandError

from apps.pmv import gateway as G
from apps.pmv import hist_config as HC
from apps.pmv import hist_sync as H


class Command(BaseCommand):
    help = "Smoke kho lịch sử: discover + mirror + backfill 1 bảng nhỏ + reconcile"

    def handle(self, *a, **o):
        self.n, self.fail = 0, []

        metas = HC.discover()
        names = {m["table"] for m in metas}
        self.ok("discover ra danh mục bảng", len(metas) >= 40, f"{len(metas)} bảng")
        for t in ("I_CUSTOMER", "TRN_RT_BUYSELL", "TRN_RT_BUYGOLD", "T_PRODUCT", "TRN_RT_BUYSELL_SELL"):
            self.ok(f"có bảng {t}", t in names)

        by = {m["table"]: m for m in metas}
        self.ok("BUYSELL = upsert theo TrnDateTime_Upd",
                by["TRN_RT_BUYSELL"]["strategy"] == "upsert" and by["TRN_RT_BUYSELL"]["watermark"] == "TrnDateTime_Upd")
        self.ok("BUYSELL_SELL = child của BUYSELL",
                by["TRN_RT_BUYSELL_SELL"]["strategy"] == "child" and by["TRN_RT_BUYSELL_SELL"]["parent"] == "TRN_RT_BUYSELL")
        self.ok("I_CUSTOMER = snapshot", by["I_CUSTOMER"]["strategy"] == "snapshot")
        self.ok("bảng *_LOG = append", by["TRN_RT_BUYSELL_Log"]["strategy"] == "append")

        H.ensure_database(); H.ensure_control()
        self.ok("tạo được DB + bảng điều khiển", True)

        m = by["T_PRODUCT_TYPE"]
        H.mirror_schema(m)
        cols = G.hist_query(
            "SELECT name FROM sys.columns WHERE object_id=OBJECT_ID('T_PRODUCT_TYPE')")
        names_c = {c["name"] for c in cols}
        self.ok("schema có 2 cột kỹ thuật _sync_seen_at/_sync_deleted",
                "_sync_seen_at" in names_c and "_sync_deleted" in names_c)

        n = H.backfill(m)
        rec = H.reconcile(m)
        self.ok("backfill T_PRODUCT_TYPE khớp KK", rec["is_match"] and rec["rows_kk"] == n,
                f"KK={rec['rows_kk']} HIST={rec['rows_hist']} n={n}")
        self.ok("checksum KK == HIST", rec["checksum_kk"] == rec["checksum_hist"])

        H._save_state(m, rec, 1)
        st = {r["table_name"]: r for r in H.status()}
        self.ok("ghi được trạng thái vào bảng điều khiển",
                "T_PRODUCT_TYPE" in st and st["T_PRODUCT_TYPE"]["is_match"])

        # ── Phase 3–4 (06/09/2026): bảng tham chiếu · đích "hist" · định tuyến · cờ liên tiến trình · trang ──
        for t in ("T_EMPLOYEE", "T_SECTION", "T_MAINSECTION", "I_GOLD_BAL", "I_XRATE", "SYS_USERS"):
            self.ok(f"bảng tham chiếu {t} trong danh mục (snapshot)",
                    t in names and by[t]["strategy"] == "snapshot")
        import datetime as _d
        from apps.pmv import hist_read as HR
        from apps.pmv.client import PmvClient
        r = PmvClient("hist", tag="smoke").query("SELECT TOP 1 EmpID FROM T_EMPLOYEE WITH (NOLOCK)")
        self.ok("PmvClient('hist') đọc kho bằng SQL y hệt KK", bool(r))
        try:
            G.pmv_call("TRN_RT_BUYSELL_Get", {"p_TrnID": "x"}, tag="smoke", target="hist")
            chan = False
        except G.PmvBlocked:
            chan = True
        self.ok("gọi proc trên kho lịch sử bị CHẶN", chan)
        qua = (_d.date.today() - _d.timedelta(days=1)).isoformat()
        hom = _d.date.today().isoformat()
        self.ok("la_qua_khu: hôm qua True / hôm nay False / rác False",
                HR.la_qua_khu(qua) and not HR.la_qua_khu(hom) and not HR.la_qua_khu("x"))
        HR.doc(lambda c: c.query("SELECT TOP 1 1 AS x FROM T_EMPLOYEE WITH (NOLOCK)"), ngay_iso=qua, tag="smoke")
        self.ok("doc(quá khứ) → cờ nguồn = hist/qua_khu", HR.co_hien_tai() == ("hist", "qua_khu"))
        ctx = HR.nguon(None)
        self.ok("context processor báo nguon_hist + hist_asof rồi XÓA cờ (consume-once)",
                ctx["nguon_hist"] and ctx["hist_asof"] is not None and HR.co_hien_tai() == ("live", ""))
        HR.doc(lambda c: c.query("SELECT TOP 1 1 AS x FROM T_EMPLOYEE WITH (NOLOCK)"), ngay_iso=hom, tag="smoke")
        self.ok("doc(hôm nay) → cờ nguồn = live", HR.co_hien_tai() == ("live", ""))
        ranh0 = not H.dang_chay()
        H._bat_co(); ban = H.dang_chay()
        H._tat_co(); ranh1 = not H.dang_chay()
        self.ok("cờ liên tiến trình: rảnh → bật=đang chạy → tắt=rảnh", ranh0 and ban and ranh1)
        from django.conf import settings as _s
        from django.contrib.auth import get_user_model
        from django.test import Client
        if "testserver" not in _s.ALLOWED_HOSTS:
            _s.ALLOWED_HOSTS.append("testserver")
        u = get_user_model().objects.filter(username="kimhanh2").first()
        if u:
            cl = Client(); cl.force_login(u)
            b = cl.get("/he-thong/kho-lich-su/").content.decode("utf-8", "replace")
            self.ok("trang /he-thong/kho-lich-su/ render (bảng + nút Đồng bộ + Đối soát)",
                    'id="hist-bang"' in b and 'value="sync"' in b and 'value="reconcile"' in b)
            p = cl.get("/he-thong/kho-lich-su/bang/").status_code
            self.ok("mảnh poll /kho-lich-su/bang/ trả 200", p == 200)
            d = cl.get(f"/banle/ban-hang/danh-sach/?ngay={qua}").content.decode("utf-8", "replace")
            self.ok("DANH SÁCH hóa đơn ngày QUÁ KHỨ gắn nhãn kho lịch sử", "kho lịch sử" in d)
            d2 = cl.get(f"/banle/ban-hang/danh-sach/?ngay={hom}").content.decode("utf-8", "replace")
            self.ok("DANH SÁCH hóa đơn HÔM NAY đọc live (không nhãn)", "kho lịch sử" not in d2)

        # KK tuyệt đối không bị đụng: kênh hist KHÔNG được trỏ 206
        from django.conf import settings
        self.ok("kênh HIST trỏ instance nội bộ (không phải KK 206)",
                "206" not in (settings.PMV_HIST_MSSQL or ""))

        self.stdout.write("")
        if self.fail:
            for f in self.fail:
                self.stdout.write(self.style.ERROR(f"  x {f}"))
            raise CommandError(f"TRƯỢT {len(self.fail)}/{self.n}")
        self.stdout.write(self.style.SUCCESS(f"TẤT CẢ {self.n} kịch bản PASS"))

    def ok(self, ten, dieu_kien, chi_tiet=""):
        self.n += 1
        if dieu_kien:
            self.stdout.write(f"  v {ten}" + (f" — {chi_tiet}" if chi_tiet else ""))
        else:
            self.fail.append(f"{ten}" + (f" — {chi_tiet}" if chi_tiet else ""))
            self.stdout.write(self.style.ERROR(f"  x {ten} — {chi_tiet}"))
