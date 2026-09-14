# Tích hợp một trang gọi lưu khách

## Chọn cách tái sử dụng

**Trang nằm trong KHBL:** ưu tiên mở nguyên popup `pos:khach_them` / `pos:khach_sua` vào `#modal-root`, POST qua `pos:khach_luu`. Đã có ba SĐT, QR, camera, cắt ảnh, token, khóa, xử lý lỗi ảnh. Sau event `khachSaved`, trang gọi đọc `event.detail.custId`, nạp hồ sơ theo ID và chọn khách cho nghiệp vụ đang làm. Không tự động chốt hợp đồng/hóa đơn vì lưu khách đã xong.

**Cần adapter Python riêng trong KHBL:** gọi lại `customer.clean_form`, `prepare_images`, `upsert`. Adapter chịu trách nhiệm quyền, CSRF, giữ payload khi sửa, ShopID/target, token/biên nhận, xử lý partial và phản hồi. Lấy `views.khach_luu` làm mẫu, không copy thành phiên bản thứ hai rồi sửa quy tắc độc lập.

```python
from apps.pos import customer as C

# Bên trong endpoint đã xác thực, kiểm quyền và xác định đúng ngữ cảnh khách.
data, errors, warnings = C.clean_form(form_values)
if errors:
    # Trả lỗi form, không gọi PMV.
    ...
else:
    images, image_info = C.prepare_images(uploaded_files)
    with C.SAVE_LOCK:
        # Adapter đặt kiểm token/biên nhận ở đây như views.khach_luu.
        result = C.upsert(data, images, shop_id=shop_id, client=client)
    # Chỉ cho bước nghiệp vụ tiếp theo chạy khi result['complete'] là True.
```

Đây là khung nối service, không phải endpoint hoàn chỉnh để chép chạy. Không giữ SAVE_LOCK rồi gọi lại view đang tự lấy khóa; không lấy thêm phone_write_lock bên ngoài decorator. Khi chỉ đổi ảnh dùng `C.cap_nhat_anh(cust_id, images, client=client)`.

**Cầm đồ là webapp/repo khác:** đọc skill này từ repo chuẩn và xác định ranh giới triển khai trước. Không tự import bằng cách thêm thư mục KHBL vào sys.path, copy customer.py, mở kết nối ghi PMV riêng hoặc chia sẻ cookie đăng nhập. Ưu tiên nối qua giao diện dịch vụ được xác thực của KHBL. Hiện `khach_luu` là endpoint HTML/HTMX dùng session/CSRF, **chưa phải API JSON liên ứng dụng**. Nếu cần API liên ứng dụng mới thì thiết kế trong phạm vi người dùng giao, vẫn gọi service chuẩn và kiểm chứng sandbox; không ghi nhận API chưa tồn tại như thể đã có.

## Hợp đồng dữ liệu và lỗi

**Bổ sung triển khai KHCD ngày 14/09/2026:** đã có dịch vụ riêng `apps/pos/customer_bridge.py`, chạy qua `manage.py run_customer_bridge`, xác thực HMAC + tài khoản/quyền KHBL và cố định đích KK. Đây là API khách riêng, không biến `khach_luu` HTML/HTMX thành JSON API. `apps/pos/customer_popup.py` cung cấp nguyên template/CSS/JS KHBL cho popup trên danh sách khách KHCD: QR, ba SĐT, upload/camera/cắt ảnh qua cùng service, lưu dùng biên nhận bền vững có fingerprint ảnh. Xem `D:/PYTHON/KHBL/docs/CUSTOMER_BRIDGE_KHCD.md` để dùng đúng hợp đồng và allowlist. KHCD không import xuyên repo hoặc copy service ghi; tiến trình bridge dùng chính code KHBL. Không mở API này ra LAN.

- Form → clean_form nhận tên input PMV (`Phone`, `GhiChu2`, `GhiChu3`, `CMND`, ...), trả snake_case (`phone`, `phone2`, `phone3`, `cmnd`, ...). Ngày input ISO `yyyy-mm-dd`, ngày cho proc do clean_form chuyển `dd/mm/yyyy`.
- `NoiCap` mặc định do form/Sync gửi. `clean_form` không tự điền giá trị này khi adapter bỏ quên; giữ dữ liệu hiện có khi sửa.
- `upsert` nhận dữ liệu **đã clean**, không phải raw QR hay request.POST trực tiếp. Tạo với `cust_id=''`; sửa với CustID chọn rõ ràng.
- Trả `cust_id`, `cust_code`, `name`, `created`, `warnings`, `complete`, `errors`. Thiếu điểm hoặc ảnh/bản sao local lỗi → `complete=False` dù thông tin khách có thể đã COMMIT.
- `views.khach_luu`: chỉ khi hoàn tất trả 204 + `HX-Trigger: khachSaved`; JS đóng popup, refresh DS, toast. Lỗi trả fragment HTML (có thể HTTP 200), vì vậy HTTP 200 không phải DONE.
- `_khach_luu_kq.html` đưa CustID đã tạo trở lại hidden input bằng OOB khi partial. Khi bấm lưu lại phải dùng ID đó.
- Hồ sơ lưu thất bại do trùng: không gửi/ghi ảnh. Lỗi ảnh sau UPSERT: hồ sơ có thể đã có và file KK có thể đã được ghi; không cam kết rollback mọi thứ, không xóa hồ sơ để "hoàn tác".

## Double click, retry và kết quả chưa chắc chắn

- Form dùng `hx-sync="this:drop"`, disabled nút lưu, `save_token` 20–80 ký tự hợp lệ và fingerprint của payload/ảnh. `previous_save`/`remember_save` chỉ nhớ kết quả hoàn tất trong RAM của một process; không phải biên nhận bền vững.
- `SAVE_LOCK` + khóa MySQL trong service tuần tự hóa phần kiểm trùng/ghi giữa worker KHBL. Chúng không tự phát hiện mọi yêu cầu lặp qua restart, đặc biệt khách không có số điện thoại/CCCD.
- Với import/hàng đợi/retry qua restart, dùng thiết kế biên nhận bền vững của `CustomerSyncReceipt` làm mẫu: gắn source ID + target + fingerprint, lưu CustID ngay qua `on_created`. Không dùng bảng biên nhận Sync cho nguồn khác khi chưa xác định khóa nguồn và vòng đời.
- Nếu không rõ INSERT đã COMMIT: đối chiếu đầy đủ payload/biên nhận, không gọi INSERT tiếp chỉ vì timeout. Chưa xác định chắc kết quả thì báo cần kiểm tra, không giả báo thành công.
- Nếu đã có CustID thì chỉ hoàn tất đúng hồ sơ đó. Callback `on_created` chỉ chạy khi proc trả được mã; không bảo đảm có mã trong mọi sự cố kết nối.

## Sync một chiều là luồng riêng

`apps/pos/customer_sync.py`: nguồn `khj_cd.customer` → PMV; chỉ thêm, không ghi đè khách đã có. Đối chiếu Phone nguồn với cả ba cột, không đối chiếu danh tính bằng CCCD. Nhiều khớp → cần kiểm tra; không chọn TOP 1 để cho INSERT lọt.

INSERT có tên + SĐT 10 số, Address nguồn, BirthDate 1900, Gender NULL nếu chưa biết, CMND=CCCD nguồn nếu có, NoiCap mặc định. Các giá trị khác theo service/vendor. Không đem quy tắc Gender NULL/BirthDate nguồn của Sync áp lên form chỉnh sửa để xóa dữ liệu khách đã có.

## Ca nghiệm thu có ý nghĩa

- Số mới trùng ở mỗi cột của khách khác, trùng nội bộ ba ô, định dạng 9/11 số hoặc chữ số Unicode; không có proc ghi/ảnh khi bị từ chối.
- Hai yêu cầu tranh cùng số phụ: chỉ một được tạo. Lưu lại chính hồ sơ với ba số hợp lệ không bị coi là trùng chính nó.
- Bỏ gửi trường phụ giữ giá trị; gửi rỗng xóa đúng số phụ; giữ các thông tin/ảnh không sửa.
- Tìm từng số đúng ra cùng một khách; CCCD nhiều hồ sơ không bị gom; kiểm trùng không dùng danh sách đã cắt còn một dòng.
- Proc báo thành công nhưng đọc lại khác; ảnh KK lỗi; ghi bản sao local lỗi; thiếu sổ điểm: popup còn mở, nguyên nhân rõ, retry không sinh hồ sơ mới.
- Màn gọi nhận đúng CustID, vẫn giữ draft/hợp đồng đang thao tác và không tự chốt nghiệp vụ.

Chọn ca theo phần code thực sự thay đổi. Không mở rộng kiểm thử hay tạo dữ liệu thật chỉ vì skill liệt kê ca.
