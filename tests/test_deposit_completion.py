import datetime as dt
from decimal import Decimal as Q
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from django.test import TestCase
from apps.pos import deposit_completion as C, deposit_workspace as W, deposit_editing as X, bill as B
from apps.pos.deposit_models import DepositOrderState, DepositEvent, DepositMoneyOperation


class CompletionTests(TestCase):
    def setUp(self):
        self.c=MagicMock(target='sandbox')
        self.state=DepositOrderState.objects.create(target='sandbox',trn_id='D1',fulfilment='delivered')
        self.f={'h':{'TrnID':'D1','Status':'C'},'applied':True,'valid':True,'links':[{'TrnID':'INV','Status':'C'}],'changes':[]}

    def test_apply_unlink_reapply_same_invoice_is_idempotent(self):
        self.assertTrue(C.sync(self.c,'D1',financial=self.f))
        self.state.refresh_from_db();self.assertEqual(self.state.fulfilment,'applied')
        version=self.state.version
        self.assertFalse(C.sync(self.c,'D1',financial=self.f))
        self.state.refresh_from_db();self.assertEqual(self.state.version,version)
        removed={**self.f,'h':{'TrnID':'D1','Status':'P'},'applied':False,'links':[]}
        self.assertTrue(C.sync(self.c,'D1',financial=removed))
        self.state.refresh_from_db();self.assertEqual(self.state.fulfilment,'ready');self.assertIsNone(self.state.delivered_at)
        self.assertTrue(C.sync(self.c,'D1',financial=self.f))
        self.assertEqual(DepositEvent.objects.filter(action='completion_sync').count(),3)
        self.c.call.assert_not_called()

    def test_draft_detach_returns_ready_without_financial_write(self):
        self.state.fulfilment='ordering';self.state.save()
        removed={**self.f,'h':{'TrnID':'D1','Status':'P'},'applied':False,'links':[]}
        self.assertTrue(C.sync(self.c,'D1',financial=removed,detached=True))
        self.state.refresh_from_db();self.assertEqual(self.state.fulfilment,'ready')
        self.c.call.assert_not_called()

    def test_uncertain_or_other_trade_does_not_clear_applied_stamp(self):
        self.state.fulfilment='applied';self.state.save()
        for changes in ({'valid':False},{'changes':[{'TrnID':'CHANGE'}]}):
            f={**self.f,'applied':False,'h':{'TrnID':'D1','Status':'P'},'links':[],**changes}
            self.assertFalse(C.sync(self.c,'D1',financial=f))
        self.state.refresh_from_db();self.assertEqual(self.state.fulfilment,'applied')

    def test_confirmed_detach_restores_ready_even_when_money_needs_review(self):
        removed={**self.f,'h':{'TrnID':'D1','Status':'P'},'applied':False,'links':[],'valid':False}
        self.assertTrue(C.sync(self.c,'D1',financial=removed,detached=True))
        self.state.refresh_from_db();self.assertEqual(self.state.fulfilment,'ready')
        self.c.call.assert_not_called()

    def test_external_invoice_delete_cancels_old_operation(self):
        self.state.fulfilment='applied';self.state.save()
        op=DepositMoneyOperation.objects.create(target='sandbox',trn_id='D1',invoice_id='INV',kind='apply',status='done',amount=1000,token='old')
        removed={**self.f,'h':{'TrnID':'D1','Status':'P'},'applied':False,'links':[]}
        with patch('apps.pos.deposit_money.financial',return_value=removed): C.process(self.c)
        self.state.refresh_from_db();op.refresh_from_db()
        self.assertEqual(self.state.fulfilment,'ready');self.assertEqual(op.status,'cancelled')

    def test_manual_applied_option_is_not_a_financial_override(self):
        with self.assertRaisesRegex(ValueError,'tự động'):
            X.set_progress(self.state,'applied',[],SimpleNamespace(username='test'),{'fulfilment':'delivered'})
        with self.assertRaisesRegex(ValueError,'gỡ áp dụng'):
            X.set_progress(self.state,'delivered',[],SimpleNamespace(username='test'),{'fulfilment':'applied','application_applied':True})

    def test_invoice_delete_syncs_only_after_vendor_success(self):
        self.c.query.side_effect=[[{'Status':'W'}],[{'DatCocID':'D1'}]]
        with patch.object(C,'after_detach') as sync:
            B.huy('INV',user_id='U1',c=self.c)
            sync.assert_called_once_with(self.c,['D1'],'U1')
        self.c.query.side_effect=[[{'Status':'W'}],[{'DatCocID':'D1'}]]
        self.c.goi_co_khoa.side_effect=ValueError('vendor failed')
        with patch.object(C,'after_detach') as sync:
            with self.assertRaises(ValueError): B.huy('INV',user_id='U1',c=self.c)
            sync.assert_not_called()

    def test_reopening_invoice_refreshes_linked_deposits_immediately(self):
        from apps.pos import deposit_application as A
        self.c.query.return_value=[{'co':1}]
        with patch.object(A,'linked_ids',return_value=['D1']),patch.object(C,'after_detach') as sync:
            B.mo_lai('INV',user_id='U1',c=self.c)
            sync.assert_called_once_with(self.c,['D1'],'U1')
        self.c.goi_co_khoa.side_effect=ValueError('vendor failed')
        with patch.object(C,'after_detach') as sync:
            with self.assertRaises(ValueError): B.mo_lai('INV',user_id='U1',c=self.c)
            sync.assert_not_called()
