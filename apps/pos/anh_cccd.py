"""
Tách thẻ CCCD ra khỏi ảnh chụp (OpenCV) — GĐ chốt 08/09/2026 tối; viết lại lần 2 sau khi đối chiếu ảnh cắt chuẩn
CCCD-cut.jpg (1162×722): bản 1 lấy mặt nạ ĐẦU TIÊN có tứ giác + approxPolyDP → góc bo bị cắt vát (phạm viền), ảnh
màn hình VNeID (thẻ nhạt trên nền trắng có hoa văn) không tìm thấy, contour thưa → mất phối cảnh.

Thuật toán (mọi bước trên ảnh thu ≤1200px, tọa độ trả về theo ảnh gốc):
  A. BIÊN chung = Canny(xám) ∪ Canny(khoảng cách màu Lab so với NỀN) → bắt được cả viền thẻ nhạt trên nền trắng.
  B. ỨNG VIÊN KHỐI: nhiều mặt nạ "vật thể ≠ nền" (ΔE Lab >8 / >16 / Otsu · đổ tràn từ viền ảnh · bão hòa >28 / Otsu ·
     xám Otsu 2 chiều · Canny đóng · ngưỡng thích nghi) → contour ngoài DÀY (CHAIN_APPROX_NONE) → minAreaRect →
     KHỚP ĐƯỜNG THẲNG 4 CẠNH (fitLine Huber trên điểm bám cạnh, bỏ 10 % hai đầu = bo góc) → giao nhau = 4 góc thật
     (không phạm bo góc, giữ phối cảnh).
  C. ỨNG VIÊN ĐƯỜNG THẲNG: HoughLinesP trên biên → 2 nhóm hướng vuông góc → gộp đoạn cùng đường → ghép 2+2 đường
     thành tứ giác (cứu cảnh hoa văn nối thẻ với chữ làm mặt nạ khối hỏng).
  D. CHẤM ĐIỂM chung mọi ứng viên = ĐỘ PHỦ BIÊN CÓ HƯỚNG (điểm biên phải có gradient vuông góc cạnh — chữ/hoa văn/vân
     khay không giả làm cạnh; 0,6 trung bình + 0,4 cạnh yếu THỨ NHÌ; mép ảnh tính nửa) × độ đầy (khối) × gần tỉ lệ ID-1
     (85,6/53,98 = 1,586; mềm vì phối cảnh — chụp chếch mạnh co tới ~2,0) × diện tích^0,15 × TƯƠNG PHẢN 2 BÊN CẠNH
     (trong thẻ ≠ ngoài nền; đường cắt ngang thẻ / chạy trên khay có 2 bên cùng màu → thấp) × màu XANH bên trong (nền
     CCCD/CMND, hue 60–130) × (1 − 0,4 × tỉ lệ điểm GIỐNG NỀN bên trong) × TÁCH MÀU (điểm xanh bên trong nhiều hơn dải
     bao ngoài 12 % — tứ giác con nằm trong thẻ hay ôm thêm khay đều ≈ nửa điểm). Điểm < NGUONG_DIEM → không thẻ.
     Cắt tỉa: phần rẻ không vượt được ứng viên tốt nhất → bỏ qua fillPoly đắt.
  D2. Tứ giác THẮNG được NẮN LẠI từng cạnh theo điểm biên thật (fitLine Huber, dải hẹp quanh cạnh) → viền chính xác;
     cạnh sát mép ảnh cùng màu thẻ → đẩy ra mép (thẻ bị khung cắt); ảnh có tỉ lệ thẻ + tứ giác ≥80 % → giữ trọn khung. Diện tích 4–100 % ảnh;
     ≥98,5 % chỉ nhận khi tỉ lệ 1,50–1,70 (ảnh đã cắt sát) → dùng trọn khung; thẻ thẳng: 4 mép ảnh cũng là đường ứng viên.
  E. Nới 0,4 % ra ngoài; nắn phối cảnh về khổ CHUẨN 1170×738 (≈ ảnh cắt chuẩn GĐ); thẻ dọc tự xoay ngang;
     `goc` = độ người dùng KÉO CON TRỎ (theo chiều kim đồng hồ): bội 90° xoay không mất nét (90/270 → ảnh dọc),
     góc lẻ → xoay quanh tâm rồi cắt hình chữ nhật nội tiếp cùng tỉ lệ.
Trả bytes JPEG. Không thấy thẻ → KhongThayThe.
"""
import math

CHUAN_W, CHUAN_H = 1170, 738           # ≈ CCCD-cut.jpg 1162×722 của GĐ · tỉ lệ ID-1
TI_LE = 85.6 / 53.98                   # 1,586


class KhongThayThe(ValueError):
    pass


def _order(pts):
    """4 điểm → (trên-trái, trên-phải, dưới-phải, dưới-trái)."""
    import numpy as np
    pts = np.array(pts, dtype="float32").reshape(4, 2)
    s = pts.sum(axis=1)
    d = np.diff(pts, axis=1).ravel()
    return np.array([pts[np.argmin(s)], pts[np.argmin(d)], pts[np.argmax(s)], pts[np.argmax(d)]], dtype="float32")


# ───────────────────────────── A. biên chung ─────────────────────────────
def _lab_nen(small):
    """Khoảng cách màu Lab từng điểm so với NỀN (median dải viền 4 %) → uint8 0–255 (0 = giống nền)."""
    import cv2
    import numpy as np
    h, w = small.shape[:2]
    lab = cv2.cvtColor(small, cv2.COLOR_BGR2LAB).astype(np.float32)
    b = max(6, int(0.04 * min(h, w)))
    vien = np.concatenate([lab[:b].reshape(-1, 3), lab[-b:].reshape(-1, 3), lab[:, :b].reshape(-1, 3), lab[:, -b:].reshape(-1, 3)])
    nen = np.median(vien, axis=0)
    dist = np.linalg.norm(lab - nen, axis=2)
    return dist, np.clip(dist * 2.0, 0, 255).astype(np.uint8)       # ×2: ΔE 8 → 16, ΔE 127 → 255


def _bien(small, gray_blur, d8):
    """(biên mảnh, biên giãn 3×3) — Canny xám ∪ Canny khoảng-cách-màu."""
    import cv2
    e = cv2.Canny(gray_blur, 30, 90) | cv2.Canny(cv2.GaussianBlur(d8, (5, 5), 0), 16, 48)   # 16/48: viền thẻ nhạt ΔE≈6 trên nền trắng
    return e, cv2.dilate(e, cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3)), iterations=1)


# ───────────────────────────── B. mặt nạ khối ─────────────────────────────
def _mat_na(small, gray_blur, dist, d8):
    import cv2
    import numpy as np
    h, w = small.shape[:2]
    ke3 = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
    ke5 = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
    ke9 = cv2.getStructuringElement(cv2.MORPH_RECT, (9, 9))

    def khoi(m):
        return cv2.morphologyEx(cv2.morphologyEx(m, cv2.MORPH_CLOSE, ke9, iterations=2), cv2.MORPH_OPEN, ke5)

    out = []
    for nguong in (8, 16):
        out.append((f"lab{nguong}", khoi((dist > nguong).astype(np.uint8) * 255)))
    _, m = cv2.threshold(cv2.GaussianBlur(d8, (5, 5), 0), 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    out.append(("lab_otsu", khoi(m)))
    # đổ tràn nền từ 8 điểm mép ảnh (phạm vi cố định ±10 BGR) → vật thể = phần không tràn tới
    bgr = cv2.GaussianBlur(small, (5, 5), 0)
    ff = np.zeros((h + 2, w + 2), np.uint8)
    co = 4 | cv2.FLOODFILL_MASK_ONLY | cv2.FLOODFILL_FIXED_RANGE | (255 << 8)
    for sx, sy in ((2, 2), (w - 3, 2), (2, h - 3), (w - 3, h - 3), (w // 2, 2), (w // 2, h - 3), (2, h // 2), (w - 3, h // 2)):
        if not ff[sy + 1, sx + 1]:
            cv2.floodFill(bgr, ff, (sx, sy), 0, (10, 10, 10), (10, 10, 10), co)
    out.append(("tran", khoi(255 - ff[1:-1, 1:-1])))
    s = cv2.GaussianBlur(cv2.cvtColor(small, cv2.COLOR_BGR2HSV)[:, :, 1], (5, 5), 0)
    out.append(("sat28", khoi((s > 28).astype(np.uint8) * 255)))
    _, m = cv2.threshold(s, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    out.append(("sat_otsu", khoi(m)))
    _, m = cv2.threshold(gray_blur, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    out.append(("otsu_sang", cv2.morphologyEx(m, cv2.MORPH_CLOSE, ke9, iterations=2)))
    out.append(("otsu_toi", cv2.morphologyEx(255 - m, cv2.MORPH_CLOSE, ke9, iterations=2)))
    e = cv2.dilate(cv2.Canny(gray_blur, 30, 100), ke3, iterations=2)
    out.append(("canny", cv2.morphologyEx(e, cv2.MORPH_CLOSE, ke5, iterations=2)))
    a = cv2.adaptiveThreshold(gray_blur, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 31, 7)
    out.append(("thich_nghi", cv2.morphologyEx(a, cv2.MORPH_CLOSE, ke5, iterations=2)))
    return out


def _khop_canh(c, rect):
    """Góc thật = giao 2 đường thẳng khớp trên điểm contour bám mỗi cạnh minAreaRect (bỏ 10 % hai đầu = bo góc).
    Trả 4 góc đã order hoặc None (không tin được → dùng minAreaRect)."""
    import cv2
    import numpy as np
    pts = c.reshape(-1, 2).astype(np.float32)
    box = _order(cv2.boxPoints(rect))
    canh_ngan = min(rect[1])
    lines = []
    for i in range(4):
        p, q = box[i], box[(i + 1) % 4]
        d = q - p
        L = float(np.linalg.norm(d))
        if L < 2:
            return None
        d = d / L
        n = np.array([-d[1], d[0]], dtype=np.float32)
        rel = pts - p
        t = rel @ d
        kc = np.abs(rel @ n)
        chon = (kc <= max(3.0, 0.04 * canh_ngan)) & (t >= 0.10 * L) & (t <= 0.90 * L)
        P = pts[chon]
        if len(P) < 8:
            lines.append((p, d))
            continue
        vx, vy, x0, y0 = cv2.fitLine(P, cv2.DIST_HUBER, 0, 0.01, 0.01).ravel()
        lines.append((np.array([x0, y0], dtype=np.float32), np.array([vx, vy], dtype=np.float32)))
    goc = []
    for i in range(4):
        (p1, d1), (p2, d2) = lines[i - 1], lines[i]
        A = np.array([[d1[0], -d2[0]], [d1[1], -d2[1]]], dtype=np.float64)
        if abs(np.linalg.det(A)) < 1e-6:
            return None
        a = np.linalg.solve(A, (p2 - p1).astype(np.float64))[0]
        goc.append(p1 + a * d1)
    quad = np.array(goc, dtype=np.float32)
    if not cv2.isContourConvex(quad.reshape(-1, 1, 2)):
        return None
    if any(np.linalg.norm(quad[i] - box[i]) > 0.15 * canh_ngan for i in range(4)):
        return None
    aq = cv2.contourArea(quad)
    if not 0.7 * rect[1][0] * rect[1][1] <= aq <= 1.3 * rect[1][0] * rect[1][1]:
        return None
    return _order(quad)


# ───────────────────────────── D. chấm điểm ─────────────────────────────
def _do_phu(quad, ctx):
    """Độ phủ biên TỪNG CẠNH: 60 điểm/cạnh — 1,0 nếu trong ±2 px dọc pháp tuyến có điểm biên (Canny thô) mà hướng gradient
    vuông góc cạnh (±30°: biên thật của thẻ; xét lân cận vì viền thẻ là đường mảnh — gradient ngay tâm đường ≈ 0, hướng nhiễu),
    0,1 nếu có biên (giãn 5×5) nhưng hướng lệch (chữ, hoa văn, vân khay), 0,5 nếu nằm trên mép ảnh (thẻ cắt sát mép).
    Trả (trung bình 4 cạnh, mảng 4 cạnh đã sắp tăng dần, số cạnh nằm trên mép ảnh)."""
    import numpy as np
    q = _order(quad)
    h, w = ctx["h"], ctx["w"]
    bien, bien_gian, gdir = ctx["bien"], ctx["bien_gian"], ctx["gdir"]
    t = (np.arange(60) + 0.5) / 60
    phu = np.zeros(4)
    n_mep = 0
    for i in range(4):
        p0, p1 = q[i], q[(i + 1) % 4]
        pts = p0[None, :] + t[:, None] * (p1 - p0)[None, :]
        x = np.clip(np.rint(pts[:, 0]).astype(int), 0, w - 1)
        y = np.clip(np.rint(pts[:, 1]).astype(int), 0, h - 1)
        mep = (pts[:, 0] <= 3) | (pts[:, 0] >= w - 4) | (pts[:, 1] <= 3) | (pts[:, 1] >= h - 4)
        if mep.mean() > 0.5:
            n_mep += 1
        co = bien_gian[y, x] > 0
        d = p1 - p0
        L = float(np.hypot(d[0], d[1])) or 1.0
        nx, ny = -d[1] / L, d[0] / L                                  # pháp tuyến đơn vị
        phap = math.atan2(ny, nx)
        dung = np.zeros(60, dtype=bool)
        for k in (-2, -1, 0, 1, 2):
            xk = np.clip(np.rint(pts[:, 0] + k * nx).astype(int), 0, w - 1)
            yk = np.clip(np.rint(pts[:, 1] + k * ny).astype(int), 0, h - 1)
            dung |= (bien[yk, xk] > 0) & (np.abs(np.cos(gdir[yk, xk] - phap)) >= 0.866)
        phu[i] = np.where(dung, 1.0, np.where(co, 0.1, np.where(mep, 0.5, 0.0))).mean()
    return float(phu.mean()), np.sort(phu), n_mep


def _noi_dung(quad, ctx):
    """Bên trong tứ giác: (tỉ lệ điểm XANH — màu nền CCCD/CMND, hue 60–130 & S>24), (tỉ lệ điểm GIỐNG NỀN ΔE<10)."""
    import cv2
    import numpy as np
    mk = np.zeros((ctx["h"], ctx["w"]), np.uint8)
    cv2.fillPoly(mk, [np.rint(_order(quad)).astype(np.int32).reshape(-1, 1, 2)], 255)
    n = int(cv2.countNonZero(mk))
    if n < 50:
        return 0.0, 1.0
    trong = mk > 0
    return float((ctx["xanh"] & trong).sum() / n), float((ctx["nen"] & trong).sum() / n)


def _vien_xanh(quad, ctx):
    """VIỀN XANH từng cạnh: dải sát TRONG cạnh (4 % và 8 % cạnh ngắn vào trong) phải là màu thẻ, dải sát NGOÀI (5 % ra ngoài)
    thì không — hiệu trong − ngoài từng cạnh (điểm ngoài ảnh = không xanh). Tứ giác con nằm trong thẻ: ngoài cũng xanh → 0;
    tứ giác ôm thêm khay: cạnh chạy trên khay có trong không xanh → 0. Trả mảng 4 hiệu đã sắp tăng dần."""
    import numpy as np
    q = _order(quad)
    h, w, xanh = ctx["h"], ctx["w"], ctx["xanh"]
    ngan = min(np.linalg.norm(q[1] - q[0]), np.linalg.norm(q[3] - q[0]))
    t = (np.arange(40) + 0.5) / 40
    hieu = np.zeros(4)

    def ti_le(pts):
        x, y = np.rint(pts[:, 0]).astype(int), np.rint(pts[:, 1]).astype(int)
        ok = (x >= 0) & (x < w) & (y >= 0) & (y < h)
        if not ok.any():
            return 0.0
        return float(xanh[y[ok], x[ok]].sum() / len(x))

    for i in range(4):
        p0, p1 = q[i], q[(i + 1) % 4]
        d = p1 - p0
        L = np.linalg.norm(d)
        if L < 1:
            return hieu
        n = np.array([d[1], -d[0]]) / L                              # pháp tuyến hướng RA NGOÀI (y xuống; tl→tr→br→bl thuận kim đồng hồ)
        pts = p0[None, :] + t[:, None] * d[None, :]
        trong = (ti_le(pts - n * 0.04 * ngan) + ti_le(pts - n * 0.08 * ngan)) / 2
        ngoai = ti_le(pts + n * 0.05 * ngan)
        hieu[i] = trong - ngoai
    return np.sort(hieu)


def _ti_le_quad(quad):
    import numpy as np
    q = _order(quad)
    a = (np.linalg.norm(q[1] - q[0]) + np.linalg.norm(q[2] - q[3])) / 2
    b = (np.linalg.norm(q[3] - q[0]) + np.linalg.norm(q[2] - q[1])) / 2
    if min(a, b) <= 0:
        return 0
    return max(a, b) / min(a, b)


def _tuong_phan(quad, ctx):
    """TƯƠNG PHẢN 2 BÊN từng cạnh: 40 điểm/cạnh, so màu Lab tại ±off (2 % cạnh ngắn, ≥4 px) hai bên cạnh — ΔE/20 kẹp 1.
    Cạnh thật của thẻ: trong (thẻ) ≠ ngoài (nền); đường cắt ngang thẻ hay chạy trên khay: 2 bên cùng màu → thấp.
    Cạnh nằm trên MÉP ẢNH (thẻ cắt sát) không đo được → 0,6 trung tính. Trả (trung bình, mảng 4 cạnh sắp tăng dần)."""
    import numpy as np
    q = _order(quad)
    h, w, lab = ctx["h"], ctx["w"], ctx["lab"]
    ngan = min(np.linalg.norm(q[1] - q[0]), np.linalg.norm(q[3] - q[0]))
    off = max(4.0, 0.02 * ngan)
    t = (np.arange(40) + 0.5) / 40
    tp = np.zeros(4)
    for i in range(4):
        p0, p1 = q[i], q[(i + 1) % 4]
        d = p1 - p0
        L = np.linalg.norm(d)
        if L < 1:
            return 0.0, tp
        n = np.array([-d[1], d[0]]) / L * off                        # pháp tuyến (hướng ra ngoài với thứ tự tl→tr→br→bl)
        pts = p0[None, :] + t[:, None] * d[None, :]
        mep = (pts[:, 0] <= 3) | (pts[:, 0] >= w - 4) | (pts[:, 1] <= 3) | (pts[:, 1] >= h - 4)
        if mep.mean() > 0.5:
            tp[i] = 0.6
            continue
        trong, ngoai = pts - n[None, :], pts + n[None, :]
        xi = np.clip(np.rint(trong[:, 0]).astype(int), 0, w - 1)
        yi = np.clip(np.rint(trong[:, 1]).astype(int), 0, h - 1)
        xo = np.clip(np.rint(ngoai[:, 0]).astype(int), 0, w - 1)
        yo = np.clip(np.rint(ngoai[:, 1]).astype(int), 0, h - 1)
        de = np.linalg.norm(lab[yi, xi] - lab[yo, xo], axis=1)
        tp[i] = np.minimum(de / 20.0, 1.0).mean()
    return float(tp.mean()), np.sort(tp)


def _diem(quad, day, ctx):
    """Điểm ứng viên (0 = loại). Phần RẺ tính trước; nếu cận trên không vượt được ứng viên tốt nhất đã có → bỏ qua phần đắt."""
    import cv2
    aq = cv2.contourArea(_order(quad))
    if aq <= 0:
        return 0.0
    r = _ti_le_quad(quad)
    if not 1.2 <= r <= 2.2:                        # ảnh chụp chếch mạnh co chiều cao thẻ tới ~2,0 (nắn phối cảnh sẽ trả lại 1,586)
        return 0.0
    tl = aq / ctx["dien_tich"]
    if tl < 0.04:
        return 0.0
    gan = 1 - min(abs(r - TI_LE) / 0.8, 1.0)
    phu_tb, phu_canh, n_mep = _do_phu(quad, ctx)
    if n_mep >= 3:                                 # 3–4 cạnh nằm trên mép = TRỌN KHUNG: chỉ khi chính ảnh đã là thẻ cắt sát
        ra = max(ctx["w"], ctx["h"]) / min(ctx["w"], ctx["h"])          # tỉ lệ CHÍNH ẢNH (loại giấy A4 √2 = 1,414 / ảnh 3:2, 16:9)
        return DIEM_TRON_KHUNG if (tl > 0.9 and 1.50 <= ra <= 1.70) else 0.0
    phu = 0.6 * phu_tb + 0.4 * phu_canh[1]         # cạnh yếu THỨ NHÌ: cho phép đúng 1 cạnh nhạt (viền trên thẻ VNeID), 2 cạnh yếu → phạt
    tp_tb, tp_canh = _tuong_phan(quad, ctx)
    tp = 0.6 * tp_tb + 0.4 * tp_canh[1]
    re = phu * day * (0.6 + 0.4 * gan) * tl ** 0.10 * (0.3 + 0.7 * tp)
    if re <= ctx.get("best", 0.0):                 # các hệ số còn lại ≤ 1 → không thể thắng
        return 0.0
    vien = _vien_xanh(quad, ctx)
    vx = 0.6 + 0.4 * min(max(vien[1] / 0.3, 0.0), 1.0)   # cạnh yếu thứ nhì: cho phép 1 cạnh sát mép ảnh / bị che
    re *= vx
    if re <= ctx.get("best", 0.0):
        return 0.0
    xanh, nen = _noi_dung(quad, ctx)
    diem = re * (0.7 + 0.3 * xanh) * (1 - 0.4 * nen)
    ctx["best"] = max(ctx.get("best", 0.0), diem)
    return diem


def _ung_vien_khoi(mask, ten, ctx):
    import cv2
    kq = []
    dien_tich = ctx["dien_tich"]
    cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    for c in sorted(cnts, key=cv2.contourArea, reverse=True)[:6]:
        area = cv2.contourArea(c)
        if area < 0.04 * dien_tich:
            continue
        hull = cv2.convexHull(c)
        rect = cv2.minAreaRect(hull)
        rw, rh = rect[1]
        if min(rw, rh) < 2:
            continue
        rr = max(rw, rh) / min(rw, rh)
        if not 1.2 <= rr <= 2.0:
            continue
        quad = _khop_canh(c, rect)
        if quad is None:
            quad = _order(cv2.boxPoints(rect))
        aq = cv2.contourArea(quad)
        if aq <= 0:
            continue
        day = min(1.0, area / aq)
        if day < 0.85:
            continue
        diem = _diem(quad, day, ctx)
        if diem > 0:
            kq.append((diem, quad, ten))
    return kq


# ───────────────────────────── C. ứng viên đường thẳng ─────────────────────────────
def _giao(l1, l2):
    """Giao 2 đường dạng pháp tuyến (rho, phi): x cosφ + y sinφ = ρ."""
    import numpy as np
    (r1, f1), (r2, f2) = l1, l2
    A = np.array([[math.cos(f1), math.sin(f1)], [math.cos(f2), math.sin(f2)]])
    if abs(np.linalg.det(A)) < 1e-6:
        return None
    return np.linalg.solve(A, np.array([r1, r2]))


def _ung_vien_duong(ctx):
    """Ghép 4 đường Hough (2 nhóm hướng vuông góc) thành tứ giác thẻ. Thử tối đa 3 HƯỚNG CHÍNH (đỉnh histogram góc gập 90°)
    — thẻ đặt trên khay/cạnh giấy nghiêng khác hướng vẫn có nhóm đường riêng."""
    import cv2
    import numpy as np
    bien = ctx["bien"]
    h, w = bien.shape[:2]
    mn = min(h, w)
    segs = cv2.HoughLinesP(bien, 1, np.pi / 360, threshold=50, minLineLength=int(0.15 * mn), maxLineGap=int(0.02 * max(h, w)))
    if segs is None:
        return []
    segs = segs.reshape(-1, 4).astype(np.float64)
    dx, dy = segs[:, 2] - segs[:, 0], segs[:, 3] - segs[:, 1]
    L = np.hypot(dx, dy)
    ang = np.degrees(np.arctan2(dy, dx)) % 180.0                     # hướng đoạn 0–180
    # hướng chính = các đỉnh histogram có trọng số độ dài, GẬP 90° (hướng a và a+90 chung bin), bin 3°, vòng tròn
    bins = np.zeros(30)
    for a, l in zip(ang % 90.0, L):
        bins[int(a // 3) % 30] += l
    bins = bins + np.roll(bins, 1) + np.roll(bins, -1)
    dinh = []
    for i in np.argsort(-bins):
        if bins[i] < 0.2 * bins.max():
            break
        if all(min(abs(i - j), 30 - abs(i - j)) >= 5 for j in dinh):
            dinh.append(int(i))
        if len(dinh) == 3:
            break

    def nhom(goc_tam):
        """Đường [ρ, φ, tổng dài, đoạn dài nhất] của các đoạn lệch ≤12° so với goc_tam, gộp theo ρ. Mọi đường trong nhóm đo ρ
        theo CÙNG pháp tuyến chuẩn φ0 = goc_tam+90° — đoạn 179,x° (pháp tuyến ngược chiều đoạn 0,x°) được lật (−ρ, φ−π),
        nếu không cùng 1 cạnh thẻ bị tách đôi trái dấu (ảnh chụp màn hình từng báo 'không thấy thẻ' vì vậy)."""
        d = np.abs((ang - goc_tam + 90) % 180 - 90)                # lệch góc vòng tròn
        idx = np.where(d <= 12)[0]
        phi0 = math.radians(goc_tam + 90.0)
        duong = []
        for i in idx:
            phi = math.radians(ang[i] + 90.0)                        # pháp tuyến
            mx, my = (segs[i, 0] + segs[i, 2]) / 2, (segs[i, 1] + segs[i, 3]) / 2
            rho = mx * math.cos(phi) + my * math.sin(phi)
            if math.cos(phi - phi0) < 0:
                rho, phi = -rho, phi - math.pi
            duong.append([rho, phi, L[i], L[i]])
        duong.sort(key=lambda z: -z[2])                              # đoạn dài làm hạt nhân đường
        gop = []
        tol_rho, tol_phi = 0.012 * max(h, w), math.radians(3.0)
        for rho, phi, l, lmax in duong:                              # gộp đoạn CÙNG đường: Δρ ≤ 1.2 % cạnh dài ảnh VÀ Δφ ≤ 3°
            for g in gop:                                            # (chỉ theo ρ thì cung hoa văn lệch 5–10° trộn vào cạnh thẻ → đường xiên)
                if abs(rho - g[0]) <= tol_rho and abs(phi - g[1]) <= tol_phi:
                    g[2] += l                                        # (ρ, φ) GIỮ của đoạn dài nhất (hạt nhân) — không lấy trung bình,
                    g[3] = max(g[3], lmax)                           # kẻo viền panel cách 6 px kéo cạnh thẻ lệch (ảnh VNeID từng bị)
                    break
            else:
                gop.append([rho, phi, l, lmax])
        gop = [g for g in gop if g[2] >= 0.12 * mn]
        # cạnh thẻ = 1 đoạn DÀI; dòng chữ/hoa văn = nhiều đoạn ngắn gom lại → xếp theo đoạn dài nhất + 0,25×tổng
        gop.sort(key=lambda z: -(z[3] + 0.25 * z[2]))
        return gop[:16]

    kq = []

    def them(pa, pb, ten):
        pts = [_giao(pa[0], pb[0]), _giao(pa[0], pb[1]), _giao(pa[1], pb[1]), _giao(pa[1], pb[0])]
        if any(p is None for p in pts):
            return
        quad = np.array(pts, dtype=np.float32)
        if (quad[:, 0] < -0.02 * w).any() or (quad[:, 0] > 1.02 * w).any() or (quad[:, 1] < -0.02 * h).any() or (quad[:, 1] > 1.02 * h).any():
            return
        if not cv2.isContourConvex(_order(quad).reshape(-1, 1, 2)):
            return
        diem = _diem(quad, 1.0, ctx) * (0.97 if ten == "hough" else 0.96)
        if diem > 0:
            kq.append((diem, _order(quad), ten))

    def cap(G):
        return [(G[i][:2], G[j][:2]) for i in range(len(G)) for j in range(i + 1, len(G)) if abs(G[i][0] - G[j][0]) >= 0.15 * mn]

    for i in dinh:
        a0 = (i * 3 + 1.5) % 180.0
        A = nhom(a0)
        B = nhom((a0 + 90.0) % 180.0)
        # thẻ nằm thẳng (hướng chính ≈ 0° hoặc 90°): thêm 4 MÉP ẢNH làm đường ứng viên — Canny không có biên ở 2–3 px
        # cuối ảnh nên thẻ sát mép (ảnh màn hình, ảnh đã cắt sát) mất cạnh
        lech = min(a0 % 90.0, 90.0 - a0 % 90.0)
        if lech <= 8.0:
            phiA, phiB = math.radians(a0 + 90.0), math.radians((a0 + 90.0) % 180.0 + 90.0)
            if abs(a0 - 90.0) > 45.0:                                # nhóm hướng ngang → mép trên/dưới; nhóm dọc → mép trái/phải
                ngang, doc, phi_ngang, phi_doc = A, B, phiA, phiB
            else:
                ngang, doc, phi_ngang, phi_doc = B, A, phiB, phiA
            # đường mép biểu diễn theo pháp tuyến chuẩn của nhóm (cùng hệ quy chiếu ρ với các đường thật)
            for G, diem_mep, phi in ((ngang, ((0.0, 0.0), (0.0, float(h - 1))), phi_ngang), (doc, ((0.0, 0.0), (float(w - 1), 0.0)), phi_doc)):
                for px, py in diem_mep:
                    rho = px * math.cos(phi) + py * math.sin(phi)
                    if not any(abs(g[0] - rho) <= 0.015 * max(h, w) for g in G):
                        G.append([rho, phi, 0.12 * mn, 0.12 * mn])
        if len(A) < 1 or len(B) < 1:
            continue
        capA, capB = cap(A), cap(B)
        for pa in capA:
            for pb in capB:
                them(pa, pb, "hough")
        # 3 đường + suy đường thứ 4 (cạnh nhạt không có biên — vd viền trên thẻ VNeID trên nền trắng):
        # cặp song song cho bề rộng D → cạnh còn lại cách đường đơn D/TI_LE hoặc D×TI_LE (thẻ ngang/dọc), 2 phía
        for pa in capA:
            D = abs(pa[0][0] - pa[1][0])
            for b in B:
                for kc in (D / TI_LE, D * TI_LE):
                    for dau in (1, -1):
                        them(pa, (b[:2], (b[0] + dau * kc, b[1])), "hough3")
        for pb in capB:
            D = abs(pb[0][0] - pb[1][0])
            for a in A:
                for kc in (D / TI_LE, D * TI_LE):
                    for dau in (1, -1):
                        them((a[:2], (a[0] + dau * kc, a[1])), pb, "hough3")
    return kq


def _ngu_canh(small):
    """Tính 1 LẦN mọi bản đồ dùng chung khi chấm hàng nghìn ứng viên: biên (giãn 5×5), HƯỚNG gradient (kênh xám hay ΔE
    nền — kênh nào mạnh hơn tại từng điểm), điểm màu xanh CCCD, điểm giống nền."""
    import cv2
    import numpy as np
    h, w = small.shape[:2]
    gray_blur = cv2.GaussianBlur(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY), (5, 5), 0)
    dist, d8 = _lab_nen(small)
    bien, _ = _bien(small, gray_blur, d8)
    bien_gian = cv2.dilate(bien, cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5)))
    d8b = cv2.GaussianBlur(d8, (5, 5), 0)
    gx1, gy1 = cv2.Sobel(gray_blur, cv2.CV_32F, 1, 0, ksize=3), cv2.Sobel(gray_blur, cv2.CV_32F, 0, 1, ksize=3)
    gx2, gy2 = cv2.Sobel(d8b, cv2.CV_32F, 1, 0, ksize=3), cv2.Sobel(d8b, cv2.CV_32F, 0, 1, ksize=3)
    manh2 = (gx2 * gx2 + gy2 * gy2) > (gx1 * gx1 + gy1 * gy1)
    gdir = np.arctan2(np.where(manh2, gy2, gy1), np.where(manh2, gx2, gx1)).astype(np.float32)
    hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
    xanh = (hsv[:, :, 0] >= 60) & (hsv[:, :, 0] <= 130) & (hsv[:, :, 1] > 24)
    lab = cv2.cvtColor(cv2.GaussianBlur(small, (3, 3), 0), cv2.COLOR_BGR2LAB).astype(np.float32)
    return dict(small=small, gray_blur=gray_blur, dist=dist, d8=d8, bien=bien, bien_gian=bien_gian, gdir=gdir,
                xanh=xanh, nen=dist < 10, lab=lab, h=h, w=w, dien_tich=h * w, best=0.0)


def _tinh_chinh(quad, ctx):
    """NẮN LẠI 4 cạnh của tứ giác thắng theo điểm biên THẬT: điểm Canny trong dải ±2,5 % cạnh ngắn quanh mỗi cạnh
    (bỏ 8 % hai đầu — góc thẻ bo tròn), hướng gradient vuông góc cạnh, đủ ≥ 30 % chiều dài có điểm → fitLine Huber;
    cạnh thiếu biên giữ nguyên. Góc nào dịch quá 5 % cạnh ngắn → trả lại tứ giác cũ (nắn sai còn hơn không nắn)."""
    import cv2
    import numpy as np
    q = _order(quad).astype(np.float64)
    ngan = min(np.linalg.norm(q[1] - q[0]), np.linalg.norm(q[3] - q[0]))
    if ngan < 20:
        return quad
    band = max(5.0, 0.025 * ngan)
    ys, xs = np.nonzero(ctx["bien"])
    P = np.stack([xs, ys], 1).astype(np.float64)
    G = ctx["gdir"][ys, xs]
    duong = []
    for i in range(4):
        p0, p1 = q[i], q[(i + 1) % 4]
        d = p1 - p0
        L = np.linalg.norm(d)
        d = d / L
        n = np.array([-d[1], d[0]])
        rel = P - p0
        t = rel @ d
        sd = rel @ n
        sel = (np.abs(sd) <= band) & (t >= 0.08 * L) & (t <= 0.92 * L)
        if sel.sum() >= 10:
            ok = np.abs(np.cos(G[sel] - math.atan2(n[1], n[0]))) >= 0.866
            pts = P[sel][ok]
            if len(pts) >= 10 and len(np.unique(np.rint(t[sel][ok] / 3.0))) >= 0.30 * (0.84 * L / 3.0):
                vx, vy, x0, y0 = cv2.fitLine(pts.astype(np.float32), cv2.DIST_HUBER, 0, 0.01, 0.01).ravel()
                duong.append((float(x0), float(y0), float(vx), float(vy)))
                continue
        duong.append((p0[0], p0[1], d[0], d[1]))
    moi = []
    for i in range(4):                                   # góc i = giao cạnh i-1 và cạnh i (tl = trái ∩ trên, ...)
        ax, ay, adx, ady = duong[i - 1]
        bx, by, bdx, bdy = duong[i]
        den = adx * bdy - ady * bdx
        if abs(den) < 1e-9:
            return quad
        tt = ((bx - ax) * bdy - (by - ay) * bdx) / den
        moi.append((ax + tt * adx, ay + tt * ady))
    moi = np.array(moi, np.float32)
    if np.linalg.norm(moi - q, axis=1).max() > 0.05 * ngan:
        return quad
    return moi


def _tim_ung_vien(img):
    """Mọi ứng viên (điểm giảm dần), tỉ lệ thu nhỏ và ngữ cảnh — dùng cho tim_the và script soi lỗi."""
    import cv2
    h, w = img.shape[:2]
    scale = min(1.0, 1200.0 / max(h, w))
    small = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA) if scale < 1 else img.copy()
    ctx = _ngu_canh(small)
    ung = []
    for ten, m in _mat_na(small, ctx["gray_blur"], ctx["dist"], ctx["d8"]):
        ung.extend(_ung_vien_khoi(m, ten, ctx))
    ung.extend(_ung_vien_duong(ctx))
    ung.sort(key=lambda z: -z[0])
    return ung, scale, ctx


DIEM_TRON_KHUNG = 0.12    # điểm cố định cho "ảnh đã là thẻ cắt sát" (3–4 cạnh trên mép, tỉ lệ ảnh 1,50–1,70) — chỉ thắng khi không có gì hơn
NGUONG_PHU = 0.40         # ứng viên thắng phải có ĐỘ PHỦ BIÊN CÓ HƯỚNG ≥ mức này (bằng chứng hình học) mới tin là thẻ


def _sat_mep(quad, ctx):
    """Cạnh nằm GẦN MÉP ẢNH (≤ 6 % cạnh ngắn, chưa chạm mép) mà dải giữa cạnh và mép CÙNG MÀU dải trong thẻ (ΔE Lab < 14)
    → thẻ bị khung ảnh cắt (ảnh màn hình VNeID thẻ tràn hết chiều ngang, ảnh chụp sát): đẩy cạnh ra sát mép."""
    import numpy as np
    q = _order(quad).astype(np.float64)
    h, w, lab = ctx["h"], ctx["w"], ctx["lab"]
    ngan = min(np.linalg.norm(q[1] - q[0]), np.linalg.norm(q[3] - q[0]))
    if ngan < 20:
        return quad
    t = (np.arange(30) + 0.5) / 30

    def mau(P):
        x = np.clip(np.rint(P[:, 0]).astype(int), 0, w - 1)
        y = np.clip(np.rint(P[:, 1]).astype(int), 0, h - 1)
        return lab[y, x]

    duong, doi = [], False
    for i in range(4):
        p0, p1 = q[i], q[(i + 1) % 4]
        d = p1 - p0
        L = np.linalg.norm(d)
        d = d / L
        n = np.array([d[1], -d[0]])                                  # pháp tuyến hướng ra ngoài
        pts = p0[None, :] + t[:, None] * (p1 - p0)[None, :]
        if abs(n[0]) >= abs(n[1]):
            bien = 0.0 if n[0] < 0 else float(w - 1)
            kc = np.abs(pts[:, 0] - bien)
            mep = (bien, 0.0, 0.0, 1.0)
        else:
            bien = 0.0 if n[1] < 0 else float(h - 1)
            kc = np.abs(pts[:, 1] - bien)
            mep = (0.0, bien, 1.0, 0.0)
        if 1.0 < kc.mean() <= 0.06 * ngan:
            giua = pts + n[None, :] * (kc.mean() / 2)
            trong = pts - n[None, :] * (0.04 * ngan)
            if np.median(np.linalg.norm(mau(giua) - mau(trong), axis=1)) < 14:
                duong.append(mep)
                doi = True
                continue
        duong.append((p0[0], p0[1], d[0], d[1]))
    if not doi:
        return quad
    moi = []
    for i in range(4):
        ax, ay, adx, ady = duong[i - 1]
        bx, by, bdx, bdy = duong[i]
        den = adx * bdy - ady * bdx
        if abs(den) < 1e-9:
            return quad
        tt = ((bx - ax) * bdy - (by - ay) * bdx) / den
        moi.append((ax + tt * adx, ay + tt * ady))
    moi = np.array(moi, np.float32)
    moi[:, 0] = np.clip(moi[:, 0], 0, w - 1)
    moi[:, 1] = np.clip(moi[:, 1], 0, h - 1)
    return moi


def tim_the(img, chi_tiet=False):
    """Trả 4 điểm (float32, tọa độ ảnh gốc) của thẻ hoặc None. chi_tiet=True → (quad, tên ứng viên, điểm)."""
    import cv2
    import numpy as np
    ung, scale, ctx = _tim_ung_vien(img)
    if not ung:
        return (None, "", 0.0) if chi_tiet else None
    diem, quad, ten = ung[0]
    phu_tb, phu_canh, n_mep = _do_phu(quad, ctx)
    if n_mep < 3 and 0.6 * phu_tb + 0.4 * phu_canh[1] < NGUONG_PHU:
        return (None, "", diem) if chi_tiet else None
    h, w = img.shape[:2]
    ra = max(w, h) / min(w, h)
    tl = cv2.contourArea(_order(quad)) / ctx["dien_tich"]
    if n_mep >= 3 or (1.50 <= ra <= 1.70 and tl >= 0.8):
        if ctx["bien"].mean() < 2.55:                  # < 1 % điểm biên: ảnh trơn / mờ tịt, không phải thẻ cắt sát
            return (None, "", diem) if chi_tiet else None
        # chính ảnh đã là thẻ cắt sát (tỉ lệ ID-1, tứ giác chiếm ≥80 %): giữ TRỌN KHUNG — cắt thêm chỉ mất nội dung
        quad = np.array([[0, 0], [w - 1, 0], [w - 1, h - 1], [0, h - 1]], dtype=np.float32)
        return (quad, "tron_khung", diem) if chi_tiet else quad
    quad = _sat_mep(_tinh_chinh(quad, ctx), ctx) / scale
    return (quad, ten, diem) if chi_tiet else quad


# ───────────────────────────── E. cắt · nắn · xoay ─────────────────────────────
def _noi_bien(quad, w, h, ti_le=0.012):
    """Nới 1,2 % ra ngoài (thà dính chút nền còn hơn lẹm viền/dòng chữ sát mép thẻ), kẹp trong ảnh; thẻ ≥ 90 % khung → trọn khung."""
    import cv2
    import numpy as np
    q = _order(quad)
    if cv2.contourArea(q) >= 0.9 * w * h:
        return np.array([[0, 0], [w - 1, 0], [w - 1, h - 1], [0, h - 1]], dtype="float32")
    tam = q.mean(axis=0)
    q2 = tam + (q - tam) * (1 + 2 * ti_le)
    q2[:, 0] = np.clip(q2[:, 0], 0, w - 1)
    q2[:, 1] = np.clip(q2[:, 1], 0, h - 1)
    return q2.astype("float32")


def _xoay_nho(img, goc_cv):
    """Xoay góc lẻ (độ, chiều OpenCV = ngược kim đồng hồ) quanh tâm rồi cắt hình chữ nhật nội tiếp cùng tỉ lệ, resize về khổ cũ."""
    import cv2
    h, w = img.shape[:2]
    M = cv2.getRotationMatrix2D((w / 2.0, h / 2.0), goc_cv, 1.0)
    rot = cv2.warpAffine(img, M, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)
    th = math.radians(abs(goc_cv))
    s = min(w / (w * math.cos(th) + h * math.sin(th)), h / (w * math.sin(th) + h * math.cos(th)))
    cw, ch = max(2, int(w * s)), max(2, int(h * s))
    x0, y0 = (w - cw) // 2, (h - ch) // 2
    return cv2.resize(rot[y0:y0 + ch, x0:x0 + cw], (w, h), interpolation=cv2.INTER_CUBIC)


def _cat_bot(quad, cat):
    """Dịch 4 cạnh tứ giác (đã ngang: tl,tr,br,bl) vào trong theo phần cạnh cat=(trên, phải, dưới, trái) ∈ [−0,3; 0,45]
    (âm = nới ra); người dùng kéo tay cầm trên ảnh kết quả → server cắt trên TỨ GIÁC trước khi nắn nên ảnh vẫn đủ khổ."""
    import numpy as np
    t, r, b, l = [min(max(float(v or 0.0), -0.3), 0.45) for v in cat]
    if t + b > 0.8 or l + r > 0.8:
        return quad
    q = np.asarray(quad, dtype=np.float64)
    tl, tr, br, bl = q
    doc_l, doc_r = bl - tl, br - tr            # véc-tơ cạnh trái/phải (trên → dưới)
    ngang_t, ngang_b = tr - tl, br - bl        # véc-tơ cạnh trên/dưới (trái → phải)
    n_tl = tl + t * doc_l + l * ngang_t
    n_tr = tr + t * doc_r - r * ngang_t
    n_bl = bl - b * doc_l + l * ngang_b
    n_br = br - b * doc_r - r * ngang_b
    return np.array([n_tl, n_tr, n_br, n_bl], dtype="float32")


def cat_cccd(data, goc=0.0, cat=None):
    """bytes ảnh → bytes JPEG thẻ đã cắt/nắn, khổ CHUAN_W×CHUAN_H (ngang). goc = độ xoay thêm theo CHIỀU KIM ĐỒNG HỒ
    (như CSS rotate) do người dùng kéo; bội 90° xoay không mất nét (90/270 → ảnh dọc CHUAN_H×CHUAN_W).
    cat = (trên, phải, dưới, trái) phần cạnh cắt bớt (âm = nới) do người dùng kéo tay cầm — áp lên tứ giác trước khi nắn."""
    import cv2
    import numpy as np
    arr = np.frombuffer(bytes(data), dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        raise KhongThayThe("Không đọc được ảnh")
    quad = tim_the(img)
    if quad is None:
        raise KhongThayThe("Không tìm thấy thẻ trong ảnh — đặt thẻ trên nền phẳng, tương phản, lấy trọn 4 góc rồi chụp lại")
    quad = _noi_bien(quad, img.shape[1], img.shape[0])
    w_top = np.linalg.norm(quad[1] - quad[0])
    h_left = np.linalg.norm(quad[3] - quad[0])
    if h_left > w_top:                     # thẻ đang DỌC trong ảnh → đổi thứ tự góc cho thành ngang
        quad = np.array([quad[3], quad[0], quad[1], quad[2]], dtype="float32")
    if cat and any(float(v or 0) for v in cat):
        quad = _cat_bot(quad, cat)
    dst = np.array([[0, 0], [CHUAN_W - 1, 0], [CHUAN_W - 1, CHUAN_H - 1], [0, CHUAN_H - 1]], dtype="float32")
    m = cv2.getPerspectiveTransform(quad, dst)
    noi_suy = cv2.INTER_AREA if max(w_top, h_left) > CHUAN_W * 1.3 else cv2.INTER_CUBIC
    out = cv2.warpPerspective(img, m, (CHUAN_W, CHUAN_H), flags=noi_suy, borderMode=cv2.BORDER_REPLICATE)
    goc = float(goc or 0.0) % 360.0
    k = int(round(goc / 90.0)) % 4
    le = goc - round(goc / 90.0) * 90.0
    if abs(le) >= 0.3:
        out = _xoay_nho(out, -le)          # CSS thuận chiều kim → OpenCV ngược chiều kim
    for _ in range(k):
        out = cv2.rotate(out, cv2.ROTATE_90_CLOCKWISE)
    ok, buf = cv2.imencode(".jpg", out, [int(cv2.IMWRITE_JPEG_QUALITY), 92])
    if not ok:
        raise KhongThayThe("Không mã hóa được ảnh")
    return buf.tobytes()


# ───────────────────────── ảnh giả lập cho smoke / đo độ chính xác ─────────────────────────
def _the_gia(w=1162, h=722):
    """Vẽ 1 thẻ giả tỉ lệ ID-1: nền xanh ngọc nhạt gradient, ảnh chân dung, chữ; kèm mask bo góc."""
    import cv2
    import numpy as np
    the = np.zeros((h, w, 3), dtype=np.uint8)
    for x in range(w):
        t = x / w
        the[:, x] = (int(200 + 30 * t), int(228 - 10 * t), int(205 + 20 * t))
    cv2.ellipse(the, (int(w * 0.7), int(h * 0.55)), (int(w * 0.35), int(h * 0.5)), 0, 0, 360, (215, 235, 225), -1)
    cv2.rectangle(the, (int(w * 0.04), int(h * 0.33)), (int(w * 0.28), int(h * 0.88)), (90, 120, 160), -1)
    cv2.circle(the, (int(w * 0.16), int(h * 0.5)), int(h * 0.09), (150, 170, 190), -1)
    cv2.putText(the, "CAN CUOC CONG DAN", (int(w * 0.33), int(h * 0.3)), cv2.FONT_HERSHEY_DUPLEX, w / 700, (40, 40, 200), 2)
    cv2.putText(the, "079202017532", (int(w * 0.33), int(h * 0.5)), cv2.FONT_HERSHEY_DUPLEX, w / 520, (30, 30, 30), 3)
    cv2.putText(the, "NGUYEN VAN A", (int(w * 0.33), int(h * 0.66)), cv2.FONT_HERSHEY_DUPLEX, w / 560, (30, 30, 30), 3)
    cv2.putText(the, "31/03/2002   Viet Nam", (int(w * 0.33), int(h * 0.82)), cv2.FONT_HERSHEY_SIMPLEX, w / 800, (30, 30, 30), 2)
    mask = np.zeros((h, w), dtype=np.uint8)
    r = int(min(w, h) * 0.06)
    cv2.rectangle(mask, (r, 0), (w - r, h), 255, -1)
    cv2.rectangle(mask, (0, r), (w, h - r), 255, -1)
    for cx, cy in ((r, r), (w - r, r), (r, h - r), (w - r, h - r)):
        cv2.circle(mask, (cx, cy), r, 255, -1)
    return the, mask


def _dan(nen, the, mask, quad_dst):
    """Dán thẻ (kèm mask bo góc) lên nền theo tứ giác đích (phối cảnh)."""
    import cv2
    import numpy as np
    h, w = the.shape[:2]
    src = np.array([[0, 0], [w - 1, 0], [w - 1, h - 1], [0, h - 1]], dtype="float32")
    M = cv2.getPerspectiveTransform(src, np.array(quad_dst, dtype="float32"))
    H, W = nen.shape[:2]
    the_w = cv2.warpPerspective(the, M, (W, H))
    mk = cv2.warpPerspective(mask, M, (W, H))
    mk3 = (mk[:, :, None] / 255.0)
    return (nen * (1 - mk3) + the_w * mk3).astype(np.uint8)


def anh_man_hinh(the_bytes=None):
    """Giả lập ảnh chụp màn hình VNeID (dọc 1170×2532): nền trắng có hoa văn nhạt, tiêu đề, THẺ bo góc gần trọn chiều
    ngang, các dòng chữ bên dưới. Trả (PNG bytes, quad thật)."""
    import cv2
    import numpy as np
    W, H = 1170, 2532
    nen = np.full((H, W, 3), 252, dtype=np.uint8)
    rng = np.random.default_rng(7)
    for _ in range(40):
        cx, cy = int(rng.integers(0, W)), int(rng.integers(0, H))
        cv2.circle(nen, (cx, cy), int(rng.integers(30, 160)), (235, 225, 210) if rng.random() < .5 else (240, 230, 220), 3)
    cv2.putText(nen, "<-   Can cuoc dien tu", (40, 330), cv2.FONT_HERSHEY_DUPLEX, 1.6, (20, 20, 20), 3)
    if the_bytes:
        the = cv2.imdecode(np.frombuffer(the_bytes, np.uint8), cv2.IMREAD_COLOR)
        h, w = the.shape[:2]
        mask = _the_gia(w, h)[1]
    else:
        the, mask = _the_gia(1162, 722)
        h, w = the.shape[:2]
    x0, y0 = 4, 420
    quad = [[x0, y0], [x0 + w - 1, y0], [x0 + w - 1, y0 + h - 1], [x0, y0 + h - 1]]
    nen = _dan(nen, the, mask, quad)
    y = y0 + h + 90
    for dong in ("Noi sinh: ---", "Noi dang ky khai sinh: Phuong Phuoc Long, TP HCM", "Que quan: Phuong Thu Duc, TP HCM",
                 "Dan toc: Kinh          Ton giao: Khong", "Noi thuong tru: 96 duong so 6, Khu pho 31", "Dac diem nhan dang: Seo cham C 1.5cm"):
        cv2.putText(nen, dong, (40, y), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (150, 90, 30), 2)
        y += 140
    ok, buf = cv2.imencode(".png", nen)
    return buf.tobytes(), quad


def anh_nghieng(goc=12, the_bytes=None, phoi_canh=0.06, nen_go=True):
    """Giả lập ảnh điện thoại: thẻ ~55 % bề ngang, xoay `goc` độ + lệch phối cảnh, nền vân gỗ nhiễu + vật tạp.
    Trả (PNG bytes, quad thật)."""
    import cv2
    import numpy as np
    W, H = 1600, 1200
    rng = np.random.default_rng(3)
    if nen_go:
        nen = np.zeros((H, W, 3), dtype=np.uint8)
        van = rng.normal(0, 1, (H, W)).astype(np.float32)
        van = cv2.GaussianBlur(van, (0, 0), 6)
        van = (van - van.min()) / (van.max() - van.min())
        nen[:, :, 0] = (40 + 40 * van).astype(np.uint8)
        nen[:, :, 1] = (70 + 50 * van).astype(np.uint8)
        nen[:, :, 2] = (110 + 60 * van).astype(np.uint8)
    else:
        nen = np.full((H, W, 3), (60, 70, 80), dtype=np.uint8)
    cv2.circle(nen, (200, 200), 90, (90, 120, 200), -1)
    cv2.rectangle(nen, (1280, 900), (1560, 1140), (40, 160, 90), -1)
    if the_bytes:
        the = cv2.imdecode(np.frombuffer(the_bytes, np.uint8), cv2.IMREAD_COLOR)
        mask = _the_gia(the.shape[1], the.shape[0])[1]
    else:
        the, mask = _the_gia(1162, 722)
    cw = W * 0.55
    ch = cw / TI_LE
    cx, cy = W * 0.52, H * 0.5
    goc_r = math.radians(goc)
    pts = []
    for sx, sy in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
        x, y = sx * cw / 2, sy * ch / 2
        x, y = x * math.cos(goc_r) - y * math.sin(goc_r), x * math.sin(goc_r) + y * math.cos(goc_r)
        x *= 1 - phoi_canh * (1 if sy < 0 else -1)
        pts.append([cx + x, cy + y])
    quad = pts
    nen = _dan(nen, the, mask, quad)
    ok, buf = cv2.imencode(".png", nen)
    return buf.tobytes(), quad


def iou_quad(a, b):
    """IoU 2 tứ giác lồi (đo độ chính xác)."""
    import cv2
    a = _order(a).reshape(-1, 1, 2)
    b = _order(b).reshape(-1, 1, 2)
    giao, _ = cv2.intersectConvexConvex(a, b)
    hop = cv2.contourArea(a) + cv2.contourArea(b) - giao
    return float(giao / hop) if hop > 0 else 0.0


def anh_thu_nghiem(goc=12, nen=(60, 70, 80)):
    """Giữ tên cũ cho smoke: thẻ giả xoay `goc` trên nền tối phẳng."""
    return anh_nghieng(goc, phoi_canh=0.0, nen_go=False)[0]
