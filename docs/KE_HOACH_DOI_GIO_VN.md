# KẾ HOẠCH ĐỔI TOÀN BỘ DB SANG GIỜ VIỆT NAM (UTC → Asia/Ho_Chi_Minh)

> Bước 1 — CHỈ LÀ KẾ HOẠCH + DANH SÁCH, CHƯA ĐỤNG DỮ LIỆU. Lập 20/09/2026, chờ GĐ duyệt.
> Phạm vi: DB `khj_bl` (KHBL) + `khj_hr` (KHJ) trên MySQL 8 @3308. KHCD (`khj_cd`) và máy KK vốn đã là giờ VN.
>
> **TIẾN ĐỘ 20/09/2026 (bước 2 — GĐ duyệt "sửa hết"):**
> - ✅ `zalo_messages` id 6·7·8 đã +7h.
> - ✅ Lỗi có sẵn mục 5 đã sửa: `pmv/behavior_log.py` `mau_dong_doi` quy mốc aware về giờ VN trước khi so với máy KK;
>   `pmv/views.py:270` nhãn bản chụp dùng `localtime`; KHCD `khcd/auth.py` `last_login` ghi `domain.now()` (giờ VN) —
>   `khj_cd.auth_user` (last_login, date_joined) đưa vào đợt đổi. Có hiệu lực sau RESET KHBL / khởi động lại KHCD.
> - ✅ Lệnh `apps/pos/management/commands/doi_gio_vn.py` (`--thu` mặc định · `--ghi` · `--lui`; `--ghi/--lui` từ chối khi
>   web 8000/8100 còn chạy). Chạy THỬ 02:17 ngày 20/09: **90 bảng · 445.039 dòng · 12,8 giây**, không lỗi trùng khóa,
>   mốc sau đổi khớp giờ VN thực (`devices.last_seen_at` → 02:17 đúng lúc chạy). Chạy lệnh phải có `PYTHONUTF8=1`.
> - ✅ **ĐÃ CHUYỂN 02:25–02:36 ngày 20/09/2026** (GĐ: "tiệm đang không hoạt động -> làm ngay"): backup 3 DB
>   `D:\PYTHON\KHJ\backups\TRUOC_DOI_GIO_20260920` → tắt KHJ + KHBL + KHCD (kể cả `run_customer_bridge`) → KHJ
>   `ghi_so._utc_naive` → `_gio_vn_naive` (tự làm, phiên KHJ OA đứng yên từ 19/09 16:42) + ghi chú "UTC" 2 dự án →
>   `DATABASES TIME_ZONE` 3 chỗ → `doi_gio_vn --ghi`: 90 bảng · 445.039 dòng → kiểm: `raw_attendance_events.event_time`
>   TRÙNG KHÍT `dateTime …+07:00` gốc máy chấm công; zalo id 6·7·8 không đổi; lọc `__date` ra 218 hóa đơn 19/09 →
>   smoke_oa 26 · smoke_engine · smoke_leave_balance 41 · smoke_oa_messages 206 PASS → bật lại cả 3 qua WMI.
>   Kèm: thêm loại tin `khbl_deposit` (chỉ xem) vào KHJ OA — smoke_oa_messages từng FAIL 1 vì thiếu nhãn này.
>   Nhãn verbose_name "(UTC)" của model KHJ `oa_messages` CỐ Ý giữ (đổi là sinh migration, không trang nào hiện).

## 1. Hiện trạng đã đo

| | Cách lưu hiện nay | Hiển thị trên web |
|---|---|---|
| Mọi bảng Django của KHBL + KHJ (+ `zalo_templates`, `zalo_send_rules`) | **UTC naive** (VN − 7h) | đúng giờ VN (Django tự đổi) — chỉ phpMyAdmin nhìn thô là lệch |
| `bank_notifications`, `gold_prices`, `zalo_messages` (từ 20/09), `khj_cd.*`, máy KK | **giờ VN** | đúng |

Kiểm tra DDL: **không có** cột kiểu `timestamp` (toàn `datetime` — MySQL không tự quy đổi), **không có** trigger / event / view
trong `khj_bl`, `khj_hr`. Không hệ nào khác (KIMHANH dùng `pmv_report` @3306; BANLE_V5 chỉ đọc cột không phải giờ của `gold_bill`)
đọc cột giờ của hai DB này.

## 2. Việc sẽ làm

**Dữ liệu** — cộng **+7 giờ** cho **164 cột / 89 bảng / ~445.000 dòng** (Phụ lục A). Không đụng 3 bảng đã là giờ VN (Phụ lục B).

**Cấu hình** (cùng một lần triển khai, không tách):
- KHBL `config/settings/base.py`: `DATABASES["default"]["TIME_ZONE"] = "Asia/Ho_Chi_Minh"`.
- KHJ `config/settings/base.py`: như trên cho `default` **và** alias `"oa"` (trong khối `if OA_DB_ENABLED:`).
- ⚠ Phải ghi đúng chuỗi `"Asia/Ho_Chi_Minh"` (không `"+07:00"`, không `"Asia/Saigon"`): Django chỉ bỏ `CONVERT_TZ` khi tên múi
  của kết nối TRÙNG tên `TIME_ZONE`; khác tên là nó sinh `CONVERT_TZ`, mà MySQL máy này chưa nạp bảng múi giờ ⇒ lọc theo ngày ra RỖNG.
  Được lợi thêm: lookup `__date` của Django dùng được (bẫy "MySQL chưa nạp bảng timezone" trong CLAUDE.md KHJ hết hiệu lực).

**Code phải sửa cùng lượt** (đã rà toàn bộ SQL thô của KHBL, KHJ, KHCD, KIMHANH, BANLE_V5):

| # | Chỗ | Vì sao |
|---|---|---|
| 1 | KHJ `apps/oa_messages/ghi_so.py:99-105` `_utc_naive` (dùng ở INSERT thô tạo tin tay) | đang ghi UTC vào `zalo_messages` (giờ VN) — đổi sang giờ VN. **Phiên riêng "KHJ OA: đổi sổ zalo_messages sang giờ VN" đang sửa đúng chỗ này** — chờ phiên đó xong rồi triển khai chung |
| 2 | KHBL `apps/pos/money_flow_views.py:174`, `pawn_bank_reconcile.py:77`, `_money_flow_in_transfer.html:10` | mã phiên bản = `updated_at.isoformat()` đổi đuôi `+00:00` → `+07:00`: trang đang mở lúc chuyển báo "QR đã thay đổi" 1 lần, tải lại là hết. **Đề xuất: chấp nhận** (không sửa) |
| 3 | Ghi chú "UTC" lỗi thời: KHBL `apps/oa/models.py`, `xep_hang.py:228-229`; KHJ `oa_messages/models.py:49-52` + nhãn "(UTC)"; `ghi_so.py:23`; CLAUDE.md hai dự án | sửa chữ cho khớp |
| 4 | `GioVNField` (KHBL `apps/oa/models.py`) | sau khi đổi thì thành vô hại (no-op) — **giữ lại** làm lớp bảo vệ cho `zalo_messages` |

Không tìm thấy chỗ nào tự cộng/trừ 7 giờ, dùng `CONVERT_TZ`/`UTC_TIMESTAMP`/`NOW()` trên bảng Django, hay so giờ thô sai kiểu.
KHJ chỉ dùng ORM (trừ #1); các khoảng lọc ngày đều là `[start, end)` aware ⇒ tự đúng sau khi đổi.

## 3. Cách chạy (bước 2 sẽ viết lệnh, bước 3 mới chạy thật)

Lệnh riêng `manage.py doi_gio_vn` (đặt ở KHBL, kết nối được cả hai DB):
- Danh sách cột lấy **đúng Phụ lục A** (đóng cứng trong lệnh — không tự dò lúc chạy để khỏi lỡ tay dính bảng giờ VN).
- Mỗi bảng MỘT câu `UPDATE … SET c1 = c1 + INTERVAL 7 HOUR, c2 = …` (cột NULL giữ NULL), trong transaction riêng.
- Bảng đánh dấu `khj_bl.tz_doi_gio_vn(db, bang, so_dong, luc)` — bảng nào đã đổi thì **không bao giờ cộng lần hai**
  (chạy lại / đứt giữa chừng đều an toàn); có chế độ `--lui` trừ 7 giờ đúng những bảng đã đánh dấu.
- ⚠ `khj_hr.raw_attendance_events` có khóa duy nhất (device, employee_no, event_time): phải `UPDATE … ORDER BY event_time DESC`,
  không thì hai lượt quẹt cách nhau đúng 7 giờ (08:00 và 15:00) làm câu lệnh nổ "Duplicate entry".
- `--thu` (mặc định): chạy thật trong transaction rồi ROLLBACK, in số dòng + mẫu trước/sau từng bảng. `--ghi` mới lưu.
- Kèm: sửa 3 tin đặt cọc `zalo_messages` id 6·7·8 (+7h — do code cũ tạo lúc 01:25 ngày 20/09, trước khi web khởi động lại 01:39).

## 4. Quy trình đêm chuyển (sau 21h, tiệm đã đóng)

1. **Backup** cả `khj_bl` và `khj_hr` (`backup_db` KHJ + backup KHBL), đặt nhãn `TRUOC_DOI_GIO`. ⚠ Từ đây về sau **không được
   phục hồi bản backup cũ hơn mốc này** mà không trừ 7 giờ (bản cũ là UTC).
2. **TẮT HẾT** KHBL + KHJ: web, scheduler (cả hai), watchdog (TRƯỚC), `isapi_worker` (poll MCC), `mssql_sync`. Máy chấm công vẫn
   giữ sự kiện — poll 2 phút lấy bù khi bật lại. KHCD không cần tắt (không đụng `khj_cd`).
3. Triển khai cấu hình + code (mục 2).
4. `doi_gio_vn --thu` → xem số liệu → `doi_gio_vn --ghi`. Ước tính vài phút (~445.000 dòng; bảng lớn nhất `pmv_audit_logs` ~315.000).
5. **Kiểm** trước khi bật:
   - `gold_bill.created_at` của vài hóa đơn hôm nay ≈ `TRN_RT_BUYSELL.CreatedDate` trên máy KK (cùng giờ VN).
   - `raw_attendance_events.event_time` ≈ giờ quẹt trong `raw_json` của máy chấm công.
   - `SELECT MAX(created_at)` các bảng hoạt động nhiều ≈ `NOW()`.
6. **Bật lại** (KHBL qua WMI như mọi lần), kiểm: Bán hàng, Thâu vào 2, Đặt-cọc/Gửi SMS, Chuyển khoản, Lịch sử MCC, Bảng công,
   Nghỉ phép, OA Tin nhắn — giờ hiển thị không đổi so với trước; phpMyAdmin nay thấy đúng giờ VN.
7. Chạy bộ hồi quy: KHBL `smoke_oa` + bộ kiểm SQLite; KHJ `smoke_engine`, `smoke_leave_balance`, `smoke_oa_messages`.

**Thời gian dừng hệ dự kiến: 15–30 phút.**

**Lùi lại nếu có sự cố:** `doi_gio_vn --lui` (trừ 7 giờ đúng các bảng đã đánh dấu) + trả cấu hình cũ; nặng nhất thì phục hồi
bản backup `TRUOC_DOI_GIO` của cả hai DB.

## 5. Cần GĐ quyết thêm (không bắt buộc cho đợt đổi)

- KHCD `khj_cd.auth_user.last_login` đang ghi UTC (`khcd/auth.py:115`) — trái quy ước "khj_cd là giờ VN"; không trang nào hiện.
  Sửa luôn cho đồng bộ?
- KHBL lỗi có sẵn không do đợt đổi: nhãn giờ bản chụp PMV (`apps/pmv/views.py:270`) hiện giờ UTC; cửa sổ "Đánh dấu" hành vi
  PMV (`pmv/views.py:349`, `behavior_log.py:272`) lệch 7 giờ. Sửa chung đợt này?

## PHỤ LỤC A — CỘT SẼ CỘNG +7 GIỜ (UTC → giờ VN)


### `khj_bl` — 37 bảng · 69 cột · 403.898 dòng

| Bảng | Số dòng | Chủ | Cột |
|---|---:|---|---|
| `auth_user` | 35 | KHBL:auth.User | last_login, date_joined |
| `bank_reconcile_state` | 163 | KHBL:pos.BankReconcileState | next_attempt, updated_at |
| `bill_audit` | 3.646 | KHBL:pos.BillAudit | created_at |
| `customer_bridge_receipt` | 2 | KHBL:pos.CustomerBridgeReceipt | updated_at |
| `customer_sync_receipt` | 4.384 | KHBL:pos.CustomerSyncReceipt | updated_at |
| `django_admin_log` | 0 | KHBL:admin.LogEntry | action_time |
| `django_migrations` | 68 | ngoài Django — chứa UTC chép từ bảng Django | applied |
| `django_session` | 606 | KHBL:sessions.Session | expire_date |
| `gold_bill` | 3.449 | KHBL:pos.DepositOrderState/KHBL:pos.GoldBill | synced_at, created_at, updated_at, delivered_at, last_contact, next_contact, ready_at |
| `gold_price_batches` | 47 | KHBL:pos.PriceBatch | created_at, synced_at |
| `mobile_employee_identity` | 32 | KHBL:pos.MobileEmployeeIdentity | created_at |
| `money_flow` | 7.240 | KHBL:pos.MoneyFlow | synced_at, created_at |
| `money_flow_bank_receipt` | 11 | KHBL:pos.MoneyFlowBankReceipt | created_at |
| `money_flow_payment` | 67 | KHBL:pos.MoneyFlowPayment | created_at, updated_at |
| `money_flow_source_write` | 11 | KHBL:pos.MoneyFlowSourceWrite | created_at, updated_at |
| `pmv_audit_logs` | 314.910 | KHBL:pmv.PmvAudit | created_at |
| `pmv_behavior_logs` | 62.679 | KHBL:pmv.PmvBehavior | event_time, collected_at |
| `pmv_change_logs` | 4.870 | KHBL:pmv.PmvChange | window_start, window_end, created_at |
| `pmv_process` | 5 | KHBL:pmv.PmvProcess | created_at, updated_at |
| `pmv_snapshots` | 22 | KHBL:pmv.PmvSnapshot | created_at |
| `pmv_state` | 30 | KHBL:pmv.PmvState | updated_at |
| `pmv_web_users` | 3 | KHBL:pmv.PmvWebUser | updated_at |
| `pos_depositevent` | 217 | KHBL:pos.DepositEvent | created_at |
| `pos_depositmessage` | 0 | KHBL:pos.DepositMessage | scheduled_at, created_at, sent_at, claimed_at |
| `pos_depositmessagetemplate` | 2 | KHBL:pos.DepositMessageTemplate | updated_at |
| `pos_depositmoneyoperation` | 65 | KHBL:pos.DepositMoneyOperation | created_at, completed_at |
| `pos_depositorderstate` | 3 | ngoài Django — chứa UTC chép từ bảng Django | next_contact, last_contact, ready_at, delivered_at, updated_at |
| `pos_depositstockhold` | 0 | KHBL:pos.DepositStockHold | created_at, released_at |
| `pos_depositsubmission` | 44 | KHBL:pos.DepositSubmission | created_at |
| `sys_users` | 2 | KHBL:pmv.PmvUser | synced_at |
| `thau_anh_tam` | 0 | KHBL:pos.ThauAnhTam | created_at |
| `thau_nhom` | 1.015 | KHBL:pos.ThauNhom | created_at |
| `thau_payment_link` | 201 | KHBL:pos.ThauPaymentLink | created_at, revoked_at |
| `unlock_passcodes` | 2 | KHBL:pos.UnlockPasscode | updated_at |
| `user_module_access` | 60 | KHBL:pmv.UserModuleAccess | updated_at |
| `zalo_send_rules` | 2 | KHBL:oa.ZaloSendRule(unmanaged)/KHJ:oa_messages.ZaloSendRule(unmanaged) | effective_from, last_scan_at, created_at, updated_at |
| `zalo_templates` | 5 | KHBL:oa.ZaloTemplate(unmanaged)/KHJ:oa_messages.ZaloTemplate(unmanaged) | zalo_created_at, last_synced_at, created_at, updated_at |

### `khj_hr` — 52 bảng · 95 cột · 41.127 dòng

| Bảng | Số dòng | Chủ | Cột |
|---|---:|---|---|
| `access_rules` | 22 | KHJ:accounts.AccessRule | updated_at |
| `attendance_daily` | 1.857 | KHJ:attendance.AttendanceDaily | check_in, check_out, updated_at |
| `audit_logs` | 1.504 | KHJ:common.AuditLog | created_at |
| `calendar_notes` | 0 | KHJ:calendar_app.CalendarNote | created_at |
| `day_time_off` | 1 | KHJ:attendance.DayTimeOff | created_at |
| `departments` | 8 | KHJ:employees.Department | created_at, updated_at |
| `devices` | 2 | KHJ:attendance.Device | last_seen_at, last_event_at, created_at, updated_at |
| `diligence_config` | 1 | KHJ:payroll.DiligenceConfig | created_at |
| `django_admin_log` | 0 | KHJ:admin.LogEntry | action_time |
| `django_migrations` | 78 | ngoài Django — chứa UTC chép từ bảng Django | applied |
| `django_session` | 689 | KHJ:sessions.Session | expire_date |
| `employee_benefits` | 35 | KHJ:payroll.EmployeeBenefit | created_at, updated_at |
| `employee_portal_calendar_revision` | 1 | KHJ:employee_portal.CalendarRevision | updated_at |
| `employee_portal_daily_admissions` | 521 | KHJ:employee_portal.PortalDailyAdmission | created_at, expires_at, used_at |
| `employee_portal_devices` | 0 | KHJ:employee_portal.EmployeeDevice | enrolled_at, last_seen_at, expires_at, revoked_at |
| `employee_portal_events` | 19 | KHJ:employee_portal.PortalEvent | created_at, updated_at |
| `employee_portal_khbl_grants` | 34 | KHJ:employee_portal.KhblAuthGrant | expires_at, opened_at, consumed_at, created_at |
| `employee_portal_lan_challenges` | 0 | KHJ:employee_portal.PortalLanChallenge | created_at, expires_at, used_at |
| `employee_portal_leave_submissions` | 35 | KHJ:employee_portal.PortalLeaveSubmission | created_at |
| `employee_portal_pairings` | 0 | KHJ:employee_portal.DevicePairingSession | created_at, expires_at, used_at, last_attempt_at, locked_at |
| `employee_portal_passkeys` | 33 | KHJ:employee_portal.PortalPasskeyCredential | created_at, last_used_at, revoked_at |
| `employees` | 36 | KHJ:employees.Employee | created_at, updated_at |
| `employees_sync_ghg_backup_20260821_192328` | 29 | ngoài Django — chứa UTC chép từ bảng Django | created_at, updated_at |
| `feedbacks` | 0 | KHJ:common.Feedback | created_at |
| `insurance_config` | 1 | KHJ:payroll.InsuranceConfig | created_at |
| `inventory_snapshot` | 54 | KHJ:kpi.InventorySnapshot | created_at |
| `kk_sync_state` | 1 | KHJ:kpi.KkSyncState | last_run_at, last_success_at |
| `kpi_periods` | 2 | KHJ:kpi.KpiPeriod | created_at |
| `kpi_scores` | 0 | KHJ:kpi.KpiScore | updated_at |
| `leave_balances` | 36 | KHJ:leave.LeaveBalance | updated_at |
| `leave_day_decisions` | 702 | KHJ:leave.LeaveDayDecision | created_at |
| `leave_requests` | 341 | KHJ:leave.LeaveRequest | approved_at, created_at, updated_at, signed_at, submitted_at, zalo_notify_scheduled_at |
| `leave_zalo_notifications` | 38 | KHJ:leave.LeaveZaloNotification | scheduled_at, next_attempt_at, synced_at, created_at, updated_at |
| `overtime_config` | 1 | KHJ:payroll.OvertimeConfig | created_at |
| `payroll_periods` | 1 | KHJ:payroll.PayrollPeriod | locked_at, created_at |
| `payroll_slips` | 36 | KHJ:payroll.PayrollSlip | updated_at |
| `positions` | 10 | KHJ:employees.Position | created_at, updated_at |
| `raw_attendance_events` | 27.237 | KHJ:attendance.RawAttendanceEvent | event_time, created_at |
| `revenue_bonus_apply` | 14 | KHJ:payroll.RevenueBonusApply | updated_at |
| `revenue_bonus_config` | 1 | KHJ:payroll.RevenueBonusConfig | created_at |
| `revenue_records` | 32 | KHJ:kpi.RevenueRecord | created_at, updated_at |
| `roster_assignments` | 4.085 | KHJ:attendance.RosterAssignment | created_at |
| `salary_advances` | 7 | KHJ:payroll.SalaryAdvance | created_at |
| `salary_scales` | 13 | KHJ:payroll.SalaryScale | created_at |
| `salary_step_adjustments` | 2 | KHJ:payroll.SalaryStepAdjustment | created_at |
| `salary_structures` | 35 | KHJ:payroll.SalaryStructure | created_at |
| `shift_attendance` | 3.471 | KHJ:attendance.ShiftAttendance | check_in, check_out, updated_at |
| `shift_group_assignments` | 65 | KHJ:attendance.ShiftGroupAssignment | created_at |
| `shift_groups` | 8 | KHJ:attendance.ShiftGroup | created_at |
| `shift_plans` | 22 | KHJ:attendance.ShiftPlan | created_at |
| `tax_config` | 2 | KHJ:payroll.TaxConfig | created_at |
| `users` | 5 | KHJ:accounts.User | last_login, date_joined |

## PHỤ LỤC B — KHÔNG ĐỔI (đã là giờ VN)

| Bảng | Số dòng | Lý do |
|---|---:|---|
| `khj_bl.bank_notifications` | 29.019 | giờ VN — bên ghi là BANLE_V5 (bank_notification_mirror) |
| `khj_bl.gold_prices` | 685 | giờ VN — KHBL prices.py ghi localtime; nguồn KIMHANH gửi giờ VN |
| `khj_bl.zalo_messages` | 3 | giờ VN từ 20/09 (GioVNField) — riêng id 6·7·8 sửa +7h (tạo bởi code cũ lúc 01:25) |
| `khj_cd.*` (toàn bộ KHCD) | — | giờ VN (Flask `now()` VN, kết nối `time_zone '+07:00'`) — không thuộc đợt đổi |
| Máy KK (MSSQL) | — | giờ VN — không đụng |
