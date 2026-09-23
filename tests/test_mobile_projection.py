import datetime as dt
from unittest.mock import patch
from django.test import TestCase, RequestFactory
from django.contrib.auth import get_user_model
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from apps.pos.models import MoneyFlow
from apps.pos.mobile_projection import refresh
from apps.pos.money_flow_views import mobile_in


class MobileProjectionTests(TestCase):
    def setUp(self):
        self.now=timezone.localtime().replace(tzinfo=None)
        self.flow=MoneyFlow.objects.create(source_system='KHBL',source_type='gold_bill_retail',
            source_id='TRN1',source_bill_code='B1',direction='IN',service='RETAIL',
            business_date=self.now.date(),expected_amount=100,source_snapshot={'existing':True})

    def _bill(self, **kw):
        from apps.pos.models import GoldBill
        data=dict(trn_id='TRN1',bill_code='B1',trn_date=self.now.date(),trn_time=self.now.strftime('%H:%M:%S'),
                  emp_id='EMP1',employee_name='Receiver',cust_id='C1',cust_name='Customer',status='C')
        data.update(kw)
        return GoldBill.objects.create(**data)

    @patch('apps.pos.mobile_projection.gateway.pmv_read',side_effect=AssertionError('refresh không được gọi KK'))
    def test_source_metadata_from_gold_bill_without_kk(self, read):
        # 21/09/2026: khối mobile dựng từ gold_bill (MySQL) — không một truy vấn KK nào.
        bill=self._bill()
        self.assertEqual(refresh(),1)
        self.flow.refresh_from_db()
        self.assertTrue(self.flow.source_snapshot['existing'])
        meta=self.flow.source_snapshot['mobile']
        self.assertEqual((meta['employee_pmv'],meta['employee_name'],meta['basis']),('EMP1','Receiver','gold_bill'))
        self.assertEqual(meta['happened_at'][:16],self.now.isoformat()[:16])
        self.assertEqual(self.flow.customer_name,'Customer')
        self.assertEqual(refresh(),0)                          # không đổi gì → không ghi lại
        bill.bill_code='WRONG';bill.save(update_fields=['bill_code'])
        self.assertEqual(refresh(),0)                          # lệch mã phiếu → bỏ qua
        read.assert_not_called()

    @patch('apps.pos.mobile_projection.gateway.pmv_read')
    def test_enrich_phone_and_change_from_kk(self, read):
        from apps.pos.mobile_projection import enrich_from_kk, tender_of
        self._bill()
        with patch('apps.pos.mobile_projection.gateway.pmv_read',side_effect=AssertionError('no KK')):
            refresh()
        read.return_value=[dict(TrnID='TRN1',BillCode='B1',CustID='C1',CustName='Customer',
            Phone='0901234567',GhiChu2='',GhiChu3='',CashPay=100,CardPay=0,PayAmount=100,
            TienTraLai=20,TienKhachTraThuc=120,Desc3='')]
        self.assertEqual(enrich_from_kk(),1)
        self.flow.refresh_from_db()
        self.assertEqual(self.flow.source_snapshot['mobile']['phone'],'0901234567')
        self.assertEqual(tender_of(self.flow)[0],20)
        self.assertTrue(tender_of(self.flow)[1])
        # 23/09/2026: 2 số này còn ghi THẲNG vào cột money_flow để bảng khác khỏi bóc JSON
        self.assertEqual((self.flow.cus_cash,self.flow.cus_change),(120,20))
        self.assertEqual(enrich_from_kk(),0)                   # đã có → không ghi lại

    @patch('apps.pos.mobile_projection.gateway.pmv_read',side_effect=AssertionError('project_now không được gọi KK'))
    def test_project_now_creates_flow_immediately_without_kk(self, read):
        from apps.pos import money_flow as MF
        self._bill(trn_id='TRN2',bill_code='B2',tong=500000,tien_mat=500000)
        with patch('apps.pos.services.nhan_vien_ban',return_value=[]):
            MF.project_now('TRN2')
        flow=MoneyFlow.objects.get(source_system='KHBL',source_id='TRN2')
        self.assertEqual((flow.direction,flow.expected_amount),('IN',500000))
        self.assertEqual(flow.source_snapshot['mobile']['employee_pmv'],'EMP1')
        read.assert_not_called()

    def test_list_reads_money_flow_without_gold_bill_or_remote(self):
        self.flow.source_snapshot={'mobile':{'happened_at':self.now.isoformat(),
            'employee_pmv':'EMP1','employee_name':'Receiver','phone':'0901234567'}}
        self.flow.save()
        req=RequestFactory().get('/banle/mobile/money-in/',{'mine':'1'})
        req.user=get_user_model().objects.create_superuser('projection-test',password='x')
        req.session={'mobile_employee':{'employee_pmv':'EMP1'}}
        with patch('apps.pos.money_flow_views.render',side_effect=lambda request,tpl,ctx:ctx), CaptureQueriesContext(connection) as queries:
            result=mobile_in(req)
        self.assertEqual([r.pk for r in result['rows']],[self.flow.pk])
        self.assertFalse(any('FROM "gold_bill"' in q['sql'] or 'khj_cd.' in q['sql'] for q in queries))
        req.session['mobile_employee']['employee_pmv']='OTHER'
        with patch('apps.pos.money_flow_views.render',side_effect=lambda request,tpl,ctx:ctx):
            self.assertEqual(mobile_in(req)['rows'],[])
