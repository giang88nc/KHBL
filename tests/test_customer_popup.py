import base64
import io
import json
from unittest.mock import patch, MagicMock
from django.contrib.auth import get_user_model
from django.test import TestCase, RequestFactory
from django.urls import get_script_prefix
from PIL import Image
from apps.pos import customer_popup as U, customer_bridge as B, anh_cccd as AC
from apps.pos.customer_bridge_models import CustomerBridgeReceipt


class CustomerPopupTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('popup-test', is_superuser=True)
        self.client = MagicMock(target='sandbox')
        self.client.query.return_value = [{'ShopID':'SHOP_TEST'}]

    def request(self, path='them/', method='GET', data=None, content_type=None):
        path = '/banle/khach-hang/' + path
        factory = RequestFactory()
        if method == 'POST':
            req = factory.post(path, data or {}, **({'content_type':content_type} if content_type else {}))
            raw = req.body; ct = req.content_type
            # Multipart needs the boundary, not just the parsed media type.
            ct = req.META['CONTENT_TYPE']
        else:
            raw = b''; ct = ''
        result = U.dispatch({'path':path, 'method':method, 'body':base64.b64encode(raw).decode(),
                            'content_type':ct}, self.user, self.client)
        return result, base64.b64decode(result['body'])

    def test_shared_form_and_assets_and_allowlist(self):
        prefix = get_script_prefix()
        result, body = self.request()
        html = body.decode()
        self.assertEqual(result['status'],200)
        self.assertIn(U.PREFIX + 'banle/khach-hang/luu/',html)
        self.assertNotIn('Dạy bộ đọc QR',html)
        self.assertIn('kh-customer-version',html)
        self.assertEqual(get_script_prefix(),prefix)
        positions = [html.index('name="' + name + '"') for name in
            ('CustName','Gender','BirthDate','CMND','NgayCap','NoiCap','Phone','GhiChu2','GhiChu3','Address','Email','Notes','CustType','anh_dai_dien','anh_truoc','anh_sau')]
        self.assertEqual(positions,sorted(positions))
        for asset in U.ASSETS:
            result = U.dispatch({'asset':asset},self.user,self.client)
            self.assertEqual(base64.b64decode(result['body']),(U.settings.BASE_DIR/'static'/asset).read_bytes())
        for payload in ({'asset':'../../.env'}, {'path':'/banle/ban-hang/'},
                        {'path':'/banle/khach-hang/luu/','method':'GET'},
                        {'path':'/banle/khach-hang/CU_TEST/xoa/thuc-hien/','method':'POST'}):
            with self.assertRaises(PermissionError):U.dispatch(payload,self.user,self.client)

    def test_view_only_cannot_open_or_post_form(self):
        with patch.object(U.C,'duoc_sua',return_value=False):
            with self.assertRaises(PermissionError):self.request()
            with self.assertRaises(PermissionError):self.request('luu/','POST')

    @patch.object(B.C,'upsert')
    def test_validation_does_not_write(self, upsert):
        result, body = self.request('luu/','POST',{'CustName':'Khách thử','Gender':'1',
            'Phone':'123','save_token':'popup_'+('v'*24),'version':''})
        self.assertEqual(result['status'],200)
        self.assertIn('10',body.decode()); upsert.assert_not_called()
        self.assertEqual(CustomerBridgeReceipt.objects.count(),0)

    @patch.object(B.C,'duplicate_errors',return_value=[])
    @patch.object(B.C,'upsert')
    def test_upload_complete_and_replay(self, upsert, duplicate):
        upsert.return_value={'complete':True,'cust_id':'CU_TEST','cust_code':'KH_TEST','name':'Khách thử','created':True,'warnings':[]}
        def data():
            image=io.BytesIO();Image.new('RGB',(32,24),'gold').save(image,'PNG');image.seek(0);image.name='portrait.png'
            return {'CustName':'Khách thử','Gender':'1','save_token':'popup_'+('i'*24),
                    'version':'','anh_dai_dien':image}
        first,_ = self.request('luu/','POST',data())
        second,_ = self.request('luu/','POST',data())
        self.assertEqual(first['status'],204);self.assertEqual(second['status'],204)
        self.assertEqual(upsert.call_count,1)
        self.assertTrue(upsert.call_args.args[1])
        self.assertIs(upsert.call_args.kwargs['client'],self.client)
        self.assertEqual(json.loads(first['headers']['HX-Trigger'])['khachSaved']['custId'],'CU_TEST')

    @patch.object(B.C,'duplicate_errors',return_value=[])
    @patch.object(B.C,'upsert')
    def test_partial_keeps_id_and_uncertain_never_reinserts(self,upsert,duplicate):
        form={'CustName':'Khách thử','Gender':'1','save_token':'popup_'+('p'*24),'version':''}
        upsert.return_value={'complete':False,'partial':True,'cust_id':'CU_PART','cust_code':'KH_PART','warnings':[], 'errors':['Ảnh chưa xong']}
        with patch.object(B.C,'_current',return_value={'CustID':'CU_PART'}):
            result,body=self.request('luu/','POST',form)
        self.assertIn('value="CU_PART"',body.decode())
        self.assertIn('outerHTML:[name=save_token]',body.decode())
        upsert.side_effect=OSError('response lost');form['save_token']='popup_'+('u'*24)
        self.request('luu/','POST',form);_,body=self.request('luu/','POST',form)
        self.assertEqual(upsert.call_count,2)
        self.assertIn('Kiểm tra kết quả lần lưu',body.decode())

    def test_qr_uses_canonical_engine_and_crop_preview(self):
        with patch('apps.pos.cccd.normalize_cccd_group',return_value={'ok':True}) as qr:
            result,body=self.request('qr/phan-tich/','POST',json.dumps({'scans':['synthetic']}),'application/json')
            self.assertEqual(json.loads(body),{'ok':True});qr.assert_called_once_with(['synthetic'])
        image=io.BytesIO(AC.anh_thu_nghiem(goc=12));image.name='synthetic-card.jpg'
        result,body=self.request('anh/cat/','POST',{'anh_truoc':image,'mat':'truoc'})
        html=body.decode();self.assertEqual(result['status'],200)
        self.assertIn('✓ DÙNG thẻ đã tách',html)
        self.assertIn(U.PREFIX+'banle/khach-hang/anh/cat/luu/',html)
        import re
        token=re.search(r'name="nguon" value="([^"]+)"',html).group(1)
        result,body=self.request('anh/cat/luu/','POST',{'nguon':token,'mat':'truoc','goc':'90'})
        self.assertEqual(result['status'],200)
        self.assertEqual((json.loads(body)['w'],json.loads(body)['h']),(738,1170))

    def test_pawn_photo_frame_reuses_camera_and_crop(self):
        result=U.dispatch({'path':'/banle/khach-hang/them/','method':'GET','query':'pawn_photos=1'},self.user,self.client)
        html=base64.b64decode(result['body']).decode()
        self.assertEqual(result['status'],200)
        for name in ('anh_truoc','anh_sau','anh_sp1','anh_sp2'):
            self.assertIn('name="'+name+'"',html)
        self.assertEqual(html.count('id="kh-camera"'),1)
        self.assertEqual(html.count('data-chup'),4)
        self.assertIn(U.PREFIX+'banle/khach-hang/anh/cat/',html)
        self.assertNotIn('khbl-qr',html)

    def test_compact_photo_groups_share_canonical_controls(self):
        groups={'front':('anh_truoc',),'back':('anh_sau',),'products':('anh_sp1','anh_sp2','anh_qr')}
        for group,names in groups.items():
            result=U.dispatch({'path':'/banle/khach-hang/them/','method':'GET','query':'pawn_photos=1&group='+group},self.user,self.client)
            html=base64.b64decode(result['body']).decode()
            self.assertEqual(result['status'],200)
            for name in names:self.assertIn('name="'+name+'"',html)
            self.assertEqual(html.count('data-chup'),len(names))
            self.assertEqual(html.count('id="kh-camera"'),1)

    @patch.object(B.C,'upsert')
    def test_pawn_bank_qr_text_and_image_use_canonical_decoder_without_writes(self,upsert):
        from apps.pos import vietqr as QR
        from apps.pos.views_thau import _qr_png_b64
        raw=QR.payload('VCB','000000123456',ten='KHACH THU')
        for source in ({'text':raw},{'image':_qr_png_b64(raw)}):
            result=B.dispatch({'action':'pawn_qr','kind':'bank',**source},self.user,'sandbox')
            self.assertEqual(result['bank_account'],'000000123456')
            self.assertEqual(result['bank_holder'],'KHACH THU')
            self.assertTrue(result['qr_image'])
        missing=B.dispatch({'action':'pawn_qr','kind':'bank','text':QR.payload('VCB','000000123456')},self.user,'sandbox')
        self.assertEqual(missing['bank_holder'],'');self.assertTrue(missing['warning'])
        suggested=B.dispatch({'action':'pawn_qr','kind':'bank','text':QR.payload('VCB','000000654321'),'customer_name':'Trương Ngọc Giang'},self.user,'sandbox')
        self.assertEqual(suggested['bank_holder'],'TRUONG NGOC GIANG')
        self.assertEqual(suggested['bank_holder_source'],'customer');self.assertTrue(suggested['warning'])
        bad=raw[:-1]+('0' if raw[-1]!='0' else '1')
        with self.assertRaises(ValueError):B.dispatch({'action':'pawn_qr','kind':'bank','text':bad},self.user,'sandbox')
        card=raw[:-4].replace('QRIBFTTA','QRIBFTTC');card+=QR._crc16(card)
        with self.assertRaises(ValueError):B.dispatch({'action':'pawn_qr','kind':'bank','text':card},self.user,'sandbox')
        import segno
        png=io.BytesIO();segno.make_qr('CD_TEST_123').save(png,kind='png',scale=8,border=4)
        receipt=B.dispatch({'action':'pawn_qr','kind':'receipt','image':base64.b64encode(png.getvalue()).decode()},self.user,'sandbox')
        self.assertEqual(receipt,{'text':'CD_TEST_123'})
        upsert.assert_not_called()

    @patch('apps.pos.vietqr.active_banks')
    def test_pawn_receiving_bank_prefers_pawn_type(self,banks):
        banks.return_value=[{'id':1,'type':'retail'},{'id':2,'type':'pawn'}]
        self.assertEqual(B.dispatch({'action':'pawn_banks'},self.user,'sandbox')['default_id'],2)
        banks.return_value=[{'id':1,'type':'retail'}]
        self.assertIsNone(B.dispatch({'action':'pawn_banks'},self.user,'sandbox')['default_id'])

    @patch.object(B.C,'upsert')
    def test_pawn_images_only_prepare_bytes_never_write_customer(self,upsert):
        out=io.BytesIO();Image.new('RGB',(32,24),'gold').save(out,'PNG')
        encoded=base64.b64encode(out.getvalue()).decode()
        result=B.dispatch({'action':'pawn_images','files':{k:encoded for k in ('anh_truoc','anh_sau','anh_sp1','anh_sp2')}},self.user,'sandbox')
        self.assertEqual(len(result['images']),4)
        for value in result['images'].values():self.assertEqual(Image.open(io.BytesIO(base64.b64decode(value))).format,'JPEG')
        upsert.assert_not_called()
        with self.assertRaises(B.C.CustomerSaveError):B.dispatch({'action':'pawn_images','files':{'anh_sp1':base64.b64encode(b'bad').decode()}},self.user,'sandbox')
        with patch.object(B.C,'duoc_sua',return_value=False):
            with self.assertRaises(PermissionError):B.dispatch({'action':'pawn_images','files':{}},self.user,'sandbox')
