import datetime as dt
from types import SimpleNamespace
from unittest.mock import patch
from django.test import TestCase, RequestFactory
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from apps.pos import mobile_orders as M

def header(pk='TRC1',status='W',shop=''):
    return dict(TrnID=pk,BillCode='26-09-18-000001',TrnDate=dt.date(2026,9,18),TrnTime='10:00',
        Status=status,ShopID=shop,Description='{"notes":[],"promise":"2026-09-19"}',UserID_Upd='',
        TienCoc=1000,CashPay=800,CardPay=200,EmpID='E1',CustID='C1',CustName='Khách thử',Phone='0901234567',EmpName='NV thử')

class MobileOrderTests(TestCase):
    def setUp(self):
        self.user=get_user_model().objects.create_superuser('orders-test',password='test')
    def request(self,method='get',params=None):
        r=getattr(RequestFactory(),method)('/banle/mobile/orders/',params or {})
        r.user=self.user;r.session={}
        return r
    def test_source_status_not_confused_with_delivery(self):
        self.assertTrue(M.decorate(header(status='C'))['delivered'])
        vendor=M.decorate(header(status='C',shop='SHOP'))
        self.assertFalse(vendor['delivered']);self.assertEqual(vendor['state_label'],'Đã sử dụng cọc')
        self.assertTrue(M.decorate(header())['split_ok'])
    def test_index_mssql_only_and_no_write_controls(self):
        with patch.object(M,'read',side_effect=[[header()],[]]) as read:
            response=M.index(self.request())
        self.assertContains(response,'Khách thử');self.assertContains(response,'Chi tiết')
        self.assertNotContains(response,'Chờ kết nối')
        self.assertEqual(read.call_count,2)
        for call in read.call_args_list:self.assertTrue(call.args[0].startswith('SELECT'))
    def test_detail_read_only(self):
        with patch.object(M,'read',side_effect=[[header()],[],[],[]]) as read:
            response=M.detail(self.request(),'TRC1')
        self.assertContains(response,'Chi tiết đặt hàng');self.assertNotContains(response,'hx-post')
        self.assertNotContains(response,'Lưu thông tin')
        for call in read.call_args_list:self.assertTrue(call.args[0].startswith('SELECT'))
    def test_reject_post_before_database_access(self):
        with patch.object(M,'read') as read:
            self.assertEqual(M.index(self.request('post')).status_code,405)
            self.assertEqual(M.detail(self.request('post'),'TRC1').status_code,405)
            read.assert_not_called()
    def test_unavailable_is_not_empty_success(self):
        with patch.object(M,'read',side_effect=RuntimeError('offline')):
            response=M.index(self.request())
        self.assertContains(response,'Chưa kết nối được MSSQL')
        self.assertNotContains(response,'Không có phiếu phù hợp')
    def test_search_parameterized_and_filters(self):
        r=self.request(params={'q':"%' OR 1=1",'mine':'1'})
        r.session={'mobile_employee':{'employee_pmv':'E1'}}
        with patch.object(M,'read',return_value=[]) as read:
            M.index(r)
        sql,params=read.call_args.args
        self.assertNotIn("OR 1=1",sql);self.assertIn('d.EmpID=?',sql);self.assertEqual(params[-1],'E1')
    def test_permission_checked_before_read(self):
        with patch('apps.pos.quyen.chan',side_effect=PermissionDenied),patch.object(M,'read') as read:
            with self.assertRaises(PermissionDenied):M.index(self.request())
            read.assert_not_called()
