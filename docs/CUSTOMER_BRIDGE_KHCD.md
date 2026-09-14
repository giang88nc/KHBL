# Dịch vụ khách dùng chung cho KHCD — 14/09/2026

Theo yêu cầu chủ hệ thống: KHCD đọc khách KK và UPSERT qua service chuẩn của KHBL.

`manage.py run_customer_bridge --key-file <tệp khóa riêng>` chạy WSGI/Waitress riêng ở `127.0.0.1:18202`; KHCD Windows host quản lý tiến trình. Không cần restart web bán lẻ, không đưa URL vào Caddy/LAN. `--target kk` mặc định cố định; sandbox là tham số khởi chạy thử, request không chọn được đích.

`POST /v1/customers`, JSON tối đa 88 MiB để chứa multipart ảnh base64. KHCD chặn request trình duyệt quá 48 MiB; service chuẩn kiểm từng ảnh 15 MB/25 triệu pixel. Header X-KHCD-Time, X-KHCD-Nonce (32 hex), X-KHCD-Signature = HMAC-SHA256 của `time + newline + nonce + newline + body`. Thời gian trong 60 giây, nonce không dùng lại. Body có actor (ID auth_user) và actor_proof = HMAC-SHA256 của hash mật khẩu nguồn với khóa dịch vụ. Chỉ tài khoản active có quyền KHACH_HANG được đọc; can_edit mới được save. Không nhận cookie hoặc truyền mật khẩu tới trình duyệt.

Thao tác giới hạn: list(q,page), get(id), batch(ids ≤ 200), search_ids(q, giới hạn 1.000 để yêu cầu thu hẹp), directory (khóa phục vụ đối soát + biên nhận sync hoàn tất), save(form,token,version), receipt(token). Không có SQL/proc tùy ý, xóa, gộp hoặc giao dịch tiền. Mọi truy vấn qua PmvClient/gateway. Tra ba SĐT dùng customer_phones.search, kiểm trùng dùng customer.duplicate_errors và được lặp lại trong service có khóa.

get trả customer, form đầy đủ theo input PMV, version và can_edit. save đọc giữ trường không gửi; form gửi rỗng là chủ động xóa. CustID rỗng là thêm, có mã là sửa; không tìm được mã thì lỗi. Pipeline dùng clean_form, prepare_images, SAVE_LOCK, upsert(client=..., on_created=...), kiểm complete. Action save JSON cũ vẫn giữ ảnh cũ; popup có hợp đồng multipart riêng bên dưới để nhận ảnh/QR/crop.

## Popup KHCD dùng nguyên form KHBL

`action=popup` gọi `apps/pos/customer_popup.py`. Payload gồm `path`, `method`, `query`, `content_type`, `body` (base64 nguyên request), hoặc `asset` trong allowlist. Chỉ có thêm/sửa/lưu, kiểm SĐT, phân tích QR, đọc ảnh, cắt ảnh và dùng ảnh đã cắt; không xuất endpoint xóa/gộp hay nghiệp vụ bán/thâu. Đích PmvClient được truyền rõ từ bridge; không theo công tắc đích của web KHBL.

Kết quả là `status`, `body` base64, `headers` giới hạn Content-Type/HX-Trigger. Form render trực tiếp template chuẩn, URL reverse có prefix `/camdo/khach-hang/popup/api/`. Tài nguyên CSS/JS/font đọc từ nguồn KHBL được allowlist; không mở thư mục tệp tùy ý. QR, kiểm SĐT, cắt/dùng ảnh gọi view/service chuẩn. `services.khach_theo_id` nhận client tùy chọn; view kiểm SĐT/cắt ảnh nhận client nội bộ và renderer cô lập, không đổi cách gọi KHBL cũ.

KHCD kiểm session/CSRF tại biên trình duyệt; bridge kiểm HMAC/actor/quyền riêng từng request. Iframe cùng origin dùng đúng CSS/JS KHBL, chỉ adapter đóng popup/thông báo parent và token CSRF là riêng. HTML render không nạp context bán hàng không liên quan. File tạm upload đóng sau request. Lưu đi qua biên nhận bền vững hiện có; fingerprint bổ sung hash ảnh, giữ tương thích token cũ không ảnh. Partial trả CustID, version hiện tại và token sửa mới; uncertain không tự retry INSERT. Không sửa thuật toán QR/cắt/nén hay proc ghi.

Migration pos 0027 tạo customer_bridge_receipt riêng trong khj_bl. Token gắn tài khoản/target/fingerprint, lưu CustID qua callback ngay khi tạo. Kết quả complete mới DONE; partial giữ mã, uncertain không tự INSERT lại. receipt đọc lại cùng tài khoản; khi đã có CustID có thể mở đúng hồ sơ và hoàn tất bằng yêu cầu sửa mới. Không sử dụng bảng customer_sync_receipt cho các yêu cầu form KHCD. Không cam kết nguyên tử hoặc exactly-once ở phía proc khi mất kết nối trước khi nhận mã.

Kiểm đơn vị: `python -X utf8 manage.py test tests.test_customer_popup tests.test_customer_bridge tests.test_customer --settings=config.settings.test_customer_popup`. Test settings SQLite có collation ascii_bin để tương thích schema OA mới; không dùng DB vận hành.

### Bàn cầm đồ thu gọn (14/09/2026)

- `pawn_banks`: chỉ đọc danh mục `gold_bank` đang Active qua `vietqr.active_banks`; `default_id` ưu tiên type=pawn, không mặc định sang gold nếu thiếu pawn.
- `pawn_qr`: nhận kind=bank/receipt, text hoặc ảnh base64. Dùng chuẩn hóa ảnh Customer và `vietqr.doc_anh`; kind=bank kiểm parser/CRC, chỉ nhận QRIBFTTA, trả ngân hàng/tài khoản/tên nếu QR có và ảnh QR. Kind=receipt chỉ trả nội dung mã để KHCD đối chiếu chính xác. Không gọi UPSERT và không tạo giao dịch.
- `pawn_images` hỗ trợ anh_truoc/anh_sau/anh_sp1/anh_sp2/anh_qr; chỉ chuẩn hóa JPEG/EXIF, không ghi hồ sơ khách.
- `popup` với pawn_photos=1 có group=front/back/products, tái sử dụng camera/cắt CCCD chuẩn; group mặc định giữ bốn ảnh tương thích. KHCD nhúng ba khung độc lập và điều khiển khóa ảnh bằng adapter cùng origin.
- Trần request bridge 112 MiB đáp ứng năm ảnh (15 MiB/ảnh) mã hóa base64; trần riêng của popup CRUD vẫn 48 MiB. 31 kiểm thử popup/bridge/customer đạt, gồm QR ảnh thật được tạo từ dữ liệu giả, thiếu tên/CRC lỗi, nhóm ảnh và mặc định tài khoản pawn.
Kiểm ghi thật trên sandbox: `python -X utf8 manage.py smoke_customer_bridge`; chạy riêng, không chạy song song các smoke có kiểm tổng số dòng. Lệnh tạo đúng một khách thử và hai biên nhận, dọn theo ID/token chính nó tạo. Không thử ghi khách vận hành KK.

Khóa riêng nằm ở instance KHCD, không commit; chỉ đường dẫn được truyền qua dòng lệnh. Việc quản trị quyền, gateway allowlist và chốt PMV_GHI_KK giữ nguyên. Bản backup KK trước chuyển nguồn: PMV_BANLE_KH2_20260914_1636.bak, COPY_ONLY/VERIFY OK.
