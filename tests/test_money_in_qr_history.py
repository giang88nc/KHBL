import datetime as dt
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch, MagicMock
from django.test import TestCase,SimpleTestCase
from django.utils import timezone
from apps.pos.models import MoneyFlow,MoneyFlowPayment
from apps.pos.money_flow_payment import save_instruction
from apps.pos import money_in as MI
from apps.pmv import gateway as G


class QRHistoryTests(TestCase):
    def setUp(self):
        self.flow=MoneyFlow.objects.create(direction='IN',service='RETAIL',source_system='KHBL',source_type='gold_bill_retail',
            source_id='T1',source_bill_code='26-09-17-000014',business_date=timezone.localdate(),expected_amount=1000,cash_amount=1000)
        self.bank=dict(bank_bin='ACB',bank_number='123',bank_user='TEST',bank_name='ACB')
        self.user=SimpleNamespace(username='test')
        self.source=patch.object(MI,'check_source');self.source.start();self.addCleanup(self.source.stop)

    def save(self,amount=1000,account='123'):
        return save_instruction(self.flow,'BANK',self.user,company_bank={**self.bank,'bank_number':account},
                                custom_bank=amount,custom_cash=1000-amount,note='260917000014')

    def test_reuse_and_supersede_keep_history(self):
        a=self.save();b=self.save()
        self.assertEqual((a.pk,a.qr_payload,a.updated_at),(b.pk,b.qr_payload,b.updated_at))
        c=self.save(300);a.refresh_from_db()
        self.assertEqual(a.status,'superseded')
        self.assertNotEqual(c.pk,a.pk)
        d=self.save(300,'999');c.refresh_from_db()
        self.assertEqual(c.status,'superseded')
        self.assertEqual(MoneyFlowPayment.objects.filter(flow=self.flow,status='ready').count(),1)
        self.assertEqual(MoneyFlowPayment.objects.count(),3)
        self.assertEqual(self.save().pk,a.pk)
        d.refresh_from_db();self.assertEqual(d.status,'superseded')

    def test_success_is_not_reopened_as_ready(self):
        a=self.save();a.status='success';a.active_key=None;a.save()
        self.assertEqual(self.save().status,'success')

    def test_many_accounts_and_amounts_without_count_limit(self):
        for i in range(12):
            self.save(100+i*10,str(100000+i))
        self.assertEqual(MoneyFlowPayment.objects.filter(flow=self.flow).count(),12)
        with self.assertRaises(ValueError):self.save(1001,'NEW')

    def test_note_cannot_change_reference(self):
        with self.assertRaises(ValueError):
            save_instruction(self.flow,'BANK',self.user,company_bank=self.bank,custom_bank=1000,custom_cash=0,note='other')

    def popup(self, params=None):
        from django.test import RequestFactory
        from apps.pos.money_flow_views import payment
        request=RequestFactory().get('/payment/',params or {})
        request.user=SimpleNamespace(username='test',is_authenticated=False)
        with patch('apps.pos.quyen.chan'),patch('apps.pos.mobile_customers.hydrate_customers'),patch('apps.pos.vietqr.active_banks',return_value=[]):
            return payment(request,self.flow.pk,'BANK')

    def test_confirmation_history_and_currency_fields(self):
        qr=self.save()
        html=self.popup().content.decode()
        self.assertIn('section class="transfer-qr-list"',html)
        self.assertIn('"reopen_qr":"'+str(qr.pk)+'"',html)
        self.assertIn('data-money="bank_amount"',html)
        self.assertNotIn('name="note"',html)
        self.assertLess(html.index('class="transfer-bank"'),html.index('class="transfer-qr-list"'))

    def test_open_exact_qr_without_mutation(self):
        old=self.save();self.save(300)
        old.refresh_from_db();stamp=old.updated_at
        html=self.popup({'qr_id':old.pk}).content.decode()
        self.assertIn('Chỉ xem lại',html)
        self.assertNotIn('data-pawn-poll=',html)
        old.refresh_from_db()
        self.assertEqual(old.status,'superseded')
        self.assertEqual(old.updated_at,stamp)
        self.assertEqual(MoneyFlowPayment.objects.count(),2)

    def test_qr_from_other_bill_rejected(self):
        from django.http import Http404
        other=MoneyFlow.objects.create(direction='IN',source_system='KHBL',source_type='test',source_id='other',expected_amount=1000,business_date=timezone.localdate())
        qr=MoneyFlowPayment.objects.create(flow=other,method='BANK',amount=1000)
        with self.assertRaises(Http404):self.popup({'qr_id':qr.pk})

    def test_empty_history_hidden(self):
        self.assertNotIn('section class="transfer-qr-list"',self.popup().content.decode())

    def test_history_filters_using_verified_remaining(self):
        from apps.pos.models import MoneyFlowBankReceipt
        a=self.save(700);b=self.save(500);c=self.save(300)
        a.status='success';a.save(update_fields=['status'])
        MoneyFlowBankReceipt.objects.create(flow=self.flow,notification_id=998,bank_identity='test-history',amount=700,status='applied')
        html=self.popup().content.decode()
        self.assertNotIn('"reopen_qr":"'+str(b.pk)+'"',html)
        self.assertIn('"reopen_qr":"'+str(c.pk)+'"',html)
        self.assertIn('aria-label="Thành công"',html)

    def test_reopen_replaced_qr_ready_preserves_payload_and_rebases_receipts(self):
        from apps.pos.money_flow_payment import reopen_instruction
        from apps.pos.models import MoneyFlowBankReceipt
        qr=self.save(300);self.save(700)
        MoneyFlowBankReceipt.objects.create(flow=self.flow,notification_id=999,bank_identity='reopen',amount=700,status='applied')
        result=reopen_instruction(self.flow,qr.pk,self.user)
        self.assertEqual(result.status,'ready')
        self.assertEqual(result.qr_payload,qr.qr_payload)
        self.assertEqual(result.received_before,700)
        self.assertNotEqual(MI.response(self.flow,result)['status'],'success')
        self.assertEqual(MoneyFlowPayment.objects.filter(flow=self.flow,status='ready').count(),1)

    def test_reopen_rejects_amount_above_remaining(self):
        from apps.pos.money_flow_payment import reopen_instruction
        from apps.pos.models import MoneyFlowBankReceipt
        qr=self.save(500);self.save(300)
        MoneyFlowBankReceipt.objects.create(flow=self.flow,notification_id=999,bank_identity='reopen',amount=700,status='applied')
        with self.assertRaises(ValueError):reopen_instruction(self.flow,qr.pk,self.user)


class DirectGatewayTests(SimpleTestCase):
    def expected(self):
        return dict(TrnID='T1',BillCode='26-09-17-000014',TrnDate='2026-09-17 00:00:00',TrnTime='10:00:00',
                    TrnDateTime_Upd='2026-09-17 10:00:00',Status='W',CashPay='1000',CardPay='0',PayAmount='1000',IsDel='0')

    def test_bank_transaction_ids_are_not_treated_as_document_codes(self):
        from apps.pos.money_in import codes
        samples={
            '147935929110 260921000138 CHUYEN TIEN OQCH000KbIum MOMO147935929110MOMO GD 6264IBT1d1B6MB84 210926-17:37:59':'260921000138',
            'ACB;127606;26090145506810-GD-252847-210926-18:47:33':'26090145506810',
            '260921000168 GD 6264MCOBQ2CYH2ZF 210926-18:47:10':'260921000168',
            'MBVCB.16165985980.484491.260921000166.CT tu 1014711850 HUYNH THANH QUY toi 50361307 TRUONG NGOC GIANG tai ACB-GD-DAlh484491-210926-18:40:29':'260921000166',
            '260921000162 FT26264117829075 GD 6264IBT1k2V4ZXTH 210926-18:38:46':'260921000162',
            '260921000161 GD 6264BIDVE26P9KT6 210926-18:39:42':'260921000161',
            '260921000159 GD 6264IBT1hW8KZRP2 210926-18:37:20':'260921000159',
            'MBVCB.16165785327.350353.260921000157.CT tu 3397488799 LE VAN BEN toi 50361307 TRUONG NGOC GIANG tai ACB-GD-DAhn350353-210926-18:30:21':'260921000157',
            'ZP262640710165 260921000155 GD 6264IBT1iJRKLMTB 210926-18:23:31':'260921000155',
            '260921000154 GD 6264BIDVE26PXAG5 210926-18:16:48':'260921000154',
            '260921000151 GD 6264BIDVE26PVKQY 210926-18:12:01':'260921000151',
        }
        for description,reference in samples.items():
            self.assertEqual(codes(description),{reference})

    def test_money_split_write_is_retry_idempotent(self):
        for already_applied in (False,True):
            expected=self.expected();actual={**expected}
            if already_applied:actual.update(CashPay='700',CardPay='300')
            cn=MagicMock();cur=cn.cursor.return_value
            cur.rowcount=1
            cur.fetchall.return_value=[tuple(actual.values())]
            cur.fetchone.side_effect=[None,('',),(Decimal(1000),Decimal(700),Decimal(300),'')]
            with patch.object(G,'_connect_dich',return_value=cn),patch('apps.pmv.models.PmvState.get',return_value='0'),patch.object(G,'_audit'):
                G.pmv_in_allocate('RETAIL',expected,300,target='sandbox')
            writes=[args[0][0] for args in cur.execute.call_args_list if args[0][0].startswith('UPDATE')]
            self.assertEqual(len(writes),0 if already_applied else 1)
            if writes:self.assertEqual(writes[0],'UPDATE TRN_RT_BUYSELL SET CashPay=?,CardPay=?,Desc4=? WHERE TrnID=? AND BillCode=?')
            cn.commit.assert_called_once()

    def test_receiving_account_is_saved_with_cumulative_bank_total(self):
        expected=self.expected();actual={**expected}
        cn=MagicMock();cur=cn.cursor.return_value;cur.rowcount=1
        cur.fetchall.return_value=[tuple(actual.values())]
        cur.fetchone.side_effect=[None,('',),(Decimal(1000),Decimal(700),Decimal(300),'666141168')]
        with patch.object(G,'_connect_dich',return_value=cn),patch('apps.pmv.models.PmvState.get',return_value='0'),patch.object(G,'_audit'):
            G.pmv_in_allocate('RETAIL',expected,300,bank_account='666141168',target='sandbox')
        write=next(x for x in cur.execute.call_args_list if x.args[0].startswith('UPDATE'))
        self.assertEqual(write.args[1][:3],(Decimal(700),Decimal(300),'666141168'))

    def test_deposit_receiving_account_is_merged_into_description(self):
        expected=dict(TrnID='D1',BillCode='26-09-17-000015',TrnDate='2026-09-17 00:00:00',TrnTime='10:00:00',
                      TrnDateTime_Upd='2026-09-17 10:00:00',Status='P',CashPay='1000',CardPay='0',TienCoc='1000')
        actual={**expected};description='{"notes":[{"date":null,"text":"Giữ ghi chú"}]}'
        saved='{"notes":[{"date":null,"text":"Giữ ghi chú"}],"Desc4":"666141168"}'
        cn=MagicMock();cur=cn.cursor.return_value;cur.rowcount=1
        cur.fetchall.return_value=[tuple(actual.values())]
        cur.fetchone.side_effect=[None,(description,),(Decimal(1000),Decimal(700),Decimal(300),saved)]
        with patch.object(G,'_connect_dich',return_value=cn),patch('apps.pmv.models.PmvState.get',return_value='0'),patch.object(G,'_audit'):
            G.pmv_in_allocate('DEPOSIT',expected,300,bank_account='666141168',target='sandbox')
        write=next(x for x in cur.execute.call_args_list if x.args[0].startswith('UPDATE'))
        self.assertIn('Giữ ghi chú',write.args[1][2])
        self.assertIn('"Desc4":"666141168"',write.args[1][2])

    def test_buygold_sending_account_is_saved_to_ma_phieu_chi(self):
        cn=MagicMock();cur=cn.cursor.return_value;cur.rowcount=1
        cur.fetchone.side_effect=[None,('C','0',None),('666141168',)]
        with patch.object(G,'_connect_dich',return_value=cn),patch('apps.pmv.models.PmvState.get',return_value='0'),patch.object(G,'_audit'):
            G.pmv_buygold_payment_account(['TBG1'],'666141168',target='sandbox')
        write=next(x for x in cur.execute.call_args_list if x.args[0].startswith('UPDATE'))
        self.assertEqual(write.args[0],'UPDATE TRN_RT_BUYGOLD SET MaPhieuChi=? WHERE TrnID=?')
        self.assertEqual(write.args[1],('666141168','TBG1'))

    def test_source_changed_rolls_back(self):
        expected=self.expected();actual={**expected,'IsDel':'1'}
        cn=MagicMock();cur=cn.cursor.return_value;cur.fetchone.return_value=None
        cur.fetchall.return_value=[tuple(actual.values())]
        with patch.object(G,'_connect_dich',return_value=cn),patch('apps.pmv.models.PmvState.get',return_value='0'),patch.object(G,'_audit'),self.assertRaises(ValueError):
            G.pmv_in_allocate('RETAIL',expected,300,target='sandbox')
        cn.commit.assert_not_called();cn.rollback.assert_called_once()
