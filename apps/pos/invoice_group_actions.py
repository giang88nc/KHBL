"""Explicit, signed confirmation for bulk buyback cancellation through vendor procs."""
import json

from django.core import signing
from django.http import HttpResponse
from django.shortcuts import render

from .invoice_display import groups_for, membership_for


def handle(request):
    from . import views as V
    data = request.POST if request.method == 'POST' else request.GET
    trn = (data.get('trn_id') or '').strip()
    action = data.get('action')
    ctx = dict(trn_id=trn, loai='THAU', action=action, group_action=True,
               action_name='Hủy thanh toán' if action == 'thanh_toan' else 'Hủy hóa đơn')
    done = []
    try:
        if action not in ('thanh_toan', 'hoa_don') or not trn:
            raise ValueError('Yêu cầu không hợp lệ.')
        membership = membership_for(groups_for([trn]))
        group = membership.get(trn)
        if not group:
            raise ValueError('Nhóm không còn tồn tại. Vui lòng tải lại danh sách.')
        ids = sorted({t for t in group.trn_ids if membership[t].pk == group.pk})
        records = [V._doc_hd_kk(t, 'THAU') for t in ids]
        for _, hd, _ in records:
            if not hd or str(hd.get('IsDel')) != '0':
                raise ValueError('Có phiếu không còn hiệu lực. Vui lòng tải lại danh sách.')
            if not V._duoc_thao_tac_hoa_don(request, hd):
                raise ValueError('Không có quyền thao tác một hoặc nhiều phiếu trong nhóm.')
            if hd.get('Status') not in (V.B.NHAP, V.B.CHOT_ROI):
                raise ValueError('Có phiếu không ở trạng thái có thể hủy.')
            if action == 'hoa_don' and hd['Status'] == V.B.CHOT_ROI and not V._co_fullcontrol_hoa_don(request):
                raise ValueError('Hãy hủy thanh toán cả nhóm trước khi hủy hóa đơn.')
        plan = {'ids': ids, 'states': [r[1]['Status'] for r in records], 'action': action,
                'group': group.pk, 'user': request.user.pk}
        ctx['bill_code'] = ' · '.join(r[1].get('BillCode') or t for t, r in zip(ids, records))
        ctx['group_count'] = len(ids)
        if request.method == 'POST':
            confirmed = signing.loads(data.get('group_token', ''), salt='invoice-group', max_age=600)
            if confirmed != plan:
                raise ValueError('Nhóm hoặc trạng thái đã thay đổi. Đóng và xác nhận lại từ danh sách.')
            if not V._passcode_dung(request, data.get('passcode') or ''):
                ctx.update(group_token=data.get('group_token'), loi='Passcode không đúng. Chưa thực hiện thao tác.')
                return render(request, 'pos/_hoa_don_xac_nhan.html', ctx)
            user_id = V._phien(request)['user_id']
            for t, (client, hd, _) in zip(ids, records):
                if action == 'hoa_don':
                    if V.B.huy_thau(t, user_id=user_id, c=client) is False:
                        raise ValueError('Phiếu đã thay đổi trong lúc xử lý: ' + t)
                elif hd['Status'] == V.B.CHOT_ROI:
                    V.B.mo_lai_thau(t, user_id=user_id, c=client)
                done.append(hd.get('BillCode') or t)
            response = HttpResponse(status=204)
            response['HX-Trigger'] = json.dumps({'khblConfirmDone': {'message': f"Đã {ctx['action_name'].lower()} {len(ids)} phiếu trong nhóm"}})
            return response
        if action == 'thanh_toan' and all(r[1]['Status'] == V.B.NHAP for r in records):
            raise ValueError('Nhóm đã ở trạng thái chờ / nháp.')
        ctx['group_token'] = signing.dumps(plan, salt='invoice-group')
    except Exception as exc:
        V.logger.exception('Thao tác nhóm thâu thất bại: %s', trn)
        ctx['loi'] = str(exc) if isinstance(exc, ValueError) else 'Không thể hoàn tất thao tác trên PMV.'
        if done:
            ctx['loi'] = 'Đã xử lý: ' + ', '.join(done) + '. Nhóm chưa hoàn tất. ' + ctx['loi']
        ctx['loi'] += ' Vui lòng tải lại danh sách và kiểm tra trạng thái từng phiếu trước khi thử lại.'
        ctx['blocked'] = True
    return render(request, 'pos/_hoa_don_xac_nhan.html', ctx)
