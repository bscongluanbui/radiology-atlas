"""Run inside each built image; real Gunicorn, HTTP login and persistent account reload."""
from http.client import HTTPConnection
from http.cookies import SimpleCookie
from pathlib import Path
import os
import platform
import re
import subprocess
import sys
import time
from urllib.parse import urlencode

sys.path.insert(0,'/app')
from docker.auth_store import AuthStore
from docker.preflight import validate

arch=sys.argv[1]
assert platform.machine() in {'amd64':{'x86_64','amd64'},'arm64':{'aarch64','arm64'}}[arch], platform.machine()
assert os.getuid()==10001
assert not Path('/app/docker/.env').exists()
assert not Path('/app/docker/tests').exists()
validate('/data','/state')
store=AuthStore('/state')
store.create_user('ciowner','CI-test-only!54','root')
assert AuthStore('/state').users()[0]['username']=='ciowner'
proc=subprocess.Popen(['gunicorn','--bind=127.0.0.1:8080','--workers=1','--threads=2','docker.portal:create_app()'])
cookies={}
def request(path,method='GET',data=None):
    conn=HTTPConnection('127.0.0.1',8080,timeout=5)
    headers={'Host':'bcanatomy.site','X-Forwarded-Proto':'https','X-Forwarded-For':'203.0.113.9',
             'Cookie':'; '.join(k+'='+v for k,v in cookies.items())}
    if data is not None: headers['Content-Type']='application/x-www-form-urlencoded'
    conn.request(method,path,urlencode(data) if data is not None else None,headers)
    response=conn.getresponse();body=response.read()
    for name,value in response.getheaders():
        if name.lower()=='set-cookie':
            cookie=SimpleCookie(value)
            for key,morsel in cookie.items():
                assert morsel['secure'] and morsel['httponly'];cookies[key]=morsel.value
    status=response.status;conn.close();return status,body
try:
    for _ in range(60):
        if proc.poll() is not None: raise RuntimeError('Gunicorn exited')
        try:
            if request('/healthz')[0]==200: break
        except OSError: pass
        time.sleep(1)
    else: raise RuntimeError('Gunicorn startup timeout')
    assert request('/api/catalogue')[0]==401
    status,body=request('/');assert status==200
    csrf=re.search(rb'name="csrf" value="([^"]+)"',body).group(1).decode()
    assert request('/login','POST',{'username':'ciowner','password':'CI-test-only!54','csrf':csrf})[0]==302
    assert request('/admin')[0]==200
    assert request('/viewer?key=BRAIN/mri-brain')[0]==200
    assert request('/data/BRAIN/mri-brain/rendered/1_Axial/default_Default/slice_0001.png')[0]==200
    print(f'IMAGE_SMOKE=PASS; arch={arch}; uid=10001; gunicorn=HTTP200; login=302; admin=200; image=200; accounts=persistent',flush=True)
finally:
    proc.terminate()
    try: proc.wait(timeout=15)
    except subprocess.TimeoutExpired: proc.kill();proc.wait()
