# Mobile online / khách gửi

- Bộ lọc mặc định: đầu–cuối tháng hiện tại. Tháng hiện tại đọc KK, các tháng trước đọc HIST; khoảng giao tháng gộp hai nguồn theo TrnID, ưu tiên bản KK còn tồn tại (kể cả hủy/xóa để không khôi phục phiếu cũ).
- Tab Khách gửi áp dụng d1–d2, mặc định tháng hiện tại đọc KK; tháng cũ đọc HIST, giao tháng gộp nguồn. Riêng tìm kiếm q vẫn bỏ giới hạn ngày theo quy tắc đã chốt. Trên 20.000 kết quả trả cảnh báo, không thống kê một tập bị cắt âm thầm.
- Khách gửi: online + customer_hold=true. Đã giao: online + customer_hold=false + customer_pickup.confirmed=true. Không suy luận giao hàng từ false đơn thuần.
- 📌 số ngày = ngày hiện tại - TrnDate, không lưu customer_hold_at.
- Xác nhận ưu tiên KK; chỉ khi KK không có mới đọc/ghi HIST. Lỗi kết nối KK không được coi là phiếu không tồn tại. Token ký chứa nguồn, người dùng, phiên bản Desc3; ghi trong transaction với khóa và đọc lại.
- HIST.TRN_RT_BUYSELL có cột kỹ thuật nullable bit `_mobile_pickup_confirmed`. Xác nhận HIST đặt 1 đồng thời với Desc3. `hist_bulk_merge` giữ Desc3 đã đánh dấu, tránh đồng bộ ghi đè xác nhận. Không dùng cột này tính ngày gửi. Không thay số tiền, EmpID, tồn kho.
- Backfill phá hủy/tạo lại bảng HIST không được chạy trên dữ liệu đã xác nhận nếu chưa sao lưu và khôi phục các ghi chú/marker này.
- Chính sách tháng là helper riêng cho mobile, không đổi quy tắc đọc theo ngày của các trang desktop.
- Đã bổ sung kho TRN_DATCOC và các bảng liên quan ngày 20/09/2026; trang Đặt hàng hiện vẫn đọc KK (chưa đổi luồng đọc trong đợt đồng bộ này). Money-in và QR tự tạo vẫn đọc MySQL.

Schema đã áp dụng trên PMV_KH2_HIST (2026-09-20):
```sql
IF COL_LENGTH('dbo.TRN_RT_BUYSELL','_mobile_pickup_confirmed') IS NULL
    ALTER TABLE dbo.TRN_RT_BUYSELL ADD _mobile_pickup_confirmed bit NULL;
```
