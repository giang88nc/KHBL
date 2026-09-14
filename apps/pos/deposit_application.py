"""Điều kiện cấn cọc theo vòng đời PMV: thu P → liên kết W → hóa đơn C/cọc C."""
from decimal import Decimal


def reason(f, cust_id, invoice_id='', invoice_status='W'):
    if f['h']['CustID'] != cust_id:
        return 'Phiếu thuộc khách khác.'
    if f['changes']:
        return 'Phiếu đã liên kết hóa đơn đổi.'
    if any(x['TrnID'] != invoice_id for x in f['links']) or len(f['links']) > 1:
        return 'Phiếu đã liên kết hóa đơn khác.'
    status = (f['h'].get('Status') or '').strip()
    expected = 'C' if invoice_id and invoice_status == 'C' else 'P'
    if f['amount'] <= 0 or f['cash'] < 0 or f['bank'] < 0:
        return 'Số tiền cọc không hợp lệ.'
    if not f['valid'] or status != expected:
        if not f['tx'] and (f['cash'] or f['bank']):
            return 'Cọc cũ chưa có chứng từ quỹ; cần đối soát nguồn tiền, không thu lại tự động.'
        if not f['tx'] and status == 'W':
            return 'Chưa xác nhận thu cọc.'
        return 'Chứng từ thu cọc hoặc trạng thái PMV chưa khớp; cần đối soát.'
    return ''


def check(c, ids, cust_id, invoice_id='', amount=None, invoice_status='W'):
    from .deposit_money import financial
    from .deposit_models import DepositOrderState, DepositMoneyOperation
    ids = list(ids)
    if len(ids) != len(set(ids)):
        raise ValueError('Danh sách phiếu cọc bị trùng.')
    states = {s.trn_id:s for s in DepositOrderState.objects.filter(target=c.target, trn_id__in=ids)}
    funds = []
    for pk in ids:
        f = financial(c, pk)
        error = reason(f, cust_id, invoice_id, invoice_status)
        if not error and DepositMoneyOperation.objects.filter(target=c.target, trn_id=pk,
                kind__in=['receive', 'refund', 'bank_reconcile'], status__in=['queued', 'running', 'uncertain']).exists():
            error = 'Phiếu còn thao tác thu/hoàn chưa xác định kết quả.'
        # Re-reading an already completed invoice must remain idempotent. New
        # applications (including a draft's linked deposits) require staff readiness.
        if not error and not (invoice_id and invoice_status=='C'):
            from .deposit_orders import is_mobile_order
            state=states.get(pk)
            progress=state.fulfilment if state else ''
            if not progress or (is_mobile_order(f['h']) and state.status!=f['h']['Status']):
                progress={'R':'ready','C':'delivered','D':'cancelled'}.get(f['h']['Status'],'')
            if progress=='cancelled': error='Phiếu đã hủy đặt hàng.'
            elif progress not in ('ready','delivered'):
                error='Chỉ áp dụng phiếu Hàng sẵn sàng hoặc Hoàn thành - đặt. Cập nhật tiến độ phiếu trước khi áp dụng.'
        if error:
            raise ValueError('Phiếu cọc ' + pk + ': ' + error)
        funds.append(f)
    if amount is not None and sum((f['amount'] for f in funds), Decimal(0)) != Decimal(amount or 0):
        raise ValueError('Tiền cọc hóa đơn phải khớp tổng phiếu cọc đã chọn/liên kết.')
    return funds


def invoice(c, invoice_id):
    rows = c.query("SELECT TrnID,Status,CustID,TienCoc,PayAmount,ShopID,TrnDateTime_Upd FROM TRN_RT_BUYSELL "
                   "WITH (NOLOCK) WHERE TrnID=? AND IsDel='0'", (invoice_id,))
    if not rows:
        raise ValueError('Hóa đơn không còn tồn tại.')
    return rows[0]


def linked_ids(c, invoice_id):
    return [r['DatCocID'] for r in c.query('SELECT DatCocID FROM TRN_RT_BUYSELL_DatCoc '
            'WITH (NOLOCK) WHERE TrnID=?', (invoice_id,))]


def reserve_invoice(c, session_key, ids, cust_id, amount, username):
    """Nếu Ins mất phản hồi, giữ yêu cầu bền để lần bấm sau không tạo một hóa đơn nữa."""
    import hashlib
    import uuid
    from django.db import IntegrityError, transaction
    from .deposit_models import DepositMoneyOperation as Operation
    key=c.target+':checkout:'+hashlib.sha256(session_key.encode()).hexdigest()[:32]
    try:
        with transaction.atomic():
            return Operation.objects.create(target=c.target,trn_id=ids[0],kind='invoice',status='running',
                amount=Decimal(amount or 0),active_key=key,token=uuid.uuid4().hex,username=username,
                evidence={'deposit_ids':list(ids),'customer':cust_id})
    except IntegrityError:
        raise ValueError('Lần tạo hóa đơn trước chưa xác định kết quả. Kiểm tra danh sách hóa đơn; không tạo lại tự động.')


def finish(c, invoice_id):
    """Đọc lại PMV trước khi ghi trạng thái thành công ở MySQL; gọi lại không thu/chốt thêm."""
    from django.utils import timezone
    from .deposit_models import DepositMoneyOperation as Operation
    h = invoice(c, invoice_id)
    ids = linked_ids(c, invoice_id)
    if not ids:
        return []
    if h['Status'] != 'C':
        raise ValueError('Hóa đơn chưa chốt; cọc mới được liên kết.')
    funds=check(c, ids, h['CustID'], invoice_id, h['TienCoc'], 'C')
    tx = c.query('SELECT Status,TillID FROM T_TILL_TXN WITH (NOLOCK) WHERE TrnRefID=?', (invoice_id,))
    if len(tx) != 1 or tx[0]['Status'] != 'P' or not tx[0]['TillID']:
        raise ValueError('Hóa đơn chưa xác nhận vào két; chưa đánh dấu cọc đã sử dụng.')
    Operation.objects.filter(target=c.target, invoice_id=invoice_id, kind='apply', trn_id__in=ids).exclude(
        status='cancelled').update(status='done', active_key=None, bank_key=None,
        completed_at=timezone.now(), message='Đã sử dụng: hóa đơn chốt, cọc và chứng từ quỹ đã đọc lại khớp.')
    from .deposit_completion import sync
    for pk,fund in zip(ids,funds):
        try: sync(c,pk,financial=fund)
        except Exception:
            from .deposits import log
            log.exception('Hóa đơn đã chốt; chờ đối soát tình trạng cọc %s',pk)
    return ids


def release_snapshot(c, invoice_id):
    """Chỉ phục hồi liên kết hóa đơn W chưa có bất kỳ chứng từ két nào."""
    h = invoice(c, invoice_id)
    if h['Status'] != 'W' or c.query('SELECT TillTxnID FROM T_TILL_TXN WITH (NOLOCK) WHERE TrnRefID=?',(invoice_id,)):
        raise ValueError('Chỉ gỡ liên kết hóa đơn lưu tạm chưa phát sinh chứng từ quỹ.')
    from .deposit_money import safe_data
    return safe_data({'invoice':h, 'ids':sorted(linked_ids(c,invoice_id))})


def reconcile(c, op):
    """Chỉ đọc PMV và sửa nhật ký web theo kết quả thật; tuyệt đối không phát lại proc."""
    from django.utils import timezone
    from .deposit_money import financial, safe_data, log_event
    f=financial(c,op.trn_id)
    if f['applied'] and len(f['links'])==1 and f['links'][0]['TrnID']==op.invoice_id:
        from types import SimpleNamespace
        from .ban_coc import hoan_tat
        hoan_tat(c,linked_ids(c,op.invoice_id),SimpleNamespace(username=op.username),trn_id=op.invoice_id)
        op.refresh_from_db()
        return op
    if len(f['links'])==1 and f['links'][0]['TrnID']==op.invoice_id and f['links'][0]['Status']=='W':
        status,message='linked','Đã liên kết hóa đơn lưu tạm; chưa sử dụng tiền cọc.'
    elif not f['links'] and not f['changes']:
        inv=c.query("SELECT Status,TienCoc FROM TRN_RT_BUYSELL WITH (NOLOCK) WHERE TrnID=?",(op.invoice_id,))
        tx=c.query('SELECT TillTxnID FROM T_TILL_TXN WITH (NOLOCK) WHERE TrnRefID=?',(op.invoice_id,))
        if f['h']['Status'] in ('W','R','P') and not tx and (not inv or inv[0]['Status']=='W' and not inv[0]['TienCoc']):
            status,message='cancelled','Đối soát: liên kết không còn, hóa đơn chưa ghi quỹ; cọc chưa được sử dụng.'
        else:
            status,message='uncertain','Liên kết không còn nhưng hóa đơn có dấu vết tiền; cần đối soát, không ghi lại.'
    else:
        status,message='uncertain','Chứng từ PMV chưa khớp; cần đối soát, không ghi lại.'
    if op.status!=status:
        op.evidence['reconciled']=safe_data({'time':timezone.now(),'h':f['h'],'links':f['links'],'tx':f['tx']})
        op.status=status;op.message=message
        if status=='cancelled': op.active_key=None;op.bank_key=None;op.completed_at=timezone.now()
        op.save();log_event(op)
    if status=='cancelled':
        from .deposit_completion import after_detach
        after_detach(c,[op.trn_id],op.username)
    return op


def release(c, invoice_id, user, expected):
    """Gỡ toàn bộ cọc khỏi bản nháp, tính lại tiền khách trả; không thu/hoàn tiền."""
    import uuid
    from django.db import transaction
    from django.utils import timezone
    from apps.pmv.models import pmv_user_for_web_user
    from . import bill as B, deposit_workspace as W
    from .deposit_models import DepositMoneyOperation as Operation, DepositEvent
    from .deposit_money import safe_data
    before = release_snapshot(c,invoice_id)
    if before != expected or not before['ids']:
        raise ValueError('Liên kết hoặc hóa đơn đã đổi; mở lại popup để kiểm tra.')
    pu = pmv_user_for_web_user(user) if getattr(user,'pk',None) else user
    if not pu or not pu.till_id or not pu.shop_id:
        raise ValueError('Tài khoản chưa liên kết nhân viên/két PMV.')
    doc = B.doc(invoice_id,c)
    if not doc: raise ValueError('Chưa đọc đủ hóa đơn để phục hồi.')
    import datetime as dt
    day=doc['ngay']
    if isinstance(day,str):
        parsed=None
        for fmt in ('%d/%m/%Y','%Y-%m-%d','%Y-%m-%d %H:%M:%S'):
            try: parsed=dt.datetime.strptime(day,fmt).date();break
            except ValueError: pass
        if parsed is None: raise ValueError('Ngày hóa đơn không hợp lệ; dừng trước khi gỡ cọc.')
        day=parsed
    with transaction.atomic():
        op = Operation.objects.create(target=c.target,trn_id=before['ids'][0],kind='unlink',
            invoice_id=invoice_id,amount=Decimal(before['invoice']['TienCoc']),
            active_key=c.target+':unlink:'+invoice_id,status='running',token=uuid.uuid4().hex,
            user_id=pu.user_id,till_id=pu.till_id,username=user.username,
            evidence={'before':before,'invoice_document':safe_data(doc)})
    try:
        if release_snapshot(c,invoice_id) != before:
            raise ValueError('Hóa đơn vừa thay đổi trước khi phục hồi.')
        c.call('TRN_RT_BUYSELL_DatCoc_Ins',write=True,p_TrnID=invoice_id,p_IDCoc='')
        if linked_ids(c,invoice_id): raise ValueError('Chưa gỡ được liên kết cọc.')
        if release_snapshot(c,invoice_id)['invoice'] != before['invoice']:
            raise ValueError('Hóa đơn vừa đổi sau khi gỡ liên kết; dừng để kiểm tra.')
        B.luu(trn_id=invoice_id,ban=doc['ban'],doi=doc['doi'],ngay=c.fmt_date(day),
              gio=doc['gio'],cust_id=doc['cust_id'],emp_id=doc['emp_id'],
              till_id=pu.till_id,shop_id=before['invoice']['ShopID'],user_id=pu.user_id,ghi_chu=doc['ghi_chu'],
              bot=doc['tong']['bot'],cong_them=doc['tong']['cong_them'],vang_them=doc['tong']['vang_them'],coc=0,c=c)
        after = release_snapshot(c,invoice_id)
        if after['ids'] or Decimal(after['invoice']['TienCoc']) != 0:
            raise ValueError('Chưa xác minh xong tiền cọc hóa đơn sau phục hồi.')
        from .deposit_completion import after_detach
        after_detach(c,before['ids'],user.username)
        op.evidence['after'] = after
        with transaction.atomic():
            Operation.objects.filter(target=c.target,invoice_id=invoice_id,kind='apply').update(
                status='cancelled',active_key=None,bank_key=None,completed_at=timezone.now(),
                message='Đã gỡ liên kết khỏi hóa đơn nháp; không thu hoặc hoàn tiền.')
            op.status='done'; op.active_key=None; op.completed_at=timezone.now()
            op.message='Đã gỡ cọc và tính lại tiền khách trả trên hóa đơn lưu tạm.'; op.save()
            for pk in before['ids']:
                DepositEvent.objects.create(target=c.target,trn_id=pk,action='money_unlink',username=user.username,
                    token=uuid.uuid4().hex,note=op.message,data={'invoice_id':invoice_id,'operation':op.pk})
    except Exception:
        op.status='uncertain';op.message='Phục hồi chưa xác định kết quả; dừng chốt hóa đơn và kiểm tra chứng từ.';op.save()
        raise
    finally:
        W.invalidate(c.target)
    return op
