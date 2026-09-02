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
