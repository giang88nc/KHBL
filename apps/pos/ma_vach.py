# -*- coding: utf-8 -*-
"""
ma_vach — SINH MÃ VẠCH Code 39 (PNG data URI) dùng chung cho MỌI mẫu in của KHBL.

TÁCH RA NGÀY 16/09/2026. Trước đó thuật toán nằm trong `apps/pos/views.py::_codebar`, chỉ phục vụ
GIẤY ĐẢM BẢO. Nay GIẤY CẦM ĐỒ cũng cần mã vạch ⇒ tách ra để HAI MẪU IN DÙNG CHUNG MỘT BẢN, không
bao giờ có chuyện sửa một bên rồi hai tờ giấy in ra hai kiểu vạch khác nhau.
⚠ Thuật toán bên dưới là BẢN CHÉP NGUYÊN VĂN — đã QUÉT ĐƯỢC ngoài thực tế trên Giấy đảm bảo.
   Đừng "cải tiến" (đổi bề rộng vạch, bỏ quiet-zone, đổi chiều cao ảnh): mọi con số ở đây là tỷ lệ
   Code 39 chuẩn, lệch một chút là máy quét câm mà KHÔNG báo lỗi gì.

VÌ SAO VẼ RA ẢNH PNG CHỨ KHÔNG PHẢI CÁC `div` CSS: trình duyệt co vạch lẻ thành pixel MỜ khi in
(vạch hẹp 0,2mm rơi vào nửa pixel → xám nhoè), máy quét đọc không ra. Ảnh raster do Pillow vẽ thì
mỗi vạch là số nguyên pixel, in ra sắc nét.

BẢNG MÃ CHỈ CÓ CHỮ SỐ (+ '-' và '*' làm start/stop). Code 39 thật có cả A-Z nhưng bản này CỐ Ý
không mở rộng — xem `so_ma_vach()` để biết mã có chữ cái thì xử lý thế nào.

AI DÙNG:
  · Giấy đảm bảo — apps/pos/views.py (qua alias `_codebar`), mã 9 số của `_ma_gdb()`.
  · Giấy cầm đồ  — apps/pos/gcd_print_config.py (xem trước) và bên KHCD (in thật).
⇒ Bên KHCD có BẢN SAO cùng thuật toán: sửa bên này thì CHÉP SANG KHCD, và hai bên phải cho ra
  CÙNG MỘT CHUỖI data URI với cùng đầu vào (bộ kiểm `manage.py smoke_gcd` khoá điều này).
"""
import base64
import re
from io import BytesIO

CODE39 = {
    "0": "nnnwwnwnn", "1": "wnnwnnnnw", "2": "nnwwnnnnw", "3": "wnwwnnnnn",
    "4": "nnnwwnnnw", "5": "wnnwwnnnn", "6": "nnwwwnnnn", "7": "nnnwnnwnw",
    "8": "wnnwnnwnn", "9": "nnwwnnwnn", "-": "nnnwnnnww", "*": "nwnnwnwnn",
    # A–Z + ký hiệu chuẩn Code 39 (17/09/2026 — GĐ chốt: mã vạch mang TRỌN mã phiếu 'KH22609123456',
    # máy quét trả đúng chuỗi mã phiếu). Chữ số giữ NGUYÊN mẫu cũ ⇒ Giấy đảm bảo (mã 9 số) không đổi.
    "A": "wnnnnwnnw", "B": "nnwnnwnnw", "C": "wnwnnwnnn", "D": "nnnnwwnnw", "E": "wnnnwwnnn",
    "F": "nnwnwwnnn", "G": "nnnnnwwnw", "H": "wnnnnwwnn", "I": "nnwnnwwnn", "J": "nnnnwwwnn",
    "K": "wnnnnnnww", "L": "nnwnnnnww", "M": "wnwnnnnwn", "N": "nnnnwnnww", "O": "wnnnwnnwn",
    "P": "nnwnwnnwn", "Q": "nnnnnnwww", "R": "wnnnnnwwn", "S": "nnwnnnwwn", "T": "nnnnwnwwn",
    "U": "wwnnnnnnw", "V": "nwwnnnnnw", "W": "wwwnnnnnn", "X": "nwnnwnnnw", "Y": "wwnnwnnnn",
    "Z": "nwwnwnnnn", " ": "nwwnnnwnn", ".": "wwnnnnwnn", "$": "nwnwnwnnn", "/": "nwnwnnnwn",
    "+": "nwnnnwnwn", "%": "nnnwnwnwn",
}


def so_ma_vach(ma):
    """CHUỖI mà mã vạch mang theo = TRỌN MÃ PHIẾU: giữ CHỮ (in hoa) + SỐ, bỏ ký tự khác, KHÔNG rút gọn.

        'KH22609020810'   → 'KH22609020810'   13 ký tự — đường cầm đồ hằng ngày
        'CU2606000000625' → 'CU2606000000625' 15 ký tự — phiếu chuyển từ hệ cũ
        'CD-2609/01 00012' → 'CD26090100012'  (ký tự ngăn cách bị bỏ)

    GĐ chốt 17/09/2026: bản cũ BỎ CHỮ CÁI ('KH22609123456' → '22609123456') nên máy quét trả về một
    chuỗi KHÔNG PHẢI mã phiếu, tra cứu không khớp. Nay Code 39 mã hoá cả A–Z (bảng CODE39 ở trên đã
    thêm), quét ra đúng 'KH22609123456' — tra thẳng theo `sku`, không phải REPLACE bỏ chữ nữa.

    ⚠ ĐÂY LÀ HỢP ĐỒNG GIỮA KHBL VÀ KHCD. Hai bên phải sinh RA ĐÚNG MỘT chuỗi cho cùng một mã phiếu,
    nếu không thì bản xem trước một đằng, tờ in ra một nẻo. Chạy lại trên chính kết quả vẫn ra thế
    (idempotent) để tra ngược an toàn. Chữ số KHÔNG đổi mẫu vạch ⇒ mã 9 số của Giấy đảm bảo y cũ.
    VÌ SAO KHÔNG RÚT GỌN như `_ma_gdb()` của Giấy đảm bảo: cắt bớt là hai phiếu khác nhau có thể ra
    cùng một mã vạch — chứng từ cầm đồ là giấy tờ pháp lý, quét nhầm phiếu là trả nhầm hàng.
    ⚠ Dài thêm 2 ký tự chữ ⇒ vạch hẹp mỏng đi ~12% so với bản 11 số: khối rộng 18,8% trên tờ 210 mm
    cho ≈ 0,152 mm — vẫn ≥ ngưỡng đỏ 0,15 mm nhưng nằm ở dải "quét thử" (xem `vach_hep_mm`).
    """
    return re.sub(r"[^0-9A-Z]", "", str(ma or "").upper())


def png_code39(code):
    """PNG Code 39 chuẩn, có start/stop và quiet-zone cho máy quét mã vạch.

    Không dùng các ``div`` CSS để browser không co vạch lẻ thành pixel mờ khi in.

    Đầu vào rỗng / không có chữ số nào → vẽ mã của số '0' (ảnh hợp lệ, KHÔNG ném lỗi): trang xem
    trước và trang in không bao giờ được chết chỉ vì một phiếu thiếu mã.
    """
    from PIL import Image, ImageDraw

    value = re.sub(r"[^0-9A-Z\-. $/+%]", "", str(code or "").upper()) or "0"
    chars = "*" + value + "*"
    narrow, wide, quiet, height = 4, 12, 40, 96
    widths = []
    for pos, char in enumerate(chars):
        widths.extend(wide if unit == "w" else narrow for unit in CODE39[char])
        if pos < len(chars) - 1:
            widths.append(narrow)  # khoảng cách chuẩn giữa hai ký tự Code 39
    image = Image.new("1", (quiet * 2 + sum(widths), height), 1)
    draw, cursor = ImageDraw.Draw(image), quiet
    for pos, char in enumerate(chars):
        for index, unit in enumerate(CODE39[char]):
            width = wide if unit == "w" else narrow
            if index % 2 == 0:
                draw.rectangle((cursor, 0, cursor + width - 1, height - 1), fill=0)
            cursor += width
        if pos < len(chars) - 1:
            cursor += narrow
    buffer = BytesIO()
    image.save(buffer, format="PNG", optimize=False)
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


def svg_qr(noi_dung):
    """QR (SVG data URI) của MÃ PHIẾU, in trên cuống tiệm giữ của Giấy cầm đồ.

    SVG chứ không PNG: cuống chỉ rộng ~55mm nên ô QR chỉ quanh 17mm — PNG ở cỡ đó bị trình duyệt
    nội suy thành mép xám lúc in, máy quét 2D đọc chậm hẳn. SVG là vector, máy in rasterise ở đúng
    DPI của nó. Đây cũng là nếp ĐÃ CHẠY THẬT của phiếu đặt cọc (apps/pos/deposit_print_config.py).

    ⚠ KHÁC mã vạch Code 39 ở một điểm QUAN TRỌNG: QR mang NGUYÊN mã phiếu KỂ CẢ CHỮ CÁI
    (`KH22609020810`), không phải chỉ phần chữ số. Bảng CODE39 ở trên chỉ có chữ số nên mã vạch
    buộc phải bỏ tiền tố và tra ngược theo PHẦN SỐ; QR thì quét ra đúng mã phiếu, khỏi suy đoán
    tiền tố — hai thứ bổ cho nhau chứ không thay nhau.

    Đầu vào rỗng → trả '' (KHÔNG vẽ QR của chuỗi rỗng). Khác hẳn png_code39 vốn cố ý vẽ mã số 0 để
    trang in không bao giờ chết: QR rỗng quét ra rỗng, nhân viên sẽ tưởng máy quét hỏng và loay
    hoay với cái máy thay vì nhìn ra tờ phiếu thiếu mã.
    """
    ma = str(noi_dung or "").strip()
    if not ma:
        return ""
    import segno

    return segno.make(ma, micro=False).svg_data_uri()
# ═══ HẾT VÙNG SAO CHÉP ═══
# ⚠ MỐC TRÊN LÀ BIÊN CỦA VÙNG KHCD CHÉP LẠI (khcd/ma_vach.py so sha256 hai vùng, lệch là TRƯỢT).
# Từ đây xuống là mã RIÊNG của KHBL — thêm/sửa thoải mái, KHCD không chép. Trước 16/09/2026 vùng
# sao chép được hiểu là "từ CODE39 tới HẾT TỆP": viết thêm một hàm ở cuối tệp là bài kiểm bên KHCD
# đỏ ngay dù không ai đụng vào thuật toán. Có mốc rồi thì hai việc đó tách hẳn nhau.


def anh_ma_vach(ma_phieu):
    """Mã phiếu → data URI PNG Code 39; '' nếu mã không có chữ số nào hoặc dựng ảnh hụt.

    ⚠ BẢN SONG SINH của ``khcd/ma_vach.anh_ma_vach`` — trang xem trước và trang in THẬT phải quyết
    định CÓ VẼ HAY KHÔNG giống hệt nhau. ``png_code39('')`` cố ý vẽ mã của số 0 (trang in không
    bao giờ được chết vì một phiếu thiếu mã), nên nếu gọi thẳng nó thì bản xem trước hiện một mã
    vạch trong khi tờ in thật bỏ trống — GĐ căn theo cái không có ngoài đời.
    Lỗi dựng ảnh cũng nuốt tại chỗ: MẤT MÃ VẠCH CÒN HƠN MẤT TRANG.
    """
    so = so_ma_vach(ma_phieu)
    if not so:
        return ""
    try:
        return png_code39(so)
    except Exception:
        return ""


# ───────────────────────── BỀ RỘNG VẠCH — THỨ QUYẾT ĐỊNH QUÉT ĐƯỢC HAY KHÔNG ─────────────────────
# Hai ngưỡng, CỐ Ý không gộp làm một:
#   · 0,19 mm (7,5 mil) = mức THOẢI MÁI của máy quét CCD/laser cầm tay. Dưới mức này vẫn thường đọc
#     được nhưng PHẢI QUÉT THỬ THẬT ⇒ nhắc nhẹ, KHÔNG báo động.
#   · 0,15 mm (6 mil) = mức máy quét quầy phổ thông bắt đầu chịu thua ⇒ phải sửa TRƯỚC khi in.
# Một ngưỡng duy nhất thì mọi tờ in đều kêu, vài hôm là không ai đọc cảnh báo nào nữa.
# ⚠ Bề rộng vạch THAY ĐỔI THEO ĐỘ DÀI MÃ PHIẾU: cùng một khối mà mã dài thêm 2 chữ số là vạch mỏng
# đi ~8% ⇒ phải đo theo TỪNG phiếu, không đo một lần rồi tin mãi.
VACH_HEP_CAN_THU_MM = 0.19
VACH_HEP_TOI_THIEU_MM = 0.15


def vach_hep_mm(ma_phieu, rong_phan_tram, giay_rong_mm=210.0):
    """Bề rộng VẠCH HẸP tính bằng mm khi ảnh mã vạch bị kéo cho vừa khối rộng ``rong_phan_tram`` %.

    Trả 0.0 khi mã không có chữ số nào (tờ đó không in vạch, khỏi báo động).
    """
    so = so_ma_vach(ma_phieu)
    if not so:
        return 0.0
    # Mỗi ký tự Code 39 = 6 vạch hẹp + 3 vạch rộng (=3 hẹp) = 15 đơn vị hẹp, cộng 1 hẹp ngăn cách.
    # Chuỗi gồm start + dữ liệu + stop, cộng quiet-zone 10 đơn vị mỗi bên (đúng như png_code39 vẽ).
    ky_tu = len(so) + 2
    don_vi = ky_tu * 15 + (ky_tu - 1) + 20
    try:
        rong_mm = float(giay_rong_mm) * float(rong_phan_tram) / 100.0
    except (TypeError, ValueError):
        return 0.0
    return rong_mm / don_vi if don_vi else 0.0


def canh_bao_vach(ma_phieu, rong_phan_tram, giay_rong_mm=210.0):
    """Trả (mức, câu nhắc) — mức là "" · "nhac" · "nang". Dùng chung cho trang cấu hình và trang in."""
    mm = vach_hep_mm(ma_phieu, rong_phan_tram, giay_rong_mm)
    if not mm:
        return "", ""
    if mm < VACH_HEP_TOI_THIEU_MM:
        return "nang", (f"Vạch hẹp chỉ {mm:.3f} mm — DƯỚI mức {VACH_HEP_TOI_THIEU_MM} mm, máy quét "
                        "quầy nhiều khả năng đọc không ra. Nới ô Rộng % hoặc chuyển mã vạch xuống "
                        "chỗ rộng hơn TRƯỚC khi in.")
    if mm < VACH_HEP_CAN_THU_MM:
        return "nhac", (f"Vạch hẹp {mm:.3f} mm — dưới mức thoải mái {VACH_HEP_CAN_THU_MM} mm. "
                        "Vẫn thường đọc được, nhưng hãy QUÉT THỬ tờ in đầu tiên trước khi dùng thật.")
    return "", ""
