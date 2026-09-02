# KHBL — WEBAPP BÁN LẺ | KIM HANH JEWELRY (song song PMVGoldRT)

> File định hướng cho Claude Code. Dự án CAN THIỆP TRỰC TIẾP vào DB phần mềm bán vàng
> PMVGoldRT **đang bán hàng thật mỗi ngày** — mức rủi ro CAO HƠN KHJ HR, mọi thay đổi
> phải qua đúng RULES bên dưới. Toàn bộ UI + commit message bằng **tiếng Việt**.
> KHÔNG sửa code ngoài phạm vi được yêu cầu.

---

## 1. BỐI CẢNH & SỨ MỆNH

- **Kim Hanh Jewelry — chi nhánh Kim Hạnh 2**: bán lẻ vàng, app desktop **PMVGoldRT**
  (.NET, vendor còn hỗ trợ nhưng **không có tài liệu API**) chạy trên 2 máy trạm
  (KK + QQ), DB SQL Server trên PC KK.
- KHBL = webapp Django chạy trên PC Mr Giang (192.168.1.6), **song song** với PMVGoldRT,
  can thiệp trực tiếp DB PMV qua **stored proc của vendor**.
- Nghiệp vụ mục tiêu (GĐ chốt 02/09/2026, theo thứ tự triển khai): **bảng giá vàng →
  customer → product (sửa) → hóa đơn THÂU → BÁN → ĐỔI**. Bán lẻ xuất hóa đơn thường —
  **KHÔNG đụng hóa đơn điện tử** (vùng cấm pháp lý).
- Anh em cùng nhà: **KHJ HR** tại `D:\PYTHON\KHJ` (webapp chấm công/lương, port 8000) —
  kế thừa toàn bộ chuẩn vận hành từ đó, nhưng 2 dự án hoàn toàn độc lập.

## 2. QUYẾT ĐỊNH GĐ ĐÃ CHỐT (02/09/2026)

| Quyết định | Hệ quả cho code |
|---|---|
| Dự án riêng `D:\PYTHON\KHBL`, mới toang | Không đụng gì sang `D:\PYTHON\KHJ` |
| MySQL 8 chung instance **3308**, DB **RIÊNG `khj_bl`** (user `khj_bl`) | MySQL = trạng thái/audit/hàng đợi của web; PMV = nguồn sự thật bán hàng |
| **MSSQL PC KK là dữ liệu CHÍNH**; backup về máy Mr Giang | Job `backup_pmv` 02:00 đêm (COPY_ONLY); sẽ restore sandbox local |
| **Vẫn dùng tài khoản `kimhanh2` (SYSADMIN)** | SQL Server không tự bảo vệ → RULE 1 gateway allowlist + CẢNH BÁO VƯỢT QUYỀN là hàng rào duy nhất |
| Vendor không có tài liệu API | Bản đồ proc tự khảo sát = skill `.claude/skills/pmv-proc-map/` — tài liệu API duy nhất |

## 3. STACK & HẠ TẦNG

| Thành phần | Chi tiết |
|---|---|
| Backend | Python 3.13 (venv) · **Django 5.2 LTS** · MySQL 8 @3308 DB `khj_bl` (`mysqlclient`) · serve **waitress** port **8100** |
| Frontend | Django Templates + HTMX + Tailwind (theo chuẩn KHJ — CRUD mở POPUP, OOB refresh) |
| Job nền | APScheduler (`BlockingScheduler`, tiến trình riêng `manage.py run_scheduler`) |
| PMV | SQL Server **2005 Express SP2** (9.00.3042, compat 90) @ `tcp:192.168.1.206,1430`, DB `PMV_BANLE_KH2`, instance `KK\SQLEXPRESS`, pyodbc + ODBC Driver 18, `Encrypt=no` |
| Sandbox (kế hoạch) | SQL Server **2014 Express** trên máy Mr Giang — bản MỚI NHẤT còn restore được backup 2005; nhận restore `PMV_SANDBOX` |
| Secret | tất cả trong `.env` (django-environ) — không bao giờ commit |

## 4. KIẾN TRÚC — 2 TẦNG DỮ LIỆU

```
PMVGoldRT (2 máy trạm, sa) ──┐
                             ├──> SQL Server 2005 @ PC KK  (NGUỒN SỰ THẬT bán hàng)
KHBL web (Mr Giang) ─────────┘         │ backup COPY_ONLY 02:00
        │                              v
        └──> MySQL khj_bl @3308     .bak trên PC KK ──(share, khi có)──> D:\KHBL_BACKUP\pmv
             (audit, trạng thái,                                          │ restore
              hàng đợi, dữ liệu web)                                      v
                                                            SQL 2014 Express: PMV_SANDBOX
```

- **MỌI lệnh sang PMV đi qua `apps/pmv/gateway.py`** — 3 kênh: `pmv_read` (chỉ SELECT),
  `pmv_admin` (backup/tiện ích theo allowlist), `pmv_exec` (GHI qua proc vendor — **CHƯA MỞ**,
  chỉ mở ở GĐ3 sau khi sandbox kiểm chứng + GĐ duyệt).
- Lệnh ngoài allowlist → exception `PmvBlocked` + dòng audit **BLOCKED (vượt quyền)** hiện
  ĐỎ trên trang trạng thái `http://localhost:8100/`.
- Web không bao giờ query PMV nặng trong request cycle người dùng — đọc báo cáo lấy từ
  MySQL/cache, PMV chỉ cho tra cứu nhanh + lệnh nghiệp vụ.

## 5. RULES BẮT BUỘC (vi phạm = hỏng dữ liệu tiệm vàng thật)

1. **GATEWAY DUY NHẤT**: không import `pyodbc` ngoài `apps/pmv/gateway.py` (ngoại lệ duy
   nhất: `_restore_sandbox` trong backup_pmv — server LOCAL). Muốn lệnh mới → thêm vào
   allowlist gateway kèm lý do, không "đi tắt".
2. **CHƯA MỞ KÊNH GHI**: `pmv_exec` chưa tồn tại. GĐ3 mới code, và chỉ gọi STORED PROC
   vendor trong allowlist (xem skill pmv-proc-map) — **không bao giờ INSERT/UPDATE thẳng
   vào bảng PMV** (né sạch logic tồn kho/sổ quỹ/audit của app).
3. **SQL tương thích 2005 (compat 90)**: không MERGE, không kiểu DATE/TIME — tham số ngày
   truyền **chuỗi ISO** `'YYYY-MM-DD'`. Đọc bảng dữ liệu PHẢI `WITH (NOLOCK)` (2 máy trạm
   đang bán hàng, tuyệt đối không giữ lock).
4. **VÙNG CẤM**: mọi thứ liên quan hóa đơn điện tử (cột `MAHDDienTu/InvoiceGUID/SoHDDienTu...`,
   proc `HoaDonDienTu_UpdateMa`, bảng `SYS_INVOICE*`, `I_DichVuHDDT`) — KHÔNG đụng.
   Proc hủy diệt của vendor (`Del_AllData*`, `Del_OldData*`, `ChuyenKyKinhDoanh`,
   `DropDefaultConstraints`) — KHÔNG bao giờ nằm trong allowlist.
5. **MÃ SỐ do vendor sinh**: TrnID/BillCode/CustID... sinh trong proc (`SYS_BILL_COUNTER_Gen`,
   `SYS_CodeMasters_Gen`, fn `GenstrID`) — web không tự bịa mã, không đọc-rồi-cộng-1.
6. **BACKUP TRƯỚC KHI GHI**: mọi tính năng ghi go-live phải có bản backup PMV ngay trước đó;
   job đêm `backup_pmv` phải xanh (kiểm tra trang trạng thái).
7. **CIRCUIT BREAKER**: `check_pmv` 30 phút/lần lấy vân tay `tbh_VersionDB`; vendor nâng cấp
   DB → `pmv_write_lock=1` tự bật, mọi hướng ghi phải dừng cho tới khi rà lại bản đồ proc.
8. **Thử trên SANDBOX trước**: tính năng ghi mới phải chạy trên `PMV_SANDBOX` (restore từ
   backup) và **diff kết quả từng bảng** với thao tác cùng nghiệp vụ trên app PMVGoldRT thật,
   khớp mới được go-live. Viết smoke test theo truyền thống KHJ.
9. **SỬA GÌ VỀ PMV PHẢI ĐỌC SKILL TRƯỚC**: `.claude/skills/pmv-proc-map/`.

## 6. DATABASE

- **MySQL `khj_bl`** (utf8mb4): `pmv_audit_logs` (mọi lời gọi PMV, kể cả BLOCKED/CANHBAO)
  · `pmv_state` (key-value: lần backup cuối, vân tay version, write_lock) · bảng Django
  auth/session. Nghiệp vụ web sẽ thêm dần theo GĐ2+.
- **PMV `PMV_BANLE_KH2`**: 251 bảng, 1.049 proc — bản đồ trong skill pmv-proc-map.
  Đặc điểm đã đo (02/09/2026): recovery FULL, data 428MB + log 1.483MB (phình),
  collation `SQL_Latin1_General_CP1_CI_AS` (tên Việt ở cột nvarchar),
  đồng hồ PC KK chậm ~37s so máy web.

## 7. VẬN HÀNH SERVER

Hệ chạy = **3 tiến trình ẨN** (qua `run_hidden_khbl.vbs`): web waitress port **8100**
(threads=4) + scheduler + watchdog (60s/lần gọi lại TURN_ON). Xem trạng thái: port 8100
(`netstat`) hoặc `http://localhost:8100/` hoặc logs.

| File tại gốc | Chức năng |
|---|---|
| `TURN_ON_KHBL.bat` | Bật web + scheduler + watchdog. Chờ MySQL80, chống bật trùng từng tiến trình |
| `TURN_OFF_KHBL.bat` | Tắt watchdog TRƯỚC rồi web + scheduler |
| `RESET_KHBL.bat` | Tắt → chờ 3s → bật (sau khi sửa file `.py`) |
| `WATCHDOG_KHBL.bat` | Vòng canh gác — KHÔNG chạy tay |
| `run_hidden_khbl.vbs` | Chạy lệnh ẩn hoàn toàn — KHÔNG chạy tay |

- Mặc định **PROD** (`config.settings.prod` trong manage.py + wsgi.py, DEBUG=False).
  Dev tạm: `set DJANGO_SETTINGS_MODULE=config.settings.dev`.
- Job nền: **02:00 backup_pmv** (KHJ HR backup 01:30 cùng máy — né giờ nhau) ·
  **30 phút check_pmv**.
- Bài học KHJ áp nguyên: `.bat` CRLF + ASCII, không `timeout` (dùng `ping -n`);
  KHÔNG gọi bat từ Git Bash (gọi `cmd /c ...` qua PowerShell); `PYTHONUTF8=1`
  + `stream.reconfigure(errors="replace")` chống crash cp1252; template sửa bằng
  Edit/Write tool, đừng replace bằng PowerShell (hỏng UTF-8).

## 8. LỘ TRÌNH (GĐ duyệt 02/09/2026)

| GĐ | Nội dung | Trạng thái |
|---|---|---|
| **GĐ0** | Vành đai an toàn: skeleton + gateway allowlist/cảnh báo vượt quyền + audit + backup đêm COPY_ONLY + check version vendor + trang trạng thái + CLAUDE.md/skill. **SQL 2014 Express instance `localhost\SQL2014` GĐ đã cài (02/09)**; **GĐ chốt KHÔNG tạo share PC KK** — file `.bak` chép TAY về `D:\KHBL_BACKUP\pmv` khi cần refresh sandbox, lệnh `manage.py restore_sandbox [--file x.bak]` restore vào `PMV_SANDBOX` (lần đầu 02/09: 35 NV · 18.305 HĐ · 37.935 SP — khớp bản thật) | ✅ 02/09/2026 — chờ nghiệm thu |
| GĐ1 | Bộ so sánh diff 2 DB (sandbox trước/sau thao tác — nền tảng kiểm chứng proc ghi) | ⏳ |
| GĐ2 | Khung web nghiệp vụ: auth + layout KHJ + màn tra cứu ĐỌC (bảng giá, khách, hàng, hóa đơn trong ngày) | ⏳ |
| GĐ3 | Mở kênh GHI `pmv_exec` từng nghiệp vụ trên SANDBOX: bảng giá → customer → product sửa → HĐ thâu → bán → đổi (chuỗi `*_Ins` → `CARDPAY_Ins` → `*_Complete`) — mỗi cái 1 bộ smoke + diff | ⏳ |
| GĐ4 | Go-live từng chức năng, có công tắc riêng, đối soát cuối ngày với PMVGoldRT | ⏳ |

**Việc treo**: hỏi vendor về bộ proc `*_Mobile_Ins`/`*_API` (có phải cổng tích hợp
chính thức?) — GĐ hỏi khi tiện, không chặn tiến độ.

## 9. UI & THƯƠNG HIỆU

Theo đúng chuẩn KHJ: heading **Cormorant Garamond**, body **Be Vietnam Pro**;
nền `#FDFCF9`, sidebar `#1C1917`, viền `#EAE4D6`; vàng kim `gold-700 #8C6A1D` ·
`gold-500 #C9A02C` · `gold-100 #F7EFD8`; xanh `#2E7D46` / đỏ `#B3402A` / cam `#C07A1A`.
Tiền `1.234.567 ₫` · ngày `dd/mm/yyyy` · toàn bộ tiếng Việt · UI "nhìn là hiểu" ·
**mọi CRUD mở POPUP** (nguyên tắc GĐ).

## 10. LƯU Ý KỸ THUẬT ĐÃ KIỂM CHỨNG

- PMV là **SQL 2005**, không phải 2008 (CLAUDE.md của KHJ ghi 2008 là sai — đã đo
  `@@VERSION` 02/09/2026). Mọi hạn chế cú pháp theo 2005.
- SQL 2005 backup chỉ restore được lên 2005→2014. **SQL 2014 Express là bản mới nhất
  dùng được** — vì vậy sandbox không dùng 2019/2022.
- `BACKUP DATABASE ... WITH COPY_ONLY` để không phá chuỗi backup của vendor (vendor
  backup tay vào `D:\PHANMEMVANG\BACKUP`, bản gần nhất 04/06/2026 — rất thưa).
- BACKUP/RESTORE qua pyodbc: bật `autocommit=True` và rút hết result set
  (`while cur.nextset()`) mới coi là xong lệnh.
- File logic của PMV_BANLE_KH2: `GOLDRTDB` (.mdf) + `GOLDRTDB_log` (.ldf) — dùng cho
  mệnh đề MOVE khi restore sandbox.
- PMV app poll `I_XRATE_GetAll` liên tục → đổi bảng giá qua proc là máy trạm tự thấy.
- `T_EMPLOYEE` bên PMV có cột nhạy cảm `DBUserName/DBPassword` — không bao giờ SELECT *
  bảng này ra UI/log; chỉ lấy `EmpID, EmpName`.
- Share mạng PC KK hiện **Access denied** từ máy Mr Giang — vì vậy `PMV_BACKUP_SHARE`
  còn trống; backup vẫn an toàn trên ổ D PC KK (178GB trống).
