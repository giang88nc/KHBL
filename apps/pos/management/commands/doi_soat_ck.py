# -*- coding: utf-8 -*-
"""
ĐỐI SOÁT CHUYỂN KHOẢN CHẠY NGẦM cho phiếu thâu (GĐ chốt 10/09/2026).

Trước đây việc đối soát chỉ chạy khi có người mở trang /thau-vao-2/ và bấm nút "Đối soát CK" (hoặc để trang mở
cho JS tự gọi mỗi 5 giây). GĐ chốt bỏ nút đi, để máy chủ tự làm — nên phần đó chuyển vào đây và scheduler gọi
định kỳ; trang chỉ còn việc hiển thị kết quả.

Vẫn dùng ĐÚNG bộ luật của thau_payments, không nới lỏng gì:
  · chỉ nối khi nội dung ngân hàng nhắc đúng mã phiếu, số tiền bằng đúng tổng cần chuyển của nhóm;
  · giao dịch phải có mã tham chiếu, chưa bị nhóm nào nhận, không tranh chấp với nhóm khác;
  · nhóm đã có liên kết (kể cả đã gỡ) thì KHÔNG tự nối lại — người ta gỡ là có lý do, để họ tự xác nhận.
Mỗi lượt chốt tối đa 30 nhóm cho nhẹ máy KK; còn dư thì lượt sau chạy tiếp.

    manage.py doi_soat_ck                # hôm nay
    manage.py doi_soat_ck --ngay 3       # gồm 3 ngày gần nhất (bắt các khoản chuyển trễ)
    manage.py doi_soat_ck --thu          # chỉ xem sẽ nối gì, KHÔNG ghi
"""
import datetime as dt

from django.core.management.base import BaseCommand

from apps.pos import thau_payments as TP
from apps.pos.bank_reconcile import single_run

TOI_DA = 30
LY_DO = "Chạy ngầm: khớp mã phiếu trong nội dung ngân hàng, đủ tiền, không tranh chấp."


class Command(BaseCommand):
    help = "Đối soát tiền chuyển khoản của phiếu thâu với thông báo ngân hàng (chạy ngầm, không cần mở trang)"

    def add_arguments(self, p):
        p.add_argument("--ngay", type=int, default=1, help="Số ngày gần nhất cần soát, mặc định 1 (hôm nay)")
        p.add_argument("--thu", action="store_true", help="Chỉ liệt kê sẽ nối gì, không ghi vào sổ")

    def handle(self, *args, **o):
        d2 = dt.date.today()
        d1 = d2 - dt.timedelta(days=max(1, o["ngay"]) - 1)
        with single_run() as duoc:      # khóa MySQL dùng chung: không bao giờ chạy trùng với lượt bấm tay trên web
            if not duoc:
                self.stdout.write("Đang có lượt đối soát khác chạy — bỏ lượt này.")
                return
            orders, live = TP.inspect(d1.isoformat(), d2.isoformat())
            if not live:
                self.stdout.write(self.style.WARNING("Đang đọc kho lịch sử, không phải máy KK — bỏ lượt để khỏi chốt nhầm."))
                return
            xong = cho = 0
            for od in orders:
                hop = [b for b in od["candidates"]
                       if b["can_link"] and not b["ambiguous"] and b["exact"] and b.get("ref_code")]
                if len(hop) != 1 or od["auto_blocked"]:
                    cho += od["required"] > 0 and od["payment_status"] in ("unconfirmed", "review")
                    continue
                if o["thu"]:
                    self.stdout.write(f"  [thử] {','.join(od['ids'])} ← bank#{hop[0]['id']} {hop[0]['trans_amount']}")
                    xong += 1
                    continue
                try:
                    TP.create_link(od, hop[0], reason=LY_DO)
                    xong += 1
                    self.stdout.write(f"  nối {','.join(od['ids'])} ← bank#{hop[0]['id']} {hop[0]['trans_amount']}")
                except ValueError as exc:      # dữ liệu vừa đổi giữa chừng — để lượt sau, không phải lỗi
                    self.stdout.write(f"  bỏ qua {','.join(od['ids'])}: {exc}")
                if xong >= TOI_DA:
                    break
            self.stdout.write(self.style.SUCCESS(
                f"Đối soát {d1:%d/%m}–{d2:%d/%m}: {xong} nhóm{' (chỉ thử)' if o['thu'] else ' vừa xác nhận'}, "
                f"{cho} nhóm còn chờ người kiểm."))
            # HÓA ĐƠN BÁN-ĐỔI dư (GĐ chốt 19/09/2026, phương án a): CardPay trên KK = tổng liên kết còn hiệu lực.
            # Chạy mọi lượt (không chỉ khi vừa nối) nên cũng tự bù lượt ghi hỏng trước đó và nhóm vừa gỡ liên kết.
            # Không có hóa đơn đổi nào thì bỏ qua — lượt chỉ-thâu không đọc thêm máy KK.
            if not o["thu"] and any(od.get("nghiep_vu") == "doi" for od in orders):
                from apps.pos import ck_tra_khach as CK
                orders2, live2 = TP.inspect(d1.isoformat(), d2.isoformat())
                if live2:
                    ghi, loi = CK.dong_bo_ck_kk(orders2)
                    if ghi or loi:
                        self.stdout.write(f"  CK trả khách lên KK: {ghi} hóa đơn vừa ghi"
                                          + (f", {len(loi)} lỗi: {'; '.join(loi)}" if loi else ""))
