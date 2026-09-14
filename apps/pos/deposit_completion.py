"""Đồng bộ hoàn thành đặt/áp dụng từ chứng từ thật; không tự ghi tiền PMV."""
import uuid
from django.db import transaction
from django.utils import timezone
from .deposit_models import DepositOrderState, DepositEvent, DepositStockHold, DepositMoneyOperation


def sync(c, pk, *, financial=None, detached=False, username='system'):
    from .deposit_money import financial as read_financial
    from .deposit_operations import base_state
    from . import deposits as D
    f = financial if financial is not None else read_financial(c, pk)
    with transaction.atomic():
        state = DepositOrderState.objects.select_for_update().filter(target=c.target,trn_id=pk).first()
        if f['applied']:
            progress = 'applied'
        elif (not f['changes'] and (
              (detached and not f['links']) or
              (f['valid'] and f['h']['Status']=='P'
               and (not f['links'] or all(x['Status']=='W' for x in f['links']))
               and state and state.fulfilment=='applied'))):
            progress = 'ready'
        else:
            return False
        if state and state.fulfilment==progress:
            return False
        if progress=='ready' and not f['links']:
            DepositMoneyOperation.objects.filter(target=c.target,trn_id=pk,kind='apply').exclude(status='cancelled').update(
                status='cancelled',active_key=None,bank_key=None,completed_at=timezone.now(),message='Đã bỏ áp dụng hóa đơn; hàng sẵn sàng.')
        state = state or base_state(c,f['h'],D.lines(c,pk))
        previous = state.fulfilment
        state.fulfilment=progress; state.delivered_at=(state.delivered_at or timezone.now()) if progress=='applied' else None
        state.ready_at=state.ready_at or timezone.now(); state.cancellation_policy=''; state.status=f['h']['Status']
        for item in state.items:
            item['ready']=item.get('quantity',0)
            item['delivered']=item.get('quantity',0) if progress=='applied' else 0
        state.next_contact=None; state.version+=1; state.save()
        DepositStockHold.objects.filter(target=c.target,trn_id=pk,active_key__isnull=False).update(
            active_key=None,released_at=timezone.now(),released_by=username)
        DepositEvent.objects.create(target=c.target,trn_id=pk,action='completion_sync',username=username,
            token=uuid.uuid4().hex,note='Hoàn thành - áp dụng' if progress=='applied' else 'Hàng sẵn sàng',
            data={'before':previous,'after':progress,'invoices':[x['TrnID'] for x in f['links']]})
    D.W.invalidate(c.target)
    return True


def after_detach(c, ids, username='system'):
    from . import deposits as D
    for pk in set(ids):
        try: sync(c,pk,detached=True,username=username)
        except Exception: D.log.exception('Chưa đồng bộ tình trạng cọc sau gỡ hóa đơn: %s',pk)


def process(c):
    from . import deposits as D
    # Khôi phục metadata nếu hóa đơn đã chốt nhưng lần ghi trạng thái trước gián đoạn.
    applied_ids=DepositOrderState.objects.filter(target=c.target,fulfilment='applied').values_list('trn_id',flat=True)
    for pk in DepositMoneyOperation.objects.filter(target=c.target,kind='apply',status='done').exclude(
            trn_id__in=applied_ids).order_by('-id').values_list('trn_id',flat=True)[:30]:
        try: sync(c,pk)
        except Exception: D.log.exception('Chưa phục hồi trạng thái áp dụng cọc: %s',pk)
    # Luân phiên để nhận cả thay đổi từ PMV desktop, kể cả thao tác apply đã done.
    for state in DepositOrderState.objects.filter(target=c.target,fulfilment__in=['applied','delivered']).order_by('updated_at')[:30]:
        try: sync(c,state.trn_id)
        except Exception: D.log.exception('Chưa đối soát tình trạng hoàn thành cọc: %s',state.trn_id)
        DepositOrderState.objects.filter(pk=state.pk).update(updated_at=timezone.now())
