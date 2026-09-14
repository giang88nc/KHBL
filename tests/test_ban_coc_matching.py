from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.http import HttpResponse
from django.template.loader import render_to_string
from django.test import SimpleTestCase, RequestFactory

from apps.pos import ban_coc as B, views as V


class DepositProductMatchingTests(SimpleTestCase):
    def phieu(self, *items):
        return {'id':'P1','ma':'P1','so_mon':len(items),'tien':Decimal(1000),'san_pham':list(items)}

    def item(self,code='A1',source='stock',name='Nhẫn'):
        return {'ma':code,'nguon':source,'ten':name}

    def test_tooltip_exact_codes_and_any_missing_disables_whole_deposit(self):
        p=self.phieu(self.item(),self.item('A10',name='Dây'))
        result=B.doi_soat_title([p],[{'row':{'ProductCode':' a1 '}}])[0]
        self.assertEqual(result['doi_soat_title'],'✓ A1: Nhẫn\n⚠️ A10: chưa có trong danh sách.')
        self.assertTrue(result['khong_duoc_dung']);self.assertEqual(result['ma_thieu'],['A10'])
        self.assertNotIn('doi_soat_title',p)
        self.assertFalse(B.doi_soat_title([p],[{'row':{'ProductCode':c}} for c in ('A1','A10')])[0]['khong_duoc_dung'])

    def test_ordered_products_are_exempt_even_with_a_template_code(self):
        p=self.phieu(self.item('Khách đặt','new'),self.item('MAU-01','new'))
        result=B.doi_soat_title([p],[])[0]
        self.assertFalse(result['khong_duoc_dung']);self.assertNotIn('⚠',result['doi_soat_title'])
        mixed=self.phieu(*p['san_pham'],self.item())
        self.assertTrue(B.doi_soat_title([mixed],[])[0]['khong_duoc_dung'])

    def test_money_only_allowed_but_incomplete_details_disabled(self):
        p=self.phieu()
        self.assertFalse(B.doi_soat_title([p],[])[0]['khong_duoc_dung'])
        p['so_mon']=1
        self.assertTrue(B.doi_soat_title([p],[])[0]['khong_duoc_dung'])

    def test_disabled_markup_keeps_escaped_tooltip(self):
        p=self.phieu(self.item('A"1'))
        result=B.doi_soat_title([p],[])
        html=render_to_string('pos/_ban_coc_ds.html',{'coc_ds':result,'coc_ids':[]})
        self.assertIn('disabled aria-disabled="true"',html);self.assertIn('A&quot;1',html)
        self.assertNotIn('Đối chiếu mã SP với DS BÁN HÀNG:',html)

    def test_fresh_check_reads_modes_and_does_not_trust_cached_match(self):
        c=MagicMock();c.query.return_value=[{'TrnID':'P1','ProductDesc':'Nhẫn','Notes':'SP: A1 | mẫu'}]
        self.assertIn('A1 chưa có',B.kiem_san_pham(c,['P1'],[]))
        self.assertEqual(B.kiem_san_pham(c,['P1'],[{'row':{'ProductCode':'A1'}}]),'')
        c.query.return_value=[{'TrnID':'P1','ProductDesc':'Nhẫn','Notes':'MA_DAT:MAU1 | mẫu'}]
        self.assertEqual(B.kiem_san_pham(c,['P1'],[]),'');c.call.assert_not_called()

    def test_forged_select_is_rejected_before_changing_cart(self):
        request=RequestFactory().post('/banle/ban-hang/coc/',{'id':'P1','mo':'1'})
        g={'cust':{'id':'C1'},'ban':[],'coc_ids':[]}
        c=MagicMock();c.query.return_value=[{'TrnID':'P1','Notes':'SP:A1 |','ProductDesc':'Nhẫn'}]
        with patch.object(V,'_dang_khoa',return_value=False),patch.object(V.cart,'get',return_value=g),patch.object(V.cart,'save') as save,patch.object(V,'_coc_ctx',return_value={'coc_ds':[self.phieu(self.item())]}),patch.object(V.S,'client',return_value=c),patch.object(V,'_loi',side_effect=lambda req,msg,extra=None:HttpResponse(msg)):
            response=V.ban_coc(request)
        self.assertIn('chưa dùng được',response.content.decode());save.assert_not_called();c.call.assert_not_called()

    def test_removed_product_blocks_checkout_before_invoice_write(self):
        request=RequestFactory().post('/banle/ban-hang/thanh-toan/')
        request.session=SimpleNamespace(session_key='test-only');g={'coc_ids':['P1'],'ban':[]}
        c=MagicMock();c.query.return_value=[{'TrnID':'P1','Notes':'SP:A1 |','ProductDesc':'Nhẫn'}]
        with patch.object(V.cart,'get',return_value=g),patch.object(V,'_phien',return_value={}),patch.object(V,'_kiem_truoc_khi_luu',return_value=('','')),patch.object(V.cache,'add',return_value=True),patch.object(V.S,'client',return_value=c),patch.object(V.B,'luu') as save,patch.object(V.B,'chot') as complete,patch.object(V,'_loi',side_effect=lambda req,msg,extra=None:HttpResponse(msg)):
            response=V.ban_thanh_toan(request)
        self.assertIn('chưa dùng được',response.content.decode());save.assert_not_called();complete.assert_not_called()

    def test_link_checks_saved_invoice_rows_before_creating_money_operation(self):
        c=MagicMock(target='sandbox')
        c.query.side_effect=lambda sql,*args: ([{'TrnID':'P1','ProductDesc':'Nhẫn','Notes':'SP:A1 |'}] if 'FROM TRN_DATCOC_DT' in sql else [])
        with patch.object(B,'kiem_truoc_khi_gan',return_value=({'TienCoc':1000},'')),patch.object(B.Operation.objects,'create') as operation:
            ids,error=B.lien_ket(c,'INV1',['P1'],'C1',SimpleNamespace(pk=None))
        self.assertEqual(ids,[]);self.assertIn('A1 chưa có',error);operation.assert_not_called();c.call.assert_not_called()
