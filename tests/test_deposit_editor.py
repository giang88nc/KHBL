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
        data={'PromiseDate':'2026-09-25','CustID':'KH1','EmpID':'E1','TienCoc':'1','CashDeposit':'1000000','BankDeposit':'250000', 'editor_version':'2'}
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
        self.assertEqual((decoded['Mode'],decoded['ProductCode'],decoded['Notes']),('new','Khách đặt','Khắc tên'))

    def test_compact_stock_uses_exact_inventory_values_not_posted_fields(self):
        from django.http import QueryDict
        post=QueryDict('',mutable=True)
        post.update({'items-TOTAL_FORMS':'2','money_format':'vi','items-0-Mode':'stock','items-0-ProductCode':'SP01',
                     'items-0-ProductDesc':'Tên giả','items-0-GoldWeight':'999','items-0-TaskPrice':'1',
                     'items-1-Mode':'new','items-1-ProductCode':'Tự đổi mã','items-1-Notes':'Không lưu ghi chú mới'})
        c=MagicMock(); c.query.return_value=[dict(ProductCode='SP01',ProductDesc='Nhẫn kho',GoldCode='18K',
            TotalWeight=Q('150'),DiamondWeight=Q('25'),RingSize='12',TaskPrice=Q('300'),WeightUnit='L')]
        data,errors=E.compact_data(c,post,[])
        self.assertFalse(errors)
        self.assertEqual(c.query.call_args.args[1],('SP01',))
        self.assertEqual(data['items-0-GoldWeight'],'1.25000000')
        self.assertEqual(data['items-0-ProductDesc'],'Nhẫn kho')
        self.assertEqual(data['items-0-TaskPrice'],'300000,000')
        self.assertEqual(data['items-1-ProductCode'],'Khách đặt')
        self.assertEqual(data['items-1-Notes'],'')
        c.query.return_value=[]
        self.assertIn(0,E.compact_data(c,post,[])[1])

    def test_vietnamese_money_parsing_and_display_are_exact(self):
        data={'PromiseDate':'2026-09-25','CustID':'KH1','EmpID':'E1','editor_version':'2','money_format':'vi','TienCoc':'0',
              'CashDeposit':'1.234.567,89','BankDeposit':'2.000.000'}
        f=D.DepositForm(data); f.fields['EmpID'].choices=[('E1','E1')]
        self.assertTrue(f.is_valid(),f.errors)
        self.assertEqual(f.cleaned_data['TienCoc'],Q('3234567.89'))
        self.assertIn('1.234.567,89',str(f['CashDeposit']))

    def test_json_notes_same_day_round_trip_and_legacy_conversion(self):
        import datetime as dt
        import json
        entries=[{'date':'2026-09-10','text':'Khách chốt cọc'},{'date':'2026-09-10','text':'Khách gọi lại <script> & "hẹn"'}]
        packed=D.O.pack_description(dt.date(2026,9,20),entries,Q('1250000'))
        self.assertEqual(json.loads(packed)['notes'],entries)
        self.assertEqual(D.O.note_entries(packed),entries)
        parsed=D.O.parse_description(packed)
        self.assertEqual(parsed['promise_date'],dt.date(2026,9,20))
        self.assertEqual(parsed['estimate'],Q('1250000'))
        self.assertIn('10/09/2026: Khách chốt cọc',parsed['note'])
        self.assertEqual(D.O.note_entries('HEN:2026-09-20 | Ghi chú cũ | 1000'),[{'date':None,'text':'Ghi chú cũ'}])
        self.assertEqual(D.O.note_entries('{invalid'),[{'date':None,'text':'{invalid'}])
        self.assertEqual(D.O.parse_description('{"2026-09-10":"Khách gọi"}')['note'],'10/09/2026: Khách gọi')

    def test_note_form_pending_text_dates_and_utf16_capacity(self):
        import json
        data={'PromiseDate':'2026-09-25','CustID':'KH1','EmpID':'E1','TienCoc':'1000000','description_format':'json','NoteEntries':'[]','NoteText':'Khách gọi lại'}
        f=D.DepositForm(data);f.fields['EmpID'].choices=[('E1','E1')]
        self.assertTrue(f.is_valid(),f.errors)
        self.assertEqual(json.loads(f.cleaned_data['Description'])['notes'],[{'date':D.timezone.localdate().isoformat(),'text':'Khách gọi lại'}])
        for notes in [[{'date':'2026-02-31','text':'Sai ngày'}],[{'date':None,'text':'😀'*250}]]:
            f=D.DepositForm({**data,'NoteEntries':json.dumps(notes),'NoteText':''});f.fields['EmpID'].choices=[('E1','E1')]
            self.assertFalse(f.is_valid());self.assertIn('NoteEntries',f.errors)

    def test_empty_json_promise_does_not_restore_old_mobile_appointment(self):
        row=D.O.enrich({'TrnID':'TRC1','Status':'W','Description':'{"notes":[]}','UserID_Upd':'2026-09-10'})
        self.assertIsNone(row['promise_date'])


from django.test import TestCase, override_settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.contrib.auth import get_user_model
from django.urls import reverse
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from PIL import Image
from apps.pos import deposit_photos as P
from apps.pos.deposit_models import DepositOrderState


class DepositPhotoTests(TestCase):
    def test_four_slots_persist_private_read_and_remove(self):
        stream=BytesIO(); Image.new('RGB',(20,12),'purple').save(stream,'PNG')
        prepared=P.prepare({'photo_'+key:SimpleUploadedFile('test.png',stream.getvalue(),content_type='image/png') for key in P.SLOTS})
        self.assertEqual(len(prepared),4)
        state=DepositOrderState.objects.create(target='sandbox',trn_id='PHOTO1')
        user=get_user_model().objects.create_superuser('photo-admin',password='test')
        self.client.force_login(user)
        with TemporaryDirectory() as folder, override_settings(BASE_DIR=Path(folder)):
            P.save(state,prepared,{})
            state.refresh_from_db(); self.assertEqual(len(state.photos),4)
            self.assertEqual(state._meta.db_table, 'gold_bill')
            self.assertEqual(bytes(state.dc_photo_sample1), prepared['sample1'])
            url=reverse('pos:dat_coc_photo',args=['PHOTO1','sample1'])
            with patch('apps.pmv.client.PmvClient',return_value=MagicMock(target='sandbox')):
                response=self.client.get(url)
                self.assertEqual(response.status_code,200)
                self.assertEqual(response['Content-Type'],'image/jpeg')
                self.assertEqual(response['Cache-Control'],'private, no-store')
                self.assertTrue(b''.join(response.streaming_content).startswith(b'\xff\xd8'))
                response.close()
            with patch('apps.pmv.client.PmvClient',return_value=MagicMock(target='kk')):
                self.assertEqual(self.client.get(url).status_code,404)
            # Ảnh thành phẩm vẫn sửa được khi cọc đã thu, không sửa chứng từ PMV.
            c=MagicMock(target='sandbox')
            edit_url=reverse('pos:dat_coc_photos_edit',args=['PHOTO1'])
            with patch('apps.pmv.client.PmvClient',return_value=c),patch.object(D,'header',return_value={'TrnID':'PHOTO1','Status':'P'}):
                opened=self.client.get(edit_url)
                updated=self.client.post(edit_url,{'token':opened.context['token'],'remove_photo_finished2':'1'})
                self.assertIn('depositSaved',updated.headers.get('HX-Trigger',''))
                c.call.assert_not_called()
            state.refresh_from_db(); self.assertNotIn('finished2',state.photos)
            P.save(state,{}, {'remove_photo_sample1':'1'})
            state.refresh_from_db(); self.assertNotIn('sample1',state.photos)
            self.assertEqual(len(state.photos),2)
            viewer=get_user_model().objects.create_user('photo-no-access',password='test')
            self.client.force_login(viewer); self.assertEqual(self.client.get(url).status_code,403)

    def test_rejects_non_image_and_oversize_uploads(self):
        with self.assertRaisesRegex(ValueError,'hợp lệ'):
            P.prepare({'photo_sample1':SimpleUploadedFile('bad.jpg',b'not an image')})
        with self.assertRaisesRegex(ValueError,'10 MB'):
            P.prepare({'photo_finished1':SimpleUploadedFile('big.jpg',b'x'*(10*1024*1024+1))})
