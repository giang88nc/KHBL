"""API NỘI BỘ: BANLE_V5 gọi NGAY SAU KHI upsert bank_notifications (webhook SePay) → KHBL nhận diện mã chứng từ
của đúng các dòng đó vào 3 cột riêng (ma_chung_tu / loai_chung_tu / nhan_dien_luc) — GĐ chốt 22/09/2026.

    POST http://127.0.0.1:8101/banle/api/gan-ma-ck/   header X-KHBL-Token: <KHBL_GAN_MA_TOKEN>   body ids=1,2,3

Rào: token trong .env (2 bên cùng giá trị) · chỉ nhận gọi THẲNG vào waitress 127.0.0.1 — request đi qua Caddy (có
X-Forwarded-For) bị từ chối. Hành động vô hại & idempotent (chỉ xử lý dòng nhan_dien_luc IS NULL), job 15 giây là lưới an toàn.
Thuật toán: skill nhan-dien-ma-chung-tu-ck (apps/pos/ma_chung_tu_ck.py).
"""
import hmac
import logging

from django.conf import settings
from django.contrib.auth.decorators import login_not_required
from django.db import connection, transaction
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

log = logging.getLogger(__name__)


@csrf_exempt
@login_not_required
@require_POST
def gan_ma_ck(request):
    token = getattr(settings, 'KHBL_GAN_MA_TOKEN', '') or ''
    nhan = request.headers.get('X-KHBL-Token', '')
    if not token or not hmac.compare_digest(token, nhan):
        return JsonResponse({'ok': False, 'loi': 'token'}, status=403)
    # Request đi qua Caddy (8100/8102) luôn mang X-Forwarded-Proto (Caddy KHBL không thêm X-Forwarded-For) → từ chối
    qua_proxy = any(k.startswith('HTTP_X_FORWARDED_') for k in request.META)
    if qua_proxy or request.META.get('REMOTE_ADDR') not in ('127.0.0.1', '::1'):
        return JsonResponse({'ok': False, 'loi': 'chỉ nhận gọi nội bộ'}, status=403)
    try:
        ids = [int(x) for x in str(request.POST.get('ids') or '').split(',') if x.strip()][:500]
    except ValueError:
        return JsonResponse({'ok': False, 'loi': 'ids'}, status=400)
    if not ids:
        return JsonResponse({'ok': True, 'so_dong': 0})
    from . import ma_chung_tu_ck as MC
    with transaction.atomic(), connection.cursor() as cur:
        dem = MC.gan_ma(cur, ids=ids, gioi_han=len(ids))
    n = dem.pop('_so_dong')
    return JsonResponse({'ok': True, 'so_dong': n, 'loai': dict(dem)})
