"""Giá MySQL là nguồn chính; mỗi lần lưu tạo một phiên bản và một lô đồng bộ."""
import hashlib
import json
import logging
import re
import uuid
from contextlib import contextmanager
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from django.conf import settings
from django.core import signing
from django.db import connection, transaction
from django.utils import timezone

from apps.pmv import gateway
from apps.pmv.models import PmvState
from .models import PriceBatch, PriceDisplay

logger = logging.getLogger(__name__)
SALT = 'khbl.price-edit.v1'
KK_SYNC_SALT = 'khbl.price-kk-sync.v1'
PMV_REPORT_SYNC_SALT = 'khbl.price-pmv-report-sync.v1'


class PriceError(ValueError):
    pass


def current_rows(lock=False):
    with connection.cursor() as cursor:
        cursor.execute(
            'SELECT id, gold_type, gold_name, buy, sell, effective_at '
            'FROM gold_prices WHERE is_current = 1 ORDER BY gold_type, id DESC'
            + (' FOR UPDATE' if lock else '')
        )
        columns = [c[0] for c in cursor.description]
        rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
    return rows


def decorate(rows):
    from .price_sync import GOLD_UNITS
    display = {r.gold_type: r for r in PriceDisplay.objects.all()}
    for index, row in enumerate(rows, 1):
        option = display.get(row['gold_type'])
        row['pinned'] = option.pinned if option else False
        row['position'] = option.position if option else index
        row['spread'] = row['sell'] - row['buy']
        row['unit_choice'] = row['gold_type'] in ('BK', 'VT')
        row['unit'] = (option.unit if option else '') if row['unit_choice'] else GOLD_UNITS.get(row['gold_type'], 'chỉ')
    return sorted(rows, key=lambda r: (r['position'], r['gold_type']))


def fingerprint(rows):
    values = sorted((r['id'], r['gold_type'], str(r['buy']), str(r['sell']),
                     r['pinned'], r['position'], r.get('unit', '')) for r in rows)
    return hashlib.sha256(json.dumps(values).encode()).hexdigest()


def page_data():
    rows = decorate(current_rows())
    with connection.cursor() as cursor:
        cursor.execute(
            'SELECT id, gold_type, gold_name, buy, sell, effective_at, source '
            'FROM gold_prices WHERE is_current = 0 '
            'ORDER BY effective_at DESC, id DESC LIMIT 12'
        )
        columns = [c[0] for c in cursor.description]
        history = [dict(zip(columns, row)) for row in cursor.fetchall()]
    batch_ids = []
    for row in history:
        try:
            if (row['source'] or '').startswith('khbl:'):
                batch_ids.append(uuid.UUID(row['source'][5:]))
        except ValueError:
            pass
    historical_units = {p['id']: p.get('unit', '') for batch in PriceBatch.objects.filter(pk__in=batch_ids) for p in batch.payload}
    for row in history:
        row['unit'] = historical_units.get(row['id'], '' if row['gold_type'] in ('BK', 'VT') else 'chỉ')
    return {
        'prices': rows, 'price_history': history,
        'price_updated_at': max((r['effective_at'] for r in rows), default=None),
        'last_batch': PriceBatch.objects.first(),
        'sync_target': gateway.dich_hien_tai(),
    }


def _kk_to_mysql(rate, row):
    """Đổi thang I_XRATE (nghìn đồng/đơn vị KK) về đồng/đơn vị MySQL."""
    from .price_sync import GOLD_UNITS
    unit, target = row.get('unit'), GOLD_UNITS.get(row['gold_type'])
    if unit not in ('gram', 'chỉ') or not target:
        raise PriceError(f"{row['gold_name']}: chưa xác định đơn vị để đọc giá KK.")
    value = Decimal(str(rate)) * Decimal('1000')
    if unit == 'chỉ' and target == 'gram':
        value *= Decimal('3.75')
    elif unit == 'gram' and target == 'chỉ':
        value /= Decimal('3.75')
    return int(value.quantize(Decimal('1'), rounding=ROUND_HALF_UP))


def kk_sync_preview(rows=None):
    """So sánh trực tiếp KK → MySQL theo cặp mã vàng/dẻ đã cấu hình.

    Chỉ đọc. Giá mua lấy từ mã dẻ, giá bán lấy từ mã vàng, đúng chiều
    ngược với ``price_sync.mssql_rate``.
    """
    from .price_sync import GOLD_CODES, read_rates
    from apps.pmv.client import PmvClient

    rows = decorate(rows if rows is not None else current_rows())
    kk_rows = read_rates(PmvClient(target='kk', tag='gia_kk_kiem_tra'))
    by_code = {r['GoldCcy']: r for r in kk_rows if r.get('ShopID') == ''}
    changes, matched, issues = [], 0, []
    for row in rows:
        codes = GOLD_CODES.get(row['gold_type'])
        if not codes or any(code not in by_code for code in codes):
            issues.append(f"{row['gold_name']}: thiếu mã giá KK tương ứng.")
            continue
        gold, old = (by_code[codes[0]], by_code[codes[1]])
        if gold.get('Type') != 'G' or old.get('Type') != 'D':
            issues.append(f"{row['gold_name']}: nhóm mã KK không đúng cấu hình.")
            continue
        buy = _kk_to_mysql(old['BuyRate'], row)
        sell = _kk_to_mysql(gold['SellRate'], row)
        if buy <= 0 or sell <= 0:
            issues.append(f"{row['gold_name']}: KK trả về giá 0; chưa thể SYNC an toàn.")
            continue
        if buy > sell:
            issues.append(f"{row['gold_name']}: KK trả về giá mua lớn hơn giá bán, chưa thể đồng bộ.")
            continue
        item = {'id': row['id'], 'gold_type': row['gold_type'], 'gold_name': row['gold_name'],
                'old_buy': int(row['buy']), 'old_sell': int(row['sell']), 'buy': buy, 'sell': sell,
                'unit': row['unit'], 'codes': '/'.join(codes)}
        if buy != item['old_buy'] or sell != item['old_sell']:
            changes.append(item)
        else:
            matched += 1
    return {'rows': rows, 'changes': changes, 'matched': matched, 'issues': issues, 'checked_at': timezone.localtime()}


def kk_sync_token(preview, user_id):
    changes = [{key: item[key] for key in ('id', 'gold_type', 'buy', 'sell')} for item in preview['changes']]
    return signing.dumps({'fingerprint': fingerprint(preview['rows']), 'user': user_id, 'changes': changes}, salt=KK_SYNC_SALT)


def apply_kk_sync(token, user):
    """Ghi các chênh lệch KK đã được người dùng duyệt vào lịch sử MySQL."""
    try:
        data = signing.loads(token, salt=KK_SYNC_SALT, max_age=10 * 60)
        if data['user'] != user.pk or not isinstance(data['changes'], list):
            raise ValueError
    except (signing.BadSignature, KeyError, ValueError, TypeError) as exc:
        raise PriceError('Phiên duyệt SYNC KK đã hết hạn hoặc không hợp lệ. Hãy xem lại bảng giá.') from exc
    with save_lock():
        rows = decorate(current_rows(lock=True))
        if fingerprint(rows) != data['fingerprint']:
            raise PriceError('Giá MySQL vừa thay đổi. Hãy xem lại chênh lệch trước khi SYNC.')
        latest = kk_sync_preview(rows)
        actual = [{key: item[key] for key in ('id', 'gold_type', 'buy', 'sell')} for item in latest['changes']]
        if actual != data['changes']:
            raise PriceError('Bảng giá KK vừa thay đổi. Hãy SYNC lại để duyệt số liệu mới.')
        if not actual:
            return 0
        now = timezone.localtime().replace(tzinfo=None)
        with transaction.atomic(), connection.cursor() as cursor:
            for item in actual:
                cursor.execute('UPDATE gold_prices SET is_current=0 WHERE id=%s AND is_current=1', [item['id']])
                if cursor.rowcount != 1:
                    raise PriceError('Giá MySQL vừa thay đổi. Hãy xem lại trước khi SYNC.')
                row = next(row for row in rows if row['id'] == item['id'])
                cursor.execute(
                    'INSERT INTO gold_prices (gold_type,gold_name,buy,sell,effective_at,source,is_current) '
                    'VALUES (%s,%s,%s,%s,%s,%s,1)',
                    [item['gold_type'], row['gold_name'], item['buy'], item['sell'], now, 'kk:manual'])
        from django.core.cache import cache
        cache.delete('khbl:gia_mysql')
        cache.delete('khbl:xrate')
        cache.delete('khbl:gia_pmv_report_canh_bao:v1')
        return len(actual)


def _pmv_report_datetime(value):
    """Đọc thời điểm API UPSERT, chỉ chấp nhận ISO date-time không timezone."""
    try:
        parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except (TypeError, ValueError) as exc:
        raise PriceError('Nguồn PMV Report trả về thời điểm cập nhật không hợp lệ.') from exc
    return parsed.replace(tzinfo=None)


def _pmv_report_source_rows():
    """Đọc bảng hiện hành của pmv_report qua API UPSERT chính, không dùng credential DB nguồn."""
    url = getattr(settings, 'PMV_REPORT_GOLD_PRICES_URL', '').strip()
    if not url:
        raise PriceError('Chưa cấu hình nguồn bảng giá PMV Report.')
    try:
        request = Request(url, headers={'Accept': 'application/json', 'User-Agent': 'KHBL-price-sync/1.0'})
        with urlopen(request, timeout=getattr(settings, 'PMV_REPORT_GOLD_PRICES_TIMEOUT', 8)) as response:
            payload = json.loads(response.read().decode('utf-8'))
    except (HTTPError, URLError, TimeoutError, OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PriceError('Không đọc được bảng giá PMV Report. Hãy kiểm tra trang UPSERT chính rồi thử lại.') from exc
    if not isinstance(payload, dict) or not payload.get('ok') or not isinstance(payload.get('prices'), list):
        raise PriceError('Nguồn PMV Report không trả về bảng giá hợp lệ.')

    rows, seen = [], set()
    for item in payload['prices']:
        if not isinstance(item, dict):
            raise PriceError('Nguồn PMV Report có một dòng giá không hợp lệ.')
        gold_type = str(item.get('gold_type') or '').strip()
        gold_name = str(item.get('gold_name') or '').strip()
        source = str(item.get('source') or 'PMV_REPORT').strip()
        try:
            buy, sell = int(item.get('buy')), int(item.get('sell'))
        except (TypeError, ValueError) as exc:
            raise PriceError(f'{gold_type or "Một loại vàng"}: giá từ PMV Report không hợp lệ.') from exc
        if not gold_type or not gold_name or gold_type in seen:
            raise PriceError('PMV Report có mã vàng trống hoặc bị trùng; chưa thể SYNC an toàn.')
        if not 0 < buy <= sell <= 999_999_999_999:
            raise PriceError(f'{gold_name}: giá mua/bán từ PMV Report không hợp lệ.')
        rows.append({'gold_type': gold_type, 'gold_name': gold_name, 'buy': buy, 'sell': sell,
                     'effective_at': _pmv_report_datetime(item.get('effective_at')), 'source': source})
        seen.add(gold_type)
    if not rows:
        raise PriceError('PMV Report chưa có giá hiện hành để SYNC.')
    return rows


def _pmv_report_fingerprint(rows):
    values = sorted((r['gold_type'], r['gold_name'], int(r['buy']), int(r['sell']),
                     r['effective_at'].isoformat(), r['source']) for r in rows)
    return hashlib.sha256(json.dumps(values, ensure_ascii=False).encode()).hexdigest()


def pmv_report_sync_preview(rows=None):
    """So sánh PMV Report (nguồn chuẩn) với gold_prices local. Chỉ đọc."""
    local_rows = decorate(rows if rows is not None else current_rows())
    local_by_type = {}
    issues = []
    for row in local_rows:
        if row['gold_type'] in local_by_type:
            issues.append(f"{row['gold_type']}: MySQL local có nhiều dòng hiện hành; chưa thể SYNC an toàn.")
        local_by_type[row['gold_type']] = row

    source_rows = _pmv_report_source_rows()
    changes, matched = [], 0
    for source in source_rows:
        local = local_by_type.get(source['gold_type'])
        item = {**source, 'old_id': local['id'] if local else None,
                'old_buy': int(local['buy']) if local else None,
                'old_sell': int(local['sell']) if local else None,
                'unit': local['unit'] if local else 'chỉ'}
        if local and item['buy'] == item['old_buy'] and item['sell'] == item['old_sell'] and item['gold_name'] == local['gold_name']:
            matched += 1
        else:
            changes.append(item)
    return {'rows': local_rows, 'source_rows': source_rows, 'changes': changes, 'matched': matched,
            'issues': issues, 'checked_at': timezone.localtime(),
            'source_updated_at': max((r['effective_at'] for r in source_rows), default=None)}


def pmv_report_sync_token(preview, user_id):
    changes = [{key: item[key] for key in ('gold_type', 'gold_name', 'buy', 'sell', 'effective_at', 'source')}
               for item in preview['changes']]
    changes = [{**item, 'effective_at': item['effective_at'].isoformat()} for item in changes]
    return signing.dumps({'local': fingerprint(preview['rows']), 'source': _pmv_report_fingerprint(preview['source_rows']),
                          'user': user_id, 'changes': changes}, salt=PMV_REPORT_SYNC_SALT)


def apply_pmv_report_sync(token, user):
    """Duyệt PMV Report → gold_prices cục bộ; tuyệt đối không ghi PMV/MSSQL."""
    try:
        data = signing.loads(token, salt=PMV_REPORT_SYNC_SALT, max_age=10 * 60)
        if data['user'] != user.pk or not isinstance(data['changes'], list):
            raise ValueError
    except (signing.BadSignature, KeyError, ValueError, TypeError) as exc:
        raise PriceError('Phiên duyệt SYNC PMV Report đã hết hạn hoặc không hợp lệ. Hãy xem lại bảng giá.') from exc
    with save_lock():
        rows = decorate(current_rows(lock=True))
        if fingerprint(rows) != data['local']:
            raise PriceError('Giá MySQL local vừa thay đổi. Hãy xem lại chênh lệch trước khi SYNC.')
        latest = pmv_report_sync_preview(rows)
        if latest['issues']:
            raise PriceError('MySQL local có dữ liệu hiện hành chưa an toàn để SYNC. Hãy xem lại bảng chênh lệch.')
        actual_source = _pmv_report_fingerprint(latest['source_rows'])
        actual = [{key: item[key] for key in ('gold_type', 'gold_name', 'buy', 'sell', 'effective_at', 'source')}
                  for item in latest['changes']]
        actual = [{**item, 'effective_at': item['effective_at'].isoformat()} for item in actual]
        if actual_source != data['source'] or actual != data['changes']:
            raise PriceError('PMV Report vừa thay đổi. Hãy SYNC lại để duyệt số liệu mới.')
        if not actual:
            return 0
        current_by_type = {row['gold_type']: row for row in rows}
        max_position = max((row['position'] for row in rows), default=0)
        with transaction.atomic(), connection.cursor() as cursor:
            for item in latest['changes']:
                old = current_by_type.get(item['gold_type'])
                if old:
                    cursor.execute('UPDATE gold_prices SET is_current=0 WHERE id=%s AND is_current=1', [old['id']])
                    if cursor.rowcount != 1:
                        raise PriceError('Giá MySQL local vừa thay đổi. Hãy xem lại trước khi SYNC.')
                cursor.execute(
                    'INSERT INTO gold_prices (gold_type,gold_name,buy,sell,effective_at,source,is_current) '
                    'VALUES (%s,%s,%s,%s,%s,%s,1)',
                    [item['gold_type'], item['gold_name'], item['buy'], item['sell'], item['effective_at'],
                     'pmv_report:' + item['source'][:80]])
                if not old:
                    max_position += 1
                    from .price_sync import GOLD_UNITS
                    PriceDisplay.objects.get_or_create(gold_type=item['gold_type'], defaults={
                        'pinned': False, 'position': max_position,
                        'unit': GOLD_UNITS.get(item['gold_type'], 'chỉ'),
                    })
        from django.core.cache import cache
        cache.delete('khbl:gia_mysql')
        cache.delete('khbl:xrate')
        cache.delete('khbl:gia_pmv_report_canh_bao:v1')
        return len(actual)


def edit_token(rows, user_id, target):
    return signing.dumps({'batch': str(uuid.uuid4()), 'fingerprint': fingerprint(rows),
                          'user': user_id, 'target': target}, salt=SALT)


def read_token(value, user_id):
    try:
        data = signing.loads(value, salt=SALT, max_age=8 * 3600)
        uuid.UUID(data['batch'])
        if data['user'] != user_id:
            raise ValueError
        return data
    except (signing.BadSignature, KeyError, ValueError, TypeError) as exc:
        raise PriceError('Phiên cập nhật đã hết hạn hoặc không hợp lệ. Hãy tải lại trang.') from exc


def clean_amount(raw, label):
    value = str(raw).strip()
    if not re.fullmatch(r'(?:\d+|\d{1,3}(?:\.\d{3})+)', value):
        raise PriceError(f'{label}: nhập số đồng nguyên, ví dụ 8.350.000.')
    number = int(value.replace('.', ''))
    if not 0 < number <= 999_999_999_999:
        raise PriceError(f'{label}: giá phải lớn hơn 0 và không quá 999.999.999.999 đồng.')
    return number


def clean_rows(post, rows):
    if not rows or len({r['gold_type'] for r in rows}) != len(rows):
        raise PriceError('Bảng giá hiện hành trống hoặc có loại vàng bị trùng. Kiểm tra dữ liệu trước khi cập nhật.')
    if sorted(post.getlist('row_id')) != sorted(str(r['id']) for r in rows):
        raise PriceError('Danh sách giá đã thay đổi. Hãy tải lại trang trước khi cập nhật.')
    result = []
    for row in rows:
        key = str(row['id'])
        buy = clean_amount(post.get('buy_' + key, ''), row['gold_name'] + ' — giá mua')
        sell = clean_amount(post.get('sell_' + key, ''), row['gold_name'] + ' — giá bán')
        if buy > sell:
            raise PriceError(f"{row['gold_name']}: giá mua không được lớn hơn giá bán.")
        position = post.get('position_' + key, '')
        if not re.fullmatch(r'\d{1,4}', position) or not 1 <= int(position) <= 9999:
            raise PriceError(f"{row['gold_name']}: số thứ tự phải từ 1 đến 9999.")
        unit = post.get('unit_' + key, '') if row.get('unit_choice') else row.get('unit', 'chỉ')
        if unit not in ('gram', 'chỉ'):
            raise PriceError(f"{row['gold_name']}: chọn đơn vị đồng/gram hoặc đồng/chỉ trước khi cập nhật.")
        result.append({**row, 'buy': buy, 'sell': sell, 'position': int(position),
                       'pinned': post.get('pinned_' + key) == 'on', 'unit': unit})
    if len({r['position'] for r in result}) != len(result):
        raise PriceError('Số thứ tự giữa các loại vàng không được trùng nhau.')
    return result


@contextmanager
def save_lock():
    # Khóa liên tiến trình, giữ xuyên suốt commit MySQL và lần đồng bộ MSSQL.
    with connection.cursor() as cursor:
        cursor.execute("SELECT GET_LOCK('khbl:gold_prices:save', 0)")
        if cursor.fetchone()[0] != 1:
            raise PriceError('Một lô giá khác đang được cập nhật. Vui lòng thử lại sau.')
    try:
        yield
    finally:
        with connection.cursor() as cursor:
            cursor.execute("SELECT RELEASE_LOCK('khbl:gold_prices:save')")


def save_prices(post, user):
    token = read_token(post.get('edit_token', ''), user.pk)
    with save_lock():
        previous = PriceBatch.objects.filter(pk=token['batch'], user=user).first()
        if previous:
            return previous  # POST lặp không sinh thêm lịch sử hoặc ghi lại MSSQL.
        if token['target'] != gateway.dich_hien_tai():
            raise PriceError('Đích MSSQL đã thay đổi. Hãy tải lại trang trước khi cập nhật.')
        if PmvState.get('pmv_write_lock') == '1':
            raise PriceError('Kênh ghi đang khóa. Chưa lưu giá; hãy kiểm tra trang Hệ thống.')
        with transaction.atomic():
            rows = decorate(current_rows(lock=True))
            if fingerprint(rows) != token['fingerprint']:
                raise PriceError('Giá hoặc cách ghim đã được thay đổi ở nơi khác. Hãy tải lại trang để lấy bản mới.')
            cleaned = clean_rows(post, rows)
            from .price_sync import GOLD_CODES
            if any(r['gold_type'] not in GOLD_CODES for r in cleaned):
                raise PriceError('Có loại vàng chưa được cấu hình mã MSSQL; chưa lưu lô giá.')
            now = timezone.localtime().replace(tzinfo=None)
            payload = []
            with connection.cursor() as cursor:
                for row in cleaned:
                    cursor.execute('UPDATE gold_prices SET is_current=0 WHERE id=%s AND is_current=1', [row['id']])
                    if cursor.rowcount != 1:
                        raise PriceError('Giá vừa thay đổi; hãy tải lại trang.')
                    cursor.execute(
                        'INSERT INTO gold_prices (gold_type,gold_name,buy,sell,effective_at,source,is_current) '
                        'VALUES (%s,%s,%s,%s,%s,%s,1)',
                        [row['gold_type'], row['gold_name'], row['buy'], row['sell'], now,
                         'khbl:' + token['batch']])
                    payload.append({'id': cursor.lastrowid, 'gold_type': row['gold_type'],
                                    'buy': row['buy'], 'sell': row['sell'], 'unit': row['unit']})
                    PriceDisplay.objects.update_or_create(gold_type=row['gold_type'], defaults={
                        'pinned': row['pinned'], 'position': row['position'], 'unit': row['unit']})
            batch = PriceBatch.objects.create(id=uuid.UUID(token['batch']), user=user, target=token['target'], payload=payload)
        # MySQL đã COMMIT. Bất kỳ lỗi MSSQL nào đều để lại lô có thể thử lại.
        return sync_batch(batch)


def sync_batch(batch):
    from .price_sync import sync_prices
    try:
        sync_prices(batch.payload, batch.target, str(batch.pk))
    except Exception as exc:
        logger.exception('Đồng bộ lô giá %s thất bại', batch.pk)
        batch.status = 'sync_failed'
        # Không hiển thị thông tin kết nối/SQL thô lên giao diện.
        batch.error = str(exc) if isinstance(exc, (PriceError, gateway.PmvBlocked)) else 'Không thể ghi hoặc xác minh MSSQL. Kiểm tra kết nối và nhật ký hệ thống rồi thử lại.'
    else:
        batch.status = 'synced'
        batch.error = ''
        batch.synced_at = timezone.now()
    batch.save(update_fields=['status', 'error', 'synced_at'])
    from django.core.cache import cache
    cache.delete('khbl:gia_pmv_report_canh_bao:v1')
    return batch


def retry_sync(batch_id):
    with save_lock():
        batch = PriceBatch.objects.get(pk=batch_id)
        if batch.target != gateway.dich_hien_tai():
            raise PriceError('Hãy chọn lại đúng đích MSSQL của lô trước khi thử đồng bộ.')
        if batch.status == 'synced':
            return batch
        current = sorted((r['id'], r['gold_type'], int(r['buy']), int(r['sell']), r['unit']) for r in decorate(current_rows()))
        saved = sorted((r['id'], r['gold_type'], r['buy'], r['sell'], r['unit']) for r in batch.payload)
        if current != saved:
            raise PriceError('Lô này đã có giá mới thay thế. Không được đồng bộ lại giá cũ.')
        return sync_batch(batch)


def board_data():
    """Bảng niêm yết đã lưu + so sánh với đúng phiên bản giá trước đó."""
    data = page_data()
    rows = [r for r in data['prices'] if r['pinned']]
    with connection.cursor() as cursor:
        for row in rows:
            cursor.execute(
                'SELECT buy,sell,source,id FROM gold_prices WHERE gold_type=%s '
                'AND is_current=0 AND effective_at<=%s ORDER BY effective_at DESC,id DESC LIMIT 1',
                [row['gold_type'], row['effective_at']])
            previous = cursor.fetchone()
            same_unit = row['gold_type'] not in ('BK', 'VT')
            if previous and (previous[2] or '').startswith('khbl:'):
                try:
                    batch = PriceBatch.objects.filter(pk=uuid.UUID(previous[2][5:])).first()
                    same_unit = bool(batch and any(p['id'] == previous[3] and p.get('unit') == row['unit'] for p in batch.payload))
                except ValueError:
                    same_unit = False
            for index, field in enumerate(('buy', 'sell')):
                value = previous[index] if previous and same_unit else None
                row['previous_' + field] = value
                row[field + '_trend'] = 'flat' if value is None or row[field] == value else 'up' if row[field] > value else 'down'
    units = {r['unit'] for r in rows}
    data.update(prices=rows, board_unit='VNĐ/CHỈ' if units == {'chỉ'} else 'VNĐ/GRAM' if units == {'gram'} else 'VNĐ',
                mixed_units=len(units) > 1, price_updated_at=max((r['effective_at'] for r in rows), default=None))
    return data
