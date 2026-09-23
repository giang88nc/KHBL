# Đồng bộ đặt cọc / chẩn đoán Khách gửi

## Phạm vi đồng bộ

TRN_DATCOC, TRN_DATCOC_DT, TRN_DATCOC_CardPay, TRN_DATCOC_Log,
TRN_DATCOC_DT_Log, TRN_DATCOC_CardPay_Log, TRN_RT_BUYSELL_DATCOC,
TRN_RT_CHANGE, TRN_RT_CHANGE_DATCOC.

- Danh mục chung hist_config đã bao gồm các bảng này: scheduler hiện hữu 09:00/21:00 sẽ tiếp tục đồng bộ.
- TRN_DATCOC snapshot UPSERT theo TrnID để nhận cả thay đổi Description/CashPay/CardPay không tăng TrnDateTime_Upd.
- Bảng con/liên kết thay thế trong một transaction HIST, chỉ với TrnID cha đang có ở KK hoặc TrnID hiện có trong bảng con KK. Giữ lại dữ liệu chỉ còn HIST; không DROP bảng. Đọc cha/con KK cùng transaction HOLDLOCK để không xóa dữ liệu đích khi nguồn đọc lỗi. Quy mô hiện tại nhỏ; nếu tăng mạnh nên chuyển sang snapshot isolation đã được quản trị phê duyệt.
- Nhật ký append theo ID. TRN_RT_CHANGE UPSERT theo watermark hiện hữu.
- Chỉ đọc KK; không thay thanh toán, sổ quỹ hay phiếu nguồn.
- Kiểm chứng lượt đầu: 9 bảng count/checksum khớp; 433 phiếu, 544 chi tiết, 21 nhật ký chi tiết, 20 nhật ký phiếu; các bảng còn lại rỗng.

## Chẩn đoán chậm (không sửa truy vấn hoặc cấu hình RAM)

- Khách gửi: HIST 8.005s, KK 0.033s, toàn trang 8.17s trong lần đo.
- HIST có 20.799 hóa đơn; điều kiện Desc3 LIKE với wildcard đầu phải quét dữ liệu toàn thời gian. Chỉ có clustered PK, không có chỉ mục riêng cho phân loại.
- COUNT(*) HIST 4.9–7s; KK khoảng 0.009s. LIKE HIST 5.8–7.3s; KK khoảng 0.021s.
- Theo dõi phiên truy vấn: blocking_session_id=0, CPU chỉ ~42ms trong ~7.7s; chờ SLEEP_TASK/PAGEIOLATCH_SH, nhiều physical reads.
- SQL2014 báo process_physical_memory_low=1, bộ nhớ tiến trình ~96MB; máy 16GB còn ~651MB khả dụng tại thời điểm đo. Đây là bằng chứng áp lực RAM; không đủ cơ sở khẳng định ổ đĩa hỏng/chậm.
- Đề xuất: xử lý áp lực RAM trước; tiếp theo lập chỉ mục/dữ liệu phân loại ở HIST để tránh quét Desc3 mỗi lần mở, cập nhật đồng thời khi sync/xác nhận giao hàng. Không dùng NOLOCK hoặc bỏ lịch sử để che độ trễ vì có thể trả sai trạng thái.
