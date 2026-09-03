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
                             ("/banle/hoa-don/", "Hóa đơn trong ngày")]:
            r = c.get(url)
            check(f"GET {url}", r.status_code == 200 and phai_co in body(r), f"status={r.status_code}")
        check("URL thiếu dấu / cuối vẫn vào được", c.get("/banle/ban-hang", follow=True).status_code == 200)

        # ── 2. TRANG CHỦ: logo · menu ngang icon to · tài khoản · chân trang đồng hồ ──
        b = body(c.get("/"))
        check("trang chủ: có logo", 'img/logo_icon.png' in b and 'class="home-brand"' in b)
        check("trang chủ: MENU NGANG đủ 6 mục", b.count('class="home-menu__i') == 6)
        for nhan in ("BÁN HÀNG", "THÂU VÀO", "BẢNG GIÁ", "KHÁCH HÀNG", "HÓA ĐƠN", "HỆ THỐNG"):
            check(f"  mục '{nhan}'", f"<b>{nhan}</b>" in b)
        check("trang chủ: icon to 34px trong menu", b.count('width="34" height="34"') >= 6)
        check("trang chủ: KHÔNG còn rail bên trái", 'class="khbl-rail"' not in b)
        check("trang chủ: hiện tên người đăng nhập", 'class="home-me' in b and TK in b)
        check("trang chủ: hiện nhân viên + két của tài khoản",
              "NV <span" in b and ("két" in b or "khong-co-ket" in b))
        check("trang chủ: có nút Đăng xuất", "Đăng xuất" in b)
        check("trang chủ: 4 thẻ số liệu", b.count("dash-kpi__val") == 4)
        check("trang chủ: biểu đồ 7 cột", b.count("dash-bar__col") == 7)
        check("trang chủ: bảng tồn theo nhóm vàng", "Tồn theo nhóm vàng" in b)

        # ── 3. CHÂN TRANG ──
        check("chân trang: tên công ty", "khbl-foot__co" in b and "KIM HẠNH" in b.upper())
        check("chân trang: thông tin phần mềm", "khbl-foot__pm" in b and "KHBL" in b)
        check("chân trang: ô đồng hồ thời gian thực", 'id="khbl-dongho"' in b and "data-gio" in b)
        check("chân trang có mặt ở trang khác (khách hàng)", "khbl-foot" in body(c.get("/banle/khach-hang/")))
        check("màn BÁN HÀNG không chèn chân trang (đã có thanh phím)",
              "khbl-foot" not in body(c.get("/banle/ban-hang/")))

        # ── 4. bán hàng: quét mã thật + vàng cũ + tổng ──
        c.post("/banle/ban-hang/moi/")
        ma = S.client("smoke").query(
            "SELECT TOP 1 ProductCode FROM T_PRODUCT WITH (NOLOCK) "
            "WHERE Status='I' AND TaskPrice>0 AND ISNULL(DiamondWeight,0)>0 ORDER BY InDate DESC")[0]["ProductCode"]
        b = body(c.post("/banle/ban-hang/quet/", {"ma": ma}))
        check(f"quét mã thật {ma} → thẻ món + phép tính", ma in b and "khbl-calc" in b)
        check("quét lại cùng mã → báo đã có trong phiếu",
              "đã có trong phiếu" in body(c.post("/banle/ban-hang/quet/", {"ma": ma})))
        check("mã không tồn tại → câu lỗi vendor P-002",
              "P-002" in body(c.post("/banle/ban-hang/quet/", {"ma": "ZZZKHONGCO"})))
        b = body(c.post("/banle/ban-hang/vang-cu/", {"gold": "D9999", "gw": "1000", "pct": "100"}))
        check("thêm vàng cũ → khối THÂU hiện", "Vàng cũ khách đưa" in b and "has-mua" in b)
        check("popup phiếu tạm mở được", "Phiếu tạm" in body(c.get("/banle/ban-hang/phieu/")))
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
        check("  nút lưu ở trạng thái chờ", "khbl-btn--cho" in b)
        b = body(c.post("/banle/khach-hang/luu/", {"CustName": "Nguyễn Thị Thử", "Phone": "0906 737 668",
                                                   "CMND": "096088009068", "BirthDate": "1988-04-07",
                                                   "Address": "Thủ Đức", "Gender": "1", "CustType": "VIP"}))
        check("lưu khách: hiện tham số sẽ gửi", "I_CUSTOMER_Ins" in b and "0906737668" in b)
        check("thiếu họ tên → báo lỗi", "Chưa nhập họ tên" in body(c.post("/banle/khach-hang/luu/", {"CustName": ""})))

        # ── 7. thâu: đối chiếu với money.py ──
        gm = S.gia_map()["D9999"]
        mong = M.money_vn(M.buy_amount_standalone(1000, gm["BuyRate"], 100, 0, gm["PriceUnit"]))
        b = body(c.post("/banle/thau-vao/tinh/", {"gold": "D9999", "gw": "1000", "pct": "100",
                                                  "add_money": "0", "dau": "+"}))
        check(f"thâu 1 lượng D9999 = {mong}", mong in b)

        # ── 8. tài nguyên tĩnh ──
        for f in ("css/khbl.css", "js/khbl.js", "js/vn_text.js", "img/logo_icon.png", "img/favicon.png"):
            check(f"static {f}", c.get("/static/" + f).status_code == 200)

        self.stdout.write((self.style.SUCCESS if not self.fail else self.style.ERROR)(
            f"KẾT QUẢ: {self.ok} PASS / {self.fail} FAIL"))
