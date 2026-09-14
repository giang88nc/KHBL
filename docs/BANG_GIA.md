# Bảng giá — chuẩn ngày 12/09/2026

- `khj_bl.gold_prices` là nguồn giá mua/bán của web. Đọc mới khi tính giá; thiếu giá thì báo/chặn, không lấy giá KK dự phòng.
- `/banle/bang-gia/`: sửa trực tiếp mọi dòng hiện hành, LƯU riêng một dòng hoặc LƯU TẤT CẢ. Lưu MySQL độc lập với đích và khóa ghi PMV; không tự gọi `I_XRATE_Ins`.
- Hai nút SYNC giữ chiều hiện có: Report → MySQL và KK → MySQL, xem chênh lệch rồi duyệt.
- Giá/đơn vị thay đổi mới tạo phiên bản lịch sử. Chỉ ghim/đổi thứ tự không tạo phiên bản giá. Nhật ký thao tác dùng `gold_price_batches`, `target=mysql`, `status=saved`.
- Khóa chung + transaction + fingerprint + token chống lặp. AJAX lưu riêng không bỏ dữ liệu đang soạn ở dòng khác; gửi lại token đã dùng yêu cầu tải lại.
- Migration `pos.0023`: chuyển `gold_prices` MyISAM → InnoDB; không đổi giá, mã, lịch sử. Bản chụp trước chuyển nằm trong `logs/gold-prices-before-innodb-20260912.json`.
- Bảng niêm yết, PNG và mức giá đang có giữ nguyên khi triển khai. Giá trên chứng từ/ước tính đã lưu giữ theo chứng từ; phép tính mới dùng nguồn MySQL.

Kiểm thử: `manage.py test tests.test_prices --settings=config.settings.test_price_save` (SQLite riêng tên `test_`, tuân thủ chốt an toàn dọn dữ liệu); hồi quy editor cọc dùng `manage.py test tests.test_deposit_editor --settings=config.settings.test_prices`.
