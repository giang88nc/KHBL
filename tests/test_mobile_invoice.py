import json
from decimal import Decimal
from unittest.mock import patch,MagicMock
from django.test import SimpleTestCase,TestCase,RequestFactory
from django.contrib.auth import get_user_model
from django.core import signing
from django.urls import reverse
from django.utils import timezone
from apps.pos import mobile_invoice as I
from apps.pos.models import MoneyFlow
from apps.pmv import gateway as G

def source(kind='gold_bill_retail'):
    row=dict(TrnID='T1',BillCode='B1',CustID='C1',EmpID='E1',Status='C',CashPay=Decimal(4321000),CardPay=Decimal(0))
    if kind=='gold_bill_retail':row.update(PayAmount=Decimal(4321000),Desc3='{"keep":1}',IsDel='0',TienKhachTraThuc=Decimal(0),TienTraLai=Decimal(0))
    else:row.update(TienCoc=Decimal(4321000),Description='{"notes":[],"promise":"2026-10-10","estimate":"14620000","keep":1}')
    return row

class InvoiceLogicTests(SimpleTestCase):
    def test_desktop_stage_uses_mobile_rules_and_applies_fresh_stamp(self):
        row=source();posted={'channel':'online','social':'Zalo','customer_hold':'1',
            'is_wedding':'1','wedding':'2026-10-10','tender':'4500000','note':'Khách hẹn lấy'}
        clean,preview=I.staged_retail(row,posted,bill_quantity=3)
        self.assertEqual(clean['channel'],'online')
        self.assertEqual(preview['TienTraLai'],Decimal(179000))
        meta=json.loads(preview['Desc3']);self.assertTrue(meta['wedding']);self.assertEqual(meta['wedding_quantity'],'3')
        with patch.object(I,'load_retail',return_value=row),patch.object(G,'pmv_invoice_update') as write:
            self.assertTrue(I.apply_staged_retail({'trn_id':'T1','bill_code':'B1','invoice_info':clean},target='sandbox'))
        write.assert_called_once_with('gold_bill_retail','T1','B1',I.stamp(row),clean,target='sandbox')
        self.assertFalse(I.apply_staged_retail({'trn_id':'T1'},target='sandbox'))

    def test_channel_wedding_and_hold_json(self):
        row=source()
        data=json.loads(I.changes('gold_bill_retail',row,{'channel':'online','social':'Zalo','customer_hold':'1','is_wedding':'1','wedding':'2026-10-10','tender':'4500000','note':'Ghi chú'},bill_quantity=3)['Desc3'])
        self.assertEqual(data['channel'],'online');self.assertTrue(data['customer_hold'])
        self.assertEqual(data['wedding_quantity'],'3');self.assertEqual(data['wedding_bill_total'],'4321000')
        self.assertEqual(data['keep'],1);self.assertEqual(data['NgayCuoi'],'2026-10-10')
        data=json.loads(I.changes('gold_bill_retail',row,{'channel':'store','social':'Zalo','customer_hold':'1','tender':'0'})['Desc3'])
        self.assertFalse(data['customer_hold']);self.assertIsNone(data['social'])
        with self.assertRaises(ValueError):I.changes('gold_bill_retail',row,{'channel':'online','social':'INVALID'})
    def test_received_exact_requires_tender_and_balanced_split(self):
        from types import SimpleNamespace
        for kind in I.SPECS:
            for tender,change,cash,bank,total,expected in [
                (0,0,100,0,100,False), (80,0,100,0,100,False),
                (100,0,100,0,100,True), (100,0,100,50,150,True),
                (120,20,100,0,100,True), (80,20,100,0,100,False),
                (100,0,100,0,150,True), (None,0,100,0,100,False),
                (0,0,0,100,100,False), ('',0,0,100,100,False),
                (100,None,100,0,100,False), ('NaN',0,100,0,100,False)]:
                with self.subTest(kind=kind,tender=tender,change=change,total=total):
                    row=SimpleNamespace(pk=1,source_system='KHBL',source_type=kind,source_id='T1',source_bill_code='B1')
                    source_row={'TrnID':'T1','BillCode':'B1','CashPay':cash,'CardPay':bank,I.SPECS[kind][1]:total}
                    data={'TienKhachTraThuc':tender,'TienTraLai':change}
                    if kind=='gold_bill_retail':source_row.update(data)
                    else:source_row['Description']=json.dumps(data)
                    with patch.object(G,'pmv_read',return_value=[source_row]):I.attach_change([row])
                    self.assertEqual(row.customer_received_exact,expected)
                    from django.template.loader import render_to_string
                    html=render_to_string('pos/_money_flow_mobile_in.html',{'rows':[row]})
                    has_change=row.customer_change is not None and row.customer_change>0
                    self.assertEqual('Đã nhận đủ' in html,expected and not has_change)
                    pending=not expected and not has_change
                    self.assertEqual('Chưa xác nhận' in html,pending)
                    if has_change:self.assertIn('Tiền trả khách:',html)
                    if pending:self.assertIn('color:#b45309',html)
    def test_list_change_reads_source_and_preserves_unknown(self):
        from types import SimpleNamespace
        rows=[SimpleNamespace(source_system='KHBL',source_type='gold_bill_retail',source_id='T1',source_bill_code='B1'),
              SimpleNamespace(source_system='KHBL',source_type='gold_bill_deposit',source_id='T2',source_bill_code='B2')]
        with patch.object(G,'pmv_read',side_effect=[
            [{'TrnID':'T1','BillCode':'B1','TienTraLai':Decimal(179000)}],
            [{'TrnID':'T2','BillCode':'B2','Description':'{"TienTraLai":"0"}'}]]):
            I.attach_change(rows)
        self.assertEqual(rows[0].customer_change,179000)
        self.assertEqual(rows[1].customer_change,0)
        with patch.object(G,'pmv_read',return_value=[]):I.attach_change(rows)
        self.assertIsNone(rows[0].customer_change)
    def test_retail_types_and_change(self):
        updates=I.changes('gold_bill_retail',source(),{'tender':'4500000','types':['Đơn cưới','Đơn online'],'wedding':'2026-10-10','note':'Ghi chú'})
        self.assertEqual(updates['TienTraLai'],179000)
        self.assertEqual(json.loads(updates['Desc3']),{'keep':1,'type':['Đơn cưới','Đơn online'],'NgayCuoi':'2026-10-10','note':'Ghi chú'})
        self.assertNotIn('CashPay',updates);self.assertNotIn('CardPay',updates)
    def test_deposit_merge_and_short_payment(self):
        updates=I.changes('gold_bill_deposit',source('gold_bill_deposit'),{'tender':'4000000'})
        self.assertEqual(set(updates),{'Description'})
        d=json.loads(updates['Description']);self.assertEqual(d['TienTraLai'],'0');self.assertEqual(d['notes'],[])
        self.assertEqual(d['promise'],'2026-10-10');self.assertEqual(d['estimate'],'14620000')
    def test_zero_cash_preserves_tender(self):
        row=source();row['CardPay']=row['PayAmount']
        updates=I.changes('gold_bill_retail',row,{'tender':'9999999','types':[]})
        self.assertEqual(set(updates),{'Desc3'})
    def test_invalid_amount_and_legacy_notes_rejected(self):
        for amount in ['NaN','Infinity','-1','1.5','text']:
            with self.assertRaises(ValueError):I.changes('gold_bill_retail',source(),{'tender':amount})
        row=source('gold_bill_deposit');row['Description']='ghi chú cũ'
        with self.assertRaises(ValueError):I.changes('gold_bill_deposit',row,{'tender':'4500000'})
    def test_gateway_narrow_write_and_concurrency(self):
        row=source();posted={'tender':'4500000','types':[]};cols=I.columns('gold_bill_retail')
        for stale in (False,True):
            cn=MagicMock();cur=cn.cursor.return_value;cur.rowcount=1
            cur.fetchall.return_value=[tuple(row[c] for c in cols)]
            wanted={**row,**I.changes('gold_bill_retail',row,posted)}
            cur.fetchone.side_effect=[None,tuple(wanted[c] for c in cols)]
            with patch.object(G,'_connect_dich',return_value=cn),patch('apps.pmv.models.PmvState.get',return_value='0'):
                if stale:
                    with self.assertRaises(ValueError):G.pmv_invoice_update('gold_bill_retail','T1','B1','stale',posted,target='sandbox')
                    cn.commit.assert_not_called();cn.rollback.assert_called_once()
                else:
                    G.pmv_invoice_update('gold_bill_retail','T1','B1',I.stamp(row),posted,target='sandbox')
                    sql=[c.args[0] for c in cur.execute.call_args_list if c.args[0].startswith('UPDATE')]
                    self.assertEqual(sql,['UPDATE TRN_RT_BUYSELL SET TienKhachTraThuc=?,TienTraLai=?,Desc3=? WHERE TrnID=? AND BillCode=?'])
                    cn.commit.assert_called_once()

class InvoiceViewTests(TestCase):
    def setUp(self):
        self.user=get_user_model().objects.create_superuser('invoice-admin',password='test')
        self.flow=MoneyFlow.objects.create(direction='IN',source_system='KHBL',source_type='gold_bill_retail',source_id='T1',source_bill_code='B1',service='RETAIL',business_date=timezone.localdate())
    def test_get_read_only_and_post_bound_to_user_and_flow(self):
        row=source();client=MagicMock();client.query.return_value=[]
        with patch.object(I,'load',return_value=row),patch.object(G,'pmv_read',return_value=[]),patch.object(G,'pmv_invoice_update') as write:
            req=RequestFactory().get('/');req.user=self.user
            response=I.popup(req,self.flow.pk)
            self.assertContains(response,'Thông tin phiếu & tiền khách')
            self.assertContains(response,'id="mobile-invoice-form"')
            write.assert_not_called()
            token=signing.dumps({'flow':self.flow.pk,'user':self.user.pk,'stamp':I.stamp(row)},salt='mobile-invoice')
            req=RequestFactory().post('/',{'version':token,'tender':'4500000'});req.user=self.user
            response=I.popup(req,self.flow.pk)
            self.assertContains(response,'Đã lưu xác nhận tiền')
            self.assertContains(response,'closeKhblModal()')
            self.assertContains(response,'money-flow-reconciled')
            write.assert_called_once()
            write.reset_mock();req.POST=req.POST.copy();req.POST['version']='forged'
            response=I.popup(req,self.flow.pk)
            self.assertContains(response,'hết hạn');write.assert_not_called()
            self.assertNotContains(response,"CustomEvent('money-flow-reconciled')")

    def test_retail_popup_stages_same_form_without_early_pmv_write(self):
        self.client.force_login(self.user)
        session=self.client.session
        session['phieu']={'trn_id':'T1','bill_code':'B1','status':'W',
            'cust':{'id':'C1','name':'Khách thử'},'ban':[{'row':{'ProductCode':'P1'},'tien':'4321000'}],
            'doi':[],'emp':'E1'}
        session.save();row=source()
        url=reverse('pos:ban_phan_loai')
        with patch.object(I,'load_retail',return_value=row),patch.object(I,'retail_quantity',return_value=3),\
             patch.object(G,'pmv_read',return_value=[]),patch.object(G,'pmv_invoice_update') as write:
            response=self.client.get(url)
            self.assertContains(response,'Phân loại HĐ &amp; tiền khách')
            self.assertContains(response,'LƯU THÔNG TIN PHIẾU')
            self.assertContains(response,f'hx-post="{url}"')
            token=signing.dumps({'trn':'T1','bill':'B1','user':self.user.pk,'stamp':I.stamp(row)},salt='retail-invoice')
            response=self.client.post(url,{'version':token,'channel':'online','social':'Zalo',
                'is_wedding':'1','wedding':'2026-10-10','tender':'4500000','note':'Khách hẹn lấy'})
        self.assertContains(response,'sẽ ghi vào PMV khi THANH TOÁN')
        self.assertEqual(self.client.session['phieu']['invoice_info']['channel'],'online')
        write.assert_not_called()


class XacNhanTienMatGhiMoneyFlowTests(TestCase):
    """GĐ chốt 23/09/2026: bấm "Xác nhận tiền mặt" trên mobile là ghi LUÔN cus_cash / cus_change vào money_flow."""

    def test_ghi_tien_khach_dua_va_tien_thua(self):
        from decimal import Decimal
        from apps.pos.mobile_invoice import ghi_tien_khach
        from apps.pos.models import MoneyFlow
        f = MoneyFlow.objects.create(direction='IN', service='RETAIL', source_system='KHBL',
                                     source_type='gold_bill_retail', source_id='TRNX', source_bill_code='26-09-23-000123',
                                     business_date='2026-09-23', expected_amount=2899000, cash_amount=2899000)
        ghi_tien_khach(f, Decimal(2900000), Decimal(1000))
        f.refresh_from_db()
        self.assertEqual((f.cus_cash, f.cus_change), (Decimal(2900000), Decimal(1000)))
