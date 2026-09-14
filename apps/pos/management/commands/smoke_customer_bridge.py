"""Exercise actual shared-service UPSERT on sandbox; no real KK customer writes."""
import secrets
import re
from datetime import datetime
from types import SimpleNamespace
from django.core.management.base import BaseCommand,CommandError
from apps.pmv.client import PmvClient
from apps.pmv import sandbox
from apps.pos import customer as C,customer_bridge as B
from apps.pos.customer_bridge_models import CustomerBridgeReceipt


class Command(BaseCommand):
    def handle(self,*args,**kwargs):
        client=PmvClient('sandbox',tag='smoke_customer_bridge')
        actor=SimpleNamespace(pk=1,is_authenticated=True,is_superuser=True)
        tokens=[secrets.token_urlsafe(24) for _ in range(2)]
        stamp=datetime.now().strftime('%H%M%S');phone='098'+stamp+'7';cid=''
        before=client.query('SELECT COUNT(*) n FROM I_CUSTOMER WITH (NOLOCK)')[0]['n']
        payload={'token':tokens[0],'form':{'CustName':'KIỂM BRIDGE '+stamp,'Phone':phone,'Gender':'1'}}
        try:
            result=B.save(payload,actor,client);cid=result.get('cust_id','')
            if not result.get('complete'):raise CommandError(str(result.get('errors')))
            replay=B.save(payload,actor,client)
            if replay['cust_id']!=cid:raise CommandError('Replay changed CustID')
            row=C._current(client,cid)
            result=B.save({'token':tokens[1],'version':B.version(row),'form':{'CustID':cid,'Address':'ĐỊA CHỈ THỬ BRIDGE'}},actor,client)
            if not result.get('complete'):raise CommandError(str(result.get('errors')))
            row=C._current(client,cid)
            if row['Phone']!=phone or row['Address']!='ĐỊA CHỈ THỬ BRIDGE':raise CommandError('Edit did not preserve phone')
            if client.query('SELECT COUNT(*) n FROM I_CUSTOMER WITH (NOLOCK)')[0]['n']!=before+1:raise CommandError('Unexpected row count')
            self.stdout.write('PASS: INSERT, durable replay, UPDATE exact CustID, preserved omitted fields, complete read-back.')
        finally:
            if not cid:
                r=CustomerBridgeReceipt.objects.filter(token=tokens[0],target='sandbox').first()
                cid=r.cust_id if r else ''
            if cid:
                cleanup(cid,client)
            CustomerBridgeReceipt.objects.filter(token__in=tokens,target='sandbox').delete()
        if client.query('SELECT COUNT(*) n FROM I_CUSTOMER WITH (NOLOCK)')[0]['n']!=before:raise CommandError('Cleanup count mismatch')
        self.stdout.write('PASS: Removed only sandbox test customer and its two receipts.')


def cleanup(cid,client):
    if client.target!='sandbox' or not re.fullmatch(r'CU[0-9]+',cid):raise CommandError('Cleanup requires exact sandbox test ID')
    try:
        with C.SAVE_LOCK:C.delete(cid,client=client)
    except Exception:
        # Sandbox intentionally disables OLE. Use the same narrowly allowed
        # cleanup paths as smoke_customer_phones, never enable OLE or touch KK.
        for table in ('I_GIAODICH_KHACHHANG','SHOP_CUSTOMER'):
            sql="DELETE FROM "+table+" WHERE CustID='"+cid+"'"
            sandbox.sandbox_exec_khuon(sql,re.compile(re.escape(sql)+r'\Z'))
        for table in ('I_LICHSUTICHLUYDIEM','T_CUSTOMER_DEBT','I_DIEMTICHLUY','I_CUSTOMER'):
            sandbox.sandbox_don_dep('DELETE FROM '+table+' WHERE CustID = ?',(cid,))
