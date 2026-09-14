from decimal import Decimal as Q
from unittest.mock import patch
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from apps.pos import deposits as D
from apps.pos.models import DepositSubmission
from tests.test_deposits import DepositViewTests


class RequiredFieldsTests(SimpleTestCase):
    def header(self, **changes):
        data = dict(CustID='C1', EmpID='E1', PromiseDate='2026-09-25', TienCoc='999999999',
                    CashDeposit='100000', BankDeposit='1', editor_version='2')
        data.update(changes)
        f = D.DepositForm(data); f.fields['EmpID'].choices = [('E1','Nhân viên hoạt động')]
        return f

    def test_required_header_and_active_employee(self):
        for key, value in [('CustID',''), ('PromiseDate',''), ('PromiseDate','2026-02-30'), ('EmpID',''), ('EmpID','INACTIVE')]:
            with self.subTest(key=key, value=value):
                f=self.header(**{key:value}); self.assertFalse(f.is_valid()); self.assertIn(key,f.errors)

    def test_strict_minimum_and_recomputed_total_even_without_editor_flag(self):
        for cash, bank in [('0','0'), ('99999','0'), ('100000','0'), ('50000','50000')]:
            f=self.header(CashDeposit=cash,BankDeposit=bank)
            self.assertFalse(f.is_valid()); self.assertIn('TienCoc',f.errors)
        for version in ['2','']:
            f=self.header(editor_version=version)
            self.assertTrue(f.is_valid(),f.errors); self.assertEqual(f.cleaned_data['TienCoc'],Q('100001'))
        f=self.header(CashDeposit='0',BankDeposit='100001')
        self.assertTrue(f.is_valid(),f.errors)

    def test_money_parts_reject_missing_negative_and_non_number(self):
        for key in ('CashDeposit','BankDeposit'):
            for value in ('','-1','abc'):
                f=self.header(**{key:value}); self.assertFalse(f.is_valid()); self.assertIn(key,f.errors)
        f=self.header(CashDeposit='100.000',BankDeposit='1',money_format='vi')
        self.assertTrue(f.is_valid(),f.errors); self.assertEqual(f.cleaned_data['TienCoc'],Q('100001'))

    def test_custom_item_requires_name_type_explicit_positive_gold_weight(self):
        data=dict(Mode='new',ProductDesc='Nhẫn đặt',GoldCode='18K',GoldWeight='0.8',TotalWeight='0.8',
                  DiamondWeight='0',SL='1',TaskPrice='0')
        for key,value in [('ProductDesc','  '),('GoldCode',''),('GoldWeight',''),('GoldWeight','0'),('GoldWeight','-1')]:
            f=D.OrderLineForm({**data,key:value}); f.fields['GoldCode'].choices=[('18K','18K')]
            self.assertFalse(f.is_valid()); self.assertIn(key,f.errors)
        f=D.OrderLineForm(data); f.fields['GoldCode'].choices=[('18K','18K')]
        self.assertTrue(f.is_valid(),f.errors)


class InvalidCrudTests(TestCase):
    def setUp(self):
        DepositViewTests.setUp(self)
        self.c.query.side_effect=lambda sql,*a: ([{'EmpID':'NV1','EmpName':'Đang hoạt động'}] if 'T_EMPLOYEE' in sql else
            [{'GoldCode':'18K','WeightUnit':'L','PriceUnit':'L'}] if 'FROM I_GOLD' in sql else [{'factor':1}] if 'fun_GetHS' in sql else [])

    def payload(self):
        opened=self.client.get(reverse('pos:dat_coc_add'))
        self.assertContains(opened,'required')
        self.assertTrue(any("WHERE Active='1'" in x.args[0] for x in self.c.query.call_args_list if 'T_EMPLOYEE' in x.args[0]))
        return {'token':opened.context['token'],'pricing_token':opened.context['pricing_token'],'editor_version':'2','row_version':'3',
            'CustID':'KH001','EmpID':'NV1','PromiseDate':'2026-09-25','CashDeposit':'200000','BankDeposit':'0','TienCoc':'200000',
            'items-TOTAL_FORMS':'1','items-INITIAL_FORMS':'0','items-0-Mode':'new','items-0-ProductDesc':'Nhẫn đặt',
            'items-0-GoldCode':'18K','items-0-GoldWeight':'1','items-0-TotalWeight':'1','items-0-DiamondWeight':'0',
            'items-0-SL':'1','items-0-TaskPrice':'0'}

    def test_invalid_header_never_consumes_token_or_writes_pmv(self):
        for key,value in [('PromiseDate',''),('CustID',''),('EmpID','INACTIVE'),('CashDeposit','100000')]:
            data=self.payload(); data[key]=value
            with patch.object(D,'save') as save:
                response=self.client.post(reverse('pos:dat_coc_add'),data)
                self.assertIn('TienCoc' if key=='CashDeposit' else key,response.context['form'].errors)
                save.assert_not_called()
        self.assertFalse(DepositSubmission.objects.exists()); self.c.call.assert_not_called()

    def test_invalid_stock_code_shows_row_error_without_any_write(self):
        data=self.payload(); data.update({'items-0-Mode':'stock','items-0-ProductCode':'SAI-MA'})
        with patch.object(D,'save') as save:
            response=self.client.post(reverse('pos:dat_coc_add'),data)
            self.assertIn('ProductCode',response.context['line_set'].forms[0].errors)
            save.assert_not_called()
        self.assertFalse(DepositSubmission.objects.exists()); self.c.call.assert_not_called()
