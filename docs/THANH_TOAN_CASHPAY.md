# CashPay khi THANH TOÁN bán hàng — 18/09/2026

Yêu cầu đã chốt: hóa đơn mới `CashPay=PayAmount`, `CardPay=0`; cập nhật `CashPay=PayAmount-CardPay`.

## Điểm thực thi

`views.ban_thanh_toan` lưu hóa đơn qua Ins/Upd như hiện có, rồi gọi `bill.chot`. Sau các kiểm tra cọc/hàng giữ và trước Complete, `chot` gọi `c.sync_retail_cash(trn_id)`. Nhánh tiếp tục hóa đơn đã C cũng đi qua bước này; không gọi lại Complete hoặc vào két nếu đã hoàn tất.

`gateway.pmv_retail_cash_default` đọc đúng dòng bằng TrnID dưới UPDLOCK/HOLDLOCK. Tính từ PayAmount/CardPay hiện tại, chỉ cập nhật CashPay; đọc kiểm mọi trường đã lấy trước commit. Dòng đã đúng thì không UPDATE. CardPay NULL được coi là 0 để tính, không đổi cột CardPay. Công thức giữ dấu khi PayAmount âm hoặc CardPay lớn hơn PayAmount; không tự ép CashPay về 0.

Phạm vi:

- Hóa đơn mới do vendor Ins đặt CardPay=0. Tới THANH TOÁN thì CashPay được đặt bằng PayAmount.
- Chỉ LƯU TẠM/đơn W chưa thanh toán: chưa chạy bước mới; proc Ins/Upd không bị sửa.
- Không lấy cách chia tiền từ session/form để ghi CardPay; giữ CK đã có trên MSSQL.
- Không ghi TienKhachTraThuc/TienTraLai, không đổi PayAmount/Status/TrnDateTime_Upd.
- Không gọi CARDPAY_Ins, không bổ sung thao tác két. Luồng Complete/T_TILL_TXN_Proc giữ nguyên.
- Không cập nhật hàng loạt các hóa đơn cũ. Chỉ hóa đơn được thanh toán/tiếp tục thanh toán qua `bill.chot`.

Lỗi ghi CashPay → dừng trước Complete. Lỗi sau Complete → không báo DONE; lượt tiếp theo xác minh đúng hóa đơn, không tạo hóa đơn mới. Kiểm hậu chốt đọc CashPay, CardPay, PayAmount và yêu cầu CashPay=PayAmount-CardPay trước khi trả thành công.

Đối soát tiền vào vẫn dùng `pmv_in_allocate`: đây là luồng riêng có thể sửa cả CashPay/CardPay. Khóa SQL của hai đường ngăn đọc/ghi đè chéo trong một transaction; snapshot đối soát cũ có thể bị từ chối và phải đối chiếu lại theo luồng hiện có.

## Kiểm chứng

`venv\Scripts\python.exe -X utf8 manage.py test tests.test_retail_cash_default --settings=config.settings.test_customer_sync`

12 ca: mới 100% tiền mặt; giữ CK khi cập nhật tổng; gọi lặp; tiếp tục đơn C; số 0/âm/đủ CK; chốt khóa và đích sai; dòng hủy/thiếu/trigger; đọc lại sai rollback; thứ tự trước Complete; lỗi ngăn chốt; kiểm cuối không khớp không báo thành công.

Sandbox: chạy gateway với `verify_only=True` trên một hóa đơn CardPay=0 và một hóa đơn có CardPay>0. UPDATE và đọc kiểm bên trong transaction, rollback; đối chiếu toàn bộ trường header đã đọc và dòng T_TILL_TXN trước/sau không đổi. Không đổi công tắc dữ liệu chung, không thanh toán hóa đơn KK để thử.
