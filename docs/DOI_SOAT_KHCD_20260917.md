# Thanh toán theo phiên cầm đồ — 17/09/2026

## Phạm vi

- Phiên KHCD mới: một `cd_payments`, `amount` tổng tiền, `cashPay=amount`, `cardPay=0`, `channel=NULL`.
- `bank_snapshot`/QR là chỉ dẫn, không phải bằng chứng đã chuyển. OUT chuẩn bị QR cũng không tự ghi nhận đã chi CK.
- Giữ nguyên các dòng lịch sử theo channel; bộ đọc hỗ trợ cả hai dạng. Không gom/xóa lịch sử tự động.
- `payment_ref = yymm + loan_id(5) + log_id(5)`, theo ngày thực của log. Ví dụ `26090097703435`. Không đổi `cd_loans.sku`.
- Vượt 99999 ở một ID: chặn cấp mã, cần nâng phiên bản mã, không cắt số gây trùng.

## Tự động IN cầm đồ

`pawn_bank_reconcile.py`: phiên native, một dòng thanh toán, có chỉ dẫn QR theo mã phiên.
So khớp mã có ranh giới, tài khoản nhận, hướng IN, thời gian từ lúc lập phiên đến 24 giờ sau.
Popup POST có CSRF/quyền CHUYEN_KHOAN và phiên bản QR, mỗi 4 giây; scheduler tiếp tục mỗi 30 giây.
`money_flow_bank_receipt` giữ chứng từ gốc, unique notification ID và bank account/direction/ref_code.
Cộng nhiều giao dịch khác nhau; thông báo nạp trùng cùng ref_code chỉ tính một lần. Khác số tiền hoặc nhiều mã phiên: báo cần kiểm tra.
`cashPay=amount-SUM(receipts)`, `cardPay=SUM(receipts)`; không cho âm tiền mặt, không tự phân bổ tiền thừa.
Claim thông báo, lưu chứng từ, cập nhật KHCD và projection trong một MySQL transaction/InnoDB; đọc lại trước commit.
Phiên đã có bằng chứng không cho hủy/xóa bằng chức năng hoàn tác 5 phút.

QR lần sau chỉ cho phần còn thiếu. Popup thành công khi chứng từ đạt số tiền QR (kể cả thanh toán hỗn hợp);
phiếu xanh/khóa QR chỉ khi tổng CK bằng toàn bộ tổng tiền phiên.
Sau 30 phút chỉ dừng polling trên màn hình, KHÔNG tuyên bố QR ngân hàng hết hiệu lực.

## Không nằm trong bộ mới này

- Không ghi PMV, phân bổ sổ quỹ, trạng thái C/W.
- Đối soát OUT và QR IN bán hàng/đặt cọc chưa nối vào bộ đối soát phiên KHCD này. Các bộ đối soát cũ không bị thay thế.
- Phiên lịch sử nhiều dòng hoặc đã có tiền BANK ngoài chứng từ bộ mới: dừng và yêu cầu đối chiếu, không ghi đè.
- Mã QR đã lưu dưới dạng ảnh vẫn có thể quét lại. Chỉ ngân hàng/đơn vị cung cấp QR động mới kiểm soát được hiệu lực chuyển tiền.

## Triển khai

1. KHCD `scripts/migrate_payment_split.py`: backup đầy đủ rồi nâng schema; xác minh các giá trị lịch sử không đổi.
2. KHBL `manage.py migrate pos`: bảng bằng chứng + mốc tiền đã nhận khi tạo QR.
3. Restart hai dịch vụ để mọi nơi đọc cấu trúc mới.
4. `manage.py reconcile_pawn_in` chỉ liệt kê phạm vi; `--apply` mới ghi. Không tạo chứng từ thử trên DB thật.

Chốt bắt buộc: `bank_notifications` thực tế đang MyISAM tại thời điểm kiểm tra. Bộ mới từ chối ghi
khi bất kỳ bảng tham gia nào chưa InnoDB. Chuyển engine bảng ngân hàng dùng chung cần backup và
phê duyệt riêng; chưa được coi đối soát là đang hoạt động chỉ vì code đã triển khai.

Kiểm thử KHBL dùng `manage.py test ... --settings=config.settings.test_prices` (SQLite riêng).
KHCD pytest dùng database `khj_cd_test_<random>` riêng; không chạy fixture xóa trên CSDL vận hành.
