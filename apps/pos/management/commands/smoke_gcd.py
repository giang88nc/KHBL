# -*- coding: utf-8 -*-
"""
Bộ kiểm MẪU IN GIẤY CẦM ĐỒ (GCD) — chạy: manage.py smoke_gcd

TỰ DỌN: lưu giá trị gốc của 3 khoá `gcd_layout` + `may_in_ds` + `gcd_may_in` ở đầu, `finally` trả
lại nguyên trạng (cả việc hàng đã tồn tại hay chưa).
KHÔNG bao giờ chạm 2 khoá đang chạy thật `gdb_layout` / `deposit_print_layout` — và có hẳn kịch bản
đối chiếu chuỗi trước/sau để chứng minh điều đó.

⚠ KHÔNG chạy bằng unittest/pytest ở gốc dự án (đã từng xoá sạch 355.918 dòng DB thật) — đây là một
lệnh manage.py bình thường, đọc/ghi đúng 2 khoá key-value rồi trả lại.
"""
import ast
import base64
import json
import re
import sys
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.test import Client

from apps.pmv.models import PmvState
from apps.pos import gcd_layout as L
from apps.pos import gcd_may_in as MI
from apps.pos import gcd_print_config as PC
from apps.pos import ma_vach as MV

URL = "/he-thong/mau-in-gcd/"
URL_TIM = URL + "tim-may-in/"
URL_CHON = URL + "chon-may-in/"
TK = "admin"
TK_CHI_XEM = "kimhanh2"   # có HE_THONG XEM nhưng KHÔNG có SỬA — dùng kiểm rào quyền


class Command(BaseCommand):
    help = "Bộ kiểm bố cục + trang cấu hình mẫu in Giấy cầm đồ (A5 ngang)"

    def handle(self, *args, **opts):
        # ⚠ BẪY ĐÃ GHI TRONG CLAUDE.md: chữ Việt in ra khi stdout bị hứng vào TỆP → Python dùng
        # cp1252 → UnicodeEncodeError → lệnh chết ngang giữa chừng (từng giết scheduler KHJ).
        for luong in (sys.stdout, sys.stderr,
                      getattr(self.stdout, "_out", None), getattr(self.stderr, "_out", None)):
            if hasattr(luong, "reconfigure"):
                try:
                    luong.reconfigure(errors="replace")
                except (ValueError, OSError):
                    pass
        if "testserver" not in settings.ALLOWED_HOSTS:
            settings.ALLOWED_HOSTS.append("testserver")
        User = get_user_model()
        u = User.objects.filter(username=TK).first()
        if not u:
            self.stderr.write(f"Chưa có tài khoản {TK}.")
            return

        self.ok = self.fail = 0

        def check(ten, dk, them=""):
            if dk:
                self.ok += 1
                self.stdout.write(f"  PASS  {ten}")
            else:
                self.fail += 1
                self.stdout.write(self.style.ERROR(f"  FAIL  {ten} {them}"))

        body = lambda r: r.content.decode("utf-8", "replace")

        # ── chụp nguyên trạng TRƯỚC khi đụng vào bất cứ thứ gì ──
        # Nhớ CẢ việc hàng đã tồn tại hay chưa: `PmvState.set(key, "")` TẠO hàng rỗng, nên nếu chỉ
        # gán lại giá trị thì DB đi từ "chưa có hàng" sang "có hàng rỗng" — chạy bộ kiểm không được
        # để lại dấu vết nào, kể cả dấu vết vô hại.
        co_gcd = PmvState.objects.filter(key=L.KEY).exists()
        co_may = PmvState.objects.filter(key=L.MAY_IN_KEY).exists()
        co_mi2 = PmvState.objects.filter(key=MI.KEY).exists()
        goc_gcd = PmvState.get(L.KEY, "")
        goc_may = PmvState.get(L.MAY_IN_KEY, "")
        goc_mi2 = PmvState.get(MI.KEY, "")
        goc_gdb = PmvState.get("gdb_layout", "")
        goc_coc = PmvState.get("deposit_print_layout", "")

        c = Client()
        c.force_login(u)   # KHÔNG set_password: đổi mật khẩu là đá mọi người đang đăng nhập ra
        post = lambda d: c.post(URL, data=json.dumps(d), content_type="application/json")

        try:
            # ── 1. MẶC ĐỊNH phải hợp lệ về hình học ──
            md = L.mac_dinh()
            check("20 khối + 3 khoá phụ (_in/_ct/_nen)",
                  len(L.BLOCKS) == 20 and all(k in md for k in (L.IN_KEY, L.CT_KEY, L.NEN_KEY)))
            loi = [b["key"] for b in L.BLOCKS
                   if md[b["key"]]["left"] + md[b["key"]]["w"] > 100.01
                   or md[b["key"]]["top"] + md[b["key"]]["h"] > 100.01]
            check("mọi khối mặc định nằm TRỌN trong tờ giấy", not loi, str(loi))
            check("khoá mặc định không trùng khoá 2 mẫu in cũ",
                  L.KEY == "gcd_layout" and L.KEY not in ("gdb_layout", "deposit_print_layout"))
            an = {b["key"] for b in L.BLOCKS if b["an"]}
            check("4 khối TẮT SẴN đúng danh sách GĐ chốt",
                  an == {"cuong_chi_tiet", "khach_ky", "trang_thai", "giay_to"}, str(an))

            # ── 2. CSS mặc định: khổ A5 NẰM NGANG, tự sinh position:relative, chống in nền ──
            css = L.css(md)
            check("tờ giấy: position:relative + 210mm + aspect-ratio 210/148",
                  ".gcd-a5{position:relative!important;width:210mm!important" in css
                  and "aspect-ratio:210/148" in css)
            check("@page = ĐÚNG TỜ 210mm 148mm (không phải auto, không phải A5 dọc)",
                  "@page{size:210mm 148mm;margin:0}" in css
                  and "size:auto" not in css and "A5 portrait" not in css)
            check("chống in nền: .gcd-nen/.no-print display:none + background:none trong @media print",
                  ".gcd-nen,.no-print{display:none!important}" in css and "background:none!important" in css)
            check("KHÔNG có print-color-adjust (công tắc ép trình duyệt in nền)",
                  "print-color-adjust" not in css)
            check("khối TẮT: ẩn khi in nhưng vẫn hiện mờ ở trang cấu hình (.gcd-sua)",
                  ".gcd-a5:not(.gcd-sua) .gcd-a5__giay-to{display:none!important}" in css
                  and ".gcd-a5.gcd-sua .gcd-a5__giay-to{opacity:.3!important" in css)
            check("bậc thang co chữ sinh sẵn cho MỌI khối",
                  css.count(".gcd-co-2{font-size:calc(") == 20 and css.count(".gcd-co-3{font-size:calc(") == 20)
            check("mỗi khối đúng 1 rule vị trí", css.count("{position:absolute!important;") == 20)

            # ── 3. lop_co / qua_dai ──
            check("co chữ: ngắn = không co · vừa = bậc 2 · dài = bậc 3",
                  L.lop_co("mon_hang", "x" * 10) == "" and L.lop_co("mon_hang", "x" * 60) == "gcd-co-2"
                  and L.lop_co("mon_hang", "x" * 90) == "gcd-co-3" and L.lop_co("ma_phieu", "x" * 90) == "")
            check("vượt cả bậc cuối → cờ cảnh báo", L.qua_dai("mon_hang", "x" * 200)
                  and not L.qua_dai("mon_hang", "x" * 60))

            # ── 3b. MÃ VẠCH SỐ BIÊN NHẬN (16/09/2026) ──
            # Mã vạch là thứ MÁY đọc: sai thì không ai nhìn ra bằng mắt, tới lúc quầy quét mới biết.
            # Nên kiểm tới tận byte của ảnh chứ không chỉ kiểm "có khối trong danh sách".
            mv = L.BLOCK_MAP.get("ma_phieu_vach")
            check("có khối mã vạch trong bộ MẶC ĐỊNH, BẬT SẴN, đứng NGAY TRÊN khối ma_phieu",
                  bool(mv) and md["ma_phieu_vach"]["an"] == 0
                  and [x["key"] for x in L.BLOCKS].index("ma_phieu_vach")
                  == [x["key"] for x in L.BLOCKS].index("ma_phieu") - 1, str(mv))
            check("BỘ SỐ MẶC ĐỊNH mã vạch ĐÚNG như đã bàn giao cho KHCD (77.4 · 5.6 · 18.8 · 6.0)",
                  mv and (mv["left"], mv["top"], mv["w"], mv["h"]) == (77.4, 5.6, 18.8, 6.0)
                  and mv["sel"] == ".gcd-a5__ma-vach", str(mv))
            # Ba con số này ĐO BẰNG MÁY trên GCD.jpg (xem chú thích khối): chữ đỏ "KIM HẠNH II" hết
            # ở 77,1% · dòng "ĐC:" bắt đầu 12,4% · mép phải phải chừa vùng chết máy in 4–6mm.
            # Kéo khối ra ngoài 3 mốc này là ĐÈ LÊN CHỮ IN SẴN ⇒ máy quét câm mà không báo gì.
            check("mã vạch không đè chữ in sẵn: phải 77,1% · trên 12,4% · chừa mép ≥4mm",
                  mv and mv["left"] >= 77.1 and mv["top"] + mv["h"] <= 12.4
                  and mv["left"] + mv["w"] <= 96.5 and mv["top"] >= 4.9)
            # Vạch hẹp = (rộng ô × tỷ lệ phần vạch) ÷ số mô-đun hẹp. Dưới ~0,15mm là chắc chắn câm.
            mm_hep = (210 * mv["w"] / 100) * (828 / 908) / 207 if mv else 0
            check("bề rộng ô đủ để vạch hẹp ≥ 0,15mm (11 chữ số, tính cả quiet-zone)",
                  mm_hep >= 0.15, f"{mm_hep:.3f}mm")

            # SỐ mà mã vạch mang — HỢP ĐỒNG với KHCD, hai bên phải ra cùng một chuỗi.
            check("so_ma_vach: bỏ tiền tố chữ, GIỮ ĐỦ 11 chữ số, KHÔNG rút gọn 9 số như GĐB",
                  MV.so_ma_vach("CD26090100012") == "26090100012"
                  and len(MV.so_ma_vach("CD26090100012")) == 11,
                  MV.so_ma_vach("CD26090100012"))
            check("so_ma_vach: chạy lại trên chính kết quả vẫn ra thế (tra ngược an toàn)",
                  MV.so_ma_vach(MV.so_ma_vach("CD26090100012")) == "26090100012")
            check("so_ma_vach: rỗng/None/toàn chữ → chuỗi rỗng, KHÔNG nổ lỗi",
                  MV.so_ma_vach("") == "" and MV.so_ma_vach(None) == ""
                  and MV.so_ma_vach("CD-/ .") == "")
            check("so_ma_vach: 2 phiếu khác nhau KHÔNG bao giờ ra cùng một số",
                  MV.so_ma_vach("CD26090100012") != MV.so_ma_vach("CD26090100013"))

            def png(data_uri):
                """Bóc byte ảnh ra khỏi data URI (trả b'' nếu không phải PNG data URI)."""
                dau = "data:image/png;base64,"
                if not str(data_uri or "").startswith(dau):
                    return b""
                try:
                    return base64.b64decode(data_uri[len(dau):], validate=True)
                except (ValueError, TypeError):
                    return b""

            anh = MV.png_code39("26090100012")
            byte = png(anh)
            check("png_code39: trả ĐÚNG data URI PNG, giải mã base64 được, có chữ ký PNG thật",
                  bool(byte) and byte[:8] == b"\x89PNG\r\n\x1a\n", anh[:40])
            rong = int.from_bytes(byte[16:20], "big") if len(byte) > 20 else 0
            cao = int.from_bytes(byte[20:24], "big") if len(byte) > 24 else 0
            check("png_code39: ảnh có kích thước thật (cao 96px, rộng đủ 11 số + quiet-zone)",
                  cao == 96 and rong == 908, f"{rong}x{cao}")
            check("png_code39: MÃ RỖNG / None / toàn chữ cái → vẫn ra PNG hợp lệ, KHÔNG nổ lỗi",
                  all(png(MV.png_code39(x))[:8] == b"\x89PNG\r\n\x1a\n"
                      for x in ("", None, "CD", "  ", "-/.")))
            check("png_code39: cùng đầu vào → CÙNG MỘT chuỗi (KHBL và KHCD không thể lệch nhau)",
                  MV.png_code39("26090100012") == anh)
            check("png_code39: mã khác nhau → ảnh khác nhau (không phải ảnh giả cố định)",
                  MV.png_code39("26090100013") != anh
                  and len(png(MV.png_code39("2609010001"))) != len(byte))
            check("png_code39: thêm 1 chữ số thì ảnh rộng thêm đúng 1 ký tự Code 39 (64px)",
                  int.from_bytes(png(MV.png_code39("260901000123"))[16:20], "big") - rong == 64)

            # GIẤY ĐẢM BẢO phải y hệt trước khi tách module — nó đang chạy thật, đã quét được.
            from apps.pos import views as V
            check("Giấy đảm bảo dùng ĐÚNG module chung, không còn bản Code 39 thứ hai",
                  V._codebar is MV.png_code39
                  and V._codebar(V._ma_gdb("26-09-07-000006")) == MV.png_code39("260907006"))

            # CSS khối ảnh: PHẢI do css() sinh (để KHCD có luôn) và JS kéo-thả phải có BẢN SAO y hệt.
            check("css() sinh rule khối ảnh (.gcd-anh) — KHCD không cần chép tay static/css",
                  L.CSS_ANH in css and "object-fit:fill" in L.CSS_ANH
                  and "image-rendering:pixelated" in L.CSS_ANH)
            js = (settings.BASE_DIR / "static" / "js" / "gcd_mau.js").read_text(encoding="utf-8")
            check("static/js/gcd_mau.js giữ BẢN SAO ĐÚNG TỪNG KÝ TỰ của CSS_ANH "
                  "(thiếu là mã vạch xẹp mất lúc kéo-thả)", L.CSS_ANH in js)

            # ── 3b. CẢNH BÁO VẠCH QUÁ HẸP — phải kêu NGAY Ở TRANG CĂN ──
            # Mã vạch chỉ có một con số quyết định quét được hay không: bề rộng VẠCH HẸP. Trước
            # 16/09/2026 toàn bộ phép đo này nằm bên KHCD ⇒ GĐ kéo khối hẹp lại, Lưu, in ra giấy,
            # rồi cầm tờ phiếu quét không ra mới biết. Nay đo ngay lúc căn, cả server lẫn JS.
            check("hai ngưỡng: mức PHẢI SỬA (0,15) thấp hơn mức thoải mái (0,19)",
                  MV.VACH_HEP_TOI_THIEU_MM < MV.VACH_HEP_CAN_THU_MM
                  and (MV.VACH_HEP_TOI_THIEU_MM, MV.VACH_HEP_CAN_THU_MM) == (0.15, 0.19))
            rong_md = L.BLOCK_MAP["ma_phieu_vach"]["w"]
            mm_md = MV.vach_hep_mm("CD26090100012", rong_md, L.PAPER_W_MM)
            check("bố cục MẶC ĐỊNH (rộng 18.8% · tờ 210mm · mã 11 số) → vạch hẹp ≈ 0,174 mm",
                  abs(mm_md - 0.1739) < 0.0005, f"{mm_md:.4f}")
            check("0,174 mm nằm GIỮA hai ngưỡng → mức 'nhac' (dải xám nhắc quét thử), KHÔNG kêu đỏ",
                  MV.canh_bao_vach("CD26090100012", rong_md, L.PAPER_W_MM)[0] == "nhac")
            check("bóp còn 8% → mức 'nang' + câu bảo NỚI RỘNG trước khi in",
                  MV.canh_bao_vach("CD26090100012", 8, L.PAPER_W_MM)[0] == "nang"
                  and "Nới ô Rộng %" in MV.canh_bao_vach("CD26090100012", 8, L.PAPER_W_MM)[1])
            check("nới đủ rộng (30%) → IM LẶNG, không dải nào",
                  MV.canh_bao_vach("CD26090100012", 30, L.PAPER_W_MM) == ("", ""))
            check("mã DÀI hơn thì vạch hẹp MỎNG đi (đo theo từng phiếu, không đo một lần)",
                  MV.vach_hep_mm("CD2609010001234", rong_md, L.PAPER_W_MM) < mm_md)
            check("tờ giấy HẸP hơn thì vạch hẹp mỏng theo (cảnh báo chạy trên kho_w thật)",
                  MV.vach_hep_mm("CD26090100012", rong_md, 148) < mm_md)
            check("mã KHÔNG có chữ số → không in vạch → KHÔNG báo động",
                  MV.vach_hep_mm("CD-/ .", rong_md, L.PAPER_W_MM) == 0.0
                  and MV.canh_bao_vach("CD-/ .", rong_md, L.PAPER_W_MM) == ("", ""))
            check("rộng/khổ là rác (None, chữ) → trả 0, KHÔNG nổ lỗi giữa trang cấu hình",
                  MV.vach_hep_mm("CD26090100012", None, L.PAPER_W_MM) == 0.0
                  and MV.vach_hep_mm("CD26090100012", rong_md, "abc") == 0.0)
            # JS phải ra ĐÚNG con số đó lúc kéo-thả, và phải LẤY NGƯỠNG TỪ SERVER chứ không chép số.
            check("gcd_mau.js có bản sao công thức đơn vị Code 39 (15/ký tự + ngăn cách + quiet 20)",
                  "kyTu * 15 + (kyTu - 1) + 20" in js)
            check("gcd_mau.js ĐỌC ngưỡng từ data-vach-* chứ không chép cứng 0.19 / 0.15 "
                  "(sửa ngưỡng ở Python là trang đổi theo)",
                  "cfg.dataset.vachThu" in js and "cfg.dataset.vachMin" in js
                  and "0.19" not in js and "0.15" not in js)
            check("gcd_mau.js vẽ lại dải cảnh báo trong apply() — kéo chuột là thấy ngay",
                  "veVach();" in js and "function veVach()" in js)
            # VẼ HAY KHÔNG VẼ: phải giống hệt trang in thật. png_code39('') cố ý vẽ mã số 0 nên gọi
            # thẳng nó ở bản xem trước là thấy mã vạch trong khi tờ in ra bỏ trống.
            check("anh_ma_vach: mã KHÔNG có chữ số → KHÔNG vẽ (giống khcd/ma_vach.anh_ma_vach), "
                  "chứ không vẽ mã của số 0",
                  MV.anh_ma_vach("CD-/ .") == "" and MV.anh_ma_vach("") == ""
                  and MV.png_code39("") == MV.png_code39("0"))
            check("anh_ma_vach: mã có số → ĐÚNG ảnh của png_code39 (không phải ảnh thứ hai)",
                  MV.anh_ma_vach("CD26090100012") == MV.png_code39("26090100012"))

            # ── 3d. HAI BẢNG CUỐNG TIỆM GIỮ (GĐ chốt 16/09/2026) ──
            # Cuống là phần XÉ RA giữ lại: in sai thì tới lúc khách chuộc mới lòi, và lúc đó tờ
            # giấy đã đi theo món hàng vào tủ.
            b1, b2 = L.BLOCK_MAP.get("cuong_bang_1"), L.BLOCK_MAP.get("cuong_bang_2")
            c1, c2 = L.BLOCK_MAP["so_cuong_1"], L.BLOCK_MAP["so_cuong_2"]
            check("có ĐỦ hai khối bảng cuống và BẬT SẴN",
                  bool(b1 and b2) and md["cuong_bang_1"]["an"] == 0 and md["cuong_bang_2"]["an"] == 0)
            check("hai bảng GIỐNG HỆT nhau về khung, chỉ khác vị trí dọc (GĐ chốt hai bản y nhau)",
                  bool(b1 and b2) and (b1["left"], b1["w"], b1["h"], b1["fs"])
                  == (b2["left"], b2["w"], b2["h"], b2["fs"]))
            # ⚠ So với SỐ ĐO TRÊN GIẤY (BUOC_CUONG_PCT), KHÔNG so với cặp so_cuong_1/so_cuong_2:
            # cặp đó đang dùng 45,2 và chính nó lệch 2,4mm so với chữ in sẵn — lấy nó làm chuẩn là
            # đo cái sai bằng cái sai, đúng cái bẫy bộ kiểm cũ đã dính.
            check("bước nhảy hai bảng ĐÚNG BẰNG khoảng cách THẬT giữa hai chữ 'SỐ:' in sẵn",
                  bool(b1 and b2) and abs((b2["top"] - b1["top"]) - L.BUOC_CUONG_PCT) <= 0.02,
                  str((b2["top"] - b1["top"], L.BUOC_CUONG_PCT, c2["top"] - c1["top"])))
            check("hằng BUOC_CUONG_PCT giữ đúng số đo trên GCD.jpg (50,06 − 3,25)",
                  abs(L.BUOC_CUONG_PCT - 46.81) < 0.005)
            # 30,53% = cột đầu tiên có mực của THÂN PHẢI (đo trên GCD.jpg); dưới letterhead thì dải
            # 14%–30,5% trắng hoàn toàn. Chỗ xé nằm đâu đó trong dải trắng ấy — giấy đục lỗ không
            # để lại mực nên không đo được, vì vậy chặn ở 29,5% cho có khoảng an toàn ~1%.
            check("bảng nằm TRỌN trong cuống trái (mép phải <= 29,5%; thân phải dính mực từ 30,53%)",
                  bool(b1 and b2) and b1["left"] + b1["w"] <= 29.5 and b2["left"] + b2["w"] <= 29.5)
            check("bản trên KHÔNG đè khối chữ in sẵn (>= 12,5%) và KHÔNG chạm chữ 'SỐ:' thứ hai (<= 49,4%)",
                  bool(b1) and b1["top"] >= 12.5 and b1["top"] + b1["h"] <= 49.4, str(b1))
            check("bản dưới nằm trọn trong tờ giấy", bool(b2) and b2["top"] + b2["h"] <= 100.0)
            check("KHÔNG VIỀN: mọi chữ 'border' trong CSS bảng cuống đều là border:none",
                  all(x.startswith(":none") for x in L.CSS_CUONG.split("border")[1:]), L.CSS_CUONG)
            check("css() sinh sẵn CSS bảng cuống — KHCD khỏi chép tay vào static", L.CSS_CUONG in css)
            check("static/js/gcd_mau.js giữ BẢN SAO ĐÚNG TỪNG KÝ TỰ của CSS_CUONG "
                  "(thiếu là bảng vỡ ngay khi kéo)", L.CSS_CUONG in js)
            # QR khác mã vạch Code 39 ở chỗ MANG CẢ CHỮ CÁI — đó là lý do có nó bên cạnh mã vạch.
            qr = MV.svg_qr("KH22609020810")
            check("svg_qr: trả SVG data URI thật",
                  qr.startswith("data:image/svg+xml") and len(qr) > 200, qr[:60])
            check("svg_qr: cùng mã phiếu → CÙNG một chuỗi (KHBL và KHCD không thể lệch)",
                  MV.svg_qr("KH22609020810") == qr)
            check("svg_qr: đổi một chữ số là đổi ảnh", MV.svg_qr("KH22609020811") != qr)
            check("svg_qr: rỗng/None → KHÔNG vẽ, khác png_code39 vốn cố ý vẽ mã của số 0",
                  MV.svg_qr("") == "" and MV.svg_qr(None) == "" and MV.png_code39("") != "")

            # ── 3c. ĐỐI CHIẾU THẲNG SANG KHO KHCD (bên in thật) ──
            # Hai kho là 2 dự án rời, không import được nhau (KHCD là Flask). Đọc TỆP rồi so.
            khcd = Path(r"D:/PYTHON/KHCD/khcd")
            if not (khcd / "gcd_layout.py").exists():
                self.stdout.write("  (bỏ qua 3 kịch bản đối chiếu KHCD — không thấy D:/PYTHON/KHCD)")
            else:
                def hang_so(tep, ten):
                    """Đọc một hằng trong tệp KHCD mà KHÔNG import (tệp đó cần Flask)."""
                    cay = ast.parse((khcd / tep).read_text(encoding="utf-8"))
                    for n in cay.body:
                        if isinstance(n, ast.Assign) and any(
                                isinstance(t, ast.Name) and t.id == ten for t in n.targets):
                            return ast.literal_eval(n.value)
                        if (isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Tuple)
                                and any(getattr(e, "id", "") == ten for e in n.targets[0].elts)):
                            i = [e.id for e in n.targets[0].elts].index(ten)
                            return ast.literal_eval(n.value.elts[i])
                    raise KeyError(ten)

                kb = hang_so("gcd_layout.py", "BLOCKS")
                # KHÔNG so 'sel': KHCD cố ý chọn [data-gcd="key"] còn KHBL dùng lớp .gcd-a5__*.
                # Cái ĐI QUA pmv_state là cụm 6 số theo `key` — đó mới là thứ phải trùng từng con.
                truong = ("key", "left", "top", "w", "h", "fs", "an")
                gon = lambda ds: [tuple(b[t] for t in truong) for b in ds]
                check("20 khối của KHCD TRÙNG KHÍT KHBL: thứ tự · tên khoá · 6 số mặc định "
                      "(lệch một số là bản xem trước nói dối tờ in ra)",
                      gon(kb) == gon(L.BLOCKS),
                      str([a for a, b in zip(gon(kb), gon(L.BLOCKS)) if a != b])[:300])
                check("KHCD đo tờ giấy CÙNG kích thước mặc định 210 × 148",
                      hang_so("gcd_layout.py", "PAPER_W_MM") == L.PAPER_W_MM
                      and hang_so("gcd_layout.py", "PAPER_H_MM") == L.PAPER_H_MM)
                check("KHCD dùng CÙNG hai ngưỡng vạch hẹp (0,19 / 0,15) — hai bên không được lệch",
                      hang_so("ma_vach.py", "VACH_HEP_CAN_THU_MM") == MV.VACH_HEP_CAN_THU_MM
                      and hang_so("ma_vach.py", "VACH_HEP_TOI_THIEU_MM") == MV.VACH_HEP_TOI_THIEU_MM)
                # VÙNG SAO CHÉP: bên KHCD đã có bài kiểm sha256, nhưng bộ kiểm KHBL cũng phải bắt
                # được — sửa thuật toán ở ĐÂY mà quên chép sang đó là hai tờ giấy khác nhau.
                moc_het = "# ═══ HẾT VÙNG SAO CHÉP ═══"
                moc_dau = "# ═══ BẮT ĐẦU VÙNG SAO CHÉP — KHÔNG SỬA MỘT KÝ TỰ ═══"
                nguon = (settings.BASE_DIR / "apps" / "pos" / "ma_vach.py").read_text(encoding="utf-8")
                ban_sao = (khcd / "ma_vach.py").read_text(encoding="utf-8")
                vung_bl = nguon[nguon.index("CODE39 = {"):].split("\n" + moc_het, 1)[0].rstrip("\n")
                vung_cd = ban_sao.split(moc_dau + "\n", 1)[-1].split("\n" + moc_het, 1)[0]
                check("KHCD khai ĐÚNG hai khối bảng cuống như KHBL",
                      tuple(hang_so("gcd_layout.py", "KHOI_BANG")) == tuple(L.KHOI_BANG))
                check("KHCD dùng CÙNG chuỗi CSS bảng cuống (KHÔNG VIỀN) — giống từng ký tự",
                      hang_so("gcd_layout.py", "CSS_CUONG") == L.CSS_CUONG)
                check("KHCD giữ CÙNG bước nhảy hai nửa cuống đo trên giấy",
                      hang_so("gcd_layout.py", "BUOC_CUONG_PCT") == L.BUOC_CUONG_PCT)
                # Thân bảng cuống ở hai kho phải giống TỪNG KÝ TỰ; chỉ khối chú thích đầu tệp được
                # khác nhau (Django dùng {% comment %}, Jinja2 dùng {# #}).
                than_bl = (settings.BASE_DIR / "templates" / "pos" / "_gcd_cuong.html").read_text(
                    encoding="utf-8").split("{% endcomment %}", 1)[-1].lstrip()
                than_cd = (khcd / "templates" / "_gcd_cuong.html").read_text(
                    encoding="utf-8").split("#}", 1)[-1].lstrip()
                check("thân bảng cuống của KHCD giống KHBL ĐÚNG TỪNG KÝ TỰ",
                      than_bl == than_cd and than_bl.startswith("{% if b.nghiep_vu")
                      and 'gcd-cuong__doc' in than_bl,
                      f"KHBL {len(than_bl)} ký tự · KHCD {len(than_cd)} ký tự")
                check("KHBL đã ĐÓNG vùng sao chép bằng dòng mốc (viết thêm hàm ở cuối tệp không "
                      "còn làm bài kiểm bên KHCD đỏ oan)", moc_het in nguon)
                check("VÙNG SAO CHÉP của KHCD giống KHBL ĐÚNG TỪNG KÝ TỰ (sửa bên này phải chép "
                      "ngay sang bên kia)", vung_bl == vung_cd,
                      f"KHBL {len(vung_bl)} ký tự · KHCD {len(vung_cd)} ký tự")

            # ── 4. save/load: ép giới hạn, bỏ rác, không ghi rác vào DB ──
            lay = L.save({"mon_hang": {"left": 12.345, "fs": 99}, "khach_ten": {"an": 1},
                          "khoa_la": {"left": 5}, "ma_phieu": {"bay": 1},
                          "_in": {"kho": "B5", "canh": "x", "dy": 999, "ty_le": 95, "kho_w": 205.5, "kho_h": 146},
                          "_ct": {"tien": "bay_dat"}, "_nen": {"x": 1.5, "w": 9999}})
            check("ép giới hạn: left làm tròn 2 số lẻ · fs kẹp 30pt",
                  lay["mon_hang"]["left"] == 12.35 and lay["mon_hang"]["fs"] == 30)
            check("khoá lạ / thuộc tính lạ bị bỏ, không lọt vào DB",
                  "khoa_la" not in lay and "bay" not in lay["ma_phieu"]
                  and "khoa_la" not in PmvState.get(L.KEY, ""))
            check("khổ/canh lạ → về mặc định A5N/giữa; dy vượt ngưỡng ép 80mm",
                  lay["_in"]["kho"] == "A5N" and lay["_in"]["canh"] == "giua" and lay["_in"]["dy"] == 80)
            check("tiền lạ → giữ mặc định du_hien_tai; _nen ép trong ngưỡng",
                  lay["_ct"]["tien"] == "du_hien_tai" and lay["_nen"]["x"] == 1.5
                  and lay["_nen"]["w"] == 200)
            check("an chỉ nhận 0/1", lay["khach_ten"]["an"] == 1 and lay["mon_hang"]["an"] == 0)
            check("load() đọc lại đúng bản vừa lưu", L.load() == lay)

            # ── 5. KHỔ GIẤY LÀ THAM SỐ (tờ nhà in xén tay, không đúng A5 chuẩn) ──
            css2 = L.css(lay)
            check("đo tờ thật 205.5 × 146 → tờ giấy + @page + tỷ lệ ăn theo",
                  ".gcd-a5{position:relative!important;width:205.5mm!important" in css2
                  and "aspect-ratio:205.5/146" in css2 and "@page{size:205.5mm 146mm;margin:0}" in css2)
            check("tỷ lệ in 95% → transform scale(0.95)", "transform:scale(0.95)!important" in css2)

            for kho, mong in (("auto", "size:auto"), ("A4N", "size:A4 landscape"),
                              ("A5D", "size:A5 portrait")):
                l2 = L.save({"_in": {"kho": kho}})
                check(f"khổ gửi máy in {kho} → @page {mong}", mong in L.css(l2))

            # ── 6. NaN / Infinity không bao giờ lọt vào CSS ──
            l3 = L.save({"mon_hang": {"left": float("nan"), "top": float("inf"), "w": "abc"}})
            check("NaN/Infinity/chữ bị loại, giữ số mặc định",
                  l3["mon_hang"]["left"] == L.BLOCK_MAP["mon_hang"]["left"]
                  and "nan" not in L.css(l3).lower() and "inf" not in L.css(l3).lower())

            # ── 7. reset ──
            l4 = L.save(None)
            check("save(None) = xoá hàng, về mặc định",
                  l4 == L.mac_dinh() and PmvState.get(L.KEY, "") == "")

            # ── 8. SỔ MÁY IN (khoá riêng may_in_ds, dùng chung 3 mẫu in) ──
            check("sổ mặc định: 1 máy quầy, cách chạy thật là trinh_duyet",
                  L.may_in_load()["ban"][0]["cach"] == "trinh_duyet"
                  and L.may_in_load()["mac_dinh"] == "quay_cd")
            so = L.may_in_save({"mac_dinh": "khong_co", "ban": [
                {"id": "Quầy CĐ!", "ten": "Máy quầy", "cach": "ipp", "host": "192.168.1.50", "cong": 631},
                {"id": "quaycd", "ten": "Trùng id"},
                {"id": "", "ten": "Thiếu id"},
                {"id": "kho_2", "ten": "Máy kho", "cach": "bay_dat"}]})
            check("id chuẩn hoá · dòng trùng id và thiếu id bị bỏ",
                  [b["id"] for b in so["ban"]] == ["quaycd", "kho_2"], str(so["ban"]))
            check("cách in lạ → trinh_duyet; mặc định không tồn tại → về dòng đầu",
                  so["ban"][1]["cach"] == "trinh_duyet" and so["mac_dinh"] == "quaycd")
            check("giữ tham số mạng cho P2 (chưa nối nhưng lưu được)",
                  so["ban"][0]["host"] == "192.168.1.50" and so["ban"][0]["cong"] == 631)
            check("may_in trong _in chỉ là ID trỏ sang sổ, đã chuẩn hoá",
                  L.save({"_in": {"may_in": "Quầy CĐ!"}})["_in"]["may_in"] == "quaycd")
            L.may_in_save(None)
            check("sổ save(None) = về mặc định", PmvState.get(L.MAY_IN_KEY, "") == "")

            # ── 9. TRANG CẤU HÌNH ──
            r = c.get(URL)
            b = body(r)
            check("GET trang mở được (200)", r.status_code == 200, f"status={r.status_code}")
            co = set(re.findall(r'data-gcd="([a-z0-9_]+)"', b))
            check("HTML render có ĐỦ và ĐÚNG 18 data-gcd (khớp BLOCKS, không thiếu không thừa)",
                  co == {x["key"] for x in L.BLOCKS}, str(co ^ {x["key"] for x in L.BLOCKS}))
            # Mã vạch trên trang cấu hình phải là ẢNH THẬT: ô trống thì GĐ căn xong mới phát hiện
            # mã bị bóp hẹp / đè chữ in sẵn — lúc đó giấy đã in ra rồi.
            img = re.search(r'<img[^>]+data-gcd="ma_phieu_vach"[^>]*>', b)
            check("xem trước có THẺ ẢNH mã vạch mang data-gcd (kéo-thả được như mọi khối)", bool(img))
            check("ảnh mã vạch là PNG data URI THẬT, không phải ô trống",
                  bool(img) and 'src="data:image/png;base64,' in img.group(0)
                  and len(img.group(0)) > 400, (img.group(0)[:120] if img else ""))
            check("ảnh mã vạch mang class .gcd-anh + alt là ĐÚNG số máy quét sẽ đọc + không bị "
                  "trình duyệt cướp thao tác kéo",
                  bool(img) and "gcd-anh" in img.group(0) and 'draggable="false"' in img.group(0)
                  and MV.so_ma_vach(PC.MAU_THUONG["sku"]) in img.group(0))
            check("số trên mã vạch KHỚP số biên nhận in ở ô SỐ: (quét ra tra đúng phiếu)",
                  PC.MAU_THUONG["sku"] in b
                  and MV.so_ma_vach(PC.MAU_THUONG["sku"]) in (img.group(0) if img else ""))
            # Dải cảnh báo vạch hẹp: bố cục mặc định cho 0,174 mm ⇒ trang PHẢI hiện dòng xám.
            dai_vach = re.search(r'<div class="gcdm-warn[^"]*"\s+id="gcdm-vach"[^>]*>([^<]*)<',
                                 b, re.S)
            check("trang cấu hình HIỆN dải nhắc vạch hẹp với bố cục mặc định (không hidden)",
                  bool(dai_vach) and "hidden" not in dai_vach.group(0)
                  and "QUÉT THỬ" in dai_vach.group(1), (dai_vach.group(0)[:160] if dai_vach else "KHÔNG THẤY"))
            check("dải nhắc là mức XÁM (gcdm-warn--nhe), không phải dải đỏ báo động",
                  bool(dai_vach) and "gcdm-warn--nhe" in dai_vach.group(0))
            check("ngưỡng gửi sang JS ở dạng CHẤM thập phân — số bản địa hoá 0,19 sẽ bị parseFloat "
                  "cắt còn 0 rồi cảnh báo im luôn",
                  'data-vach-thu="0.19"' in b and 'data-vach-min="0.15"' in b
                  and 'data-vach-so="%s"' % MV.so_ma_vach(PC.MAU_THUONG["sku"]) in b)
            cuong = re.findall(r'<div class="[^"]*gcd-cuong" data-gcd="(cuong_bang_[12])"', b)
            check("trang cấu hình render ĐỦ hai bảng cuống",
                  sorted(cuong) == ["cuong_bang_1", "cuong_bang_2"], str(cuong))
            # GĐ chốt 17/09: BỎ hẳn nhãn "Thu/Chi:" — dòng tiền chỉ còn MỘT số tự nói hướng của nó.
            # KHÔNG gò cứng nhãn tiếng Việt ở đây: Giám đốc còn chỉnh chữ trên template (17/09 đã
            # đổi "Cầm:" thành "Tiền gốc:"). Kiểm theo CẤU TRÚC + con số, để sửa chữ không làm đỏ oan.
            check("mỗi bảng có QR thật + đủ 2 dòng tiền + số tiền đúng, KHÔNG còn nhãn Thu/Chi",
                  b.count('class="gcd-cuong__qr"') == 2
                  and b.count('class="gcd-cuong__tien"') == 4 and b.count("Thu/Chi:") == 0
                  and b.count("Chi 25.000.000") == 2 and b.count("25.000.000</b>") == 2)
            check("QR mang NGUYÊN mã phiếu kể cả chữ cái (mã vạch Code 39 chỉ mang phần số)",
                  b.count('alt="QR mã phiếu %s"' % PC.MAU_THUONG["sku"]) == 2
                  and 'src="data:image/svg+xml' in b)
            # ── NỘI DUNG 4 KHỐI GĐ CHỐT 17/09/2026 ──
            # Mã truy vết = mã phiếu bỏ KH2 · loan_id · log_id · lần in. In ở 3 ô: hai cuống + ô
            # "giấy tờ". Có nó mới lần được từ TỜ GIẤY về đúng lượt việc trong sổ.
            theo_doi = "2609020839-977-3435-01"
            check("mã truy vết đúng dạng và in ở CẢ BA ô (2 cuống + ô giấy tờ)",
                  b.count(theo_doi) == 3, str(b.count(theo_doi)))
            check("mã truy vết BỎ tiền tố KH2 của mã phiếu, nhưng mã phiếu ở ô SỐ: vẫn in đủ",
                  not theo_doi.startswith("KH2") and PC.MAU_THUONG["sku"] in b)
            check("ô Nhận của Ông/Bà có tên KÈM sđt che 4 số giữa",
                  "Nguyễn Thị Kim Anh | 090****567" in b and "0901234567" not in b)
            check("ô dấu đổi thành TÊN TIỆM cố định, không còn trạng thái phiếu",
                  "CẦM ĐỒ KIM HẠNH 2" in b and "ĐANG CẦM" not in b)
            check("khối tóm tắt giao dịch đủ ba dòng đúng mẫu GĐ đưa",
                  "Trả bớt: 25.000.000 ₫" in b and "Số ngày cầm: 1 ngày" in b
                  and "Tiền lời: 11.000 ₫" in b)
            # "CCCD" còn trong TÊN KHỐI trên bảng chỉnh (nhãn giao diện), nhưng SỐ giấy tờ thì
            # không được in ra tờ giấy nữa.
            check("KHÔNG còn in số CCCD lên tờ giấy", "CCCD 0" not in b and "079188001234" not in b)

            check("có <style id=gcd-layout-css> + bảng chỉnh + ảnh nền tách riêng",
                  'id="gcd-layout-css"' in b and 'class="gcdm-row"' in b
                  and 'class="gcd-nen no-print"' in b and "img/GCD.jpg" in b)
            check("chú thích template KHÔNG lọt ra màn hình",
                  "BẨY ĐÃ TRÁNH" not in b and "{% comment %}" not in b)
            # ⚠ khbl.css có sẵn @page A5 dọc (cho GĐB) và @page A4 ngang (cho CỌC). <style> của GCD
            # phải đứng SAU thì @page 210mm 148mm mới thắng — cùng !important, khai sau thắng.
            check("<style> bố cục GCD đứng SAU khbl.css (để @page tờ ngang thắng @page A5 dọc)",
                  b.find("css/khbl.css") >= 0 and b.find("css/khbl.css") < b.find('id="gcd-layout-css"'))
            check("nói rõ ô Máy in chưa điều khiển được thiết bị",
                  "NHÃN CẤU HÌNH" in b and "kiosk-printing" in b)
            check("?dai=1 dựng được trường hợp dài nhất",
                  c.get(URL + "?dai=1").status_code == 200)

            # ── 10. POST: server trả CSS của chính nó (đang nhìn = sắp in) ──
            j = post({"layout": {"mon_hang": {"left": 40, "fs": 9.5}},
                      "may_in": {"mac_dinh": "quay_cd", "ban": [
                          {"id": "quay_cd", "ten": "Máy in quầy cầm đồ", "cach": "trinh_duyet"}]}}).json()
            check("POST lưu → ok, CSS có left:40% và font-size:9.5pt cho MÓN HÀNG",
                  j.get("ok") and ".gcd-a5__mon-hang{position:absolute!important;left:40%" in j["css"]
                  and "font-size:9.5pt!important" in j["css"])
            check("CSS server trả về = css(layout) sinh lại → 2 đường không thể lệch",
                  j["css"] == L.css(j["layout"]))
            check("POST trả kèm sổ máy in", j["may_in"]["ban"][0]["id"] == "quay_cd")
            j2 = post({"reset": True}).json()
            check("POST reset → về mặc định cả bố cục lẫn sổ máy in",
                  j2["layout"] == L.mac_dinh() and j2["may_in"] == L.may_in_mac_dinh())
            check("POST JSON hỏng → 400 có thông báo tiếng Việt",
                  c.post(URL, data="{[", content_type="application/json").status_code == 400)

            # ── 11. RÀO QUYỀN (máy quầy Edge kiosk KHÔNG có nút Back — GET phải vào được) ──
            uv = User.objects.filter(username=TK_CHI_XEM).first()
            if uv and not uv.is_superuser:
                cv = Client()
                cv.force_login(uv)
                bv = body(cv.get(URL))
                check(f"{TK_CHI_XEM} (chỉ XEM): GET 200, ẩn nút LƯU, có dải báo chỉ-xem",
                      "gcdm-save" not in bv and "chỉ được XEM" in bv)
                check(f"{TK_CHI_XEM} (chỉ XEM): POST bị chặn 403",
                      cv.post(URL, data="{}", content_type="application/json").status_code == 403)
            else:
                check(f"có tài khoản {TK_CHI_XEM} không-superuser để kiểm rào quyền", False)

            # ── 12. MÁY IN THẬT: TÌM · CHỌN · LƯU (khoá riêng gcd_may_in, 15/09/2026) ──
            ao1, ly1 = MI.la_ao("Microsoft Print to PDF", "PORTPROMPT:", "Microsoft Print To PDF")
            ao2, ly2 = MI.la_ao("Microsoft Print to PDF (redirected 1)", "TS003",
                                "Remote Desktop Easy Print")
            ao3, _ = MI.la_ao("HP LaserJet M404 quầy cầm đồ", "USB001", "HP LaserJet M404 PCL-6")
            check("nhận dạng máy in ẢO: in-ra-tệp · phiên Remote Desktop · máy in thật thì KHÔNG",
                  ao1 and "TỆP" in ly1 and ao2 and "Remote Desktop" in ly2 and not ao3,
                  f"{ao1}/{ao2}/{ao3}")

            tim = MI.tim()
            khoa_dong = {"ten", "cong", "driver", "trang_thai", "chia_se", "mac_dinh", "ao", "ly_do_ao"}
            check("tim(): hỏi Windows KHÔNG ném lỗi, trả đủ ok/ds/loi/giai_thich",
                  isinstance(tim, dict) and isinstance(tim["ds"], list)
                  and set(tim) >= {"ok", "ds", "loi", "giai_thich"}, str(tim.get("loi")))
            check("tim(): mỗi máy in có ĐỦ trường, tên không rỗng",
                  all(set(p) == khoa_dong and p["ten"] for p in tim["ds"]), str(tim["ds"][:1]))
            check("tim(): máy chủ KHÔNG có máy in thật → có câu giải thích, KHÔNG báo lỗi",
                  bool(tim["ok"]) and (any(not p["ao"] for p in tim["ds"]) or tim["giai_thich"]),
                  str(tim.get("loi")))

            tt = MI.tinh_trang()
            check("tinh_trang(): nói ĐÚNG sự thật máy chủ (đủ 2 mảnh mới sẵn sàng)",
                  tt["san_sang"] == bool(tt["edge"] and (tt["day_pdf"] or tt["pywin32"]))
                  and (tt["san_sang"] or tt["thieu"]) and tt["loi_nhan"], str(tt))
            check("tinh_trang(): chưa sẵn sàng thì lời nhắn nói rõ VẪN mở hộp thoại trình duyệt",
                  tt["san_sang"] or "hộp thoại" in tt["loi_nhan"])

            m = MI.save({"ten": "HP LaserJet\tM404\n", "cong": "USB001", "ao": False,
                         "ghi_chu": "x" * 500, "boi": "ke_gia"}, boi="admin")
            check("lưu: bỏ ký tự điều khiển trong tên · cắt ghi chú · ghi ai lưu / lúc nào",
                  m["ten"] == "HP LaserJetM404" and len(m["ghi_chu"]) == MI.GIOI_HAN["ghi_chu"]
                  and m["boi"] == "admin" and m["luc"], str(m))
            check("load() đọc lại đúng bản vừa lưu", MI.load() == m)
            check("bỏ chọn (ten rỗng) = XOÁ HÀNG, về 'chưa chọn'",
                  MI.save({"ten": "  "})["ten"] == "" and PmvState.get(MI.KEY, "") == "")
            PmvState.set(MI.KEY, "{ĐÂY KHÔNG PHẢI JSON")
            check("dữ liệu HỎNG trong DB → coi như chưa chọn, KHÔNG nổ lỗi",
                  MI.load() == MI.mac_dinh())
            PmvState.set(MI.KEY, '{"ten": 123, "ao": "co", "khoa_la": 1}')
            xau = MI.load()
            check("kiểu dữ liệu lạ bị ép sạch, khoá lạ không lọt ra",
                  xau["ten"] == "123" and xau["ao"] is True and "khoa_la" not in xau, str(xau))

            # ⚠ ĐIỂM CỐT LÕI của việc tách khoá: chọn máy in KHÔNG được chạm vào bố cục.
            L.save({"mon_hang": {"left": 33.3}})
            truoc_bc, truoc_so = PmvState.get(L.KEY, ""), PmvState.get(L.MAY_IN_KEY, "")
            jt = c.post(URL_TIM, data="{}", content_type="application/json").json()
            check("POST tìm máy in → ok + kèm máy in đang chọn + tình trạng máy chủ",
                  jt.get("ok") is True and "dang_chon" in jt and "san_sang" in jt["tinh_trang"])
            jc = c.post(URL_CHON, data=json.dumps({"may_in": {"ten": "Máy in quầy cầm đồ",
                                                              "cong": "USB001", "ao": False}}),
                        content_type="application/json").json()
            check("POST chọn máy in → lưu đúng tên (giữ nguyên dấu tiếng Việt)",
                  jc.get("ok") and jc["may_in"]["ten"] == "Máy in quầy cầm đồ"
                  and MI.load()["ten"] == "Máy in quầy cầm đồ", str(jc))
            check("CHỌN MÁY IN KHÔNG ĐỤNG bố cục gcd_layout và sổ may_in_ds (lý do tách khoá riêng)",
                  PmvState.get(L.KEY, "") == truoc_bc and PmvState.get(L.MAY_IN_KEY, "") == truoc_so)
            bg = body(c.get(URL))
            check("trang hiện TÊN máy in đang chọn + nói thẳng tình trạng thật của máy chủ",
                  "Máy in quầy cầm đồ" in bg and "gcdm-mi2-tim" in bg
                  and ("hộp thoại in của trình duyệt" in bg or "tự đẩy bản in" in bg))
            jb = c.post(URL_CHON, data=json.dumps({"bo_chon": True}),
                        content_type="application/json").json()
            check("POST bỏ chọn → về rỗng, xoá hàng (mỗi lần in dùng hộp thoại trình duyệt)",
                  jb.get("ok") and jb["may_in"]["ten"] == "" and PmvState.get(MI.KEY, "") == "")
            check("POST thiếu khoá may_in → 400, KHÔNG âm thầm xoá lựa chọn",
                  c.post(URL_CHON, data="{}", content_type="application/json").status_code == 400)
            check("POST JSON hỏng ở endpoint máy in → 400",
                  c.post(URL_CHON, data="{[", content_type="application/json").status_code == 400)
            check("2 endpoint máy in chỉ nhận POST (GET trả 405)",
                  c.get(URL_TIM).status_code == 405 and c.get(URL_CHON).status_code == 405)
            if uv and not uv.is_superuser:
                check(f"{TK_CHI_XEM} (chỉ XEM): KHÔNG được tìm / chọn máy in (403)",
                      cv.post(URL_TIM, data="{}", content_type="application/json").status_code == 403
                      and cv.post(URL_CHON, data="{}",
                                  content_type="application/json").status_code == 403)

            # ── 13. KHÔNG ĐỤNG 2 MẪU IN ĐANG CHẠY ──
            check("khoá gdb_layout giữ NGUYÊN VĂN sau toàn bộ bộ kiểm",
                  PmvState.get("gdb_layout", "") == goc_gdb)
            check("khoá deposit_print_layout giữ NGUYÊN VĂN sau toàn bộ bộ kiểm",
                  PmvState.get("deposit_print_layout", "") == goc_coc)
            from apps.pos import gdb_layout as G
            check("KHÔNG mượn IN_KHO của gdb_layout (dict đó không có khổ NGANG)",
                  L.IN_KHO is not G.IN_KHO and "A5N" not in G.IN_KHO and "A5N" in L.IN_KHO)

        finally:
            for khoa, co, goc in ((L.KEY, co_gcd, goc_gcd), (L.MAY_IN_KEY, co_may, goc_may),
                                  (MI.KEY, co_mi2, goc_mi2)):
                if co:
                    PmvState.set(khoa, goc)
                else:
                    PmvState.objects.filter(key=khoa).delete()

        sach = all(PmvState.objects.filter(key=k).exists() == co and PmvState.get(k, "") == goc
                   for k, co, goc in ((L.KEY, co_gcd, goc_gcd), (L.MAY_IN_KEY, co_may, goc_may),
                                      (MI.KEY, co_mi2, goc_mi2)))
        check("TỰ DỌN: 3 khoá gcd_layout / may_in_ds / gcd_may_in trở về ĐÚNG trạng thái trước khi "
              "chạy (còn/không còn hàng, đúng giá trị)", sach)

        self.stdout.write((self.style.SUCCESS if not self.fail else self.style.ERROR)(
            f"KẾT QUẢ smoke_gcd: {self.ok} PASS / {self.fail} FAIL"))
