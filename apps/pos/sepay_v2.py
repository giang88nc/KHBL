"""WEBHOOK SEPAY V2 — KHBL nhận thẳng dữ liệu SePay, UPSERT khj_bl.bank_notifications (GĐ chốt 22/09/2026).

    POST https://127.0.0.1:8100/banle/webhook/sepay2     body = JSON NGUYÊN VĂN của SePay   (nội bộ, qua Caddy KHBL)

Giai đoạn 1 (hiện tại): SePay → V1 (BANLE_V5 /sepay/webhook) → V1 chuyển tiếp NGUYÊN VĂN body sang đây (header X-KHBL-Token). Giai đoạn 2: SePay gọi thẳng V2 qua ngrok → Caddy (xác thực `Authorization: Apikey <SEPAY_API_KEY>`),
tắt V1.

Việc V2 làm — CHỈ 3 bước, trả lời trong vài ms (SePay tính giao hàng thất bại nếu chậm):
  1. Đọc JSON SePay (tự đọc lại từ đầu — không dùng kết quả tách mã của V1).
  2. UPSERT theo khóa (provider='sepay', ref_code = id sự kiện SePay) — SePay / V1 gửi lại bao nhiêu lần cũng 1 dòng.
  3. Tách mã chứng từ NGAY trong cùng transaction → cột ma_chung_tu / loai_chung_tu (skill nhan-dien-ma-chung-tu-ck).
KHÔNG ghi KK, KHÔNG ghi CashPay/CardPay: việc đó là đối soát qua nội dung CK, xác nhận trên bản Mobile (GĐ chốt).

Cột cũ bill_code_raw / bill_code_norm / full_code / match_status giữ ĐÚNG nghĩa V1 (chép nguyên luật _extract_code_parts,
_build_match_status của BANLE_V5/app/routes/sepayVN.py) — các luồng KHBL đang đọc cột này không bị gãy khi chuyển tiếp.
"""
import datetime as dt
import hmac
import json
import logging
import re
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.contrib.auth.decorators import login_not_required
from django.db import IntegrityError, connection, transaction
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

log = logging.getLogger(__name__)
PROVIDER = 'sepay'

# ── chép NGUYÊN luật V1 (BANLE_V5 sepayVN._extract_code_parts) — chỉ để giữ nghĩa cột cũ trong giai đoạn chuyển tiếp ──
_V1_MAU = [r"\b(?P<raw>TDC\d{12}(?:CT)?)\s+(?P<norm>\d{6})\b",
           r"\b(?P<raw>TRC\d{12}[A-Z]{2})\s+(?P<norm>\d{6})\b",
           r"\b(?P<raw>\d{12})\s+(?P<norm>\d{6})\b",
           r"\b(?P<raw>\d{10}[A-Z]{2})\s+(?P<norm>\d{6})\b"]


def _v1_ma(description):
    desc = str(description or '').strip()
    for mau in _V1_MAU:
        m = re.search(mau, desc, flags=re.I)
        if m:
            return m.group('raw').upper(), m.group('norm')
    return '', ''


def _v1_trang_thai(raw, huong, mo_ta=''):
    if huong == 'out':
        return 'ignored', 'Tiền ra'
    if huong != 'in':
        return 'unmatched', 'Không rõ chiều tiền (thiếu transferType)'
    code = raw.upper()
    if not code:
        # 29/09/2026: mã KIỂU MỚI (12 số trần · KHBL+10 · 14 số cầm đồ) không có "+ 6 số" nên luật V1 không bắt được —
        # hỏi bộ nhận diện chung để cột match_status không còn báo "Chưa xác định?" sai cho gần hết tiền vào.
        from . import ma_chung_tu_ck as MC
        ds = MC.nhan_dien(mo_ta, MC.IN)
        if len(ds) == 1:
            return 'matched', ds[0].ten
        if len(ds) > 1:
            return 'unmatched', 'Nhiều mã chứng từ — kiểm tra tay'
    if 'CT' in code or code.startswith('TDC'):
        return 'matched', 'Cọc tiền'
    if 'CD' in code:
        return 'matched', 'Cầm đồ'
    if 'KC' in code:
        return 'matched', 'CK khác'
    return ('matched', 'Done!') if code else ('unmatched', 'Chưa xác định?')


def _tien(v):
    if v in (None, ''):
        return 0
    try:
        return int(Decimal(str(v).replace(',', '').strip()))
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError(f'Số tiền không hợp lệ: {v!r}')


def _gio(v):
    s = str(v or '').strip()
    for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%dT%H:%M:%S'):
        try:
            return dt.datetime.strptime(s[:19], fmt).strftime('%Y-%m-%d %H:%M:%S')
        except ValueError:
            pass
    return None


def dong_tu_sepay(data):
    """JSON SePay → dict cột bank_notifications (thuần, không DB)."""
    ref = str(data.get('id') or data.get('referenceCode') or '').strip()   # id sự kiện = khóa chống trùng (như V1)
    if not ref:
        raise ValueError('Thiếu id/referenceCode của SePay')
    huong = str(data.get('transferType') or '').strip().lower()
    huong = huong if huong in ('in', 'out') else 'unknown'      # 29/09/2026: thiếu/lạ KHÔNG còn mặc định là tiền vào
    mo_ta = data.get('content') or ''
    raw, norm = _v1_ma(mo_ta)
    st, msg = _v1_trang_thai(raw, huong, mo_ta)
    return dict(provider=PROVIDER, ref_code=ref, bank_number=data.get('accountNumber'), bank_name=data.get('gateway'),
                acc_name=None, trans_amount=_tien(data.get('transferAmount')), balance_after=_tien(data.get('accumulated')),
                description=mo_ta, full_code=f'{raw}{norm}', bill_code_raw=raw, bill_code_norm=norm,
                transaction_time=_gio(data.get('transactionDate')), direction=huong, match_status=st, match_message=msg,
                raw_payload=json.dumps(data, ensure_ascii=False, default=str))


def upsert(data):
    """UPSERT 1 giao dịch SePay + tách mã chứng từ, trong 1 transaction. Trả (id, moi: bool)."""
    from . import ma_chung_tu_ck as MC
    d = dong_tu_sepay(data)
    with transaction.atomic(), connection.cursor() as cur:
        cur.execute('SELECT id FROM bank_notifications WHERE provider=%s AND ref_code=%s ORDER BY id LIMIT 1'
                    + (' FOR UPDATE' if connection.vendor == 'mysql' else ''), [PROVIDER, d['ref_code']])
        co = cur.fetchone()
        if co:
            bid, moi = co[0], False
        else:
            cot = list(d)
            try:
                with transaction.atomic():
                    from django.utils import timezone
                    bay_gio = timezone.localtime().replace(tzinfo=None, microsecond=0)      # giờ VN (DB lưu giờ VN)
                    cur.execute('INSERT INTO bank_notifications (' + ','.join(cot) + ', is_check, created_at, updated_at) '
                                'VALUES (' + ','.join(['%s'] * len(cot)) + ', 0, %s, %s)', [d[c] for c in cot] + [bay_gio, bay_gio])
            except IntegrityError:                   # 2 lần gửi cùng lúc: bên kia vừa chèn xong — coi là trùng
                cur.execute('SELECT id FROM bank_notifications WHERE provider=%s AND ref_code=%s ORDER BY id LIMIT 1',
                            [PROVIDER, d['ref_code']])
                row = cur.fetchone()
                if not row:
                    raise
                bid, moi = row[0], False
            else:
                cur.execute('SELECT id FROM bank_notifications WHERE provider=%s AND ref_code=%s ORDER BY id LIMIT 1',
                            [PROVIDER, d['ref_code']])
                bid, moi = cur.fetchone()[0], True
        MC.gan_ma(cur, ids=[bid], gioi_han=1)       # chỉ xử lý nếu dòng chưa nhận diện (idempotent)
    return bid, moi


def _xac_thuc(request):
    """2 kiểu: V1 chuyển tiếp nội bộ (X-KHBL-Token) · SePay gọi thẳng (Apikey, giai đoạn 2).

    Token nội bộ: kết nối phải tới từ 127.0.0.1 (V1 gọi thẳng 8101 hoặc qua Caddy 8100 trên CÙNG máy). Caddy KHBL không
    chuyển IP gốc nên sau Caddy không phân biệt được máy LAN — khóa thật là TOKEN 32 byte chỉ nằm trong .env máy chủ."""
    tok = getattr(settings, 'KHBL_GAN_MA_TOKEN', '') or ''
    nhan = request.headers.get('X-KHBL-Token', '')
    if tok and nhan and hmac.compare_digest(tok, nhan):
        return request.META.get('REMOTE_ADDR') in ('127.0.0.1', '::1')
    key = getattr(settings, 'SEPAY_API_KEY', '') or ''
    auth = (request.headers.get('Authorization') or '').strip()
    return bool(key) and hmac.compare_digest(auth, f'Apikey {key}')


@csrf_exempt
@login_not_required
@require_POST
def webhook(request):
    if not _xac_thuc(request):
        return JsonResponse({'success': False, 'message': 'Unauthorized'}, status=401)
    try:
        data = json.loads(request.body.decode('utf-8'))
        if not isinstance(data, dict):
            raise ValueError('JSON không phải object')
    except (ValueError, UnicodeDecodeError):
        return JsonResponse({'success': False, 'message': 'Invalid JSON'}, status=400)
    try:
        bid, moi = upsert(data)
    except ValueError as exc:
        return JsonResponse({'success': False, 'message': str(exc)}, status=400)
    except Exception:
        log.exception('SePay V2: lỗi upsert')
        return JsonResponse({'success': False, 'message': 'Temporary server error'}, status=500)   # SePay/V1 gửi lại
    return JsonResponse({'success': True, 'message': 'Webhook saved' if moi else 'Duplicate webhook', 'id': bid})
