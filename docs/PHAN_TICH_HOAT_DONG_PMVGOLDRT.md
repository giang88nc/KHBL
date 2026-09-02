# PHÂN TÍCH CÁCH HOẠT ĐỘNG CỦA PMVGoldRT — từng bước, từ chứng cứ thật

> Nguồn: trace SQL 02/09/2026 14:37→21:09 (4.394 lời gọi, 110 hóa đơn, 78 proc, 2 máy trạm KK/QQ),
> dấu vết hóa đơn thật trên bảng PMV, source 23 proc chính, diff sandbox 13:59 ↔ PMV thật.
> Tài liệu này là **spec API sống** cho GĐ3 (mở kênh ghi). Cập nhật khi vendor đổi version (check_pmv).

## 0. Bức tranh chung

| Đặc điểm | Chứng cứ |
|---|---|
| App .NET, **2 máy trạm** | host KK 3.418 lời gọi, QQ 976; user `US1806000000001` (KK) và `US2410000000001` (QQ); két riêng `TIL260500000001` (KK) / `TIL110500000001` (QQ) |
| **100% nghiệp vụ qua stored proc**, không SQL trần | 0 câu ad-hoc ghi; ad-hoc chỉ là `sp_cursoropen` đọc view in tem |
| App **tự tra tham số proc lúc chạy** (`sp_procedure_params_managed` trước mỗi proc lạ) | ADO.NET DeriveParameters → vendor đổi chữ ký proc, app vẫn chạy — KHBL cũng nên đọc `sys.parameters` thay vì hard-code |
| Tham số **toàn kiểu chuỗi**: ngày `'02/09/2026'`, giờ `'14:38:06'`, tiền `'7050000'`; **dòng hàng = XML** `<NewDataSet><TRN_RT_BUYSELL_SELL>…` | mọi `_Ins/_Upd` |
| Mọi proc ghi = `BEGIN TRAN … IF @@Error GOTO ABORT … COMMIT`, `RETURN 0/-1`, ghi `RetailLog`/`ErrorLog` | source `_Ins/_Complete/_Del` |
| Mã sinh trong proc: `SYS_CodeMasters_Gen` (TrnID `TRB`/`TBG`/`TI`/`TO`/`CU`/`TTX`/`LOG` + YYMM + seq) · `SYS_BILL_COUNTER_Gen` (BillCode `26-09-02-000233`, đếm theo ngày+tiệm) · `SYS_PRODUCT_COUNTER_Gen` (mã hàng `9N60016572`) | app luôn gửi `@p_TrnID=''` rồi `_Get` để lấy mã |
| **Khóa lạc quan**: `TrnDateTime_Upd` — proc so với giá trị app gửi, lệch → "Dữ liệu đã được chỉnh sửa bởi …" | source `TRN_RT_BUYSELL_Ins/_Upd` |
| Hành vi cấu hình qua `SYS_PARAMETERS`: `TaoSoHDKhiThanhToan` (BillCode sinh lúc tạo hay lúc chốt), `CongNoBanLe`, `WalkInCustID`, `MaTienTeHeThong`, `UnitWeight`, `TLDiem_SuDungThanhTienCuoiCung`, `SuDungLuatTichDiemRieng` | source `_Complete` |
| Đơn vị vàng nội bộ: **1 chỉ = 100 đơn vị** (nhẫn 1 chỉ `TotalWeight=100`, giá `SellRate=14100` = 14.100.000 ₫/chỉ → `SellAmount=14.100.000`); thâu `GoldWeight=1000` = 1 lượng × `BuyRate 13600` → 136.000.000 | dòng TRN_RT_BUYSELL_SELL / TRN_RT_BUYGOLD_Ins |

## 1. BÁN HÀNG (SRT) — 8 bước, nhìn từ 3 hóa đơn thật

Mẫu: `TRB260900000220` (14:37, khách đổi vàng cũ), `TRB260900000300` (18:54, thẻ), `TRB260900000329` (20:43, máy QQ).

**Bước 0 — quét mã hàng**: `T_PRODUCT_GetByCodeForSell @p_ProductCode, @p_TaskPrice=0, @p_CheckRealSL=1, @p_TillID` — kiểm hàng tồn thật, lấy giá/công.

**Bước 1 — TẠO PHIẾU**: `TRN_RT_BUYSELL_Ins @p_TrnID='', @p_TrnDate, @p_TrnTime, @p_CustID='CU0000000000000' (khách lẻ), @p_SellTotalAmount, @p_BuyTotalAmount (vàng cũ), @p_TotalAmount, @p_PayAmount, @p_Status='W', @p_CreatedBy, @p_EmpID, @p_ShopID='TSP141100000001', @p_TillID, @p_BanLeBanSi='BL', @p_Trn_RT_BUYSELL=<XML dòng hàng>`
Trong proc (369 dòng): kiểm phiếu GDN chưa bị sửa → `SYS_CodeMasters_Gen 'TRN_RT_BUYSELL'` sinh TrnID → nếu `TaoSoHDKhiThanhToan=0` sinh BillCode ngay → `INSERT TRN_RT_BUYSELL` (W) → bung XML vào bảng tạm, kiểm lỗi `P-011/P-010/P-007/PAY-003` (hàng đã bán, không tồn tại, khác quầy…) → `INSERT TRN_RT_BUYSELL_SELL` → `UPDATE T_PRODUCT` (giữ hàng) → `INSERT TRN_RT_BUYSELL_BUYGOLD` (vàng cũ khách đổi). App gọi ngay `TRN_RT_BUYSELL_Get` để nạp lại phiếu + lấy TrnID.

**Bước 2 — SỬA PHIẾU (lặp 2–5 lần)**: mỗi thao tác trên màn hình (chọn khách, đổi giá, thêm dòng) → `TRN_RT_BUYSELL_Upd` gửi LẠI TOÀN BỘ phiếu + XML (proc DELETE chi tiết cũ, INSERT lại, UPDATE header, xử lý đổi quà `I_DOIQUA`, đặt cọc, GDN) rồi `_Get`. Cách nhau vài giây→phút (người bán thao tác). App **không** có proc sửa từng dòng.

**Bước 3 — CHỐT** (`TRN_RT_BUYSELL_Complete @p_TrnID, @p_UserID, @p_ThuHo='0'` — proc nặng nhất, 721 dòng), trong 1 transaction:
1. kiểm tồn tại (`B-001`) và Status='W' (`B-002`);
2. nếu `TaoSoHDKhiThanhToan=1`: sinh BillCode; `UPDATE TRN_RT_BUYSELL SET Status='C'`;
3. đặt cọc (kiểm `TRN_DATCOC`), công nợ (`T_CUSTOMER_DEBT`, `I_LichSuCongNo` khi `CongNoBanLe=1`);
4. **từng món**: `UPDATE T_PRODUCT` (Status `S`, `SellTrnID`, `SellOutDate`, `CustID`), `SECTION_PRODUCT`, `ChangeTaskPrice_Log` nếu đổi công;
5. **4 lần `TRN_DAILY_LOG_Ins`** cho mỗi món (AmountTag `PROD_WEI`, `PROD_GOLDWEI`, `PROD_QTY`, `PROD_STAMPWEI`, CRDR `-`, RelatedID = quầy `TSN…`) → mỗi lần **UPDATE `I_GOLD_BAL`** (tồn theo quầy + loại vàng) — đây là SỔ TỒN;
6. `INSERT T_PRODUCT_TRACKING` (SRT `-`, tham chiếu phiếu nhập `PRODIN +`);
7. `EXEC TRN_TILL_TXN_Ins` → `T_TILL_TXN` Status **`U`** (chờ két) + `T_TILL_TXN_DETAIL` (VND `+` tiền; `D9999 +` vàng cũ thâu);
8. điểm tích lũy: `spQuyDiemTL`, `I_LichSuTLD_Ins`, `I_QuyDiemTichLuy`, `I_DiemTichLuy_Ins` → `I_LICHSUTICHLUYDIEM`, `I_DIEMTICHLUY`; bỏ qua nếu khách = `WalkInCustID`;
9. `UPDATE I_CUSTOMER` (ngày giao dịch cuối, nâng hạng);
10. COMMIT → `RetailLog 'Complete_success_TinhTrangC'`.

**Bước 4 — THẺ/CHUYỂN KHOẢN (nếu có)**: `TRN_RT_BUYSELL_SELL_LstM` → `CARDPAY_Ins @p_TillID, @p_TillTxnID='TTX…', @p_TrnID, @p_TypeTrade='SRT', @p_ProductIDs, @p_CardAmounts, @p_Amount, @p_List=<XML <Card><NumberBank><PayType><Amount>>` → `TRN_RT_BUYSELL_CardPay`, UPDATE `CardPay` header, `TRN_TILL_TXN_Upd`.

**Bước 5 — XÁC NHẬN KÉT**: `T_TILL_TXN_DETAIL_GetByTrnID` → `T_TILL_TXN_Proc @p_TrnIDs='TRB…' (nhiều mã nối bằng '@'), @p_TillID, @p_UserID` → `T_TILL_TXN` **U→P**, cập nhật **`T_TILL_BAL`** (số dư két theo TillID + GoldCcy: InFlow/OutFlow) — đây là SỔ QUỸ.

**Bước 6 — SMS/Zalo**: `SYS_SMS_SRT_GetInfo`, `SMS_Check`, `PHONE_LIST_Get`, `SYS_SMS_SYSTEM_GetByTrnCode`, `SYS_Notification_Templates_Check` → `tbc_TinNhan_Ins` (JSON Zalo OA: `TempID`, `OAID`, `Params [tên khách, BillCode]`) + `tbc_TinNhan_Upd` (trạng thái gửi). 2 tin/hóa đơn (loại 3 và 4).

**Bước 7 — HĐ ĐIỆN TỬ (luôn gọi, kể cả hóa đơn thường)**: `HoaDonDienTu_UpdateMa @LoaiGiaoDich='SRT', @TrnID, @input=<XML dòng hàng>, @TotalAmountHDDT, @smaHoaDonDT='', @SoHDDT='', @InvoiceGUID=''` → chỉ `UPDATE TRN_RT_BUYSELL.TotalAmountHDDT` + `INSERT SYS_INVOICE_HIST`. Với tham số trống = **không phát hành** (`MAHDDienTu` NULL, `SYS_INVOICE` 0 dòng) — là bước "chuẩn bị" HĐĐT.

**Bước 8 — IN**: `TRN_PROMOTION_LOG_Lst` → `rptSRT_PrintBill @p_TrnID, @xemtruoc='0'` → `RetailLog '0 - TinhTrangC'`. In lại = gọi lại (329 in 2 lần).

**HỦY PHIẾU** (10 lần/buổi): `T_TILL_TXN_Del @p_TrnRefID, @pType='SRT', @pCongNoBanLe, @p_UserUpd, @p_TrnDateTime_Upd, @p_Type='1'` (2.327 dòng: đảo `T_TILL_BAL`, đảo `I_GOLD_BAL`, xóa tracking/điểm, trả `T_PRODUCT` về tồn, chép mọi bảng sang `*_Log`) **rồi** `TRN_RT_BUYSELL_Del @p_TrnID, @p_UserUpd, @p_TrnDateTime_Upd, @p_LogDel='0'` (soft delete + `*_Log`). Thứ tự: két trước, phiếu sau.

**Dấu vết 1 hóa đơn C trên DB** (`TRB260900000329`): TRN_RT_BUYSELL 1 · _SELL 1 · _BUYGOLD 1 · _Log/_SELL_Log 1+1 · T_PRODUCT 1 (SellTrnID) · T_PRODUCT_TRACKING 1 · TRN_DAILY_LOG 4 · T_TILL_TXN 1 (+Log) · T_TILL_TXN_DETAIL 2 · I_LICHSUTICHLUYDIEM 1 · RetailLog 4 = **13 bảng, ~20 dòng** cho 1 món hàng.

## 2. THÂU VÀNG (BRT) — `TBG…`

`TRN_RT_BUYGOLD_Ins @p_TrnID='', @p_GoldCode='D9999', @p_GoldWeight='1000', @p_BuyRate='13600', @p_PercentValue='100', @p_TotalAmount, @p_CustPay, @p_TillID, @p_PriceCcy='VND', @p_CcyRate='1000'…` → TBG id + BillCode → (tạo khách nếu mới: mục 5) → `TRN_RT_BUYGOLD_Upd` (gán khách/NV) → `TRN_RT_BUYGOLD_Complete @p_TrnID` (BillCode, Status, `TRN_TILL_TXN_Ins` chi tiền + nhận vàng, điểm, công nợ) → nếu chuyển khoản `CARDPAY_Ins @p_TypeTrade='BRT', @p_Amount=-136000000` (**âm = chi**) → `T_TILL_TXN_Proc`. Biến thể `TRN_RT_BUYGOLD_CompleteMore @p_TrnIDs='TBG…@'` chốt nhiều phiếu (danh sách nối `@`). Thâu KHÔNG ghi `TRN_DAILY_LOG` — vàng thâu vào két (`T_TILL_TXN_DETAIL D9999 +`), không vào tồn quầy.

## 3. NHẬP HÀNG = TẠO SẢN PHẨM (73 lần/buổi) — `TI…`

`TRN_PRODUCT_IN_Ins @p_TrnID='', @p_InOut='I', @p_SectionID='TSN…' (quầy), @p_GoldCode='18K', @p_ProductDesc, @p_GroupID, @p_Quantity, @p_TotalWeight='177.900', @p_TaskPrice, @p_Supplier, @p_ProductCode='' …` (78 tham số) → TI id, `TRN_PRODUCT_IN` + `_DT` Status W → `TRN_PRODUCT_IN_GenProductCode @p_TrnIDs` (`SYS_PRODUCT_COUNTER_Gen` → mã `1L60001489`) → in tem `rptPrintStamp` (cursor trên `vwPrintStamp_ProductIn`) → **duyệt** `TRN_PRODUCT_IN_Appr @p_TrnID, @p_Type='PRODIN', @p_AuthUserID` → `INSERT T_PRODUCT` (hàng chính thức), `SECTION_PRODUCT`, `TRN_DAILY_LOG_Ins +` (tồn quầy `I_GOLD_BAL` tăng), `T_PRODUCT_TRACKING PRODIN +`, kiểm SMS `IN`. **Không có proc tạo T_PRODUCT trực tiếp** — hàng chỉ ra đời qua duyệt phiếu nhập.

## 4. XUẤT HÀNG — `TO…`
`T_PRODUCT_GetByCodeForOut` → `TRN_PRODUCT_OUT_Ins` (TO id, W) → `TRN_PRODUCT_OUT_Appr @p_TrnID, @p_AuthUserID` (BillCode, `T_PRODUCT` status, `DAILY_LOG -`, TRACKING). Quan sát: sửa món = **xuất mã cũ + nhập lại mã mới** (1L60001112 → "lắc tt bi" 177,9).

## 5. KHÁCH HÀNG (37 khách mới/buổi — tạo ngay trong luồng bán/thâu)
`SYS_LOADCOMBO I_CUSTOMER_GROUP/TYPE`, `I_GIAODICH_Lst` → `I_CUSTOMER_Ins @p_CustID='', @p_CustName, @p_Phone, @p_Address, @p_BirthDate (app điền = ngày tạo!), @p_Gender, @TuDongNangHang='1', @MaGD=<XML loại giao dịch>, @p_NoiCap='Cục Cảnh Sát QLHC về TTXH'…` (35 tham số) → CU id, `I_CUSTOMER`, `I_GIAODICH_KHACHHANG`, `SHOP_CUSTOMER` → `I_DiemTichLuy_InsFromGT @p_CustID` (mở sổ điểm `I_DIEMTICHLUY`). Khách lẻ = `CU0000000000000` (`WalkInCustID`).

## 6. BẢNG GIÁ
`I_XRATE_Ins @p_RateDate, @p_RateTime, @p_InputXML=<XML bảng giá>, @p_ShopID` → DELETE+INSERT `I_XRATE`, INSERT `I_XRATE_HIST`; máy trạm poll `I_XRATE_GetAll` liên tục. (Buổi chiều 02/09 không đổi giá — chưa có mẫu trace, có source.)

## 7. BA "SỔ" MÀ MỌI NGHIỆP VỤ ĐỀU CHẠM

| Sổ | Bảng | Ai ghi |
|---|---|---|
| **Tồn kho theo quầy** | `TRN_DAILY_LOG` (4 tag/món) → `I_GOLD_BAL` (SectionID + GoldCode) · `T_PRODUCT.Status` · `SECTION_PRODUCT` · `T_PRODUCT_TRACKING` | `TRN_DAILY_LOG_Ins` (được `_Complete`, `_IN_Appr`, `_OUT_Appr`, `_Del` gọi) |
| **Két/quỹ** | `T_TILL_TXN` (U→P) · `T_TILL_TXN_DETAIL` (theo GoldCcy) · `T_TILL_BAL` (số dư két) · `*_CardPay` | `TRN_TILL_TXN_Ins` (trong `_Complete`) + `T_TILL_TXN_Proc` + `CARDPAY_Ins` |
| **Khách/điểm/công nợ** | `I_CUSTOMER` · `I_DIEMTICHLUY` · `I_LICHSUTICHLUYDIEM` · `T_CUSTOMER_DEBT` · `I_LichSuCongNo` | `_Complete` (bán/thâu), `I_CUSTOMER_Ins` |

Ghi thẳng bảng mà bỏ 1 trong 3 sổ là lệch ngay — đó là lý do RULE 2 (chỉ gọi proc).

## 8. Số liệu buổi chiều (diff sandbox 13:59 → PMV 21:10)
102 HĐ bán (+156 dòng hàng) · 22 thâu · 74 nhập · 4 xuất · 35 khách mới · +820 daily log · +124 till txn · +214 SMS · +49 công nợ · +8 ErrorLog (lỗi app ghi lại — nên đọc bảng này định kỳ).

## 9. HỆ QUẢ CHO KHBL (GĐ3)

1. **Công thức replay BÁN**: `GetByCodeForSell` (kiểm) → `Ins` (XML dòng hàng, `@p_TrnID=''`) → đọc `_Get` lấy TrnID + `TrnDateTime_Upd` → `Complete` → [`CARDPAY_Ins` nếu thẻ] → `T_TILL_TXN_Proc` → [`HoaDonDienTu_UpdateMa` tham số trống — **GĐ chốt giữ hay bỏ**] → [SMS] → `rptSRT_PrintBill` (in) — mỗi lời gọi kiểm `RETURN` và `RetailLog`.
2. THÂU: `Ins` → `Upd` (khách) → `Complete` → [`CARDPAY_Ins` âm] → `T_TILL_TXN_Proc`.
3. KHÁCH: `I_CUSTOMER_Ins` → `I_DiemTichLuy_InsFromGT`. BẢNG GIÁ: `I_XRATE_Ins` (XML).
4. SẢN PHẨM mới: `TRN_PRODUCT_IN_Ins` → `GenProductCode` → `Appr` (3 bước, có duyệt). Sửa: `T_PRODUCT_Upd`.
5. Hằng số cần lấy từ DB, không hard-code: `ShopID`, `TillID` (theo két KHBL riêng? → cần GĐ tạo TillID cho web), `UserID` (SYS_USERS), `EmpID`, `WalkInCustID`, `SectionID`, `SYS_PARAMETERS`.
6. Tham số truyền đúng kiểu như app: chuỗi ngày `dd/MM/yyyy`, giờ `HH:mm:ss`, tiền chuỗi số, XML `NewDataSet` đúng tên bảng con (`TRN_RT_BUYSELL_SELL`, `Card`, `Table1`).
7. Trước khi gọi `_Upd/_Del`, phải `_Get` lấy `TrnDateTime_Upd` mới nhất (khóa lạc quan).
