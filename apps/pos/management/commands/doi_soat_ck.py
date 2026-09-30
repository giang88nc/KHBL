# -*- coding: utf-8 -*-
"""
ĐỐI SOÁT CHUYỂN KHOẢN CHẠY NGẦM cho phiếu thâu (GĐ chốt 10/09/2026).

Trước đây việc đối soát chỉ chạy khi có người mở trang /thau-vao-2/ và bấm nút "Đối soát CK" (hoặc để trang mở
cho JS tự gọi mỗi 5 giây). GĐ chốt bỏ nút đi, để máy chủ tự làm — nên phần đó chuyển vào đây và scheduler gọi
định kỳ; trang chỉ còn việc hiển thị kết quả.

Vẫn dùng ĐÚNG bộ luật của thau_payments (TP.tu_doi_soat — chung với trang tự quét), GĐ chốt 29/09/2026:
  · chỉ nối giao dịch TRONG NGÀY: nội dung ngân hàng mang đủ mã 12 số (cùng ngày phiếu);
  · CK nhiều lần cho một phiếu → CỘNG DỒN: mỗi giao dịch được nối khi tổng đã nối + nó ≤ số cần chuyển;
  · giao dịch phải có mã tham chiếu, chưa bị nhóm nào nhận, không tranh chấp với nhóm khác;
  · nhóm từng bị GỠ liên kết thì KHÔNG tự nối lại — người ta gỡ là có lý do, để họ tự xác nhận.
Sau đó ghi kết quả: bán-đổi + phiếu thâu → CardPay trên KK · cầm đồ → báo ngược KHCD (cầm đồ không có trên KK).
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
            # MỘT bộ luật với trang tự quét (TP.tu_doi_soat — GĐ chốt 29/09/2026: trong ngày, CK nhiều lần cộng dồn)
            noi = TP.tu_doi_soat(orders, LY_DO, thu=o["thu"], toi_da=TOI_DA)
            for od, b in noi:
                self.stdout.write(f"  {'[thử] ' if o['thu'] else ''}nối {','.join(od['ids'])} ← bank#{b['id']} {b['trans_amount']}")
            cho = sum(1 for od in orders if od["required"] > 0 and od["payment_status"] in ("unconfirmed", "review", "partial"))
            self.stdout.write(self.style.SUCCESS(
                f"Đối soát {d1:%d/%m}–{d2:%d/%m}: {len(noi)} giao dịch{' (chỉ thử)' if o['thu'] else ' vừa nối'}, "
                f"{cho} nhóm còn chờ / thiếu tiền."))
            if o["thu"]:
                return
            # GHI KẾT QUẢ mọi lượt (không chỉ khi vừa nối) — tự bù lượt ghi hỏng trước đó và nhóm vừa gỡ liên kết:
            #   bán-đổi + phiếu thâu cách mới → CardPay trên KK = tổng liên kết · cầm đồ → báo ngược KHCD (không KK).
            if any(od.get("nghiep_vu") in ("doi", "camdo") or od.get("ck_sau") for od in orders):
                tin = TP.dong_bo_sau(d1.isoformat(), d2.isoformat())
                if tin:
                    self.stdout.write("  " + tin.strip())
