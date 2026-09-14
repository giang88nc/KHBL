"""Kiểm quỹ cọc tiền mặt → CK nhiều lần trên sandbox, trả két về số ban đầu."""
from decimal import Decimal
from types import SimpleNamespace
import uuid
from django.core.management.base import BaseCommand
from apps.pmv.client import PmvClient
from apps.pos import deposits as D, deposit_money as F, deposit_bank as R
from apps.pos.deposit_models import DepositOrderState, DepositMoneyOperation, DepositEvent
from apps.pos.deposit_operations import base_state


class Command(BaseCommand):
    def handle(self, *args, **options):
        c = PmvClient('sandbox', tag='smoke-dc-bank')
        assert c.target == 'sandbox'
        u = c.query("SELECT UserID,EmpID,ShopID FROM SYS_USERS WITH (NOLOCK) WHERE UserName='admin'")[0]
        till = c.query("SELECT TillID FROM T_TILL WITH (NOLOCK) WHERE TillCode='ad'")[0]['TillID']
        user = SimpleNamespace(user_id=u['UserID'], emp_id=u['EmpID'], shop_id=u['ShopID'], till_id=till)
        cust = c.query('SELECT TOP 1 CustID FROM I_CUSTOMER WITH (NOLOCK) ORDER BY CustID')[0]['CustID']
        def balance():
            return c.query("SELECT InFlow,OutFlow,TillBal FROM T_TILL_BAL WITH (NOLOCK) WHERE TillID=? AND GoldCcy='VND'", (till,))[0]
        before = balance()
        pk = None
        try:
            pk = D.save(c, dict(CustID=cust, EmpID=user.emp_id, TienCoc=Decimal('1000000'), Description='KHBL TEST đối soát CK - tự dọn'),
                        [dict(Mode='new', ProductCode='Khách đặt', ProductDesc='TEST CK', GoldCode='18K', SL=1,
                              TotalWeight=0, DiamondWeight=0, GoldWeight=0, TaskPrice=0, Size='', Notes='')], user)
            h = D.header(c, pk)
            base_state(c, h, D.lines(c, pk)).save()
            receipt = DepositMoneyOperation.objects.create(target='sandbox', trn_id=pk, kind='receive',
                amount=Decimal('1000000'), cash=Decimal('1000000'), bank=0, username='smoke-dc-bank',
                user_id=user.user_id, till_id=till, token=uuid.uuid4().hex, active_key='sandbox:'+pk,
                evidence={'stamp': F.money_stamp(c, h), 'staff_confirmed': True, 'reconcile_later': True, 'money_policy': R.POLICY})
            receipt = F.execute(receipt)
            assert receipt.status == 'done', receipt.message
            assert DepositOrderState.objects.get(target='sandbox', trn_id=pk).payment_plan['reconcile_enabled']
            assert balance()['TillBal'] - before['TillBal'] == Decimal('1000000')
            for bank in [Decimal('300000'), Decimal('600000'), Decimal('1000000'), Decimal('1000000')]:
                current = F.financial(c, pk)
                op = SimpleNamespace(trn_id=pk, cash=Decimal('1000000')-bank, bank=bank, amount=Decimal('1000000'),
                                     user_id=user.user_id, till_id=till, evidence={'stamp': F.money_stamp(c, current['h'])})
                after = R.reallocate(c, op)
                assert after['valid'] and after['bank'] == bank and after['cash'] == op.cash and after['amount'] == op.amount
                assert balance()['TillBal'] - before['TillBal'] == op.cash
                self.stdout.write('PASS TM/CK: ' + str(op.cash) + '/' + str(bank))
        finally:
            if pk:
                h = D.header(c, pk)
                if h and h['Status'] == 'P':
                    c.call('T_TILL_TXN_Del', write=True, day_du=True, p_TrnRefID=pk, pType='TDC', pCongNoBanLe=0,
                           p_UserUpd=user.user_id, p_TrnDateTime_Upd=h['TrnDateTime_Upd'], p_Type='0')
                    h = D.header(c, pk)
                if h and h['Status'] == 'W':
                    c.call('TRN_DATCOC_Del', write=True, p_TrnID=pk, p_UserUpd=user.user_id, p_TrnDateTime_Upd=h['TrnDateTime_Upd'])
                assert not D.header(c, pk)
                for model in (DepositMoneyOperation, DepositOrderState, DepositEvent):
                    model.objects.filter(target='sandbox', trn_id=pk).delete()
            assert balance() == before, (before, balance())
            self.stdout.write('PASS: xóa phiếu thử, InFlow/OutFlow/TillBal trở về đúng trước kiểm thử.')
