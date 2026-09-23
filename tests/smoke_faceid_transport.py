"""Manual live transport check only. Never authenticates/creates a passkey or user."""
import re
import json
import ssl
from http.cookiejar import CookieJar
from urllib.request import Request, build_opener, ProxyHandler, HTTPCookieProcessor, HTTPSHandler, HTTPRedirectHandler
from urllib.error import HTTPError
from urllib.parse import urlencode
from pathlib import Path


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None


def main(app_mode=False):
    origin='http://192.168.1.6:8102'
    public='https://unopposable-parheliacal-waylon.ngrok-free.dev'
    context=ssl.create_default_context()
    session=build_opener(ProxyHandler({}),HTTPCookieProcessor(CookieJar()),HTTPSHandler(context=context),NoRedirect())
    def fetch(url,data=None,headers=None):
        try:response=session.open(Request(url,data=data,headers=headers or {}),timeout=15)
        except HTTPError as error:response=error
        return response.status,response.headers,response.read().decode()
    status,_,page=fetch(origin+'/banle/mobile/')
    assert status==200,status
    # A hand-built HTTP Origin alone misses the browser's form referrer policy.
    assert 'name="referrer" content="same-origin"' in page
    csrf=re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"',page).group(1)
    for path in ('/static/mobile/manifest.webmanifest','/banle/mobile/health/'):
        status,_,body=fetch(origin+path)
        assert status==200,(path,status)
        json.loads(body)
    fields={'csrfmiddlewaretoken':csrf}
    if app_mode:fields['mode']='app'
    status,headers,body=fetch(origin+'/banle/mobile/auth/start/',urlencode(fields).encode(),
        {'Referer':origin+'/banle/mobile/','Origin':origin})
    assert status==(200 if app_mode else 302),(status,body[:120])
    target=json.loads(body)['authorize'] if app_mode else headers['Location']
    assert target.startswith(public+'/banle/mobile/?ticket=')
    if app_mode:
        assert target.endswith('&app=1')
        status,_,body=fetch(origin+'/banle/mobile/auth/result/',urlencode({'csrfmiddlewaretoken':csrf}).encode(),{'Origin':origin})
        assert status==200 and json.loads(body)['status']=='pending',(status,body[:120])
        # Separate browser cookie jar: this browser does not carry KHBL's initiating session.
        session=build_opener(ProxyHandler({}),HTTPCookieProcessor(CookieJar()),HTTPSHandler(context=context),NoRedirect())
    status,_,page=fetch(target,headers={'ngrok-skip-browser-warning':'1'})
    assert status==200,(status,page[:120])
    assert 'Đăng nhập bằng FACE ID' in page
    csrf=re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"',page).group(1)
    status,_,body=fetch(public+'/portal-user/auth/khbl/options/',b'{}',{
        'Content-Type':'application/json','ngrok-skip-browser-warning':'1','X-CSRFToken':csrf,'Origin':public,'Referer':public+'/portal-user/auth/khbl/'})
    assert status==200,(status,body[:120])
    options=json.loads(body)
    assert options['rpId']=='unopposable-parheliacal-waylon.ngrok-free.dev'
    assert options['userVerification']=='required'
    print('PASS:', 'separate-cookie standalone transport + pending result +' if app_mode else 'normal-browser transport +', 'LAN HTTP 8102 + CSRF start + signed backend + public HTTPS + WebAuthn options. No biometric assertion submitted.')


if __name__=='__main__':
    import sys
    main(app_mode='--app' in sys.argv)
