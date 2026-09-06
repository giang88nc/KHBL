# Cập nhật bảng giá hàng loạt — 06/09/2026

Trang quản lý: `/banle/bang-gia/`. Trang niêm yết: `/banle/bang-gia/xem/`.

- Ô giá sửa trực tiếp, ghim và số thứ tự được lưu cùng nút Cập nhật. Hai nút XEM BẢNG / CẬP NHẬT đặt ngay tiêu đề bảng; không có ô tìm kiếm.
- MySQL `gold_prices` là nguồn chính. Mỗi lần cập nhật chuyển các dòng hiện hành sang `is_current=0`, INSERT cả bộ giá mới, giữ giá cũ nguyên vẹn. Không thay schema của bảng có sẵn.
- `gold_price_display`: ghim, vị trí, đơn vị nhập của từng loại. `gold_price_batches`: lô bền vững, người lưu, đích MSSQL đã chốt, giá/đơn vị/version đã lưu, trạng thái và lỗi.
- Đơn vị 610/980/9999/SJC là đồng/chỉ. BK/VT phải chọn rõ đồng/gram hoặc đồng/chỉ trước lần lưu đầu. Nếu nhập theo chỉ, đổi sang gram bằng 3,75 rồi chia 1.000; làm tròn HALF_UP đến 0,001 nghìn đồng (1 đồng). Đơn vị của từng phiên bản được lưu trong payload lô để tra lịch sử.
- Ánh xạ: 610 → 18K/D18K; 980 → 24K/D24K; 9999 → N9999/D9999; BK → BK/DBk; VT → VT/DT; SJC → SJC/DSJC. Cả BuyRate/SellRate trên mã vàng và mã dẻ nhận giá tương ứng.
- MySQL commit trước, MSSQL theo sau, không có giao dịch phân tán. Lỗi MSSQL không rollback MySQL. Pending/sync_failed hiện rõ trên UI và có nút thử lại; lô cũ bị thay thế không được phát lại. POST lặp cùng token không INSERT thêm.
- Khóa MySQL GET_LOCK liên tiến trình bao quanh lưu và đồng bộ; khóa dòng + fingerprint phát hiện form cũ. Đích MSSQL đổi kể từ lúc mở trang thì từ chối lô. Gateway vẫn giữ chốt PMV_GHI_KK và pmv_write_lock.
- Đồng bộ dùng I_XRATE_Ins, đủ TB_GIAVANG/TB_GIADE/TB_NGOAITE. Đọc lại toàn bộ giá để xác minh. Giữ nguyên giá ngoài mapping. Proc vendor thay cả bảng nên không gửi riêng những dòng đang ghim. Chặn nguồn nhiều ShopID và dữ liệu phụ mà proc không thể giữ.
- Trước mỗi lần ghi thay đổi giá vào KK: COPY_ONLY backup + VERIFY qua gateway. Không restore sandbox hoặc dọn backup trong request cập nhật giá.
- Bảng niêm yết đọc giá đã lưu, chỉ hiển thị dòng được ghim theo số thứ tự, mở bằng popup và nạp giá mới mỗi lần mở. Giá MySQL dùng chung dù MSSQL đang chọn bản thử.

## Kiểm chứng

`manage.py test tests.test_prices --settings=config.settings.test_prices`: SQLite bộ nhớ, tách khỏi dữ liệu thật. Kiểm tra định dạng/giới hạn tiền, quy đổi đơn vị, lịch sử, ghim/thứ tự, rollback MySQL, lỗi MSSQL và retry, form cũ, lô cũ, chống POST lặp, đích đổi, chốt KK, bảng niêm yết và HTTP POST.

`manage.py smoke_price_sync`: chỉ ghi MSSQL sandbox qua gateway. Đối chiếu 251 bảng: I_XRATE vẫn 15 dòng, I_XRATE_HIST tăng 15 dòng; 249 bảng khác không đổi. 12 mã vàng/dẻ nhận đúng giá. Khôi phục giá gốc qua proc vendor; giữ lịch sử thử. Không cập nhật gold_prices thật và không ghi KK. Mẫu trước ghi ở `logs/price_sync_sandbox_original.json`.

`manage.py smoke_pmv_money`: 15 PASS, đối chiếu công thức tiền với dữ liệu hiện có.

Giới hạn của vendor: I_XRATE_Ins không có version/khóa lạc quan giữa web và ứng dụng desktop. Khóa ứng dụng tuần tự hóa các request web, nhưng không khóa được thao tác đổi giá đồng thời trong app desktop. Web đọc lại giá sau ghi và báo lỗi nếu không khớp.


## Popup và PNG theo mẫu — 06/09/2026

Mẫu: `http://192.168.1.6:1276/bang-gia-vàng-kim-hanh-2`, phần `section.gold-board`, và ảnh người dùng `2026-09-06-bang-gia-vang-05-33-05.png`.

CSS gốc và logo giữ bản local trong `static/vendor/gold-board/`. Font Segoe UI (regular/bold/black/italic) và Georgia (regular/bold) từ Windows máy gốc được nạp riêng trong iframe, tránh font thay thế khác ảnh mẫu. Iframe có viewport 1280 px, bảng rộng 1160 px; popup chỉ thu tỉ lệ hiển thị. Cả document thật và bản clone đều chờ font/ảnh tải xong trước khi chụp, tránh mất cột logo khi font tải chậm.

Xuất dùng html2canvas 1.4.1 local, chụp scale=2, nền xuất #980909 (nền xem gradient theo CSS gốc), thu theo giới hạn 950.000 pixel. Bảng 3 dòng tạo PNG 1456×653 như ảnh mẫu, không chứa khung popup. Dòng tăng/giảm được tính từ phiên bản giá trước, không lấy dấu cố định trong ảnh mẫu.

POST `luu-png/` xác thực PNG (2 MB / 4 triệu pixel), lưu nguyên bytes vào `exports/bang-gia/<uuid>/<ngày>-bang-gia-vang-<giờ>.png`. GET `anh/<uuid>/` yêu cầu đăng nhập, trả file attachment. Nút lưu tự tải và có link Tải lại ảnh PNG để dùng trong trình duyệt không hỗ trợ blob download. Không thay đổi giá MySQL/MSSQL khi xem hoặc xuất ảnh.
