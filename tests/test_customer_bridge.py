import hashlib
import hmac
import io
import json
import time
import uuid
from types import SimpleNamespace
from unittest.mock import patch,MagicMock
from django.test import TestCase
from django.contrib.auth import get_user_model
from apps.pos import customer_bridge as B
from apps.pos.customer_bridge_models import CustomerBridgeReceipt


class BridgeTests(TestCase):
    def setUp(self):
        self.user=get_user_model().objects.create_user('bridge-test',password='not-production',is_superuser=True)
        self.key='k'*64;self.app=B.Application(self.key,'sandbox')

    def request(self,payload,signature=True,nonce=None):
        payload=dict(payload,actor=self.user.pk,actor_proof=hmac.new(self.key.encode(),self.user.password.encode(),hashlib.sha256).hexdigest())
        body=json.dumps(payload).encode();stamp=str(time.time());nonce=nonce or uuid.uuid4().hex
        sig=hmac.new(self.key.encode(),stamp.encode()+b'\n'+nonce.encode()+b'\n'+body,hashlib.sha256).hexdigest()
        env={'REQUEST_METHOD':'POST','PATH_INFO':'/v1/customers','REMOTE_ADDR':'127.0.0.1','CONTENT_LENGTH':str(len(body)),
             'wsgi.input':io.BytesIO(body),'HTTP_X_KHCD_TIME':stamp,'HTTP_X_KHCD_NONCE':nonce,'HTTP_X_KHCD_SIGNATURE':sig if signature else 'bad'}
        status=[]
        result=b''.join(self.app(env,lambda value,headers:status.append(value)))
        return status[0],json.loads(result)

    @patch.object(B,'dispatch',return_value={'rows':[]})
    def test_signed_actor_target_and_replay(self,dispatch):
        nonce=uuid.uuid4().hex
        self.assertTrue(self.request({'action':'list'},nonce=nonce)[0].startswith('200'))
        self.assertEqual(dispatch.call_args.args[2],'sandbox')
        self.assertTrue(self.request({'action':'list'},nonce=nonce)[0].startswith('403'))
        self.assertTrue(self.request({'action':'list'},signature=False)[0].startswith('403'))
        self.assertEqual(dispatch.call_count,1)

    @patch.object(B,'dispatch')
    def test_disabled_and_permissions(self,dispatch):
        self.user.is_superuser=False;self.user.save()
        self.assertTrue(self.request({'action':'list'})[0].startswith('403'))
        self.user.is_superuser=True;self.user.is_active=False;self.user.save()
        self.assertTrue(self.request({'action':'save'})[0].startswith('403'))
        dispatch.assert_not_called()

    def pmv_client(self):
        client=MagicMock();client.target='sandbox';client.query.return_value=[{'ShopID':'test-shop'}]
        return client

    @patch.object(B.C,'duplicate_errors',return_value=[])
    @patch.object(B.C,'upsert')
    def test_complete_replay_only_one_insert(self,upsert,dup):
        upsert.return_value={'cust_id':'CU_TEST','complete':True,'created':True,'warnings':[],'errors':[]}
        payload={'token':'test_token_'+'1'*20,'form':{'CustName':'Khách thử','Gender':'1','Phone':'0900000001'}}
        client=self.pmv_client()
        self.assertTrue(B.save(payload,self.user,client)['complete'])
        self.assertTrue(B.save(payload,self.user,client)['complete'])
        self.assertEqual(upsert.call_count,1)
        self.assertEqual(upsert.call_args.kwargs['client'],client)
        self.assertEqual(CustomerBridgeReceipt.objects.get().status,'done')
        payload['form']['Phone']='0900000002'
        with self.assertRaises(ValueError):B.save(payload,self.user,client)

    @patch.object(B.C,'duplicate_errors',return_value=[])
    @patch.object(B.C,'upsert')
    def test_uncertain_insert_is_not_repeated(self,upsert,dup):
        def fail(*a,**kw):kw['on_created']('CU_PARTIAL');raise OSError('lost response')
        upsert.side_effect=fail
        payload={'token':'uncertain_'+'2'*20,'form':{'CustName':'Khách thử','Gender':'1'}}
        first=B.save(payload,self.user,self.pmv_client())
        self.assertTrue(first['uncertain']);self.assertEqual(first['cust_id'],'CU_PARTIAL')
        second=B.save(payload,self.user,self.pmv_client())
        self.assertTrue(second['uncertain']);self.assertEqual(upsert.call_count,1)

    @patch.object(B.C,'upsert')
    def test_invalid_form_never_writes(self,upsert):
        payload={'token':'validation_'+'3'*20,'form':{'CustName':'Khách thử','Gender':'1','Phone':'１２３４５６７８９０'}}
        self.assertFalse(B.save(payload,self.user,self.pmv_client())['complete']);upsert.assert_not_called()
        self.assertEqual(CustomerBridgeReceipt.objects.count(),0)

    @patch.object(B.C,'duplicate_errors',return_value=[])
    @patch.object(B.C,'upsert')
    @patch.object(B.C,'_current')
    def test_edit_preserves_omitted_fields_and_stale_rejected(self,current,upsert,dup):
        row={'CustID':'CU_OLD','CustName':'Tên cũ','Phone':'0900000001','GhiChu2':'0900000002','GhiChu3':'',
             'CMND':'','Gender':True,'Address':'Địa chỉ cũ','Notes':'Ghi chú cũ','Active':'1','NoiCap':'Cơ quan cũ'}
        current.return_value=row
        upsert.return_value={'cust_id':'CU_OLD','complete':True,'created':False,'warnings':[],'errors':[]}
        payload={'token':'editing_'+'4'*20,'version':B.version(row),'form':{'CustID':'CU_OLD','CustName':'Tên mới'}}
        B.save(payload,self.user,self.pmv_client())
        data=upsert.call_args.args[0]
        self.assertEqual(data['phone2'],'0900000002');self.assertEqual(data['address'],'Địa chỉ cũ')
        self.assertEqual(data['notes'],'Ghi chú cũ');self.assertEqual(data['issued_by'],'Cơ quan cũ')
        payload['token']='stale_'+('5'*20);payload['version']='stale'
        with self.assertRaises(ValueError):B.save(payload,self.user,self.pmv_client())
        self.assertEqual(upsert.call_count,1)

    @patch.object(B.C,'_current',return_value=None)
    @patch.object(B.C,'upsert')
    def test_missing_id_never_becomes_insert(self,upsert,current):
        with self.assertRaises(ValueError):B.save({'token':'missing_'+'6'*20,'form':{'CustID':'MISSING'}},self.user,self.pmv_client())
        upsert.assert_not_called()

    def test_receipt_only_own_actor_and_target(self):
        CustomerBridgeReceipt.objects.create(token='receipt_'+('7'*20),target='sandbox',user_id=self.user.pk,
            fingerprint='f'*64,status='done',cust_id='CU_DONE',result={'complete':True,'cust_id':'CU_DONE'})
        payload={'action':'receipt','token':'receipt_'+('7'*20)}
        self.assertTrue(B.dispatch(payload,self.user,'sandbox')['complete'])
        self.assertFalse(B.dispatch(payload,self.user,'kk')['complete'])
        other=SimpleNamespace(pk=self.user.pk+1)
        self.assertFalse(B.dispatch(payload,other,'sandbox')['complete'])
