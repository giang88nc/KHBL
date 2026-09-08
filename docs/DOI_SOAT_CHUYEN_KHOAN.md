# Đối soát chuyển khoản RA với phiếu thâu

GĐ duyệt 08/09/2026. Không trigger, không sửa webhook, không job nền.

- Trang `/banle/chuyen-khoan/` gọi POST `/banle/chuyen-khoan/doi-soat/` khi tab hiển thị và d2 đã áp dụng là hôm nay (Asia/Ho_Chi_Minh).
- Gọi ngay khi vào/F5, chuyển về hôm nay hoặc quay lại tab; hẹn lượt kế tiếp 5 giây sau khi lượt trước kết thúc. Rời trang, ẩn tab, đổi ngày cũ thì dừng gửi lượt mới. Yêu cầu đã gửi có thể hoàn tất.
- Backend cũng kiểm tra d2; yêu cầu đăng nhập và CSRF. Khóa MySQL GET_LOCK không chờ + mốc 5 giây dùng chung mọi process/tab.
- Nguồn: transaction_time trong ngày hôm nay, direction=out, is_check=0, trans_amount>0. Bộ lọc tài khoản/tiền/nội dung trên UI không giới hạn phạm vi đối soát.
- T=transaction_time. PMV máy KK chỉ SELECT qua gateway: TRN_RT_BUYGOLD, Status=C, IsDel=0, CardPay<0, trans_amount=-CardPay, CreatedDate trong [T-30 phút,T], gồm hai biên. Có thể tìm sang ngày hôm trước khi T gần 00:00.
- Mỗi lượt lấy tối đa 30 giao dịch đến hạn, gom một truy vấn PMV với số tiền và khoảng thời gian liên quan. Timeout kết nối và timeout câu truy vấn đều 3 giây.
- Chỉ gắn khi đúng một phiếu và không có khoản RA khác cùng tranh phiếu đó. TrnID đã có trong bill_code_raw của bản ghi khác hoặc nhật ký unique thì không dùng lại. Không tự chọn phiếu gần nhất, không ghi đè mã có sẵn khác TrnID.
- Trước ghi, khóa và đọc lại dòng ngân hàng, kiểm tra fingerprint và updated_at. Transaction ngắn cập nhật bill_code_raw=TrnID, is_check=1 và giữ TrnID unique trong bank_reconcile_state. Không giữ transaction MySQL khi chờ PMV.
- Không khớp/lỗi/trùng: giữ nguyên nguồn, lưu lý do, thử lại sau 15/30/60/120/240/300 giây khi trang còn hoạt động. Dữ liệu nguồn đổi thì được thử lại ngay. Không mở rộng cửa sổ T-30 phút theo thời gian retry.
- Nếu webhook đặt lại is_check=0 trên dòng đã khớp, giữ reservation và báo cần kiểm tra; không tự gắn lần thứ hai.
- Migration 0007 tạo bank_reconcile_state; 0008 thêm index (direction,is_check,transaction_time) vào bảng nguồn MySQL. Không thay đổi schema hoặc ghi dữ liệu PMV.
- Kiểm tra: `manage.py test tests.test_bank_reconcile tests.test_transfers --settings=config.settings.test_prices` và `node --test tests/bank_reconcile_ui.test.cjs`.

Kiểm chứng triển khai: 20 bài server + 6 bài JS đạt; hai kết nối MySQL xác nhận khóa loại trừ. Qua trang thật khớp 12/26 khoản RA, 14 khoản chưa khớp giữ nguyên. Bản chụp trước cập nhật được lưu trong runtime/bank-reconcile-before-2026-09-08.json (không đưa vào Git).
