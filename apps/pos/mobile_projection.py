"""Source metadata belongs in money_flow, not in mobile page queries.

Hai lớp (GĐ chốt 21/09/2026 — trang mobile chạy trên MySQL, bảng chính money_flow, không đụng KK):
  · refresh()        — CHỈ MySQL: giờ lập / nhân viên / khách lấy từ gold_bill (đã đối chiếu 199/199 khớp KK).
                       Chạy ngay khi KHBL ghi gold_bill (chốt/lưu/hủy HĐ, lưu phiếu cọc) + job nhanh 20 giây.
  · enrich_from_kk() — NỀN: đọc KK 1 lượt để bổ sung SĐT khách (gold_bill phần lớn để trống) và tiền thối lại
                       (TienTraLai chỉ có trên KK) vào source_snapshot. Job 5 phút + ngay sau khi lưu popup HĐ.
"""
import datetime as dt
from django.db import transaction
from django.utils import timezone
from apps.pmv import gateway
from .models import MoneyFlow

KINDS = (('gold_bill_retail', 'TRN_RT_BUYSELL'), ('gold_bill_deposit', 'TRN_DATCOC'))


def _flows(d1=None, d2=None, trn_ids=None):
    qs = MoneyFlow.objects.filter(source_system='KHBL', direction='IN', source_type__in=[k for k, _ in KINDS])
    if d1: qs = qs.filter(business_date__gte=d1)
    if d2: qs = qs.filter(business_date__lte=d2)
    if trn_ids: qs = qs.filter(source_id__in=[str(t) for t in trn_ids])
    return list(qs)


def _stamp(bill):
    if bill.trn_date and bill.trn_time:
        try:
            return dt.datetime.combine(bill.trn_date, dt.time.fromisoformat(str(bill.trn_time)[:8]))
        except ValueError:
            pass
    return timezone.localtime(bill.created_at).replace(tzinfo=None, microsecond=0) if bill.created_at else None


def _staff_names():
    try:
        from . import services
        return {str(e['EmpID']).strip(): e['EmpName'] for e in services.nhan_vien_ban()}   # cache 5 phút
    except Exception:
        return {}


def _save(flow, snapshot, cust_id=None, cust_name=None):
    with transaction.atomic():
        locked = MoneyFlow.objects.select_for_update().get(pk=flow.pk)
        locked.source_snapshot = {**(locked.source_snapshot or {}), **snapshot}
        fields = ['source_snapshot']
        if cust_id: locked.customer_id = cust_id; fields.append('customer_id')
        if cust_name: locked.customer_name = cust_name[:200]; fields.append('customer_name')
        locked.save(update_fields=fields)


def refresh(d1=None, d2=None, trn_ids=None):
    """Khối `mobile` từ gold_bill — không một truy vấn KK nào (tên NV từ cache danh sách NV)."""
    from .models import GoldBill
    from . import customer_phones
    flows = _flows(d1, d2, trn_ids)
    if not flows:
        return 0
    bills = {b.trn_id: b for b in GoldBill.all_objects.filter(trn_id__in=[f.source_id for f in flows])}
    names = None
    count = 0
    for flow in flows:
        bill = bills.get(flow.source_id)
        if not bill or (bill.bill_code or '').strip() != flow.source_bill_code:
            continue
        stamp = _stamp(bill)
        if stamp is None:
            continue
        old = (flow.source_snapshot or {}).get('mobile') or {}
        emp = (bill.emp_id or '').strip()
        name = old.get('employee_name') if old.get('employee_pmv') == emp and old.get('employee_name') else ''
        if not name:
            name = (bill.employee_name or '').strip()
        if not name and emp:
            names = _staff_names() if names is None else names
            name = names.get(emp, '')
        phone = customer_phones.phone_key(bill.cust_phone)
        meta = {'happened_at': stamp.isoformat(), 'employee_pmv': emp, 'employee_name': name,
                'phone': old.get('phone') or (phone if customer_phones.valid(phone) else ''), 'basis': 'gold_bill'}
        cust_id = (bill.cust_id or '').strip()
        cust_name = (bill.cust_name or '').strip()
        if meta == old and (not cust_id or cust_id == flow.customer_id) and (not cust_name or cust_name == flow.customer_name):
            continue
        _save(flow, {'mobile': meta}, cust_id, cust_name)
        count += 1
    return count


def enrich_from_kk(d1=None, d2=None, trn_ids=None):
    """NỀN: SĐT khách + tiền thối lại từ KK, ghi vào money_flow để trang mobile chỉ đọc MySQL."""
    from . import customer_phones
    from .mobile_invoice import attach_change
    flows = [f for f in _flows(d1, d2, trn_ids) if (f.source_snapshot or {}).get('mobile')]
    count = 0
    for kind, table in KINDS:
        part = [f for f in flows if f.source_type == kind]
        for offset in range(0, len(part), 200):
            batch = part[offset:offset + 200]
            records = gateway.pmv_read(
                f'SELECT b.TrnID,b.BillCode,b.CustID,c.CustName,c.Phone,c.GhiChu2,c.GhiChu3 FROM {table} b WITH (NOLOCK) '
                'LEFT JOIN I_CUSTOMER c WITH (NOLOCK) ON c.CustID=b.CustID '
                'WHERE b.TrnID IN (' + ','.join('?' for _ in batch) + ')',
                tuple(f.source_id for f in batch), tag='mobile-flow-projection', target='kk', audit=False)
            by_id = {str(r['TrnID']): r for r in records}
            attach_change(batch)            # đặt f.customer_change / f.customer_received_exact (1 truy vấn KK / 200 HĐ)
            for flow in batch:
                row = by_id.get(flow.source_id)
                if not row or str(row['BillCode']).strip() != flow.source_bill_code:
                    continue
                snap = flow.source_snapshot or {}
                meta = dict(snap.get('mobile') or {})
                phone = next((customer_phones.phone_key(row.get(k)) for k in ('Phone', 'GhiChu2', 'GhiChu3')
                              if customer_phones.valid(customer_phones.phone_key(row.get(k)))), '')
                if phone: meta['phone'] = phone
                tender = {'change': None if flow.customer_change is None else str(flow.customer_change),
                          'exact': bool(flow.customer_received_exact)}
                # 23/09/2026: 2 cột tiền khách đưa / tiền thừa đã nằm THẲNG trên money_flow — đồng bộ lại từ KK
                cot = {}
                if getattr(flow, 'customer_tender', None) is not None and flow.customer_tender != flow.cus_cash:
                    cot['cus_cash'] = flow.customer_tender
                if flow.customer_change is not None and flow.customer_change != flow.cus_change:
                    cot['cus_change'] = flow.customer_change
                if cot:
                    MoneyFlow.objects.filter(pk=flow.pk).update(**cot)
                if meta == snap.get('mobile') and tender == {k: (snap.get('tender') or {}).get(k) for k in tender}:
                    if cot:
                        count += 1
                    continue
                _save(flow, {'mobile': meta, 'tender': {**tender, 'at': timezone.now().isoformat()}},
                      str(row['CustID'] or '').strip(), str(row['CustName'] or '').strip())
                count += 1
    return count


def tender_of(flow):
    """Tiền thối lại đã lưu trong money_flow (None = chưa biết) — trang mobile đọc cái này, không hỏi KK."""
    from decimal import Decimal
    tender = (flow.source_snapshot or {}).get('tender') or {}
    raw = tender.get('change')
    return (Decimal(raw) if raw not in (None, '') else None), bool(tender.get('exact'))
