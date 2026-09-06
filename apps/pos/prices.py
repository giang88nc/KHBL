"""Giá MySQL là nguồn chính; mỗi lần lưu tạo một phiên bản và một lô đồng bộ."""
import hashlib
import json
import logging
import re
import uuid
from contextlib import contextmanager
from decimal import Decimal

from django.core import signing
from django.db import connection, transaction
from django.utils import timezone

from apps.pmv import gateway
from apps.pmv.models import PmvState
from .models import PriceBatch, PriceDisplay

logger = logging.getLogger(__name__)
SALT = 'khbl.price-edit.v1'


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
