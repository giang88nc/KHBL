import datetime as dt
import json
from unittest.mock import patch, MagicMock
from django.test import TestCase, SimpleTestCase
from django.core.cache import cache
from django.utils import timezone
from apps.pos import deposit_reminders as R, deposit_orders as O, deposit_workspace as W
from apps.pos.deposit_models import DepositEvent, DepositMessage, DepositMessageTemplate
from apps.pmv import gateway as G


class ReminderTests(TestCase):
    day=dt.date(2026,9,13)

    def setUp(self):
        cache.clear()
        self.template=DepositMessageTemplate.objects.create(name='Nhắc',body='{bill}')

    def row(self, pk='TDC1', state='ready', promise=None, **kw):
        return dict(TrnID=pk,fulfilment=state,active=state not in ('cancelled','delivered','applied'),
                    promise_date=promise,Description='',CustName='An',Phone='0901234567',**kw)

    def message(self,status='sent',day=None,**kw):
        date=day or self.day
        return DepositMessage.objects.create(target='sandbox',trn_id='TDC1',template=self.template,
            template_version=1,planned_day=date,status=status,
            sent_at=timezone.make_aware(dt.datetime.combine(date,dt.time(10))) if status=='sent' else None,**kw)

    def test_union_ready_due_overdue_and_exclude_closed(self):
        rows=[self.row('TDC1',promise=self.day+dt.timedelta(days=2)),
              self.row('TDC2','ordering',self.day), self.row('TDC3','crafting',self.day-dt.timedelta(days=1)),
              self.row('TDC4','ordering',self.day+dt.timedelta(days=1)),self.row('TDC5','cancelled',self.day)]
        self.assertEqual({r['TrnID'] for r in R.suggestions(rows,'sandbox',self.day)},{'TDC1','TDC2','TDC3'})

    def test_dismiss_day_scoped_persistent_and_add_restores(self):
        row=self.row(promise=self.day)
        for action,expected in [('reminder_remove',0),('reminder_add',1)]:
            DepositEvent.objects.create(target='sandbox',trn_id='TDC1',action=action,username='u',token=action,data={'day':self.day.isoformat()})
            self.assertEqual(len(R.suggestions([row],'sandbox',self.day)),expected)
        self.assertEqual(len(R.suggestions([row],'kk',self.day)),1)
        self.assertEqual(len(R.suggestions([row],'sandbox',self.day+dt.timedelta(days=1))),1)

    def test_manual_add_allows_early_order_but_not_terminal(self):
        DepositEvent.objects.create(target='sandbox',trn_id='TDC1',action='reminder_add',username='u',token='add',data={'day':str(self.day)})
        self.assertEqual(len(R.suggestions([self.row(state='ordering',promise=self.day+dt.timedelta(days=20))],'sandbox',self.day)),1)
        self.assertEqual(R.suggestions([self.row(state='delivered')],'sandbox',self.day),[])

    def test_repeat_waits_seven_days_and_counts_only_confirmed_messages(self):
        self.message(day=self.day-dt.timedelta(days=6))
        row=self.row(promise=self.day-dt.timedelta(days=10))
        self.assertEqual(R.suggestions([row],'sandbox',self.day),[])
        next_rows=R.suggestions([row],'sandbox',self.day+dt.timedelta(days=1))
        self.assertEqual(next_rows[0]['reminder_count'],2)
        self.assertEqual(next_rows[0]['reminder_date'],self.day+dt.timedelta(days=1))

    def test_pending_uncertain_suppress_until_cancelled(self):
        m=self.message(status='uncertain')
        self.assertEqual(R.suggestions([self.row()],'sandbox',self.day),[])
        m.status='cancelled';m.save()
        self.assertEqual(len(R.suggestions([self.row()],'sandbox',self.day)),1)

    def test_external_description_marker_uses_due_date_not_count_as_sent_today(self):
        row=self.row();row['Description']='{"zalo":{"2026-09-14":"nhắc lần 3"}}'
        self.assertEqual(R.suggestions([row],'sandbox',self.day),[])
        result=R.suggestions([row],'sandbox',dt.date(2026,9,14))
        self.assertEqual(result[0]['reminder_count'],3)

    def test_sent_outbox_is_idempotent_and_sync_never_calls_oa(self):
        m=self.message();row=self.row(promise=self.day)
        R.record_sent(m,row);R.record_sent(m,row)
        self.assertEqual(DepositEvent.objects.filter(action='reminder_zalo').count(),1)
        with patch.object(G,'pmv_deposit_zalo') as write:
            R.sync_descriptions('sandbox');R.sync_descriptions('sandbox')
            write.assert_called_once_with('TDC1','2026-09-20',2,target='sandbox')

    def test_sync_failure_keeps_sent_and_pending_outbox(self):
        m=self.message();R.record_sent(m,self.row(promise=self.day))
        with patch.object(G,'pmv_deposit_zalo',side_effect=ValueError('Full')) as write:
            R.sync_descriptions('sandbox');R.sync_descriptions('sandbox')
            self.assertEqual(write.call_count,1)
        m.refresh_from_db();self.assertEqual(m.status,'sent')
        self.assertFalse(DepositEvent.objects.filter(action='reminder_synced').exists())


class DescriptionTests(SimpleTestCase):
    def test_json_date_notes_and_other_fields_survive_zalo_merge(self):
        before={'notes':[{'date':'2026-09-13','text':'Khách gọi'}],'promise':'2026-09-20','custom':'keep'}
        after=json.loads(O.merge_zalo(json.dumps(before),{'2026-09-27':'nhắc lần 1'}))
        for key,value in before.items():self.assertEqual(after[key],value)
        self.assertEqual(O.parse_description(json.dumps(after))['promise_date'],dt.date(2026,9,20))
        raw='{"2026-09-13":"Khách chốt","zalo":{"2026-09-20":"nhắc lần 1"}}'
        self.assertEqual(O.note_entries(raw),[{'date':'2026-09-13','text':'Khách chốt'}])

    def test_legacy_promise_and_note_preserved_and_overflow_rejected(self):
        raw=O.merge_zalo('HEN:2026-09-20 | Khách chốt | 0',{'2026-09-27':'nhắc lần 1'})
        self.assertEqual(O.parse_description(raw)['promise_date'],dt.date(2026,9,20))
        with self.assertRaisesRegex(ValueError,'đã đầy'):O.merge_zalo('x'*500,{'2026-09-27':'nhắc lần 1'})

    def test_gateway_updates_only_description_and_stamp_under_lock(self):
        cn=MagicMock();cur=cn.cursor.return_value;cur.rowcount=1
        before='{"notes":[],"promise":"2026-09-13"}'
        after=O.merge_zalo(before,{'2026-09-20':'nhắc lần 2'})
        cur.fetchone.side_effect=[(before,),(after,)]
        with patch.object(G,'_connect_dich',return_value=cn),patch.object(G,'_audit'),patch('apps.pmv.models.PmvState.get',return_value='0'):
            G.pmv_deposit_zalo('TDC260900000001','2026-09-20',2,target='sandbox')
        updates=[c.args[0] for c in cur.execute.call_args_list if c.args[0].startswith('UPDATE')]
        self.assertEqual(updates,['UPDATE TRN_DATCOC SET Description=?,TrnDateTime_Upd=GETDATE() WHERE TrnID=?'])
        cn.commit.assert_called_once()

    def test_new_tab_excludes_closed_receipts_even_created_today(self):
        rows=W.prepare({'headers':[{'TrnID':'1','Status':'C','TrnDate':timezone.localdate()},
            {'TrnID':'2','Status':'W','TrnDate':timezone.localdate()}],'items':{}})
        rows[0]['fulfilment']='delivered';rows=W.classify(rows)
        self.assertEqual([r['TrnID'] for r in W.paginate(rows,{'tab':'all'})['rows']],['2'])
        self.assertEqual([r['TrnID'] for r in W.paginate(rows,{'tab':'new'})['rows']],['2'])
        rows[1]['fulfilment']='crafting'
        self.assertTrue(W.matches(rows[1],{'tab':'ordering'}))
        self.assertFalse(W.matches(rows[0],{'tab':'ordering'}))
