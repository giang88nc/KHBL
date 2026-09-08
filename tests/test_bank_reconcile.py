import datetime as dt
from contextlib import nullcontext
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import Client, TransactionTestCase
from django.utils import timezone

from apps.pmv.models import PmvState
from apps.pos import bank_reconcile as R
from apps.pos.models import BankReconcileState
from apps.pos.transfers import query


class BankReconcileTests(TransactionTestCase):
    today = dt.date(2026, 9, 8)

    def setUp(self):
        with connection.cursor() as c:
            c.execute('CREATE TABLE bank_notifications (id integer primary key, bank_number varchar(100), '
                      'transaction_time varchar(50), trans_amount decimal, direction varchar(10), '
                      'bill_code_raw varchar(100), is_check integer, updated_at datetime)')
        self.user = get_user_model().objects.create_user('reconcile-test')
        self.client.force_login(self.user)

    def tearDown(self):
        with connection.cursor() as c:
            c.execute('DROP TABLE bank_notifications')

    def bank(self, id=1, when='2026-09-08 12:00:00', amount=100, direction='out', checked=0, code=''):
        with connection.cursor() as c:
            c.execute('INSERT INTO bank_notifications VALUES (%s,%s,%s,%s,%s,%s,%s,%s)',
                      [id, '001', when, amount, direction, code, checked, '2026-09-08 12:00:01'])

    def bill(self, trn='TBG260900000001', when='2026-09-08 11:45:00', amount=-100, status='C', deleted='0'):
        return dict(TrnID=trn, CardPay=Decimal(amount), CreatedDate=dt.datetime.fromisoformat(when), Status=status, IsDel=deleted)

    def run_match(self, bills):
        PmvState.objects.filter(key=R.LAST_RUN).delete()
        with patch.object(R.gateway, 'pmv_read', return_value=bills) as pmv:
            result = R.reconcile(self.today)
        return result, pmv

    def test_match_sign_window_and_repeat(self):
        self.bank()
        result, pmv = self.run_match([self.bill()])
        self.assertEqual(result['matched'], 1)
        self.assertEqual(query('SELECT bill_code_raw,is_check FROM bank_notifications')[0],
                         dict(bill_code_raw='TBG260900000001', is_check=1))
        self.assertEqual(pmv.call_args.kwargs['target'], 'kk')
        self.assertEqual(pmv.call_args.kwargs['query_timeout'], 3)
        result, pmv = self.run_match([self.bill()])
        pmv.assert_not_called()
        self.assertEqual(BankReconcileState.objects.count(), 1)

    def test_inclusive_30_minutes_and_reject_future_or_old(self):
        self.bank()
        row = query('SELECT * FROM bank_notifications')[0]
        for when, expected in [('11:30:00',1), ('12:00:00',1), ('11:29:59',0), ('12:00:01',0)]:
            self.assertEqual(len(R.candidates(row, [self.bill(when='2026-09-08 '+when)])), expected)
        for bill in [self.bill(amount=100), self.bill(amount=-99), self.bill(status='W'), self.bill(deleted='1')]:
            self.assertEqual(R.candidates(row, [bill]), [])

    def test_midnight_looks_into_previous_day(self):
        self.bank(when='2026-09-08 00:05:00')
        result, _ = self.run_match([self.bill(when='2026-09-07 23:35:00')])
        self.assertEqual(result['matched'], 1)

    def test_only_today_out_unchecked_positive(self):
        self.bank(1, direction='in')
        self.bank(2, checked=1)
        self.bank(3, when='2026-09-07 12:00:00')
        self.bank(4, amount=0)
        result, pmv = self.run_match([self.bill()])
        self.assertEqual(result['pending'], 0)
        pmv.assert_not_called()

    def test_multiple_bills_no_choice(self):
        self.bank()
        result, _ = self.run_match([self.bill(), self.bill(trn='TBG260900000002')])
        self.assertEqual(result['matched'], 0)
        self.assertEqual(result['needs_review'], 1)
        self.assertEqual(query('SELECT is_check FROM bank_notifications')[0]['is_check'], 0)

    def test_two_notifications_do_not_share_bill(self):
        self.bank(1)
        self.bank(2)
        result, _ = self.run_match([self.bill()])
        self.assertEqual(result['matched'], 0)
        self.assertEqual(result['needs_review'], 2)

    def test_existing_code_is_not_overwritten(self):
        self.bank(code='MANUAL')
        result, _ = self.run_match([self.bill()])
        self.assertEqual(result['matched'], 0)
        self.assertEqual(query('SELECT bill_code_raw FROM bank_notifications')[0]['bill_code_raw'], 'MANUAL')

    def test_bill_used_by_checked_or_previous_day_notification(self):
        self.bank(1)
        self.bank(2, when='2026-09-07 23:50:00', checked=1, code=self.bill()['TrnID'])
        result, _ = self.run_match([self.bill()])
        self.assertEqual(result['matched'], 0)

    def test_webhook_change_during_pmv_read(self):
        self.bank()
        def changed(*args, **kwargs):
            with connection.cursor() as c:
                c.execute('UPDATE bank_notifications SET trans_amount=200 WHERE id=1')
            return [self.bill()]
        with patch.object(R.gateway, 'pmv_read', side_effect=changed):
            result = R.reconcile(self.today)
        self.assertEqual(result['matched'], 0)
        self.assertFalse(BankReconcileState.objects.exclude(trn_id=None).exists())

    def test_second_transfer_arrives_during_pmv_read(self):
        self.bank()
        def inserted(*args, **kwargs):
            self.bank(2)
            return [self.bill()]
        with patch.object(R.gateway, 'pmv_read', side_effect=inserted):
            result = R.reconcile(self.today)
        self.assertEqual(result['matched'], 0)

    def test_reserved_bill_survives_webhook_reset(self):
        self.bank()
        self.run_match([self.bill()])
        with connection.cursor() as c:
            c.execute("UPDATE bank_notifications SET is_check=0,bill_code_raw='' WHERE id=1")
        result, pmv = self.run_match([self.bill()])
        self.assertEqual(result['matched'], 0)
        self.assertEqual(result['needs_review'], 1)
        pmv.assert_not_called()
        self.assertEqual(BankReconcileState.objects.get().trn_id, self.bill()['TrnID'])

    def test_shared_cooldown_avoids_second_pmv_query(self):
        self.bank()
        with patch.object(R.gateway, 'pmv_read', return_value=[]) as pmv:
            R.reconcile(self.today)
            self.assertEqual(R.reconcile(self.today)['status'], 'busy')
        pmv.assert_called_once()

    def test_unmatched_backoff_and_changed_source_retries(self):
        self.bank()
        self.run_match([])
        result, pmv = self.run_match([])
        pmv.assert_not_called()
        with connection.cursor() as c:
            c.execute('UPDATE bank_notifications SET trans_amount=200 WHERE id=1')
        result, pmv = self.run_match([self.bill(amount=-200)])
        self.assertEqual(result['matched'], 1)

    def test_pmv_failure_preserves_source(self):
        self.bank()
        with patch.object(R.gateway, 'pmv_read', side_effect=RuntimeError('offline')):
            result = R.reconcile(self.today)
        self.assertEqual(result['status'], 'error')
        self.assertEqual(query('SELECT is_check FROM bank_notifications')[0]['is_check'], 0)
        self.assertEqual(BankReconcileState.objects.get().status, 'error')

    def test_post_today_only_auth_csrf_and_busy(self):
        url = '/banle/chuyen-khoan/doi-soat/'
        with patch.object(R.timezone, 'localdate', return_value=self.today), patch.object(R, 'single_run', return_value=nullcontext(False)) as lock:
            self.assertEqual(self.client.get(url).status_code, 405)
            self.assertEqual(self.client.post(url, {'d2':'2026-09-07'}).json()['status'], 'skipped')
            lock.assert_not_called()
            self.assertEqual(self.client.post(url, {'d2':self.today.isoformat()}).json()['status'], 'busy')
        protected = Client(enforce_csrf_checks=True)
        protected.force_login(self.user)
        self.assertEqual(protected.post(url, {'d2':self.today.isoformat()}).status_code, 403)
        self.client.logout()
        self.assertEqual(self.client.post(url, {'d2':self.today.isoformat()}).status_code, 302)
