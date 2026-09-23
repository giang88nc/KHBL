from unittest.mock import Mock, patch
from django.test import SimpleTestCase, RequestFactory
from apps.pos import customer as C, views, customer_phones as P

class AppendPhoneTests(SimpleTestCase):
    def setUp(self):
        self.row=dict(CustID='CU1',CustName='Khách QA',CustCode='QA',CMND='012345678901',Phone='0900000001',GhiChu2='',GhiChu3='')
        self.client=Mock();self.client.query.return_value=[]

    def data(self,row,guard):
        return dict(cust_id=row['CustID'],cmnd=row['CMND'],append_guard=guard,**{k:P.phone_key(row.get(c)) for c,k in zip(P.COLUMNS,P.KEYS)})

    def test_first_empty_slot_and_unchanged_original(self):
        new,guard=C.prepare_append(self.client,self.row,'0900000002',self.row['CMND'])
        self.assertEqual(new['GhiChu2'],'0900000002');self.assertEqual(self.row['GhiChu2'],'')
        C.validate_append(self.data(new,guard),self.row)
        C.validate_append(self.data(new,guard),new)
        occupied=dict(self.row,GhiChu2='0900000003')
        new,guard=C.prepare_append(self.client,occupied,'0900000002',occupied['CMND'])
        self.assertEqual(new['GhiChu3'],'0900000002')
        C.validate_append(self.data(new,guard),occupied)

    def test_full_or_wrong_identity_or_existing_phone_blocked(self):
        for row,phone,cmnd in [(dict(self.row,GhiChu2='0900000002',GhiChu3='0900000003'),'0900000004',self.row['CMND']),(self.row,'0900000002','111111111111'),(self.row,'0900000001',self.row['CMND'])]:
            with self.assertRaises(C.CustomerSaveError):C.prepare_append(self.client,row,phone,cmnd)
        self.client.call.assert_not_called()

    def test_stale_and_tampered_guard_blocked(self):
        new,guard=C.prepare_append(self.client,self.row,'0900000002',self.row['CMND'])
        data=self.data(new,guard)
        with self.assertRaises(C.CustomerSaveError):C.validate_append(data,dict(self.row,GhiChu2='0900000003'))
        with self.assertRaises(C.CustomerSaveError):C.validate_append(dict(data,phone='0900000004'),self.row)
        with self.assertRaises(C.CustomerSaveError):C.validate_append(dict(data,append_guard='invalid'),self.row)

    def test_duplicate_phone_message_identifies_owner(self):
        self.client.query.return_value=[self.row]
        errors=C.duplicate_errors(self.client,dict(cust_id='',phone='0900000001',cmnd=''))
        self.assertIn('Khách QA',errors[0]);self.assertIn('012345678901',errors[0])
        with self.assertRaises(C.CustomerSaveError):C.prepare_append(self.client,dict(self.row,Phone='0900000005'),'0900000001',self.row['CMND'])

    def test_identity_lookup_returns_explicit_customer_choices(self):
        request=RequestFactory().get('/',{'cmnd':self.row['CMND']})
        with patch.object(views.Q,'chan'),patch.object(views.S,'client',return_value=self.client):
            self.client.query.return_value=[dict(self.row)]
            response=views.khach_kiem_sdt(request)
        self.assertContains(response,'has_slot')
        self.assertContains(response,'/CU1/sua/')

    def test_confirm_updates_same_id_never_inserts(self):
        from contextlib import nullcontext
        new,guard=C.prepare_append(self.client,self.row,'0900000002',self.row['CMND'])
        data,errors,_=C.clean_form(dict(new,Gender='1',append_guard=guard))
        self.assertFalse(errors)
        self.client.target='sandbox';self.client.call.return_value=(0,[])
        with patch.object(C,'phone_write_lock',return_value=nullcontext()),patch.object(C,'_current',side_effect=[self.row,new]),patch.object(C,'_has_points',return_value=True):
            result=C.upsert(data,{},client=self.client)
        self.assertTrue(result['complete'])
        self.assertEqual(result['cust_id'],'CU1')
        self.assertEqual(self.client.call.call_args.args[0],'I_CUSTOMER_Upd')
        self.assertEqual(self.client.call.call_args.kwargs['p_GhiChu2'],'0900000002')
