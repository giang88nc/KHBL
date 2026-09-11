"""Tạo/sửa/xóa phiếu thử qua gateway; chỉ PMV_SANDBOX, không thu tiền."""
from decimal import Decimal
from types import SimpleNamespace
from django.core.management.base import BaseCommand
from apps.pmv.client import PmvClient
from apps.pos import deposits as D, deposit_editor as E


class Command(BaseCommand):
    def handle(self,*args,**options):
        c=PmvClient('sandbox',tag='smoke-datcoc-editor')
        assert c.target=='sandbox'
        u=c.query("SELECT UserID,EmpID,ShopID FROM SYS_USERS WITH (NOLOCK) WHERE UserName='admin'")[0]
        user=SimpleNamespace(user_id=u['UserID'],emp_id=u['EmpID'],shop_id=u['ShopID'])
        cust=c.query('SELECT TOP 1 CustID FROM I_CUSTOMER WITH (NOLOCK) ORDER BY CustID')[0]['CustID']
        pricing,_=E.price_context(c)
        row={'Mode':'new','ProductDesc':'KHBL TEST popup - tự dọn','GoldCode':'18K','SL':2,'GoldWeight':Decimal('1.25'),
             'DiamondWeight':Decimal('.25'),'TotalWeight':Decimal('1.5'),'TaskPrice':Decimal('100000'),
             'Size':'12','Notes':'MA_DAT:MAU-TEST | Ghi chú thử'}
        data={'CustID':cust,'EmpID':user.emp_id,'TienCoc':Decimal('1250000'),'Description':'KHBL TEST popup - chưa thu tiền - tự dọn'}
        pk=None
        try:
            pk=D.save(c,data,E.native_lines([row],pricing['rates']),user)
            scale=Decimal(pricing['rates']['18K']['native_per_display'])
            first=D.lines(c,pk)[0]
            assert first['GoldWeight']/scale==Decimal('1.25'),first
            row.update(GoldWeight=Decimal('2.34567890'),TotalWeight=Decimal('2.59567890'),Size='13')
            D.save(c,data,E.native_lines([row],pricing['rates']),user,D.header(c,pk))
            after=D.lines(c,pk)[0]
            assert after['GoldWeight']/scale==row['GoldWeight'],after
            assert after['TotalWeight']/scale==row['TotalWeight'],after
            assert D.O.item_info(after)['ProductCode']=='MAU-TEST'
            h=D.header(c,pk)
            assert h['Status']=='W' and not h['CashPay'] and not h['CardPay']
            assert not c.query('SELECT TillTxnID FROM T_TILL_TXN WITH (NOLOCK) WHERE TrnRefID=?',(pk,))
            self.stdout.write(self.style.SUCCESS('PASS: tạo/sửa phiếu qua proc; TL chỉ ↔ PMV đúng 8 số lẻ; mã mẫu/ghi chú giữ nguyên; không phát sinh quỹ.'))
        finally:
            if pk:
                h=D.header(c,pk)
                if h and h['Status']=='W':
                    c.call('TRN_DATCOC_Del',write=True,p_TrnID=pk,p_UserUpd=user.user_id,p_TrnDateTime_Upd=h['TrnDateTime_Upd'])
                    assert not D.header(c,pk)
                    self.stdout.write('Đã xóa phiếu thử '+pk)
