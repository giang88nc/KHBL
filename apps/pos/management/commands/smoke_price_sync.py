"""Kiểm chứng proc giá trên SANDBOX; trả lại giá gốc qua cùng proc vendor."""
import json
import uuid
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.pmv.client import PmvClient
from apps.pmv.diff import snapshot, diff
from apps.pos.price_sync import read_rates, build_rates, sync_prices, same_rates, xml_rates, GOLD_UNITS
from apps.pos.prices import current_rows


class Command(BaseCommand):
    help = 'Ghi thử giá trên MSSQL sandbox, diff 251 bảng, khôi phục giá gốc; không sửa giá MySQL/KK'

    def handle(self, *args, **options):
        client = PmvClient(target='sandbox', tag='smoke_price_sync')
        original = read_rates(client)
        path = Path(settings.BASE_DIR) / 'logs' / 'price_sync_sandbox_original.json'
        path.write_text(json.dumps(original, default=str, ensure_ascii=False, indent=2), encoding='utf-8')
        # Đơn vị tường minh cho dữ liệu THỬ; không suy đơn vị rồi ghi lại MySQL.
        payload = [{'gold_type': r['gold_type'], 'buy': int(r['buy']), 'sell': int(r['sell']),
                    'unit': GOLD_UNITS[r['gold_type']]} for r in current_rows()]
        expected = build_rates(original, payload)
        before = snapshot('sandbox', 'full')
        try:
            sync_prices(payload, 'sandbox', str(uuid.uuid4()))
            if not same_rates(read_rates(client), expected):
                raise CommandError('Giá sau ghi không khớp')
            after = snapshot('sandbox', 'full')
            changes = [r for r in diff(before, after) if r['status'] != 'same']
            if any(r['tbl'] not in ('I_XRATE', 'I_XRATE_HIST') for r in changes):
                raise CommandError(f'Có bảng ngoài dự kiến thay đổi: {changes}')
            self.stdout.write(f'PASS: {len(original)} dòng giá, {len(payload) * 2} mã mua/bán đúng; bảng thay đổi: {changes}')
            self.stdout.write(f'PASS: đối chiếu {len(before)} bảng; các loại vàng/ngoại tệ ngoài mapping giữ nguyên')
        finally:
            client.call('I_XRATE_Ins', write=True, p_RateDate=timezone.localdate().strftime('%d/%m/%Y'),
                        p_RateTime=timezone.localtime().strftime('%H:%M:%S'),
                        p_InputXML=xml_rates(original), p_ShopID='')
            if not same_rates(read_rates(client), original):
                raise CommandError(f'Chưa khôi phục được giá gốc; xem {path}')
            self.stdout.write('PASS: đã khôi phục toàn bộ giá gốc sandbox; lịch sử thử được giữ lại')
