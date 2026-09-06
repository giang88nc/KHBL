"""Đồng bộ KK → KHO LỊCH SỬ PMV_KH2_HIST (Phase 1: backfill + reconcile).

  manage.py sync_hist --list                 # xem danh mục bảng + chiến lược
  manage.py sync_hist --backfill             # dựng schema + chép trọn + đối soát TẤT CẢ
  manage.py sync_hist --backfill --table TRN_RT_BUYGOLD,I_CUSTOMER
  manage.py sync_hist --reconcile            # chỉ đối soát (không chép lại)
  manage.py sync_hist                        # xem trạng thái lần chạy gần nhất

KHÔNG bao giờ ghi vào KK — chỉ đọc KK, ghi vào PMV_KH2_HIST.
"""
from django.core.management.base import BaseCommand

from apps.pmv import hist_config as HC
from apps.pmv import hist_sync as H


class Command(BaseCommand):
    help = "Đồng bộ KK → kho lịch sử PMV_KH2_HIST (backfill/reconcile)"

    def add_arguments(self, parser):
        parser.add_argument("--backfill", action="store_true", help="Dựng schema + chép trọn + đối soát")
        parser.add_argument("--sync", action="store_true", help="Đồng bộ incremental (append+upsert+con+void)")
        parser.add_argument("--reconcile", action="store_true", help="Chỉ đối soát")
        parser.add_argument("--list", action="store_true", help="Liệt kê danh mục bảng")
        parser.add_argument("--table", default="", help="Giới hạn bảng (phân tách dấu phẩy)")

    def handle(self, *args, **o):
        tables = [x.strip() for x in o["table"].split(",") if x.strip()] or None

        if o["list"]:
            for m in HC.discover():
                if tables and m["table"].lower() not in {t.lower() for t in tables}:
                    continue
                self.stdout.write(f"  {m['table']:<32} {m['strategy']:<9} "
                                  f"wm={m['watermark'] or '-':<16} pk={','.join(m['pk']) or '-'}"
                                  f"{'  ← cha '+m['parent'] if m['parent'] else ''}")
            return

        if o["sync"] or o["backfill"] or o["reconcile"]:
            # Cờ liên tiến trình: job 09:00/21:00, nút web và lệnh tay KHÔNG chạy chồng nhau
            if H.dang_chay():
                self.stdout.write(self.style.WARNING(
                    "Đang có lượt đồng bộ khác chạy (hist_sync_running=1) — bỏ qua lượt này."))
                return
            H._bat_co()
            try:
                self._chay(o, tables)
            finally:
                H._tat_co()
            return

        # mặc định: trạng thái
        rows = H.status()
        if not rows:
            self.stdout.write("Chưa chạy lần nào. Dùng --backfill để khởi tạo kho lịch sử.")
            return
        self.stdout.write(self.style.MIGRATE_HEADING("TRẠNG THÁI KHO LỊCH SỬ PMV_KH2_HIST"))
        for r in rows:
            flag = "KHỚP" if r["is_match"] else "LỆCH"
            self.stdout.write(f"  {'OK ' if r['is_match'] else '≠  '}{r['table_name']:<32} "
                              f"{r['strategy'] or '':<9} KK={r['rows_kk']} HIST={r['rows_hist']} {flag}"
                              + (f"  {r['note']}" if r["note"] else ""))

    def _chay(self, o, tables):
        if o["sync"]:
            self.stdout.write(self.style.MIGRATE_HEADING("ĐỒNG BỘ INCREMENTAL KK → PMV_KH2_HIST"))
            rep = H.run_sync(tables, log=self.stdout.write)
            loi = [r for r in rep if r.get("error")]
            them = sum(r.get("ins", 0) or 0 for r in rep) + sum(r.get("child_refresh", 0) or 0 for r in rep)
            sua = sum(r.get("upd", 0) or 0 for r in rep)
            void = sum(r.get("void", 0) or 0 for r in rep)
            self.stdout.write("")
            msg = f"XONG {len(rep)} bảng · +{them} mới · ~{sua} sửa · void {void} · lỗi {len(loi)}"
            self.stdout.write((self.style.ERROR if loi else self.style.SUCCESS)(msg))
            for r in loi:
                self.stdout.write(self.style.ERROR(f"  LỖI {r['table']}: {r['error'][:160]}"))
            return

        if o["backfill"] or o["reconcile"]:
            do_bf = bool(o["backfill"])
            self.stdout.write(self.style.MIGRATE_HEADING(
                ("BACKFILL + ĐỐI SOÁT" if do_bf else "ĐỐI SOÁT") + " KK → PMV_KH2_HIST"))
            rep = H.run(tables, do_backfill=do_bf, do_reconcile=True, log=self.stdout.write)
            loi = [r for r in rep if r.get("error")]
            lech = [r for r in rep if r.get("is_match") is False]
            tong_rows = sum(r.get("rows") or 0 for r in rep)
            self.stdout.write("")
            msg = f"XONG {len(rep)} bảng · chép {tong_rows:,} dòng · lệch {len(lech)} · lỗi {len(loi)}"
            self.stdout.write((self.style.ERROR if (loi or lech) else self.style.SUCCESS)(msg))
            for r in lech:
                self.stdout.write(self.style.WARNING(
                    f"  LỆCH {r['table']}: KK={r.get('rows_kk')} HIST={r.get('rows_hist')}"))
            for r in loi:
                self.stdout.write(self.style.ERROR(f"  LỖI {r['table']}: {r['error'][:160]}"))
