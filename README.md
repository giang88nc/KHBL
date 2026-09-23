# KHBL — Kim Hạnh 2


## Liên hệ theo phiếu dùng chung — 21/09/2026

Bảng `khj_bl.document_contacts`: khóa duy nhất `(source_type, source_id)`; thêm `document_code`, `cust_id`, `customer_name`, `phone`, `created_at`, `updated_at`, `updated_by`.

| source_type | source_id | document_code |
|---|---|---|
| KHBL_BUYSELL | TRN_RT_BUYSELL.TrnID | BillCode |
| KHBL_BUYGOLD | TRN_RT_BUYGOLD.TrnID (từng dòng nhóm thâu) | BillCode |
| KHBL_DEPOSIT | TRN_DATCOC.TrnID: TDC260900000045 | 26-09-21-000007 |
| KHCD_LOAN | cd_loans.id | cd_loans.sku |

- SĐT trên phiếu được chọn/nhập riêng; không sửa I_CUSTOMER, không tạo khách trùng CCCD. Dùng cùng snapshot cho bản in web và SMS. Đổi hồ sơ khách không đổi SĐT phiếu đã lưu.
- Một phiếu cọc được áp dụng: mặc định kế thừa số trên phiếu cọc nếu chưa nhập riêng. Nhiều cọc không tự chọn số bất kỳ; số hóa đơn độc lập sau khi chốt.
- Phiếu cũ chưa có snapshot: KHCD dùng cd_loans.phone; KHBL dùng Phone nguồn hiện có. Không backfill từ hồ sơ hiện tại rồi coi là số lịch sử. Tin đã xếp hàng/gửi trước nâng cấp giữ nguyên.
- KHCD: ghi snapshot và cd_loans.phone cùng transaction MySQL. Xóa cầm mới hợp lệ xóa liên hệ cùng transaction; xóa phiên sau không xóa liên hệ phiếu.
- KHBL: `document_contact_writes` giữ yêu cầu bền trước lệnh MSSQL, token duy nhất, ID do PMV trả về và pending/done. Chỉ hoàn tất snapshot sau đọc lại đúng CustID; không tự gửi lại giao dịch khi mất phản hồi. Pending đã có ID chặn in/SMS tới khi đối soát.
- Không dùng snapshot KK cho MSSQL sandbox. Test KHCD phải đặt DOCUMENT_CONTACT_DB về database test.
- Phạm vi: quầy bán/thâu KHBL, đặt cọc web, KHCD. PMV desktop và hệ ngoài cần tích hợp riêng để đọc SĐT phiếu.

Migration KHBL: `venv/Scripts/python.exe manage.py migrate pos 0039`.
Đối soát chỉ đọc PMV và hoàn tất snapshot MySQL, không ghi tiền/trạng thái MSSQL:
```
venv/Scripts/python.exe manage.py reconcile_document_contacts
venv/Scripts/python.exe manage.py reconcile_document_contacts --apply TOKEN
```
Chưa nhận ID: đối soát thủ công, không tự ghép SĐT/BillCode. Không ghi đè snapshot bằng yêu cầu cũ hơn.

### Gợi ý khách theo từng SĐT — 21/09/2026

Các ô chọn khách trên quầy bán/thâu, đặt cọc và lập phiếu cầm đồ hiển thị mỗi SĐT một dòng, giữ nguyên CustID. Ưu tiên số khớp chính xác, rồi chứa chuỗi tìm, sau đó các số còn lại cùng khách. Chọn dòng ghi số đó vào SĐT trên phiếu; không đổi I_CUSTOMER. Số trùng trong cùng hồ sơ chỉ hiển thị một lần. QR CCCD có nhiều SĐT cần chọn dòng, không tự chọn số chính.

### Thêm SĐT vào hồ sơ trùng CCCD — 21/09/2026
Popup thêm khách báo trùng SĐT/CCCD kèm tên, CCCD và mã khách. Với CCCD có sẵn và SĐT mới, nút Thêm số điện thoại cho khách này nạp đúng CustID và toàn bộ hồ sơ, giữ Phone chính, điền SĐT 2 hoặc 3 còn trống. Người dùng kiểm tra rồi Lưu bằng UPDATE chuẩn. Đủ ba số hoặc số mới thuộc người khác: từ chối. Guard có chữ ký và kiểm tra trong khóa bảo vệ các số cũ; không gộp khách hay sửa liên hệ của phiếu cũ. Dùng cùng popup/service cho KHBL và KHCD.
