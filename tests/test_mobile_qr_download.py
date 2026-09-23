from django.test import TestCase,RequestFactory
from django.contrib.auth import get_user_model
from django.utils import timezone
from django.http import Http404
from apps.pos.models import MoneyFlow,MoneyFlowPayment
from apps.pos.money_flow_views import mobile_qr_image,mobile_qr_success

class QrDownloadTests(TestCase):
    def setUp(self):
        flow=MoneyFlow.objects.create(direction='IN',service='RETAIL',source_id='test',business_date=timezone.localdate(),expected_amount=100)
        self.qr=MoneyFlowPayment.objects.create(flow=flow,method='BANK',status='ready',amount=100,qr_payload='TEST ONLY')
        self.request=RequestFactory().get('/banle/mobile/qr/1/image/')
        self.request.user=get_user_model().objects.create_superuser('download-test',password='x')
        self.request.khbl_mobile_session=True
    def test_png_attachment_and_success_gate(self):
        response=mobile_qr_image(self.request,self.qr.pk)
        self.assertTrue(response.content.startswith(b'\x89PNG'))
        self.assertIn('attachment;',response['Content-Disposition'])
        with self.assertRaises(Http404):mobile_qr_success(self.request,self.qr.pk)
        self.qr.status='success';self.qr.save()
        response=mobile_qr_success(self.request,self.qr.pk)
        self.assertContains(response,'ĐÃ NHẬN TIỀN')
        self.assertNotContains(response,'class="transfer-qr"')
        with self.assertRaises(Http404):mobile_qr_image(self.request,self.qr.pk)
