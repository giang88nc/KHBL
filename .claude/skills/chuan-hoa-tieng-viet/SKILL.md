---
name: chuan-hoa-tieng-viet
description: Chuẩn hóa chữ TIẾNG VIỆT bị hỏng mã về đúng dấu — máy quét CCCD trả chuỗi lạ, dán từ Excel/clipboard ra ký tự rác, mojibake kiểu 'TrÆ°Æ¡ng', dãy số 0225 0187 0141 thay cho chữ, thực thể HTML &#7885;, chuỗi %E1%BB%8D, hoặc dữ liệu cũ trong CSDL bị sai bảng mã. BẮT BUỘC đọc trước khi viết bất kỳ code nào nhận chữ tiếng Việt từ nguồn ngoài (quét mã, nhập liệu, import, đọc dữ liệu vendor) hoặc khi thấy tên/địa chỉ khách hiện sai dấu.
---

# CHUẨN HÓA CHỮ TIẾNG VIỆT BỊ HỎNG MÃ

## QR CCCD: định tuyến theo RAW

Dùng `apps/pos/cccd.py::normalize_cccd_group(scans, trusted_context=None)` với 1–5
RAW cùng thẻ. Engine nhận dạng Unicode, UTF-8 mojibake, byte đặc biệt, mã số
Unicode, OEM và HTML/percent; giải mã từng từ rồi xét ngữ cảnh cả câu; trả kết quả,
gợi ý từng trường và bằng chứng nguồn.
Đọc [quy tắc QR và mở rộng profile](references/cccd-qr-normalization.md) khi gặp
kiểu lỗi mới. Không tạo normalizer riêng cho mỗi hãng máy.

Popup dùng `CCCD.analyze()` gọi POST `banle/khach-hang/qr/phan-tich/`. **Máy chủ là
nơi xếp hạng duy nhất**. JavaScript giữ RAW, chống phản hồi cũ và hiển thị gợi ý;
không tự dùng helper offline `CCCD.parse()` thay kết quả server khi mất kết nối.

### Công cụ dạy thêm mẫu ngay trên web

Mở `/banle/khach-hang/qr/cong-cu/` hoặc bấm **Dạy bộ đọc QR** ở trang khách hàng/
popup. Nhập **RAW → kết quả đúng → Áp dụng — học mẫu** là phê duyệt lưu cặp đó
vào kho dùng chung `var/private/qr/learned_rules.json`, có hiệu lực ở lượt quét
tiếp theo, không cần sửa SKILL.md hay khởi động web. `Xem thử` không ghi. Có Sửa,
Tạm tắt/Bật lại, kiểm tra phiên bản đồng thời, lịch sử thay đổi và chống lưu trùng.

Nếu mới có RAW nhưng chưa xác định được kết quả, **Lưu nháp** đưa RAW vào danh
sách CHỜ trong cùng kho private. Mở CHỜ → Xử lý → Dịch/đối chiếu → Áp dụng;
khi RAW đầy đủ khớp nháp, một lần ghi sẽ thêm mẫu học và lấy nháp khỏi CHỜ.
Nếu chỉ học một đoạn đã LỌC từ RAW dài, giữ RAW đầy đủ trong CHỜ để tránh đánh
dấu nhầm là đã xử lý xong. CHỜ và ĐÃ HỌC nằm trong popup quản lý; mẫu học hiển
thị một dòng `RAW → kết quả`.

**Dịch** gọi cùng engine, điền gợi ý sang Kết quả đúng mà chưa học mẫu.
**LỌC** gọi `apps/pos/cccd_tools.py::filter_errors()` để giữ các cặp đoạn còn lỗi
mã. Ghép bằng mốc từ đã giải mã, hỗ trợ một token RAW bung thành nhiều từ;
không cắt theo cùng chỉ số từ hai phía. Khi ghép chưa chắc, giữ cụm rộng hơn.
Các đoạn riêng được sửa/lưu từng cặp, có Hoàn tác nhập để khôi phục văn bản đầy đủ.
Lọc lỗi font không xác minh được tên/địa danh sai nhưng vẫn viết bằng Unicode hợp lệ.

- **Quyền của công cụ kế thừa danh mục KHÁCH HÀNG** (GĐ chốt 11/09/2026): xem trang + Dịch/LỌC/Xem thử
  theo ô *Xem*, Lưu nháp + Áp dụng theo ô *Tạo / sửa* của danh mục Khách hàng trong ma trận
  `UserModuleAccess`. Luật gom ở `apps/pos/customer.py::quyen` — công cụ KHÔNG đặt luật riêng
  (bản cũ đòi superuser đã bỏ).
- Đây là học mẫu được người dùng dạy rõ ràng, không tự huấn luyện từ mọi lượt quét.
- `apps/pos/cccd_learning.py` giữ một nguồn quy tắc cho engine, công cụ và CLI của
  skill. Luôn dùng engine này; không chỉ đọc bảng mã trong Markdown để bỏ qua mẫu mới.
- Cụm chữ khớp nguyên từ/cụm trong tên/địa chỉ, có chọn phạm vi; ưu tiên cụm dài.
  Thay đúng một lần trên RAW gốc; kết quả được dạy không đưa qua giải mã lại.
- Toàn QR chỉ khớp mẫu đầy đủ; số CCCD/CMND/ngày được khóa khi học. Không tự mở
  rộng một hiệu chỉnh nội dung thành quy tắc bảng mã chung.
- Người dùng đã duyệt bốn cặp: `KhaV259nC226n → Kha Vạn Cân`,
  `ñp Tríi L≥n → Ấp Trại Lớn`, `Phan Minh Ngh)a → Phan Minh Nghĩa`,
  `Ph░íng, Sαi, Ph432417ng S417n → Phương Sài, Phường Tây`.
  Cặp cuối đổi nội dung theo chỉ định: chỉ áp nguyên cụm RAW đó, không đổi mọi
  `Phương Sơn` thành `Phường Tây`. Quy tắc học có nguồn riêng, ưu tiên hơn giải mã.
- Chỉ tài khoản quản trị được ghi mẫu chung. Khi dùng nút Áp dụng để dạy, RAW và
  output hoặc khi dùng Lưu nháp, RAW được lưu có chủ đích trong file private
  (ngoài Git); lượt quét thường vẫn
  không lưu RAW. Khi chuyển máy phải chuyển cả hai file model và learned_rules.

- Điểm gợi ý là điểm quy tắc, không phải xác suất chính xác. Dữ liệu mã hóa cần
  đối chiếu trước khi áp dụng; phân tích QR không gọi UPSERT.
- `í` OEM có thể xuất phát từ `ơ` hoặc `ạ`. Giữ các khả năng khi chưa đủ bằng chứng.
- Người dùng đã xác nhận **cột D trong `mã CCCD.xlsx` là đáp án đúng**. Dùng D để
  đo khớp toàn QR, từng trường và cập nhật ngữ cảnh đã duyệt; không tự hạ D xuống
  dữ liệu tham khảo. Các nguồn khác vẫn cần xác nhận trước khi dùng làm đáp án.
- Phân biệt giải mã ký tự với hiệu chỉnh theo mẫu đã duyệt: `Thơi → Thới`,
  `Lòn → Lớn` không phải phép đổi bảng mã. Chỉ áp hiệu chỉnh mẫu khi fingerprint
  **tất cả trường RAW** khớp; không tra khách theo tên/CCCD riêng rồi ghi đè.
- Không đếm `|` để quyết định QR hợp lệ. Dùng CCCD, CMND cũ, ngày sinh, giới tính,
  ngày cấp làm mốc; giữ trường mở rộng, báo khi ranh giới không đủ bằng chứng.
- RAW quét thường chỉ ở bộ nhớ/request. Model được duyệt ở `var/private/qr/approved_context.json`
  gồm hash toàn trường RAW, đáp án và từ/cụm từ, được loại khỏi Git. Không tự học
  từ lượt quét chưa duyệt; fixture mới dùng dữ liệu giả.
- Dùng CLI gọi cùng engine, không sao chép thuật toán:
  `python .claude/skills/chuan-hoa-tieng-viet/scripts/normalize_cccd_qr.py --xlsx <file.xlsx> --summary`.
- Đo khớp D bằng `--benchmark --leave-one-out`. Báo riêng kết quả giải mã khi bỏ
  đáp án của thẻ đang thử khỏi ngữ cảnh và kết quả có đối chiếu mẫu đã duyệt.
  Không dùng tỷ lệ tra đúng mẫu có sẵn để khẳng định chính xác trên thẻ mới.
- Kiểm thử `python -m unittest tests.test_cccd tests.test_customer` và
  `node --test tests/qr.test.cjs`. Không chạy `smoke_ui` cho tác vụ chỉ phân tích:
  bộ kiểm thử đó có thể thao tác đơn hàng thật.
- Khi sửa công cụ học, chạy thêm `tests.test_qr_learning` và `tests/qr_learning.test.cjs`;
  kiểm tra cặp mới có hiệu lực trong popup đang mở ở tab khác.

## Văn bản rời và các helper hiện có

Hai bản helper văn bản CÙNG một thuật toán, phải sửa SONG SONG khi thay đổi:
- Máy chủ: `apps/pos/vn_text.py` — `chuan_hoa(s)` → `(text, canh_bao)`
- Trình duyệt: `static/js/vn_text.js` — `VNText.chuanHoa(s)` → `{text, canhBao}`

Kèm theo: `bo_dau()` / `VNText.boDau()` (bỏ dấu để so khớp), `hoa_dau_tu()` / `VNText.hoaDauTu()`
(tên IN HOA từ máy quét → `Trương Ngọc Giang`).

## Gọi ở đâu

Mọi chỗ nhận chữ Việt từ NGUỒN NGOÀI, ngay tại cửa vào:
`apps/pos/cccd.py` (quét thẻ CCCD) · ô nhập tay có thể bị dán · import Excel/CSV ·
dữ liệu cũ đọc từ PMV nếu nghi sai bảng mã. **Đừng chuẩn hóa nhiều lần** — chuỗi đã đúng
thì hàm trả nguyên vẹn, nhưng gọi lồng nhau làm cảnh báo bị nhân đôi.

## 5 kiểu hỏng và cách nhận ra

| # | Kiểu | Dấu hiệu | Ví dụ |
|---|---|---|---|
| 1 | **BYTE-SỐ** | dãy số 4 chữ số bắt đầu bằng `0`, chen giữa chữ | `Ng022501870141c` → `Ngọc` |
| 2 | **MOJIBAKE** | `Ã Â Æ » ‹ … ™ °` rải rác | `TrÆ°Æ¡ng` → `Trương` |
| 3 | **THỰC THỂ HTML** | `&#7885;` `&#x1ECD;` | `&#7885;` → `ọ` |
| 4 | **PERCENT** | `%E1%BB%8D` | → `ọ` |
| 5 | **DẤU RỜI** | nhìn đúng nhưng so khớp/`len()` sai | `e`+U+0301 → `é` (NFC) |

### Kiểu 1 giải thích kỹ (hay gặp nhất ở tiệm)

Mỗi byte UTF-8 ≥ 128 bị in ra thành **số thập phân 4 chữ số**:
`0198`=0xC6 `0176`=0xB0 → `ư` · `0225 0187 0141`=E1 BB 8D → `ọ` · `0195 0160`=C3 A0 → `à`.
Gom lại thành byte rồi `bytes.decode("utf-8")`.

## Byte bị NUỐT — quy luật đã truy được

Đường truyền đọc byte bằng **CP1252** rồi lọc bỏ ký tự ngoài ASCII:
- Byte 0x80–0x9F **CÓ** ký tự trong CP1252 → **bị nuốt** (0x83 của `ă` → `ƒ` → mất)
- Byte **KHÔNG** có (0x81 0x8D 0x8F 0x90 0x9D) → **sống sót** thành số (0x8D của `ọ`)

Đây là giả thuyết phù hợp bộ mẫu cũ, không phải quy luật chung cho mọi máy.
`sua_byte_mat()` đề xuất khi còn một khả năng trong tập giả định của helper:
lọc ứng viên theo hoa/thường của chữ liền kề, và theo vị trí (kẹp giữa 2 phụ âm ⇒ phải là
nguyên âm). Việc dựng lại byte vẫn phải cảnh báo đối chiếu. Từ 2 khả năng trở lên
thì giữ ký tự chưa rõ và đưa gợi ý, không tuyên bố đã giải mã chắc chắn.

## LUẬT

1. **KHÔNG BỊA.** Byte không phục hồi được thì giữ dấu thiếu và cảnh báo. Khi có
   nhiều ứng viên, QR trả gợi ý xếp hạng cùng phương án khác và yêu cầu đối chiếu;
   không tuyên bố gợi ý có xác suất chính xác tuyệt đối.
2. **Luôn hiện `canh_bao` cho người dùng** (popup khách hàng in dưới ô quét màu cam).
3. **Chấm điểm mojibake phải TRỪ ký tự rác.** Bản hỏng cũng chứa sẵn `á â`, nếu chỉ cộng
   điểm chữ có dấu thì hai bản hòa nhau và không sửa gì — lỗi này đã dính một lần.
4. Máy quét kiểu bàn phím có thể hỏng ngay khi quét vào web. Code page, chế độ xuất
   và phần mềm nhận đều có thể ảnh hưởng; không khẳng định clipboard là nguyên nhân duy nhất.
5. Sửa `vn_text.py` thì phải sửa `vn_text.js` y hệt, rồi chạy lại bộ thử.

## Bộ thử (chạy trước khi commit)

`manage.py smoke_vn_text` — 14 kịch bản, gồm chuỗi thật của GĐ:

```
096088009068|381472415|Tr0198017601980161ng Ng022501870141c Giang|07041988|Nam|
Kh01950179m 4, TT. N0196m C0196n, N0196m C0196n, C01950160 Mau|15092023
   ↓
096088009068|381472415|Trương Ngọc Giang|07041988|Nam|
Khóm 4, TT. Năm Căn, Năm Căn, Cà Mau|15092023
```

Bắt buộc có trong bộ thử: chuỗi **vốn đã đúng** phải trả về nguyên vẹn, không cảnh báo.

## Định dạng mã QR thẻ CCCD gắn chip

Lược đồ nghiệp vụ có 7 trường (dạng thông thường dùng 6 dấu `|`):
`số CCCD | số CMND cũ | họ tên | ngày sinh ddmmyyyy | giới tính | địa chỉ | ngày cấp ddmmyyyy`
Máy có thể thiếu/lặp dấu phân cách, ký tự hỏng có thể chứa pipe và QR có thể có
trường mở rộng. `split_raw()` phân tích theo mốc, không cố định số pipe.
Đọc bằng `apps/pos/cccd.py::parse()` hoặc `normalize_cccd_group()`. Quy ước dự án: ô `CMND` của
`I_CUSTOMER` lưu **số CCCD 12 số** (GĐ chốt 03/09/2026).
