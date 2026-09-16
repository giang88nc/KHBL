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
}


def so_ma_vach(ma):
    """SỐ mà mã vạch mang theo = BỎ mọi ký tự không phải chữ số, GIỮ NGUYÊN thứ tự, KHÔNG rút gọn.

    MÃ PHIẾU THẬT ĐANG CHẠY — đếm trên sổ KHCD ngày 16/09/2026, 25.465 mã khác nhau:
        'KH22609020810'   → '22609020810'    11 chữ số · 18.854 mã — đường cầm đồ hằng ngày
        'CU2606000000625' → '2606000000625'  13 chữ số ·  6.594 mã — phiếu chuyển từ hệ cũ
      lác đác vài mã 9 / 10 / 12 chữ số. Bản xem trước bên KHBL dùng mã GIẢ 'CD26090100012' (11 số,
      đúng độ dài thường gặp) — đừng đọc 'CD…' thành tiền tố thật.

    ⚠ ĐÂY LÀ HỢP ĐỒNG GIỮA KHBL VÀ KHCD. Hai bên phải sinh RA ĐÚNG MỘT chuỗi cho cùng một mã phiếu,
    nếu không thì bản xem trước một đằng, tờ in ra một nẻo.

    VÌ SAO BỎ CHỮ CÁI chứ không mã hoá chúng: bảng `CODE39` ở trên chỉ có chữ số (bản chép nguyên đã
    quét được thực tế), thêm A-Z vào là ĐỘNG VÀO THUẬT TOÁN ĐANG CHẠY THẬT của Giấy đảm bảo.
    VÌ SAO KHÔNG RÚT GỌN 9 số như `_ma_gdb()` của Giấy đảm bảo: mã cầm đồ dài 11–13 chữ số, cắt còn
    9 là VỨT BỎ thông tin ⇒ hai phiếu khác nhau có thể ra cùng một mã vạch. Chứng từ cầm đồ là giấy
    tờ pháp lý, quét nhầm phiếu là trả nhầm hàng.

    TRA NGƯỢC VỀ ĐÚNG PHIẾU: bỏ chữ cái xong vẫn phải còn DUY NHẤT. Cách tra an toàn bên KHCD là so
    theo PHẦN SỐ chứ đừng ghép chuỗi tiền tố + số:
        ... WHERE REPLACE(...) -- hoặc lọc trong Python: so_ma_vach(row.sku) == so_quet_duoc
    ⚠ ĐIỀU KIỆN PHẢI GIỮ: PHẦN SỐ của mọi mã phiếu không được trùng nhau. Kho hiện có HAI tiền tố
    ('KH…' và 'CU…') chứ không phải một — đã soát cả 25.465 mã: bỏ chữ cái rồi KHÔNG còn cặp nào
    trùng (hai nhóm khác độ dài). Ngày nào sinh thêm loại mã mà phần số đụng mã đang có (ví dụ
    'GH22609020810' cho gia hạn) thì hai tờ giấy mang CÙNG một mã vạch — lúc đó phải đổi sang mã
    vạch có chữ cái, không vá bằng cách đoán tiền tố.
    ⚠ NHÓM 'CU…' 13 số là nhóm SÁT NGƯỠNG nhất: với bề rộng khối mặc định, vạch hẹp chỉ còn
    ≈ 0,152 mm — nhỉnh hơn ngưỡng đỏ 0,15 mm một chút (xem `canh_bao_vach`). Quét thử một tờ 'CU…'
    trước khi in loạt.
    """
    return re.sub(r"\D", "", str(ma or ""))


def png_code39(code):
    """PNG Code 39 chuẩn, có start/stop và quiet-zone cho máy quét mã vạch.

    Không dùng các ``div`` CSS để browser không co vạch lẻ thành pixel mờ khi in.

    Đầu vào rỗng / không có chữ số nào → vẽ mã của số '0' (ảnh hợp lệ, KHÔNG ném lỗi): trang xem
    trước và trang in không bao giờ được chết chỉ vì một phiếu thiếu mã.
    """
    from PIL import Image, ImageDraw

    value = re.sub(r"[^0-9]", "", str(code or "")) or "0"
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
