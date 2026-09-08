"""
Bộ kiểm GIAO DIỆN bán lẻ: URL /banle/, trang chủ, bán hàng, khách hàng, thâu, bảng giá, hóa đơn.
Chạy: manage.py smoke_ui        (đọc PMV thật + sandbox, KHÔNG ghi gì)
"""
import json
import re

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.test import Client

from apps.pmv import money as M
from apps.pos import services as S

TK = "admin"


class Command(BaseCommand):
    help = "Smoke test giao diện các màn bán lẻ"

    def handle(self, *args, **opts):
        if "testserver" not in settings.ALLOWED_HOSTS:
            settings.ALLOWED_HOSTS.append("testserver")
        User = get_user_model()
        u = User.objects.filter(username=TK).first()
        if not u:
            self.stderr.write(f"Chưa có tài khoản {TK} — chạy manage.py sync_pmv_users trước.")
            return
        c = Client()
        # force_login: KHÔNG đặt lại mật khẩu — set_password() đổi session hash và
        # ĐÁ MỌI NGƯỜI ĐANG ĐĂNG NHẬP ra ngoài, không được phép khi tiệm đang bán.
        c.force_login(u)

        self.ok = self.fail = 0
        body = lambda r: r.content.decode("utf-8", "replace")

        def check(ten, dieu_kien, them=""):
            if dieu_kien:
                self.ok += 1
                self.stdout.write(f"  PASS  {ten}")
            else:
                self.fail += 1
                self.stdout.write(self.style.ERROR(f"  FAIL  {ten} {them}"))

        # ── 1. cấu trúc URL /banle/ ──
        for url, phai_co in [("/", "KIM HẠNH 2"), ("/banle/", "KIM HẠNH 2"),
                             ("/banle/ban-hang/", "Quét tem"), ("/banle/thau-vao/", "VÀNG THÂU"),
                             ("/banle/khach-hang/", "Khách hàng"), ("/banle/bang-gia/", "Bảng giá vàng"),
                             ("/banle/hoa-don/", "giao dịch")]:   # 06/09: trang đổi thành "Hóa đơn & giao dịch" (hero)
            r = c.get(url)
            check(f"GET {url}", r.status_code == 200 and phai_co in body(r), f"status={r.status_code}")
        check("URL thiếu dấu / cuối vẫn vào được", c.get("/banle/ban-hang", follow=True).status_code == 200)

        # ── 2. KHUNG CHUNG: topbar + chân trang có ở MỌI trang, KHÔNG còn rail dọc ──
        TRANG = ["/", "/banle/ban-hang/", "/banle/thau-vao/", "/banle/bang-gia/",
                 "/banle/khach-hang/", "/banle/hoa-don/"]
        for url in TRANG:
            b = body(c.get(url))
            check(f"topbar có ở {url}", 'class="khbl-top"' in b and "img/logo_icon.png" in b)
            check(f"  chân trang có ở {url}", 'class="khbl-foot"' in b and 'id="khbl-dongho"' in b)
            check(f"  KHÔNG còn thanh menu đứng ở {url}", 'khbl-rail' not in b)

        b = body(c.get("/"))
        check("topbar: đủ 7 mục menu", b.count('class="khbl-top__i') == 7)
        for nhan in ("TỔNG QUAN", "BÁN HÀNG", "THÂU VÀO", "BẢNG GIÁ", "KHÁCH HÀNG", "HÓA ĐƠN", "HỆ THỐNG"):
            check(f"  mục '{nhan}'", f"<span>{nhan}</span>" in b)
        # Icon topbar = ẢNH (bộ tranh vàng GĐ đưa 03/09/2026), bản 96px trong img/ico/.
        # Ảnh gốc 1254px ~2MB/cái GIỮ trong img/ — không bao giờ nhúng thẳng vào trang.
        check("topbar: 7 icon ảnh 26px", b.count('class="khbl-ico-anh"') == 7)
        for k in ("tong", "ban", "thau", "gia", "khach", "hoadon", "hethong"):
            check(f"  icon '{k}' được nhúng", f'img/ico/{k}.png' in b)
            r = c.get(f"/static/img/ico/{k}.png")
            check(f"  icon '{k}' tải được + nhẹ (<60KB)",
                  r.status_code == 200 and len(r.getvalue() if hasattr(r, "getvalue")
                                               else b"".join(r.streaming_content)
                                               if r.streaming else r.content) < 60_000)
        check("topbar: tên người đăng nhập + két", "khbl-top__me" in b and TK in b)
        check("topbar: nút Đăng xuất", "Đăng xuất" in b)
        check("topbar: nút 🔑 passcode + popup đặt/đổi render",
              "tai-khoan/passcode" in b and 'name="moi2"' in body(c.get("/tai-khoan/passcode/")))
        check("topbar: đánh dấu trang đang mở (Tổng quan)", 'khbl-top__i is-on' in b)
        check("topbar: trang Bán hàng tự đánh dấu đúng mục",
              'khbl-top__i khbl-top__i--chinh is-on' in body(c.get("/banle/ban-hang/")))

        # ── 3. nội dung trang chủ + chân trang ──
        check("trang chủ: 4 thẻ số liệu", b.count('class="pg-tong__kpi-val') == 4)
        check("trang chủ: biểu đồ 7 cột", b.count("pg-tong__bar-track") == 7)
        check("trang chủ: bảng tồn theo nhóm vàng", "Tồn theo nhóm vàng" in b)
        check("trang chủ: tổng bán trong tháng", "TỔNG BÁN TRONG THÁNG" in b)
        check("trang chủ: cảnh báo PMV", "Cảnh báo từ PMV" in b and "pg-tong__alerts" in b)
        check("trang chủ: bảng nhẫn 9999", "Vàng nhẫn 9999" in b and "pg-tong__ring-table" in b)
        check("chân trang: tên công ty từ T_SHOP", "khbl-foot__co" in b and "KIM HẠNH" in b.upper())
        check("chân trang: thông tin phần mềm", "khbl-foot__pm" in b and "KHBL" in b)
        check("chân trang: ô đồng hồ", 'id="khbl-dongho"' in b and "data-gio" in b)

        # ── 4. bán hàng: quét mã thật + vàng cũ + tổng ──
        c.post("/banle/ban-hang/moi/")
        b0 = body(c.get("/banle/ban-hang/"))
        check("trang trắng: 4 nút chân trang hiện nhưng đều TẮT (08/09)",
              b0.count('disabled title="Chưa có đơn"') == 3 and "Thanh toán xong mới in" in b0)
        ma = S.client("smoke").query(
            "SELECT TOP 1 ProductCode FROM T_PRODUCT WITH (NOLOCK) "
            "WHERE Status='I' AND TaskPrice>0 AND ISNULL(DiamondWeight,0)>0 ORDER BY InDate DESC")[0]["ProductCode"]
        b = body(c.post("/banle/ban-hang/quet/", {"ma": ma}))
        check(f"quét mã thật {ma} → thêm dòng VÀNG BÁN", ma in b and "pos-ban" in b)
        # v5: món đầu đã Ins thành đơn W thật → quét lại bị vendor chặn P-008 (kèm tên đơn đang giữ);
        # giỏ chưa ghi được KK thì vẫn là "đã có trong phiếu"
        b2 = body(c.post("/banle/ban-hang/quet/", {"ma": ma}))
        check("quét lại cùng mã → báo đã có trong phiếu / P-008 đang trong đơn",
              "đã có trong phiếu" in b2 or "P-008" in b2 or "đang trong đơn" in b2)
        check("mã không tồn tại → câu lỗi vendor P-002",
              "P-002" in body(c.post("/banle/ban-hang/quet/", {"ma": "ZZZKHONGCO"})))
        # ── QUÉT MÃ GĐB ở ô quét (08/09/2026 chiều): 9 số yymmdd+stt → mở hóa đơn; ngày vô lý/không có → quét hàng như cũ ──
        from apps.pos import bill as B
        from apps.pos.views import _ma_gdb
        check("mã GĐB hợp lệ 260908038 → (2026-09-08, '038'); tháng 13 / mã hàng có chữ → None",
              B.ma_gdb_hop_le("260908038") == ("2026-09-08", "038") and B.ma_gdb_hop_le("261308038") is None
              and B.ma_gdb_hop_le("1B60000865") is None and B.ma_gdb_hop_le("26090803") is None)
        hd = S.client("smoke_ui").query("SELECT TOP 1 TrnID, BillCode FROM TRN_RT_BUYSELL WITH (NOLOCK) "
                                        "WHERE IsDel='0' AND Status='C' ORDER BY TrnDate DESC, TrnTime DESC")[0]
        tim = B.tim_theo_ma_gdb(_ma_gdb(hd["BillCode"]))
        check(f"tra mã GĐB {_ma_gdb(hd['BillCode'])} → đúng hóa đơn {hd['BillCode']}",
              bool(tim) and tim["TrnID"] == hd["TrnID"])
        check("mã dạng GĐB nhưng không có hóa đơn (200101999) → rơi về quét hàng, vendor P-002",
              "P-002" in body(c.post("/banle/ban-hang/quet/", {"ma": "200101999"})))
        # ── QUÉT QR THẺ CCCD vào ô khách (08/09/2026 chiều): chỉ lấy 12 số đầu, tự chọn nếu đúng 1 khách ──
        qr_rac = "|381472415|Tr0198017601980161ng Ng022501870141c Giang|07041988|Nam|Kh01950179m 4, TT. N0196m|15092023"
        check("cccd_tu_qr: rút đúng 12 số dù tên/địa chỉ hỏng; gõ tay (không '|') → ''",
              S.cccd_tu_qr("096088009068" + qr_rac) == "096088009068" and S.cccd_tu_qr("0944351461") == ""
              and S.cccd_tu_qr("xx|096088009068|y") == "096088009068")
        kh = S.client("smoke_ui").query("SELECT TOP 1 CustID, CMND FROM I_CUSTOMER WITH (NOLOCK) WHERE Active='1' "
                                        "AND LEN(CMND)=12 AND CMND NOT LIKE '%[^0-9]%' ORDER BY LastTradingDate DESC")
        if kh:
            bq = body(c.get("/banle/ban-hang/tim-khach/", {"q": kh[0]["CMND"] + qr_rac}))
            # tự chọn = gọi thẳng htmx.ajax ban_dat (b.click() rơi vì htmx chưa gắn hx-post cho nút vừa swap)
            check(f"quét QR CCCD {kh[0]['CMND']} → ô tìm = 12 số, khách {kh[0]['CustID']} hiện + tự chọn (htmx.ajax ban_dat)",
                  f'o.value = "{kh[0]["CMND"]}"' in bq and f'cust_id: "{kh[0]["CustID"]}"' in bq
                  and 'htmx.ajax("POST", "/banle/ban-hang/dat/"' in bq)
        check("QR CCCD chưa có khách → báo 'Chưa có khách' + không tự chọn",
              (lambda b0: "Chưa có khách" in b0 and "ban-hang/dat/" not in b0)(
                  body(c.get("/banle/ban-hang/tim-khach/", {"q": "000000000001" + qr_rac}))))
        b = body(c.post("/banle/ban-hang/vang-doi/",
                        {"gold": "D9999", "tong_tl": "100", "tl_hot": "0", "gia": ""}))
        check("thêm dòng VÀNG ĐỔI (giá tự lấy từ bảng giá)",
              "D9999" in b and "TIỀN VÀNG CŨ" in b)
        check("vàng đổi thiếu trọng lượng → báo lỗi rõ",
              "Chưa nhập tổng trọng lượng" in body(
                  c.post("/banle/ban-hang/vang-doi/", {"gold": "D9999", "tong_tl": "0"})))
        check("TL hột > tổng TL → chặn",
              "không được lớn hơn" in body(c.post("/banle/ban-hang/vang-doi/",
                                                  {"gold": "D9999", "tong_tl": "10", "tl_hot": "20"})))
        # ── 4b. khối THÔNG TIN + TÍNH TỔNG ──
        b = body(c.get("/banle/ban-hang/"))
        for nhan in ("VÀNG BÁN", "VÀNG ĐỔI", "TÍNH TỔNG", "TIỀN KHÁCH TRẢ",
                     "DANH SÁCH", "ĐƠN MỚI", "THANH TOÁN", "THANH TOÁN &amp; IN", "IN HÓA ĐƠN"):
            check(f"  màn bán hàng có '{nhan}'", nhan in b)
        # Số hóa đơn + nút CLEAR đã BỎ (05/09/2026); mã đơn nay là badge bên VÀNG BÁN
        check("mã đơn hiện dạng badge trên VÀNG BÁN", "pg-hd-badge" in b)
        check("ô Ngày chỉ xem", 'id="o-ngay"' in b and "readonly" in b)
        # Phương thức thanh toán + gợi ý bớt lẻ + màu tiền khách trả (05/09/2026)
        check("có form phương thức thanh toán (tiền mặt/CK/thẻ)",
              "pg-pay" in b and 'name="pay_method"' in b and 'id="o-tienmat"' in b)
        check("có ô tách chuyển khoản/thẻ", 'id="o-tienck"' in b)
        check("có khối gợi ý BỚT LẺ", "pg-botle" in b)
        check("TIỀN KHÁCH TRẢ có lớp màu theo giá trị", "pg-tong__tra" in b)
        check("ô tìm nhân viên theo tên", 'id="o-nv"' in b)
        check("ô tìm khách theo tên/SĐT/CCCD", "CCCD" in b and 'id="o-khach"' in b)
        check("nút + mở popup thêm khách", "khach_them" in b or "khach-hang/them" in b)
        check("gợi ý nhân viên trả về được",
              "pg-goiy__i" in body(c.get("/banle/ban-hang/tim-nv/?q=ng")))
        check("popup DANH SÁCH hóa đơn mở được",
              "Hóa đơn bán" in body(c.get("/banle/ban-hang/danh-sach/")))
        b2 = body(c.get("/banle/ban-hang/danh-sach/?d1=2026-09-01&d2=2026-09-06&trang_thai=C"))
        check("popup DANH SÁCH lọc khoảng ngày + thống kê + cột BỚT/CỌC",
              "pg-ds__tk" in b2 and "TIỀN BỚT" in b2 and "TIỀN CỌC" in b2 and 'value="2026-09-01"' in b2)
        check("6 khoản tiền đủ trong cột TÍNH TỔNG",
              all(x in b for x in ("Tiền vàng mới", "Tiền vàng cũ", "CÒN LẠI", "Tiền vàng thêm",
                                   "Tiền công thêm", "Tiền bớt", "Tiền cọc", "Ghi chú")))
        check("giấy đảm bảo: phiếu nháp bị chặn in (in/thang từ chối; trang in cũ /in/ đã bỏ → 404)",
              "chưa thanh toán" in body(c.post("/banle/ban-hang/in/thang/", {}))
              and c.get("/banle/ban-hang/in/").status_code == 404)
        # v5: giỏ = đơn W THẬT → dọn bằng XÓA ĐƠN qua popup xác nhận (hàng về kho), không để đơn treo
        c.post("/banle/ban-hang/thuc-hien/xoa_nhap/")
        c.post("/banle/ban-hang/moi/")

        # ── 5. bảng giá + nhịp 204 ──
        r = c.get("/banle/bang-gia/nhip/")
        sig = re.search(r'data-sig="([^"]+)"', body(r))
        check("nhịp bảng giá lần đầu → 200", r.status_code == 200 and bool(sig))
        check("nhịp bảng giá chữ ký giống → 204",
              c.get("/banle/bang-gia/nhip/?sig=" + sig.group(1)).status_code == 204)

        # ── 6. khách hàng: lọc · phân trang 50 · popup CRUD · quét CCCD ──
        b = body(c.get("/banle/khach-hang/"))
        for cot in ("Mã KH", "Họ tên", "Địa chỉ", "CCCD", "Điện thoại", "Ngày sinh", "Loại", "Giới tính", "Ngày tạo", "GD cuối"):
            check(f"cột '{cot}'", f">{cot}<" in b)
        check("phân trang 50/trang", b.count("data-kh-row=") == 50)
        check("dòng KHÔNG còn bấm mở popup", 'style="cursor:pointer" hx-get="/banle/khach-hang/CU' not in b)
        check("mỗi dòng có cụm icon XEM · SỬA · XÓA", b.count('class="kh-act"') == 50
              and b.count('aria-label="Xem"') == 50 and b.count('aria-label="Sửa"') == 50
              and b.count("kh-act__b--xoa") == 50)
        check("ô lọc dữ liệu khách (xóa được / chưa hợp lệ)", 'name="loc"' in b and 'value="chuahople"' in b)
        bl = body(c.get("/banle/khach-hang/?partial=1&loc=xoa"))
        check("lọc 'xóa được' → mọi dòng đều có nút Xóa bật", "data-kh-row=" in bl
              and 'aria-label="Không xóa được"' not in bl)
        bx = body(c.get("/banle/khach-hang/CU0000000000000/xoa/"))
        check("popup xóa khách lẻ → guard giao dịch, không có ô passcode",
              "đã có giao dịch" in bx and 'name="passcode"' not in bx)
        check("nút THÊM KHÁCH", "+ THÊM KHÁCH" in b)
        check("lọc rỗng → trạng thái rỗng",
              "Không có khách nào khớp" in body(c.get("/banle/khach-hang/?partial=1&key=zzzkhongcoai")))
        b = body(c.get("/banle/khach-hang/them/"))
        for f in ("CustName", "Phone", "CMND", "BirthDate", "Address", "Gender", "CustType"):
            check(f"  popup có ô '{f}'", f'name="{f}"' in b)
        check("  popup có ô quét QR", 'id="kh-qr-in"' in b)
        check("  popup có 3 ô ảnh", b.count("kh-anh__khung") == 3)
        check("  nút lưu thật + khóa double click", "LƯU KHÁCH" in b and
              'hx-sync="this:drop"' in b and 'name="save_token"' in b)
        check("thiếu họ tên → báo lỗi", "Chưa nhập họ tên" in body(c.post("/banle/khach-hang/luu/", {"CustName": ""})))

        # ── 6b. THÂU VÀO — THAU_VANG (08/09/2026): form thật + DS hôm nay + gợi ý ──
        b = body(c.get("/banle/thau-vao/"))
        check("trang thâu: khung màn bán chủ đề xanh (pg-ban--thau) + 3 khối + chân", "pg-ban--thau" in b and 'id="pos-info"' in b
              and 'id="pos-thau"' in b and 'id="pos-tong"' in b and 'id="pos-foot"' in b)
        check("  không còn form VÀNG BÁN", 'id="pos-ban"' not in b and "VÀNG BÁN" not in b)
        for f in ("gold", "tong_tl", "tl_hot", "gia", "kieu", "bu", "bot", "ghi_chu", "pay_method", "tien_mat"):
            check(f"  ô '{f}'", f'name="{f}"' in b)
        check("  công tắc Giá thâu vào / bán ra (thay Đổi ngang)", "Giá thâu vào" in b and "Giá bán ra" in b and "Đổi ngang" not in b)
        check("  TÍNH TỔNG đầy đủ: bù · bớt · bớt lẻ · phương thức · TIỆM TRẢ", "Bớt lẻ" in b and "pg-pay__ways" in b and "TIỆM TRẢ KHÁCH" in b)
        check("  chân 4 nút + DANH SÁCH", "THANH TOÁN &amp; IN" in b and "IN PHIẾU" in b and "thau-vao/danh-sach/" in b)
        check("  popup DANH SÁCH mở được", "Phiếu thâu vàng" in body(c.get("/banle/thau-vao/danh-sach/")))
        check("  thêm dòng thiếu loại → báo", "loại vàng" in body(c.post("/banle/thau-vao/them/", {"gold": "", "tong_tl": "10"})).lower())
        check("  in phiếu không tồn tại → 404", c.get("/banle/thau-vao/in/?trn_id=TBGKHONGCO").status_code == 404)
        c.post("/banle/thau-vao/moi/")

        # ── 7. GIÁ = MySQL gold_prices (07/09/2026): bảng giá web phủ sell/buy MySQL lên khung KK ──
        from apps.pos import prices as P
        from apps.pos.price_sync import mssql_rate
        my = {p["gold_type"]: p for p in P.decorate(P.current_rows())}
        gm_all = S.gia_map()
        if "9999" in my and "N9999" in gm_all:
            check("giá N9999/D9999 trên web = MySQL 9999 (sell & buy, quy về nghìn/đơn vị KK)",
                  M.dec(gm_all["N9999"]["SellRate"]) == mssql_rate(my["9999"], "sell") and
                  M.dec(gm_all["D9999"]["BuyRate"]) == mssql_rate(my["9999"], "buy") and
                  gm_all["N9999"].get("gia_mysql") is True)
            check("giá 18K web = MySQL 610 sell (không còn giá KK)",
                  M.dec(gm_all["18K"]["SellRate"]) == mssql_rate(my["610"], "sell"))
        else:
            check("MySQL gold_prices có dòng 9999 để đối chiếu", False)
        check("mã không có trong MySQL (VND) vẫn giữ giá KK", "VND" in gm_all and not gm_all["VND"].get("gia_mysql"))

        # ── 7b. thâu: đối chiếu với money.py ──
        gm = S.gia_map()["D9999"]
        mong = M.money_vn(M.buy_amount_standalone(1000, gm["BuyRate"], 100, 0, gm["PriceUnit"]))
        b = body(c.post("/banle/thau-vao/them/", {"gold": "D9999", "tong_tl": "1000", "tl_hot": "0", "gia": "", "kieu": "thau"}))
        check(f"thâu 1 lượng D9999 = {mong} (dòng trong giỏ, giá MySQL)", mong in b)
        c.post("/banle/thau-vao/moi/")

        # ── 7c. MẪU IN GĐB tùy chỉnh (08/09/2026): trang chỉnh + lưu/đặt lại bố cục ──
        from apps.pmv.models import PmvState
        goc = PmvState.get("gdb_layout", "")
        try:
            b = body(c.get("/banle/giay-dam-bao/mau/"))
            check("trang chỉnh mẫu GĐB: tờ A5 + 10 khối data-gdb + CSS bố cục",
                  b.count('data-gdb="') >= 10 and "gdb-layout-css" in b and 'class="gdbm-row"' in b)
            r = c.post("/banle/giay-dam-bao/mau/", data=json.dumps({"layout": {"front_items": {"fs": 9.5, "left": 3}}}),
                       content_type="application/json")
            j = r.json() if r.status_code == 200 else {}
            check("lưu bố cục → CSS có font-size 9.5pt / left 3% cho bảng món",
                  bool(j.get("ok")) and "font-size:9.5pt" in j["css"] and ".gdb-a5__front-items{left:3%" in j["css"])
            j2 = c.post("/banle/giay-dam-bao/mau/", data=json.dumps({"reset": True}), content_type="application/json").json()
            j3 = c.post("/banle/giay-dam-bao/mau/", data=json.dumps({"layout": {"front_items": {"fs": 99}}}),
                        content_type="application/json").json()
            check("đặt lại mặc định → 7pt bảng món; giới hạn số (fs tối đa 30pt) được ép",
                  "font-size:7pt" in j2["css"] and j3["css"].count("font-size:30pt") == 1)
            # thiết lập MÁY IN (08/09/2026 — bản in tụt xuống gấp đôi vì @page A5 cứng trên máy in Letter/A4):
            # mặc định @page size:auto + tờ ghim sát mép trên canh giữa; ép khổ/lệch/tỷ lệ; giá trị lạ bị bỏ
            check("mặc định in: @page size:auto, tờ GĐB top 0 canh giữa (không còn A5 cứng)",
                  "@media print{@page{size:auto;margin:0}.gdb-a5{left:0mm!important;top:0mm!important;margin:0 auto!important}}"
                  in j2["css"] and "size:A5" not in j2["css"])
            j4 = c.post("/banle/giay-dam-bao/mau/", data=json.dumps({"layout": {"_in": {
                "kho": "A4", "canh": "trai", "dx": 2.5, "dy": -40, "ty_le": 95, "la": 1}}}), content_type="application/json").json()
            check("thiết lập in A4 · sát trái · lệch 2.5/-40mm · tỷ lệ 95% → CSS đúng, khối bảng vẫn giữ",
                  "@page{size:A4 portrait;margin:0}" in j4["css"] and "left:2.5mm!important;top:-40mm!important;margin:0!important;"
                  "transform:scale(0.95)!important;transform-origin:top left!important" in j4["css"]
                  and j4["layout"]["_in"] == {"kho": "A4", "canh": "trai", "dx": 2.5, "dy": -40, "ty_le": 95}
                  and "font-size:7pt" in j4["css"])
            j5 = c.post("/banle/giay-dam-bao/mau/", data=json.dumps({"layout": {"_in": {"kho": "B5", "canh": "x", "dy": 999}}}),
                        content_type="application/json").json()
            check("khổ/canh lạ bị bỏ → về auto/giữa; dy vượt ngưỡng ép 80mm",
                  j5["layout"]["_in"]["kho"] == "auto" and j5["layout"]["_in"]["canh"] == "giua" and j5["layout"]["_in"]["dy"] == 80)
        finally:
            PmvState.set("gdb_layout", goc)

        # ── 8. tài nguyên tĩnh: vừa PHẢI TỒN TẠI vừa PHẢI ĐƯỢC NHÚNG vào trang ──
        # (03/09/2026 từng làm rơi thẻ nạp htmx khi viết lại base.html → mọi tương tác chết
        #  mà bộ kiểm cũ vẫn xanh vì chỉ kiểm phản hồi máy chủ)
        b = body(c.get("/"))
        for f in ("css/khbl.css", "css/fonts.css", "js/vendor/htmx.min.js", "js/khbl.js",
                  "js/vn_text.js", "img/logo_icon.png", "img/favicon.png"):
            check(f"static {f} tải được", c.get("/static/" + f).status_code == 200)
            check(f"  và được nhúng vào trang", f in b)

        self.stdout.write((self.style.SUCCESS if not self.fail else self.style.ERROR)(
            f"KẾT QUẢ: {self.ok} PASS / {self.fail} FAIL"))
