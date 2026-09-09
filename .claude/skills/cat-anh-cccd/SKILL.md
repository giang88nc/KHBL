---
name: cat-anh-cccd
description: >-
  Thuật toán TỰ PHÁT HIỆN + CẮT + NẮN PHỐI CẢNH thẻ CCCD/CMND (khổ ID-1 85,6×53,98 mm) trong ảnh chụp điện thoại,
  ảnh chụp màn hình VNeID hay ảnh trên khay cân — module dùng chung `apps/pos/anh_cccd.py` (OpenCV), đầu ra JPEG chuẩn
  1170×738, xoay tự do theo góc người dùng kéo. Dùng skill này BẤT CỨ KHI NÀO đụng đến: cắt/tách/crop ảnh CCCD, ảnh
  hồ sơ KHÁCH HÀNG hay NHÂN VIÊN có mặt trước/mặt sau thẻ, nút ✂ trên phiếu thâu, popup kéo xoay ảnh thẻ, chất lượng
  cắt (lẹm viền, xiên, không thấy thẻ), hay muốn thêm nút "tách thẻ" ở màn hình mới — kể cả khi người dùng không gọi
  đúng các thuật ngữ trên.
---

# CẮT ẢNH CCCD — thuật toán dùng chung (KHBL)

## Nguồn sự thật

| Thứ | Nơi | Vai trò |
|---|---|---|
| 1 | `apps/pos/anh_cccd.py` + `apps/pos/cccd_mau/*.npz` | **Thuật toán thuần OpenCV (SIFT khớp mẫu → dự phòng hình học), không đụng DB, không biết khách/nhân viên là ai.** Mọi màn hình gọi chung |
| 2 | `apps/pos/views_thau.py` → `thau_anh_cat` / `thau_anh_cat_luu` + `templates/pos/_thau_cat_modal.html` | Mẫu tích hợp đã chạy thật: popup xem trước + KÉO XOAY bằng con trỏ + ✓ LƯU |
| 3 | `manage.py smoke_thau` mục 8b | Hồi quy web (xem trước 1170×738, khung kéo xoay, LƯU góc 0/90/187,5, ảnh trơn báo không thấy) |
| 4 | Skill này | Vì sao lại thế, cách tái dùng cho KHÁCH / NHÂN VIÊN, bẫy đã dính |

## API — chỉ cần 2 hàm

```python
from apps.pos import anh_cccd as AC

jpeg = AC.cat_cccd(data_bytes, goc=0.0, cat=None)   # bytes ảnh bất kỳ → bytes JPEG 1170×738 (q92)
#   cat = (trên, phải, dưới, trái) phần cạnh CẮT BỚT (âm = nới, kẹp −0,3…0,45) người dùng kéo tay cầm — dịch 4 cạnh tứ giác
#   TRƯỚC khi nắn phối cảnh (_cat_bot) nên ảnh vẫn đủ khổ, không méo tỉ lệ
quad = AC.tim_the(img_bgr)               # ndarray BGR → 4 điểm float32 (tl, tr, br, bl) tọa độ ảnh gốc, hoặc None
```

- `goc` = độ xoay THÊM theo chiều kim đồng hồ (đúng quy ước CSS `rotate()` nên gửi thẳng số từ JS): bội 90° xoay không
  mất nét (90/270 → ảnh DỌC 738×1170), góc lẻ → xoay quanh tâm rồi cắt hình chữ nhật nội tiếp cùng tỉ lệ. Nhận cả
  chuỗi phẩy VN ("187,5") nếu qua `views_thau._goc_xoay`.
- Không thấy thẻ → raise `AC.KhongThayThe(msg)` (msg tiếng Việt, hiện thẳng cho người dùng). Lỗi khác cứ để nổ, view bắt.
- Thẻ DỌC trong ảnh tự đưa về ngang. Ảnh **đã là thẻ cắt sát** (tỉ lệ ảnh 1,50–1,70, tứ giác thắng ≥80 % khung) → giữ
  TRỌN KHUNG, chỉ resize; ảnh trơn (<1 % điểm biên) → "không thấy".
- Hằng: `CHUAN_W, CHUAN_H = 1170, 738` (đúng cỡ file mẫu `CCCD-cut.jpg` GĐ đưa 08/09/2026, tỉ lệ ID-1 1,586);
  `NGUONG_PHU = 0.40` (độ phủ biên tối thiểu của ứng viên thắng); `DIEM_TRON_KHUNG = 0.12`.
- Tốc độ 1–12 s/ảnh (ảnh thu về ≤1200 px cạnh dài rồi mới tìm; ảnh màn hình nhiều chữ chậm nhất) → gọi trong request
  được nhưng ĐỪNG gọi hàng loạt trong vòng lặp web; batch thì chạy lệnh quản trị.

## Tái dùng cho KHÁCH HÀNG / NHÂN VIÊN — công thức tích hợp

1. **Nguồn ảnh**: hàm `_anh_slot_data`-kiểu (lấy bytes ảnh đang có của ô: đã lưu / tạm / trong hồ sơ). Với khách trên
   PMV: `customer.saved_image(cust_id, "mat-truoc"|"mat-sau", client)`. Với nhân viên (module sau): đọc từ cột ảnh của
   bảng nhân viên — thuật toán không quan tâm nguồn.
2. **View xem trước** (GET, `@require_GET` đặt NGAY TRÊN hàm view): `kq = AC.cat_cccd(data, 0.0)` → trả popup extend
   `partials/modal_shell.html` với 2 ảnh base64 (`_nen_anh(..., canh=900)` cho nhẹ) + khung kéo xoay. Copy nguyên khối
   JS trong `_thau_cat_modal.html` (pointer events, hít 0/90/180/270 trong ±6°, nhãn độ, nút ↺/↻ 90° + Đặt lại, hidden
   `goc`; 4 tay cầm `.th-cat__tay` cắt bớt từng cạnh → hidden `cat_t/r/b/l`, kéo quy về hệ tọa độ ảnh bằng quay −goc);
   nút ✓ LƯU dùng `hx-include="#th-cat-form"` + `hx-swap="none"`.
3. **View LƯU** (POST): `AC.cat_cccd(data, _goc_xoay(request.POST.get("goc")), _phan_cat(request))` → nén `_nen_anh(kq,
   canh=AC.CHUAN_W, chat_luong=88)` → **chỉ ghi ẢNH CHỜ** (`_luu_anh_slot` → `thau_anh_tam`, GĐ chốt 09/09: mọi thay đổi ảnh đi qua
   THANH TOÁN) → trả OOB làm mới ô ảnh + `dong_modal`. Ghi thật ở THANH TOÁN (`_chot_anh`): CCCD → `customer.cap_nhat_anh`
   trong `SAVE_LOCK` (I_CUSTOMER_Upd, ghi đè) + file `media/cccd/<CustID>/MT|MS_<lúc>.jpg` trên máy chủ; nhân viên (module sau):
   cột tương ứng + file cùng kiểu. CCCD đòi ĐÃ CHỌN khách — chưa chọn thì ô khóa 'chọn khách hàng'.
4. **Nút ✂ trên ô ảnh**: chỉ bật khi ô có ảnh và phiếu/hồ sơ không khóa; thêm `hx-disabled-elt="this"` +
   `hx-on::before-request="khblCatLoading(label)"` (hàm + template `#th-cat-loading` ở trang chứa — popup LOADING hiện
   NGAY vì server mất 1–12 s, chống bấm nhiều lần); **phải khai tường minh
   `hx-target="#modal-root" hx-swap="innerHTML"`** — htmx KẾ THỪA `hx-swap="none"` từ form cha nên nút từng bấm không
   thấy gì.
5. Thêm 3–4 kịch bản smoke như mục 8b `smoke_thau` (ảnh giả `AC.anh_thu_nghiem(goc=12)`, so độ sáng với
   `AC._the_gia()`; LƯU goc 90 → (738, 1170); ảnh xám trơn → `KhongThayThe`).

## Đường CHÍNH v3 — so khớp SIFT với mẫu thẻ (09/09/2026)

`_tim_the_sift(img)`: ảnh xám ≤2000 px + CLAHE → SIFT (6000 điểm) → với mỗi mẫu trong `apps/pos/cccd_mau/*.npz`
(`kp` N×2 trong khung 1170×738, `des` N×128 float16, `kich`) BFMatcher knn ratio 0,75 → `findHomography` RANSAC 5 px →
≥ `SIFT_INLIER_MIN` 15 inlier → chiếu 4 góc khung mẫu = 4 góc thẻ theo THỨ TỰ tl,tr,br,bl của thẻ (biết chiều, không cần
đoán ngược đầu) → kiểm lồi / tỉ lệ 1,1–2,4 / ≥0,5 % ảnh / không lật gương → lấy mẫu nhiều inlier nhất. Sau đó nắn cạnh
(`_tinh_chinh` + `_sat_mep` trong ảnh nhỏ) và `_khop_thu_tu` giữ chiều. `tim_the(chi_tiet=True)` trả tên `sift:<mẫu>`;
`cat_cccd` thấy tên đó thì `_noi_bien(giu_thu_tu=True)` và KHÔNG sắp lại góc theo hình học.

**Mẫu**: dựng bằng script scratchpad `build_mau.py` từ ảnh cắt chuẩn (1170×738): chỉ giữ SIFT ở vùng in CỐ ĐỊNH — mặt
trước: dải đầu y 0–40 % trừ ô QR; VNeID: y 0–42 % trừ ô ảnh; mặt sau: cột trái x 0–55 % y 0–72 % (nhãn, chức danh, chip,
mộc) trừ dòng đặc điểm nhân dạng + chữ ký, thêm nhãn "Ngón trỏ" — che ảnh/số/tên/địa chỉ/QR/vân tay/MRZ. **Chỉ lưu
descriptor, không lưu ảnh** (dữ liệu cá nhân). Thêm mẫu mới (thẻ mẫu 2024 "CĂN CƯỚC", CMND cũ) = thêm 1 file .npz cùng
định dạng, không sửa code. Không khớp mẫu nào → rơi về đường hình học bên dưới (kể cả thẻ giả của smoke).

Thực đo 09/09: 4/4 ảnh tại quầy GĐ đưa (thẻ trong tay + bao nhựa 140 inlier, mặt sau 65, thẻ trên màn hình điện thoại
giữa tủ vàng 78–101, thẻ nhỏ ~5 % khung) đúng trong 0,25–0,5 s; hình học + màu (v2) từng cắt sai cả 4 — trong tiệm vàng
mọi tiêu chí màu/cạnh đều bị vàng, đỏ nhung, cạnh tủ làm nhiễu.

## Đường DỰ PHÒNG — hình học (đọc khi cần sửa chất lượng cắt khi không khớp mẫu)

Trước khi vào pipeline dưới: `_vung_mau` tìm khối màu lam/lục (hue 60–108) → cắt ROI nới 35 % từ ảnh gốc, phóng ~1000 px
và chạy lại toàn bộ trong ROI (ứng viên ×1,05; thẻ nhỏ giữa khung được xem ở độ phân giải đủ). Cặp đường Hough chỉ ghép
khi tỉ lệ 2 bề rộng 1,15–2,3 (từng 60 s/ảnh).


Ảnh thu ≤1200 px → `_ngu_canh()` tính 1 lần: biên (Canny xám 30/90 ∪ Canny ΔE-so-nền 16/48), hướng gradient (kênh
mạnh hơn tại từng điểm), điểm XANH thẻ (hue 60–130, S>24), điểm giống nền (ΔE<10 so màu trung vị dải mép ảnh), Lab.

**A. Ứng viên tứ giác** từ 2 nguồn:
- Khối: 10 mặt nạ (ΔE 8/16/Otsu · đổ tràn nền từ 8 điểm mép · bão hòa 28/Otsu · Otsu sáng/tối · Canny · thích nghi)
  → contour ngoài → `_khop_canh` fit từng cạnh minAreaRect bằng fitLine Huber → tứ giác.
- Đường Hough (`_ung_vien_duong`): HoughLinesP (≥0,15·cạnh ngắn) → histogram góc GẬP 90° lấy tối đa **3 hướng chính**
  (thẻ trên khay/giấy nghiêng khác hướng vẫn bắt) → mỗi hướng 2 nhóm vuông góc, mỗi nhóm gộp đoạn thành đường rồi ghép
  2+2 đường ("hough" ×0,97) hoặc **3 đường + suy cạnh thứ 4 theo tỉ lệ ID-1** ("hough3" ×0,96 — cứu viền nhạt thẻ
  VNeID trên nền trắng); thẻ nằm thẳng thì thêm 4 MÉP ẢNH làm đường (thẻ cắt sát mép).

**B. Chấm điểm** `_diem` (0 = loại): tỉ lệ cạnh 1,2–2,2 (chụp chếch co chiều cao tới ~2,0), diện tích ≥4 %;
`phu` = độ phủ biên CÓ HƯỚNG (điểm biên trong ±2 px dọc pháp tuyến có gradient vuông góc cạnh ±30°; biên lệch hướng chỉ
0,1; mép ảnh 0,5, tối đa 2 cạnh — 3–4 cạnh trên mép = trọn khung chỉ nhận khi CHÍNH ẢNH tỉ lệ 1,5–1,7) lấy
0,6×trung bình + 0,4×**cạnh yếu THỨ NHÌ** (cho phép đúng 1 cạnh nhạt) × độ đầy × gần 1,586 × diện tích^0,1 ×
tương phản Lab 2 bên cạnh (0,3+0,7·tp) × **viền xanh** (dải sát trong cạnh màu thẻ, sát ngoài không — loại tứ giác con
trong thẻ lẫn tứ giác ôm khay; pháp tuyến ra ngoài = `(dy, −dx)`) × màu xanh trong × (1−0,4·giống nền). Cắt tỉa: phần
rẻ không vượt ứng viên tốt nhất → bỏ qua fillPoly đắt (`ctx["best"]`).

**C. Hậu kỳ** cho ứng viên thắng: `_tinh_chinh` nắn 4 cạnh theo điểm biên thật (dải ±2,5 % cạnh ngắn, fitLine Huber,
góc dịch >5 % → giữ cũ) → `_sat_mep` đẩy cạnh sát mép cùng màu ra mép → `_noi_bien` nới 1,2 % (thà dính nền còn hơn
lẹm viền) → warpPerspective 1170×738 (INTER_AREA khi thu, CUBIC khi phóng) → xoay theo `goc`.

## Bộ đo độ chính xác (không có trong repo — dựng lại khi cần)

Script scratchpad `thu_cat.py` (đã xóa vì chứa ảnh thật) chạy 17 ảnh: `AC.anh_man_hinh(the_bytes)` (giả lập màn hình
VNeID 1170×2532 với thẻ thật dán bo góc + chữ), `AC.anh_nghieng(goc, the_bytes, phoi_canh, nen_go)` (thẻ xoay + phối cảnh
trên vân gỗ có vật tạp), 2 file scan giấy (ca ÂM — vẫn bị nhận là thẻ, chấp nhận: người dùng bấm Đóng), 3 ảnh CCCD thật
trên KK (`customer.saved_image`, CHỈ ĐỌC) — đo IoU bằng `AC.iou_quad`. Mốc 08/09/2026: VNeID 0,99 · nghiêng 0/7/−18/33/90°
≥0,994 · 3/3 KK đúng (thẻ trên khay cân có vòng tròn + tờ giấy, thẻ trong bao nhựa, ảnh cắt sát → trọn khung).
**Ảnh CCCD thật chỉ được nằm trong scratchpad lúc đo và PHẢI XÓA ngay sau** (dữ liệu cá nhân khách).

## Bẫy đã dính (đừng lặp lại)

1. **ρ trái dấu trong nhóm Hough**: đoạn góc 0,x° và 179,x° cho pháp tuyến ngược chiều → cùng cạnh bị tách đôi, rớt top,
   khoảng cách cặp đường sai → ảnh chụp màn hình VNeID thật báo "không thấy thẻ" (GĐ bắt 08/09). Mỗi nhóm có φ0 chuẩn,
   đoạn ngược thì lật (−ρ, φ−π); đường mép ảnh cũng biểu diễn theo φ0.
2. **Gộp đoạn chỉ theo ρ** → cung hoa văn lệch 5–10° trộn vào cạnh thẻ → ảnh cắt XIÊN. Phải thêm Δφ ≤ 3° và GIỮ (ρ, φ)
   của đoạn dài nhất (không trung bình — viền panel cách 6 px kéo cạnh lệch). Siết Δρ 1,2 % → 0,5 % thì mất cạnh thẻ ở
   ảnh khay cân (đoạn lệch góc lượng tử 0,5°) → giữ 1,2 %.
3. **Viền thẻ là đường mảnh 2 px**: gradient ngay tâm đường ≈ 0, hướng nhiễu → cạnh trái ảnh VNeID chỉ được 0,47 → xét
   điểm biên trong ±2 px dọc pháp tuyến.
4. Xếp hạng đường theo TỔNG độ dài thì dòng chữ (nhiều đoạn ngắn) thắng cạnh thẻ (1 đoạn dài) → xếp theo đoạn dài nhất
   + 0,25×tổng.
5. Mép ảnh tính 0,5 cho MỌI cạnh → tứ giác 3 mép + 1 đường khay thắng thẻ thật. Tối đa 2 cạnh mép.
6. Ngưỡng chấp nhận đặt trên TÍCH các hệ số mềm → điểm tuyệt đối trôi mỗi lần thêm hệ số, lúc thì bỏ sót thẻ thật
   → ngưỡng chỉ đặt trên BẰNG CHỨNG HÌNH HỌC (`NGUONG_PHU` trên độ phủ của ứng viên thắng).
7. Ảnh khay cân: khay xám ánh lam cũng "xanh" nếu S>18 → S>24; tỉ lệ thẻ chụp chếch tới 1,96 → cửa sổ 1,2–2,2.
8. Thẻ giả cho smoke (`_the_gia`) có ảnh chân dung tối → chỉ ~67 % điểm sáng; smoke so với chính thẻ giả (≥90 %) chứ
   không dùng ngưỡng cứng 70 %.
9. Chèn helper vào giữa decorator `@require_GET` và `def thau_anh_cat` → helper bị bọc → 500 `'str' has no attribute
   'method'`. Decorator phải đứng ngay trên view.
10. Thư viện: `opencv-python-headless==5.0.0.93` + `numpy>=2.0` trong `requirements.txt` — chỉ server cần, PC LAN không (SIFT có sẵn trong bản chính, không cần contrib).
11. 📷 CHỤP không tải lên dù preview hiện: `khbl.js` đóng camera (`target = null`) trước khi bắn `change` → phải giữ tham chiếu
    input trước khi `closeCamera()`. Đường CHỌN tệp không dính (change gốc). Smoke server không bắt được — thử JS bằng
    `getUserMedia` giả (canvas.captureStream) trên trang render qua test client.
12. Hệ số 'bố cục màu' (quốc huy đỏ / chip vàng đúng chỗ, v2.16–2.17) tưởng hay nhưng trong tiệm vàng đỏ nhung + vàng trang sức khắp nơi → điểm thẻ thật 0,4–0,7 lẫn với mảnh sai 0,3–0,5 → đã bỏ, dùng SIFT.
