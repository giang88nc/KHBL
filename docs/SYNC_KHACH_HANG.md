# Sync khách hàng một chiều — 14/09/2026

Quyết định: `khj_cd.customer.phone → I_CUSTOMER.Phone` là khóa đối chiếu duy nhất.
CCCD không dùng để đối chiếu; khi INSERT mới, chép `customer.cccd → CMND` nếu có.
Không cập nhật khách PMV đã có.

Nút **SYNC 1 CHIỀU** nằm trước **CHỤP HÌNH** ở trang Khách hàng. Popup có ba nhóm,
tìm không dấu, 50 dòng/trang. **THÊM TẤT CẢ** chạy toàn bộ nhóm CHƯA CÓ đủ điều kiện,
không giới hạn trang hoặc ô tìm. Mỗi yêu cầu xử lý một khách, tuần tự; có nút dừng
sau khách đang chạy, tiến độ, toast và refresh danh sách khách giữ bộ lọc.

## Đối chiếu và dữ liệu

- Bỏ khoảng trắng thông dụng, dấu chấm, ngoặc và gạch nối trong PHONE, giữ số 0 đầu.
  PHONE phải có 9–11 chữ số theo form hiện tại; không đoán số điện thoại thật/giả.
- CHƯA CÓ: PHONE chưa có trên PMV, có TÊN, dữ liệu không vượt giới hạn/không có ký tự
  điều khiển, PHONE duy nhất trong nguồn. Không bắt buộc địa chỉ.
- ĐÃ CÓ: PHONE khớp đúng một khách PMV.
- CẦN XỬ LÝ: thiếu/sai PHONE, thiếu TÊN, trùng PHONE nguồn/PMV hoặc lần sync chưa
  xác nhận hoàn tất. Không tự chọn một tên giữa các dòng có cùng PHONE.
- `name → CustName`, `addr → Address`, `BirthDate=1900-01-01 00:00:00`, `Gender=NULL`,
  `cccd → CMND` (trống thì để trống), `NoiCap=Cục Cảnh Sát QLHC về TTXH`,
  `Active=1`, khách thường, tự nâng hạng bật; các trường còn lại theo `_params` của form.
  Tên/địa chỉ đi qua bộ chuẩn hóa form khi ghi. NULL hiện là **Chưa xác định** trên web.
- CCCD/CMND nguồn có dữ liệu thì phải đủ 9 hoặc 12 chữ số theo form. Sai định dạng
  được báo tại CẦN XỬ LÝ; trùng CMND trên PMV được báo lúc thêm theo ràng buộc ghi,
  không đổi thành đối chiếu CCCD và không bỏ CCCD để cố INSERT.
- Form thêm/sửa thủ công vẫn yêu cầu người dùng chọn giới tính; cập nhật ảnh bảo toàn NULL.

## Ghi và phục hồi

1. Kiểm quyền Tạo/sửa KHÁCH HÀNG + CSRF; đọc lại nguồn từ ID phía server.
2. Kiểm fingerprint PHONE/TÊN/địa chỉ/CCCD và đích PMV so với lúc mở popup.
3. Khóa `SAVE_LOCK` dùng chung form và `GET_LOCK` trên MySQL cho nhiều tiến trình sync.
4. Đối chiếu PHONE chuẩn hóa lại ngay trước ghi; dùng `I_CUSTOMER_Ins` qua gateway,
   để vendor sinh mã; biên nhận lưu CustID ngay khi proc trả mã.
5. Tạo/kiểm `I_DIEMTICHLUY` bằng `I_DiemTichLuy_InsFromGT`, đọc lại PHONE/tên/địa chỉ/
   CMND/NoiCap/BirthDate/Gender, rồi mới đánh dấu DONE.

Biên nhận nằm tại MySQL KHBL `customer_sync_receipt`, khóa duy nhất `(target, source_id)`;
không ghi gì về `khj_cd.customer`. Migration `pos.0026_customersyncreceipt`.
Khách đã tạo nhưng lỗi sổ điểm có thể **Hoàn tất / kiểm tra lại**, chỉ hoàn tất sổ điểm,
không UPSERT lại hồ sơ. Nếu mất kết nối chưa có CustID, hệ thống chỉ nhận lại dòng PMV
có PHONE + payload khớp; nếu chưa chắc kết quả thì yêu cầu đối chiếu thủ công, không tự
INSERT lần hai. Nguồn thay đổi sau sync cũng cần đối chiếu thủ công.
Biên nhận cũ vẫn được nhận diện bằng fingerprint cũ, bảo toàn payload đã ghi và không
INSERT lại sau nâng cấp CCCD/NoiCap. Hai trường mới áp dụng cho lần INSERT mới.

## Kiểm chứng

`venv\Scripts\python.exe -X utf8 manage.py test tests.test_customer_sync tests.test_customer --settings=config.settings.test_customer_sync`

`venv\Scripts\python.exe -X utf8 manage.py smoke_customer_sync`

Thêm `--without-cccd` để kiểm trường hợp nguồn không có CCCD; cả hai trường hợp đều
phải ghi đúng nơi cấp mặc định.

Bộ smoke chỉ tạo khách giả trên sandbox, gửi đồng thời/gửi lặp, kiểm NULL giới tính,
ngày sinh, điểm và số dòng; tự dọn đúng khách thử và biên nhận. Không đổi công tắc PMV
dùng chung. 14/09/2026 kiểm thực tế: `I_CUSTOMER +1`, `I_DIEMTICHLUY +1`; hai bảng
`I_GIAODICH_KHACHHANG`, `SHOP_CUSTOMER` không tăng khi MaGD rỗng (cùng form hiện tại).
Backup KK trước mở tính năng: `PMV_BANLE_KH2_20260914_1322.bak`, 447 MB, VERIFY OK.
Không chạy THÊM TẤT CẢ trên dữ liệu thật trong bước kiểm thử giao diện.
