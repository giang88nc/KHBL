"""Read-only presentation of invoices; PMV amounts and individual actions stay intact."""
from django.db.models import Q

from apps.pmv import money as M
from .models import GoldBill, ThauNhom


def groups_for(ids):
    if not ids:
        return []
    condition = Q()
    for trn in ids:
        condition |= Q(trn_ids__contains=[trn])
    groups = list(ThauNhom.objects.filter(condition).order_by('-pk'))
    # A re-opened invoice can occur in an older group and a newer checkout.
    # Resolve every member against its latest group, including filtered-out rows.
    members = {trn for group in groups for trn in group.trn_ids}
    if members - set(ids):
        condition = Q()
        for trn in members:
            condition |= Q(trn_ids__contains=[trn])
        groups = list(ThauNhom.objects.filter(condition).order_by('-pk'))
    return groups


def membership_for(groups):
    membership = {}
    for group in groups:
        for trn in group.trn_ids:
            membership.setdefault(trn, group)
    return membership


def compact(rows, groups, support=None):
    """Group only filtered rows; never infer a group from customer/time/amount."""
    membership = membership_for(groups)
    result, buckets = [], {}
    for original in rows:
        row = dict(original)
        row['tien_ck'] = abs(M.dec(row.get('CardPay')))
        row['EmpSupName'] = (support or {}).get(row['TrnID'], '')
        if row['loai'] != 'THAU':
            result.append(row)
            continue
        group = membership.get(row['TrnID'])
        key = ('group', group.pk) if group else ('single', row['TrnID'])
        if key not in buckets:
            display = dict(row, members=[], thau_lines=[], SoTien=M.D0, tien_ck=M.D0)
            display['group_size'] = sum(membership[t].pk == group.pk for t in group.trn_ids) if group else 1
            buckets[key] = display
            result.append(display)
        display = buckets[key]
        display['members'].append(row)
        display['thau_lines'].append({'label': gold_label(row),
                                      'amount': M.dec(row.get('TienMua'))})
        display['SoTien'] += M.dec(row.get('SoTien'))
        display['tien_ck'] += row['tien_ck']
        display['mixed_status'] = len({(x.get('IsDel'), x.get('Status')) for x in display['members']}) > 1
        display['co_the_thao_tac'] = all(x.get('co_the_thao_tac') for x in display['members'])
        if any(x.get('Status') == 'C' for x in display['members']):
            display['Status'] = 'C'
        display['partial_group'] = len(display['members']) < display['group_size']
    return result


def prepare(rows, employees):
    groups = groups_for([r['TrnID'] for r in rows if r['loai'] == 'THAU'])
    names = {r['EmpID']: r['EmpName'] for r in employees}
    support = {b.trn_id: names.get(b.emp_sup_id, b.emp_sup_id) for b in
               GoldBill.objects.filter(trn_id__in=[r['TrnID'] for r in rows if r['loai'] != 'THAU'])
               .only('trn_id', 'emp_sup_id')}
    return compact(rows, groups, support)


def gold_label(row):
    code = (row.get('GoldCode') or '').strip().upper()
    if code:
        return M.tuoi(code).replace('99.99', '9999')
    label = (row.get('GoldDesc') or '').strip()
    if label.upper().startswith('DẺ '):
        label = label[3:].strip()
    return {'18K': '610', '24K': '980', '99.99': '9999'}.get(label.upper(), label or '—')
