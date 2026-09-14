"""Gợi ý theo ngày; X/+ chỉ thay đổi danh sách nhắc, không thay đổi phiếu."""
import datetime as dt
import json

from django.utils import timezone
from .deposit_models import DepositEvent, DepositMessage

PENDING = ('draft', 'waiting_template', 'scheduled', 'sending', 'uncertain')


def zalo_entry(description):
    """Đọc mốc gọn {zalo:{YYYY-MM-DD: 'nhắc lần N'}} của các app."""
    try:
        entries = json.loads(description or '{}').get('zalo', {})
        result = []
        if isinstance(entries, dict):
            for day, text in entries.items():
                count = int(str(text).rsplit(' ', 1)[-1])
                if count > 0: result.append((dt.date.fromisoformat(day), count))
        return max(result, default=(None, 0))
    except (ValueError, TypeError, AttributeError):
        return None, 0


def suggestions(rows, target, day, query=''):
    events = DepositEvent.objects.filter(target=target, action__in=['reminder_add', 'reminder_remove']).order_by('id')
    overrides = {e.trn_id:e.action for e in events if e.data.get('day') == day.isoformat()}
    messages = DepositMessage.objects.filter(target=target).exclude(status='cancelled').only('trn_id','status','sent_at','planned_day')
    pending, sent = set(), {}
    for message in messages:
        if message.status in PENDING: pending.add(message.trn_id)
        if message.status == 'sent' and message.sent_at:
            sent.setdefault(message.trn_id, []).append(timezone.localtime(message.sent_at).date())
    result = []
    for source in rows:
        if not source['active']: continue
        row = dict(source); pk = row['TrnID']; promise = row.get('promise_date')
        external_day, external_count = zalo_entry(row.get('Description'))
        dates = sent.get(pk, [])
        last = max(dates, default=None)
        count = max(len(dates), external_count - 1)
        next_day = max(last + dt.timedelta(days=7), promise + dt.timedelta(days=7) if promise else last) if last else (external_day or (promise + dt.timedelta(days=7) if promise else None))
        manual = overrides.get(pk) == 'reminder_add'
        if overrides.get(pk) == 'reminder_remove' or pk in pending: continue
        if last and last >= day: continue
        automatic = (next_day <= day if last or external_day else row['fulfilment']=='ready' or bool(promise and promise <= day))
        if not manual and not automatic: continue
        if query.strip().casefold() not in ' '.join(str(row.get(k) or '') for k in ('TrnID','CustName','Phone')).casefold(): continue
        row.update(reminder_count=count + 1, reminder_date=next_day, reminder_manual=manual,
                   reminder_reason='Thêm thủ công' if manual else 'Nhắc lại' if last else 'Hàng sẵn sàng' if row['fulfilment']=='ready' else 'Đến / quá hẹn')
        result.append(row)
    return sorted(result, key=lambda r:(r.get('promise_date') or dt.date.max, r['TrnID']))


def record_sent(message, current):
    """Outbox JSON chỉ tạo sau xác nhận OA; thất bại đồng bộ không gửi tin lần nữa."""
    dates=list(DepositMessage.objects.filter(target=message.target,trn_id=message.trn_id,status='sent').values_list('sent_at',flat=True))
    _, external_number=zalo_entry(current.get('Description'))
    number=max(len(dates)+1,external_number+1)
    sent_day=timezone.localtime(message.sent_at).date()
    promise=current.get('promise_date') or sent_day
    next_day=max(promise+dt.timedelta(days=7),sent_day+dt.timedelta(days=7))
    DepositEvent.objects.get_or_create(token='zalo-out:'+str(message.pk),defaults={
        'target':message.target,'trn_id':message.trn_id,'action':'reminder_zalo','username':'scheduler',
        'data':{'message_id':message.pk,'day':next_day.isoformat(),'number':number},
        'note':f'Nhắc tiếp: {next_day:%d/%m/%Y} · lần {number}'})


def sync_descriptions(target):
    from apps.pmv.gateway import pmv_deposit_zalo
    from django.core.cache import cache
    done={token.removeprefix('zalo-sync:') for token in DepositEvent.objects.filter(target=target,action='reminder_synced').values_list('token',flat=True)}
    events=DepositEvent.objects.filter(target=target,action='reminder_zalo').exclude(token__in=done).order_by('id')
    attempts=0
    for event in events.iterator(chunk_size=100):
        retry_key=f'datcoc:zalo-retry:{target}:{event.pk}'
        if cache.get(retry_key): continue
        if attempts>=30: break
        attempts+=1
        try:
            # A newer confirmed reminder supersedes this compact marker; full log remains.
            if not DepositEvent.objects.filter(target=target,trn_id=event.trn_id,action='reminder_zalo',id__gt=event.pk).exists():
                pmv_deposit_zalo(event.trn_id,event.data['day'],event.data['number'],target=target)
            DepositEvent.objects.get_or_create(token='zalo-sync:'+event.token,defaults={
                'target':target,'trn_id':event.trn_id,'action':'reminder_synced','username':'scheduler','data':event.data})
            cache.delete(f'datcoc:workspace:v2:{target}')
        except Exception:
            # Includes full Description; keep outbox and retry independently, never resend OA.
            cache.set(retry_key,True,3600)
