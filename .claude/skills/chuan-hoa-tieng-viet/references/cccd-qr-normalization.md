# RAW → mốc trường → profile → lưới ứng viên → ngữ cảnh → kết quả

Engine: `apps/pos/cccd.py`. `PROFILE_LABELS` và `detect_profiles()` nhận dạng dữ liệu;
`_field_candidates()` gọi helper; `normalize_cccd_group()` hợp nhất theo trường.
`apps/pos/cccd_context.py` quản lý ngữ cảnh cục bộ đã duyệt.
Thêm profile tại đây, không viết bộ giải mã song song trong skill hoặc JavaScript.

## Phân chia trường

Không đếm pipe rồi yêu cầu đúng số phần. Bảy trường nghiệp vụ thông thường chỉ
cần sáu dấu phân cách. `split_raw()` giữ fast path cho cấu trúc rõ ràng, kể cả
trường trống. Khi dấu phân cách thay đổi: xác định CCCD 12 số ASCII, CMND cũ 9 số
nếu có, ngày sinh 8 số, giới tính, địa chỉ và ngày cấp 8 số. Hỗ trợ thiếu/lặp pipe,
pipe chen trong tên/địa chỉ, trường mở rộng sau ngày cấp. Không tự điền trường
mở rộng vào khách. Thiếu mốc, ngày sai hoặc nhiều mốc khả dĩ thì yêu cầu quét lại.

Chỉ thay pipe nằm trong chuỗi byte đã nhận diện trước khi tìm mốc. Không kéo số
nhà sang trường CCCD, không tự bỏ chữ trong ngày. Bản phục hồi ranh giới cần đối chiếu.

## Kênh giải mã

| Profile | Dấu hiệu và luồng |
|---|---|
| unicode | Giữ nội dung Unicode, chuẩn NFC; không áp OEM toàn cục |
| utf8_mojibake | `Ã`, `Æ`, `áº`, `á»`, byte C1; strict UTF-8 từng đoạn, giữ phần lỗi |
| scanner_bytes | `á»\` → E1 BB 85; `á»` rồi pipe → E1 BB 81, chỉ trong chuỗi byte tương ứng |
| byte_numbers | Byte UTF-8 in dạng `0198...`; dùng helper cũ, giữ số độc lập |
| decimal | `Nguy7877n`, `432417`; whitelist chữ Việt, tách trọn dãy số |
| oem | Byte thấp Unicode hiện OEM/CP437, có thể trộn mã số |
| escaped | HTML numeric entity/percent; chỉ giải mã trong trường |

Các profile có thể cùng xuất hiện. Giải mã OEM trước codepoint để không biến chữ
vừa phục hồi thành ký hiệu OEM lần nữa. Unicode gốc vẫn là candidate.
Chỉ gom khoảng trắng/dấu phẩy sau giải mã: NBSP có thể biểu diễn byte A0.

`_lattice_candidates()` giải mã từng token gốc, rồi beam search tối đa 32 nhánh
trên câu. Không giải mã cả địa chỉ rồi cắt 16 tổ hợp đầu: cách đó bỏ mất phương
án đúng ở từ phía sau. Mỗi cạnh có bằng chứng kênh mã, phạt ký tự/mã số còn hỏng,
kiểm tra chính tả hỗ trợ và điểm từ/cụm hai-ba từ từ D đã duyệt. Giải OEM trước mã
số trong cùng cạnh; không đưa Unicode vừa phục hồi qua giải OEM lần nữa.

`í` có thể là `ơ`/`ạ`; `╙` có thể là `Ó`/`ồ`; `∙` có thể là `ù`/`ỹ`. Giữ các cách
hiểu cạnh tranh. Số nhà/mã khu `272A`, `B272`, `KP7889` phải giữ nguyên. Khi nhận
diện lỗi máy quét, có ứng viên sửa hoa/thường, TP./KP, hậu tố số nhà và vị trí dấu
tương đương (`Hoà`/`Hòa`), không đổi địa danh theo kiến thức bên ngoài.

CP437 low-byte là mô hình phù hợp mẫu, không chứng minh model scanner. `ơ` (417)
và `ạ` (7841) cùng byte thấp 161, có thể đều hiện `í`. Chỉ số này không tự xác định
ký tự gốc; không thêm quy tắc thay chuỗi một chiều dựa trên một tên khách.

## Kết quả và bằng chứng

- Tối đa 5 RAW, mỗi chuỗi 8192 ký tự; mâu thuẫn CCCD/CMND/ngày thì không hợp nhất.
- Quét lặp RAW chỉ tính một nguồn. Candidate cùng lượt quét không phải nhiều phiếu.
- `suggestions`: value, score, sources, methods, uncertain, riêng từng trường.
- `field_provenance`: nguồn hỗ trợ gợi ý ưu tiên.
- `AUTO_ACCEPT`: Unicode không cảnh báo. Chỉ là trạng thái parser, không xác minh
  với thẻ thật hoặc cho phép tự lưu khách.
- `ACCEPT_WITH_WARNING`: có biến đổi giải mã; cần người dùng áp dụng sau đối chiếu.
- `MANUAL_REVIEW`: thiếu byte, ứng viên cạnh tranh, ngữ cảnh mâu thuẫn hoặc thiếu trường.
- Điểm là heuristic chưa hiệu chuẩn. `Thơi → Thới`, `Lòn → Lớn` cần nguồn D đã
  duyệt và khớp fingerprint toàn mẫu; không phải quy tắc sửa cho mọi khách.
- `trusted_context` chỉ kiểm tra mâu thuẫn với khóa CCCD; không tự ghi đè.
- Giới hạn số candidate để tránh tăng tổ hợp; va chạm phải giữ trạng thái đối chiếu.

## Cột D đã duyệt và model cục bộ

Người dùng đã xác nhận D là đáp án. CLI đọc B/C làm RAW, D làm nhãn; không sửa
Excel. Kiểm tra toàn bộ CCCD/CMND/ngày giữa B/C và D trước khi tạo model. Cùng
fingerprint có hai đáp án khác nhau phải từ chối cập nhật.

Tạo/cập nhật sau khi có phê duyệt D (đã có cho workbook hiện tại):

```powershell
venv\Scripts\python.exe -B .claude\skills\chuan-hoa-tieng-viet\scripts\normalize_cccd_qr.py --xlsx '<workbook>' --build-context var\private\qr\approved_context.json --approve-column-d
```

Model gồm từ/cụm từ, SHA-256 của toàn bộ trường RAW, đáp án và dòng nguồn. Không
lưu RAW hay CCCD dạng rõ, nhưng đáp án vẫn có họ tên/địa chỉ: giữ private và ngoài
Git, không gửi dịch vụ bên ngoài. File được thay thế nguyên tử sau khi kiểm tra.
Có thể cấu hình `KHBL_QR_CONTEXT_PATH`. Khi chuyển máy phải sao lưu/chuyển model;
chỉ chuyển code sẽ thiếu nguồn tham chiếu. Request chỉ đọc, không tự học từ lượt quét.

`approved_column_d` chỉ dùng khi hash **tất cả** trường RAW khớp, kể cả định danh,
ngày và chữ hỏng; không tra theo tên/CCCD riêng. Hiệu chỉnh theo D không lan sang
khách khác. Kết quả hiển thị việc dùng mẫu đã duyệt và dòng Excel tương ứng qua
`approved_examples`; `extra_fields` giữ trường mở rộng.

Đánh giá:

```powershell
venv\Scripts\python.exe -B .claude\skills\chuan-hoa-tieng-viet\scripts\normalize_cccd_qr.py --xlsx '<workbook>' --benchmark --leave-one-out
```

Báo riêng B, C, B+C; đo khớp **toàn QR và từng trường**, không chỉ parsed.
`decoder_without_example_lookup` tắt tra đáp án nhưng dùng từ/cụm từ đã học.
`leave_one_card_out` bỏ toàn bộ B/C/D của thẻ đang thử khỏi ngữ cảnh trước khi thử.
`with_approved_examples` đo khả năng trả đúng mẫu có sẵn, không phải chính xác trên
mọi thẻ mới. Bộ hiện tại có 23 dòng, 44 RAW; tám RAW ở dòng 4/6/11/12 cần hiệu
chỉnh từ D ngoài phép đổi bảng mã. Không được báo tỷ lệ tra mẫu là độ chính xác
giải mã thuần túy.

## Thêm máy hoặc dạng lỗi mới

1. Thu RAW, ứng dụng nhận, cấu hình máy nếu biết; không suy ra hãng từ dấu lỗi.
2. Xác định giả thuyết byte/codepoint và ví dụ phản chứng có thể sửa sai.
3. Bổ sung detector/helper và fixture giả, gồm cả trường hợp mơ hồ.
4. Test số/ngày, Unicode chuẩn, cùng RAW không tăng bằng chứng, nguồn mâu thuẫn,
   ký tự HTML, server về chậm và mất kết nối.
5. Test riêng mỗi máy và nhóm nhiều máy; luôn báo riêng tra mẫu với thử trên thẻ
   được loại khỏi ngữ cảnh. Không tự hạ D đã duyệt xuống dữ liệu tham khảo.

Endpoint phân tích dùng đăng nhập/CSRF và `Cache-Control: private, no-store`, không
đọc/ghi PMV. Không log nội dung RAW hoặc thêm mẫu thật vào Git.

## Dạy mẫu qua form RAW → output

Trang `/banle/khach-hang/qr/cong-cu/` gọi `qr_learning_views.preview` để xem thử
và `qr_learning_views.save` khi người dùng bấm Áp dụng. Save yêu cầu superuser,
đăng nhập và CSRF; không gọi PMV/UPSERT. Dùng `cccd_learning.save_rule()` cho mọi
ghi mẫu, không tự sửa JSON bằng công cụ khác vì phải giữ khóa ghi và revision.

`learned_rules.json` độc lập với `approved_context.json`: nhập lại Excel không làm
mất mẫu dạy trên web. File được ghi nguyên tử, khóa theo thread/process, kiểm tra
revision để hai cửa sổ không ghi đè. Retry cùng cặp không tạo bản trùng; cùng RAW
mà output khác phải dùng Sửa. Có active và lịch sử để tạm tắt hoặc đối chiếu thay đổi.

Mỗi rule có raw, output, scope (`text`, `ho_ten`, `dia_chi`, `qr`), id, active,
updated_at/by. Full QR thêm fingerprint tất cả trường và fields đã kiểm tra.
Kho học có RAW và output do người dùng chủ động phê duyệt lưu; giữ trong private,
ngoài Git. Các lượt quét thông thường chỉ đọc kho này, không tự học.

Kho version 1 còn có `drafts`: id, raw, scope, created_at/by. Store cũ thiếu
`drafts` được đọc như danh sách rỗng. `save_draft()` khóa file, kiểm tra revision,
chống nháp trùng và ghi audit. Endpoint `qr/hoc/luu-nhap/` yêu cầu superuser,
đăng nhập, CSRF và chỉ nhận RAW/phạm vi; không cần output, không gọi PMV.

`save_rule(..., draft_id=...)` chỉ xóa nháp khi RAW của rule khớp toàn bộ RAW
nháp. Việc thêm rule và xóa nháp nằm trong cùng lần `os.replace`, nên không có
trạng thái đã học nhưng vẫn CHỜ do ghi dở. Nếu rule chỉ là đoạn được LỌC từ RAW
dài, rule vẫn được học nhưng nháp giữ lại và UI báo đang xử lý một phần. Retry
sau phản hồi thất lạc không nhân đôi rule hay nháp.

UI chỉ hiển thị số lượng CHỜ/ĐÃ HỌC trên form. Popup dùng chung hai tab: CHỜ có
RAW một dòng và nút Xử lý; ĐÃ HỌC có `RAW → output` một dòng, Sửa và Tạm tắt.

`mask_fragments()` khớp literal, ranh giới nguyên từ, ưu tiên cụm dài rồi phạm vi
cụ thể. Bộ giải mã chỉ xử lý phần RAW chưa được dạy, dùng placeholder bảo vệ output
đã duyệt; phục hồi đúng một lần sau đó. Không nối hiệu chỉnh dây chuyền, không đổi
chữ số/giới tính bằng rule cụm chữ. Full QR phải giữ số định danh và ngày.

Sau Save thành công, form báo rõ đã học và cập nhật danh sách. `localStorage`
chỉ phát revision (không chứa RAW) để popup ở tab cùng origin hủy kết quả cũ;
quét lại cùng RAW vẫn được phân tích với kho mới. Mất kết nối/ghi lỗi giữ nguyên
form và không báo thành công.

CLI đọc cùng kho khi phân tích QR. Với cụm rời, truyền stdin JSON
`{"text":"KhaV259nC226n","scope":"dia_chi"}`. Benchmark tắt đối chiếu mẫu và
leave-one-card-out cũng tắt rule được dạy, tránh dùng đáp án đang thử làm bằng chứng.

### Dịch và lọc đoạn còn lỗi

POST `qr/hoc/dich/` nhận `raw, scope`, trả `output, warnings`; không yêu cầu nhập
output trước. POST `qr/hoc/loc/` nhận cả `raw, output, scope`, trả `segments` với
cặp chữ, scope, vị trí trên hai chuỗi gốc và `alignment` (`token`/`context`).
Cả hai yêu cầu đăng nhập/CSRF, no-store, không gọi PMV và không ghi mẫu.

`cccd_tools` dùng engine tạo gợi ý token để ghép với kết quả hiện tại. Từ bung
ra từ cùng một token nguồn cùng trỏ về cả token đó; phần chưa khớp được giữ
theo cụm giữa các mốc. Từ lặp với số lần khác nhau phải giữ ngữ cảnh, không đoán
vị trí. QR được kiểm tra cùng mốc số/ngày; đoạn vượt nhiều trường giữ cả QR.
Offset luôn trỏ về chuỗi đầu vào, không phải chuỗi đã được chuẩn hóa.

Giao diện giữ từng cặp riêng, lưu nội dung đang sửa khi chuyển đoạn; LỌC không
ghép các đoạn rời thành một quy tắc. Bấm Dịch/LỌC có thể hoàn tác phần nhập,
không hoàn tác mẫu đã lưu. Lỗi mạng, phản hồi cũ, hoặc không phát hiện lỗi mã
không được xóa/ghi đè nội dung đang nhập. Chỉ Áp dụng mới ghi learned_rules.
