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
