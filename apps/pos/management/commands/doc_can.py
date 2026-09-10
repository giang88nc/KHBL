# -*- coding: utf-8 -*-
"""
ĐỌC THỬ CÂN VÀNG qua cổng COM (10/09/2026 — GĐ chốt phương án chẻ tín hiệu).

Bối cảnh: cân nối vào PC KK bằng RS232, PMVGoldRT giữ cổng đó. Ta chẻ thêm một nhánh CHỈ NGHE (chân 2 + chân 5)
sang máy chủ này. Lệnh này để xem nhánh mới có nhận được gì không và cân gửi ra khuôn dạng nào — CHỈ ĐỌC, không
bao giờ ghi ra cổng (nhánh mới cũng không đấu chân 3 nên về mặt vật lý không thể nói ngược lại cân).

    manage.py doc_can --liet-ke                 # xem máy đang thấy cổng COM nào
    manage.py doc_can --cong COM3               # nghe 15 giây ở 9600 8N1 (mặc định của hầu hết cân)
    manage.py doc_can --cong COM3 --do-baud     # không biết tốc độ → dò lần lượt 9600/4800/2400/19200/1200
    manage.py doc_can --cong COM3 --baud 4800 --giay 30 --tho
    manage.py doc_can --tcp 192.168.1.60:8899   # khi nhánh mới đi qua bộ chuyển RS232 sang mạng LAN

Đọc kết quả:
  · Ra chữ đọc được kèm số nhảy theo vật đặt lên cân → ĐÚNG cổng, đúng baud. Ghi lại dòng "GỢI Ý CẤU HÌNH" ở cuối.
  · Ra toàn ký tự rác (Ð, þ, ÿ...) → đúng cổng nhưng SAI BAUD, chạy lại với --do-baud.
  · Im lặng hoàn toàn → dây chưa thông. Đổi dây đang cắm chân 2 sang chân 3 rồi thử lại (vài cân đảo hai chân này),
    hoặc kiểm tra lại ốc chân 5, hoặc cân đang ở chế độ chỉ gửi khi bấm nút PRINT.
"""
import re
import time

from django.core.management.base import BaseCommand, CommandError

BAUD_THU = (9600, 4800, 2400, 19200, 1200, 38400)
# Cân điện tử thường gửi: "ST,GS,   12.345 g\r\n" (ổn định) hay "US,GS,..." (còn nhảy); nhiều cân vàng VN gửi
# gọn hơn, chỉ số và đơn vị. Bắt số có dấu chấm/phẩy, kèm dấu âm, rồi đơn vị nếu có.
SO = re.compile(r"[-+]?\d+(?:[.,]\d+)?")
DON_VI = re.compile(r"\b(g|kg|ct|gn|oz|ly|chi|chỉ)\b", re.I)
ON_DINH = re.compile(r"\b(ST|S|STABLE)\b", re.I)
CHUA_ON = re.compile(r"\b(US|U|UNSTABLE|MOVING)\b", re.I)


class Command(BaseCommand):
    help = "Đọc thử cân vàng qua cổng COM (chỉ nghe, không ghi) — dùng khi lắp nhánh chẻ tín hiệu"

    def add_arguments(self, p):
        p.add_argument("--liet-ke", action="store_true", help="Liệt kê cổng COM máy đang thấy rồi thoát")
        p.add_argument("--cong", help="Tên cổng, ví dụ COM3")
        p.add_argument("--tcp", help="Đọc qua mạng LAN khi dùng bộ chuyển RS232 sang Ethernet, dạng IP:cổng")
        p.add_argument("--baud", type=int, default=9600, help="Tốc độ, mặc định 9600")
        p.add_argument("--do-baud", action="store_true", help="Dò lần lượt các tốc độ thường gặp")
        p.add_argument("--giay", type=int, default=15, help="Nghe bao nhiêu giây, mặc định 15")
        p.add_argument("--tho", action="store_true", help="In thêm nguyên văn từng dòng dạng byte")

    def handle(self, *args, **o):
        if o.get("tcp"):
            return self._nghe_tcp(o["tcp"], max(3, o["giay"]), o["tho"])
        try:
            import serial
            from serial.tools import list_ports
        except ImportError:
            raise CommandError("Thiếu thư viện pyserial. Cài bằng: venv\\Scripts\\python -m pip install pyserial")
        self.serial = serial

        if o["liet_ke"] or not o["cong"]:
            cong = list(list_ports.comports())
            if not cong:
                self.stdout.write(self.style.WARNING(
                    "Máy này chưa thấy cổng COM nào. Cắm đầu chuyển USB sang COM của nhánh mới vào rồi chạy lại."))
            for c in cong:
                self.stdout.write(f"  {c.device:<8} {c.description}")
            if not o["cong"]:
                self.stdout.write("\nChọn một cổng rồi chạy: manage.py doc_can --cong COM3")
            return

        bauds = BAUD_THU if o["do_baud"] else (o["baud"],)
        giay = max(3, o["giay"]) if not o["do_baud"] else max(3, o["giay"] // 2)
        for baud in bauds:
            if self._nghe(o["cong"], baud, giay, o["tho"]):
                return
        self.stdout.write(self.style.WARNING(
            "\nKhông nhận được gì đọc hiểu được. Xem lại: dây chân 2 (thử đổi sang chân 3), ốc chân 5, "
            "và xem cân có đang ở chế độ chỉ gửi khi bấm PRINT không."))

    # ── nghe qua mạng LAN (bộ chuyển RS232 sang Ethernet chạy chế độ TCP Server) ─────────
    def _nghe_tcp(self, dia_chi, giay, tho):
        import socket

        try:
            ip, cong = dia_chi.rsplit(":", 1)
            cong = int(cong)
        except ValueError:
            raise CommandError("Địa chỉ phải có dạng IP:cổng, ví dụ 192.168.1.60:8899")
        self.stdout.write(f"\n— Nối tới {ip}:{cong} và nghe {giay}s. Đặt và nhấc vật trên cân vài lần —")
        try:
            with socket.create_connection((ip, cong), timeout=5) as s:
                s.settimeout(0.5)
                het, dem, doc_duoc, mau, du = time.time() + giay, 0, 0, [], b""
                while time.time() < het:
                    try:
                        goi = s.recv(4096)
                    except socket.timeout:
                        continue
                    if not goi:
                        break
                    du += goi
                    while b"\n" in du or len(du) > 512:
                        dong, _, du = du.partition(b"\n") if b"\n" in du else (du, b"", b"")
                        dem += 1
                        van = dong.decode("ascii", "replace").strip()
                        if tho:
                            self.stdout.write(f"    tho: {dong!r}")
                        if not van or van.count("�") > len(van) / 3:
                            continue
                        so = SO.findall(van.replace("�", ""))
                        if not so:
                            continue
                        doc_duoc += 1
                        dv = DON_VI.search(van)
                        co = "ổn định" if ON_DINH.search(van) else ("đang nhảy" if CHUA_ON.search(van) else "")
                        if len(mau) < 40:
                            mau.append(van)
                        self.stdout.write(f"    {van:<34} → số {so[-1]}"
                                          + (f" {dv.group(0)}" if dv else "") + (f" · {co}" if co else ""))
        except OSError as exc:
            self.stdout.write(self.style.ERROR(f"  Không nối được {ip}:{cong} — {exc}"))
            self.stdout.write("  Xem lại: bộ chuyển đã cắm nguồn và dây mạng chưa, IP đặt đúng dải của tiệm chưa, "
                              "chế độ có đang để TCP Server không.")
            return
        if doc_duoc:
            self.stdout.write(self.style.SUCCESS(f"\n  ĐỌC ĐƯỢC {doc_duoc}/{dem} dòng qua mạng LAN."))
            self.stdout.write(f"  GỢI Ý CẤU HÌNH: tcp={ip}:{cong} khuon_mau={mau[0]!r}")
            self.stdout.write("  Gửi nguyên dòng này cho Claude để nối số cân vào ô TỔNG TL của trang Thâu vào.")
        else:
            self.stdout.write(self.style.WARNING(
                "  Nối được nhưng không có số. Xem lại tốc độ baud đặt trong bộ chuyển có khớp cân không "
                "(thường 9600 8-N-1), và dây chân 2 với chân 5 đã bắt đúng chưa."))

    # ── nghe một tốc độ ──────────────────────────────────────────────────────
    def _nghe(self, cong, baud, giay, tho):
        self.stdout.write(f"\n— Nghe {cong} ở {baud} 8N1 trong {giay}s. Đặt và nhấc vật trên cân vài lần —")
        try:
            # timeout ngắn để vòng lặp còn nhả; KHÔNG mở với write, chỉ đọc.
            with self.serial.Serial(cong, baud, timeout=0.4) as cua:
                cua.reset_input_buffer()
                het = time.time() + giay
                dem, doc_duoc, mau = 0, 0, []
                dem_rac = 0
                while time.time() < het:
                    dong = cua.readline()
                    if not dong:
                        continue
                    dem += 1
                    van = dong.decode("ascii", "replace").strip()
                    if tho:
                        self.stdout.write(f"    tho: {dong!r}")
                    if van.count("�") > len(van) / 3:
                        dem_rac += 1
                        continue
                    so = SO.findall(van.replace("�", ""))
                    if not so:
                        continue
                    doc_duoc += 1
                    dv = DON_VI.search(van)
                    co = "ổn định" if ON_DINH.search(van) else ("đang nhảy" if CHUA_ON.search(van) else "")
                    if len(mau) < 40:
                        mau.append(van)
                    self.stdout.write(f"    {van:<34} → số {so[-1]}"
                                      + (f" {dv.group(0)}" if dv else "") + (f" · {co}" if co else ""))
        except self.serial.SerialException as exc:
            loi = str(exc)
            if "PermissionError" in loi or "Access is denied" in loi:
                self.stdout.write(self.style.ERROR(
                    f"  {cong} đang bị phần mềm khác giữ. Một cổng chỉ cho một chương trình mở — đóng chương trình "
                    "đang dùng cổng này rồi chạy lại."))
            else:
                self.stdout.write(self.style.ERROR(f"  Không mở được {cong}: {loi}"))
            return False

        if doc_duoc:
            self.stdout.write(self.style.SUCCESS(
                f"\n  ĐỌC ĐƯỢC {doc_duoc}/{dem} dòng ở {baud}. Nhánh chẻ tín hiệu đã thông."))
            self.stdout.write("  GỢI Ý CẤU HÌNH: " + f"cong={cong} baud={baud} khuon_mau={mau[0]!r}" if mau else "")
            self.stdout.write("  Gửi nguyên dòng này cho Claude để nối số cân vào ô TỔNG TL của trang Thâu vào.")
            return True
        if dem_rac:
            self.stdout.write(self.style.WARNING(
                f"  Có tín hiệu ({dem_rac} dòng) nhưng toàn ký tự rác → đúng cổng, SAI tốc độ. Chạy lại với --do-baud."))
        else:
            self.stdout.write(f"  Im lặng ở {baud}.")
        return False
