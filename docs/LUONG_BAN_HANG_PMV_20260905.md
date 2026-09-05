# LUỒNG BÁN HÀNG THỰC TẾ CỦA PMVGoldRT — đo trên máy KK ngày 05/09/2026

> **PHỤ LỤC SỐ LIỆU** của quy trình chuẩn FULL `docs/quy_trinh/BAN_HANG.md` (v3, GĐ chốt 05/09/2026 — trang
> `/he-thong/quy-trinh/BAN_HANG/`). File này giữ nguyên số đo chi tiết của thao tác đầu (bán 1 món); các thao tác
> tạo-xóa, tạo khách, hủy thanh toán, xóa đơn đã thanh toán được tóm trong quy trình FULL và `logs\pmv\2026-09-05\`.

> Nguồn: trace SQL KK (40 lời gọi, tham số thật, EventSequence 842826–843739) + dò thay đổi
> 2 phút + so từng dòng KK ↔ sandbox (sandbox = trạng thái TRƯỚC thao tác, sync 04:37, diff=0).
> Thao tác: GĐ bán 1 món trên PMVGoldRT lúc 05:56:53–05:58:41 (giờ KK, chậm ~35s so máy web).
> Hóa đơn thật **TRB260900000604 / BillCode 26-09-05-000001**, khách CU2405000000234,
> món 1D60001400 "Dây mì" 18K 123,4 ly · giá 8.900/chỉ · công 490 → 11.473.000; bớt 3.000 +
> công thêm 30.000 → **khách trả 11.500.000**. Status W → C, két TIL260500000001.

## 1. Dòng thời gian lời gọi (RPC của app, theo thứ tự)

| # | Giờ KK | Proc | Ghi/đọc | Ghi chú |
|---|---|---|---|---|
| 1 | 05:56:53 | `T_PRODUCT_GetByCodeForSell` | đọc | quét tem; `p_CustID=''`, `p_TillID=` két đang mở, `p_ShopID_XRate=''` |
| 2 | 05:56:53 | **`TRN_RT_BUYSELL_Ins`** | GHI | **ngay sau lượt quét đầu**: khách = walk-in `CU0000000000000`, Status `W`, PayAmount = SellTotal, EmpID = NV mặc định của user, `p_TrnDateTime_Upd_GDN` = giờ hiện tại. Bên trong: `SYS_CodeMasters_Gen` (TRB 603→604 = TrnID), `SYS_BILL_COUNTER_Gen` (dòng mới YY/MM/DD CurValue=1 → BillCode `26-09-05-000001`) |
| 3 | 05:56:53 | `TRN_RT_BUYSELL_Get` | đọc | đọc lại lấy mốc `TrnDateTime_Upd` |
| 4–17 | 05:57:14 → 05:58:09 | **`TRN_RT_BUYSELL_Upd` × 7**, mỗi lần kèm `Get` | GHI | **app TỰ LƯU sau MỖI lần đổi 1 ô** (chọn khách, đổi NV bán, gõ bớt, gõ công thêm…). Mỗi Upd gửi TRỌN dòng hàng vừa đọc từ Get (60 cột, có cả `*_ZNS`, `TienCongHDDT`, `SellRateNoTaskPrice`…) + `p_TrnDateTime_Upd` = mốc của Get liền trước. Lần cuối: CustID thật, EmpID `EMP250800000001`, `Discount=3000`, `TaskPriceAdd=30000`, `PayAmount=11500000` |
| 18 | 05:58:09 | **`TRN_RT_BUYSELL_Complete`** `@p_TrnID,@p_UserID,@p_ThuHo='0'` | GHI | 54ms, w=14 — khối ghi lớn nhất (xem mục 2) |
| 19 | 05:58:10 | `T_TILL_TXN_DETAIL_GetByTrnID` | đọc | kiểm chưa có sổ quỹ |
| 20 | 05:58:10 | **`T_TILL_TXN_Proc`** `@p_TrnIDs,@p_TillID,@p_UserID` | GHI | vào sổ quỹ (két) |
| 21–29 | 05:58:11–13 | `SMS_Check` → `PHONE_LIST_Get` → `SYS_SMS_SYSTEM_GetByTrnCode` → `SYS_SMS_SRT_GetInfo` → `SYS_Notification_Templates_Check` → `tbc_TinNhan_Ins` ×2 + `tbc_TinNhan_Upd` ×2 | GHI | Zalo ZNS 2 mẫu (321283 · 320573) — **cả 2 lỗi** "Lỗi SMS Service" (MaTrangThai 1000). NGOÀI phạm vi KHBL |
| 30 | 05:58:13 | `HoaDonDienTu_UpdateMa` | GHI | mã HĐĐT TRỐNG (không phát hành) nhưng `@input` = XML dòng hàng có **số liệu HĐĐT đã quy đổi** (mục 3) |
| 31–33 | 05:58:13 | `TRN_RT_BUYSELL_Get` · `TRN_PROMOTION_LOG_Lst` · **`rptSRT_PrintBill`** `@xemtruoc='0'` | đọc | in hóa đơn — proc in **tự ghi RetailLog** |
| 34 | 05:58:41 | `TRN_RT_BUYSELL_Lst` | đọc | app làm mới danh sách ngày |

Trước mỗi proc mới app gọi `sys.sp_procedure_params_managed` (DeriveParameters) — KHBL làm tương
đương bằng `sys.parameters` (`PmvClient.params_of`).

## 2. Bản đồ proc → bảng (đo được, 18 bảng đổi)

| Bảng | Kiểu | Do proc nào | Cột/dòng đổi (so KK ↔ sandbox) |
|---|---|---|---|
| `TRN_RT_BUYSELL` | INSERT rồi UPDATE | Ins · Upd ×7 · Complete | Status W→C · CustID · EmpID · Discount 3000 · TaskPriceAdd 30000 · PayAmount 11.500.000 · TotalAmountHDDT 11.473.000 · `TrnDateTime_Upd` đổi mỗi bước · **TrnTime GIỮ 05:56:53 dù Upd gửi 05:58:10** |
| `TRN_RT_BUYSELL_SELL` | INSERT (1 dòng/món) | Ins/Upd | đủ cột dòng hàng + **`DonGiaHDDT=9.319.286` · `AmountHDDT=11.500.000`** (giá/tiền hiệu dụng sau bớt/công thêm — ghi bởi Complete hoặc UpdateMa, chưa tách được) |
| `T_PRODUCT` | UPDATE | Complete | `Status I→S` · `SellTrnID=TRB…` · `SellTrnType=SRT` · `SellOutDate=05:58:09.997` |
| `T_PRODUCT_TRACKING` | INSERT | Complete | STT 699484 · TrnCode SRT · CRDR `-` · TrnRefID = phiếu nhập gốc `TI2609000000859` (RefCRDR `+`) · MaHangGoc |
| `I_GOLD_BAL` | UPDATE | Complete | quầy TSN110500000004: Qty 349→348 · TotalWei −123,4 · GoldWei −123,4 · Stamp_Wei −1,1 |
| `TonKho` | INSERT (1 dòng/ngày/quầy/tuổi) | Complete | Date 05/09 · SectionID · GoldCcy 18K · RTSell_Qty 1 · RTSell_GoldWei 123,4 · BuySell_* |
| `TRN_DAILY_LOG` | INSERT ×4 | Complete (`INS_TRN_DAILY_LOG`/`TRN_DAILY_LOG_Ins`) | LOG260900006393–6396: AmountTag `PROD_WEI` · `PROD_GOLDWEI` · `PROD_QTY` · `PROD_STAMPWEI`, CRDR `-`, TrnDesc "XUẤT BÁN HÀNG NGÀY 05/09/2026" |
| `SYS_CODEMASTERS` | UPDATE ×3 | Ins (TRB) · Complete (LOG +4) · Proc (TTX) | TRB 603→604 · LOG 6392→6396 · TTX 803→804 |
| `SYS_BILL_COUNTER` | INSERT | Ins (`SYS_BILL_COUNTER_Gen`) | YY=26 MM=09 DD=05 CurValue=1 → BillCode ngày đầu |
| `I_CUSTOMER` | UPDATE | Complete | `LastTradingDate` 2024-05-09 → 2026-09-05 |
| `T_CUSTOMER_DEBT` | UPDATE | Complete | `LastModify` = giờ chốt (số dư 0 không đổi) |
| `I_LICHSUTICHLUYDIEM` | INSERT | Complete (`I_LichSuTLD_Ins`) | TrnRefID · CustID · TrnTypeDtl SRT · SoTienTT 11.473.000 · TinhCong 0 |
| `I_DIEMTICHLUY` | (gọi `I_DiemTichLuy_Ins` nhưng KHÔNG đổi) | Complete | điểm = 0 với hóa đơn này |
| `T_TILL_TXN` | INSERT | T_TILL_TXN_Proc (`TRN_TILL_TXN_Ins`) | TTX260900000804 · TillID · TrnRefID · BillCode · TrnType RT · TrnCode SRT · CustID · TrnTotalAmount 11.500.000 · Status P |
| `T_TILL_TXN_DETAIL` | INSERT +1 | T_TILL_TXN_Proc | (không có cột khóa để lọc) |
| `T_TILL_BAL` | UPDATE | T_TILL_TXN_Proc | két TIL260500000001: InFlow +11.500.000 · TillBal +11.500.000 |
| `RetailLog` | INSERT ×2 | Complete · rptSRT_PrintBill | "Complete_success_TinhTrangC" · "0 - TinhTrangC" |
| `tbc_TinNhan` | INSERT ×2 (+Upd) | tbc_TinNhan_Ins/Upd | 2 ZNS lỗi — ngoài phạm vi |

Proc chạy **bên trong** proc (thấy qua bộ đếm, không có trong trace RPC): `SYS_BILL_COUNTER_Gen`,
`SYS_CodeMasters_Gen`, `fun_GetHS`, `INS_TRN_DAILY_LOG`, `TRN_DAILY_LOG_Ins`, `I_LichSuTLD_Ins`,
`I_QuyDiemTichLuy`, `I_DiemTichLuy_Ins`, `TRN_TILL_TXN_Ins`, `GoldWeightToChar`, `base64_encode`,
`fun_GetExchangeRate` (in phiếu).

## 3. Số liệu HĐĐT app tự quy đổi khi gọi `HoaDonDienTu_UpdateMa` (dù không phát hành)

Trong XML `@input` (Table1) app gửi thêm: `SoLuong=1.234` (chỉ) · `DonGiaVang=8.900.000` ·
`SellRate=9319287` · `GiaHDDT=9319286` (= 11.500.000 ÷ 1,234) · `ThanhTienHDDT=11.500.000` ·
`TongChietKhau=-27000` · `TongTienThem=30000` · `DiscountAmount=27000` (= 30.000 − 3.000) ·
`AmountAfterDiscount=11.446.000` · `TienCongHDDT=490.000` · `SellAmountNoTaskPrice=10.983.000`.
Tham số ngoài: `@LoaiGiaoDich='SRT'`, mã HĐĐT/số HĐ/GUID/json **đều trống**, `@TotalAmountHDDT='11473000'`.

## 4. Đối chiếu với `apps/pos/bill.py` của KHBL (03/09/2026)

| Điểm | App PMVGoldRT | KHBL hiện tại | Kết luận |
|---|---|---|---|
| Thời điểm tạo hóa đơn | `Ins` NGAY sau lượt quét đầu (walk-in, NV mặc định), rồi `Upd` sau từng ô | Gom trong session, `Ins`/`Upd` khi bấm THANH TOÁN | Khác cách nhưng **kết quả DB tương đương**; cách KHBL ít lời gọi hơn, không để hóa đơn `W` treo khi bỏ dở. GIỮ cách KHBL |
| `p_TrnDateTime_Upd_GDN` ở Ins | giờ hiện tại | `MOC_TRONG` (1900) | **Cần kiểm trên sandbox**: giá trị này có được ghi vào cột nào không |
| `p_TrnTime` ở Upd | gửi giờ hiện tại nhưng DB giữ TrnTime lúc Ins | gửi giờ hiện tại | Proc bỏ qua → không sao |
| Dòng hàng gửi lại khi Upd | trọn 60 cột từ Get (có `PCcyRate`, `MainSectionName`, `TienCongHDDT`, `SellRateNoTaskPrice`, `SellAmountNoTaskPrice`, `TuoiPho`, `*_ZNS`, `UnitCode/UnitDesc`, `RoundValue`, `TienBot`, `SuDungTTL`…) | `COT_DONG_BAN` 42 cột | **Cần kiểm trên sandbox** bằng ĐÁNH DẤU: Upd của KHBL rồi so dòng `TRN_RT_BUYSELL_SELL` với dòng app tạo — đặc biệt `DonGiaHDDT/AmountHDDT` |
| Chốt | `Complete` → `T_TILL_TXN_DETAIL_GetByTrnID` → `T_TILL_TXN_Proc` | `Complete` → `T_TILL_TXN_Proc` | Khớp (bỏ 1 lượt đọc kiểm — nên THÊM lại để idempotent: đã có sổ quỹ thì không Proc lần 2) |
| Công thức tiền | Pay = Total − Discount + TaskPriceAdd = 11.473.000 − 3.000 + 30.000 ✓ | `tinh_tong` cùng công thức | **Khớp 100%** trên hóa đơn thật đầu tiên có bớt + công thêm |
| `HoaDonDienTu_UpdateMa` | gọi với XML số liệu HĐĐT đã quy đổi, mã trống | GĐ chốt gọi tham số TRỐNG | **Cần kiểm trên sandbox**: nếu proc này là nơi ghi `DonGiaHDDT/AmountHDDT` vào `TRN_RT_BUYSELL_SELL` thì KHBL phải gửi XML giống app (tính `GiaHDDT = Pay ÷ chỉ`, `DiscountAmount = TaskPriceAdd − Discount`…) |
| ZNS/SMS | 5 proc đọc + 4 ghi, đang lỗi | không làm | Đúng phạm vi GĐ chốt |
| In phiếu | `rptSRT_PrintBill` (ghi RetailLog) | in giấy đảm bảo riêng | Khác chủ đích; nếu muốn có vết "đã in" trong RetailLog thì gọi thêm proc này (chỉ đọc, đã có trong PROC_READ_ALLOW? — chưa, cần duyệt) |

## 5. Luồng chuẩn đề xuất cho webapp (GĐ3)

1. Quét → `T_PRODUCT_GetByCodeForSell` (như hiện tại), giữ trọn `row`.
2. THANH TOÁN → `TRN_RT_BUYSELL_Ins` (Status W, đủ 37 tham số, XML dòng hàng) → đọc `Get` lấy mốc
   → nếu người bán còn sửa → `Upd` với mốc vừa đọc (đúng như app) → `Complete` → kiểm
   `T_TILL_TXN_DETAIL_GetByTrnID` (chưa có) → `T_TILL_TXN_Proc` → `HoaDonDienTu_UpdateMa`
   (XML theo mục 3, mã trống) → đọc lại đối chiếu 6 bảng: `TRN_RT_BUYSELL.Status='C'`,
   `T_PRODUCT.Status='S'`, `T_TILL_TXN` có dòng, `T_TILL_BAL` tăng đúng PayAmount, `I_GOLD_BAL`
   giảm đúng trọng lượng, `TRN_DAILY_LOG` +4.
3. Mọi bước chạy trên **sandbox trước**, dùng nút ĐÁNH DẤU TRƯỚC/SAU để so với bản đồ mục 2
   — khớp 18 bảng (trừ `tbc_TinNhan`, `RetailLog` của in) thì mới go-live.
