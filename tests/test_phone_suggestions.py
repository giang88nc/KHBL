from unittest.mock import patch
from django.test import SimpleTestCase, RequestFactory
from apps.pos.customer_phones import suggestion_rows
from apps.pos import views


class PhoneSuggestionsTests(SimpleTestCase):
    def setUp(self):
        self.customer=dict(CustID='CU1',CustCode='QA',CustName='Khách QA',Phone='0944351461',GhiChu2='0944351464',GhiChu3='0944351465',Address='Địa chỉ QA',Diem=0)

    def test_partial_and_secondary_priority_without_mutation(self):
        rows=suggestion_rows([self.customer],'351464')
        self.assertEqual([r['selected_phone'] for r in rows],['0944351464','0944351461','0944351465'])
        self.assertEqual({r['CustID'] for r in rows},{'CU1'})
        self.assertEqual(self.customer['Phone'],'0944351461')
        self.assertNotIn('selected_phone',self.customer)

    def test_duplicate_and_empty_phones(self):
        rows=suggestion_rows([dict(self.customer,GhiChu2='0944.351.461',GhiChu3='')],'QA')
        self.assertEqual(len(rows),1)
        self.assertEqual(suggestion_rows([{'CustID':'CU2'}])[0]['selected_phone'],'')

    def test_html_posts_the_selected_phone(self):
        request=RequestFactory().get('/',{'q':'351464'})
        with patch.object(views.S,'tim_khach',return_value=[self.customer]):
            response=views.ban_tim_khach(request)
        body=response.content.decode()
        self.assertEqual(body.count('hx-post='),3)
        self.assertLess(body.index('0944351464'),body.index('0944351461'))
        self.assertIn('document_phone',body)

    def test_qr_multiple_phones_requires_choice(self):
        request=RequestFactory().get('/',{'q':'QR'})
        with patch.object(views.S,'cccd_tu_qr',return_value='012345678901'),patch.object(views.S,'tim_khach',return_value=[dict(self.customer,CMND='012345678901')]):
            response=views.ban_tim_khach(request)
        self.assertNotIn('htmx.ajax(',response.content.decode())
