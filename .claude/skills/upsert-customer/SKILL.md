---
name: upsert-customer
description: Tích hợp hoặc sửa luồng thêm/sửa khách PMV từ webapp KHBL, cầm đồ hoặc màn hình khác dùng I_CUSTOMER. Tái sử dụng UPSERT theo CustID, ba SĐT, ảnh CCCD, quy tắc webapp/PMV và xử lý hoàn tất/thất bại. Dùng khi triển khai lưu khách, gắn popup khách hoặc xử lý ảnh khách; không áp đặt cho CRM không dùng PMV.
---

# UPSERT CUSTOMER dùng chung

Quy tắc GĐ chốt 14/09/2026. Repo chuẩn: `D:\PYTHON\KHBL`. Đây là hướng dẫn tích hợp cho agent; chương trình gọi **service/endpoint hiện có**, không thực thi file skill lúc người dùng bấm Lưu.

## Điểm vào và nguồn chuẩn

- Đọc [pmv-proc-map](../pmv-proc-map/SKILL.md) trước khi sửa luồng đụng PMV. Mọi truy cập DB PMV qua `apps/pmv/gateway.py` và `PmvClient`.
- `apps/pos/customer.py`: `clean_form`, `prepare_images`, `upsert`, `cap_nhat_anh`, `saved_image`; đây là code ghi dùng chung.
- `apps/pos/customer_phones.py`: chuẩn hóa, kiểm định và SQL đối chiếu ba SĐT. Không viết bộ kiểm riêng ở trang mới.
- `apps/pos/views.py::khach_luu`, `templates/pos/_khach_form.html`, `static/js/khbl.js`: hợp đồng popup, token, phản hồi hoàn tất/lỗi.
- Đọc [tích hợp và phục hồi](references/tich-hop.md) khi thêm trang gọi lưu khách, import/sync hoặc xử lý retry.
- Khi có QR: đọc [chuan-hoa-tieng-viet](../chuan-hoa-tieng-viet/SKILL.md). Khi có ô ảnh CCCD có thể sửa: đọc [cat-anh-cccd](../cat-anh-cccd/SKILL.md).
- Các đường dẫn code trong skill là tương đối với repo chuẩn. Khi code thay đổi, kiểm tra lại các hàm trên và cập nhật skill; không coi số liệu kiểm thử cũ là bằng chứng cho phiên bản mới.

## Các quyết định đã chốt

1. **Giữ nguyên I_CUSTOMER cũ.** Không tự gộp/xóa CustID, xóa số cũ, liên kết hồ sơ, chuyển điểm/công nợ hoặc đổi khách của giao dịch lịch sử.
2. UPSERT xác định theo **CustID**: trống → tạo mới, có → sửa đúng khách. Không dùng CCCD/tên để UPDATE nhiều dòng. Nếu CustID đã mất, báo lỗi, không tự chuyển sang INSERT.
3. Chốt 21/09/2026: thêm mới trùng CCCD và khác SĐT → hiện tên, CCCD, CustID và nút “Thêm số điện thoại cho khách này”. Người dùng chọn rõ hồ sơ; `prepare_append` nạp hồ sơ theo CustID và điền số mới vào SĐT 2, rồi SĐT 3 nếu trống. Người dùng kiểm tra rồi Lưu qua UPSERT UPDATE. Không tự gộp, không tự chọn một CustID khi có nhiều kết quả. Đủ 3 số thì từ chối; kiểm trùng chéo và guard có chữ ký kiểm lại các số/CCCD trong khóa trước ghi để tránh ghi đè số vừa thay đổi.
4. Lưu khách không tự tạo/chốt hợp đồng cầm đồ, hóa đơn, thanh toán hoặc chuyển số dư. Trang gọi tiếp tục nghiệp vụ của mình với CustID trả về sau khi lưu khách hoàn tất.

## Quy tắc phía webapp

| Input form | Dữ liệu đã clean | I_CUSTOMER | Quy tắc |
|---|---|---|---|
| `CustID` | `cust_id` | `CustID` | Do PMV sinh khi tạo; dùng lại khi sửa |
| `Phone` | `phone` | `Phone` | SĐT chính |
| `GhiChu2` | `phone2` | `GhiChu2` | SĐT 2, không JSON/ghi chú |
| `GhiChu3` | `phone3` | `GhiChu3` | SĐT 3, không JSON/ghi chú |
| `CustName` | `name` | `CustName` | Bắt buộc; chuẩn hóa tiếng Việt |
| `CMND` | `cmnd` | `CMND` | CCCD 12 hoặc CMND 9 số; không có thì trống |
| `NoiCap` | `issued_by` | `NoiCap` | Form/Sync mặc định `Cục Cảnh Sát QLHC về TTXH` |
| `BirthDate` | `birth` | `BirthDate` | Không có → 01/01/1900, không lấy ngày hôm nay |

- SĐT nhập mới là chuỗi **đúng 10 chữ số ASCII**, giữ số 0 đầu. Ô SĐT được để trống; riêng Sync yêu cầu có SĐT + tên.
- Dùng `phone_key` chung để bỏ dấu cách, chấm, ngoặc, gạch nối khi đối chiếu. Không tự đổi đầu số, suy đoán số hoặc nhận chữ số Unicode giống hình dạng.
- Các số không rỗng trong một hồ sơ phải khác nhau. Mỗi số kiểm tra với **cả ba cột của tất cả khách**, kể cả khách ngừng hoạt động. Khi sửa, chỉ loại trừ chính CustID đang sửa.
- Giữ dữ liệu cũ không có nghĩa bỏ kiểm tra khi lưu lại: hồ sơ cũ sai định dạng/trùng vẫn có thể bị từ chối lưu.
- Trường phụ không gửi/`None` → giữ giá trị đang có; gửi `""` → chủ đích xóa số phụ. Các trường khác không phải PATCH tự động; adapter sửa khách phải giữ đủ dữ liệu, không gửi một payload thiếu rồi làm trống hồ sơ.
- `Gender`: PMV bit 1 Nam, 0 Nữ. Form yêu cầu chọn; Sync dùng `allow_unknown_gender=True` cho NULL khi chưa biết. Không suy giới tính từ tên.
- Cảnh báo lúc nhập đủ 10 số qua `khach_kiem_sdt` chỉ là gợi ý. Quyết định nằm trong kiểm tra phía server ngay trước proc, không dựa vào JS hay kết quả lookup trước đó.
- Xác thực/quyền theo `apps/pos/quyen.py` và danh mục đã gán. Service Python không thay thế kiểm tra quyền/CSRF ở endpoint. Không tự nới quyền để trang khác gọi được.

## Luồng lưu bắt buộc giữ

`clean_form → chuẩn bị ảnh → token/fingerprint → SAVE_LOCK → upsert → đọc kiểm hồ sơ → kiểm/mở sổ điểm → ghi và đọc kiểm ảnh → complete → đóng popup → làm mới DS → toast`.

- `upsert` được bọc khóa MySQL `khbl:customer-write:<target>` dùng chung worker. Gọi `with C.SAVE_LOCK:` như endpoint hiện có; `SAVE_LOCK` không reentrant, không lồng hai lần. Truyền `client=` bằng keyword, giữ nguyên đích dữ liệu trong cả thao tác.
- Không sao chép proc/SQL ghi sang cầm đồ hoặc trang khác, không tự gọi proc sau khi chỉ kiểm trùng ở giao diện. Gọi lại service qua đường có khóa.
- Không lưu file ảnh khách vào KK/kho ảnh local nếu UPSERT hồ sơ bị từ chối. Việc giải mã/nén trước đó không phải xác nhận đã lưu ảnh; upload framework có thể dùng tệp tạm.
- Chỉ `result["complete"] == True` mới coi DONE. Với lỗi sau khi hồ sơ đã tạo, giữ CustID để tiếp tục đúng hồ sơ; không INSERT lại.
- Nếu mất kết nối và chưa rõ proc đã COMMIT hay chưa, cần đối chiếu/biên nhận trước khi thử lại. Token trong RAM không phải bảo đảm exactly-once qua khởi động lại.

## Quy tắc và giới hạn phía PMV

- SQL Server KK 2005. `Phone varchar(30)`, `CMND varchar(20)`, `GhiChu2/GhiChu3 nvarchar(max)`. Ba cột SĐT là quy ước ứng dụng, không phải ba cột điện thoại có ràng buộc duy nhất sẵn trong DB.
- Ghi qua `I_CUSTOMER_Ins` / `I_CUSTOMER_Upd`; mở sổ điểm qua `I_DiemTichLuy_InsFromGT`. CustID/CustCode do vendor sinh, không tự cấp.
- Proc vendor kiểm trùng **Phone và CMND**, không kiểm trùng chéo GhiChu2/GhiChu3 và không áp đặt chuẩn 10 số. Webapp phải kiểm đầy đủ trước khi gọi.
- `I_CUSTOMER_Upd` có thể từ chối cả hồ sơ cũ trùng CMND. Không xóa/để trống CMND để lách kiểm tra.
- Proc cập nhật còn xử lý `I_GIAODICH_KHACHHANG`, `SHOP_CUSTOMER`, đường dẫn ảnh. Dùng `_params`/`_trade_xml` qua service để giữ dữ liệu; không gửi mặc định tất cả tham số với ý nghĩ chỉ sửa một cột.
- PMV không cung cấp một giao dịch nguyên tử bao trùm hồ sơ + điểm + file KK + bản sao local. Thành công proc/hay HTTP 200 chưa đủ chứng minh hoàn tất.
- Khóa và kiểm tra ba SĐT bảo vệ **các luồng ghi qua KHBL**. PMV desktop/ứng dụng khác không dùng khóa này vẫn có thể tạo xung đột số phụ. Không tuyên bố đã có ràng buộc duy nhất toàn DB.

## Ảnh đại diện và hai mặt CCCD

| File input | Kind đọc ảnh | Hậu tố local |
|---|---|---|
| `anh_dai_dien` | `dai-dien` | `DD` |
| `anh_truoc` | `mat-truoc` | `MT` |
| `anh_sau` | `mat-sau` | `MS` |

- Tái sử dụng upload/chụp camera → xem trước → dùng ảnh/chụp lại của form. Hai ô CCCD có nút **Tách thẻ** theo skill cắt ảnh. Chụp/cắt xem trước không đồng nghĩa UPSERT khách.
- `prepare_images`: ảnh hợp lệ tối đa 15 MB, 25 triệu pixel; xoay EXIF, RGB, resize và JPEG. Ngưỡng hiện tại: đại diện 1600 px/380.000 byte, CCCD 2000 px/650.000 byte, quality 88→78. Đây là mục tiêu dung lượng, không phải trần tuyệt đối hay bảo đảm mọi ảnh đọc rõ.
- Sau khi hồ sơ lưu và đọc kiểm, service gọi `I_CUSTOMER_Upd` để ghi ảnh, kiểm đường dẫn và đọc được bytes ảnh rồi mới lưu bản sao local.
- SQL giữ đường dẫn vendor, thường `D:\PHANMEMVANG\HINHANHKH\...`. Tên KK do proc dựng; **không giả định tên KK giống tên local**.
- Kho local theo `settings.CUSTOMER_IMAGE_ARCHIVE_ROOT`, mặc định `D:\PYTHON\KHBL\media\cccd\`; **không tạo thư mục CU… con**.
- Tên local: `CustID_cust_name_MT.jpg`, `_MS.jpg`, `_DD.jpg`; tên khách bỏ dấu, chữ thường, gạch dưới. `_archive_name` là hàm duy nhất đặt tên.
- Bản sao local nén riêng: đại diện mục tiêu 140.000 byte, CCCD 320.000 byte; giữ mức rõ hợp lý theo thuật toán hiện có. Ghi tệp qua `_atomic_write`; không thay bằng ghi từng phần lên file đích.
- Đọc qua `saved_image(CustID, kind, client=...)` và route `khach_anh`: tìm bản local theo hồ sơ, nếu thiếu thì đọc ảnh PMV và cache; sandbox có fallback đọc KK. **Hàm đọc có thể tạo cache local**, không dùng khi nhiệm vụ cấm mọi ghi file.
- Không đưa đường dẫn Windows làm URL `<img>`, không tìm/thay chuỗi đường dẫn một cách mù quáng, không tự công khai kho ảnh. Chỉ hiện ảnh đã lưu khi route đọc được; lỗi nêu đúng mặt ảnh và nguyên nhân.
- Chỉ đổi ảnh của khách đã chọn: dùng `cap_nhat_anh`, không dựng UPSERT hồ sơ thiếu trường. Ảnh trên phiếu/hợp đồng riêng không tự biến thành ảnh hồ sơ khách nếu nghiệp vụ chưa yêu cầu.

## Tìm kiếm

- Dùng `customer_phones.search` thông qua `services.khach_loc`, `tim_khach`, `danh_sach_khach`.
- Đủ 10 số: khớp chính xác ba cột, tối đa một CustID; dữ liệu cũ trùng thì ưu tiên Phone → GhiChu2 → GhiChu3 → CustID tăng dần. Đây là chọn dòng hiển thị, không phải hợp nhất danh tính.
- CCCD/CMND đủ 12/9 số: khớp chính xác, giữ mọi dòng khớp. Tên/chuỗi chưa đủ: tìm gần đúng, có thể nhiều dòng.
- Lọc phụ và Active vẫn áp dụng. Kiểm trùng khi ghi luôn xét **tất cả kết quả**, không lấy TOP 1 của tìm kiếm để kết luận số chưa được dùng.

## Kiểm chứng khi tích hợp

Chỉ sửa tài liệu skill thì kiểm cấu trúc/liên kết, không ghi thử khách thật. Khi sửa code nghiệp vụ:

```powershell
venv\Scripts\python.exe -X utf8 manage.py test tests.test_customer_phones tests.test_customer_sync tests.test_customer --settings=config.settings.test_customer_sync
venv\Scripts\python.exe -X utf8 manage.py smoke_customer_phones
venv\Scripts\python.exe -X utf8 manage.py smoke_customer_sync
```

Hai smoke có ghi **sandbox** và tự dọn đúng khách thử. Dùng kiểm ảnh phù hợp nếu thay luồng ảnh; kiểm UI form lỗi/hoàn tất. Không chạy `python -m unittest` thay `manage.py test`. Không reset mật khẩu để thử giao diện. Trước áp dụng code ghi PMV thật, theo quy trình sandbox/backup/gateway của repo; skill không tự cấp quyền thực hiện giao dịch thật ngoài yêu cầu hiện tại.
