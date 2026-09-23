from types import SimpleNamespace
from unittest.mock import patch
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, Client, override_settings
from apps.pos.models import MobileEmployeeIdentity
from apps.pos import mobile_auth as A


@override_settings(ALLOWED_HOSTS=['testserver','192.168.1.6','localhost'])
class MobileAuthTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user=get_user_model().objects.create_user('staff',password='test')
        self.identity=MobileEmployeeIdentity.objects.create(employee_id=42,user=self.user,employee_pmv='NV1')
        self.meta={'HTTP_HOST':'192.168.1.6:8102','secure':False}
        self.data={'employee_id':42,'full_name':'Test Staff','employee_pmv':'NV1','passkey_id':8}

    def start(self):
        with patch.object(A,'bridge_call',return_value={'authorize':A.PUBLIC_ORIGIN+A.PUBLIC_PATH+'?ticket=abc'}):
            response=self.client.post('/banle/mobile/auth/start/',**self.meta)
        self.assertEqual(response.status_code,302)
        self.assertIn('khbl_mobile_http_session',response.cookies)
        self.assertFalse(response.cookies['khbl_mobile_http_session']['secure'])
        self.assertTrue(response.cookies['khbl_mobile_http_session']['httponly'])
        return response

    def test_anonymous_landing_and_dashboard_gate(self):
        self.assertContains(self.client.get('/banle/mobile/',**self.meta),'Xác nhận FACE ID')
        response=self.client.get('/banle/mobile/dashboard/',**self.meta)
        self.assertEqual(response.status_code,302)
        self.assertIn('/banle/mobile/',response.url)

    def app_start(self,client=None):
        client=client or self.client
        with patch.object(A,'bridge_call',return_value={'authorize':A.PUBLIC_ORIGIN+A.PUBLIC_PATH+'?ticket=abc'}):
            result=client.post('/banle/mobile/auth/start/',{'mode':'app'},**self.meta)
        self.assertEqual(result.status_code,200)
        self.assertTrue(result.json()['authorize'].endswith('&app=1'))
        self.assertNotIn('verifier',result.content.decode())
        page=client.get('/banle/mobile/',**self.meta)
        self.assertEqual(page.context['app_authorize'],result.json()['authorize'])
        self.assertEqual(page.context['app_created'],result.json()['created'])

    def test_app_result_bound_to_original_cookie_not_safari(self):
        self.app_start()
        safari=Client()
        with patch.object(A,'bridge_call') as bridge:
            result=safari.post('/banle/mobile/auth/result/',**self.meta)
            self.assertEqual(result.status_code,400)
            bridge.assert_not_called()
        with patch.object(A,'bridge_call',return_value={'status':'pending'}):
            self.assertEqual(self.client.post('/banle/mobile/auth/result/',**self.meta).json()['status'],'pending')
        self.assertContains(self.client.get('/banle/mobile/',**self.meta),'data-pending="1"')
        cache.clear()
        with patch.object(A,'bridge_call',return_value={'status':'verified',**self.data}):
            result=self.client.post('/banle/mobile/auth/result/',**self.meta)
        self.assertEqual(result.json()['status'],'success')
        self.assertEqual(int(result.cookies['khbl_mobile_http_session']['max-age']),A.FACEID_SESSION_AGE)
        self.assertEqual(self.client.get('/banle/mobile/dashboard/',**self.meta).status_code,200)
        self.assertEqual(safari.get('/banle/mobile/dashboard/',**self.meta).status_code,302)
        with patch.object(A,'bridge_call') as bridge:
            self.assertEqual(self.client.post('/banle/mobile/auth/result/',**self.meta).status_code,400)
            bridge.assert_not_called()

    def test_app_result_expiry_outage_and_csrf(self):
        self.app_start()
        with patch.object(A,'bridge_call',side_effect=OSError):
            self.assertEqual(self.client.post('/banle/mobile/auth/result/',**self.meta).status_code,503)
        strict=Client(enforce_csrf_checks=True)
        strict.cookies=self.client.cookies
        self.assertEqual(strict.post('/banle/mobile/auth/result/',**self.meta).status_code,403)
        with patch.object(A.time,'time',return_value=A.time.time()+241),patch.object(A,'bridge_call') as bridge:
            self.assertEqual(self.client.post('/banle/mobile/auth/result/',**self.meta).status_code,400)
            bridge.assert_not_called()

    def test_app_result_revoked_response_clears_pending(self):
        from urllib.error import HTTPError
        self.app_start()
        with patch.object(A,'bridge_call',side_effect=HTTPError('http://127.0.0.1',400,'revoked',{},None)):
            self.assertEqual(self.client.post('/banle/mobile/auth/result/',**self.meta).status_code,400)
        self.assertContains(self.client.get('/banle/mobile/',**self.meta),'data-pending="0"')

    def test_mobile_assets_and_health_do_not_refresh_session(self):
        from django.template.loader import render_to_string
        page=self.client.get('/banle/mobile/',**self.meta)
        for snippet in ('viewport-fit=cover','apple-touch-icon','manifest.webmanifest','mobile_app.js','mobile_faceid.js'):
            self.assertContains(page,snippet)
        self.assertContains(page,'KH2 mApp')
        self.assertContains(page,'kh2-icon-512.png')
        for removed in ('Thêm KHBL','<details>','Đăng nhập bằng passkey','Mở xác nhận FACE ID'):
            self.assertNotContains(page,removed)
        self.faceid_login()
        with patch.object(A.time,'time',return_value=A.time.time()+301),patch.object(A,'bridge_call') as bridge:
            health=self.client.get('/banle/mobile/health/',**self.meta)
            self.assertEqual(health.json(),{'service':'khbl-mobile','ok':True})
            self.assertNotIn('khbl_mobile_http_session',health.cookies)
            bridge.assert_not_called()
        self.assertIn('no-store',health['Cache-Control'])

    def test_callback_login_isolated_and_no_replay(self):
        self.client.force_login(self.user)
        desktop_cookie=self.client.cookies[settings.SESSION_COOKIE_NAME].value
        self.start()
        with patch.object(A,'bridge_call',return_value=self.data):
            result=self.client.get('/banle/mobile/auth/callback/',{'code':'code'},**self.meta)
        self.assertEqual(result.status_code,302)
        self.assertEqual(result.url,'/banle/mobile/dashboard/')
        self.assertEqual(int(result.cookies['khbl_mobile_http_session']['max-age']),A.FACEID_SESSION_AGE)
        self.assertEqual(self.client.cookies[settings.SESSION_COOKIE_NAME].value,desktop_cookie)
        self.assertEqual(self.client.get('/banle/mobile/auth/callback/',{'code':'code'},**self.meta).status_code,400)

    def test_pmv_required_and_identity_is_employee_id(self):
        self.identity.employee_pmv='OTHER';self.identity.save()
        self.assertEqual(A.resolve_identity(self.data).pk,self.identity.pk)
        data={**self.data,'employee_id':99,'employee_pmv':''}
        with self.assertRaises(ValueError):A.resolve_identity(data)
        self.assertFalse(MobileEmployeeIdentity.objects.filter(employee_id=99).exists())
        data['employee_pmv']='NV99'
        identity=A.resolve_identity(data)
        self.assertEqual(A.resolve_identity(data).pk,identity.pk)
        self.assertEqual(identity.user.username,'khj_99')
        self.assertFalse(identity.user.has_usable_password())
        self.assertFalse(identity.user.is_staff or identity.user.is_superuser)
        from apps.pmv.models import UserModuleAccess
        access=UserModuleAccess.objects.get(user=identity.user)
        self.assertEqual(access.module,'CHUYEN_KHOAN')
        self.assertTrue(access.can_view and access.can_edit)
        self.assertFalse(access.can_delete or access.can_approve)

    def test_disabled_identity_and_username_collision_not_overwritten(self):
        self.identity.enabled=False;self.identity.save()
        with self.assertRaises(ValueError):A.resolve_identity(self.data)
        get_user_model().objects.create_user('khj_99')
        with self.assertRaises(ValueError):A.resolve_identity({**self.data,'employee_id':99})
        self.assertFalse(MobileEmployeeIdentity.objects.filter(employee_id=99).exists())
        self.identity.enabled=True;self.identity.save()
        self.user.is_active=False;self.user.save()
        with self.assertRaises(ValueError):A.resolve_identity(self.data)

    def test_new_employee_without_pmv_login_and_list_access(self):
        self.data={**self.data,'employee_id':99,'employee_pmv':''}
        self.start()
        with patch.object(A,'bridge_call',return_value=self.data):
            self.assertEqual(self.client.get('/banle/mobile/auth/callback/',{'code':'code'},**self.meta).status_code,403)
        self.assertEqual(self.client.get('/banle/mobile/money-in/',**self.meta).status_code,302)

    def test_removed_mapping_invalidates_existing_session(self):
        self.faceid_login()
        self.identity.delete()
        self.assertEqual(self.client.get('/banle/mobile/dashboard/',**self.meta).status_code,403)

    def test_only_verified_giang_admin_gets_mobile_exception(self):
        self.data={**self.data,'employee_id':24,'employee_pmv':'','mobile_admin':True}
        self.faceid_login()
        result=self.client.get('/banle/mobile/dashboard/',**self.meta)
        self.assertEqual(result.status_code,200)
        self.assertTrue(result.wsgi_request.khbl_mobile_admin)
        identity=MobileEmployeeIdentity.objects.get(employee_id=24)
        self.assertFalse(identity.user.is_superuser)
        self.client.cookies[settings.SESSION_COOKIE_NAME]=self.client.cookies['khbl_mobile_http_session'].value
        self.assertEqual(self.client.get('/admin/',**self.meta).status_code,403)
        with self.assertRaises(A.MissingPmv):
            A.resolve_identity({**self.data,'employee_id':25})
        with self.assertRaises(A.MissingPmv):
            A.resolve_identity({**self.data,'mobile_admin':False})

    def test_notice_and_app_missing_pmv_redirect(self):
        self.assertContains(self.client.get('/banle/mobile/auth/notice/',**self.meta),
                            'Chưa liên kết mã PMV',status_code=403)
        self.app_start()
        with patch.object(A,'bridge_call',return_value={'status':'verified',**self.data,'employee_pmv':''}):
            result=self.client.post('/banle/mobile/auth/result/',**self.meta)
        self.assertEqual(result.status_code,403)
        self.assertEqual(result.json()['redirect'],'/banle/mobile/auth/notice/')

    def test_password_account_without_employee_pmv_keeps_access(self):
        user=get_user_model().objects.create_superuser('password-admin',password='test')
        self.client.force_login(user)
        self.client.cookies['khbl_mobile_http_session']=self.client.cookies[settings.SESSION_COOKIE_NAME].value
        with patch.object(A,'bridge_call') as bridge:
            self.assertEqual(self.client.get('/banle/mobile/money-in/',**self.meta).status_code,200)
        bridge.assert_not_called()

    def faceid_login(self):
        self.start()
        with patch.object(A,'bridge_call',return_value=self.data):
            self.assertEqual(self.client.get('/banle/mobile/auth/callback/',{'code':'code'},**self.meta).status_code,302)

    def test_session_revocation_and_outage_fail_closed(self):
        self.faceid_login()
        with patch.object(A.time,'time',return_value=A.time.time()+301):
            with patch.object(A,'bridge_call',side_effect=OSError):
                self.assertEqual(self.client.get('/banle/mobile/dashboard/',**self.meta).status_code,503)
            with patch.object(A,'bridge_call',return_value={'active':False}):
                self.assertEqual(self.client.get('/banle/mobile/dashboard/',**self.meta).status_code,403)
        self.assertEqual(self.client.get('/banle/mobile/dashboard/',**self.meta).status_code,302)

    def test_active_session_rechecks_periodically_and_rolls_expiry(self):
        self.faceid_login()
        with patch.object(A,'bridge_call',return_value={'active':True}) as call:
            self.assertEqual(self.client.get('/banle/mobile/dashboard/',**self.meta).status_code,200)
            call.assert_not_called()
            with patch.object(A.time,'time',return_value=A.time.time()+86401):
                result=self.client.get('/banle/mobile/dashboard/',**self.meta)
                self.assertEqual(result.status_code,200)
                self.assertEqual(int(result.cookies['khbl_mobile_http_session']['max-age']),A.FACEID_SESSION_AGE)
                call.assert_called_once_with('status',{'employee_id':42,'passkey_id':8})

    def test_callback_without_initiating_cookie_rejected(self):
        with patch.object(A,'bridge_call') as call:
            self.assertEqual(self.client.get('/banle/mobile/auth/callback/',{'code':'x'},**self.meta).status_code,400)
        call.assert_not_called()

    def test_start_other_host_does_not_issue_grant(self):
        with patch.object(A,'bridge_call') as call:
            self.assertEqual(self.client.post('/banle/mobile/auth/start/',secure=True,HTTP_HOST='localhost:8100').status_code,400)
        call.assert_not_called()

    def test_login_form_preserves_origin_and_csrf_is_enforced(self):
        import re
        client=Client(enforce_csrf_checks=True)
        page=client.get('/banle/mobile/',**self.meta)
        html=page.content.decode()
        self.assertIn('name="referrer" content="same-origin"',html)
        self.assertNotIn('content="no-referrer"',html)
        token=re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"',html)[1]
        path='/banle/mobile/auth/start/'
        with patch.object(A,'bridge_call',return_value={'authorize':A.PUBLIC_ORIGIN+A.PUBLIC_PATH+'?ticket=abc'}) as call:
            for origin,data in [('null',{'csrfmiddlewaretoken':token}),
                                ('https://attacker.example',{'csrfmiddlewaretoken':token}),
                                (A.LAN_ORIGIN,{})]:
                self.assertEqual(client.post(path,data,HTTP_ORIGIN=origin,**self.meta).status_code,403)
            call.assert_not_called()
            response=client.post(path,{'csrfmiddlewaretoken':token},HTTP_ORIGIN=A.LAN_ORIGIN,**self.meta)
            self.assertEqual(response.status_code,302)
            call.assert_called_once()

    def test_mobile_only_cannot_open_admin_or_out_or_cash(self):
        from apps.pos.models import MoneyFlow
        from django.utils import timezone
        self.client.force_login(self.user)
        self.client.cookies['khbl_mobile_http_session']=self.client.cookies[settings.SESSION_COOKIE_NAME].value
        for path in ('/admin/','/banle/check-money-flow/','/he-thong/'):
            self.assertEqual(self.client.get(path).status_code,403)
        flow=MoneyFlow.objects.create(direction='OUT',expected_amount=100,business_date=timezone.localdate(),source_system='KHBL',source_type='test',source_id='out')
        headers={**self.meta,'HTTP_X_KHBL_MOBILE':'1'}
        self.assertEqual(self.client.get(f'/banle/check-money-flow/{flow.pk}/payment/BANK/',**headers).status_code,403)
        self.assertEqual(self.client.get(f'/banle/check-money-flow/{flow.pk}/payment/CASH/',**headers).status_code,403)
