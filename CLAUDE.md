# KHBL — WEBAPP BÁN LẺ | KIM HANH JEWELRY (song song PMVGoldRT)

> File định hướng cho Claude Code. Dự án **GHI TRỰC TIẾP vào DB phần mềm bán vàng PMVGoldRT đang bán hàng thật mỗi
> ngày** — rủi ro CAO HƠN KHJ HR, mọi thay đổi phải qua RULES mục 6. UI + commit message bằng **tiếng Việt**.
> KHÔNG sửa code ngoài phạm vi được yêu cầu. Chi tiết lịch sử từng ngày: `git log`.
> **UPSERT khách PMV** (webapp, cầm đồ, trang khác): đọc `.claude/skills/upsert-customer/SKILL.md` trước, tái dùng
> `apps/pos/customer.py` + `customer_phones.py`; không tạo luồng ghi khách riêng, không gộp/xóa CustID cũ.

---

## 1. BỐI CẢNH

- **Kim Hạnh 2** bán lẻ vàng bằng app desktop **PMVGoldRT** (.NET, vendor **không có tài liệu API**) trên 2 máy trạm
  (KK + QQ), DB SQL Server trên PC KK.
- KHBL = Django trên PC Mr Giang (192.168.1.6), **song song** PMVGoldRT, ghi PMV qua **stored proc của vendor**.
- Phạm vi (`docs/LO_TRINH_KHBL.md`): bảng giá · khách · sửa product · THÂU · BÁN–ĐỔI (1 giao dịch BUYSELL+_BUYGOLD,
  không dùng TRN_RT_CHANGE) · đặt-cọc · in Giấy đảm bảo (GĐB) · báo cáo · đối soát CK. **KHÔNG đụng hóa đơn điện tử.**
- Anh em: **KHJ HR** `D:\PYTHON\KHJ` (port 8000, chuẩn vận hành gốc) · **KHCD** cầm đồ `D:\PYTHON\KHCD` (8200).
  Độc lập nhau nhưng chung MySQL 8 + CA nội bộ + bộ vận hành RESET.
- Git: `github.com/giang88nc/KHBL` (private, nhánh `main`). `setup/SQLEXPR_x64_ENU` đã gỡ khỏi lịch sử + gitignore (07/10/2026).

## 2. STACK & HẠ TẦNG

| Thành phần | Chi tiết |
|---|---|
| Backend | Python 3.13 (venv) · Django 5.2 LTS · waitress `127.0.0.1:8101` sau **Caddy HTTPS `*:8100`** |
| Frontend | Django Templates + HTMX + CSS riêng `static/css/khbl.css` (token, **cấm hex cứng trong template**) · font tự host · `static/js/khbl.js` |
| Job nền | APScheduler `BlockingScheduler` — tiến trình riêng `manage.py run_scheduler` |
| MySQL 8 | port **3308**, DB **`khj_bl`** (utf8mb4), tài khoản CHUNG **`khj_admin`** (quản mọi DB `khj_*`; không tạo user quyền hẹp — rào an toàn ở tầng mã) |
| PMV (LIVE) | **SQL Server 2005 Express SP2** (compat 90) @ `tcp:192.168.1.206,1430`, DB `PMV_BANLE_KH2`, tài khoản `kimhanh2` (SYSADMIN), pyodbc + ODBC 18 `Encrypt=no` |
| Local MSSQL | `localhost\SQL2014` (Express 2014 — bản mới nhất còn restore được .bak 2005): `PMV_SANDBOX` (bản thử) + `PMV_KH2_HIST` (kho lịch sử), app dùng **Windows/Trusted auth** (SQL-auth `kimhanh2` chỉ để GĐ vào SSMS). ⚠ KHÔNG đụng instance `MSSQL$ICLICK` |
| Thư viện đáng chú ý | `segno` (QR offline) · `opencv-python-headless` + numpy (đọc QR, tách CCCD) · `openpyxl`. Không Pillow-only pipeline, không npm |
| Secret | `.env` (django-environ) — không bao giờ commit |

## 3. LINK & CỔNG

- Web: **`https://tiemvangkimhanh2:8100`** (= `https://localhost:8100` = `https://192.168.1.6:8100`); gõ `http://` → Caddy 308 sang https cùng cổng. Đăng nhập `/dang-nhap/`.
- Trang trạng thái / công tắc đích: `/he-thong/` · Kho lịch sử `/he-thong/kho-lich-su/` · Quy trình `/he-thong/quy-trinh/` · So sánh `/he-thong/so-sanh/` · Mẫu in GCD `/he-thong/mau-in-gcd/` · Mẫu in GĐB `/banle/giay-dam-bao/mau/`.
- Nghiệp vụ dưới `/banle/…`: `ban-hang` · `thau-vao` · `thau-vao-2` (đối soát OUT) · `khach-hang` · `bang-gia` · `hoa-don` · `bao-cao` · đặt-cọc.
- **Webhook SePay V2**: `/webhook/sepay2` (SePay gọi qua ngrok KIMHANH → Caddy 1277 → `127.0.0.1:8101`).
- phpMyAdmin: `http://localhost/phpmyadmin/index.php?server=3` (`khj_admin`).
- Cổng: 8100 Caddy · 8101 waitress · **8109 khóa scheduler** · **8119 khóa giám sát** · 18202 = cầu nối khách hàng **của KHCD** (chạy bằng venv KHBL nhưng KHÔNG thuộc KHBL).

## 4. CẤU TRÚC

```
KHBL/
├── config/            # settings (base/dev/prod/test_price_save), urls, scheduler.py
├── apps/pmv/          # gateway.py (CỬA DUY NHẤT sang MSSQL) · client.py PmvClient · money.py · diff.py
│                      # hist_config/hist_sync/hist_read (kho lịch sử) · classify/behavior_log (trace) · quy_trinh*
├── apps/pos/          # màn nghiệp vụ: bill.py · don.py · cart.py · services.py · views*.py · thau_* · ban_coc
│                      # customer.py · anh_cccd.py · ma_chung_tu_ck.py · thau_payments · sepay_v2 · quyen.py
│                      # gdb_layout / gcd_layout / gcd_may_in / ma_vach · vietqr · vn_text · bao_cao · gold_bill
├── apps/oa/           # sổ Zalo ZNS (managed=False) + xep_hang.py
├── templates/ static/ docs/ (quy_trinh/*.md, LUONG_BAN_HANG_PMV_20260905.md, …) · tests/
├── ops/vanhanh/       # vanhanh.ps1 (bản sao KHJ) + cauhinh_khbl.ps1 · ops/caddy/Caddyfile
├── LAN_HTTPS_KIT/     # cài hosts + CA cho PC LAN
└── RESET_KHBL.bat     # cửa vào vận hành DUY NHẤT
```

Skill (ĐỌC TRƯỚC khi sửa vùng tương ứng): `pmv-proc-map` (bản đồ proc vendor — tài liệu API duy nhất) · `cat-anh-cccd` ·
`upsert-customer` · `nhan-dien-ma-chung-tu-ck` · `chuan-hoa-tieng-viet`.

## 5. KIẾN TRÚC DỮ LIỆU

**3 kho**: **KK MSSQL** = sự thật bán hàng, CRUD chỉ qua proc · **`khj_bl` MySQL** = audit/trạng thái/dữ liệu web
(`gold_bill`, `thau_nhom`, `bill_audit`, `pmv_state`, giá `gold_prices`, TK `gold_bank`…) · **`PMV_KH2_HIST`** = bản sao
đầy đủ giữ mãi (superset, KHÔNG xóa dòng; hủy đơn trên KK → `_sync_deleted=1` trong cửa sổ 60 ngày) để đọc quá khứ + failover.

- **Gateway** `apps/pmv/gateway.py`: `pmv_read` (SELECT) · `pmv_admin` (allowlist tiện ích) · `pmv_call(proc, write=)`
  (`PROC_READ_ALLOW` / `PROC_WRITE_ALLOW`) · `pmv_image_read` (OPENROWSET BULK, regex khóa thư mục `D:\PHANMEMVANG\HINHANHKH`)
  · `hist_*` (chỉ ghi HIST). Ngoài allowlist → `PmvBlocked` + audit **BLOCKED** đỏ trên trang trạng thái.
- **Công tắc đích**: nút trên `/he-thong/` (ghi `PmvState['pmv_target']`, thắng `.env PMV_TARGET`, đổi ngay không RESET)
  · **chốt ghi KK CHỈ ở `.env PMV_GHI_KK`** (sửa file + RESET; web không có chỗ bật). Chốt nằm TRONG `gateway.pmv_call`.
  Hiện tại: đích **kk**, bán thật. Huy hiệu đích ở **chân trang mọi trang** — đừng bỏ. Backup/check/trace/sync luôn soi KK (chỉ đọc).
  Bộ kiểm chạy web trên sandbox phải đổi `gateway.dich_hien_tai` TRONG TIẾN TRÌNH (đổi công tắc thật là kéo cả máy quầy).
- **Định tuyến đọc** (`hist_read`, quy tắc toàn hệ): khoảng ngày có NGÀY CUỐI < hôm nay → HIST trước; có hôm nay / không ngày
  → KK trước, KK chết → lùi HIST + cờ `failover` (nhãn "📚 Kho lịch sử").
- **Sync HIST** `manage.py sync_hist` (`--sync` · `--backfill` · `--reconcile` · `--list`): job 09:00 & 21:00; cờ liên tiến trình
  `hist_sync_running` (quá 2h coi là rảnh). Danh mục bảng dò từ metadata KK (`hist_config`), không hardcode.
- **Giá bán/mua = MySQL `gold_prices`** (nguồn giá chính) phủ lên khung I_XRATE; quy đổi bằng đúng `price_sync.mssql_rate`.
  Mã **`2S…`/`9S…`** (vàng miếng, GoldCode N9999) bán theo giá **SJC** (`services.ap_gia_sjc`), thiếu giá SJC ⇒ CHẶN quét.
- Web không query PMV nặng trong request; realtime = poll nhẹ + HTTP 204 (KHÔNG long-poll).
- Tài khoản web = đúng tài khoản app (`sys_users` MySQL đồng bộ bằng `manage.py sync_pmv_users`, mật khẩu băm) → web
  stamp UserID/TillID của chính user, tiền vào chung két app.

## 6. RULES BẮT BUỘC (vi phạm = hỏng dữ liệu tiệm vàng thật)

1. **GATEWAY DUY NHẤT**: không import `pyodbc` ngoài `gateway.py` (ngoại lệ: `_restore_sandbox`, server local). Lệnh mới → thêm allowlist kèm lý do.
2. **CHỈ GỌI PROC VENDOR, không INSERT/UPDATE thẳng bảng PMV**. Thêm proc vào `PROC_WRITE_ALLOW` phải ghi NGÀY kiểm chứng trên sandbox.
   Ngoại lệ duy nhất (GĐ duyệt): khuôn `UPDATE TOP (n) I_CUSTOMER SET BirthDate='1900-01-01' WHERE BirthDate > '2010-01-01'` (`manage.py sua_ngay_sinh_khach`, chạy tay).
3. **Không bao giờ bật `PMV_GHI_KK` "cho tiện"** — chỉ khi GĐ duyệt go-live từng nghiệp vụ.
4. **SQL tương thích 2005**: không MERGE/OFFSET/kiểu DATE-TIME; tham số ngày truyền **chuỗi ISO**; lọc khoảng `>= d1 AND < d2+1`;
   phân trang `ROW_NUMBER()`; đọc bảng PHẢI `WITH (NOLOCK)`.
5. **VÙNG CẤM**: hóa đơn điện tử (`MAHDDienTu/InvoiceGUID/SoHDDienTu`, `SYS_INVOICE*`, `I_DichVuHDDT`; `HoaDonDienTu_UpdateMa`
   chỉ gọi THAM SỐ TRỐNG y app) · proc hủy diệt (`Del_AllData*`, `Del_OldData*`, `ChuyenKyKinhDoanh`, `DropDefaultConstraints`).
6. **Mã số do vendor sinh** (TrnID/BillCode/CustID…) — không tự bịa, không đọc-rồi-cộng-1 (số "dự kiến" chỉ để xem).
7. **BACKUP TRƯỚC KHI GHI** + **CIRCUIT BREAKER**: `check_pmv` lấy vân tay `tbh_VersionDB`; vendor nâng cấp → `pmv_write_lock=1`, dừng mọi ghi tới khi rà lại bản đồ proc.
8. **Thử trên SANDBOX trước** + diff từng bảng với cùng thao tác trên PMVGoldRT, khớp mới go-live. Sửa PMV → đọc skill `pmv-proc-map`; sửa `bill.py` → đọc `docs/LUONG_BAN_HANG_PMV_20260905.md`.
9. **CÔNG THỨC TIỀN: `apps/pmv/money.py` là bản DUY NHẤT** (SQL không tính hộ): `SellAmount = tròn(GoldReal/HS × SellRate × 1000 + TaskPrice × 1000)`,
   HS=100 ('L'/'M') hay 1 ('G'), GoldReal = Total − hột, **công theo MÓN**; vàng cũ dùng hằng 1000 (KHÔNG CcyRate);
   `TotalAmount = Sell − Buy` (không trừ Discount); `Pay = Total − Discount + TaskPriceAdd`; khách trả = còn lại − bớt + công thêm + vàng thêm − cọc
   (dấu AddMoney/TienCoc do GĐ chốt — đổi thì sửa `bill.tinh_tong`). Làm tròn **ROUND_HALF_UP** (không `round()`); toàn màn tròn nghìn (`money.tron_ngan`).
   Sửa tiền → `manage.py smoke_pmv_money`.
10. **Ô ảnh CCCD ⇒ LUÔN có nút ✂ TÁCH THẺ** ở mọi màn hình. Thuật toán GỐC ở `apps/pos/anh_cccd.py` + `apps/pos/cccd_mau/*.npz`
    (KHJ giữ bản sao byte-identical — sửa ở đây rồi chép sang KHJ `apps/common/`). Chỉ gọi lại `AC.cat_cccd(data, goc, cat)`, không sửa tùy tiện; đọc skill `cat-anh-cccd`.
11. **Mã chứng từ trong nội dung CK**: chỉ dùng `apps/pos/ma_chung_tu_ck.py` (skill `nhan-dien-ma-chung-tu-ck`) — không viết regex bóc mã chỗ khác.
12. **Ô nhập/chọn NHÂN VIÊN**: xếp theo TÊN GỌI, tìm không dấu — gọi `services.nhan_vien_ban()` / `tim_nhan_vien()` / `khoa_sap_nv()`, không tự query `T_EMPLOYEE`.
    `T_EMPLOYEE` có `DBUserName/DBPassword` — chỉ SELECT `EmpID, EmpName`.
13. **Chữ Việt từ nguồn ngoài** (máy quét qua clipboard/Excel) chuẩn hóa bằng `apps/pos/vn_text.py` + `static/js/vn_text.js` (sửa cả hai); **KHÔNG BỊA** — mơ hồ thì `?` + cảnh báo.
14. **Trang công khai trong LAN = DANH SÁCH ĐÓNG** 6 đường `@login_not_required`: `pos:thau_vao_2` · `thau_vao_2_xem` · `thau_vao_2_xuat_ncc` · `thau_vao_2_in_cccd` · `thau_anh` · `khach_anh`.
    Thêm đường khác phải hỏi GĐ (lộ tên/SĐT/CCCD/ảnh). Mọi trang nhập liệu + thao tác GHI bắt đăng nhập.
15. **Quyền**: ma trận `UserModuleAccess` (Xem / Tạo-sửa / Hủy-xóa / Duyệt-chốt) — một nguồn `apps/pos/quyen.py` cho CẢ menu (`quyen_muc.<MÃ>`) lẫn trang (`Q.chan`).
    Màn phụ kế thừa danh mục cha, không đặt luật riêng. Màn mới có gate quyền ⇒ bọc mục menu VÀ cấp quyền cho tài khoản quầy cùng lượt
    (máy quầy Edge `--app` không có nút Back). URL con chưa chặn riêng. Trang Khách hàng chưa soi ma trận — chỉ nối khi GĐ yêu cầu.
16. **Chú thích template**: gom ở ĐẦU tệp `{% comment %}…{% endcomment %}`; `{# #}` chỉ một dòng và không chứa `{% block %}`. Check Django **khbl.E001** (`apps/pos/kiem_template.py`, bản sao ở KHJ — sửa chép cả hai).
17. **Mọi CRUD mở POPUP** + OOB làm mới; passcode (riêng từng user, nút 🔑; chưa đặt thì TỪ CHỐI, sai 5 lần khóa 30s) cho mọi thao tác phá đơn đã chốt.

## 7. QUY TẮC NGHIỆP VỤ (module · bẫy)

- **Hóa đơn bán** — `apps/pos/bill.py` là NƠI DUY NHẤT gọi `TRN_RT_BUYSELL_*`; `don.py` = đơn chờ v5: quét món đầu = `_Ins` thành đơn **W thật**
  (SP khóa P-008 mọi máy), mỗi thay đổi = 1 `_Upd` (so vân tay giỏ, idempotent); ghi lỗi → giỏ quay về sự thật KK. Đơn W > 30' = "treo".
  Đơn **C**: `_Upd` từ chối (rc=−1) → phải MỞ LẠI; xóa đơn đã thanh toán = 2 bước (passcode mở → XÓA). `bill.chot` idempotent, chỉ coi đã vào sổ quỹ khi
  `T_TILL_TXN Status='P'` (Complete tạo dòng 'U', `T_TILL_TXN_Proc` mới gán két). `T_TILL_TXN_Del p_Type`: `'0'` = hủy thanh toán (hoàn két);
  đơn C chưa vào két → `'1'`. Chống 2 người sửa: `bill.kiem_moc` (TrnDateTime_Upd lúc mở). Nhật ký `bill_audit` append-only. Ngày cũ đã chốt = chỉ xem.
- **Khóa lạc quan — bẫy im lặng**: `_Upd/_Del` sai mốc `TrnDateTime_Upd` → `rc=0` mà KHÔNG làm gì. Dùng `PmvClient.goi_co_khoa(...)` (đọc mốc → gọi → kiểm dữ liệu đổi thật);
  `call(..., day_du=True)` tự điền tham số thiếu. **`rc=0` ≠ đã ghi** — luôn đọc lại đối chiếu.
- **Thâu vàng** — `bill.*_thau`, `views_thau.py`, `thau_cart.py`, nhóm nhiều dòng lưu `thau_nhom` (chốt chung `CompleteMore 'A@B@'`). Giá thâu luôn lấy server (MySQL).
  Ảnh: CCCD thuộc KHÁCH (file trên KK, `customer.cap_nhat_anh`) · Hình 1/2/QR thuộc NHÓM (`thau_nhom.anh_*`); ảnh chờ (`thau_anh_tam`) chỉ ghi khi THANH TOÁN,
  chỉ ＋ PHIẾU MỚI (có hỏi) và THANH TOÁN & IN mới dọn.
- **Đổi ngang vàng**: phần dẻ trong hạn mức (Σ GoldReal hàng bán CÙNG LOẠI) tính giá BÁN RA, dư tính giá THÂU MySQL; hột không tính tiền (`money.chia_doi_ngang`).
- **Đặt-cọc áp vào đơn bán**: chỉ phiếu CHỜ; 1 đơn nhiều cọc, 1 cọc 1 đơn, áp trọn. ⚠ `TRN_RT_BUYSELL_DatCoc_Ins` nhận danh sách ngăn '@' và XÓA SẠCH rồi ghi lại → gọi ĐÚNG 1 lần cả danh sách.
  Xóa phiếu cọc cần passcode; phiếu đã áp vào hóa đơn / đã thu vào két thì vẫn CHẶN. Đảo tiến độ giao/hủy bằng sửa thông tin: CẤM.
- **Tiền CK/thẻ bán-đổi KHÔNG đẩy lên KK lúc thanh toán**; nguồn CK là `gold_bill` + đối soát.
- **Đối soát OUT** (`thau_payments`, `ck_tra_khach`, `khcd_bao_nguoc`): ① thâu thanh toán như bán-đổi — KK nằm tiền mặt, khớp mới ghi CardPay ·
  ② nhiều lần CK một phiếu → cộng dồn · ③ CHỈ TRONG NGÀY (đủ mã) · ④ chi cầm đồ không có trên KK → chỉ báo ngược KHCD (`khj_cd.cd_payments`).
  Đối soát 2 lượt: webhook V2 → `doi_soat_nen` ngay + job `doi_soat_ck` 5'. Két KK không chỉnh (tiệm không dùng két KK — chấp nhận). Số KK lạ ⇒ "Cần kiểm tra", không ghi đè.
- **SePay V2 là nguồn CHÍNH** (`/webhook/sepay2`, `SEPAY_API_KEY`). V1 (BANLE 5000 `/sepay/webhook`) đã gỡ khỏi Caddy KIMHANH; BANLE 5000 chép
  `khj_bl.bank_notifications` → `pmv_report` **MỘT CHIỀU** cho MISA/IMPORT (khác id; chép ngược pmv_report → khj_bl BỊ CẤM).
- **Báo cáo** (`apps/pos/bao_cao.py`): 3 doanh thu THỰC `SUM(SellTotalAmount)` · RÒNG `SUM(PayAmount)` · HH (phần >0); chỉ `IsDel='0' AND Status='C'`, mốc `CreatedDate`.
  ⚠ `ThuHo` là varchar — không SUM.
- **In**: GĐB A5 (`gdb_layout.py`, `@page size:auto` + ghim mép trên; giấy in sẵn → không in nền); GCD cầm đồ A5 ngang cấu hình ở KHBL, in thật ở KHCD
  (`gcd_layout.py`, khóa `pmv_state`: `gcd_layout` · `may_in_ds` · `gcd_may_in` — KHBL là người ghi duy nhất, KHCD chỉ SELECT). Mã vạch `ma_vach.py`
  (GCD giữ ĐỦ chữ số, không rút 9 số như GĐB). Trang web không chọn được máy in — in thẳng cần Edge `--kiosk-printing`. JS `buildCss` phải y hệt `css()` Python.
- **Khách hàng**: CustType ghi `I_CUSTOMER.CustType` (Thường/VIP/VVIP/Cảnh báo); xóa khách guard 5 bảng (proc vendor bỏ sót `TRN_RT_BUYGOLD`).
  `Gender` bit 1=Nam 0=Nữ — dữ liệu mặc định sai nhiều, **không tự suy giới tính từ tên**. BirthDate trống → 01/01/1900. Ảnh CCCD = file `D:\PHANMEMVANG\HINHANHKH` trên KK.
- **OA / ZNS** (`apps/oa`): 3 bảng `zalo_templates/send_rules/messages` là schema của **CARE360**, `managed=False` — **KHÔNG BAO GIỜ bỏ `managed=False`**, migrate với app này là no-op.
  `khj_bl.zalo_messages` = **SỔ CHUNG**; **CARE360 là bộ gửi duy nhất**, cầu nối nằm ở KHJ (`apps/oa_messages/cau_noi.py`). KHBL chỉ XẾP HÀNG
  (`xep_hang.py`, không gọi mạng): móc ở `bill.chot/mo_lai/huy` bọc try/except 2 tầng — **lỗi xếp hàng không bao giờ làm hỏng lượt bán**. 4 lớp chống trùng,
  không INSERT mù; băm SĐT giữ tiền tố `care360-zbs|`. Công tắc: `STOP_ZNS.flag` · `ZNS_XEP_HANG` (.env, mặc định TẮT) · đích phải kk. Hóa đơn hiện vẫn do CARE360 tự quét KK.
  Đổi cột 3 bảng ⇒ sửa KHJ `apps/oa_messages/models.py` cùng lượt. ⚠ **Không điền `provider_id` / token cho mẫu 635720** khi GĐ chưa chốt bỏ đường gửi thẳng `deposit_messages.py` (gửi đôi).
- **Giờ**: cả `khj_bl` + `khj_hr` lưu **GIỜ VN naive từ 20/09/2026** (`TIME_ZONE="Asia/Ho_Chi_Minh"`); code gán datetime aware, không cộng trừ tay. Phục hồi backup cũ hơn mốc này phải trừ 7h.
- Proc `T_PRODUCT_GetByCodeForSell`: `p_ShopID_XRate=''`; cột trùng tên → `_dedupe_cols` thêm `__2`; lỗi vendor (P-002/P-008/P-017) in NGUYÊN VĂN, KHBL chỉ bổ sung "đơn nào đang giữ".
- Học hành vi PMVGoldRT mới = tay: ĐÁNH DẤU TRƯỚC (tự bật trace) → thao tác app → ĐÁNH DẤU SAU → HỌC vào `/he-thong/quy-trinh/` (lưu `docs/quy_trinh/<CODE>.md`).

## 8. VẬN HÀNH (07/10/2026 — mỗi dự án CHỈ 1 file RESET)

Hệ KHBL = 4 tiến trình ẨN ở phiên nền: Caddy `*:8100` · waitress `127.0.0.1:8101` · scheduler (**giữ khóa `127.0.0.1:8109`**, bản thứ 2 tự thoát;
đổi qua `KHBL_SCHEDULER_KHOA_CONG` + `cauhinh_khbl.ps1`) · vòng giám sát (`vanhanh.ps1 -Lenh giamsat`, khóa **8119**, 60s/lần bật lại cái chết).
`TURN_ON_KHBL` / `TURN_OFF_KHBL` / `WATCHDOG_KHBL` / `run_hidden_khbl.vbs` **ĐÃ XÓA**.

| Lệnh | Việc |
|---|---|
| `RESET_KHBL.bat` | RESET đầy đủ: kiểm điều kiện (thiếu MySQL80/venv/caddy → KHÔNG tắt gì) → tắt (giám sát trước) → kiểm sạch → bật → kiểm cổng + `https://localhost:8100/` + giám sát, có `pause` |
| `/reset` | Như trên không pause — **Claude dùng từ PowerShell**: `cmd /c D:\PYTHON\KHBL\RESET_KHBL.bat /reset` (**KHÔNG Git Bash** — đã từng làm hệ nằm im) |
| `/bat` · `/tat` · `/kiem` | Chỉ bật cái thiếu · tắt hẳn bảo trì · chỉ xem (quyền thường được) |

- Động cơ `ops/vanhanh/vanhanh.ps1` = **bản sao GIỐNG HỆT của KHJ** (sửa ở KHJ rồi chép sang KHBL + KHCD; `KHJ\scripts\kiem_sau_khoi_dong.ps1` so sha256) +
  `ops/vanhanh/cauhinh_khbl.ps1` (thành phần · cổng · lệnh bật · dấu hiệu; `TruocKhiBat` chép CA từ KIMHANH nếu thiếu). Log `logs/vanhanh_khbl.log`.
- Nhận diện bằng CỔNG; chỉ giết khi dòng lệnh khớp dấu hiệu VÀ thuộc `D:\PYTHON\KHBL`; ứng dụng lạ giữ 8100 → báo LỖI, không giết (kill tay rồi RESET).
  **Không bao giờ đụng 18202** (cầu nối của KHCD).
- Quyền thường bấm RESET không hỏi UAC: đi qua tác vụ Windows **`KimHanh2-VanHanh-KHBL`** (S4U mức Admin, đăng ký bởi `KHJ\CAU_HINH_TU_HOI_PHUC.bat`)
  → tiến trình ở session 0, **không nằm trong job object của app gọi** (đóng app Claude không còn làm tiệm sập).
- Bật cùng Windows: tác vụ `KimHanh2-ToanHeThong-Boot` → `KHJ\KHOI_DONG_TOAN_HE_THONG.bat` → `RESET_KHBL /bat`.
- Sửa `.py` → RESET. Sửa model → **`migrate` TRƯỚC rồi mới RESET** (19/09 từng sinh 17 phiếu thâu trùng trên KK). PROD mặc định (`config.settings.prod`, DEBUG=False).
- **HTTPS LAN**: `ops/caddy/Caddyfile` (`admin off` vì KIMHANH giữ 2019; `caddy validate …` trước khi RESET); CA dùng chung KIMHANH
  ("Caddy Local Authority - 2026 ECC Root") ở `runtime/caddy-data/pki/…`; PC LAN chạy `LAN_HTTPS_KIT\CAI_HTTPS_PC_LAN.bat` một lần. Camera chỉ chạy trên https.
  `prod.py` có `SECURE_PROXY_SSL_HEADER`; `.env` ALLOWED_HOSTS/CSRF_TRUSTED_ORIGINS đủ 5 host. Kiểm: `netstat` thấy `0.0.0.0:8100` (Caddy) + `127.0.0.1:8101`.
- **Lịch scheduler**: sync HIST 09:00/21:00 · backup KK (`backup_pmv`, COPY_ONLY 251 bảng) + kho (`backup_hist` → `D:\KHBL_BACKUP\hist`, giữ 30, tự từ chối
  nếu cùng ổ với .mdf) **09:30/21:30** (`misfire_grace_time=3600` chạy bù) · `check_pmv` + vân tay vendor 60' · `sync_gold_bill` 60' · `doi_soat_ck` 5'.
  Job thu thập hành vi 2' + trace KK đang TẮT (`manage.py pmv_trace start|stop|status`). Phục hồi thử: RESTORE … WITH MOVE vào DB tạm rồi DROP.
- Sandbox: `manage.py sync_sandbox` (KK backup COPY_ONLY → hút .bak qua `OPENROWSET(BULK)` → restore `PMV_SANDBOX`; share PC KK bị Access denied, UNC từ SQL KK không được — đừng thử lại).

## 9. BẪY ĐÃ DÍNH (tránh chẩn đoán lại)

- 🔴 **Bộ kiểm CHỈ chạy qua `manage.py test`** (thường `--settings=config.settings.test_price_save`) — `unittest discover`/`pytest` xóa thẳng `khj_bl`
  (11/09: mất 355.918 dòng, cứu bằng binlog ROW 30 ngày). `tests/__init__.py` có chốt DB phải bắt đầu `test_`. Smoke command chạy trên DB thật phải
  `atomic()` + rollback. Bộ kiểm **không `set_password()`** (đá mọi người ra) — dùng `force_login`. Test client cần SERVER_NAME hợp lệ.
- `str(Decimal)` ra `0E-8` / `2.000E+6` → proc CONVERT chết: mọi Decimal viết `format(v,"f")` (`PmvClient._xml_val`, `ban_coc.chuoi_tien`). Tham số varchar chứa số để rỗng → chết (bộ mặc định `bill._MAC_DINH`).
- Vàng bán + vàng đổi đi CHUNG 1 tham số XML `@p_Trn_RT_BUYSELL` (`PmvClient.xml_nhieu_bang`). Món đang trên hóa đơn không quét lại được — dựng dòng từ `_Get`.
- Hủy hóa đơn không dọn `T_CUSTOMER_DEBT` và `*_Log`; `I_CUSTOMER_Del` từ chối khách có giao dịch; xóa khách gọi `SaveImageToFile` (OLE) — sandbox SQL2014 tắt OLE nên nổ.
- `fast_executemany`: nổ RAM với `nvarchar(max)`, nổ với chuỗi rỗng / Decimal lệch scale → HIST tự rơi về đường chậm. BINARY_CHECKSUM bỏ text/ntext/image/xml.
- Trace SQL 2005: `@maxfilesize` biến BIGINT, `@on` biến BIT; tên file không đuôi `_số`; lấy đường thật từ `sys.traces`; TextData RPC không có chữ exec (`classify.proc_name_from_text`). Đồng hồ KK chậm ~35–38s.
- Đặt method `check` trong Command đè `BaseCommand.check()`. `@require_GET` phải đứng ngay trên view (chèn helper giữa → helper bị bọc, 405 âm thầm).
- MySQL chưa nạp bảng timezone → ưu tiên lọc khoảng `[đầu, sau)` thay vì `__date`.
- Viết lại `base.html` dễ rơi thẻ nạp `htmx.min.js` (smoke vẫn xanh) → mở trình duyệt bấm thử thật. Đầu trang nằm trong `{% block page_head %}` — override rỗng, đừng xóa khỏi base.
- HTMX: script trong `_ban_oob.html` phải nằm TRONG mảnh OOB (nút dùng `hx-swap="none"`); settle chép lại `class` → JS thêm class phải trễ ~80ms; `b.click()` nút vừa swap không ăn → dùng `htmx.ajax`;
  `hx-confirm` cấp form bị kế thừa xuống phần tử hx-* con; `hx-vals='{{ vals }}'` autoescape vẫn đúng, đừng `|safe`; `&` trong src bị escape `&amp;`.
- CSS grid item cần `min-width:0` (nút DANH SÁCH từng bị đè không bấm được). Edge bỏ `window.print()` trong iframe ẩn → in từ chính cửa sổ (`#pos-in`).
- `@page` cứng A5 + máy in Letter/A4 → Chromium canh giữa, lệch dọc 35–43mm. `segno` cần border=4 thì OpenCV mới đọc. `ConvertTo-Json` PS 5.1 bỏ lớp mảng khi 1 phần tử.
- Camera: phải giữ `inp = target` trước `closeCamera()` rồi mới dispatch `change`. Đổi JS chung thì đổi cache-buster `khbl.js?v=…` trong base.html.
- Chữ Việt ra stdout bị redirect → cp1252 crash: `PYTHONUTF8=1` + `reconfigure(errors="replace")`. Template sửa bằng Edit/Write, KHÔNG replace bằng PowerShell (hỏng UTF-8).
- `.bat` phải CRLF + ASCII, không `timeout` (dùng `ping -n`), không `)` trong echo giữa khối `if`. Ghi `hosts` trên PC LAN dùng `[IO.File]::WriteAllText` (Set-Content lỗi stream).
- Icon topbar: ảnh 96px ở `static/img/ico/<key>.png` (sinh bằng System.Drawing), key mới phải khai `_ICON_ANH` trong `pos_extras.py`; nơi khác dùng `{% icon %}` SVG.
- Luật template KHBL (`{% comment %}`) **đừng mang sang KHCD** (Flask/Jinja2).

## 10. UI & THƯƠNG HIỆU

Theo chuẩn KHJ: heading **Cormorant Garamond**, body **Be Vietnam Pro**; nền `#FDFCF9`, tối `#1C1917`, viền `#EAE4D6`; vàng `#8C6A1D` · `#C9A02C` · `#F7EFD8`;
xanh `#2E7D46` / đỏ `#B3402A` / cam `#C07A1A`. Tiền `1.234.567 ₫` · ngày `dd/mm/yyyy` · UI "nhìn là hiểu". Khung chung: topbar 64px (menu ngang theo quyền)
+ chân trang (huy hiệu đích). Toast tự ẩn (lỗi 60s · cảnh báo 8s · OK 5s); lỗi thiếu dữ liệu focus + nhấp nháy ô lỗi. Màn bán: chân 4 nút cố định bật/tắt theo trạng thái.

## 11. TRẠNG THÁI & VIỆC TIẾP

- **Đang chạy thật trên KK**: bán–đổi (v5), thâu, khách (CRUD + ảnh + ✂), đặt-cọc + áp cọc, hóa đơn, báo cáo, in GĐB, đối soát OUT + SePay V2, kho lịch sử + failover.
  Phần lớn hạng mục "✅ chờ nghiệm thu".
- **Treo / chờ GĐ**: chốt bỏ hay giữ đường gửi Zalo riêng `deposit_messages.py` (trước khi bật mẫu 635720) · GCD: đo khổ giấy thật, driver "Fit to page",
  quét thử mã vạch 7 mil trên tờ in đầu · Báo cáo: drill-down, Excel, lãi gộp (`GiaVon` rỗng, nghĩa `InPrice`?), tồn chậm (nghĩa trạng thái `O`) ·
  `manage.py test` cần quyền tạo `test_khj_bl` · đẩy CK/thẻ bán-đổi lên KK (`CARDPAY_Ins`) = phát triển sau · hỏi vendor về proc `*_Mobile_Ins`/`*_API` ·
  backup ra NAS/USB (`PMV_BACKUP_DIR_LOCAL`, `PMV_HIST_BACKUP_DIR`).

Chi tiết lịch sử: `git log` (bản CLAUDE.md đầy đủ trước 07/10/2026 nằm trong lịch sử Git).
