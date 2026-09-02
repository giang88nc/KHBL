# LỘ TRÌNH KHBL — webapp BÁN LẺ song song PMVGoldRT (GĐ chốt phạm vi 03/09/2026)

## Phạm vi
- LÀM: hóa đơn **bán – đổi** (1 giao dịch `TRN_RT_BUYSELL` + dòng vàng cũ `_BUYGOLD`; `TRN_RT_CHANGE` vendor 0 dòng — không dùng), **thâu** (`TRN_RT_BUYGOLD`), CRUD **khách**, **update product**, **update bảng giá** (I_XRATE + tự ghi I_XRATE_HIST), **in GIẤY ĐẢM BẢO**.
- KHÔNG LÀM: thẻ/chuyển khoản (`CARDPAY_Ins`), SMS/Zalo (`tbc_TinNhan_*`), HĐ điện tử phát hành, nhập/xuất hàng.
- Yêu cầu: LAN, nhẹ, nhanh, realtime, nhiều PC; **bill web = bill app** (KK đọc/sửa/xóa được và ngược lại).
- PMVGoldRT vẫn là app chủ lực (nhập/xuất/báo cáo).

## Tài khoản — kết luận khảo sát 03/09
- App đăng nhập qua `SYS_Users_CheckLogin` (7 user trong `SYS_USERS`, mật khẩu PLAIN TEXT), **1 user = 1 két** (`T_TILL.OpenUserID`), user gắn `EmpID`+`ShopID`.
- Giới hạn tài khoản = **license client**: `tbh_NguoiDungDangNhap` (MaNguoiDung ↔ MachineCode/MAC/IP/TenMay + cờ DangNhap), app poll `_Load` liên tục để chặn phiên trùng. **SQL không giới hạn** → webapp ghi DB KHÔNG tốn license, KHÔNG xung đột (web không gọi CheckLogin, không ghi tbh_NguoiDungDangNhap).
- Không FK nào từ bảng giao dịch tới `SYS_USERS` → cột `CreatedBy/@p_UserID` chỉ cần là UserID có thật để app hiện tên.
- **ĐỀ XUẤT**: tài khoản web riêng trong **MySQL** (Django auth như KHJ: user + vai trò + ma trận quyền, mỗi user gắn `EmpID` — map sẵn từ `khj_hr.employees.employee_pmv`). Trên app KK, GĐ tạo **1 user `webapp` + 1 két `WEB`** (qua màn hình quản trị app, không ghi SQL tay) → web stamp `CreatedBy/@p_UserID = webapp`, `@p_TillID = WEB`, `@p_EmpID = NV bán thật`. Lợi: báo cáo két của app tách riêng dòng tiền web; sổ két WEB chốt riêng; không lộ mật khẩu app.

## Track A — nền tảng (tuần 1, GĐ + code)
A1. GĐ tạo user `webapp` + két `WEB` trên app → ghi `PMV_WEB_USERID`, `PMV_WEB_TILLID` vào .env.
A2. Auth web trong `khj_bl` (kế thừa accounts/permissions của KHJ), user ↔ EmpID.
A3. Gateway kênh GHI `pmv_exec(proc, params)`: allowlist proc mở dần theo track B; `pmv_write_lock`; audit tham số (mask); lớp **PmvClient** chuẩn hóa: chuỗi ngày `dd/MM/yyyy`, giờ `HH:mm:ss`, tiền chuỗi, XML `NewDataSet` builder, đọc `sys.parameters` để build lệnh, kiểm `RETURN`/`RetailLog`/`ErrorLog`, khóa lạc quan `TrnDateTime_Upd` (luôn `_Get` trước `_Upd/_Del`).
A4. Bộ kiểm chứng: sandbox + snapshot diff + **đối chiếu với app** (cùng nghiệp vụ làm trên app KK, so dấu vết 13 bảng).

## Track B — nghiệp vụ GHI (tuần tự theo rủi ro; mỗi bước: sandbox → diff = app → GĐ duyệt → bật thật)
B1. **Bảng giá**: `I_XRATE_Ins @p_RateDate,@p_RateTime,@p_InputXML,@p_ShopID` (proc tự DELETE+INSERT I_XRATE + INSERT I_XRATE_HIST). UI: bảng giá hiện tại, sửa, lịch sử.
B2. **Khách hàng**: `I_CUSTOMER_Ins/_Upd/_Del` + `I_DiemTichLuy_InsFromGT`. UI tra theo SĐT/tên, popup CRUD.
B3. **Sản phẩm (sửa)**: `T_PRODUCT_Upd` (+`T_PRODUCT_UPD_LOG`). UI tra hàng + tồn quầy (`I_GOLD_BAL`, `T_PRODUCT.Status`).
B4. **Thâu**: `TRN_RT_BUYGOLD_Ins` → `_Upd` (khách/NV) → `_Complete` → `T_TILL_TXN_Proc`; hủy: `T_TILL_TXN_Del @pType='BRT'` → `DEL_TRN_RT_BUYGOLD`. Tiền mặt (Amount dương/âm theo chiều).
B5. **Bán – đổi**: `T_PRODUCT_GetByCodeForSell` (kiểm) → `TRN_RT_BUYSELL_Ins` (XML SELL + vàng cũ → `_BUYGOLD`, `@p_TrnID=''`, BillCode có ngay vì `TaoSoHDKhiThanhToan=0`) → `_Get` → `_Upd` khi sửa → `_Complete @p_ThuHo='0'` → `T_TILL_TXN_Proc`; hủy: `T_TILL_TXN_Del @pType='SRT',@p_Type='1'` → `TRN_RT_BUYSELL_Del`. Bỏ CARDPAY/SMS. **`HoaDonDienTu_UpdateMa` tham số trống**: app luôn gọi (chỉ ghi `TotalAmountHDDT` + `SYS_INVOICE_HIST`, KHÔNG phát hành) — khuyến nghị gọi y app để bill web = bill app 100%; GĐ chốt.
B6. **In GIẤY ĐẢM BẢO**: template HTML in từ `TRN_RT_BUYSELL_Get` (3 result set: header · dòng hàng · vàng cũ) + `dbo.GoldWeightToChar` (chữ "x chỉ y phân"); GĐ đưa mẫu giấy hiện hành để dựng đúng. Không gọi `rptSRT_PrintBill` (21 result set, ghi RetailLog).

## Track C — màn hình bán hàng (song song B, đọc trước ghi sau)
C1. POS: quét/nhập mã → tồn quầy → tính nhanh (giá × TL + công − vàng cũ) → giỏ → khách → tổng.
C2. Danh sách hóa đơn hôm nay realtime (long-poll như KHJ MCC), lọc W/C, in lại, hủy.
C3. Tra tồn/bảng giá live; nhiều PC = nhiều trình duyệt vào 1 server, mã do proc sinh nên không đụng nhau.

## Track D — go-live song song
D1. Bật từng chức năng có công tắc; backup PMV ngay trước; `check_pmv` canh vendor.
D2. Đối soát cuối ngày: diff sandbox↔thật + báo cáo két `WEB` trên app KK; app mở/sửa/xóa 1 bill web thử.
D3. Sau 1 tuần song song ổn → dùng chính cho bán lẻ.

Ước lượng: A 1 tuần · B1–B3 1 tuần · B4–B5 2 tuần · B6+C 2 tuần (song song) · D 1 tuần ≈ 6–7 tuần.
