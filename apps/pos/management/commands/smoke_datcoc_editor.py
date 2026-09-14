"""Tạo/sửa/xóa phiếu thử qua gateway; chỉ PMV_SANDBOX, không thu tiền."""
from decimal import Decimal
import datetime as dt
import json
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
        notes=[{'date':'2026-09-10','text':'KHBL TEST khách chốt cọc'},{'date':'2026-09-11','text':'Khách gọi lại "hẹn" <A>'}]
        data={'CustID':cust,'EmpID':user.emp_id,'TienCoc':Decimal('1250000'),
              'Description':D.O.pack_description(dt.date(2026,9,20),notes,Decimal('12500000'))}
        pk=None
        try:
            pk=D.save(c,data,E.native_lines([row],pricing['rates']),user)
            assert json.loads(D.header(c,pk)['Description'])['notes']==notes
            scale=Decimal(pricing['rates']['18K']['native_per_display'])
            first=D.lines(c,pk)[0]
            assert first['GoldWeight']/scale==Decimal('1.25'),first
            row.update(GoldWeight=Decimal('2.34567890'),TotalWeight=Decimal('2.59567890'),Size='13')
            notes.append({'date':'2026-09-11','text':'Thợ nhận mẫu, giữ lịch sử ghi chú'})
            data['Description']=D.O.pack_description(dt.date(2026,9,21),notes,Decimal('12500000'))
            D.save(c,data,E.native_lines([row],pricing['rates']),user,D.header(c,pk))
            after=D.lines(c,pk)[0]
            assert after['GoldWeight']/scale==row['GoldWeight'],after
            assert after['TotalWeight']/scale==row['TotalWeight'],after
            assert D.O.item_info(after)['ProductCode']=='Khách đặt'
            raw=c.query('SELECT TrnID,ProductDesc,Notes FROM TRN_DATCOC_DT WITH (NOLOCK) WHERE TrnID=?',(pk,))[0]
            assert raw['TrnID']==pk
            assert json.loads(raw['ProductDesc'])=={'Khách đặt':row['ProductDesc']},raw
            assert raw['Notes']=='Ghi chú thử',raw
            h=D.header(c,pk)
            assert D.O.note_entries(h['Description'])==notes
            assert D.O.parse_description(h['Description'])['promise_date']==dt.date(2026,9,21)
            # Luồng sửa thường gửi nguyên chi tiết gốc, không lấy TL đã làm tròn hiển thị.
            original_lines=D.lines(c,pk)
            data['Description']=D.O.pack_description(dt.date(2026,9,25),notes,Decimal('12500000'))
            D.save(c,data,original_lines,user,h)
            check=D.lines(c,pk)
            assert [{k:v for k,v in i.items() if k!='TrnDTID'} for i in check]==[{k:v for k,v in i.items() if k!='TrnDTID'} for i in original_lines]
            h=D.header(c,pk)
            assert h['TienCoc']==data['TienCoc']
            assert h['Status']=='W' and not h['CashPay'] and not h['CardPay']
            assert not c.query('SELECT TillTxnID FROM T_TILL_TXN WITH (NOLOCK) WHERE TrnRefID=?',(pk,))
            self.stdout.write(self.style.SUCCESS('PASS: thêm ghi chú/đổi ngày hẹn qua proc; sửa thông tin giữ nguyên chi tiết 8 số lẻ và tiền cọc; không phát sinh quỹ.'))
        finally:
            if pk:
                h=D.header(c,pk)
                if h and h['Status']=='W':
                    c.call('TRN_DATCOC_Del',write=True,p_TrnID=pk,p_UserUpd=user.user_id,p_TrnDateTime_Upd=h['TrnDateTime_Upd'])
                    assert not D.header(c,pk)
                    self.stdout.write('Đã xóa phiếu thử '+pk)
