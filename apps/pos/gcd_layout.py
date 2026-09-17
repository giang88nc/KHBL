# -*- coding: utf-8 -*-
"""
gcd_layout — BỐ CỤC IN GIẤY CẦM ĐỒ (GCD) khổ **A5 NẰM NGANG**, GĐ chốt 15/09/2026.

TỜ GIẤY ĐÃ IN SẴN (ảnh quét: static/img/GCD.jpg — 2470×1724). Khi in THẬT chỉ in CHỮ vào đúng ô,
**TUYỆT ĐỐI KHÔNG in ảnh nền**. Ảnh chỉ làm NỀN XEM TRƯỚC ở trang cấu hình /he-thong/mau-in-gcd/.

Tờ gồm 2 phần, ngăn bởi đường xé dọc ở ~29,5% bề ngang:
  · CUỐNG TRÁI  — chỉ có chữ "SỐ:" in sẵn 2 lần + tên tiệm, phần còn lại TRẮNG.
  · THÂN PHẢI   — 6 dòng kẻ chấm (Nhận của Ông,Bà · Địa chỉ · Món hàng · Số tiền cầm · Bằng chữ ·
                  Thời gian cầm), 2 nhãn ký, khối LƯU Ý, dòng cuối "Cửa hàng có giữ các giấy tờ:".
⇒ KHÔNG có bảng món nhiều dòng (món gộp MỘT dòng); KHÔNG có ô cho lãi suất/tủ/SĐT/ghi chú —
  những thứ đó chỉ nằm ở khối `cuong_chi_tiet` (TẮT SẴN) trên vùng trắng của cuống trái.

AI GHI — AI ĐỌC (một chiều, không HTTP):
    KHBL /he-thong/mau-in-gcd/ --(PmvState.set)--> khj_bl.pmv_state['gcd_layout']   ← NGƯỜI GHI DUY NHẤT
                                                            |  (SELECT chéo DB, chỉ đọc)
    KHCD /camdo/bien-nhan/<lid>/giay  <-- khcd/gcd_layout.load() ------------------+
Module này là BẢN GỐC. Bên KHCD có bản sao CHỈ-ĐỌC cùng thuật toán: **sửa bên này thì chép bên kia**,
và `css()` hai bên phải sinh chuỗi GIỐNG NHAU TỪNG KÝ TỰ (đó là lý do `_so()` dùng "%g").

BA CÁI BẪY ĐÃ XỬ SẴN TRONG MÃ NÀY — đừng gỡ ra:
  1. `css()` TỰ SINH `position:relative` cho `.gcd-a5`. Bên KHCD không có khbl.css; thiếu dòng này thì
     18 khối `position:absolute` neo vào viewport và bố cục vỡ sạch ở CẢ bản in lẫn bản xem trước.
  2. Nền xem trước KHÔNG BAO GIỜ đặt bằng `background` của `.gcd-a5` (rule `body.x .gcd-a5` có độ đặc
     hiệu cao hơn rule chống in ⇒ nền IN RA GIẤY IN SẴN). Nền là phần tử riêng `.gcd-nen.no-print`, và
     `css_in()` ẩn thẳng `display:none` khi in. Cũng KHÔNG dùng `print-color-adjust:exact`.
  3. KHÔNG dùng `G.IN_KHO` / `G.css_in()` của gdb_layout: dict đó không có khổ NGANG, gọi vào sẽ trả
     "auto" mà KHÔNG báo lỗi → tờ 210mm bề ngang tràn/vỡ 2 trang trên máy in đang để A5 dọc.

KHỔ GIẤY LÀ THAM SỐ, KHÔNG PHẢI HẰNG: ảnh quét tỷ lệ 2470/1724 = 1,4327 trong khi A5 chuẩn
210/148 = 1,4189 (lệch ~1% ≈ 2mm) — nhà in địa phương xén tay. `_in.kho_w` / `_in.kho_h` (mm) là số
ĐO BẰNG THƯỚC trên tờ thật; mọi thứ (bề rộng tờ, aspect-ratio, @page size) sinh ra từ hai số đó.
"""
import json
import unicodedata

from apps.pmv.models import PmvState

KEY = "gcd_layout"
MAY_IN_KEY = "may_in_ds"          # SỔ MÁY IN — khoá RIÊNG, dùng chung cho cả 3 mẫu in (GĐB · CỌC · GCD)

PAPER_W_MM, PAPER_H_MM = 210, 148  # chỉ là MẶC ĐỊNH; số thật lấy ở _in.kho_w / _in.kho_h

# BƯỚC NHẢY HAI NỬA CUỐNG — ĐO BẰNG MÁY trên static/img/GCD.jpg (2470×1724) ngày 16/09/2026:
# tâm mực chữ "SỐ:" in sẵn nằm ở 3,25% và 50,06% chiều cao ⇒ hai nửa xé cách nhau 46,81%.
# Hai bảng cuống dùng ĐÚNG số này, nhờ vậy hai mảnh giấy xé ra nằm cùng một vị trí dưới chữ "SỐ:"
# của nửa mình — đó là nghĩa của "canh chỉnh đều theo phần xé".
# ⚠ Cặp so_cuong_1 (top 1,0) / so_cuong_2 (top 46,2) có từ 15/09 đang dùng bước nhảy 45,2, tức mã
#   phiếu ở nửa DƯỚI in cao hơn chữ "SỐ:" của nó khoảng 2,2mm. CHƯA SỬA: đó là bố cục ĐANG CHẠY
#   THẬT và Giám đốc đã lưu bản riêng trong pmv_state — đổi mặc định ở đây KHÔNG đổi được bản đã
#   lưu, phải kéo trên trang cấu hình. Ghi ra để lần sau không ai tưởng 45,2 là số đo của tờ giấy.
BUOC_CUONG_PCT = 46.81

# ── 20 KHỐI ────────────────────────────────────────────────────────────────────────────────────
# `sel` = selector dùng CHUNG ở 2 nơi: partial xem trước KHBL và trang in KHCD (đều gắn data-gcd=key).
# top/h: hộp chữ đặt sao cho ĐÁY hộp trùng dòng kẻ chấm (`align-items:flex-end` trong CSS tĩnh) → chữ
# NGỒI TRÊN đường kẻ, không đè lên. Số dưới đây ĐO TRÊN ẢNH GCD.jpg, là ĐIỂM KHỞI ĐẦU để GĐ kéo tinh.
# an = 1 → khối TẮT SẴN (không in). Xem ghi chú từng khối để biết vì sao tắt.
BLOCKS = [
    # A. CUỐNG TRÁI (tiệm giữ, gắn vào món hàng)
    {"key": "so_cuong_1", "ten": "Số phiếu — cuống trên", "sel": ".gcd-a5__so-cuong-1",
     "left": 5.6, "top": 1.0, "w": 22.0, "h": 3.2, "fs": 8, "an": 0},
    {"key": "so_cuong_2", "ten": "Số phiếu — cuống dưới", "sel": ".gcd-a5__so-cuong-2",
     "left": 5.6, "top": 46.2, "w": 22.0, "h": 3.2, "fs": 8, "an": 0},
    # vùng trắng lớn của cuống: món đầy đủ · lãi %/tháng · tủ · SĐT · ghi chú. Tờ in sẵn KHÔNG có ô cho
    # mấy thứ này ở thân phải nên đây là chỗ hợp lệ duy nhất. TẮT SẴN — GĐ bật nếu muốn cuống mang tin.
    {"key": "cuong_chi_tiet", "ten": "Chi tiết cuống (tuỳ chọn)", "sel": ".gcd-a5__cuong-chi-tiet",
     "left": 2.5, "top": 8.0, "w": 25.0, "h": 37.0, "fs": 6.5, "an": 1},
    # HAI BẢNG CUỐNG (GĐ chốt 16/09/2026) — hai BẢN GIỐNG HỆT nhau: xé đôi cuống thì mỗi nửa vẫn
    # mang đủ thông tin, một bản gắn theo món hàng, một bản lưu sổ.
    # VÌ SAO ĐẶT Ở HAI MỐC NÀY: tờ in sẵn có ĐÚNG HAI chữ "SỐ:" trên cuống — đó chính là hai nửa
    # xé. Hai bảng cách nhau ĐÚNG BẰNG khoảng cách giữa hai chữ đó (BUOC_CUONG_PCT), nên hai mảnh
    # giấy xé ra trông y hệt nhau: mỗi bảng nằm cùng một khoảng cách dưới chữ "SỐ:" của nửa mình.
    # ⚠ 16/09/2026: bước nhảy này ĐO LẠI BẰNG MÁY trên GCD.jpg = 46,81%, KHÔNG phải 45,2% như cặp
    # so_cuong_1/so_cuong_2 đang dùng (hai khối đó lệch 2,4mm so với chữ in sẵn — xem BUOC_CUONG_PCT).
    # Bản trên bắt đầu 13,5%: NGAY DƯỚI khối chữ in sẵn "DNTN… / KIM HẠNH II" (đáy ~12,5%).
    # ⚠ KHÔNG VIỀN (GĐ chốt): CSS_CUONG không vẽ một đường kẻ nào, chỉ căn hàng bằng khoảng cách.
    # `cuong_chi_tiet` ở trên coi như đã bị hai bảng này THAY THẾ — giữ lại (vẫn TẮT SẴN) để bố cục
    # cũ ai đã lưu thì không mất, đừng bật cùng lúc kẻo hai khối đè nhau.
    {"key": "cuong_bang_1", "ten": "Bảng cuống — bản trên", "sel": ".gcd-a5__cuong-bang-1",
     "left": 3.0, "top": 13.5, "w": 26.0, "h": 33.0, "fs": 7, "an": 0},
    {"key": "cuong_bang_2", "ten": "Bảng cuống — bản dưới", "sel": ".gcd-a5__cuong-bang-2",
     "left": 3.0, "top": 60.3, "w": 26.0, "h": 33.0, "fs": 7, "an": 0},

    # B. THÂN PHẢI — biên nhận giao khách
    # MÃ VẠCH Code 39 của SỐ BIÊN NHẬN (GĐ yêu cầu 16/09/2026). Nội dung = phần CHỮ SỐ của mã phiếu
    # (CD26090100012 → 26090100012), sinh bằng apps/pos/ma_vach.py — xem `so_ma_vach()` để biết vì
    # sao bỏ tiền tố chữ và vì sao KHÔNG rút gọn 9 số như Giấy đảm bảo.
    # VÌ SAO ĐẶT Ở ĐÂY (left 77,4 · top 5,6 · rộng 18,8 · cao 6,0) — số ĐO BẰNG MÁY trên GCD.jpg,
    # không ước lượng bằng mắt (ước lượng bằng mắt đã sai 5% và đè mất chữ "II" của KIM HẠNH II):
    #   · Quét mực tờ giấy: trong dải y 5,0–12,4% (DƯỚI dòng in sẵn "DNTN KINH DOANH VÀNG & CẦM ĐỒ"
    #     đáy 4,8% · TRÊN dòng "ĐC: 1276 Kha Vạn Cân…" đỉnh 12,4%) thì chữ đỏ "KIM HẠNH II" hết ở
    #     77,1% và từ đó sang phải SẠCH MỰC tới mép. Đây là khoảng trống rộng nhất NẰM TRÊN ô "SỐ:".
    #   · Bắt đầu 77,4% (hở 0,3% với chữ đỏ; cộng quiet-zone 1,7mm dựng sẵn trong ảnh ⇒ vạch đen thật
    #     cách chữ ~2,4mm). Mép phải 96,2% chừa 8,0mm — ngoài vùng chết cơ khí 4–6mm của máy in, cùng
    #     lý do đã bắt khối `giay_to` phải TẮT SẴN.
    #   · CAO 6,0% = 8,9mm — thừa sức cho máy quét bắt tia chéo, vẫn hở ~1,2mm trên và 0,8mm dưới.
    # ⚠ BỀ RỘNG LÀ TRẦN CỨNG CỦA TỜ GIẤY NÀY, KHÔNG PHẢI LỰA CHỌN THẨM MỸ: cả nửa phải tờ giấy không
    #   có dải trống nào quá ~40mm (đã đo cả dải y 14,6–19,4% và y 22–31%, đều 38–40mm). 11 chữ số tốn
    #   207 mô-đun hẹp ⇒ vạch hẹp ≈ 0,17mm (7 mil). Máy quét CCD/laser cầm tay ở cự ly quầy đọc được
    #   cỡ này, NHƯNG ĐÂY LÀ ĐIỀU DUY NHẤT PHẢI QUÉT THỬ THẬT trên tờ in đầu tiên.
    #   Quét không ra thì ĐỪNG bóp mã cho vừa chỗ khác — hoặc nới ô Rộng % lấn sang trái (chấp nhận
    #   sát chữ đỏ hơn), hoặc kéo xuống dải y 23–30,5% (bên phải tiêu đề "BIÊN NHẬN CẦM ĐỒ", rộng
    #   tương đương, chỉ tội nằm DƯỚI ô SỐ:), hoặc đặt lại tờ giấy in sẵn có chừa chỗ cho mã vạch.
    #   TUYỆT ĐỐI KHÔNG cắt bớt chữ số cho mã ngắn lại — xem `so_ma_vach()`.
    # KHÔNG làm khối "số đọc được" riêng như Giấy đảm bảo: bên GĐB mã vạch mang mã 9 số RÚT GỌN,
    # khác mã hóa đơn in trên giấy, nên bắt buộc phải in kèm số để người đọc đối chiếu. Ở đây khối
    # `ma_phieu` ngay bên dưới ĐÃ in nguyên mã CD26090100012 rồi — thêm một dòng số nữa là in trùng,
    # tốn chỗ trên tờ giấy đã chật và đẻ thêm một khối phải căn.
    {"key": "ma_phieu_vach", "ten": "Mã vạch Số biên nhận", "sel": ".gcd-a5__ma-vach",
     "left": 77.4, "top": 5.6, "w": 18.8, "h": 6.0, "fs": 8, "an": 0},
    {"key": "ma_phieu", "ten": "Số biên nhận (ô SỐ:)", "sel": ".gcd-a5__ma-phieu",
     "left": 83.8, "top": 18.4, "w": 14.2, "h": 3.2, "fs": 8, "an": 0},
    {"key": "khach_ten", "ten": "Nhận của Ông/Bà", "sel": ".gcd-a5__khach-ten",
     "left": 45.0, "top": 29.0, "w": 52.9, "h": 3.4, "fs": 9, "an": 0},
    {"key": "khach_diachi", "ten": "Địa chỉ", "sel": ".gcd-a5__khach-diachi",
     "left": 37.0, "top": 33.0, "w": 60.9, "h": 3.4, "fs": 9, "an": 0},
    {"key": "mon_hang", "ten": "Món hàng (gộp 1 dòng)", "sel": ".gcd-a5__mon-hang",
     "left": 39.3, "top": 36.9, "w": 58.6, "h": 3.4, "fs": 8.5, "an": 0},
    {"key": "so_tien_so", "ten": "Số tiền cầm (số)", "sel": ".gcd-a5__so-tien-so",
     "left": 40.5, "top": 40.7, "w": 57.4, "h": 3.6, "fs": 10, "an": 0},
    {"key": "so_tien_chu", "ten": "Bằng chữ", "sel": ".gcd-a5__so-tien-chu",
     "left": 38.8, "top": 44.8, "w": 59.1, "h": 3.4, "fs": 8.5, "an": 0},
    {"key": "ky_han", "ten": "Thời gian cầm (số ngày)", "sel": ".gcd-a5__ky-han",
     "left": 42.9, "top": 49.6, "w": 4.7, "h": 3.2, "fs": 9, "an": 0},
    {"key": "ngay_lap", "ten": "Kể từ ngày", "sel": ".gcd-a5__ngay-lap",
     "left": 59.0, "top": 49.6, "w": 9.6, "h": 3.2, "fs": 9, "an": 0},
    {"key": "ngay_hen", "ten": "Đến hết ngày", "sel": ".gcd-a5__ngay-hen",
     "left": 78.0, "top": 49.6, "w": 10.1, "h": 3.2, "fs": 9, "an": 0},
    {"key": "nam", "ten": "Năm", "sel": ".gcd-a5__nam",
     "left": 91.6, "top": 49.6, "w": 6.3, "h": 3.2, "fs": 9, "an": 0},
    {"key": "nhan_vien", "ten": "Lập phiếu (tên NV)", "sel": ".gcd-a5__nhan-vien",
     "left": 76.0, "top": 59.5, "w": 21.0, "h": 3.2, "fs": 8, "an": 0},
    # khách tự ký tay → TẮT SẴN
    {"key": "khach_ky", "ten": "Tên khách dưới chữ ký", "sel": ".gcd-a5__khach-ky",
     "left": 33.0, "top": 59.5, "w": 18.0, "h": 3.2, "fs": 8, "an": 1},
    # chỉ cần khi IN LẠI phiếu cũ (đã chuộc / BÁO MẤT BIÊN NHẬN) → TẮT SẴN
    {"key": "trang_thai", "ten": "Dấu trạng thái / BÁO MẤT", "sel": ".gcd-a5__trang-thai",
     "left": 34.0, "top": 62.5, "w": 60.0, "h": 4.0, "fs": 10, "an": 1},
    # ⚠ dòng "* Cửa hàng có giữ các giấy tờ:" nằm ở ~97% chiều cao = CÁCH MÉP DƯỚI ~4mm, lọt vùng chết
    # cơ khí của laser/inkjet phổ thông (4,2–6,4mm) — @page margin:0 KHÔNG mở được vùng đó. TẮT SẴN cho
    # tới khi in thử đúng máy in thật; cần ghi CCCD thì bật `cuong_chi_tiet` bên cuống trái.
    {"key": "giay_to", "ten": "Mã truy vết (ô Giấy tờ)", "sel": ".gcd-a5__giay-to",
     "left": 55.5, "top": 94.1, "w": 31.8, "h": 3.0, "fs": 8, "an": 1},
]
BLOCK_MAP = {b["key"]: b for b in BLOCKS}
GIOI_HAN = {"left": (0, 100), "top": (0, 100), "w": (1, 100), "h": (0.5, 100), "fs": (3, 30)}

# Khối ẢNH (mã vạch) — markup là thẻ <img class="… gcd-anh">. Rule này PHẢI do `css()` sinh ra, KHÔNG
# để trong static/css: tệp static là của riêng KHBL, KHCD có bản sao khác ⇒ đúng kiểu lệch hai bên đã
# làm chữ lệch 9mm hôm trước. `object-fit:fill` = kéo ảnh phủ trọn hộp, tỷ lệ BỀ NGANG giữa các vạch
# vẫn đều nhau (co giãn đồng nhất theo trục ngang) nên máy quét vẫn đọc đúng; `image-rendering:
# pixelated` để trình duyệt đừng làm mờ mép vạch lúc phóng to ra khổ in.
# ⚠ Chuỗi này được NHÂN BẢN trong static/js/gcd_mau.js (hàm buildCss dựng lại CSS lúc kéo-thả). Sửa
# đây thì sửa cả bên kia — `smoke_gcd` đối chiếu từng ký tự và FAIL nếu lệch.
CSS_ANH = (".gcd-anh{display:block!important;padding:0!important;background:#fff;"
           "object-fit:fill;image-rendering:pixelated}")

# Khối BẢNG CUỐNG — markup là <div class="… gcd-cuong"> chứa QR + mấy dòng. Cùng lý do với CSS_ANH:
# rule phải do `css()` sinh để KHCD có luôn, và static/js/gcd_mau.js giữ BẢN SAO y hệt.
# KHÔNG VẼ VIỀN (GĐ chốt 16/09/2026): chỉ căn hàng bằng khoảng cách. Mọi đường kẻ đều vắng mặt có
# chủ đích — thêm `border` vào đây là đi ngược yêu cầu, đừng "sửa cho đẹp".
# Khối này KHÔNG mang class .gcd-o (lớp đó xếp chữ dồn xuống đáy hộp, phá bố cục bảng) nên phải tự
# khai z-index, y như bẫy đã gặp với .gcd-anh: không có thì nó nằm cùng tầng ảnh nền xem trước.
# Nhãn dọc co theo `em` chứ KHÔNG dùng var(--gcd-fs): biến đó chỉ có bên KHBL, css() của KHCD không
# sinh ra. Dùng `em` thì hai bên cùng ăn theo cỡ chữ của khối, khỏi phải nhớ đồng bộ thêm một thứ.
CSS_CUONG = (
    ".gcd-a5 .gcd-cuong{z-index:1}"
    ".gcd-cuong{display:flex!important;flex-direction:row;align-items:stretch;gap:1.2mm;border:none!important;padding:0!important;overflow:hidden;line-height:1.15}"
    ".gcd-cuong__doc{flex:0 0 auto;writing-mode:vertical-rl;transform:rotate(180deg);white-space:nowrap;text-align:center;font-size:1.3em}"
    ".gcd-cuong__than{flex:1 1 auto;min-width:0;display:flex;flex-direction:column;gap:.9mm}"
    ".gcd-cuong__dau{display:flex;flex-direction:row;align-items:flex-start;gap:1.2mm}"
    ".gcd-cuong__qr{flex:0 0 32%;aspect-ratio:1/1;height:auto;display:block;background:#fff}"
    ".gcd-cuong__ds{flex:1 1 auto;min-width:0;display:flex;flex-direction:column;gap:1.5mm; overflow-wrap:anywhere;word-break:break-word;margin-top: 10px;}"
    ".gcd-cuong__noi{flex:1 1 auto;min-height:0;overflow-wrap:anywhere;word-break:break-word}"
    ".gcd-cuong__tien{display:flex;flex-direction:row;gap:1.2mm;justify-content:center}"
    ".gcd-cuong__tien span{flex:0 0 auto}"
    ".gcd-cuong b{font-weight:700; font-size: 16px;}"
)

# Khối nào là ẢNH, khối nào là BẢNG — hai bên dùng chung để template khỏi đoán theo tên khoá.
KHOI_MA_VACH = "ma_phieu_vach"
KHOI_ANH = (KHOI_MA_VACH,)
KHOI_BANG = ("cuong_bang_1", "cuong_bang_2")

# ── _in : THIẾT LẬP MÁY IN ─────────────────────────────────────────────────────────────────────
# kho "A5N" = gửi máy in ĐÚNG kích thước tờ đo được (kho_w × kho_h mm). KHÔNG dùng "auto" làm mặc định
# như gdb_layout: GĐB là tờ DỌC 148mm bề ngang nên lọt trong mọi khổ dọc; GCD là tờ NGANG 210mm bề
# ngang — máy in đang để A5 DỌC thì "auto" cho khung 148mm ⇒ tờ tràn ra ngoài / vỡ thành 2 trang.
IN_KEY = "_in"
CT_KEY = "_ct"
NEN_KEY = "_nen"
IN_MAC_DINH = {"kho": "A5N", "canh": "giua", "dx": 0, "dy": 0, "ty_le": 100,
               "kho_w": PAPER_W_MM, "kho_h": PAPER_H_MM, "may_in": ""}
KHO_TO = "TỜ"   # giá trị canh: sinh size từ kho_w × kho_h
IN_KHO = {"A5N": KHO_TO, "auto": "auto", "A4N": "A4 landscape",
          "A5D": "A5 portrait", "A4D": "A4 portrait", "Letter": "letter landscape"}
IN_CANH = ("giua", "trai")
IN_GIOI_HAN = {"dx": (-80, 80), "dy": (-80, 80), "ty_le": (50, 150),
               "kho_w": (80, 420), "kho_h": (60, 420)}

# ── _ct : NỘI DUNG ────────────────────────────────────────────────────────────────────────────
# ⚠ CHỜ GĐ CHỐT. Trang in cũ của KHCD (loan_print.html) đang in `principal_balance` (dư gốc hiện tại)
# và ghi hẳn câu "Biên nhận phản ánh dư gốc hiện tại". Mặc định ở đây để ĐÚNG BẰNG số đang in hôm nay
# để hai đường in không bao giờ đưa ra hai con số khác nhau cho cùng một phiếu. GĐ muốn in GỐC BAN ĐẦU
# thì đổi ở trang cấu hình — và khi đó phải dán nhãn "bản nội bộ" cho trang in cũ.
CT_MAC_DINH = {"tien": "du_hien_tai"}
CT_TIEN = ("du_hien_tai", "goc_ban_dau")

# ── _nen : HIỆU CHỈNH ẢNH NỀN XEM TRƯỚC ───────────────────────────────────────────────────────
# CHỈ trang cấu hình KHBL dùng. `css()` KHÔNG BAO GIỜ sinh ra nó, KHCD đọc xong BỎ QUA.
# Đây là van an toàn cho sai số quét ảnh, KHÔNG phải công cụ căn chính: căn theo THƯỚC ĐO TỜ THẬT.
NEN_MAC_DINH = {"x": 0, "y": 0, "w": 100, "h": 100}
NEN_GIOI_HAN = {"x": (-30, 30), "y": (-30, 30), "w": (50, 200), "h": (50, 200)}


def mac_dinh():
    out = {b["key"]: {k: b[k] for k in ("left", "top", "w", "h", "fs", "an")} for b in BLOCKS}
    out[IN_KEY] = dict(IN_MAC_DINH)
    out[CT_KEY] = dict(CT_MAC_DINH)
    out[NEN_KEY] = dict(NEN_MAC_DINH)
    return out


def _ep(v, lo, hi):
    """Ép số vào khoảng, làm tròn 2 số lẻ. Trả None nếu không phải số (NaN/inf cũng bị loại)."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f or f in (float("inf"), float("-inf")):   # NaN / ±Infinity
        return None
    return max(lo, min(hi, round(f, 2)))


def _nhan_in(dst, vals):
    """Nhận thiết lập máy in hợp lệ vào dst (khổ/canh thuộc danh sách, số trong giới hạn)."""
    if vals.get("kho") in IN_KHO:
        dst["kho"] = vals["kho"]
    if vals.get("canh") in IN_CANH:
        dst["canh"] = vals["canh"]
    if "may_in" in vals:
        # chỉ là ID trỏ sang SỔ MÁY IN (khoá may_in_ds) — không bao giờ chứa tên máy in thật
        dst["may_in"] = _ma(vals["may_in"])
    for p, (lo, hi) in IN_GIOI_HAN.items():
        if p in vals:
            f = _ep(vals[p], lo, hi)
            if f is not None:
                dst[p] = f


def _gop(out, data):
    """Đè bản lưu lên mặc định — chỉ nhận key/thuộc tính hợp lệ, số trong giới hạn (dùng chung
    load/save nên DB không bao giờ chứa rác, và thêm khối mới vào BLOCKS là chạy ngay)."""
    for key, vals in (data or {}).items():
        if key not in out or not isinstance(vals, dict):
            continue
        if key == IN_KEY:
            _nhan_in(out[key], vals)
            continue
        if key == CT_KEY:
            if vals.get("tien") in CT_TIEN:
                out[key]["tien"] = vals["tien"]
            continue
        if key == NEN_KEY:
            for p, (lo, hi) in NEN_GIOI_HAN.items():
                f = _ep(vals.get(p), lo, hi) if p in vals else None
                if f is not None:
                    out[key][p] = f
            continue
        for p, v in vals.items():
            if p == "an":                       # ẨN/HIỆN: chỉ nhận đúng 0 hoặc 1, không qua GIOI_HAN
                out[key]["an"] = 1 if v in (1, "1", True, "true") else 0
            elif p in out[key] and p in GIOI_HAN:
                f = _ep(v, *GIOI_HAN[p])
                if f is not None:
                    out[key][p] = f
    return out


def load():
    """Bố cục hiện hành = mặc định ⊕ bản lưu."""
    out = mac_dinh()
    raw = PmvState.get(KEY, "")
    if raw:
        try:
            data = json.loads(raw)
        except ValueError:
            data = {}
        _gop(out, data)
    return out


def save(data):
    """Lưu bố cục (đi qua cùng merge với load). data = dict; None/{} = XOÁ hàng, về mặc định."""
    if not data:
        PmvState.set(KEY, "")
        return mac_dinh()
    clean = _gop(mac_dinh(), data)
    PmvState.set(KEY, json.dumps(clean, ensure_ascii=False))
    return clean


# ── SỔ MÁY IN (khoá riêng may_in_ds) ──────────────────────────────────────────────────────────
# TÁCH KHỎI BỐ CỤC vì bố cục là của TỜ GIẤY (mỗi mẫu in một bản) còn sổ máy in là của THIẾT BỊ (cả nhà
# dùng chung). Gộp chung thì đổi máy in phải sửa 3 khoá; tách ra thì sửa MỘT dòng trong sổ.
# ⚠ SỰ THẬT KỸ THUẬT: trang web KHÔNG ra lệnh được cho trình duyệt in vào máy in X — không có API web
# nào liệt kê/chọn máy in, đó là rào bảo mật cố ý. `--kiosk-printing` bỏ hộp thoại nhưng in ra MÁY IN
# MẶC ĐỊNH CỦA WINDOWS trên chính máy đang mở trình duyệt. Vậy sổ này hiện là NHÃN CẤU HÌNH cho người
# vận hành; muốn bấm-là-máy-chủ-đẩy thì phải có máy in mạng và thêm nhánh vào `gui_lenh_in()` sau này.
MAY_IN_PHIEN_BAN = 1
MAY_IN_CACH = {
    "trinh_duyet": "Trình duyệt máy quầy in (đang dùng)",
    "ipp": "Máy chủ gửi IPP 631 (chưa nối)",
    "raw9100": "Máy chủ gửi raw TCP 9100 (chưa nối)",
    "windows": "Máy chủ in qua driver Windows (chưa nối)",
    "agent": "Đại lý in tại máy quầy (chưa nối)",
}
MAY_IN_CHAY_THAT = ("trinh_duyet",)     # các cách còn lại: lưu được, giao diện gắn badge "chưa nối"
MAY_IN_MAC_DINH = {
    "phien_ban": MAY_IN_PHIEN_BAN,
    "mac_dinh": "quay_cd",
    "ban": [{
        "id": "quay_cd", "ten": "Máy in quầy cầm đồ", "cach": "trinh_duyet",
        "host": "", "cong": 0, "hang_doi": "", "ten_windows": "", "bat": True,
        "ghi_chu": "Edge --kiosk-printing tại máy quầy; máy in MẶC ĐỊNH của Windows máy đó quyết định.",
    }],
}


def _ma(v):
    """Chuẩn hoá ID máy in: BỎ DẤU rồi chỉ giữ a-z 0-9 _ -, tối đa 40 ký tự. Rác → chuỗi rỗng.
    ⚠ KHÔNG dùng `str.isalnum()` để lọc: 'ầ'.isalnum() là True nên "Quầy CĐ" sẽ ra id "quầycđ"
    (bộ kiểm 15/09 bắt được) — id là khoá đối chiếu giữa `_in.may_in` và sổ máy in, phải thuần ASCII."""
    s = unicodedata.normalize("NFD", str(v or "").strip().lower())
    s = s.replace("đ", "d")          # đ KHÔNG tách dấu được bằng NFD, phải đổi tay
    s = "".join(c for c in s if ("a" <= c <= "z") or ("0" <= c <= "9") or c in "_-")
    return s[:40]


def _chu(v, n):
    return str(v or "").strip()[:n]


def may_in_mac_dinh():
    return json.loads(json.dumps(MAY_IN_MAC_DINH))


def _gop_may_in(data):
    """Nhận SỔ MÁY IN hợp lệ: bỏ dòng thiếu id, khử trùng id, giữ tối đa 20 dòng."""
    out = {"phien_ban": MAY_IN_PHIEN_BAN, "mac_dinh": "", "ban": []}
    da_co = set()
    for row in (data or {}).get("ban", []) or []:
        if not isinstance(row, dict):
            continue
        ma = _ma(row.get("id"))
        if not ma or ma in da_co:
            continue
        da_co.add(ma)
        cong = _ep(row.get("cong", 0), 0, 65535)
        out["ban"].append({
            "id": ma,
            "ten": _chu(row.get("ten"), 60) or ma,
            "cach": row.get("cach") if row.get("cach") in MAY_IN_CACH else "trinh_duyet",
            "host": _chu(row.get("host"), 60),
            "cong": int(cong or 0),
            "hang_doi": _chu(row.get("hang_doi"), 60),
            "ten_windows": _chu(row.get("ten_windows"), 80),
            "bat": bool(row.get("bat", True)),
            "ghi_chu": _chu(row.get("ghi_chu"), 200),
        })
        if len(out["ban"]) >= 20:
            break
    md = _ma((data or {}).get("mac_dinh"))
    out["mac_dinh"] = md if md in da_co else (out["ban"][0]["id"] if out["ban"] else "")
    return out


def may_in_load():
    raw = PmvState.get(MAY_IN_KEY, "")
    if raw:
        try:
            return _gop_may_in(json.loads(raw))
        except (ValueError, TypeError):
            pass
    return may_in_mac_dinh()


def may_in_save(data):
    """None/{} = xoá hàng, về sổ mặc định."""
    if not data:
        PmvState.set(MAY_IN_KEY, "")
        return may_in_mac_dinh()
    clean = _gop_may_in(data)
    PmvState.set(MAY_IN_KEY, json.dumps(clean, ensure_ascii=False))
    return clean


# ── SINH CSS ──────────────────────────────────────────────────────────────────────────────────
def _so(v):
    """3.0 → '3', 20.4 → '20.4'. Khớp cách JS in số ⇒ CSS trang cấu hình và CSS server GIỐNG NHAU
    TỪNG KÝ TỰ (cũng là điều kiện để bản KHCD so khớp được với bản này)."""
    return "%g" % float(v)


def css_in(inn=None):
    """Khối @media print: khổ trang gửi máy in + cách đặt tờ GCD trên trang đó + CHỐNG IN NỀN.
    Phải đứng SAU mọi rule in khác (cùng !important → khai sau thắng) nên là nguồn duy nhất
    quyết định @page. ⚠ khbl.css đang có sẵn `@media print{@page{size:A5 portrait}}` cho GĐB —
    chuỗi này chèn sau nên thắng; đừng đảo thứ tự."""
    inn = {**IN_MAC_DINH, **(inn or {})}
    size = IN_KHO.get(inn["kho"], "auto")
    if size == KHO_TO:
        size = "%smm %smm" % (_so(inn["kho_w"]), _so(inn["kho_h"]))
    giua = inn["canh"] != "trai"
    rule = ["left:%smm!important" % _so(inn["dx"]), "top:%smm!important" % _so(inn["dy"]),
            "margin:0 auto!important" if giua else "margin:0!important",
            "background:none!important", "box-shadow:none!important", "overflow:hidden!important",
            # Bản thân .gcd-a5 có max-width:100% cho vừa màn hình. Rule đó nằm NGOÀI
            # @media print nên vẫn còn hiệu lực lúc in: chọn khổ giấy máy in KHÁC tờ A5
            # là cả phiếu CO NHỎ LẠI mà không báo gì — chữ lệch hết khỏi ô giấy in sẵn.
            "max-width:none!important"]
    ty_le = float(inn["ty_le"])
    if ty_le != 100:
        rule.append("transform:scale(%s)!important;transform-origin:top %s!important"
                    % (_so(ty_le / 100), "center" if giua else "left"))
    # `.gcd-nen,.no-print{display:none}` = lớp chống in nền CUỐI CÙNG: nền là phần tử riêng nên
    # display:none diệt hẳn, không rule background nào cứu được nó.
    return ("@media print{@page{size:%s;margin:0}.gcd-nen,.no-print{display:none!important}"
            ".gcd-a5{%s}}" % (size, ";".join(rule)))


def css(layout=None):
    """CSS ghi đè vị trí + cỡ chữ từng khối. Dùng CHUNG cho trang cấu hình KHBL và trang in KHCD.

    Bậc thang co chữ `.gcd-co-2` / `.gcd-co-3` (88% / 76%): SERVER đếm ký tự rồi gắn sẵn class vào khối
    — không mutate style lúc chạy nên không phụ thuộc CSP của KHCD. Hết bậc thang thì cho XUỐNG DÒNG
    trong chiều cao đã cấp, TUYỆT ĐỐI không `overflow:hidden` cấp khối (cắt cụt tên khách hay danh sách
    món trên chứng từ pháp lý là lỗi nặng). `overflow:hidden` chỉ đặt ở CẤP TỜ GIẤY để chữ tràn không
    đẻ thêm trang 2 — ăn thêm một tờ giấy in sẵn.
    """
    layout = layout or load()
    inn = {**IN_MAC_DINH, **(layout.get(IN_KEY) or {})}
    w, h = _so(inn["kho_w"]), _so(inn["kho_h"])
    out = [".gcd-a5{position:relative!important;width:%smm!important;max-width:100%%;height:auto;"
           "aspect-ratio:%s/%s;overflow:hidden}" % (w, w, h), CSS_ANH, CSS_CUONG]
    for b in BLOCKS:
        v = layout[b["key"]]
        fs = _so(v["fs"])
        out.append(b["sel"] + "{position:absolute!important;left:%s%%!important;top:%s%%!important;"
                   "width:%s%%!important;height:%s%%!important;font-size:%spt!important;--gcd-fs:%spt}"
                   % (_so(v["left"]), _so(v["top"]), _so(v["w"]), _so(v["h"]), fs, fs))
        out.append(b["sel"] + ".gcd-co-2{font-size:calc(%spt*.88)!important}" % fs)
        out.append(b["sel"] + ".gcd-co-3{font-size:calc(%spt*.76)!important}" % fs)
        if v.get("an"):
            # ẨN khi IN; ở trang cấu hình (tờ mang class .gcd-sua) vẫn THẤY MỜ để còn kéo được —
            # khối ẩn mà biến mất thì phải bật lên mới chỉnh, và lúc bật vị trí đã sai từ đời nào.
            out.append(".gcd-a5:not(.gcd-sua) " + b["sel"] + "{display:none!important}")
            out.append(".gcd-a5.gcd-sua " + b["sel"] +
                       "{opacity:.3!important;outline:1px dashed #B3402A!important}")
    out.append(css_in(inn))
    return "\n".join(out)


# ── BẬC THANG CO CHỮ (server quyết định, không JS) ────────────────────────────────────────────
CO_NGUONG = {  # key khối → (ngưỡng sang bậc 2, ngưỡng sang bậc 3) tính bằng SỐ KÝ TỰ
    "mon_hang": (46, 72),
    "khach_diachi": (44, 66),
    "so_tien_chu": (52, 78),
    "khach_ten": (34, 48),
}


def lop_co(key, text):
    """Trả class co chữ cho khối: '' | 'gcd-co-2' | 'gcd-co-3'. Dùng ở CẢ hai bên (KHBL xem trước,
    KHCD in thật) để bản xem trước không nói dối."""
    nguong = CO_NGUONG.get(key)
    if not nguong:
        return ""
    n = len(str(text or ""))
    if n > nguong[1]:
        return "gcd-co-3"
    if n > nguong[0]:
        return "gcd-co-2"
    return ""


def qua_dai(key, text):
    """True khi vượt cả bậc cuối → trang xem trước hiện dải cảnh báo (class no-print)."""
    nguong = CO_NGUONG.get(key)
    return bool(nguong) and len(str(text or "")) > nguong[1] * 1.25
