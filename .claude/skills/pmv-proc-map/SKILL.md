---
name: pmv-proc-map
description: Bản đồ "API" của PMVGoldRT — stored proc + bảng trực tiếp/gián tiếp cho từng nghiệp vụ (hóa đơn bán/thâu/đổi, product, customer, bảng giá), vòng đời hóa đơn W→C, danh sách proc CẤM, cách khảo sát thêm. BẮT BUỘC đọc trước khi viết bất kỳ code nào đụng tới DB PMV_BANLE_KH2 (đọc lẫn ghi), thêm lệnh vào allowlist gateway, hoặc phân tích hành vi PMVGoldRT.
---

# PMV-PROC-MAP — tài liệu API duy nhất của PMVGoldRT

Vendor KHÔNG có tài liệu API. Toàn bộ tri thức dưới đây tự khảo sát 02/09/2026 bằng
3 script trong `tools/khaosat/` (chỉ-đọc, chạy lại được bất cứ lúc nào): đọc source
1.049 proc + soi cache thực thi (dm_exec_query_stats) + truy vết hóa đơn thật.

## 1. NGUYÊN TẮC ĐÃ XÁC MINH

- App desktop ghi **100% qua stored proc** (soi cache: 51/60 câu chạy nhiều nhất là proc,
  ad-hoc chỉ là SELECT tra cứu). → Webapp ghi = gọi đúng proc, KHÔNG ghi bảng trần.
- Chỉ có 4 trigger toàn DB (3 trên TRN_DAILY_LOG + 1 TRN_PRODUCT_IN_DT) → logic nằm ở
  proc + code app; ghi thẳng bảng là né sạch tồn kho/sổ quỹ/audit.
- Mã số sinh trong proc con: `SYS_BILL_COUNTER_Gen` (BillCode theo ngày, bảng
  SYS_BILL_COUNTER/SYS_BILL_COUNTER_NEW) + `SYS_CodeMasters_Gen` (TrnID/CustID... bảng
  SYS_CODEMASTERS) + fn `GenstrID`. Định dạng thực tế: TrnID bán = `TRB` + YYMM + seq
  (vd `TRB260900000176`), BillCode = `26-09-02-000047`.
- Soft delete: `IsDel='1'`; trạng thái `Status`: **W** = chờ/đang làm, **C** = hoàn tất.
- Bảng bóng `*_Log` (TRN_RT_BUYSELL_Log...) = audit của app, proc tự ghi.
- Cột `IsSync` trên 4 bảng giao dịch (BUYSELL/BUYGOLD/CHANGE/PRODUCT_IN) + bộ proc
  `DongBo*` = đồng bộ về hệ thị trường/trung tâm — proc vendor tự set, web không đụng.
- App có phân quyền riêng: SYS_USERS (FK → T_EMPLOYEE) / SYS_GROUPS / SYS_RIGHTS / SYS_MENUS.
- `tbh_VersionDB` (272 dòng) = lịch sử vendor nâng schema → `check_pmv` lấy vân tay,
  đổi là bật `pmv_write_lock`.

## 2. VÒNG ĐỜI HÓA ĐƠN (quan trọng nhất)

```
TẠO:    TRN_RT_BUYSELL_Ins  → phiếu Status='W' + dòng chi tiết + sinh mã
THANH TOÁN: CARDPAY_Ins     → ghi CardPay + sổ quỹ T_TILL_TXN(+DETAIL) (qua TRN_TILL_TXN_Upd)
CHỐT:   TRN_RT_BUYSELL_Complete → Status='C' + cập nhật I_CUSTOMER (điểm tích lũy)
SỬA:    TRN_RT_BUYSELL_Upd  (app gọi rất nhiều — 540 lần trong cache)
HỦY:    TRN_RT_CANCELED / TRN_RT_BUYSELL_Del / DEL_TRN_RT_BUYSELL (đảo daily log + I_GOLD_BAL)
```
THÂU (`TRN_RT_BUYGOLD_*`) và ĐỔI (`TRN_RT_CHANGE_*`) cùng khung; riêng `TRN_RT_CHANGE_Ins`
phức tạp nhất — tự DELETE+INSERT 5 bảng con (_SELL/_BUYGOLD/_DTL_NEW/_RATE).
⚠ Chuỗi Ins→CardPay→Complete do CODE APP nối trong 1 phiên — thứ tự + tham số chính xác
phải trace thêm khi code GĐ3 (SQL Profiler trên sandbox, hoặc đọc source proc bằng
`sys.sql_modules`).

## 2b. VÒNG ĐỜI ĐÃ CHẠY THẬT ĐẦU-CUỐI (kiểm chứng trên sandbox 03/09/2026)

Chuỗi dưới đây đã chạy trọn vẹn qua gateway, tạo đúng dấu vết 11 bảng như app desktop,
rồi hủy sạch trả hàng về kho. Đây là khuôn để code GĐ3 — bộ hồi quy `manage.py smoke_dich`
chạy lại y hệt mỗi lần sửa.

```
1. TRN_RT_BUYSELL_Ins      → TrnID + BillCode do vendor sinh, Status='W'
2. TRN_RT_BUYSELL_Complete @p_TrnID, @p_UserID, @p_ThuHo='0'  → trả TillTxnID
3. T_TILL_TXN_Proc         @p_TrnIDs, @p_TillID, @p_UserID    → Status='C', hàng sang 'S'
HỦY (ngược lại, ĐÚNG THỨ TỰ):
4. T_TILL_TXN_Del      @p_TrnRefID, @pType='SRT', @pCongNoBanLe=0, @p_Type='1'  → về 'W'
5. TRN_RT_BUYSELL_Del  @p_TrnID, @p_LogDel='0'                → xóa hẳn, hàng về 'I'
```

- `@p_Type` của `T_TILL_TXN_Del`: **`'1'` cho hóa đơn ĐÃ CHỐT (C)**, `'0'` cho hóa đơn còn
  chờ (W). Dùng nhầm `'0'` trên hóa đơn C = không hủy được (im lặng).
- Bước 4 và 5 **mỗi bước một mốc khóa lạc quan khác nhau** — xem mục 2c.
- Không cần `CARDPAY_Ins` cho hóa đơn tiền mặt.
- Hủy KHÔNG dọn `T_CUSTOMER_DEBT` (còn dòng số dư 0) và các bảng `*_Log` — cố ý.
- `I_CUSTOMER_Del` **từ chối** xóa khách đã phát sinh giao dịch (`Result=-1`).

## 2c. ⚠ KHÓA LẠC QUAN — HỎNG IM LẶNG (bài học đắt, 03/09/2026)

Proc `_Upd`/`_Del` so `@p_TrnDateTime_Upd` truyền vào với `TrnDateTime_Upd` trong bảng.
**Không khớp → proc trả `rc=0`, không báo lỗi, và KHÔNG LÀM GÌ CẢ.** Đã dính thật: tưởng
đã hủy hóa đơn, thực tế hóa đơn còn nguyên và món hàng vẫn kẹt ở trạng thái đã bán.

Vì thế **không bao giờ gọi `_Upd`/`_Del` trần**. Dùng:

```python
c.goi_co_khoa("TRN_RT_BUYSELL_Del", bang="TRN_RT_BUYSELL", cot_id="TrnID", gia_tri=hd,
              kiem_tra=lambda cl: not cl.query("SELECT TrnID FROM TRN_RT_BUYSELL "
                                               "WITH (NOLOCK) WHERE TrnID=? AND IsDel='0'", (hd,)),
              p_TrnID=hd, p_UserUpd=NGUOI, p_LogDel="0")
```

Hàm tự đọc mốc HIỆN TẠI, gọi proc, rồi **kiểm chứng dữ liệu đã đổi thật**; không đổi thì ném
lỗi. `kiem_tra` là bắt buộc về mặt tinh thần — bỏ nó là quay lại bẫy cũ.

Thêm 2 điều:
- Mốc **đổi sau mỗi bước** → đọc lại giữa bước 4 và 5, đừng dùng lại mốc cũ.
- Proc vendor **không có tham số mặc định**; thiếu 1 cái là `expects parameter '@X'`.
  Dùng `c.call(proc, day_du=True, ...)` để tự điền phần còn lại theo chữ ký proc.

## 3. DẤU VẾT 1 HÓA ĐƠN BÁN THẬT (TRB260900000194, đo 02/09/2026)

| Bảng | Dòng | Qua cột |
|---|---|---|
| TRN_RT_BUYSELL | 1 | TrnID |
| TRN_RT_BUYSELL_SELL | 2 | TrnID (dòng chi tiết món hàng) |
| T_PRODUCT | 2 | SellTrnID (đánh dấu món ĐÃ BÁN) |
| T_PRODUCT_TRACKING | 2 | TrnID |
| RetailLog | 2 | TrnID |
| T_TILL_TXN | 1 | TrnRefID (sổ quỹ) |
| TRN_DAILY_LOG | 8 | TrnRefID (nhật ký ngày → trigger đổ I_GOLD_BAL) |
| I_LICHSUTICHLUYDIEM | 1 | TrnRefID (điểm tích lũy) |

→ 1 hóa đơn C = ≥8 bảng + counter + I_GOLD_BAL + I_CUSTOMER. Diff sandbox phải phủ đủ
danh sách này.

## 4. BẢN ĐỒ PROC THEO NGHIỆP VỤ (GĐ chốt)

| Nghiệp vụ | Proc cửa chính | Ghi chú |
|---|---|---|
| Bảng giá | `I_XRATE_Ins` (tự DELETE+INSERT I_XRATE + ghi I_XRATE_HIST); giá sỉ: `I_XRATE_GiaSi_Upd`, `I_XRATE_GiaBanSi`; vàng chuẩn: `I_XRATE_VangChuan` | DỄ NHẤT — 1 proc. App poll I_XRATE_GetAll nên đổi là màn hình bán tự thấy |
| Customer | `I_CUSTOMER_Ins` / `I_CUSTOMER_Upd` (+I_GIAODICH_KHACHHANG, SHOP_CUSTOMER; ảnh qua `SaveImageToFile`); xóa `I_CUSTOMER_Del` | Có biến thể `I_CUSTOMER_Ins_Mobile` |
| Product SỬA | `T_PRODUCT_Upd` (+T_PRODUCT_UPD_LOG, ChangeTaskPrice_Log, UPDATE TRN_PRODUCT_IN) | KHÔNG có proc tạo T_PRODUCT trực tiếp — hàng mới đời qua nhập hàng `TRN_PRODUCT_IN` + `TRN_PRODUCT_IN_Appr` (duyệt) |
| HĐ bán | `TRN_RT_BUYSELL_Ins` → `CARDPAY_Ins` → `TRN_RT_BUYSELL_Complete`; sửa `_Upd`; hủy `TRN_RT_CANCELED`/`_Del`/`DEL_TRN_RT_BUYSELL` | Biến thể `TRN_RT_BUYSELL_Mobile_Ins`/`_Mobile_Complete` — dấu vết cổng mobile của vendor, nên hỏi vendor |
| HĐ thâu | `TRN_RT_BUYGOLD_Ins` (+`_NEW_Ins`) → `CARDPAY_Ins` → `TRN_RT_BUYGOLD_Complete`; hủy `DEL_TRN_RT_BUYGOLD` (xóa cứng + xóa T_TILL_TXN) | |
| HĐ đổi | `TRN_RT_CHANGE_Ins` → `CARDPAY_Ins` → `TRN_RT_CHANGE_Complete` | Phức tạp nhất, làm CUỐI |

## 5. PROC/VÙNG CẤM — KHÔNG BAO GIỜ VÀO ALLOWLIST

- `Del_AllData`, `Del_AllData_ExpIndexTable`, `Del_OldData_FromDate_ToDate`,
  `Del_OldPOData_FromDate_ToDate`, `Del_POData`, `ChuyenKyKinhDoanh`,
  `DropDefaultConstraints`, `BackUpData` (của vendor — mình có backup riêng).
- Hóa đơn điện tử: proc `HoaDonDienTu_UpdateMa`, bảng `SYS_INVOICE*`, `I_DichVuHDDT`,
  `I_TEMPLATE_HDDT`, cột `MAHDDienTu/InvoiceGUID/SoHDDienTu/KyHieuHDDienTu...`.
- `T_EMPLOYEE.DBUserName/DBPassword` (credential per-shop) — không SELECT * bảng này.

## 5a. PHÂN TÍCH TỪNG BƯỚC (02/09/2026, từ 4.394 lời gọi thật)

**Đọc `docs/PHAN_TICH_HOAT_DONG_PMVGOLDRT.md` trước khi code bất kỳ nghiệp vụ ghi nào** —
8 bước bán hàng (GetByCodeForSell → Ins XML → Upd lặp → Complete → CARDPAY → T_TILL_TXN_Proc
→ SMS → HoaDonDienTu_UpdateMa tham số trống → PrintBill), thâu (TBG), nhập hàng 3 bước
(Ins → GenProductCode → Appr — không có proc tạo T_PRODUCT trực tiếp), xuất, khách, giá,
3 sổ cái (tồn quầy I_GOLD_BAL · két T_TILL_BAL · khách/điểm/công nợ), kiểu tham số (chuỗi,
XML NewDataSet), khóa lạc quan TrnDateTime_Upd, đơn vị 1 chỉ = 100.

## 5b. LOG HÀNH VI THẬT (từ 02/09/2026 — nguồn tốt nhất cho GĐ3)

Trang `/hanh-vi/` + bảng `pmv_behavior_logs`: trace SQL đang ghi TỪNG lời gọi proc của
app kèm THAM SỐ THẬT (login sa, host KK/QQ). Trước khi code 1 nghiệp vụ ghi:
1. Lọc nhóm (vd "HĐ bán", hành động "ghi") trong ngày tiệm bán → thấy đúng CHUỖI proc
   app gọi + thứ tự + giá trị tham số (đây là spec API chính xác hơn mọi suy luận).
2. Ghép với snapshot sandbox trước/sau (`/so-sanh/`) khi replay chuỗi đó trên sandbox.
Lệnh: `manage.py pmv_trace start|stop|status`, `collect_pmv_behavior` (2 phút/lần).

## 6. KHẢO SÁT THÊM KHI CẦN

- `tools/khaosat/khao_sat_thong_so.py` — thông số server/DB/quyền/2 bảng chính.
- `tools/khaosat/khao_sat_cau_truc.py` — bảng/proc/trigger/FK/sync/user.
- `tools/khaosat/khao_sat_hanh_vi.py` — cache thực thi + dấu vết hóa đơn + bản đồ
  proc↔bảng (regex trên `sys.sql_modules`).
- Đọc source 1 proc: `pmv_read("SELECT m.definition FROM sys.sql_modules m JOIN sys.objects o ON o.object_id=m.object_id WHERE o.name = ?", (ten_proc,), tag=...)`.
- Xem app đang chạy gì: query `sys.dm_exec_query_stats` + `dm_exec_sql_text`
  (mẫu trong khao_sat_hanh_vi.py) — 2005 KHÔNG có dm_exec_procedure_stats.
- Mọi script khảo sát: chỉ-đọc, `WITH (NOLOCK)` trên bảng dữ liệu, không in mật khẩu.

## 7. QUY TẮC THÊM PROC VÀO ALLOWLIST GHI (GĐ3+)

1. Đọc source proc (sql_modules) — hiểu đủ tham số + bảng nó đụng.
2. Chạy trên PMV_SANDBOX, diff từng bảng với thao tác thật trên app PMVGoldRT.
3. GĐ duyệt nghiệp vụ đó → thêm vào allowlist `pmv_exec` kèm comment ngày duyệt.
4. Smoke test + backup PMV ngay trước go-live. `pmv_write_lock` đang bật thì KHÔNG ghi.

## LUỒNG THẬT ĐÃ ĐO TRÊN KK (05/09/2026)

Bán 1 món trên PMVGoldRT → 40 lời gọi, 18 bảng đổi, đối chiếu từng cột KK ↔ sandbox và so với
`apps/pos/bill.py`: xem **`docs/LUONG_BAN_HANG_PMV_20260905.md`**. Cách đo lại cho nghiệp vụ khác
(thâu, khách, bảng giá…): nút ĐÁNH DẤU TRƯỚC/SAU trên `/he-thong/so-sanh/` + file `logs\pmv\<ngày>\`.

Quy trình chuẩn FULL (v3, GĐ chốt 05/09/2026): `docs/quy_trinh/BAN_HANG.md` — 9 pha, có hủy thanh toán
(`T_TILL_TXN_Del @p_Type=0` → về W), xóa đơn (`TRN_RT_BUYSELL_Del` → *_Log), tạo khách trong đơn (`I_CUSTOMER_Ins`),
chống trùng P-008. ⚠ Đính chính: `@p_Type` không rẽ nhánh với SRT (xem CLAUDE.md 4c).
