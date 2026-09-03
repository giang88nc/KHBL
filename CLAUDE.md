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
| MySQL 8 chung instance **3308**, DB **RIÊNG `khj_bl`**; **tài khoản CHUNG `khj_admin`** (GĐ chốt 03/09/2026) quản MỌI DB `khj_*` — khj_hr, khj_bl và DB mới sau này (grant theo mẫu `khj\_%`, không phải cấp lại) | MySQL = trạng thái/audit/hàng đợi của web; PMV = nguồn sự thật bán hàng. Quản trị: phpMyAdmin `http://localhost/phpmyadmin/index.php?server=3` đăng nhập `khj_admin` (mật khẩu trong `.env`) |
| **MSSQL PC KK là dữ liệu CHÍNH**; backup về máy Mr Giang | Job `backup_pmv` 02:00 đêm (COPY_ONLY); sẽ restore sandbox local |
| **Vẫn dùng tài khoản `kimhanh2` (SYSADMIN)** | SQL Server không tự bảo vệ → RULE 1 gateway allowlist + CẢNH BÁO VƯỢT QUYỀN là hàng rào duy nhất |
| Vendor không có tài liệu API | Bản đồ proc tự khảo sát = skill `.claude/skills/pmv-proc-map/` — tài liệu API duy nhất |

## 3. STACK & HẠ TẦNG

| Thành phần | Chi tiết |
|---|---|
| Backend | Python 3.13 (venv) · **Django 5.2 LTS** · MySQL 8 @3308 DB `khj_bl` (`mysqlclient`, user chung `khj_admin`; dữ liệu `D:\PYTHON\mysql8\data\khj_bl`) · serve **waitress** port **8100** |
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
| **GĐ1** | Bộ so sánh diff 2 SQL: `apps/pmv/diff.py` (vân tay rows từ sys.partitions + checksum; FAST cho PMV thật/FULL cho sandbox) + model `PmvSnapshot` + trang **`/so-sanh/`** (chọn 2 nguồn PMV/sandbox/snapshot, tô màu bảng khác, lọc "chỉ bảng khác", chụp/xóa snapshot). Kiểm chứng: test_diff 4/4 PASS (UPDATE 1 dòng → checksum bắt đúng 1 bảng dù rows không đổi). **Cách dùng cho GĐ3**: chụp snapshot sandbox FULL TRƯỚC → chạy proc → chụp SAU → so 2 snapshot ra đúng bảng proc đụng | ✅ 02/09/2026 — chờ nghiệm thu |
| **GĐ1+** | **Nút ⟳ SYNC 1 chiều KK → Mr Giang** (`manage.py sync_sandbox`, nút trên `/so-sanh/` + trang trạng thái): SQL KK backup COPY_ONLY ra đĩa local của nó → Mr Giang **hút .bak về qua kết nối SQL** (`OPENROWSET(BULK …, SINGLE_BLOB)` — gateway mở đúng 1 dạng lệnh, giới hạn thư mục `D:\KHJ_PMV_BACKUP`) → restore đè `PMV_SANDBOX`. **KHÔNG cần share, không đổi gì trên KK/AA**. Đo thật 02/09: 50s cho 428MB (hút ~11MB/s LAN); sau sync so PMV thật ↔ sandbox = **251/251 giống, 0 khác**. `.env` `PMV_SYNC_BAK_READ` (tùy chọn) = UNC đọc được file đó thì copy thay vì hút | ✅ 02/09/2026 — chờ nghiệm thu |
| **GĐ0+ LOG HÀNH VI PMVGoldRT** (02/09/2026 — GĐ yêu cầu): bảng `pmv_behavior_logs` (model `PmvBehavior`) + trang **`/hanh-vi/`**. 2 lớp thu thập (`collect_pmv_behavior`, scheduler **2 phút/lần**): **THỐNG KÊ** = đọc `dm_exec_query_stats` → proc tăng lượt gọi (`exec_delta`), luôn chạy, chỉ đọc · **TRACE** = server-side trace SQL 2005 (`manage.py pmv_trace start|stop|status`, nút Bật/Tắt trên trang): event RPC:Completed(10) + SQL:BatchCompleted(12) kèm **tham số thật**, lọc tại SQL DB=PMV + bỏ login kimhanh2, file `D:\KHJ_PMV_BACKUP\trace\pmv_behavior*.trc` xoay vòng **5×20MB** (SQL tự xóa cũ nhất), đọc về bằng `fn_trace_gettable` theo `EventSequence` (state `pmv_trace_last_seq`). Bộ phân loại `apps/pmv/classify.py`: NHÓM (HĐ bán/thâu/đổi, Khách hàng, Bảng giá, Sản phẩm/kho, Sổ quỹ, Nhật ký ngày, Đồng bộ, HĐ điện tử, Hệ thống…) + HÀNH ĐỘNG (ghi/đọc/hệ thống); `NOISE` = proc app poll liên tục (GetAll, đăng nhập, khuyến mãi…) bỏ ở CẢ 2 lớp. Gateway kênh mới `pmv_trace_batch` chỉ nhận đúng khuôn `sp_trace_*` (file khóa trong thư mục trace). UI: thẻ tổng quan, thanh nhóm bấm lọc, top proc, bộ lọc ngày/nhóm/hành động/nguồn/tìm, dòng thời gian (tham số mở rộng), nút Bật/Tắt trace + Thu thập ngay + tự làm mới 30s | ✅ 02/09/2026 — trace đang BẬT, chờ nghiệm thu |
| **TRACK C — MÀN HÌNH BÁN HÀNG** (03/09/2026): hệ thiết kế **"MẶT KÍNH"** — vùng làm việc SÁNG kẹp giữa 2 dải TỐI (bảng giá đỉnh · dải quyết toán đáy), chọn qua hội đồng 3 phương án × 3 giám khảo. `static/css/khbl.css` (token → base → layout → component → trang → in; **cấm hardcode hex trong template**, chữ trên dải tối dùng bản `*-glow` ≥5:1) · font **tự host** `static/fonts/` (mất mạng không đổi font giữa ca) · htmx + `static/js/khbl.js` (1 hàm `khblBind`) · `templates/base.html` rail 64px + `#modal-root`/`#toast-root`. App **`apps/pos`**: `services.py` (đọc PMV: quét mã qua proc vendor lấy nguyên câu lỗi P-002/P-017, tìm hàng, khách, bảng giá cache 5s, hóa đơn ngày) · `cart.py` (giỏ trong session — F5 không mất) · 5 màn: **`/` Mua bán** (quét → thẻ lớn 1 món / bảng ≥2 món, ngăn THÂU VÀO cùng hóa đơn, dải quyết toán 8 khối, nhãn tự đổi **"Tiệm trả lại khách"** khi PayAmount<0) · **`/thau/`** · **`/bang-gia/`** · **`/khach-hang/`** · **`/hoa-don/`**. Realtime = **poll nhẹ + HTTP 204** (KHÔNG long-poll: waitress ít thread + nguồn là MSSQL qua LAN): bảng giá 15s theo chữ ký sha1, hóa đơn 3s. Smoke: `smoke_pmv_money` **11/11** + smoke UI **19/19** PASS. ⚠ v1 CHƯA GHI: nút Lưu dùng `.khbl-btn--cho` (vân sọc, không toast xanh) → popup phiếu tạm để nhập tay sang PMVGoldRT | ✅ 03/09/2026 — chờ nghiệm thu |
| **TRACK C-2 — CẤU TRÚC URL `/banle/` + TỔNG QUAN + KHÁCH HÀNG** (03/09/2026, GĐ chốt): mọi màn dời vào **`/banle/…`** (`ban-hang` · `thau-vao` · `khach-hang` · `bang-gia` · `hoa-don`); **`/` và `/banle/` = TỔNG QUAN**: 4 thẻ KPI (bán/thâu/khách mới/hàng tồn hôm nay) · biểu đồ cột 7 ngày vẽ **thuần CSS** (không thư viện chart, không npm — cột dùng `%` nên PHẢI có `.dash-bar__track` cao cố định làm mốc, thiếu là cột dẹp lép) · tồn theo nhóm vàng · lối tắt; hiện lên so le `.dash-in`. **KHÁCH HÀNG viết lại**: lọc 3 ô (key = SĐT/CCCD/họ tên/mã · địa chỉ · ngày sinh, HTMX gõ-là-lọc), 9 cột theo GĐ, **phân trang 50/trang bằng `ROW_NUMBER()`** (SQL 2005 không có OFFSET/FETCH); nút **+ THÊM KHÁCH** → popup CRUD đầy đủ (`_khach_form.html`) có **ô quét QR thẻ CCCD tự điền 6 trường** (`apps/pos/cccd.py` + bản JS trong khbl.js: `CCCD\|CMND cũ\|họ tên\|ddmmyyyy\|giới tính\|địa chỉ\|ddmmyyyy cấp`, tên IN HOA tự về Hoa-đầu-từ), 3 ô ảnh (đại diện + 2 mặt CCCD, xem trước tại chỗ), **loại khách radio Thường/VIP/VVIP/Cảnh báo** ghi cột `I_CUSTOMER.CustType` (bảng `I_CUSTOMER_TYPE` của vendor RỖNG nên KHÔNG dùng CustTypeID; trống = Thường), CMND = số CCCD. Lưu vẫn ở trạng thái chờ: kiểm tra dữ liệu rồi hiện **bộ tham số sẽ gửi cho `I_CUSTOMER_Ins/_Upd`**. Smoke 57/57 PASS | ✅ 03/09/2026 — chờ nghiệm thu |
| **TRACK C-3 — TRANG CHỦ + NHẬN DIỆN + CHÂN TRANG** (03/09/2026, GĐ chốt): logo thật `static/img/logo_icon.png|logo_full.png|favicon.png` (chép từ KHJ). **`/` và `/banle/` = TRANG CHỦ riêng khung**: `{% block rail %}` rỗng + `.khbl-shell--home` (bỏ rail) → đầu trang logo 56px + tên tiệm + **khối tài khoản** (họ tên · username · EmpID · két · nút Đăng xuất), dưới là **MENU NGANG 6 nút icon 34px** (Bán hàng nổi bật nền vàng · Thâu vào · Bảng giá · Khách hàng · Hóa đơn · Hệ thống), rồi bảng giá + số liệu. Các màn làm việc GIỮ rail (tiết kiệm chiều dọc), logo đặt đầu rail bấm về trang chủ. **CHÂN TRANG** `partials/footer.html` (nền tối, có ở mọi trang TRỪ màn bán hàng vì đã có thanh phím): trái = công ty/địa chỉ/ĐT/MST đọc từ `T_SHOP` (cache 1 giờ), giữa = phiên bản phần mềm + chấm trạng thái kết nối KK, phải = **đồng hồ thời gian thực** (JS 1 nhịp/giây, `window.__khblDongHo` chống chạy nhiều nhịp). Bộ kiểm gộp thành lệnh chính thức **`manage.py smoke_ui` (66/66 PASS)** — dùng `force_login`, KHÔNG đặt lại mật khẩu (set_password đổi session hash và ĐÁ MỌI NGƯỜI ĐANG ĐĂNG NHẬP ra ngoài) | ✅ 03/09/2026 — chờ nghiệm thu |
| **TRACK C-4 — KHUNG CHUNG TOÀN HỆ: TOPBAR + CHÂN TRANG, BỎ RAIL DỌC** (03/09/2026, GĐ chốt): `partials/topbar.html` cao **58px** dùng cho MỌI trang — logo 36px bấm về trang chủ · **menu ngang 7 mục icon 24px** (Tổng quan · Bán hàng · Thâu vào · Bảng giá · Khách hàng · Hóa đơn · Hệ thống, mục đang mở có nền vàng + gạch chân gradient qua `nav_active`) · khối tài khoản (họ tên, username, két, avatar, Đăng xuất). `partials/footer.html` cũng có ở MỌI trang (kể cả màn bán hàng). `.khbl-shell` đổi từ 2 CỘT (rail 64px) sang 3 HÀNG `auto 1fr auto`; `.khbl-rail*` và `partials/rail_item.html` đã XÓA HẲN. ⚠ Topbar+chân trang ăn 107px chiều dọc của màn bán hàng → đã siết các dải cố định (bảng giá 56→50 · dải quyết toán 88→78 · thanh phím 32→26 · chân trang 40) **và sửa lỗi có sẵn: `#pos-work` không kéo giãn** nên lưới giỏ hàng chỉ cao bằng nội dung — thêm `#pos-work{display:flex;flex-direction:column}` + `.pg-ban__body{flex:1;grid-template-rows:minmax(0,1fr)}` → vùng danh sách món **286px**, hơn cả trước khi có topbar (282px). Smoke `manage.py smoke_ui` **91/91 PASS** | ✅ 03/09/2026 — chờ nghiệm thu |
| GĐ1.5 | (nếu cần) so sánh SÂU 1 bảng: diff theo từng dòng PK, xem giá trị cột đổi | ⏳ |
| GĐ2 | Khung web nghiệp vụ: auth + layout KHJ + màn tra cứu ĐỌC (bảng giá, khách, hàng, hóa đơn trong ngày) | ⏳ |
| GĐ3 | Mở kênh GHI `pmv_exec` từng nghiệp vụ trên SANDBOX: bảng giá → customer → product sửa → HĐ thâu → bán → đổi (chuỗi `*_Ins` → `CARDPAY_Ins` → `*_Complete`) — mỗi cái 1 bộ smoke + diff | ⏳ |
| GĐ4 | Go-live từng chức năng, có công tắc riêng, đối soát cuối ngày với PMVGoldRT | ⏳ |

**PHẠM VI CHỐT 03/09/2026 + LỘ TRÌNH CHI TIẾT: `docs/LO_TRINH_KHBL.md`** — làm: bán–đổi
(1 giao dịch BUYSELL+_BUYGOLD; TRN_RT_CHANGE không dùng), thâu, CRUD khách, sửa product,
bảng giá (+HIST), in GIẤY ĐẢM BẢO; KHÔNG: thẻ/CK, SMS/Zalo, HĐĐT phát hành, nhập/xuất.
Tài khoản (GĐ chốt 03/09/2026 — thay đề xuất user `webapp`): web dùng ĐÚNG 2 tài khoản
app **admin / kimhanh2** — bảng MySQL `sys_users` (model `PmvUser`: UserID, UserName,
Password=BĂM Django, FirstName, LastName, FullName, IsAdmin, Active, ShopID, EmpID + TillID/
TillCode) đồng bộ bằng `manage.py sync_pmv_users` (đọc SYS_USERS + T_TILL.OpenUserID; mật
khẩu app chỉ dùng để băm, không lưu plain); user Django cùng tên để đăng nhập
(`LoginRequiredMiddleware`, `/dang-nhap/`). Hệ quả: web stamp UserID/TillID của chính user
→ tiền web VÀO CHUNG két app (admin → két `ad`, kimhanh2 → két `KH`). License app nằm ở
client (`tbh_NguoiDungDangNhap`) nên web không tốn/không đụng. `HoaDonDienTu_UpdateMa`:
GĐ chốt gọi với THAM SỐ TRỐNG y app (không phát hành). Phân tích nền:
`docs/PHAN_TICH_HOAT_DONG_PMVGOLDRT.md`.

**TRACK A — ✅ code xong 03/09/2026 (chờ nghiệm thu)**: `sys_users` + auth web ·
kênh **`pmv_call(proc, params, write=)`** trong gateway (`PROC_READ_ALLOW` proc Get/Lst ·
`PROC_WRITE_ALLOW` mở dần theo Track B, ghi ngày duyệt · chặn khi `pmv_write_lock`) ·
**`apps/pmv/client.py` PmvClient** (target pmv|sandbox: `fmt_date/fmt_time/money/xml_dataset`,
`params_of` từ sys.parameters — tham số sai tên chặn sớm, `call()` trả (rc, sets), rc≠0 →
`PmvProcError`, `bill()` = TRN_RT_BUYSELL_Get 3 result set, `retail_log()`, `sys_param()`) ·
`sandbox_call` · `manage.py smoke_pmv_client` **18/18 PASS**. Audit kind mới `EXEC`.

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
- **Backup-to-UNC từ SQL KK KHÔNG ĐƯỢC** (đã thử `\\AA\kim hanh ii\PUBLIC`, máy AA =
  192.168.1.200): SQL KK chạy `NT AUTHORITY\NetworkService` → ra mạng bằng tài khoản máy
  `KK$`, workgroup không nhận → "Operating system error 5" dù user đăng nhập trên KK
  copy tay được. Đừng thử lại bằng share — dùng cơ chế hút qua SQL (`sync_sandbox`).
- GĐ từng **cắt (move)** file .bak khỏi `D:\KHJ_PMV_BACKUP` trên KK → thư mục trống;
  SYNC luôn tự tạo bản mới nên không phụ thuộc file cũ.
- Máy Mr Giang còn instance **`MSSQL$ICLICK`** của phần mềm khác — KHÔNG đụng; KHBL chỉ
  dùng `localhost\SQL2014`.
- **CÔNG THỨC TIỀN: `apps/pmv/money.py` là bản DUY NHẤT** — SQL KHÔNG tính tiền hộ (proc đọc
  thẳng SellAmount từ XML), sai công thức là hóa đơn thật sai tiền. Đã kiểm chứng 100%:
  `SellAmount = tròn(GoldReal/HS × SellRate × 1000 + TaskPrice × 1000)` với HS=100 ('L'/'M') hay 1 ('G'),
  GoldReal = TotalWeight − DiamondWeight, **công tính THEO MÓN không nhân trọng lượng**;
  vàng cũ trong hóa đơn dùng **hằng 1000** (cột CcyRate của bảng đó = 1, dùng nó sai 1000 lần);
  **TotalAmount = Sell − Buy, KHÔNG trừ Discount**; Pay = Total − Discount + TaskPriceAdd.
  Làm tròn **HALF-AWAY-FROM-ZERO** (Decimal ROUND_HALF_UP) — `round()` của Python là banker's, SAI.
  Sửa gì về tiền phải chạy `manage.py smoke_pmv_money` (đối chiếu 1.400+ dòng thật).
- **Proc `T_PRODUCT_GetByCodeForSell`**: 8 tham số, `p_ShopID_XRate` phải truyền **''** (truyền
  ShopID thật thì SellRate về 0); result set có **cột trùng tên** (GoldReal/WeightUnit/InPrice ×2)
  → `gateway._dedupe_cols` giữ lần đầu, lần sau thêm `__2`; lỗi trả rc=−1 + ErrorCode/ErrorDesc
  tiếng Việt sẵn (P-002 không tồn tại · P-017 đã xuất) — UI in NGUYÊN VĂN, không tự dịch.
- ⚠ **Viết lại `base.html` là dễ LÀM RƠI thẻ nạp thư viện** — 03/09/2026 rơi mất `js/vendor/htmx.min.js`, mọi tương tác (quét mã, lọc, popup) chết mà bộ kiểm vẫn XANH vì nó chỉ kiểm phản hồi máy chủ. Nay `smoke_ui` khẳng định mỗi tài nguyên vừa **tải được** vừa **được nhúng vào HTML**. Sau khi sửa layout luôn mở trình duyệt bấm thử một thao tác thật.
- ⚠ **KHÔNG gọi file `.bat` từ Git Bash** (`cmd //c ...`) — bước `sc query MySQL80` trả sai dưới bash → báo “MySQL80 khong chay sau 60s” và HỦY bật, trong khi TURN_OFF đã chạy xong ⇒ hệ nằm im. Đã dính 03/09/2026. Luôn gọi qua PowerShell: `cmd /c D:\PYTHON\KHBL\RESET_KHBL.bat`.
- **Bộ kiểm KHÔNG được `set_password()`** — nó đổi session hash và đá mọi người đang đăng nhập ra; dùng `Client.force_login(user)`.
- **CHỮ TIẾNG VIỆT TỪ NGUỒN NGOÀI PHẢI CHUẨN HÓA** — skill `.claude/skills/chuan-hoa-tieng-viet/`,
  code `apps/pos/vn_text.py` + bản JS `static/js/vn_text.js` (sửa cái nào phải sửa cả hai).
  Máy quét CCCD đi qua clipboard/Excel trả về chữ hỏng mã 4 kiểu (byte-số `0225 0187 0141`,
  mojibake `TrÆ°Æ¡ng`, thực thể HTML, percent). Nguyên tắc **KHÔNG BỊA**: byte mất mà còn ≥2
  khả năng thì để `?` + cảnh báo, chỉ điền khi duy nhất 1 khả năng hợp lệ. Bộ thử
  `manage.py smoke_vn_text` (23 kịch bản, có chuỗi thật của GĐ). Gốc rễ: **quét THẲNG vào ô
  nhập của web thì không hỏng gì** — hỏng là do đi vòng qua clipboard/Excel.
- **`I_CUSTOMER.Gender` là bit: 1 = Nam · 0 = Nữ** (đã đối chiếu: 106 khách Gender=1 hầu hết
  tên "Anh/Chú/A"). ⚠ NHƯNG phần mềm để MẶC ĐỊNH 0 và nhân viên không sửa → **13.805 khách tên
  "Anh …" đang mang giới tính Nữ**. KHBL hiển thị ĐÚNG dữ liệu và gắn dấu `?` cam khi xưng hô
  trong tên mâu thuẫn (`services._dep_khach`), **KHÔNG tự suy** giới tính từ tên.
- **Trace SQL 2005 khó tính về kiểu**: `sp_trace_create @maxfilesize` phải là biến BIGINT,
  `sp_trace_setevent @on` phải là biến BIT — literal số bị "Procedure expects parameter…
  of type". File trace: nếu `pmv_behavior.trc` còn (lần tạo lỗi) SQL tự đặt `_1.trc` →
  luôn lấy đường thật từ `sys.traces`. TextData RPC trên 2005 là **`[TenProc]`/`TenProc @p=…`
  KHÔNG có chữ exec**, ObjectName thường NULL → bóc tên bằng `classify.proc_name_from_text`.
  Duration trong file trace = micro giây (÷1000). Giờ trong trace = giờ máy KK (chậm ~38s).
