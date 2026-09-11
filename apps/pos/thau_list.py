"""Standalone buyback list: read-only, group totals and lazy-loaded photos."""
import datetime as dt
from django.db.models.functions import Length
from django.contrib.auth.decorators import login_not_required
from django.shortcuts import render
from django.http import HttpResponse
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET
from django.db.models import Q

from apps.pmv import money as M
from apps.pmv.client import PmvClient
from . import services as S, bill as B, thau_payments as TP
from .models import ThauNhom, ThauPaymentLink
from .invoice_display import groups_for, membership_for, compact

SLOTS = [('hinh1', 'Hình 1'), ('hinh2', 'Hình 2'), ('qr', 'QR chuyển khoản')]


def enrich(rows, membership):
    ids = {g.pk for g in membership.values() if g}
    photos = {r['pk']: r for r in ThauNhom.objects.filter(pk__in=ids).order_by()
              .annotate(**{'size_'+s: Length('anh_'+s) for s, _ in SLOTS})
              .values('pk', *['size_'+s for s, _ in SLOTS])}
    for row in rows:
        group = membership.get(row['TrnID'])
        row['bank'] = group
        row['method'] = 'bank' if row['tien_ck'] or (group and group.pay_method in ('bank', 'mixed', 'card')) else 'cash'
        row['images'] = []
        for slot, label in SLOTS:
            if group and photos.get(group.pk, {}).get('size_'+slot):
                row['images'].append({'slot': slot, 'label': label, 'url': reverse('pos:thau_anh')+'?nhom='+str(group.pk)+'&slot='+slot})
        by_slot = {im['slot']: im for im in row['images']}
        row['qr_image'] = by_slot.get('qr')
        row['photo_grid'] = [im for im in row['images'] if im['slot'] != 'qr']
        ids = [m['TrnID'] for m in row.get('members', [])] or [row['TrnID']]
        # dấu ✓ sau Tên chủ thẻ cũng phải có ở DANH SÁCH, không riêng popup chi tiết (GĐ bắt lỗi 11/09/2026)
        ten_kh = row.get('CustName') or (row.get('members') or [{}])[0].get('CustName')
        row['ten_khop'] = doi_chieu_ten(getattr(group, 'ck_ten', ''), ten_kh)
        row['nd_khop'] = nd_dung_chuan(getattr(group, 'ck_nd', ''), ids)
        row['qr_het_han'] = not qr_con_han(row.get('TrnDate') or (row.get('members') or [{}])[0].get('CreatedDate'))
        row['gd_khop'] = giao_dich_da_khop(ids)
    return rows


@login_not_required     # GĐ chốt 10/09/2026: trang xem công khai trong mạng tiệm
@require_GET
def listing(request):
    from .views_thau import _ngay
    today = timezone.localdate().isoformat()
    d1, d2 = _ngay(request.GET.get('d1'), today), _ngay(request.GET.get('d2'), today)
    if d1 > d2:
        d1, d2 = d2, d1
    method = request.GET.get('method', 'bank')
    if method not in ('all', 'cash', 'bank'):
        method = 'bank'
    customer, status = request.GET.get('khach', '').strip(), request.GET.get('trang_thai', '')
    rows, error, live = [], '', True
    try:
        rows, live = TP.inspect(d1, d2)
        rows = enrich(rows, {r['TrnID']: r['bank'] for r in rows})
        rows = [r for r in rows if (method == 'all' or r['method'] == method)
                and (not customer or any(customer.casefold() in str(m.get(k, '')).casefold() for m in r['members'] for k in ('CustName', 'Phone', 'CMND')))
                and (not request.GET.get('payment_status') or r['payment_status'] == request.GET['payment_status'])
                and (not status or any(m['Status'] == status and str(m['IsDel']) == '0' for m in r['members']))]
    except ValueError as exc:
        error = str(exc)
    except Exception:
        import logging
        logging.getLogger(__name__).exception('Không đọc được danh sách thâu 2')
        error = 'Không đọc được danh sách. Vui lòng thử lại.'
    ctx = dict(rows=rows, d1=d1, d2=d2, today=today, method=method, khach=customer,
               trang_thai=status, source='kk' if live else 'hist', error=error,
               payment_status=request.GET.get('payment_status', ''), payment_labels=TP.LABELS.items(), can_manage=TP.permitted(request.user))
    return render(request, 'pos/_thau2_list.html' if request.headers.get('HX-Request') else 'pos/thau2.html', ctx)


XUNG_HO = {"ANH", "CHI", "CO", "BA", "ONG", "EM", "CHU", "BAC", "DI", "CAU", "MO", "THIM", "KHACH", "KH"}


def ten_chuan(value):
    """Tên đem đi so khớp: bỏ dấu, viết hoa, bỏ từ xưng hô đứng đầu ("Chị Trang" và "TRANG" là một người)."""
    from .vietqr import khong_dau

    tu = khong_dau(value).split()
    while tu and tu[0] in XUNG_HO:
        tu = tu[1:]
    return " ".join(tu)


def doi_chieu_ten(chu_the, khach):
    """So tên CHỦ THẺ nhận tiền với tên KHÁCH trên phiếu (GĐ chốt 10/09/2026).

    Trả None khi thiếu một trong hai tên (không kết luận được, template không vẽ icon), True khi trùng, False khi lệch.
    Chỉ là dấu hiệu để nhân viên nhìn, không chặn gì: khách hoàn toàn có thể nhờ người khác nhận hộ.
    """
    a, b = ten_chuan(chu_the), ten_chuan(khach)
    return None if not a or not b else a == b


def nd_dung_chuan(ck_nd, ids):
    """Nội dung chuyển khoản của phiếu có đúng khuôn đối soát không (GĐ chốt 11/09/2026).

    Đúng khuôn nghĩa là chứa "THANH TOAN TIEN VANG {4 số cuối mã phiếu}" — chính chuỗi mà bên đối soát đi tìm
    trong nội dung ngân hàng. Trả None khi chưa có nội dung hoặc chưa có mã phiếu (không kết luận được).
    """
    from . import thau_payments as TP

    nd = (ck_nd or "").strip().upper()
    if not nd or not ids:
        return None
    return TP.code_match({"ids": list(ids), "members": []}, {"description": nd})


def qr_con_han(ngay_phieu):
    """Mã QR chỉ dùng trong NGÀY. Phiếu của ngày khác hôm nay thì coi như hết hạn, giao diện làm mờ và che lại
    để không ai quét lại mã cũ (GĐ chốt 11/09/2026)."""
    if not ngay_phieu:
        return True
    ngay = ngay_phieu.date() if hasattr(ngay_phieu, "date") else None
    if ngay is None:
        try:
            ngay = dt.date.fromisoformat(str(ngay_phieu)[:10])
        except ValueError:
            return True
    return ngay == dt.date.today()


def giao_dich_da_khop(ids):
    """Mã tham chiếu + số tiền của giao dịch ngân hàng đã được nối cho nhóm này — để hiện thay chữ
    "Đã xác nhận CK" (GĐ chốt 11/09/2026). Trả None khi chưa nối được giao dịch nào."""
    from .transfers import query

    lk = [l for l in ThauPaymentLink.objects.filter(active_notification_id__isnull=False).order_by("-pk")
          if set(ids) & set(l.trn_ids)]
    if not lk:
        return None
    bank = query("SELECT ref_code, trans_amount FROM bank_notifications WHERE id=%s", [lk[0].active_notification_id])
    if not bank:
        return None
    return {"ref_code": bank[0]["ref_code"], "trans_amount": bank[0]["trans_amount"], "so_lk": len(lk)}


def da_xac_nhan_ck(ids, can_tra):
    """Nhóm này đã được xác nhận chuyển khoản đủ tiền chưa — đọc bảng thau_payment_link (MySQL), chỉ tính liên kết
    còn hiệu lực (chưa gỡ). Dùng để che mã QR trong popup chi tiết: tiền đã đi rồi thì đừng quét lại lần nữa."""
    from decimal import Decimal

    if not ids or M.dec(can_tra) <= 0:
        return False, Decimal(0)
    cond = Q()
    for t in ids:
        cond |= Q(trn_ids__contains=[t])
    da_tra = sum((l.amount for l in ThauPaymentLink.objects.filter(cond, active_notification_id__isnull=False)), Decimal(0))
    return da_tra >= M.dec(can_tra), da_tra


@login_not_required     # GĐ chốt 10/09/2026: trang xem công khai trong mạng tiệm
@require_GET
def detail(request):
    from .views_thau import _phieu_ctx
    trn = request.GET.get('trn_id', '').strip()
    source = 'hist' if request.GET.get('source') == 'hist' else 'kk'
    membership = membership_for(groups_for([trn]))
    group = membership.get(trn)
    ids = [t for t in group.trn_ids if membership[t].pk == group.pk] if group else [trn]
    try:
        client = PmvClient(source, tag='thau2_detail')
        items = [B.phieu_thau(t, client) for t in ids]
        items = [r for r in items if r]
        if not items:
            return HttpResponse('Không tìm thấy phiếu.', status=404)
        p = _phieu_ctx(items, group)
        row = {'TrnID': trn, 'members': items, 'tien_ck': p['tien_ck']}
        enrich([row], membership)
        row['da_xac_nhan'], row['ck_da_tra'] = da_xac_nhan_ck([i['TrnID'] for i in items], p['tien_ck'])
        row['ten_khop'] = doi_chieu_ten(getattr(group, 'ck_ten', ''), items[0].get('CustName'))
        row['nd_khop'] = nd_dung_chuan(getattr(group, 'ck_nd', ''), ids)
        row['qr_het_han'] = not qr_con_han(items[0].get('TrnDate') or items[0].get('CreatedDate'))
        row['gd_khop'] = giao_dich_da_khop(ids)
        from . import customer as C
        cust_id = items[0].get('CustID')
        row['cccd_images'] = []
        try:
            customer = C._current(client, cust_id) if cust_id else {}
        except Exception:
            customer = {}
            row['cccd_error'] = 'Tạm chưa đọc được ảnh CCCD của khách hàng.'
        for field, kind, label in [('ImagePathMatTruoc', 'mat-truoc', 'CCCD trước'), ('ImagePathMatSau', 'mat-sau', 'CCCD sau')]:
            im = {'label': label}
            if customer and customer.get(field):
                im['url'] = reverse('pos:khach_anh', kwargs={'cust_id': cust_id, 'kind': kind})
            row['cccd_images'].append(im)
        return render(request, 'pos/_thau2_detail.html', {'p': p, 'r': row, 'items': items,
                      'tiem': S.thong_tin_tiem(), 'in_luc': timezone.now()})
    except Exception:
        return HttpResponse('Không đọc được chi tiết phiếu. Vui lòng thử lại.', status=503)
