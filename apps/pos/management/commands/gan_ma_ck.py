"""Nhận diện mã chứng từ trong nội dung CK → ghi 3 cột riêng của KHBL trên bank_notifications (GĐ chốt 22/09/2026).

    manage.py gan_ma_ck --tao-cot          # thêm cột ma_chung_tu / loai_chung_tu / nhan_dien_luc (+ chỉ mục), an toàn chạy lại
    manage.py gan_ma_ck                    # THỬ: nhận diện + UPDATE trong transaction rồi ROLLBACK, in thống kê
    manage.py gan_ma_ck --ghi [--tu 2026-09-01] [--het]   # lưu thật; --het = chạy tới khi hết dòng chưa xử lý

Job scheduler gọi gan_ma(…) mỗi 15 giây. Thuật toán + đặc tả: skill nhan-dien-ma-chung-tu-ck (apps/pos/ma_chung_tu_ck.py).
Chỉ ghi 3 cột mới — KHÔNG đụng bill_code_raw / is_check / cột nào của BANLE_V5.
"""
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction

from apps.pos import ma_chung_tu_ck as MC


class Command(BaseCommand):
    help = "Nhận diện mã chứng từ trong nội dung CK và ghi vào cột riêng KHBL của bank_notifications"

    def add_arguments(self, parser):
        parser.add_argument('--tao-cot', action='store_true', help='Thêm 3 cột + chỉ mục nếu chưa có')
        parser.add_argument('--ghi', action='store_true', help='Lưu thật (mặc định chạy THỬ rồi rollback)')
        parser.add_argument('--tu', help='Chỉ xét dòng có transaction_time >= ngày này (YYYY-MM-DD)')
        parser.add_argument('--het', action='store_true', help='Lặp tới khi hết dòng chưa xử lý (chạy bù dữ liệu cũ)')

    def handle(self, *args, **o):
        with connection.cursor() as cur:
            if o['tao_cot']:
                moi = MC.tao_cot(cur)
                self.stdout.write(self.style.SUCCESS('Đã thêm cột: ' + (', '.join(moi) if moi else '(đã có đủ)')))
                return
            cur.execute("SELECT COUNT(*) FROM information_schema.columns WHERE table_schema=DATABASE() "
                        "AND table_name='bank_notifications' AND column_name='nhan_dien_luc'")
            if not cur.fetchone()[0]:
                raise CommandError('Chưa có cột — chạy trước: manage.py gan_ma_ck --tao-cot')
            tong = {}
            while True:
                with transaction.atomic():
                    dem = MC.gan_ma(cur, tu=o.get('tu'))
                    if not o['ghi']:
                        transaction.set_rollback(True)
                n = dem.pop('_so_dong')
                for k, v in dem.items():
                    tong[k] = tong.get(k, 0) + v
                if not (o['ghi'] and o['het'] and n):
                    break
            nhan = 'GHI THẬT' if o['ghi'] else 'THỬ (rollback)'
            self.stdout.write(self.style.SUCCESS(f'{nhan}: {sum(tong.values())} dòng · ' +
                                                 ' · '.join(f'{k} {v}' for k, v in sorted(tong.items()))))
