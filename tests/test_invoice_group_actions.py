from types import SimpleNamespace
from unittest.mock import patch
from django.core import signing
from django.contrib.auth import get_user_model
from django.test import TestCase, RequestFactory
from apps.pos.invoice_group_actions import handle
from apps.pos.invoice_display import gold_label


class GroupActionsTests(TestCase):
    def run_action(self, action='thanh_toan', fail=False, states=None):
        states = states or ['C', 'C']
        plan = {'ids': ['A', 'B'], 'states': states, 'action': action, 'group': 1, 'user': 7}
        request = RequestFactory().post('/', {'trn_id': 'A', 'action': action,
            'passcode': '1234', 'group_token': signing.dumps(plan, salt='invoice-group')})
        request.user = get_user_model().objects.create(pk=7, username='group_admin', is_superuser=True)
        group = SimpleNamespace(pk=1, trn_ids=['A', 'B'])
        rows = [(None, {'TrnID': t, 'BillCode': t, 'IsDel': '0', 'Status': st}, '') for t, st in zip(['A','B'], states)]
        with patch('apps.pos.invoice_group_actions.groups_for', return_value=[group]), \
             patch('apps.pos.views._doc_hd_kk', side_effect=rows), \
             patch('apps.pos.views._passcode_dung', return_value=True), \
             patch('apps.pos.views._phien', return_value={'user_id': 'admin'}), \
             patch('apps.pos.views.B.mo_lai_thau', side_effect=[True, ValueError('failed')] if fail else None) as reopen, \
             patch('apps.pos.views.B.huy_thau') as delete:
            response = handle(request)
        return response, reopen, delete

    def test_bulk_payment(self):
        response, reopen, delete = self.run_action()
        self.assertEqual(response.status_code, 204)
        self.assertEqual([c.args[0] for c in reopen.call_args_list], ['A','B'])
        delete.assert_not_called()

    def test_bulk_delete(self):
        response, reopen, delete = self.run_action('hoa_don')
        self.assertEqual(response.status_code, 204)
        self.assertEqual(delete.call_count, 2)

    def test_partial_failure_is_not_success(self):
        response, _, _ = self.run_action(fail=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn('Đã xử lý: A', response.content.decode())
        self.assertNotIn('HX-Trigger', response)

    def test_gold_labels(self):
        for code, label in [('D18K','610'), ('D24K','980'), ('DN9999','9999'), ('DBK','BK')]:
            self.assertEqual(gold_label({'GoldCode':code}), label)
