---
name: nhan-dien-ma-chung-tu-ck
description: Nhận diện MÃ CHỨNG TỪ trong nội dung CHUYỂN KHOẢN ngân hàng (bảng bank_notifications) TRONG NGÀY, tách chiều IN/OUT — khoản CK vào là của HĐ BÁN HÀNG / CỌC (12 số), CẦM ĐỒ (14 số) hay QR độc lập (KHBL+10 số); khoản CK ra là THÂU VÀNG / ĐỔI DƯ ("THANH TOAN TIEN VANG" + 4 số STT) hay CHI CẦM ĐỒ ("…VANG" + 6 số log phiên KHCD), và thuộc phiếu nào trong sổ money_flow. BẮT BUỘC đọc trước khi viết code đọc description của bank_notifications, dò CK "không rõ nguồn", dựng bảng CK trong ngày, sinh nội dung chuyển khoản / mã QR, hoặc sửa regex bóc mã (money_in · thau_cart · thau_payments · ck_tra_khach · mobile_qr · check_bill).
---

# NHẬN DIỆN MÃ CHỨNG TỪ TRONG NỘI DUNG CHUYỂN KHOẢN

Nguồn sự thật DUY NHẤT: **`apps/pos/ma_chung_tu_ck.py`** — đặc tả GĐ chốt **22/09/2026**. Không viết regex bóc mã
ở chỗ khác; cần luật mới thì thêm VÀO module + ca kiểm vào `tests/test_ma_chung_tu_ck.py`.

## Nguyên tắc

1. **CHỈ DÙNG TRONG NGÀY** — `tra_so(ma, ngay)` luôn kèm ngày. STT 4 số chỉ duy nhất trong ngày.
2. **Tách chiều** theo `bank_notifications.direction`: `nhan_dien(mo_ta, 'in' | 'out')`.
3. **Chỉ nhận dạng MỚI** — dạng cũ KHÔNG nhận diện nữa (ra "Không rõ nguồn"): QR `BH/CD/DC/KH`+12 ký tự,
   `KH2Q…`, TrnID `TDC…`, `yymmddHHmm`+`CD/CT/KC`, `…TIEN VANG 1-…`, và KHÔNG đọc cột `bill_code_raw`.
4. Chỉ trả lời "mã gì, loại gì, phiếu nào". Luật ĐỐI SOÁT TIỀN (tài khoản, cửa sổ giờ, số tiền, `is_check`,
   chống dùng 2 lần) vẫn nằm ở từng luồng đối soát.

## Định dạng

| Chiều | Loại (`Ma.loai`) | Mẫu | Ví dụ | Ai sinh ra |
|---|---|---|---|---|
| IN | `HD` — mua bán + cọc | **12 số**: yymmdd hợp lệ + STT 6 số (nhận cả dạng gạch) | `260921000232` | QR IN = số HĐ bỏ gạch (`money_flow_payment.transfer_content`, `ck_tra_khach.noi_dung_in`) |
| IN | `CD` (cầm đồ) | **14 số**: YYMM (tháng 1–12) + phiếu 5 số + phiên 5 số | `26090178206824` | `payment_reference.pawn_reference` = `cd_payments.payment_ref` |
| IN | `QR` — QR độc lập | **`KHBL` + 10 số** (giây epoch) | `KHBL1790054215` | `mobile_qr.new_token/create` |
| OUT | `TV` — thâu / đổi dư | `THANH TOAN TIEN VANG` + **4 số cuối số HĐ ĐẦU** → mã = yymmdd + 4 số đệm 0 | `…VANG 1014-…` → `260921001014` | `thau_cart.noi_dung_ck` (thâu + đổi dư qua `ck_tra_khach.noi_dung`) |
| OUT | `CD` — chi cầm đồ | `THANH TOAN TIEN VANG` + **6 số** (KHCD `live_loans.outgoing_reference` = log id đệm 6 số) → mã = yymmdd + 6 số | `…VANG 101234-…` → `260922101234` · `…VANG 006830` → `260922006830` | nhóm `thau_nhom` nghiep_vu=`camdo` — đối soát ở Thâu vào 2 (22/09/2026) |

- OUT tách thâu/đổi dư (4 số) với cầm đồ (`1`+5 = 6 số) bằng **độ dài** + ràng buộc không có chữ số liền sau.
- `HD` 12 số dùng CHUNG cho HĐ bán và phiếu cọc → `tra_so` + `loai_that` phân theo `MoneyFlow.service`
  (RETAIL → Bán hàng, DEPOSIT → Cọc). `STT` → GOLD_BUY = Thâu vàng, RETAIL chiều OUT = Đổi dư.
- 12/14 số được lọc bằng `money_in.codes`: loại mã giao dịch ngân hàng (`FT26264117829075`, `MBVCB.1615…`) vì
  ngày/tháng không hợp lệ. `KHBL`+10 số KHÔNG trùng dạng 12/14 số nên không bị nhận nhầm là HĐ.
- ≥2 mã trong 1 nội dung = **mơ hồ** → `ma_duy_nhat` trả None, KHÔNG tự gán (cùng nguyên tắc `money_in`).

## Cách gọi

```python
from apps.pos import ma_chung_tu_ck as MC

ds  = MC.nhan_dien(description, MC.IN)            # hoặc MC.OUT — THUẦN, không DB → list[Ma(loai, ma, quy_tac)]
ma  = MC.ma_duy_nhat(description, MC.OUT)         # đúng 1 mã → Ma ; 0 / ≥2 → None
fs  = MC.tra_so(ma, ngay)                         # money_flow TRONG NGÀY (MySQL) → list[MoneyFlow]
qr  = MC.tra_qr(ma, ngay)                         # QR độc lập → MoneyFlowPayment hoặc None
ten = MC.loai_that(ma, fs)                        # 'Bán hàng' · 'Cọc' · 'Thâu vàng' · 'Đổi dư' …
```

## Kết quả đã GHI SẴN trên bank_notifications (cột riêng KHBL — 22/09/2026)

Các bảng khác đối soát / hiển thị nên **đọc 3 cột này** thay vì tự bóc lại nội dung:

| Cột | Ý nghĩa |
|---|---|
| `ma_chung_tu` | IN: 12 số / 14 số / `KHBL…` · OUT: **yymmdd (ngày giao dịch) + 6 số sau "TIEN VANG"** — không cần tra sổ |
| `loai_chung_tu` | `HD` (bán/cọc) · `CD` (cầm đồ: IN thu / OUT chi — phân bằng `direction`) · `TV` (thâu/đổi dư) · `QR` · `NHIEU` · `''` |
| `nhan_dien_luc` | mốc đã nhận diện (NULL = chưa xử lý) |

- **Ghi NGAY lúc UPSERT — WEBHOOK SEPAY V2** (GĐ chốt 22/09/2026): `apps/pos/sepay_v2.py` — NỘI BỘ `POST https://127.0.0.1:8100/banle/webhook/sepay2` (qua Caddy KHBL; ngrok `/webhook/sepay2` để giai đoạn sau) nhận
  NGUYÊN VĂN JSON SePay → UPSERT `khj_bl.bank_notifications` (chống trùng theo `provider='sepay'` + `ref_code` = id sự kiện
  SePay) → `gan_ma(ids=[id])` trong CÙNG transaction. Giai đoạn 1: V1 (BANLE_V5 `/sepay/webhook`) chuyển tiếp nguyên văn
  (`chuyen_tiep_v2`, header `X-KHBL-Token`, `.env` BANLE_V5 `KHBL_SEPAY_V2_URL`; kết nối phải từ 127.0.0.1 — Caddy cùng máy; V2 lỗi → V1 trả 503 để SePay gửi lại). Giai đoạn 2: SePay gọi thẳng
  V2 qua ngrok→Caddy với `Authorization: Apikey <SEPAY_API_KEY>` (.env KHBL), tắt V1.
  V1 đã TẮT bước 7 (ghi KK CashPay/CardPay + bank_qr_log) và bước 8 (chép sang khj_bl) — V2 là nơi UPSERT duy nhất của khj_bl.
  V2 KHÔNG ghi KK: ghi tiền lên KK + MySQL là việc đối soát qua nội dung CK, xác nhận trên bản Mobile.
- Lưới an toàn: job scheduler KHBL **15 giây** (`_job_gan_ma_ck`) xử lý dòng nào còn `nhan_dien_luc IS NULL`.
- Lệnh tay `manage.py gan_ma_ck` (`--tao-cot` · mặc định THỬ/rollback · `--ghi [--tu] [--het]`). Mỗi dòng xử lý MỘT lần.
- **Không ghi `bill_code_raw`**: BANLE_V5 `bank_notification_mirror` chép ĐÈ mọi cột nó biết mỗi lần webhook cập nhật; 3 cột
  mới nó không biết nên không bị đè. `bill_code_raw` giữ nghĩa cũ cho luồng cũ.
- Chạy bù 22/09/2026: 29.172 dòng / 6 giây → HD 20.979 · CD 10 · TV 222 · không mã 7.961 · NHIEU 0.

## Lọc nhiễu trước khi đọc 12/14 số (`bo_so_dinh_chu` + năm hợp lệ)

- Bỏ dãy số DÍNH LIỀN SAU CHỮ CÁI — mã giao dịch ngân hàng/ví: `FT…`(14 số, 2.962 lần) · `MOMO…` · `ZP…` · `TRC/TDC…`.
  ⚠ `money_in.codes` KHÔNG chặn: `FT` + năm + NGÀY THỨ trong năm (vd `FT26093…`, ngày 093) có "tháng" hợp lệ → bị nhận là
  mã cầm đồ 14 số suốt khoảng tháng 1–5; `ZP260930…` thành HĐ. Tiền tố khách gõ `CK`/`HD` được tách ra giữ lại.
- Năm của mã phải là **năm nay hoặc năm trước** (mã tham chiếu `130125245197…` = "25/01/2013" bị loại).

## Sinh nội dung ↔ đối soát phải CÙNG khuôn (sửa một bên phải sửa bên kia)

- Thâu / đổi dư / cầm đồ: sinh ở `thau_cart.noi_dung_ck(g, ma)` (ma = số HĐ; mặc định `g['bill_codes'][0]`),
  `ck_tra_khach.noi_dung(g)` (dùng `g['bill_code']`), KHCD `live_loans.outgoing_reference` (6 số).
  **ĐỐI SOÁT (GĐ chốt 22/09/2026) = `thau_payments.ma_phieu(order)` == `bank_notifications.ma_chung_tu`**:
  `ma_phieu` chạy CHÍNH skill này trên `thau_nhom.ck_nd` với NGÀY PHIẾU (TV cho thâu/đổi, CD cho cầm đồ; ck_nd
  không mang mã → dựng lại từ số HĐ đầu), `ma_bank` đọc cột `ma_chung_tu`+`loai_chung_tu` (dòng chưa nhận diện →
  tách tại chỗ). So CẢ loại lẫn 12 số: trùng đủ = `'ma'` → được TỰ xác nhận; chỉ trùng đuôi (CK khác ngày phiếu)
  = `'duoi'` → ứng viên chỉ xác nhận TAY. Cầm đồ chỉ xét TK `gold_bank` type `pawn` (127606), thâu/đổi TK 666141168.
  `thau_list.nd_dung_chuan(ck_nd, bill_codes, nghiep_vu)` kiểm ck_nd đúng khuôn (so mã trần).
  **GĐ đảo quyết định 10/09 (TrnID) → 22/09 (số HĐ), cắt ngay** — nội dung đã tạo trước theo TrnID sẽ không tự khớp.
- QR độc lập: sinh ở `mobile_qr.new_token` (đuôi = giây, giữ chỗ trong token; JS `mobile_standalone_qr.js` hiện
  `KHBL`+đuôi); đối soát `mobile_qr.reconcile` nhận `QR_MOI` + `QR_DOC_LAP` (dạng cũ chỉ để QR đã phát trước
  22/09 còn khớp trong cửa sổ 3 giờ).

## Bẫy đã gặp

- **STT không trùng trong ngày** vì bán · thâu · cọc dùng CHUNG một dãy số HĐ/ngày (thực đo 21/09: 212 HĐ, 0 trùng).
  Sang ngày khác thì 4 số lặp lại → luôn tra kèm ngày.
- `bank_reconcile.CD_PATTERN = '%THANH TOAN TIEN VANG 1%'` (luồng cũ, chưa sửa) ghi `bill_code_raw='chi CĐ'` cho
  mọi nội dung "VANG 1…" — kể cả thâu có STT ≥ 1000 (`VANG 1234`). Module này không đọc `bill_code_raw` nên không
  bị ảnh hưởng; nếu luồng thâu cũ loại nhầm khoản thì nghi chỗ này.
- Cột `bank_notifications.transaction_time` là **CHUỖI** 'YYYY-MM-DD HH:MM:SS' — đừng `.strftime` thẳng.
- Cột `bill_code_raw` KHÔNG đồng nhất (mã HĐ SePay/ACB bóc · TrnID do save_match ghi · chữ `chi CĐ`), ACB chỉ bóc mã
  có ngày HÔM NAY → vì vậy đặc tả mới chỉ đọc `description`.
- `mobile_receipts.amounts_by_flow` dò `is_check=1` theo TrnID → chỉ khớp phiếu cọc; HĐ bán chỉ "đã nhận" khi có
  biên nhận `money_flow_bank_receipt`.

## Nơi đang dùng

- `check_bill.ck_trong_ngay` (CHECK BILL · Dò phiếu · bảng CK VÀO): khoản chưa có chủ → `nhan_dien(…, IN)` →
  phiếu trong ngày / QR độc lập / "… · không có phiếu trong ngày" / "Nhiều mã" / "Không rõ nguồn".
- Chiều OUT: `thau_payments.code_match` (Thâu vào 2 — thâu · đổi dư · cầm đồ). Trang tự đối soát 15 giây/lần khi
  d2 = hôm nay (`thau2_payments.js`, lượt `automatic=1` không cần đăng nhập, máy chủ chặn ≤1 lượt/10 giây) + job 5 phút.

## Kiểm

`manage.py test tests.test_ma_chung_tu_ck tests.test_thau_payments tests.test_ck_tra_khach tests.test_mobile_standalone_qr
--settings=config.settings.test_price_save` (KHÔNG chạy unittest/discover — xóa DB thật).

## Bẫy one-shot + VỚT LẠI (29/09/2026)
- `gan_ma` chỉ xử lý dòng `nhan_dien_luc IS NULL` — MỘT LẦN. Webhook SePay V2 có khi chèn dòng lúc chưa có
  `transferType` → `direction='unknown'` → `nhan_dien` trả rỗng → dòng bị đóng dấu KHÔNG mã vĩnh viễn, dù sau đó
  chiều tiền được cập nhật `in`/`out` (ca thật #30351 HĐ 260929000162 · #30352 CĐ 26090137707172: đối soát vẫn ✓
  vì ghép theo id ngân hàng, nhưng cột MÃ HĐ trống).
- Vá: `vot_lai(cur, so_ngay=3)` — job 15s gọi ngay sau `gan_ma`: dòng đã đóng dấu, `ma_chung_tu IS NULL`,
  `loai_chung_tu` rỗng, chiều `in/out`, trong 3 ngày → nhận diện lại, CHỈ ghi khi nay ra đúng 1 mã. Không bao giờ
  đè mã đã có, không đụng dòng `NHIEU`. Test: `GanMaTests.test_vot_lai_dong_chieu_unknown`.
