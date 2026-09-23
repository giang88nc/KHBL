import datetime as dt
import json
from unittest.mock import patch,MagicMock
from django.test import TestCase,RequestFactory
from django.contrib.auth import get_user_model
from django.core import signing
from apps.pos import mobile_online as O
from apps.pmv import gateway as G
from apps.pos.mobile_invoice import stamp

def source():
    return dict(TrnID='T1',BillCode='B1',Desc3='{"channel":"online","customer_hold":true,"note":"Giữ lại","keep":1}',Status='C',IsDel='0')

class OnlineTests(TestCase):
    def setUp(self):self.user=get_user_model().objects.create_superuser('online-test',password='test')
    def req(self,method='get',data=None):
        r=getattr(RequestFactory(),method)('/banle/mobile/online/',data or {});r.user=self.user;r.session={};return r
    def test_dates(self):
        today=dt.date(2026,1,10)
        self.assertEqual(O.dates({},today),(dt.date(2026,1,1),dt.date(2026,1,31)))
        self.assertEqual(O.dates({'quick':'previous'},today),(dt.date(2025,12,1),dt.date(2025,12,31)))
        with self.assertRaises(ValueError):O.dates({'d1':'2026-10-10','d2':'2026-09-09'},today)
    def test_pickup_merge_and_validation(self):
        result=json.loads(O.pickup_document(source(),{'full_name':'NV giao'},dt.datetime(2026,9,19)))
        self.assertFalse(result['customer_hold']);self.assertEqual(result['keep'],1)
        self.assertTrue(result['customer_pickup']['confirmed']);self.assertEqual(len(result['customer_pickups']),1)
        with self.assertRaises(ValueError):O.pickup_document({**source(),'Desc3':json.dumps(result)},{},dt.datetime.now())
        with self.assertRaises(ValueError):O.pickup_document({**source(),'IsDel':'1'},{},dt.datetime.now())
    def test_list_filters_and_totals(self):
        row={**source(),'TrnDate':dt.date.today(),'PayAmount':100,'CashPay':60,'CardPay':40,'Phone':'0901234567'}
        with patch.object(O,'read',return_value=[row,{**row,'TrnID':'T2','Desc3':'{"channel":"store"}'}]):
            response=O.index(self.req(data={'tab':'hold'}))
        self.assertContains(response,'1 đơn');self.assertContains(response,'100đ')
        self.assertContains(response,'Cập nhật');self.assertNotContains(response,'T2')
    def test_confirmation_and_token(self):
        row=source()
        with patch.object(O,'read',return_value=[row]),patch.object(G,'pmv_online_pickup') as write:
            response=O.pickup(self.req('post',{'confirm':'1','version':'invalid'}),'T1')
            self.assertContains(response,'hết hạn');write.assert_not_called()
            token=signing.dumps({'user':self.user.pk,'pk':'T1','stamp':stamp(row)},salt='online-pickup')
            response=O.pickup(self.req('post',{'confirm':'1','version':token}),'T1')
            self.assertContains(response,'Đã xác nhận');write.assert_called_once()
    def test_wedding_includes_store_excludes_string_true(self):
        row={**source(),'TrnDate':dt.date.today(),'PayAmount':100,'CashPay':100,'CardPay':0}
        rows=[{**row,'BillCode':'WEDDING1','Desc3':json.dumps({'channel':'store','wedding':True,'NgayCuoi':'2026-10-10','wedding_quantity':'3','wedding_bill_total':'100'})},
              {**row,'TrnID':'T2','BillCode':'STRINGTRUE','Desc3':'{"channel":"store","wedding":"true"}'},
              {**row,'TrnID':'T3','BillCode':'ONLINEONLY'}]
        with patch.object(O,'read',return_value=rows):
            response=O.index(self.req(data={'tab':'wedding'}))
            self.assertContains(response,'WEDDING1');self.assertContains(response,'10/10/2026')
            self.assertNotContains(response,'STRINGTRUE');self.assertNotContains(response,'ONLINEONLY')
            response=O.index(self.req(data={'tab':'online'}))
            self.assertNotContains(response,'WEDDING1');self.assertContains(response,'ONLINEONLY')
    def test_gateway_only_desc3_and_stale_rollback(self):
        row=source();cols=O.PICKUP_COLUMNS
        for stale in (True,False):
            cn=MagicMock();cur=cn.cursor.return_value;cur.rowcount=1;cur.fetchall.return_value=[tuple(row[c] for c in cols)]
            updated=O.pickup_document(row,{},dt.datetime(2026,9,19))
            cur.fetchone.side_effect=[None,tuple(({**row,'Desc3':updated})[c] for c in cols)]
            with patch.object(G,'_connect_dich',return_value=cn),patch('apps.pmv.models.PmvState.get',return_value='0'),patch.object(O,'pickup_document',return_value=updated):
                if stale:
                    with self.assertRaises(ValueError):G.pmv_online_pickup('T1','stale',{},target='sandbox')
                    cn.rollback.assert_called_once();cn.commit.assert_not_called()
                else:
                    G.pmv_online_pickup('T1',stamp(row),{},target='sandbox');cn.commit.assert_called_once()
                    sql=[c.args[0] for c in cur.execute.call_args_list if c.args[0].startswith('UPDATE')]
                    self.assertEqual(sql,['UPDATE TRN_RT_BUYSELL SET Desc3=? WHERE TrnID=?'])

    def test_month_routing(self):
        from apps.pos import mobile_sources as S
        with patch.object(S,'month_bounds',return_value=(dt.date(2026,9,1),dt.date(2026,9,30))):
            self.assertEqual(S.periods(dt.date(2026,8,1),dt.date(2026,9,30)),[
                ('hist',dt.date(2026,8,1),dt.date(2026,8,31)),('kk',dt.date(2026,9,1),dt.date(2026,9,30))])
            self.assertEqual(S.periods(None,None,True),[('hist',None,None),('kk',None,None)])

    def test_hold_days_and_delivered_not_false_only(self):
        row={**source(),'TrnDate':dt.date(2026,8,1),'PayAmount':100,'CashPay':100,'CardPay':0}
        delivered={**row,'TrnID':'T2','BillCode':'DELIVERED','Desc3':O.pickup_document(source(),{},dt.datetime(2026,9,1))}
        plain={**row,'TrnID':'T3','BillCode':'NEVERHELD','Desc3':'{"channel":"online","customer_hold":false}'}
        with patch.object(O,'read',return_value=[row,delivered,plain]) as read,patch.object(O.timezone,'localdate',return_value=dt.date(2026,9,20)):
            response=O.index(self.req(data={'tab':'hold','hold_state':'all'}))
            self.assertContains(response,'2 đơn');self.assertContains(response,'📌50')
            self.assertContains(response,'DELIVERED');self.assertNotContains(response,'NEVERHELD')
            for call in read.call_args_list:self.assertIn('b.TrnDate>=?',call.args[0])
            response=O.index(self.req(data={'tab':'hold','hold_state':'delivered'}))
            self.assertContains(response,'1 đơn');self.assertNotContains(response,'✓ Cập nhật')

    def test_hold_date_source_and_search_exception(self):
        with patch.object(O.timezone,'localdate',return_value=dt.date(2026,9,20)),patch.object(O,'read',return_value=[]) as read:
            response=O.index(self.req(data={'tab':'hold'}))
            self.assertEqual([c.kwargs['target'] for c in read.call_args_list],['kk'])
            self.assertEqual(read.call_args.args[1],('2026-09-01','2026-10-01'))
            self.assertNotContains(response,'Không giới hạn ngày')
            read.reset_mock()
            O.index(self.req(data={'tab':'hold','quick':'previous'}))
            self.assertEqual([c.kwargs['target'] for c in read.call_args_list],['hist'])
            self.assertEqual(read.call_args.args[1],('2026-08-01','2026-09-01'))
            read.reset_mock()
            O.index(self.req(data={'tab':'hold','d1':'2026-08-15','d2':'2026-09-15'}))
            self.assertEqual([c.kwargs['target'] for c in read.call_args_list],['hist','kk'])
            read.reset_mock()
            response=O.index(self.req(data={'tab':'hold','q':'B1'}))
            self.assertEqual([c.kwargs['target'] for c in read.call_args_list],['hist','kk'])
            for call in read.call_args_list:self.assertNotIn('b.TrnDate>=?',call.args[0])
            self.assertContains(response,'Không giới hạn ngày')

    def test_hist_pickup_and_source_change(self):
        row=source()
        token=signing.dumps({'user':self.user.pk,'pk':'T1','stamp':stamp(row),'target':'hist'},salt='online-pickup')
        with patch.object(O,'read',side_effect=[[],[row]]),patch.object(G,'pmv_online_pickup') as write:
            response=O.pickup(self.req('post',{'confirm':'1','version':token}),'T1')
            self.assertContains(response,'Đã xác nhận');self.assertEqual(write.call_args.kwargs['target'],'hist')
        with patch.object(O,'read',return_value=[row]),patch.object(G,'pmv_online_pickup') as write:
            response=O.pickup(self.req('post',{'confirm':'1','version':token}),'T1')
            self.assertContains(response,'đã thay đổi');write.assert_not_called()

    def test_hist_merge_preserves_pickup(self):
        cn=MagicMock();cur=cn.cursor.return_value
        cn.__enter__.return_value=cn;cur.fetchone.return_value=(1,);cur.fetchall.return_value=[('UPDATE',)]
        with patch.object(G,'_hist_connect',return_value=cn),patch.object(G,'_audit'):
            G.hist_bulk_merge('TRN_RT_BUYSELL',['TrnID','Desc3'],['TrnID'],[('T1','{}')])
        sql=next(c.args[0] for c in cur.execute.call_args_list if c.args[0].startswith('MERGE'))
        self.assertIn('CASE WHEN t.[_mobile_pickup_confirmed]=1 THEN t.[Desc3]',sql)

    def test_hist_gateway_marks_confirmation(self):
        row=source();cols=O.PICKUP_COLUMNS;cn=MagicMock();cur=cn.cursor.return_value
        cur.rowcount=1;cur.fetchall.return_value=[tuple(row[c] for c in cols)]
        updated=O.pickup_document(row,{},dt.datetime(2026,9,20))
        cur.fetchone.side_effect=[None,tuple(({**row,'Desc3':updated})[c] for c in cols)]
        with patch.object(G,'_connect_dich',return_value=cn),patch.object(G,'pmv_read',return_value=[]),patch.object(G,'duoc_ghi_kk',return_value=True),patch('apps.pmv.models.PmvState.get',return_value='0'),patch.object(O,'pickup_document',return_value=updated):
            G.pmv_online_pickup('T1',stamp(row),{},target='hist')
        cn.commit.assert_called_once()
        self.assertTrue(any('Desc3=?,_mobile_pickup_confirmed=1' in c.args[0] for c in cur.execute.call_args_list))
