"""Đổi dữ liệu giờ UTC naive → GIỜ VN (+7h) cho khj_bl + khj_hr (+ khj_cd.auth_user) — kế hoạch docs/KE_HOACH_DOI_GIO_VN.md.

    manage.py doi_gio_vn            # = --thu: chạy UPDATE thật trong transaction rồi ROLLBACK, in số liệu
    manage.py doi_gio_vn --ghi      # lưu thật (chỉ khi web KHBL 8100 + KHJ 8000 đã TẮT)
    manage.py doi_gio_vn --lui      # trừ 7h đúng các bảng đã đánh dấu (lùi lại)

- Danh sách cột ĐÓNG CỨNG theo Phụ lục A (không tự dò lúc chạy) — bảng đã là giờ VN (bank_notifications, gold_prices,
  zalo_messages, khj_cd.* trừ auth_user) KHÔNG có ở đây.
- Mỗi bảng 1 câu UPDATE trong 1 transaction, kèm dòng đánh dấu `khj_bl.tz_doi_gio_vn` cùng transaction ⇒ bảng đã đổi
  không bao giờ bị cộng lần hai (chạy lại / đứt giữa chừng đều an toàn).
- Kết nối `default` (khj_admin dùng chung 3 DB khj_*) nên gọi thẳng `db`.`bang`.
"""
import socket

from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction

GIO = 7
BANG_DANH_DAU = "`khj_bl`.`tz_doi_gio_vn`"

# ── PHỤ LỤC A (đối chiếu lại information_schema 20/09/2026: khớp 100%) ──
COT = {
    "khj_bl": {
        "auth_user": ["last_login", "date_joined"],
        "bank_reconcile_state": ["next_attempt", "updated_at"],
        "bill_audit": ["created_at"],
        "customer_bridge_receipt": ["updated_at"],
        "customer_sync_receipt": ["updated_at"],
        "django_admin_log": ["action_time"],
        "django_migrations": ["applied"],
        "django_session": ["expire_date"],
        "gold_bill": ["synced_at", "created_at", "updated_at", "delivered_at", "last_contact", "next_contact", "ready_at"],
        "gold_price_batches": ["created_at", "synced_at"],
        "mobile_employee_identity": ["created_at"],
        "money_flow": ["synced_at", "created_at"],
        "money_flow_bank_receipt": ["created_at"],
        "money_flow_payment": ["created_at", "updated_at"],
        "money_flow_source_write": ["created_at", "updated_at"],
        "pmv_audit_logs": ["created_at"],
        "pmv_behavior_logs": ["event_time", "collected_at"],
        "pmv_change_logs": ["window_start", "window_end", "created_at"],
        "pmv_process": ["created_at", "updated_at"],
        "pmv_snapshots": ["created_at"],
        "pmv_state": ["updated_at"],
        "pmv_web_users": ["updated_at"],
        "pos_depositevent": ["created_at"],
        "pos_depositmessage": ["scheduled_at", "created_at", "sent_at", "claimed_at"],
        "pos_depositmessagetemplate": ["updated_at"],
        "pos_depositmoneyoperation": ["created_at", "completed_at"],
        "pos_depositorderstate": ["next_contact", "last_contact", "ready_at", "delivered_at", "updated_at"],
        "pos_depositstockhold": ["created_at", "released_at"],
        "pos_depositsubmission": ["created_at"],
        "sys_users": ["synced_at"],
        "thau_anh_tam": ["created_at"],
        "thau_nhom": ["created_at"],
        "thau_payment_link": ["created_at", "revoked_at"],
        "unlock_passcodes": ["updated_at"],
        "user_module_access": ["updated_at"],
        "zalo_send_rules": ["effective_from", "last_scan_at", "created_at", "updated_at"],
        "zalo_templates": ["zalo_created_at", "last_synced_at", "created_at", "updated_at"],
    },
    "khj_hr": {
        "access_rules": ["updated_at"],
        "attendance_daily": ["check_in", "check_out", "updated_at"],
        "audit_logs": ["created_at"],
        "calendar_notes": ["created_at"],
        "day_time_off": ["created_at"],
        "departments": ["created_at", "updated_at"],
        "devices": ["last_seen_at", "last_event_at", "created_at", "updated_at"],
        "diligence_config": ["created_at"],
        "django_admin_log": ["action_time"],
        "django_migrations": ["applied"],
        "django_session": ["expire_date"],
        "employee_benefits": ["created_at", "updated_at"],
        "employee_portal_calendar_revision": ["updated_at"],
        "employee_portal_daily_admissions": ["created_at", "expires_at", "used_at"],
        "employee_portal_devices": ["enrolled_at", "last_seen_at", "expires_at", "revoked_at"],
        "employee_portal_events": ["created_at", "updated_at"],
        "employee_portal_khbl_grants": ["expires_at", "opened_at", "consumed_at", "created_at"],
        "employee_portal_lan_challenges": ["created_at", "expires_at", "used_at"],
        "employee_portal_leave_submissions": ["created_at"],
        "employee_portal_pairings": ["created_at", "expires_at", "used_at", "last_attempt_at", "locked_at"],
        "employee_portal_passkeys": ["created_at", "last_used_at", "revoked_at"],
        "employees": ["created_at", "updated_at"],
        "employees_sync_ghg_backup_20260821_192328": ["created_at", "updated_at"],
        "feedbacks": ["created_at"],
        "insurance_config": ["created_at"],
        "inventory_snapshot": ["created_at"],
        "kk_sync_state": ["last_run_at", "last_success_at"],
        "kpi_periods": ["created_at"],
        "kpi_scores": ["updated_at"],
        "leave_balances": ["updated_at"],
        "leave_day_decisions": ["created_at"],
        "leave_requests": ["approved_at", "created_at", "updated_at", "signed_at", "submitted_at",
                           "zalo_notify_scheduled_at"],
        "leave_zalo_notifications": ["scheduled_at", "next_attempt_at", "synced_at", "created_at", "updated_at"],
        "overtime_config": ["created_at"],
        "payroll_periods": ["locked_at", "created_at"],
        "payroll_slips": ["updated_at"],
        "positions": ["created_at", "updated_at"],
        "raw_attendance_events": ["event_time", "created_at"],
        "revenue_bonus_apply": ["updated_at"],
        "revenue_bonus_config": ["created_at"],
        "revenue_records": ["created_at", "updated_at"],
        "roster_assignments": ["created_at"],
        "salary_advances": ["created_at"],
        "salary_scales": ["created_at"],
        "salary_step_adjustments": ["created_at"],
        "salary_structures": ["created_at"],
        "shift_attendance": ["check_in", "check_out", "updated_at"],
        "shift_group_assignments": ["created_at"],
        "shift_groups": ["created_at"],
        "shift_plans": ["created_at"],
        "tax_config": ["created_at"],
        "users": ["last_login", "date_joined"],
    },
    # KHCD: cả DB đã giờ VN, riêng auth_user còn UTC (last_login do auth.py cũ ghi; date_joined chép từ bảng user nguồn
    # vốn UTC) — auth.py đã sửa 20/09/2026, KHỞI ĐỘNG LẠI KHCD NGAY SAU --ghi.
    "khj_cd": {
        "auth_user": ["last_login", "date_joined"],
    },
}

# Bảng có khóa duy nhất chứa cột giờ: cộng từ dòng MUỘN nhất trước (lùi thì ngược lại) để 2 lượt quẹt cách nhau đúng
# 7 giờ (08:00 và 15:00 cùng NV cùng máy) không đụng nhau giữa chừng câu UPDATE.
THU_TU = {("khj_hr", "raw_attendance_events"): "event_time"}

CONG_WEB = {8100: "KHBL web", 8000: "KHJ web"}


def _q(s):
    return "`" + s.replace("`", "``") + "`"


def _ten(db, bang):
    return f"{_q(db)}.{_q(bang)}"


def _cau_update(db, bang, cols, dau):
    phep = "+" if dau > 0 else "-"
    set_ = ", ".join(f"{_q(c)} = {_q(c)} {phep} INTERVAL {GIO} HOUR" for c in cols)
    sql = f"UPDATE {_ten(db, bang)} SET {set_}"
    moc = THU_TU.get((db, bang))
    if moc:
        sql += f" ORDER BY {_q(moc)} {'DESC' if dau > 0 else 'ASC'}"
    return sql


def _cong_dang_mo(port):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            return True
    except OSError:
        return False


class Command(BaseCommand):
    help = "Đổi dữ liệu giờ UTC → giờ VN (+7h) cho khj_bl + khj_hr + khj_cd.auth_user (mặc định chạy THỬ, rollback)."

    def add_arguments(self, parser):
        g = parser.add_mutually_exclusive_group()
        g.add_argument("--thu", action="store_true", help="(mặc định) chạy trong transaction rồi ROLLBACK")
        g.add_argument("--ghi", action="store_true", help="LƯU THẬT — chỉ khi web KHBL + KHJ đã tắt")
        g.add_argument("--lui", action="store_true", help="trừ 7h đúng các bảng đã đánh dấu (lùi lại)")
        parser.add_argument("--bo-kiem-tat", action="store_true",
                            help="bỏ qua kiểm tra web 8000/8100 đã tắt (không khuyến khích)")

    # ── đánh dấu ──
    def _co_bang_danh_dau(self, c):
        c.execute("SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='khj_bl' "
                  "AND table_name='tz_doi_gio_vn'")
        return c.fetchone()[0] > 0

    def _da_doi(self, c):
        if not self._co_bang_danh_dau(c):
            return {}
        c.execute(f"SELECT db, bang, so_dong, luc FROM {BANG_DANH_DAU}")
        return {(db, bang): (n, luc) for db, bang, n, luc in c.fetchall()}

    def _tao_bang_danh_dau(self, c):
        # DDL tự COMMIT ở MySQL — tạo NGOÀI mọi transaction dữ liệu.
        c.execute(f"""CREATE TABLE IF NOT EXISTS {BANG_DANH_DAU} (
            db VARCHAR(32) NOT NULL, bang VARCHAR(128) NOT NULL, so_dong INT NOT NULL,
            luc DATETIME NOT NULL, PRIMARY KEY (db, bang)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Đợt đổi UTC→giờ VN: bảng đã +7h (doi_gio_vn)'""")

    def _max(self, c, db, bang, col):
        c.execute(f"SELECT MAX({_q(col)}) FROM {_ten(db, bang)}")
        return c.fetchone()[0]

    def handle(self, *args, **o):
        che_do = "ghi" if o["ghi"] else "lui" if o["lui"] else "thu"
        if che_do != "thu" and not o["bo_kiem_tat"]:
            mo = [f"{ten} ({p})" for p, ten in CONG_WEB.items() if _cong_dang_mo(p)]
            if mo:
                raise CommandError("Web còn đang chạy: " + ", ".join(mo) + " — TẮT HẾT (watchdog trước) rồi chạy lại.")

        with connection.cursor() as c:
            # MyISAM không rollback được ⇒ --thu sẽ GHI THẬT vào bảng đó: chặn từ đầu.
            c.execute("SELECT table_schema, table_name, engine FROM information_schema.tables "
                      "WHERE table_schema IN ('khj_bl','khj_hr','khj_cd') AND table_type='BASE TABLE'")
            may = {(s, t): e for s, t, e in c.fetchall()}
            thieu = [f"{db}.{b}" for db, bs in COT.items() for b in bs if (db, b) not in may]
            khac = [f"{db}.{b} ({may[(db, b)]})" for db, bs in COT.items() for b in bs
                    if (db, b) in may and may[(db, b)] != "InnoDB"]
            if thieu or khac:
                raise CommandError("Dừng, chưa đụng gì: " + "; ".join(
                    ([f"không thấy bảng {', '.join(thieu)}"] if thieu else [])
                    + ([f"bảng không phải InnoDB {', '.join(khac)}"] if khac else [])))
            da = self._da_doi(c)
            if che_do == "ghi":
                self._tao_bang_danh_dau(c)
            if che_do == "lui":
                return self._lui(c, da)

            tong_bang = tong_dong = bo_qua = 0
            nhan = "THỬ (rollback)" if che_do == "thu" else "GHI THẬT"
            self.stdout.write(self.style.MIGRATE_HEADING(f"=== doi_gio_vn — {nhan} — +{GIO} giờ ==="))
            for db, bangs in COT.items():
                self.stdout.write(self.style.MIGRATE_HEADING(f"\n[{db}]"))
                for bang, cols in bangs.items():
                    if (db, bang) in da:
                        n, luc = da[(db, bang)]
                        self.stdout.write(f"  = {bang:<45} ĐÃ ĐỔI lúc {luc:%d/%m/%Y %H:%M} ({n} dòng) — bỏ qua")
                        bo_qua += 1
                        continue
                    try:
                        with transaction.atomic():
                            truoc = self._max(c, db, bang, cols[0])
                            c.execute(_cau_update(db, bang, cols, +1))
                            n = c.rowcount
                            sau = self._max(c, db, bang, cols[0])
                            if che_do == "ghi":
                                c.execute(f"INSERT INTO {BANG_DANH_DAU} (db, bang, so_dong, luc) VALUES (%s,%s,%s,NOW())",
                                          [db, bang, n])
                            else:
                                transaction.set_rollback(True)
                    except Exception as e:  # 1 bảng lỗi: transaction bảng đó tự rollback, dừng hẳn để GĐ xem
                        raise CommandError(f"{db}.{bang}: {e} — bảng này KHÔNG đổi; các bảng trước "
                                           f"{'đã lưu + đánh dấu' if che_do == 'ghi' else 'đều đã rollback'}.")
                    tong_bang += 1
                    tong_dong += n
                    mau = f"{cols[0]} max {truoc:%d/%m %H:%M} → {sau:%d/%m %H:%M}" if truoc else f"{cols[0]} trống"
                    self.stdout.write(f"  + {bang:<45} {n:>8} dòng · {len(cols)} cột · {mau}")

            self.stdout.write(self.style.SUCCESS(
                f"\n{nhan}: {tong_bang} bảng · {tong_dong:,} dòng".replace(",", ".")
                + (f" · {bo_qua} bảng đã đổi từ trước" if bo_qua else "")))
            if che_do == "thu":
                self.stdout.write("Chưa lưu gì. Lưu thật: TẮT HẾT hệ rồi chạy  manage.py doi_gio_vn --ghi")
            else:
                self.stdout.write(self.style.WARNING(
                    "Nhớ: đặt DATABASES TIME_ZONE='Asia/Ho_Chi_Minh' (KHBL default · KHJ default + 'oa') TRƯỚC khi bật "
                    "lại, và khởi động lại KHCD."))

    def _lui(self, c, da):
        if not da:
            self.stdout.write("Chưa có bảng nào được đánh dấu đã đổi — không có gì để lùi.")
            return
        self.stdout.write(self.style.MIGRATE_HEADING(f"=== doi_gio_vn --lui — trừ {GIO} giờ {len(da)} bảng ==="))
        tong = 0
        for db, bangs in COT.items():
            for bang, cols in bangs.items():
                if (db, bang) not in da:
                    continue
                with transaction.atomic():
                    c.execute(_cau_update(db, bang, cols, -1))
                    n = c.rowcount
                    c.execute(f"DELETE FROM {BANG_DANH_DAU} WHERE db=%s AND bang=%s", [db, bang])
                tong += n
                self.stdout.write(f"  - {db}.{bang:<45} {n:>8} dòng")
        self.stdout.write(self.style.SUCCESS(f"Đã lùi {len(da)} bảng · {tong} dòng. Nhớ trả DATABASES TIME_ZONE về cũ."))
