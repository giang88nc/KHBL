---
name: chuan-hoa-tieng-viet
description: Chuẩn hóa chữ TIẾNG VIỆT bị hỏng mã về đúng dấu — máy quét CCCD trả chuỗi lạ, dán từ Excel/clipboard ra ký tự rác, mojibake kiểu 'TrÆ°Æ¡ng', dãy số 0225 0187 0141 thay cho chữ, thực thể HTML &#7885;, chuỗi %E1%BB%8D, hoặc dữ liệu cũ trong CSDL bị sai bảng mã. BẮT BUỘC đọc trước khi viết bất kỳ code nào nhận chữ tiếng Việt từ nguồn ngoài (quét mã, nhập liệu, import, đọc dữ liệu vendor) hoặc khi thấy tên/địa chỉ khách hiện sai dấu.
---

# CHUẨN HÓA CHỮ TIẾNG VIỆT BỊ HỎNG MÃ

Hai bản CÙNG một thuật toán, phải sửa SONG SONG khi thay đổi:
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

Khớp 100% mẫu thật. `sua_byte_mat()` điền lại **KHI VÀ CHỈ KHI còn đúng MỘT khả năng**:
lọc ứng viên theo hoa/thường của chữ liền kề, và theo vị trí (kẹp giữa 2 phụ âm ⇒ phải là
nguyên âm). `N?m` → chỉ còn `ă` → `Năm`. Từ 2 khả năng trở lên thì **giữ `?`**.

## LUẬT

1. **KHÔNG BỊA.** Còn từ 2 khả năng thì để `?` và báo qua `canh_bao` — tên/địa chỉ khách
   sai còn tệ hơn thiếu dấu.
2. **Luôn hiện `canh_bao` cho người dùng** (popup khách hàng in dưới ô quét màu cam).
3. **Chấm điểm mojibake phải TRỪ ký tự rác.** Bản hỏng cũng chứa sẵn `á â`, nếu chỉ cộng
   điểm chữ có dấu thì hai bản hòa nhau và không sửa gì — lỗi này đã dính một lần.
4. **Gốc rễ**: quét THẲNG vào ô nhập của web thì không hỏng gì cả (trình duyệt nhận
   Unicode đúng). Hỏng là do đi vòng qua clipboard/Excel. Nhắc người dùng quét thẳng.
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

7 trường ngăn bằng `|`:
`số CCCD | số CMND cũ | họ tên | ngày sinh ddmmyyyy | giới tính | địa chỉ | ngày cấp ddmmyyyy`
Đọc bằng `apps/pos/cccd.py::parse()` (đã tự gọi `chuan_hoa`). Quy ước dự án: ô `CMND` của
`I_CUSTOMER` lưu **số CCCD 12 số** (GĐ chốt 03/09/2026).
