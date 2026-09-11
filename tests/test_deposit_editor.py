from decimal import Decimal as Q
from unittest.mock import MagicMock, patch
from django.core import signing
from django.test import SimpleTestCase
from apps.pos import deposit_editor as E, deposits as D
from apps.pmv import money as M


class EditorCalculationTests(SimpleTestCase):
    def setUp(self):
        self.rates={'18K':{'unit':'chỉ','rate':'8000000','native_per_display':'100','round_unit':'1000'},
                    'BK':{'unit':'g','rate':'1500000','native_per_display':'1','round_unit':'1000'}}

    def row(self,**kw):
        return dict(GoldCode='18K',GoldWeight=Q('1.25'),DiamondWeight=Q('.25'),TotalWeight=Q('1.5'),SL=2,TaskPrice=Q('100000'),**kw)

    def test_multiple_gold_types_quantity_stones_and_deleted_rows(self):
        rows=[self.row(),{**self.row(),'GoldCode':'BK','GoldWeight':Q('2'),'SL':1,'TaskPrice':Q(0)},self.row(DELETE=True)]
        result=E.calculate(rows,self.rates)
        self.assertEqual(result['total'],Q('23200000'))
        self.assertEqual([g['weight'] for g in result['groups']],[Q('2.5'),Q('2')])
        self.assertEqual(result['tasks'],Q('200000'))
        self.assertEqual(len(result['lines']),2)

    def test_half_thousand_rounding_matches_common_money_formula(self):
        row={**self.row(),'GoldWeight':Q('0.0000625'),'SL':1,'TaskPrice':Q('500')}
        result=E.calculate([row],self.rates)
        self.assertEqual(result['gold_total'],Q('1000'))
        self.assertEqual(result['total'],Q('1000'))
        self.assertEqual(M.round_vnd(Q('1500'),quantum='1000'),Q('2000'))

    def test_unknown_price_does_not_become_zero(self):
        self.rates['18K']['rate']=None
        result=E.calculate([self.row()],self.rates)
        self.assertIsNone(result['total']); self.assertEqual(result['missing'],['18K'])

    def test_native_conversion_preserves_originals_and_grams(self):
        original=self.row(); converted=E.native_lines([original,{**original,'GoldCode':'BK'}],self.rates)
        self.assertEqual(converted[0]['GoldWeight'],Q('125'))
        self.assertEqual(converted[0]['TotalWeight'],Q('150'))
        self.assertEqual(converted[1]['GoldWeight'],Q('1.25'))
        self.assertEqual(original['GoldWeight'],Q('1.25'))
        with self.assertRaisesRegex(ValueError,'vượt giới hạn'):
            E.native_lines([{**original,'GoldWeight':Q('100000000000')}],self.rates)

    @patch.object(E.S,'gia_mysql',return_value={'18K':{'SellRate':Q('8100')}})
    def test_signed_prices_keep_prior_quote_and_mobile_units(self,_):
        c=MagicMock(target='sandbox'); c.sys_param.return_value='3@499@0'
        c.query.side_effect=lambda sql,*args: [{'GoldCode':'18K','WeightUnit':'L','PriceUnit':'L'}] if 'I_GOLD' in sql else [{'factor':1}] if 'fun_GetHS' in sql else [{'GoldCcy':'18K','SellRate':Q('8000')}]
        payload,token=E.price_context(c)
        self.assertEqual(signing.loads(token,salt='dc-pricing'),payload)
        self.assertEqual(payload['rates']['18K']['rate'],'8100000')
        self.assertEqual(payload['rates']['18K']['native_per_display'],'100')
        saved={**payload,'rates':{'18K':{**payload['rates']['18K'],'rate':'7000000'}}}
        mobile,_=E.price_context(c,mobile=True,stored=saved)
        self.assertEqual(mobile['rates']['18K']['rate'],'7000000')
        self.assertEqual(mobile['rates']['18K']['native_per_display'],'1')

    def test_deposit_sum_ignores_posted_total_and_requires_partitions(self):
        data={'CustID':'KH1','EmpID':'E1','TienCoc':'1','CashDeposit':'1000000','BankDeposit':'250000', 'editor_version':'2'}
        f=D.DepositForm(data); f.fields['EmpID'].choices=[('E1','E1')]
        self.assertTrue(f.is_valid(),f.errors); self.assertEqual(f.cleaned_data['TienCoc'],Q('1250000'))
        f=D.DepositForm({**data,'BankDeposit':''}); f.fields['EmpID'].choices=[('E1','E1')]
        self.assertFalse(f.is_valid()); self.assertIn('BankDeposit',f.errors)

    def test_display_row_derives_total_and_keeps_order_product_code(self):
        data={'Mode':'new','ProductCode':'MAU12','ProductDesc':'Nhẫn đặt','GoldCode':'18K','SL':'1','TotalWeight':'0',
              'GoldWeight':'1.25','DiamondWeight':'.25','TaskPrice':'0','DisplayUnits':'True','Notes':'Khắc tên'}
        f=D.OrderLineForm(data); f.fields['GoldCode'].choices=[('18K','18K')]
        self.assertTrue(f.is_valid(),f.errors); self.assertEqual(f.cleaned_data['TotalWeight'],Q('1.5'))
        decoded=D.O.item_info(f.cleaned_data)
        self.assertEqual((decoded['Mode'],decoded['ProductCode'],decoded['Notes']),('new','MAU12','Khắc tên'))
