# QUY TRÌNH tạo khách hàng mới từ danh mục (`KHACH_HANG_MOI`) — v2

> Trạng thái: **GĐ đã duyệt** · cập nhật 05/09/2026 07:24 · 1 pha · 6 bước (1 ghi) · KHBL làm 5 · cần kiểm 1 · bỏ 0

Nguồn học: HỌC từ khung 05/09/2026 07:20→23:59 (admin)

```mermaid
flowchart LR
  P0["0. Đã học 05/09 07:23 — chưa phân nhóm"]
```

## 0. Đã học 05/09 07:23 — chưa phân nhóm

Bảng đổi trong khung: I_CUSTOMER, I_DIEMTICHLUY, SYS_CODEMASTERS

| # | Bước | Proc | Ghi/đọc | Tham số quan trọng | Bảng đổi | KHBL | Đối chiếu | Ghi chú |
|---|---|---|---|---|---|---|---|---|
| 0.1 | Khách hàng: I_CUSTOMER_Lst | `I_CUSTOMER_Lst` | đọc | exec [I_CUSTOMER_Lst] @p_CustID='',@p_CustCode=N'',@p_CustName=N'',@p_Address=N'',@p_Phone='',@p_Notes=N'',@p_Active='',@p_Sort='CustName ASC',@p_CustGroupID='',@p_CustTypeID='',@p_BirthDate='',@p_Gender='',@p_Email='',@p_DateOfJoining='',@p_LastTradingDate='',@p_LoaiGD='',@ShopID='' | — | KHBL thực hiện | Chưa đối chiếu | — |
| 0.2 | Hệ thống: SYS_LOADCOMBO ×2 | `SYS_LOADCOMBO` ×N | đọc | exec [SYS_LOADCOMBO] @pType=N'I_CUSTOMER_GROUP',@pParam=N'',@ShopID='' | — | KHBL thực hiện | Chưa đối chiếu | — |
| 0.3 | Khác: I_GIAODICH_Lst | `I_GIAODICH_Lst` | đọc | I_GIAODICH_Lst | — | KHBL thực hiện | Chưa đối chiếu | — |
| 0.4 | Khách hàng: I_CUSTOMER_Ins ×2 | `I_CUSTOMER_Ins` ×N | ghi | declare @p18 xml set @p18=convert(xml,N'') exec [I_CUSTOMER_Ins] @p_CustID='',@p_CustCode=N'',@p_CustName=N'Trương Ngọc Giang',@p_Address=N'Hcm',@p_Phone='0944351461',@p_Notes=N'',@p_Active='1',@p_CMND='',@p_CustGroupID='',@p_CustTypeID='',@p_BirthDate='05/09/2026',@p_Gender='0',@p_Email='',@p_DateOfJoining='',@p_LastTradingDate='',@TuDongNangHang='1',@p_Image=NULL,@MaGD=@p18,@p_Company=N'',@p_Com | — | Cần kiểm trên sandbox | Chưa đối chiếu | — |
| 0.5 | Khách hàng: I_DiemTichLuy_InsFromGT | `I_DiemTichLuy_InsFromGT` | đọc | exec [I_DiemTichLuy_InsFromGT] @p_CustID='CU2609000000193' | — | KHBL thực hiện | Chưa đối chiếu | — |
| 0.6 | Khách hàng: I_CUSTOMER_Lst | `I_CUSTOMER_Lst` | đọc | exec [I_CUSTOMER_Lst] @p_CustID='',@p_CustCode=N'',@p_CustName=N'',@p_Address=N'',@p_Phone='',@p_Notes=N'',@p_Active='',@p_Sort='CustName ASC',@p_CustGroupID='',@p_CustTypeID='',@p_BirthDate='',@p_Gender='',@p_Email='',@p_DateOfJoining='',@p_LastTradingDate='',@p_LoaiGD='',@ShopID='' | — | KHBL thực hiện | Chưa đối chiếu | — |

