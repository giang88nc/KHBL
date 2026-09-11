"""Chỉ bản thử: kiểm luồng thu cọc, phân bổ TM/CK, hủy thu và hoàn két."""
from decimal import Decimal
from types import SimpleNamespace
from django.core.management.base import BaseCommand
from apps.pmv.client import PmvClient
from apps.pmv.models import PmvUser
from apps.pmv import gateway
from apps.pos import deposits as D


class Command(BaseCommand):
    def add_arguments(self,parser):
        parser.add_argument('--bank',type=int,default=250,choices=[0,250,1000])

    def handle(self,*args,**options):
        bank=Decimal(options['bank']); cash=Decimal(1000)-bank
        c=PmvClient('sandbox',tag='smoke-datcoc-money')
        assert c.target=='sandbox'
        u=c.query("SELECT UserID,EmpID,ShopID FROM SYS_USERS WITH (NOLOCK) WHERE UserName='admin'")[0]
        till=c.query("SELECT TillID FROM T_TILL WITH (NOLOCK) WHERE TillCode='ad'")[0]['TillID']
        user=SimpleNamespace(user_id=u['UserID'],emp_id=u['EmpID'],shop_id=u['ShopID'],till_id=till)
        assert user and user.till_id
        cust=c.query('SELECT TOP 1 CustID FROM I_CUSTOMER WITH (NOLOCK) ORDER BY CustID')[0]['CustID']
        gold=c.query('SELECT TOP 1 GoldCode FROM I_GOLD WITH (NOLOCK) ORDER BY GoldCode')[0]['GoldCode']
        before=c.query("SELECT SUM(TillBal) balance FROM T_TILL_BAL WITH (NOLOCK) WHERE TillID=? AND GoldCcy='VND'",(user.till_id,))[0]['balance'] or Decimal(0)
        # Chỉ cấp trong process bản thử; chốt gateway và audit vẫn áp dụng.
        original=gateway.PROC_WRITE_ALLOW
        gateway.PROC_WRITE_ALLOW=original|{'TRN_DATCOC_Complete','TRN_RT_BUYSELL_DatCoc_Ins'}
        pk=None; invoice=None
        try:
            data={'CustID':cust,'EmpID':user.emp_id,'TienCoc':Decimal('1000'),'Description':'KHBL TEST luồng cọc - tự dọn'}
            line={'ProductDesc':'TEST cọc','GoldCode':gold,'SL':1,'TotalWeight':Decimal(0),'DiamondWeight':Decimal(0),
                  'GoldWeight':Decimal(0),'TaskPrice':Decimal(0),'Size':'','Notes':''}
            pk=D.save(c,data,[line],user)
            _,sets=c.call('TRN_DATCOC_Complete',write=True,p_TrnID=pk,p_UserID=user.user_id)
            till=c.query("SELECT TillTxnID,Status,TrnTotalAmount FROM T_TILL_TXN WITH (NOLOCK) WHERE TrnRefID=?",(pk,))
            self.stdout.write('Sau Complete: '+str(till))
            assert len(till)==1 and till[0]['Status']=='U',till
            c.call('CARDPAY_Ins',write=True,day_du=True,p_TillID=user.till_id,p_TillTxnID=till[0]['TillTxnID'],p_TrnID=pk,
                   p_TypeTrade='TDC',p_ProductIDs=None,p_CardAmounts=None,p_Amount=str(bank),p_AmountTra=str(cash),p_List='<NewDataSet/>')
            detail=c.query('SELECT Amount,CrDr FROM T_TILL_TXN_DETAIL WITH (NOLOCK) WHERE TillTxnID=?',(till[0]['TillTxnID'],))
            self.stdout.write('Sau phân bổ: '+str(detail))
            assert detail and detail[0]['Amount']==cash and detail[0]['CrDr']=='+',detail
            c.call('T_TILL_TXN_Proc',write=True,p_TrnIDs=pk,p_TillID=user.till_id,p_UserID=user.user_id)
            h=D.header(c,pk)
            assert h['Status']=='P' and h['CashPay']==cash and h['CardPay']==bank,h
            after=c.query("SELECT SUM(TillBal) balance FROM T_TILL_BAL WITH (NOLOCK) WHERE TillID=? AND GoldCcy='VND'",(user.till_id,))[0]['balance'] or Decimal(0)
            assert after-before==cash,(before,after)
            from apps.pos import bill as B
            product=c.query("SELECT TOP 1 ProductCode FROM T_PRODUCT WHERE Status='I' AND SellTrnID IS NULL ORDER BY ProductCode DESC")[0]['ProductCode']
            _,sets=c.call('T_PRODUCT_GetByCodeForSell',raise_on_rc=False,p_ProductCode=product,p_TaskPrice=0,p_ShopID='',p_CheckRealSL=1,
                p_TillID=user.till_id,p_CustID=cust,p_ShopID_XRate='',p_RutGon='0')
            row=sets[0][0]; line=B.dong_ban_tu_quet(row)
            assert line[0]>1000
            inv=B.luu(trn_id='',ban=[line],doi=[],ngay=c.fmt_date(),gio=c.fmt_time(),cust_id=cust,emp_id=user.emp_id,
                till_id=user.till_id,shop_id=user.shop_id,user_id=user.user_id,coc=1000,c=c)
            invoice=inv['trn_id']
            self.stdout.write('Hóa đơn trước liên kết: '+str(c.query('SELECT TienCoc,PayAmount FROM TRN_RT_BUYSELL WHERE TrnID=?',(invoice,))))
            c.call('TRN_RT_BUYSELL_DatCoc_Ins',write=True,p_TrnID=invoice,p_IDCoc=pk)
            self.stdout.write('Liên kết: '+str(c.query('SELECT DatCocID FROM TRN_RT_BUYSELL_DatCoc WHERE TrnID=?',(invoice,))))
            B.chot(invoice,till_id=user.till_id,user_id=user.user_id,c=c)
            from apps.pos.deposit_money import financial
            assert financial(c,pk)['applied']
            self.stdout.write('PASS: cấn 1000 vào hóa đơn cùng khách; chốt hóa đơn → số dư cọc 0.')
            B.mo_lai(invoice,user_id=user.user_id,c=c)
            c.call('TRN_RT_BUYSELL_DatCoc_Ins',write=True,p_TrnID=invoice,p_IDCoc='')
            B.huy(invoice,user_id=user.user_id,c=c); invoice=None
            h=D.header(c,pk)
            c.call('T_TILL_TXN_Del',write=True,day_du=True,p_TrnRefID=pk,pType='TDC',pCongNoBanLe=0,
                   p_UserUpd=user.user_id,p_TrnDateTime_Upd=h['TrnDateTime_Upd'],p_Type='0')
            h=D.header(c,pk)
            assert h['Status']=='W' and h['CashPay']==0 and h['CardPay']==0,h
            restored=c.query("SELECT SUM(TillBal) balance FROM T_TILL_BAL WITH (NOLOCK) WHERE TillID=? AND GoldCcy='VND'",(user.till_id,))[0]['balance'] or Decimal(0)
            assert restored==before,(before,restored)
            assert not c.query('SELECT TillTxnID FROM T_TILL_TXN WITH (NOLOCK) WHERE TrnRefID=?',(pk,))
            self.stdout.write(self.style.SUCCESS(f'PASS: thu 1000 = TM {cash} + CK {bank}; két +{cash}; hủy thu về W, két khôi phục đúng.'))
        finally:
            if invoice:
                from apps.pos import bill as B
                existing=c.query('SELECT Status FROM TRN_RT_BUYSELL WHERE TrnID=?',(invoice,))
                if existing and existing[0]['Status']=='C': B.mo_lai(invoice,user_id=user.user_id,c=c)
                c.call('TRN_RT_BUYSELL_DatCoc_Ins',write=True,p_TrnID=invoice,p_IDCoc='')
                B.huy(invoice,user_id=user.user_id,c=c)
            if pk:
                h=D.header(c,pk)
                if h and h['Status']=='P':
                    c.call('T_TILL_TXN_Del',write=True,day_du=True,p_TrnRefID=pk,pType='TDC',pCongNoBanLe=0,
                           p_UserUpd=user.user_id,p_TrnDateTime_Upd=h['TrnDateTime_Upd'],p_Type='0')
                    h=D.header(c,pk)
                if h and h['Status']=='W':
                    c.call('TRN_DATCOC_Del',write=True,p_TrnID=pk,p_UserUpd=user.user_id,p_TrnDateTime_Upd=h['TrnDateTime_Upd'])
                    assert D.header(c,pk) is None
                    self.stdout.write('Đã dọn phiếu thử.')
            gateway.PROC_WRITE_ALLOW=original
