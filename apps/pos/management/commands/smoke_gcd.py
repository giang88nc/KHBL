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
import json
import re
import sys

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.test import Client

from apps.pmv.models import PmvState
from apps.pos import gcd_layout as L
from apps.pos import gcd_may_in as MI

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
            check("17 khối + 3 khoá phụ (_in/_ct/_nen)",
                  len(L.BLOCKS) == 17 and all(k in md for k in (L.IN_KEY, L.CT_KEY, L.NEN_KEY)))
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
                  css.count(".gcd-co-2{font-size:calc(") == 17 and css.count(".gcd-co-3{font-size:calc(") == 17)
            check("mỗi khối đúng 1 rule vị trí", css.count("{position:absolute!important;") == 17)

            # ── 3. lop_co / qua_dai ──
            check("co chữ: ngắn = không co · vừa = bậc 2 · dài = bậc 3",
                  L.lop_co("mon_hang", "x" * 10) == "" and L.lop_co("mon_hang", "x" * 60) == "gcd-co-2"
                  and L.lop_co("mon_hang", "x" * 90) == "gcd-co-3" and L.lop_co("ma_phieu", "x" * 90) == "")
            check("vượt cả bậc cuối → cờ cảnh báo", L.qua_dai("mon_hang", "x" * 200)
                  and not L.qua_dai("mon_hang", "x" * 60))

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
            check("HTML render có ĐỦ và ĐÚNG 17 data-gcd (khớp BLOCKS, không thiếu không thừa)",
                  co == {x["key"] for x in L.BLOCKS}, str(co ^ {x["key"] for x in L.BLOCKS}))
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
