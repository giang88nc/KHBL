"""Đồng bộ giá qua I_XRATE_Ins; giữ đủ bảng giá và kiểm chứng sau ghi."""
from decimal import Decimal, ROUND_HALF_UP

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

from apps.pmv import gateway
from apps.pmv.client import PmvClient
from apps.pmv.models import PmvState
from .prices import PriceError

# Nhãn tuổi vàng đã xác minh tại money.TUOI_LABEL và I_GOLD/I_XRATE.
GOLD_CODES = {'610': ('18K', 'D18K'), '980': ('24K', 'D24K'), '9999': ('N9999', 'D9999'),
              'BK': ('BK', 'DBk'), 'VT': ('VT', 'DT'), 'SJC': ('SJC', 'DSJC')}
GOLD_UNITS = {'610': 'chỉ', '980': 'chỉ', '9999': 'chỉ', 'SJC': 'chỉ', 'BK': 'gram', 'VT': 'gram'}
RATE_FIELDS = ('SellRate', 'BuyRate', 'SellRate2', 'POSellRate', 'POBuyRate', 'ChangeRate')


def read_rates(client):
    return client.query(
        'SELECT GoldCcy,RateDate,RateTime,SellRate,BuyRate,Type,SellRate2,'
        'POSellRate,POBuyRate,ChangeRate,ShopID FROM I_XRATE WITH (NOLOCK) ORDER BY GoldCcy')


def build_rates(existing, payload):
    if not existing or any(r['ShopID'] != '' for r in existing):
        # Proc ghi lịch sử từ TOÀN BỘ I_XRATE; nhiều ShopID cần một quy trình riêng.
        raise PriceError('Bảng MSSQL trống hoặc có nhiều chi nhánh; chưa thể thay bảng giá an toàn.')
    rows = [dict(r) for r in existing]
    by_code = {r['GoldCcy']: r for r in rows}
    if len(by_code) != len(rows):
        raise PriceError('MSSQL có mã giá trùng; chưa đồng bộ.')
    for price in payload:
        codes = GOLD_CODES.get(price['gold_type'])
        if not codes or any(code not in by_code for code in codes):
            raise PriceError(f"Thiếu mã MSSQL tương ứng loại vàng {price['gold_type']}.")
        for code, expected_type in zip(codes, ('G', 'D')):
            if by_code[code]['Type'] != expected_type:
                raise PriceError(f'Mã MSSQL {code} không đúng nhóm vàng đã cấu hình.')
            by_code[code]['BuyRate'] = mssql_rate(price, 'buy')
            by_code[code]['SellRate'] = mssql_rate(price, 'sell')
    for row in rows:
        if row['Type'] not in ('G', 'D', 'C'):
            raise PriceError('MSSQL có nhóm giá chưa hỗ trợ; chưa đồng bộ.')
        # Các trường này bị vendor đưa về 0 khi ghi nhóm D/C. Không để mất dữ liệu.
        zero_fields = ('SellRate2', 'POSellRate', 'ChangeRate') if row['Type'] == 'D' else RATE_FIELDS[2:] if row['Type'] == 'C' else ()
        if any(row[k] != 0 for k in zero_fields):
            raise PriceError(f"Mã {row['GoldCcy']} có giá phụ mà thủ tục không giữ được; chưa đồng bộ.")
    return rows


def mssql_rate(price, field):
    unit = price.get('unit', 'chỉ' if price['gold_type'] not in ('BK', 'VT') else '')
    if unit not in ('gram', 'chỉ'):
        raise PriceError(f"Chưa xác định đơn vị giá {price['gold_type']}.")
    value = Decimal(str(price[field]))
    target_unit = GOLD_UNITS[price['gold_type']]
    if unit != target_unit:
        value = value / Decimal('3.75') if unit == 'chỉ' else value * Decimal('3.75')
    # Cột I_XRATE là numeric(...,3), giá lưu theo nghìn: làm tròn đến đồng.
    return (value / 1000).quantize(Decimal('0.001'), rounding=ROUND_HALF_UP)


def xml_rates(rows):
    tables = {'C': 'TB_NGOAITE', 'G': 'TB_GIAVANG', 'D': 'TB_GIADE'}
    groups = []
    for kind, table in tables.items():
        values = []
        for row in rows:
            if row['Type'] != kind:
                continue
            values.append({**row, 'GoldDesc': row['GoldCcy'], 'GoldCcyDesc': row['GoldCcy'],
                           'RateDate': timezone.localdate().strftime('%d/%m/%Y'),
                           'Notes': '', 'OrderBy': '0', 'PriceCcy': 'VND'})
        groups.append((table, values))
    return PmvClient.xml_nhieu_bang(groups)


def same_rates(left, right):
    def normalized(rows):
        return sorted((r['GoldCcy'], r['ShopID'], r['Type'],
                       *(Decimal(str(r[k])) for k in RATE_FIELDS)) for r in rows)
    return normalized(left) == normalized(right)


def backup_before_kk(batch_id):
    directory = settings.PMV_BACKUP_DIR_KK.rstrip('\\')
    path = directory + '\\PRICE_' + batch_id + '_' + timezone.now().strftime('%Y%m%d%H%M%S') + '.bak'
    # Các đường dẫn cấu hình được escape như SQL literal; không dùng dữ liệu form.
    quoted_dir, quoted_path = directory.replace("'", "''"), path.replace("'", "''")
    gateway.pmv_admin(f"EXEC master.dbo.xp_create_subdir N'{quoted_dir}'", tag='price_backup')
    gateway.pmv_admin(f"BACKUP DATABASE [PMV_BANLE_KH2] TO DISK=N'{quoted_path}' WITH COPY_ONLY, INIT, CHECKSUM", tag='price_backup', timeout=1800)
    gateway.pmv_admin(f"RESTORE VERIFYONLY FROM DISK=N'{quoted_path}' WITH CHECKSUM", tag='price_backup', timeout=1800)
    PmvState.set('pmv_last_price_backup', path + ' | VERIFY OK')


def sync_prices(payload, target, batch_id):
    if PmvState.get('pmv_write_lock') == '1':
        raise PriceError('Kênh ghi MSSQL đang khóa; giá MySQL vẫn được giữ lại.')
    if target == 'kk' and not gateway.duoc_ghi_kk():
        raise PriceError('Chốt ghi máy KK đang tắt; giá MySQL vẫn được giữ lại.')
    client = PmvClient(target=target, tag='bang_gia:' + batch_id)
    original = read_rates(client)
    expected = build_rates(original, payload)
    if same_rates(original, expected):
        return  # Hữu ích khi lần ghi trước thành công nhưng phản hồi bị mất.
    if target == 'kk':
        backup_before_kk(batch_id)
        # Backup có thể mất thời gian: lấy lại phần giá không sửa ngay trước khi ghi.
        expected = build_rates(read_rates(client), payload)
    client.call('I_XRATE_Ins', write=True,
                p_RateDate=timezone.localdate().strftime('%d/%m/%Y'),
                p_RateTime=timezone.localtime().strftime('%H:%M:%S'),
                p_InputXML=xml_rates(expected), p_ShopID='')
    if not same_rates(read_rates(client), expected):
        raise PriceError('Đã gọi ghi MSSQL nhưng dữ liệu đọc lại chưa khớp; hãy kiểm tra rồi thử đồng bộ lại.')
    cache.delete('khbl:xrate')
