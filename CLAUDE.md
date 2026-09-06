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

## 4b. CÔNG TẮC ĐÍCH DỮ LIỆU (chốt 03/09/2026)

Toàn bộ nghiệp vụ bán lẻ ĐỌC và GHI vào **một đích duy nhất**. Hai lớp điều khiển:

| | Đổi ở đâu | Đổi được lúc đang chạy? |
|---|---|---|
| **Đích dữ liệu** (bản thử ↔ máy KK) | **nút trên trang `/he-thong/`** (chính) · `.env PMV_TARGET` (mặc định lúc khởi động) | **CÓ** — bấm là đổi ngay, không RESET |
| **Chốt ghi máy KK** | **CHỈ** `.env PMV_GHI_KK` | không — phải sửa file + RESET |

```
PMV_TARGET=sandbox     # mặc định khi chưa bật công tắc: bản thử | kk = dữ liệu THẬT
PMV_GHI_KK=False       # chốt an toàn: cho phép GHI vào máy KK. MẶC ĐỊNH TẮT
```

Cố ý tách hai lớp: **đổi đích là việc thường ngày** (tập dượt rồi soi số thật) nên để trên
web cho nhanh; **mở đường ghi vào sổ tiệm là việc hệ trọng** nên bắt phải mở file, không
bấm nhầm được. Nút trên web ghi vào `PmvState['pmv_target']` (sống qua RESET), thắng `.env`;
nút "↺ Theo .env" bỏ ghi đè. Gateway đệm 2 giây để khỏi hỏi MySQL mỗi lệnh.

Kiểm chứng 03/09: đích `kk` đọc ra 56.320 khách (số sống), `sandbox` 56.318 (số lúc đồng bộ).

| | Đi theo `PMV_TARGET` | Luôn nói chuyện với máy KK |
|---|---|---|
| Ai | `PmvClient()` → mọi màn bán lẻ | backup đêm · `check_pmv` · trace hành vi · nút ĐỒNG BỘ |
| Vì sao | đổi đích là đổi cả app | hạ tầng phải soi máy thật, và chỉ ĐỌC |

**Chốt an toàn nằm TRONG `gateway.pmv_call`**, trước cả bước mở kết nối — không phải ở
người gọi, để không ai quên được. `PMV_TARGET=kk` + `PMV_GHI_KK=False` = đọc thật, ghi chặn.

**Băng đích trên mọi trang** (`templates/partials/dich.html`, hàng 2 của `.khbl-shell`):
xanh "BẢN THỬ" / đỏ "DỮ LIỆU THẬT" + chấm nhấp nháy. Đừng bỏ — nhầm đích là hỏng sổ tiệm.

**Tập dượt đi ĐÚNG đường chạy thật**: sandbox cũng qua gateway (allowlist + nhật ký +
khóa ghi). `sandbox_call` đi tắt đã XÓA; `sandbox_query` chỉ còn cho hạ tầng.

Bộ hồi quy: **`manage.py smoke_dich`** (45 kịch bản — công tắc, chốt chặn ghi KK, CRUD thật
trên bản thử: tạo khách → lập hóa đơn → chốt → hủy sạch → trả hàng về kho, tự dọn rác;
và giao diện công tắc: bấm đổi → băng đổi màu cả trang Hệ thống lẫn màn bán lẻ, ở đích KK
ghi vẫn bị chặn, web KHÔNG có chỗ bật chốt ghi). Bộ này KHÔNG bao giờ bật `PMV_GHI_KK`.

## 4c. KHÓA LẠC QUAN — BẪY IM LẶNG (kiểm chứng 03/09/2026)

Proc `_Upd`/`_Del` của vendor so `TrnDateTime_Upd` truyền vào với giá trị trong bảng.
**Sai mốc → proc trả `rc=0` nhưng KHÔNG LÀM GÌ CẢ.** Đã dính thật: hủy hóa đơn "thành công"
mà hóa đơn vẫn còn, món hàng vẫn bị giữ trong trạng thái đã bán.

- Mốc **đổi sau MỖI bước** → phải đọc lại giữa `T_TILL_TXN_Del` và `TRN_RT_BUYSELL_Del`.
- `T_TILL_TXN_Del @p_Type` — **ĐÍNH CHÍNH 05/09/2026 (đo thật + đọc proc)**: với hóa đơn bán (SRT) proc
  KHÔNG rẽ nhánh theo `@p_Type`, giá trị chỉ được ghi vào bảng `*_Log`; `'0'` hay `'1'` đều hoàn kho/két/nhật
  ký. App dùng **`'0'` = hủy thanh toán → đơn về W** (đo 06:58:30 TRB…606) và **`'1'` chỉ trong chuỗi xóa đơn đã
  thanh toán** (06:48:49 TRB…604, Del ngay 1s sau). Câu cũ "`'0'` không ăn gì" đúng CHỈ với đơn còn W (không có
  gì để hoàn). `bill.mo_lai` đang dùng `'1'` → **đổi sang `'0'`** khi làm GĐ3 (đã ghi trong quy trình BAN_HANG pha 6).
- Dùng **`PmvClient.goi_co_khoa(...)`**: tự đọc mốc hiện tại, gọi, rồi **kiểm chứng dữ liệu
  đã đổi thật**; không đổi thì ném lỗi. Đừng gọi `_Upd`/`_Del` trần.
- Proc vendor không có tham số mặc định → thiếu 1 cái là SQL Server chửi. Dùng
  `call(..., day_du=True)` để tự điền nốt theo chữ ký proc.
- Hủy hóa đơn **không dọn** `T_CUSTOMER_DEBT` (còn 1 dòng số dư 0) và các bảng `*_Log`
  (cố ý giữ). `I_CUSTOMER_Del` **từ chối** xóa khách đã phát sinh giao dịch (trả `Result=-1`).

## 4d. MÀN BÁN HÀNG (thiết kế lại 03/09/2026 — GĐ duyệt)

`/banle/ban-hang/` — bố cục GĐ chốt:

```
CỘT TRÁI (1fr) — 3 form xếp dọc:
  #pos-info  THÔNG TIN — Ngày [DANH SÁCH][THÊM MỚI] · Số HĐ (khóa) · [CLEAR]
                        Nhân viên (gõ tên) · Khách hàng (tên/SĐT/CCCD) [+/SỬA]
  #pos-ban   VÀNG BÁN — quét tem/gõ mã ⏎ → thêm dòng, ô tự trống để quét tiếp, SUM ở chân
  #pos-doi   VÀNG ĐỔI — loại dẻ · tổng TL · TL hột · TL vàng · giá đổi · thành tiền, SUM ở chân
CỘT PHẢI (340px) — #pos-tong TÍNH TỔNG — vàng mới · vàng cũ · CÒN LẠI · vàng thêm ·
                   công thêm · bớt · cọc · ghi chú · TIỀN KHÁCH TRẢ
CHÂN (full)  THÊM MỚI · XÓA · THANH TOÁN · THANH TOÁN & IN · IN HÓA ĐƠN
```

**Bố cục 2 CỘT (relayout 05/09/2026, GĐ chốt — chỉ đổi UI, hành vi/OOB giữ nguyên)**: `.pg-ban__body`
grid 2 cột `minmax(0,1fr) 340px`; **cột trái `.pg-ban__col-l`** grid 3 hàng `auto / 1.35fr / 1fr` chứa
`#pos-info` (cao tự nhiên) + `#pos-ban` + `#pos-doi` (mỗi khối `display:flex` để `.pg-box` bên trong lấp
đầy track → thân bảng `.pg-box__body--cuon` MỚI cuộn đúng); **cột phải** `#pos-tong` (`.pg-ban__c2`).
Các id `#pos-info/#pos-ban/#pos-doi/#pos-tong` GIỮ NGUYÊN nên OOB `hx-swap-oob` không đổi.
⚠ **Bẫy đã sửa**: sau khi bỏ `#gia-strip` (block `chrome` rỗng) mà `.khbl-main--pos` vẫn để
`grid-template-rows:auto minmax(0,1fr)` → `.pg-ban` rơi vào hàng `auto` (cao bằng nội dung) nên bảng tràn
xuống che mất `.pg-ban__foot`; sửa `.khbl-main--pos` còn 1 hàng `minmax(0,1fr)` để `.pg-ban` lấp đầy chiều
cao. Đã dọn CSS chết `.pg-ban__body.has-mua` + biến thể cột `1fr 92px` (layout đời cũ, không nơi dùng).

**Đơn giản hóa UI (05/09/2026, GĐ chốt — chỉ đổi UI)**:
- **`#pos-info` gộp 1 DÒNG** (`.pg-info--1d`): ＋ ĐƠN MỚI · Ngày (**chỉ xem** — hóa đơn luôn mang hôm nay) ·
  Nhân viên · Khách hàng (nút **＋ THÊM gắn liền input** qua `.pg-inb`; đã chọn khách → `.pg-chon` + ✎ SỬA) ·
  ☰ DANH SÁCH. **BỎ nút CLEAR + ô Số hóa đơn** (mã đơn chuyển sang badge bên VÀNG BÁN).
- **VÀNG BÁN**: bỏ nút "Tìm hàng · F3" (không tìm hàng ở màn này) → thay bằng **badge mã đơn**
  `.pg-hd-badge` = `bill_code | trn_id | ma_du_kien`; chưa quét món đầu / chưa có đơn → badge **"bỏ trống"**
  (`.pg-hd-badge--trong`). Gỡ F3 khỏi keymap + gợi ý phím.
- **VÀNG ĐỔI**: form `.pg-doi-nhap` **tích hợp lên header** (`.pg-doi-head`), **bỏ tiêu đề "VÀNG ĐỔI dẻ khách
  đưa"**; checkbox ⇄ Đổi ngang **float phải** (`.pg-ngang--right`, `margin-left:auto`).
- smoke_ui 122/122 (cập nhật assertion: bỏ CLEAR/Số HĐ, thêm badge + Ngày chỉ xem), smoke_ban_hang 51/51.

**TÍNH TỔNG nâng cấp (05/09/2026, GĐ chốt)**:
- **Làm tròn HÀNG NGHÌN toàn màn**: `money.tron_ngan` (quantize 1.000, HALF_UP); `bill.tinh_tong` làm tròn
  `con_lai` + `khach_tra` (giá vàng/công vốn theo nghìn → thực tế no-op, PayAmount lưu cũng tròn); `ban_dat`
  làm tròn mọi khoản nhập (bớt/cọc/thêm/tiền mặt).
- **BỚT LẺ = tối đa 3 nút gợi ý** (`money.bot_le_goiy`): lẻ hiện tại, +10k, +20k (lẻ=0 → 10k/20k/30k), không
  vượt tổng; VD khách trả 7.346.000 → [6.000·16.000·26.000]; bấm = **set** thẳng `bot` qua `ban_dat`
  (không cộng dồn) + nút **✕ bỏ** (bot=0). Nút cũ "Bớt lẻ cho tròn" đã thay; F6 trỏ nút gợi ý đầu.
- **PHƯƠNG THỨC THANH TOÁN** (session, CHƯA ghi PMV — để GĐ3): radio Tiền mặt (mặc định) / Chuyển khoản /
  Quẹt thẻ + 2 ô **Tiền mặt** + **CK/Thẻ** tự bù cho đủ khách trả (JS `sync`, gõ ô này ô kia auto). Session
  giữ `pay_method` + `tien_mat` (rỗng = auto theo phương thức; đổi radio → về auto); `cart.tong` suy
  `tien_mat`/`tien_ck` (clamp trong [0, khách trả]). Nhãn ô 2 đổi theo radio.
- **TIỀN KHÁCH TRẢ đổi màu**: >0 xanh nhạt (`--duong`) · <0 đỏ nhạt (`--am`) · =0 giữ nền vàng.
- **QR CHUYỂN KHOẢN (VietQR, dựng OFFLINE)** — trong form thanh toán: `select name="bank_code"` (danh sách
  ~30 NH VN + BIN napas ở **`apps/pos/vietqr.py`**) + ô **Số tài khoản** (`bank_num`) + nút **🔳 Tạo QR**.
  `ban_qr` (GET, popup `_qr_modal`) dựng chuỗi EMVCo/VietQR thuần Python (`vietqr.payload`: GUID
  A000000727, dịch vụ QRIBFTTA, CRC-16/CCITT) rồi **`segno` vẽ PNG data-URI** (không gọi mạng — chạy được
  cả khi LAN offline). Số tiền QR = ô CK/Thẻ (`amount` truyền qua hx-vals; ≤0 → QR để người chuyển tự nhập);
  nội dung CK = mã đơn. QR chỉ để KHÁCH quét chuyển — CHƯA đối soát/ghi PMV. ⚠ thêm dependency
  **`segno==1.6.6`** (pure-python, requirements.txt).
- **DANH SÁCH TK LẤY TỪ MySQL** (06/09/2026, GĐ chốt): bảng **`gold_bank`** (app MySQL) — `bank_bin` = MÃ NH
  ('ACB', map napas BIN qua `vietqr._BIN`; là BIN 6 số thì dùng thẳng), `bank_number` = số TK,
  `bank_name`/`bank_user`, `Active`. `vietqr.active_banks()` (Active=1) đổ vào select; **mặc định chọn TK số
  666141168** (`default_bank_id`, hằng `DEFAULT_BANK_NUMBER`). Session giữ **`bank_id`** (thay bank_code/num
  cũ), giữ qua `clear()`; `ban_qr` tra `gold_bank` theo bank_id (fallback mặc định) → payload. Bỏ ô nhập STK
  tay — chọn NH là có sẵn bin+số.
- smoke_pmv_money 15/15 (+bot_le_goiy, +tron_ngan), smoke_ui 126/126, smoke_ban_hang 51/51.
  ⚠ Nếu GĐ muốn "làm tròn LÊN" kiểu ceil (thay HALF_UP nearest) thì đổi rounding trong `tron_ngan`.

**Công thức tiền** (phần "còn lại" đo trên 18.441 hóa đơn thật; hai khoản mới GĐ chốt):
```
còn lại   = tiền vàng mới − tiền vàng cũ
khách trả = còn lại − bớt + công thêm + vàng thêm − cọc
```
Cột PMV: `SellTotalAmount` `BuyTotalAmount` `TotalAmount` `Discount` `TaskPriceAdd`
`AddMoney` `TienCoc` `PayAmount` `Description`. ⚠ **`AddMoney` và `TienCoc` chưa từng
phát sinh trong 18.441 hóa đơn của tiệm** — dấu (+ / −) là do GĐ chốt 03/09/2026, không
phải đo được; đổi ý thì sửa `bill.tinh_tong` chứ đừng sửa rải rác.

**"Dẻ"** là từ của chính vendor cho vàng cũ: `I_GOLD.GoldType='D'` → `D18K · D24K ·
D9999 · DBk · DSJC · DT`. Bảng giá có sẵn đúng các mã đó nên **giá đổi lấy thẳng**,
không phải quy đổi. Đơn vị: `PriceUnit='L'` → ₫/chỉ (trọng lượng nhập theo **ly**);
`'G'` → ₫/g. Ô "giá đổi" trên màn hình nhập theo **ĐỒNG** (8.750.000), view chia
`RATE_SCALE` trước khi đưa xuống PMV (PMV lưu 8750).

**ĐỔI NGANG VÀNG (checkbox "⇄ Đổi ngang vàng", GĐ chốt 05/09/2026)** — cạnh tiêu đề khối VÀNG ĐỔI:
- **Bỏ tick** = như cũ, 1 dòng dẻ giá THÂU.
- **Tick** = khách đổi dẻ CÙNG TUỔI VÀNG lấy hàng mới: phần dẻ trong **HẠN MỨC** (= Σ `GoldReal` hàng
  BÁN cùng loại còn lại) tính **GIÁ BÁN RA** (ngang giá, tiệm không ăn chênh mua–bán); phần DƯ tính giá thâu.
  `views.ban_doi_them` tự tách 1–2 dòng dẻ (`money.chia_doi_ngang`), đánh dấu `DoiNgang='1'` cho dòng ngang.
- Khớp **theo từng loại** (GĐ chốt): dẻ D18K↔hàng 18K, D9999↔N9999… (`money.DE_TO_BASE`/`de_base`). Giá bán ra
  lấy **SellRate của chính dòng dẻ** trong bảng giá (đã kiểm: D18K.Sell=18K.Sell=8850; nếu 0 → SellRate loại base).
- Hạn mức = Σ GoldReal hàng bán loại đó − Σ TL vàng các dòng dẻ đã đổi ngang loại đó (`_han_muc_doi_ngang`);
  hiện gợi ý "Hạn mức đổi ngang còn: 18K …" trên khối khi giỏ có hàng. Kiểm chứng đúng ví dụ GĐ: mua 2,5 chỉ 18K,
  dẻ 3,1 chỉ → dòng 1 = 2,5 chỉ × giá bán, dòng 2 = 0,6 chỉ × giá thâu; hột dồn dòng cuối (không đổi tiền).
- Hiệu ứng phân biệt: dòng đổi ngang nền xanh + badge "⇄ đổi ngang" (xanh), dòng thâu badge "giá thâu";
  khối viền vàng khi đang tick; JS đổi nhãn "GIÁ ĐỔI"→"GIÁ BÁN RA" và tự điền SellRate.
- ⚠ Trọng lượng vàng đổi có thể LẺ (GoldReal 258,2 ly) → dùng `_so_tl` (phẩy=thập phân, chấm=nghìn), KHÔNG
  dùng `_so` (parser tiền, bỏ hết dấu → hỏng số lẻ). Đổi ngang chỉ ảnh hưởng BuyRate từng dòng dẻ; vẫn là
  các dòng `TRN_RT_BUYSELL_BUYGOLD` bình thường khi GHI (GĐ3).

**Bố cục THÔNG TIN (chỉnh 05/09/2026)**: "Số hóa đơn" chuyển xuống ĐẦU dòng NHÂN VIÊN BÁN (`pg-info__d2`);
nút **DANH SÁCH + THÊM MỚI** làm nổi bật (`khbl-btn--gold pg-btn-noi`, bóng vàng).

**Bảng VÀNG BÁN/ĐỔI tinh gọn (chỉnh 05/09/2026, GĐ chốt)**: thêm cột **LOẠI** = nhãn TUỔI VÀNG
(`money.tuoi` + filter `tuoi`): 18K→**610** · 24K→**980** · N9999→**99.99** · BK · VT · SJC (áp cả mã dẻ, quy
`de_base` trước; không có trong `TUOI_LABEL` → trả base). Trọng lượng mọi ô quy về **1 đơn vị KHÔNG kèm chữ**
(`money.weight_chi` + filter `tl_chi`): `L`/`M` → **chỉ** (0,001 chỉ), `G`/`K` → **gram**. Tên hàng 1 dòng ellipsis
(`.pg-td-ten`), bảng gọn (`.pg-tb--gon`), body `.pg-box__body--cuon` cuộn khi >10 dòng. VÀNG ĐỔI: checkbox
"⇄ Đổi ngang vàng" mặc định **CHECKED**; ô input vẫn nhập theo **ly**, nội dung bảng hiển thị quy chỉ.
`ban.html`: JS ẩn dropdown gợi ý NV/Khách khi bấm ra ngoài `.pg-f--tim`; **BỎ dải giá đỉnh `#gia-strip`**
(kéo theo bỏ poll 15s của dải). smoke_ui 122/122, smoke_pmv_money 11/11.

**Tinh chỉnh chân bảng + khách + đổi ngang (05/09/2026, GĐ chốt)**:
- Dropdown NV: **KHÔNG hiện EmpID** (chỉ tên gọi).
- Khách hàng: đã chọn → **ẩn nút ＋ THÊM, hiện nút ✎ SỬA** mở popup `khach_sua` ngay trên bán hàng;
  lưu xong (`HX-Trigger khachSaved`) → `ban.html` nghe sự kiện, `htmx.ajax` POST `ban_dat` với `custId`
  để nạp lại tên/SĐT khách đang chọn (CSRF qua `KHBL_CSRF`).
- Chân bảng VÀNG BÁN/ĐỔI: ngoài TL tổng còn **chi tiết theo TỪNG LOẠI VÀNG** (chip `.pg-tl-loai`,
  helper `cart._tl_theo_loai` gộp theo `money.tuoi` + đơn vị giá; bán dùng `GoldReal`, đổi dùng `GoldWeight`).
- VÀNG ĐỔI: **BỎ dải "Hạn mức đổi ngang còn:"**; form nhập `flex-wrap:nowrap` + `overflow-x:auto`
  để KHÔNG đẩy mất chân bảng khi thêm nhiều dòng; checkbox "Đổi ngang" giữ **CHECKED** sau mỗi lần thêm.
- ⚠ **Đổi ngang khi KHÔNG có hàng bán cùng loại** (hạn mức ≤ 0): KHÔNG chặn nữa — coi hạn mức = 0 →
  toàn bộ `(tổng − hột) × giá thâu` như thâu thường (`views.ban_doi_them`, `chia_doi_ngang` với hm=0 trả 1
  dòng giá thâu).
- **Nút ↻ TÍNH LẠI** (cuối dòng nút ＋ THÊM, `views.ban_doi_tinh_lai`): **gôm các dòng VÀNG ĐỔI cùng
  loại vàng** rồi tính lại theo từng loại — mỗi loại **tối đa 2 dòng: 1 NGANG + 1 THÂU**. Cách tính = gộp
  TL vàng (`GoldWeight`) + hột theo `GoldCode` → `chia_doi_ngang` với hạn mức = Σ `GoldReal` hàng bán cùng
  base (chia dần nếu nhiều dẻ cùng base), đơn giá CHUẨN từ bảng giá (giống lúc THÊM có tick đổi ngang; đổi
  ngang trong hạn mức giá BÁN RA, dư mới giá thâu). `cart.dat_doi` thay trọn danh sách đổi. smoke_ban_hang 51/51.

**Số hóa đơn** ô khóa, chữ mờ + nhãn "dự kiến": chỉ là `MAX(TrnID)+1` cho người bán dễ
hình dung. Số THẬT do vendor cấp lúc lưu (RULE 5) — máy KK bán song song có thể lấy
trước số đó. Lưu xong ô hiện `BillCode` thật.

**Ngày**: hóa đơn mới LUÔN mang ngày hôm nay (GĐ chốt). Ô ngày chỉ dùng để lọc DANH SÁCH.

### Vòng đời trên màn hình

| Nút | Việc |
|---|---|
| DANH SÁCH | popup hóa đơn trong ngày → MỞ nạp lên form |
| THANH TOÁN | `bill.luu` (Ins hoặc Upd) rồi `bill.chot` |
| MỞ LẠI ĐỂ SỬA | chỉ hiện khi phiếu ĐÃ CHỐT — `bill.mo_lai` đưa về nháp |
| XÓA | phiếu chưa lưu thì dọn form; đã lưu thì `bill.huy` (hàng về kho) |

⚠ **Hóa đơn đã chốt thì proc `_Upd` TỪ CHỐI (rc=-1)** — muốn sửa phải MỞ LẠI trước.
`bill.luu` tự kiểm và ném lỗi kèm hướng dẫn, không im lặng bỏ qua.

### Tệp

- **`apps/pos/bill.py`** — NƠI DUY NHẤT nói chuyện với `TRN_RT_BUYSELL_*`. View không
  được tự gọi proc hóa đơn. Đọc 3 luật ghi trong docstring trước khi sửa.
- `apps/pos/cart.py` — phiếu đang làm dở trong session; mỗi dòng giữ NGUYÊN bộ cột proc
  cần (khóa `row`) nên quét mã mới và mở hóa đơn cũ đi chung một đường.
- `templates/pos/ban.html` + `_ban_info/_ban_ban/_ban_doi/_ban_tong/_ban_ds/_nv_goiy` ·
  OOB gom trong `_ban_oob.html` · CSS tiền tố `.pg-`.
- Bộ hồi quy **`manage.py smoke_ban_hang`** (48 kịch bản: công thức · tạo/sửa/chốt/mở
  lại/hủy trên bản thử · đầu-cuối qua web), tự dọn hóa đơn của mình.

### Bẫy đã trả giá (03/09/2026)

1. **`str(Decimal('0E-8'))` ra `'0E-8'`** → proc CONVERT sang numeric là CHẾT
   ("Error converting data type varchar to numeric"). Dính khi đọc dòng hàng bằng
   `TRN_RT_BUYSELL_Get` rồi đẩy ngược vào `_Upd`. Đã bịt trong `PmvClient._xml_val`
   (mọi Decimal viết dạng `format(v,"f")`); `cart._chuoi` cũng vậy.
2. **Món đang nằm trên hóa đơn KHÔNG quét lại được** (đã mang trạng thái đã bán) — mở
   hóa đơn ra sửa thì dòng hàng phải dựng từ `TRN_RT_BUYSELL_Get`, xem `dong_ban_tu_phieu`.
3. **Vàng bán và vàng đổi đi CHUNG một tham số XML** `@p_Trn_RT_BUYSELL`:
   `<NewDataSet><TRN_RT_BUYSELL_SELL/>…<TRN_RT_BUYSELL_BUYGOLD/>…</NewDataSet>`.
   Dùng `PmvClient.xml_nhieu_bang`.
4. **Tham số varchar chứa SỐ để rỗng là chết** — proc CONVERT sang numeric. Bộ mặc định
   "0" gom ở `bill._MAC_DINH` (chép từ chính lệnh app desktop gửi).
5. **`rc=0` không có nghĩa đã ghi** — `bill.luu` luôn đọc lại đối chiếu mã hàng + tiền.

## 4e. KHO LỊCH SỬ — GIANG MSSQL `PMV_KH2_HIST` (Phase 1 XONG 06/09/2026)

3 kho (GĐ chốt): **KK MSSQL** (nguồn LIVE, có thể prune ~30–60 ngày/lần) · **GIANG MSSQL
`PMV_KH2_HIST`** (bản sao ĐẦY ĐỦ, giữ mãi, đọc quá khứ + failover) · **GIANG MYSQL `khj_bl`** (bổ trợ).

- **Nơi đặt**: DB `PMV_KH2_HIST` trên **chính instance `localhost\SQL2014`** (cùng chỗ `PMV_SANDBOX`),
  SQL Server 2014 Express. **Windows auth (Trusted)** như sandbox — ⚠ tài khoản `kimhanh2` chỉ có trên KK
  (206), KHÔNG có trên instance nội bộ (login failed), nên HIST dùng Trusted.
- **An toàn KK**: mọi thao tác KK CHỈ ĐỌC (SELECT/metadata). Ghi CHỈ vào HIST qua **`gateway.hist_*`**
  (kết nối riêng `settings.PMV_HIST_*`, tuyệt đối không chạm 206). `gateway.pmv_read_stream` đọc KK theo lô.
- **Danh mục DÒ TỪ metadata KK** (`apps/pmv/hist_config.py`, không hardcode): 46 bảng = `I_CUSTOMER` ·
  `TRN_RT_BUYSELL(+con)` · `TRN_RT_BUYGOLD(+con)` · `T_PRODUCT(+liên quan)` · mọi `*_LOG`. Chiến lược tự suy:
  **append** (`*_LOG`, watermark=identity/PK) · **upsert** (cha có `TrnDateTime_Upd`+PK 1 cột) · **child**
  (bảng con, làm mới theo `TrnID` cha) · **snapshot** (I_CUSTOMER/T_PRODUCT — không watermark, đối soát checksum).
- **Phase 1 = dựng + backfill + reconcile** (`apps/pmv/hist_sync.py`, lệnh `manage.py sync_hist`):
  `--list` xem danh mục · `--backfill [--table a,b]` DROP+CREATE schema (mirror cột KK + 2 cột kỹ thuật
  `_sync_seen_at`,`_sync_deleted`) rồi chép trọn theo lô · `--reconcile` so `COUNT` + `CHECKSUM_AGG(BINARY_CHECKSUM)`
  từng bảng · không cờ = trạng thái từ bảng điều khiển `_hist_sync_state`. **Chạy thật 06/09: 46/46 bảng,
  489.071 dòng, 0 lệch, 0 lỗi** (mọi bảng KHỚP count+checksum). `smoke_hist` 16/16.
- ⚠ **Bẫy**: `fast_executemany` nổ RAM với cột `nvarchar(max)` (I_CUSTOMER) → backfill tự TẮT fast + lô nhỏ
  khi bảng có cột `(max)`. text/ntext/image/xml bị loại khỏi BINARY_CHECKSUM (chỉ so COUNT).
- **Phase 2 XONG 06/09/2026 — SYNC INCREMENTAL theo LỊCH 09:00 & 21:00** (GĐ chốt): `manage.py sync_hist
  --sync` (job scheduler `_job_sync_hist`, CronTrigger hour="9,21"). Chiến lược: **append** (log, chỉ chép dòng
  có watermark > MAX ở HIST) · **upsert** (cha: đọc KK `TrnDateTime_Upd > MAX(HIST)` → `hist_bulk_merge` staging
  #stg + MERGE theo PK, thu tập `TrnID` đổi) · **child** (xóa+chép lại dòng con theo `TrnID` cha vừa đổi) ·
  **snapshot** (I_CUSTOMER/T_PRODUCT: MERGE toàn bộ theo PK — không watermark). **KHÔNG XÓA** dòng nào (kho =
  superset). **VOID**: `detect_void` đánh `_sync_deleted=1` cho dòng trong cửa sổ `PMV_HIST_VOID_DAYS`=60 còn ở
  HIST nhưng đã mất ở KK (hủy đơn = xóa cứng), ngoài cửa sổ coi là KK prune → giữ. Reconcile "lành" =
  `HIST ≥ KK` (superset). Kiểm chứng: sync sau backfill bắt đúng hoạt động live (+52 HĐ, +95 dòng bán qua
  child-refresh, +19 khách), 0 lỗi, reconcile khớp.
- **TÀI KHOẢN SQL `kimhanh2/KimHanh2` (sysadmin) đã TẠO trên `localhost\SQL2014`** để GĐ vào SSMS kiểm tay.
  ⚠ instance đang **Windows-only auth** (`IntegratedSecurityOnly=1`) → phải bật **Mixed Mode** (SSMS: chuột phải
  server → Properties → Security → "SQL Server and Windows Authentication mode") + **khởi động lại service
  `MSSQL$SQL2014`** thì SQL-auth mới dùng được. App HIST hiện chạy **Trusted** (chạy tốt); muốn app dùng SQL-auth
  thì sau khi bật Mixed Mode đặt `.env PMV_HIST_USER=kimhanh2 / PMV_HIST_PASSWORD=KimHanh2`.
- **Phase 3 XONG 06/09/2026 — TRANG BÁO CÁO** `/he-thong/kho-lich-su/` (view `pmv.hist_view`, template
  standalone `pmv/hist.html` + mảnh `_hist_bang.html`, link "Kho lịch sử" trên subnav 5 trang Hệ thống): thẻ tóm
  tắt (lần sync gần nhất · số bảng · dòng KK/kho · bảng THIẾU (kho<KK) · bảng LỖI · đơn đã void) + bảng từng bảng
  (cách sync, KK, kho, chênh, tình trạng OK/THIẾU/LỖI, ghi chú) + nút **⟳ Đồng bộ ngay / ✓ Đối soát / chép lại
  từng bảng** chạy **THREAD NỀN** (`hist_sync.chay_nen`), trang tự poll 4s khi đang chạy, log 60 dòng cuối ở
  `PmvState.hist_sync_log`. **Cờ LIÊN TIẾN TRÌNH** `hist_sync_running`+`hist_sync_started` (PmvState): job
  09:00/21:00, nút web và lệnh tay KHÔNG chạy chồng; cờ quá 2h (tiến trình chết) tự coi là rảnh. `sync_hist`
  lệnh tay cũng bật/tắt cờ này.
- **Phase 4 XONG 06/09/2026 — ĐỊNH TUYẾN ĐỌC QUÁ KHỨ + FAILOVER** (`apps/pmv/hist_read.py`): gateway/PmvClient
  nhận đích **`"hist"`** (chỉ SELECT — `pmv_call` trên hist bị CHẶN), cùng câu SQL chạy được cả 2 kho.
  `HR.doc(fn, ngay_iso=…)`: ngày **QUÁ KHỨ** → kho lịch sử trước (lỗi → lùi live); **hôm nay/không ngày** → live
  trước, **KK chết → lùi về kho + cờ `failover`** (ghi cảnh báo vàng). Đã nối: `services.hoa_don_ngay`,
  `services.tong_quan` (tách `_tong_quan(c,…)`), `services.lich_su_khach`, `bill.trong_ngay` (view KHÔNG đổi).
  Cờ nguồn thread-local → context processor `hist_read.nguon` (đọc rồi XÓA, consume-once) → nhãn
  **"📚 Kho lịch sử · dữ liệu tới HH:mm (· máy KK không đọc được)"** trên Hóa đơn, Tổng quan (đặt ở ĐẦU `block
  content` — vì `base.html` bản đang sửa của phiên Bảng giá đã bỏ `page_sub`), popup DANH SÁCH hóa đơn.
  Để kho tự đủ cho các JOIN: thêm **10 bảng tham chiếu** (`hist_config._EXTRA_REF`: T_EMPLOYEE · T_SECTION ·
  T_MAINSECTION · I_GOLD · I_GOLD_BAL · T_SHOP · I_XRATE · T_TILL · SYS_USERS · I_DIEMTICHLUY, đều snapshot)
  → **56 bảng**. ⚠ Bẫy: `fast_executemany` nổ với chuỗi RỖNG `''` (HY090 buffer length 0) và Decimal lệch
  scale ("loses precision") → `hist_bulk_merge`/`hist_executemany` **tự thử lại đường CHẬM** khi fast lỗi.
  `smoke_hist` 33/33; sync steady-state 56 bảng +0 mới 0 lệch 0 lỗi.
- **ĐÃ KÍCH HOẠT 06/09/2026 14:20** — GĐ chạy RESET_KHBL: web + scheduler đều tạo 14:20:07 (sau mtime
  `scheduler.py`) → job sync 09:00/21:00 + trang Kho lịch sử + định tuyến đang chạy thật. ⚠ Bẫy: banner
  "scheduler khởi động" KHÔNG hiện trong `logs/scheduler.log` vì print bị block-buffer (BlockingScheduler
  không thoát) → đã vá `flush=True` (hiệu lực từ lần restart sau); muốn chắc scheduler nạp code mới thì so
  **CreationDate tiến trình `run_scheduler` với mtime file**, đừng tin banner cũ.
- **SQL-auth `kimhanh2/KimHanh2` ĐÃ DÙNG ĐƯỢC** (06/09/2026): GĐ bật Mixed Mode bằng
  `xp_instance_regwrite … LoginMode=2` rồi `Restart-Service 'MSSQL$SQL2014'` (SSMS 2008 R2 cũ hơn engine 2014
  nên dialog Properties không dùng được — dùng lệnh). Xác minh: `IsIntegratedSecurityOnly=0`, login vào
  `PMV_KH2_HIST` sysadmin. **App HIST vẫn giữ Trusted** (GĐ chốt — chạy tốt, không đổi `.env`); tài khoản
  SQL chỉ để GĐ vào SSMS kiểm tay (Server `localhost\SQL2014`, SQL Server Authentication).

## 5. RULES BẮT BUỘC (vi phạm = hỏng dữ liệu tiệm vàng thật)

1. **GATEWAY DUY NHẤT**: không import `pyodbc` ngoài `apps/pmv/gateway.py` (ngoại lệ duy
   nhất: `_restore_sandbox` trong backup_pmv — server LOCAL). Muốn lệnh mới → thêm vào
   allowlist gateway kèm lý do, không "đi tắt".
2. **KÊNH GHI ĐÃ MỞ NHƯNG KHÓA VÀO MÁY KK** (03/09/2026): `gateway.pmv_call(..., write=True)`
   chỉ gọi STORED PROC vendor trong `PROC_WRITE_ALLOW` — **không bao giờ INSERT/UPDATE thẳng
   vào bảng PMV** (né sạch logic tồn kho/sổ quỹ/audit của app). Thêm proc vào allowlist phải
   ghi NGÀY kiểm chứng trên sandbox.
   **Ngoại lệ RULE 2 duy nhất (GĐ duyệt 05/09/2026)**: khuôn `UPDATE TOP (n) I_CUSTOMER SET BirthDate = '1900-01-01'
   WHERE BirthDate > CAST('2010-01-01' AS datetime)` trong `_ADMIN_ALLOW` — dọn ngày sinh khách mà app PMVGoldRT ghi
   mặc định = ngày tạo hồ sơ (bảng không trigger; proc `I_CUSTOMER_Upd` ghi cả 35 cột + DELETE/INSERT lại
   `I_GIAODICH_KHACHHANG`/`SHOP_CUSTOMER` nên KHÔNG dùng). Lệnh tay `manage.py sua_ngay_sinh_khach [--kiem |
   --dich sandbox | --dich kk --xac-nhan KK]`, lô 5.000/~0,3s. **Đã chạy thật 05/09/2026 09:01**: sync backup mới
   09:00 → sandbox 56.392 dòng OK, diff chỉ `I_CUSTOMER` → KK 56.392 dòng/12 lô/4,0s, tổng 56.401 giữ nguyên, sau
   khi chạy KK = sandbox 251/251 (còn 7 ngày sinh thật ≤ 2010, mới nhất 04/05/2000). GĐ chốt: KHÔNG job đêm dọn
   lại (KHBL tạo khách sẽ gửi 01/01/1900); app vẫn ghi sai cho khách mới → chạy tay lại khi cần. Audit CANHBAO tag
   `sua_ngay_sinh` + dòng trong `thay_doi.log`.
2b. **CÔNG TẮC ĐÍCH + CHỐT AN TOÀN** — xem mục 4b. Đích mặc định là BẢN THỬ; ghi vào máy KK
   bị gateway TỪ CHỐI khi `PMV_GHI_KK=False`. **Không bao giờ bật cờ này để "cho tiện"** —
   chỉ bật khi GĐ duyệt go-live từng nghiệp vụ.
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
  **30 phút check_pmv** · **2 phút collect_pmv_behavior** (trace → MySQL + file `logs\pmv\<ngày>\`
  + chụp FULL KK dò bảng đổi → `thay_doi.log`; dọn 1 lần/ngày: file 90 ngày, DB 30 ngày).
- **Trace SQL trên KK chạy LIÊN TỤC có chủ đích** (GĐ duyệt 05/09/2026 — là nguồn của nhật ký
  hành vi), gồm cả lời gọi của KHBL. Tắt/bật: `manage.py pmv_trace stop|start|status`.
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
| **NHẬT KÝ HÀNH VI RA FILE + DÒ THAY ĐỔI SQL KK** (05/09/2026 — GĐ duyệt 7 điểm thiết kế, code `apps/pmv/behavior_log.py`): **Bước 1** — mỗi lời gọi bắt được từ trace ghi thêm 1 dòng text vào **`logs\pmv\<YYYY-MM-DD>\ALL.log`** + file NHÓM (`ban_hang · thau_vang · hd_doi · dat_coc · khach_hang · bang_gia · san_pham_kho · so_quy · nhat_ky_ngay · dong_bo · hd_dien_tu · tin_nhan · he_thong · nhan_vien · bao_cao · khac` — map `NHOM_FILE` từ classify.py, có assert phủ đủ nhóm); dòng = `giờ KK TRACE\|STATS \| máy \| login \| nhóm \| ghi/đọc \| proc \| ms \| r/w/n \| tham số ĐẦY ĐỦ ép 1 dòng`. **Trace bỏ lọc login** (GĐ chốt): lời gọi của chính KHBL đi chung dòng thời gian, collect gắn máy **`KHBL`**, bỏ SELECT trần của web (poll giá/hóa đơn, sys.parameters) để không ngập; tên file trace gắn mốc giờ `pmv_behavior_YYYYMMDDTHHMM*.trc (SQL cấm đuôi "_số" khi xoay vòng — lỗi 19069)`, 10×20MB; lớp đọc lấy **file ĐẦU chuỗi** sống từ `sys.traces` + `xp_dirtree` (`trace_first_file`/`_chon_file_dau`) rồi `fn_trace_gettable(…, DEFAULT)` — hết ghim path cũ; EventSequence là bộ đếm toàn instance nên GIỮ mốc khi bật trace mới. **Bước 2** — cùng chu kỳ 2 phút `collect_pmv_behavior` chụp **FULL KK** (`diff.snapshot("pmv","full",force=True)` — đo 05/09: 251 bảng checksum NOLOCK ≈ 1,5s, 18 bảng nghiệp vụ 0,48s → GĐ duyệt chạy liên tục, tải ≈1%) so với lần trước (`PmvState pmv_kk_snapshot`) → bảng đổi ghi **`thay_doi.log`** + bảng **`pmv_change_logs`** (model `PmvChange`, migration pmv-0005) **kèm các proc GHI bắt được trong khung** = bản đồ proc→bảng cho GĐ3; `sync_sandbox` xong tự chụp **baseline** + đặt lại mốc. **Chế độ thủ công** trên `/he-thong/so-sanh/`: nút **① ĐÁNH DẤU TRƯỚC** (chụp FULL + ghim seq trace) → GĐ làm 1 thao tác trên PMVGoldRT → **② ĐÁNH DẤU SAU** → trang `pmv/danh_dau.html`: lời gọi giữa 2 mốc (mọi máy kể cả KHBL, so theo `event_seq` nên không lệ thuộc đồng hồ KK chậm 35s) + bảng đổi + **dòng vừa đổi** (30/251 bảng có cột `TrnDateTime_Upd/CreatedDate/…`, tham số ISO có `T` để không lệ thuộc ngôn ngữ phiên; ẩn cột password) + file `danh_dau_HHMMSS.log`; PmvChange gắn `mark=danhdau:<snap>`. Dọn tự động 1 lần/ngày: file 90 ngày · DB 30 ngày. Lệnh `manage.py pmv_log xuat-lai [ngày] \| don \| thu-muc`. Hạn chế đã nói với GĐ: BINARY_CHECKSUM bỏ cột text/ntext/image/xml; số dòng sys.partitions có thể trễ; khung 2 phút gom nhiều thao tác → dùng đánh dấu để tách. Lời gọi của KHBL qua pyodbc đi trong lớp bọc RPC `sp_prepexec`/`sp_execute` — `classify.proc_name_from_text` bóc proc thật bên trong (bọc SELECT trần → "" → collect bỏ), `sp_unprepare` vào NOISE. Smoke **`manage.py smoke_pmv_log` 41/41** (thư mục tạm + rollback, không đụng KK) + `smoke_ui` 123/123. ⚠ BẪY: đặt tên method `check` trong Command đè `BaseCommand.check()` → nổ ngay khi chạy; `event_time__date` trên MySQL trả rỗng (chưa nạp timezone) — lọc khoảng `[đầu ngày, ngày sau)` | ✅ 05/09/2026 — chờ nghiệm thu |
| **TRACK C — MÀN HÌNH BÁN HÀNG** (03/09/2026): hệ thiết kế **"MẶT KÍNH"** — vùng làm việc SÁNG kẹp giữa 2 dải TỐI (bảng giá đỉnh · dải quyết toán đáy), chọn qua hội đồng 3 phương án × 3 giám khảo. `static/css/khbl.css` (token → base → layout → component → trang → in; **cấm hardcode hex trong template**, chữ trên dải tối dùng bản `*-glow` ≥5:1) · font **tự host** `static/fonts/` (mất mạng không đổi font giữa ca) · htmx + `static/js/khbl.js` (1 hàm `khblBind`) · `templates/base.html` rail 64px + `#modal-root`/`#toast-root`. App **`apps/pos`**: `services.py` (đọc PMV: quét mã qua proc vendor lấy nguyên câu lỗi P-002/P-017, tìm hàng, khách, bảng giá cache 5s, hóa đơn ngày) · `cart.py` (giỏ trong session — F5 không mất) · 5 màn: **`/` Mua bán** (quét → thẻ lớn 1 món / bảng ≥2 món, ngăn THÂU VÀO cùng hóa đơn, dải quyết toán 8 khối, nhãn tự đổi **"Tiệm trả lại khách"** khi PayAmount<0) · **`/thau/`** · **`/bang-gia/`** · **`/khach-hang/`** · **`/hoa-don/`**. Realtime = **poll nhẹ + HTTP 204** (KHÔNG long-poll: waitress ít thread + nguồn là MSSQL qua LAN): bảng giá 15s theo chữ ký sha1, hóa đơn 3s. Smoke: `smoke_pmv_money` **11/11** + smoke UI **19/19** PASS. ⚠ v1 CHƯA GHI: nút Lưu dùng `.khbl-btn--cho` (vân sọc, không toast xanh) → popup phiếu tạm để nhập tay sang PMVGoldRT | ✅ 03/09/2026 — chờ nghiệm thu |
| **TRACK C-2 — CẤU TRÚC URL `/banle/` + TỔNG QUAN + KHÁCH HÀNG** (03/09/2026, GĐ chốt): mọi màn dời vào **`/banle/…`** (`ban-hang` · `thau-vao` · `khach-hang` · `bang-gia` · `hoa-don`); **`/` và `/banle/` = TỔNG QUAN**: 4 thẻ KPI (bán/thâu/khách mới/hàng tồn hôm nay) · biểu đồ cột 7 ngày vẽ **thuần CSS** (không thư viện chart, không npm — cột dùng `%` nên PHẢI có `.dash-bar__track` cao cố định làm mốc, thiếu là cột dẹp lép) · tồn theo nhóm vàng · lối tắt; hiện lên so le `.dash-in`. **KHÁCH HÀNG viết lại**: lọc 3 ô (key = SĐT/CCCD/họ tên/mã · địa chỉ · ngày sinh, HTMX gõ-là-lọc), 9 cột theo GĐ, **phân trang 50/trang bằng `ROW_NUMBER()`** (SQL 2005 không có OFFSET/FETCH); nút **+ THÊM KHÁCH** → popup CRUD đầy đủ (`_khach_form.html`) có **ô quét QR thẻ CCCD tự điền 6 trường** (`apps/pos/cccd.py` + bản JS trong khbl.js: `CCCD\|CMND cũ\|họ tên\|ddmmyyyy\|giới tính\|địa chỉ\|ddmmyyyy cấp`, tên IN HOA tự về Hoa-đầu-từ), 3 ô ảnh (đại diện + 2 mặt CCCD, xem trước tại chỗ), **loại khách radio Thường/VIP/VVIP/Cảnh báo** ghi cột `I_CUSTOMER.CustType` (bảng `I_CUSTOMER_TYPE` của vendor RỖNG nên KHÔNG dùng CustTypeID; trống = Thường), CMND = số CCCD. Lưu vẫn ở trạng thái chờ: kiểm tra dữ liệu rồi hiện **bộ tham số sẽ gửi cho `I_CUSTOMER_Ins/_Upd`**. Smoke 57/57 PASS | ✅ 03/09/2026 — chờ nghiệm thu |
| **TRACK C-3 — TRANG CHỦ + NHẬN DIỆN + CHÂN TRANG** (03/09/2026, GĐ chốt): logo thật `static/img/logo_icon.png|logo_full.png|favicon.png` (chép từ KHJ). **`/` và `/banle/` = TRANG CHỦ riêng khung**: `{% block rail %}` rỗng + `.khbl-shell--home` (bỏ rail) → đầu trang logo 56px + tên tiệm + **khối tài khoản** (họ tên · username · EmpID · két · nút Đăng xuất), dưới là **MENU NGANG 6 nút icon 34px** (Bán hàng nổi bật nền vàng · Thâu vào · Bảng giá · Khách hàng · Hóa đơn · Hệ thống), rồi bảng giá + số liệu. Các màn làm việc GIỮ rail (tiết kiệm chiều dọc), logo đặt đầu rail bấm về trang chủ. **CHÂN TRANG** `partials/footer.html` (nền tối, có ở mọi trang TRỪ màn bán hàng vì đã có thanh phím): trái = công ty/địa chỉ/ĐT/MST đọc từ `T_SHOP` (cache 1 giờ), giữa = phiên bản phần mềm + chấm trạng thái kết nối KK, phải = **đồng hồ thời gian thực** (JS 1 nhịp/giây, `window.__khblDongHo` chống chạy nhiều nhịp). Bộ kiểm gộp thành lệnh chính thức **`manage.py smoke_ui` (66/66 PASS)** — dùng `force_login`, KHÔNG đặt lại mật khẩu (set_password đổi session hash và ĐÁ MỌI NGƯỜI ĐANG ĐĂNG NHẬP ra ngoài) | ✅ 03/09/2026 — chờ nghiệm thu |
| **TRACK C-4 — KHUNG CHUNG TOÀN HỆ: TOPBAR + CHÂN TRANG, BỎ RAIL DỌC** (03/09/2026, GĐ chốt): `partials/topbar.html` cao **58px** dùng cho MỌI trang — logo 36px bấm về trang chủ · **menu ngang 7 mục icon 24px** (Tổng quan · Bán hàng · Thâu vào · Bảng giá · Khách hàng · Hóa đơn · Hệ thống, mục đang mở có nền vàng + gạch chân gradient qua `nav_active`) · khối tài khoản (họ tên, username, két, avatar, Đăng xuất). `partials/footer.html` cũng có ở MỌI trang (kể cả màn bán hàng). `.khbl-shell` đổi từ 2 CỘT (rail 64px) sang 3 HÀNG `auto 1fr auto`; `.khbl-rail*` và `partials/rail_item.html` đã XÓA HẲN. ⚠ Topbar+chân trang ăn 107px chiều dọc của màn bán hàng → đã siết các dải cố định (bảng giá 56→50 · dải quyết toán 88→78 · thanh phím 32→26 · chân trang 40) **và sửa lỗi có sẵn: `#pos-work` không kéo giãn** nên lưới giỏ hàng chỉ cao bằng nội dung — thêm `#pos-work{display:flex;flex-direction:column}` + `.pg-ban__body{flex:1;grid-template-rows:minmax(0,1fr)}` → vùng danh sách món **286px**, hơn cả trước khi có topbar (282px). Smoke `manage.py smoke_ui` **91/91 PASS** | ✅ 03/09/2026 — chờ nghiệm thu |
| **TRACK C-1B — LƯU KHÁCH THẬT** (05/09/2026, GĐ yêu cầu): popup Thêm/Sửa gọi `I_CUSTOMER_Ins/_Upd` qua gateway; kiểm trùng `Phone` + `CMND` trước proc và proc kiểm lại; `hx-sync:drop` + khóa server + token chống gửi lặp; khách mới gọi `I_DiemTichLuy_InsFromGT`; ảnh xoay EXIF, thu về tối đa 1600/2000px, nén JPEG theo ngưỡng và đặt tiền tố tên khách không dấu. Dữ liệu khách UPSERT trước, chỉ gọi proc ảnh sau khi đọc lại đúng nên UPSERT fail không để lại ảnh. Thành công: đóng popup → giữ bộ lọc và tải lại danh sách → toast. Sandbox `smoke_customer` PASS tạo/sửa/điểm/ảnh rồi dọn sạch; `smoke_ui` 122/122; backup KK 05/09 02:00 VERIFY OK; `.env PMV_GHI_KK=True`, runtime `target=kk`, `pmv_write_lock=''`. Dòng C/C-2 cũ ghi “chưa ghi/chờ” đã được thay thế bởi trạng thái này. | ✅ 05/09/2026 — đang mở ghi thật |
| **TRACK C-1C — HIỆN ẢNH ĐÃ LƯU + LƯU 2 BƯỚC** (05/09/2026, GĐ yêu cầu): popup Thêm/Sửa + popup Chi tiết khách **hiện lại 3 ảnh đã lưu** (đại diện + 2 mặt CCCD). Ảnh nằm trên ĐĨA của máy SQL PMV (cột `ImagePath/ImagePathMatTruoc/ImagePathMatSau`) → đọc qua kênh chỉ đọc riêng **`gateway.pmv_image_read`** = `OPENROWSET(BULK N'…', SINGLE_BLOB)` (BULK KHÔNG nhận tham số → đường dẫn phải KHỚP REGEX TUYỆT ĐỐI thư mục `D:\PHANMEMVANG\HINHANHKH` + bộ ký tự an toàn + chặn `..`/dấu nháy; ngoài thư mục → `PmvBlocked`). View **`khach_anh` (GET, url `/banle/khach-hang/<cust_id>/anh/<kind>/`)** phục vụ ảnh THEO MÃ KHÁCH — URL không bao giờ nhận path đĩa; `customer.saved_image()`/`_validated_image()` kiểm ảnh JPEG/PNG hợp lệ trước khi trả, header `nosniff` + cache private 5'. **Lưu 2 bước — giữ popup khi chưa hoàn tất**: `upsert()` trả thêm `complete`/`errors`, kiểm THẬT sổ điểm tích lũy (`_has_points` truy `I_DIEMTICHLUY`) + kiểm ảnh đã ghi được đường dẫn + đọc lại hợp lệ; **chưa hoàn tất → KHÔNG `remember_save`**, trả `_khach_luu_kq` "đã lưu thông tin {mã}, chưa hoàn tất" kèm hidden `CustID` (OOB) để bấm Lưu lại KHÔNG mất phần đã ghi (không tạo dòng trùng). `error_message()` giữ nguyên nhân PMV/ODBC hữu ích thay câu chung chung. test_customer 10/10 (có case chặn path ngoài thư mục, popup giữ khi ảnh lỗi). ⚠ `smoke_customer` trên **sandbox FAIL ở bước ghi FILE ảnh** (SQL sandbox không ghi được vào `D:\PHANMEMVANG\HINHANHKH` của nó — lỗi MÔI TRƯỜNG, KHÔNG phải hồi quy: baseline trước khi sửa cũng fail y hệt); logic ảnh xác thực bằng test_customer + chạy thật trên KK. | ✅ 05/09/2026 — chờ nghiệm thu |
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

**QUY TRÌNH — LƯU và HỌC (05/09/2026, GĐ chốt)**: trang **`/he-thong/quy-trinh/`** = cây PHA → BƯỚC
từ 0 đến hoàn thành (model `PmvProcess`/`PmvProcessStep`, migration pmv-0006, module
`apps/pmv/quy_trinh.py`). Mỗi bước: proc · ghi/đọc · ×N · tham số quan trọng · bảng đổi · **KHBL**
(làm / cần kiểm / làm cách riêng / bỏ) · **đối chiếu** (khớp / lệch / chưa) · ghi chú. CRUD bằng
`<dialog>` native (trang pmv standalone, không htmx), ↑↓ đổi chỗ, dời bước sang pha khác. **LƯU**: MỌI
thay đổi → version +1 + ghi lại **`docs/quy_trinh/<CODE>.md`** (sơ đồ mermaid + bảng) và `.json` — git
giữ lịch sử; xóa quy trình khỏi DB vẫn giữ file. **HỌC**: form khung giờ trên trang Quy trình HOẶC nút
📚 trên báo cáo ĐÁNH DẤU SAU (theo `event_seq`) → gom lời gọi trace (bỏ DeriveParameters/NOISE/KHBL,
liên tiếp cùng proc = ×N) thành bước nháp trong pha "Đã học — chưa phân nhóm" (ghi = cần kiểm) + bảng đổi
→ GĐ dời vào pha đúng rồi duyệt; có thể HỌC THÊM vào quy trình sẵn có. Quy trình chuẩn
**`BAN_HANG`** seed từ thao tác thật (`manage.py seed_quy_trinh [--ghi-de]`, dữ liệu `quy_trinh.BAN_HANG`):
**v3 = FULL (GĐ chốt 05/09/2026, dữ liệu `apps/pmv/quy_trinh_ban_hang.py`)**: 9 pha (0 điều kiện · 1 khách
có sẵn/tạo mới/vãng lai · 2 gom hàng vào ĐƠN CHỜ (Ins ngay món đầu, dẻ vụn cùng XML) · 3 tính tiền · 4 DUYỆT ·
5 sau duyệt · 6 HỦY THANH TOÁN C→W · 7 XÓA ĐƠN → *_Log · 8 kiểm soát trùng P-008 & đơn treo) · 30 bước (15 ghi · KHBL làm 18 · cần kiểm 6 · bỏ 1; dẻ vụn đo thật đơn 612 → khớp; thêm I_DiemTichLuy_InsFromGT sau tạo khách; gộp 6 bản GĐ tự học BAN_1·HD_FULL·DON_MOI·HUY_HOA_DON·HUY_THANH_TOAN·KHACH_HANG_MOI), đo từ 4
thao tác thật 05/09 (604 bán · 605 tạo-xóa · 606 2 món + tạo khách + hủy TT + xóa · 604 xóa đơn đã TT). **6 điểm
GĐ chốt** ghi trong ghi chú bước (nhãn "cần kiểm" = KHBL PHẢI SỬA khi làm GĐ3): (1) `Ins` NGAY món đầu như app →
SP khóa P-008 mọi máy; kèm nút XÓA đơn bỏ dở + nhắc đơn W treo quá **30 phút** (GĐ chốt 05/09) · (2) xóa đơn
ĐÃ THANH TOÁN = 2 bước rõ ràng, 2 xác nhận · (3) BirthDate rỗng → gửi **01/01/1900** (app gửi ngày hôm nay = sai)
· (4) dẻ vụn đi theo MÃ ĐƠN (Upd/Del kéo theo) · (5) HĐĐT giữ "cần kiểm" · (6) giữ mã BAN_HANG; sửa đơn đã TT =
nhánh pha 6→2/3→4. Vendor tự chặn SP đang trong đơn: **P-008 "Mã hàng … đang chờ duyệt mua bán"** (proc dòng 219,
mọi W/C, mọi máy) — KHBL chỉ cần bổ sung thông điệp "đơn nào đang giữ" + nút MỞ. `ErrorLog` +1 mỗi lần
`T_TILL_TXN_Del` là vết kết nối vendor tự ghi, không phải lỗi. Sau này thêm quy trình khác: ĐÁNH DẤU → làm trên
app → HỌC. Smoke **`smoke_quy_trinh` 31/31**.

**LUỒNG BÁN HÀNG THẬT ĐÃ ĐO (05/09/2026)**: `docs/LUONG_BAN_HANG_PMV_20260905.md` — 40 lời gọi
+ bản đồ proc→18 bảng (cột nào đổi) + đối chiếu với `bill.py` + 3 điểm phải kiểm trên sandbox
trước GĐ3 (`p_TrnDateTime_Upd_GDN`, 60 cột dòng hàng khi Upd, XML `HoaDonDienTu_UpdateMa` ghi
`DonGiaHDDT/AmountHDDT`). ĐỌC TRƯỚC KHI SỬA bill.py.

## 8b. ICON TOPBAR (bộ tranh vàng GĐ đưa 03/09/2026)

Ảnh gốc ~1254px, **1,5–2,4 MB mỗi cái** — 7 cái là ~13,5 MB mỗi lần tải trang, không được
nhúng thẳng. Bản dùng thật nằm ở **`static/img/ico/<key>.png` 96px (~19 KB)**, sinh từ ảnh
gốc bằng System.Drawing của Windows (dự án KHÔNG có Pillow, cũng không cần thêm).

Key ↔ ảnh gốc: `tong`←tong-quan · `ban`←ban-hang · `thau`←thau-vao · `gia`←gia-vang ·
`khach`←khach-hang · `hoadon`←hoa-don · `hethong`←he-thong. **Giữ ảnh gốc trong `static/img/`**
(nguồn để sinh lại). Sinh lại bằng PowerShell:

```powershell
Add-Type -AssemblyName System.Drawing
# đọc ảnh gốc → Bitmap 96x96, InterpolationMode HighQualityBicubic → lưu vào static/img/ico/
```

Thẻ template **`{% icon_anh "key" 26 %}`** (pos_extras) chỉ dùng cho topbar. Mọi chỗ khác
trong app vẫn dùng **`{% icon %}`** — SVG 1 nét, ăn theo `currentColor`, đổi màu theo ngữ
cảnh; đừng thay bằng ảnh. Ảnh nhiều màu nên CSS `.khbl-ico-anh` giảm bão hòa khi mục đang
tắt, chỉ lên đủ màu khi rê chuột / đang mở. Topbar cao **64px** (trước 58px) để chứa icon 26px.

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
  - **Byte đầu UTF-8 mồ côi (05/09/2026)** — `_sua_byte_dau_mo_coi` (Python) + `suaByteDauMoCoi` (JS):
    chuỗi ĐÃ sửa nửa chừng còn lộ byte đầu (vd `NÄm CÄn` giữ C4 của `ă` nhưng mất byte sau) — không
    giải mã lại cả câu được vì các từ khác đã đúng Unicode; chỉ thay khi còn **đúng 1 ứng viên** theo
    hoa/thường + nguyên/phụ âm bên cạnh, mơ hồ thì giữ nguyên + cảnh báo đối chiếu thẻ.
  - **Chuẩn hóa KHI ĐỌC (05/09/2026)** — `services._dep_khach` chạy `chuan_hoa` cho `CustName/Address/NoiCap`
    lúc đọc → DS + popup khách hiện ĐÚNG ngay với dữ liệu cũ đã lưu hỏng; lần Sửa+Lưu kế tiếp ghi bản sạch.
    ⚠ Node chưa cài trên máy này → `tests/qr.test.cjs` không chạy được; bản JS đối chiếu qua smoke_vn_text (cùng thuật toán).
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
