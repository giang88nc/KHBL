from io import BytesIO
from types import SimpleNamespace
from unittest.mock import patch
from django.test import SimpleTestCase,RequestFactory
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse
from PIL import Image
from apps.pos import customer_camera as C


class CustomerCameraCardTests(SimpleTestCase):
    def request(self, data=b'',query='',content_type='image/jpeg',user=None):
        r=RequestFactory().post('/banle/khach-hang/chup-hinh/tach-cccd/'+query,data=data,content_type=content_type)
        r.user=user or SimpleNamespace(is_authenticated=True,is_superuser=True)
        return r

    def test_rejects_multipart_invalid_image_and_oversize(self):
        self.assertEqual(C.split_card(self.request(b'abc',content_type='application/octet-stream')).status_code,415)
        self.assertEqual(C.split_card(self.request(b'abc')).status_code,422)
        self.assertEqual(C.split_card(self.request(b'x'*(6*1024*1024+1))).status_code,413)

    def test_checks_customer_permission_before_reading_image(self):
        r=self.request(user=SimpleNamespace(is_authenticated=False))
        with patch.object(r,'read') as read:
            with self.assertRaises(PermissionDenied):C.split_card(r)
            read.assert_not_called()

    def test_canonical_algorithm_rotation_and_adjustment_are_returned_without_storage(self):
        source=C.AC.anh_thu_nghiem(goc=12)
        with patch('django.core.cache.cache.set',side_effect=AssertionError('No image cache')):
            result=C.split_card(self.request(source,'?mode=apply&goc=90'))
            self.assertEqual(result.status_code,200)
            self.assertEqual(Image.open(BytesIO(result.content)).size,(738,1170))
            self.assertIn('no-store',result['Cache-Control'])
            adjusted=C.split_card(self.request(source,'?mode=apply&goc=90&cat_r=0.2'))
            self.assertEqual(Image.open(BytesIO(adjusted.content)).size,(738,1170))
            self.assertNotEqual(result.content,adjusted.content)

    def test_uniform_image_reports_no_card(self):
        out=BytesIO();Image.new('RGB',(600,400),'gray').save(out,format='JPEG')
        result=C.split_card(self.request(out.getvalue(),'?mode=apply'))
        self.assertEqual(result.status_code,422)

    def test_preview_reuses_skill_popup_without_cache_source_id(self):
        out=BytesIO();Image.new('RGB',(600,400),'white').save(out,format='JPEG')
        with patch.object(C.AC,'cat_cccd',return_value=out.getvalue()),patch.object(C,'render',side_effect=lambda req,tpl,ctx: HttpResponse(tpl)) as render:
            result=C.split_card(self.request(out.getvalue()))
            self.assertEqual(result.content,b'pos/_customer_camera_card.html')
            self.assertNotIn('nguon',render.call_args.args[2])
            self.assertIn('no-store',result['Cache-Control'])
