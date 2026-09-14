# Ba số điện thoại khách hàng — chốt 14/09/2026

- Giữ nguyên I_CUSTOMER cũ, không gộp hoặc xóa CustID, không liên kết hồ sơ.
- Phone / GhiChu2 / GhiChu3 là SĐT chính / SĐT 2 / SĐT 3, mỗi số nhập gồm đúng 10 chữ số ASCII. Ô trống được phép. Số 0 đầu được giữ.
- Chuẩn hóa dấu cách, dấu chấm, ngoặc, gạch nối khi đối chiếu dữ liệu cũ. Không tự đoán/chuyển đầu số.
- Khách mới hoặc hồ sơ được lưu phải có các số khác nhau, không trùng bất kỳ số nào trong ba cột của khách khác, kể cả khách ngừng hoạt động. Loại trừ chính CustID đang sửa.
- Không sửa dữ liệu cũ hàng loạt. Nếu người dùng mở sửa một hồ sơ cũ đang trùng/sai định dạng, việc lưu vẫn phải vượt kiểm tra và quy tắc vendor; không miễn kiểm tra vì đó là dữ liệu cũ.
- Form bỏ qua trường phụ trong request cũ thì giữ giá trị đang có; gửi chuỗi trống là yêu cầu xóa số phụ. Đọc lại cả ba cột sau UPSERT trước khi báo thành công.
- Form cảnh báo trùng khi nhập đủ 10 số. Kiểm tra phía máy chủ trong khóa mới là quyết định khi lưu; không phụ thuộc JavaScript.
- Mọi worker KHBL dùng khóa MySQL `khbl:customer-write:<target>` cho UPSERT và cập nhật ảnh khách. Kiểm tra trùng trước khi gọi proc; không lưu ảnh nếu hồ sơ bị từ chối.
- Sync một chiều đối chiếu cả ba cột, vẫn chỉ INSERT khách chưa có; không tự UPDATE theo CCCD. Dữ liệu nguồn để thêm mới phải có SĐT đúng 10 số và tên.
- Nhập đủ 10 số để tìm: so sánh chính xác trên cả ba cột, chỉ chọn một CustID. Nếu dữ liệu cũ trùng, ưu tiên Phone rồi GhiChu2 rồi GhiChu3, sau đó CustID tăng dần. Không gộp giao dịch hay số dư. Các bộ lọc phụ vẫn áp dụng nên có thể không có kết quả.
- CCCD/CMND đủ 12/9 số: so sánh chính xác và giữ tất cả hồ sơ khớp. Tên hoặc chuỗi chưa đầy đủ: tìm gần đúng, có thể nhiều dòng. Không dùng TOP 1 để kiểm tra trùng khi ghi.
- Tìm theo mã trên màn chọn khách hoạt động vẫn tuân theo Active; hồ sơ được chọn trong dữ liệu cũ có thể ngừng hoạt động và không dùng để giao dịch mới.
- Giới hạn: PMV desktop không dùng khóa KHBL và proc vendor chỉ chống trùng Phone/CMND. Quy tắc ba cột bảo vệ các luồng ghi qua KHBL; không phải ràng buộc toàn DB cho mọi phần mềm.

## Kiểm chứng

`manage.py test tests.test_customer_phones tests.test_customer_sync tests.test_customer --settings=config.settings.test_customer_sync`

`manage.py smoke_customer_phones`: INSERT/UPDATE/xóa số phụ; tìm theo từng số; 9 xung đột chéo cột; hai worker tranh cùng số phụ chỉ một được tạo; kiểm sổ điểm; dọn khách thử và đối chiếu số dòng.

Đọc KK: một CCCD cũ có hai hồ sơ vẫn trả hai dòng; từng SĐT đúng trả một dòng; tìm tên vẫn trả nhiều dòng. Không ghi dữ liệu khách KK trong kiểm thử.

Backup trước áp dụng: `D:\KHJ_PMV_BACKUP\PMV_BANLE_KH2_20260914_1557.bak`, VERIFY OK, 448 MB.
