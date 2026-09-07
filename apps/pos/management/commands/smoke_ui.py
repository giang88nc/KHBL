"""
Bộ kiểm GIAO DIỆN bán lẻ: URL /banle/, trang chủ, bán hàng, khách hàng, thâu, bảng giá, hóa đơn.
Chạy: manage.py smoke_ui        (đọc PMV thật + sandbox, KHÔNG ghi gì)
"""
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
                             ("/banle/ban-hang/", "Quét tem"), ("/banle/thau-vao/", "Thâu vàng vào"),
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
        check("giấy đảm bảo: phiếu ĐANG SỬA (nháp) bị chặn in 403 (GĐ 07/09: chốt mới in)",
              c.get("/banle/ban-hang/in/").status_code == 403)
        # v5: giỏ = đơn W THẬT trên sandbox → dọn bằng XÓA ĐƠN (hàng về kho), không để đơn treo
        c.post("/banle/ban-hang/huy/")
        c.post("/banle/ban-hang/moi/")

        # ── 5. bảng giá + nhịp 204 ──
        r = c.get("/banle/bang-gia/nhip/")
        sig = re.search(r'data-sig="([^"]+)"', body(r))
        check("nhịp bảng giá lần đầu → 200", r.status_code == 200 and bool(sig))
        check("nhịp bảng giá chữ ký giống → 204",
              c.get("/banle/bang-gia/nhip/?sig=" + sig.group(1)).status_code == 204)

        # ── 6. khách hàng: lọc · phân trang 50 · popup CRUD · quét CCCD ──
        b = body(c.get("/banle/khach-hang/"))
        for cot in ("Mã KH", "Họ tên", "Địa chỉ", "CCCD", "Điện thoại", "Ngày sinh", "Loại", "Giới tính", "GD cuối"):
            check(f"cột '{cot}'", f">{cot}<" in b)
        check("phân trang 50/trang", b.count('hx-get="/banle/khach-hang/CU') == 50)
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
        b = body(c.post("/banle/thau-vao/tinh/", {"gold": "D9999", "gw": "1000", "pct": "100",
                                                  "add_money": "0", "dau": "+"}))
        check(f"thâu 1 lượng D9999 = {mong}", mong in b)

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
