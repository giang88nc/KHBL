"""
Bộ thử bộ chuẩn hóa chữ tiếng Việt (skill `chuan-hoa-tieng-viet`).
Chạy: manage.py smoke_vn_text   — KHÔNG đụng CSDL, chạy được mọi lúc.
"""
from django.core.management.base import BaseCommand

from apps.pos import cccd
from apps.pos.vn_text import bo_dau, chuan_hoa, hoa_dau_tu

CHUOI_THAT = ("096088009068|381472415|Tr0198017601980161ng Ng022501870141c Giang|07041988|Nam|"
              "Kh01950179m 4, TT. N0196m C0196n, N0196m C0196n, C01950160 Mau|15092023")
MONG_DOI = ("096088009068|381472415|Trương Ngọc Giang|07041988|Nam|"
            "Khóm 4, TT. Năm Căn, Năm Căn, Cà Mau|15092023")


class Command(BaseCommand):
    help = "Kiểm thử chuẩn hóa chữ tiếng Việt bị hỏng mã"

    def handle(self, *args, **opts):
        ok = fail = 0

        def check(ten, cond, chi_tiet=""):
            nonlocal ok, fail
            if cond:
                ok += 1
                self.stdout.write(f"  PASS  {ten}")
            else:
                fail += 1
                self.stdout.write(self.style.ERROR(f"  FAIL  {ten}  {chi_tiet}"))

        # ① byte-số
        for goc, mong in [
            ("Tr0198017601980161ng Ng022501870141c Giang", "Trương Ngọc Giang"),
            ("Kh01950179m 4", "Khóm 4"),
            ("C01950160 Mau", "Cà Mau"),
        ]:
            kq, _ = chuan_hoa(goc)
            check(f"byte-số: {goc[:22]}… → {mong}", kq == mong, f"ra {kq!r}")

        # ② byte bị nuốt — chỉ điền khi duy nhất 1 khả năng
        kq, cb = chuan_hoa("TT. N0196m C0196n")
        check("byte bị nuốt: 'N?m C?n' → 'Năm Căn'", kq == "TT. Năm Căn", f"ra {kq!r}")
        check("  có báo cho người dùng biết đã dựng lại", any("dựng lại" in c for c in cb))

        # ③ mojibake
        for goc, mong in [("Nguyá»…n Thá»‹ Lan", "Nguyễn Thị Lan"),
                          ("TrÆ°Æ¡ng Ngá»?c", None)]:
            kq, _ = chuan_hoa(goc)
            if mong:
                check(f"mojibake: {goc} → {mong}", kq == mong, f"ra {kq!r}")
            else:
                check("mojibake mất byte: sửa được phần lành, chừa chỗ hỏng",
                      kq.startswith("Trương Ng") and "?" in kq or "�" in kq, f"ra {kq!r}")

        # ④ thực thể HTML + percent
        kq, _ = chuan_hoa("Xu&#226;n")
        check("thực thể HTML: 'Xu&#226;n' → 'Xuân'", kq == "Xuân", f"ra {kq!r}")
        kq, _ = chuan_hoa("Tr%C6%B0%C6%A1ng%20Ng%E1%BB%8Dc")
        check("percent: → 'Trương Ngọc'", kq == "Trương Ngọc", f"ra {kq!r}")

        # ⑤ KHÔNG được đụng vào chuỗi vốn đã đúng
        for t in ("Trương Ngọc Giang", "NGUYEN VAN A", "Chị Hạnh", "Khóm 4, TT. Năm Căn, Cà Mau",
                  "0906737668", ""):
            kq, cb = chuan_hoa(t)
            check(f"giữ nguyên chuỗi đúng: {t!r}", kq == t and not cb, f"ra {kq!r} {cb}")

        # ⑥ tiện ích
        check("bo_dau", bo_dau("Trương Ngọc Đá") == "Truong Ngoc Da")
        check("hoa_dau_tu IN HOA", hoa_dau_tu("TRẦN NGỌC GIANG") == "Trần Ngọc Giang")
        check("hoa_dau_tu giữ nguyên chuỗi thường", hoa_dau_tu("Trần Ngọc Giang") == "Trần Ngọc Giang")

        # ⑦ chuỗi THẬT của GĐ, đi qua trọn luồng quét CCCD
        kq, _ = chuan_hoa(CHUOI_THAT)
        check("chuỗi quét thật khôi phục trọn vẹn", kq == MONG_DOI, f"\n     ra   {kq!r}\n     cần {MONG_DOI!r}")
        d = cccd.parse(CHUOI_THAT)
        check("cccd.parse: họ tên", d and d["ho_ten"] == "Trương Ngọc Giang", str(d))
        check("cccd.parse: CCCD + ngày sinh + giới tính",
              d and d["cmnd"] == "096088009068" and d["ngay_sinh"] == "1988-04-07" and d["gioi_tinh"] == "1")
        check("cccd.parse: địa chỉ đủ dấu",
              d and d["dia_chi"] == "Khóm 4, TT. Năm Căn, Năm Căn, Cà Mau", d and d["dia_chi"])
        check("cccd.parse: chuỗi rác trả None", cccd.parse("abc|def") is None)

        self.stdout.write((self.style.SUCCESS if not fail else self.style.ERROR)(
            f"KẾT QUẢ: {ok} PASS / {fail} FAIL"))
