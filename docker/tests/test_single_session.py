"""Exclusive viewer: actual SQLite transactions and HTTP, independent of the UI."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import hashlib
import re
import secrets
import sys
import tempfile
import threading
import unittest

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'docker/tests'))
from ci_fixture import create_fixture
from docker.auth_store import AuthStore
from docker.portal import create_app, VIEWER_CONFLICT


class SingleSessionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.now=1000000.0
        self.auth=AuthStore(self.root/'state',clock=lambda:self.now)
        self.uid=self.auth.create_user('reader','x','admin')
        self.first=self.auth.login('reader','x');self.second=self.auth.login('reader','x')
        self.a='a'*32;self.b='b'*32
    def lease(self,token=None,client=None,action='acquire'):
        return self.auth.viewer_session(token or self.first,client or self.a,action)
    def test_01_single_login_and_same_cookie_two_tabs(self):
        self.assertEqual(self.lease(),'ok')
        self.assertEqual(self.lease(),'ok')
        self.assertEqual(self.lease(self.second,self.b),'conflict')
        self.assertEqual(self.lease(self.first,self.b),'conflict')
        self.assertEqual(self.lease(self.first,self.a,'check'),'ok')
    def test_02_server_race_only_one_winner(self):
        barrier=threading.Barrier(8)
        def compete(index):
            barrier.wait()
            store=AuthStore(self.root/'state',clock=lambda:self.now)
            return store.viewer_session(self.first,f'{index:032x}','acquire')
        with ThreadPoolExecutor(max_workers=8) as pool: results=list(pool.map(compete,range(8)))
        self.assertEqual(results.count('ok'),1);self.assertEqual(results.count('conflict'),7)
    def test_03_stale_heartbeat_release_and_expiry(self):
        self.assertEqual(self.lease(),'ok');self.now+=91
        self.assertEqual(self.lease(action='check'),'expired')
        self.assertEqual(self.lease(self.second,self.b),'ok')
        self.assertEqual(self.lease(action='heartbeat'),'conflict')
        self.assertEqual(self.lease(action='release'),'released')
        self.assertEqual(self.lease(self.second,self.b,'check'),'ok')
    def test_04_heartbeat_renews_and_read_does_not(self):
        self.lease();self.now+=80
        self.assertEqual(self.lease(action='heartbeat'),'ok');self.now+=80
        self.assertEqual(self.lease(action='check'),'ok');self.now+=11
        self.assertEqual(self.lease(action='check'),'expired')
        self.assertEqual(self.lease(action='heartbeat'),'expired')
    def test_05_logout_revoke_and_password_release(self):
        self.lease();self.auth.logout(self.first)
        self.assertEqual(self.lease(self.second,self.b),'ok')
        self.auth.revoke(self.uid)
        self.assertEqual(self.lease(self.second,self.b,'check'),'unauthenticated')
        fresh=self.auth.login('reader','x');self.lease(fresh,self.a)
        self.auth.change_password(self.uid,'x','y')
        self.assertEqual(self.lease(self.auth.login('reader','y'),self.b),'ok')
    def test_06_invalid_id_login_expiry_and_maintenance(self):
        self.assertEqual(self.auth.viewer_session(self.first,'','acquire'),'invalid')
        self.assertEqual(self.lease('bogus',self.a),'unauthenticated')
        self.lease();self.now+=1801
        self.assertEqual(self.lease(),'unauthenticated')
        self.auth.maintenance()
        with self.auth.connect() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM viewer_leases').fetchone()[0],0)
    def test_07_restart_additive_upgrade_and_login_flood(self):
        with self.auth.connect() as db:
            before=dict(db.execute('SELECT * FROM users').fetchone())
            db.execute('DROP TABLE viewer_leases')
        self.auth=AuthStore(self.root/'state',clock=lambda:self.now)
        with self.auth.connect() as db:
            self.assertEqual(before,dict(db.execute('SELECT * FROM users').fetchone()))
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],3)
        self.lease()
        other=AuthStore(self.root/'state',clock=lambda:self.now)
        self.assertEqual(other.viewer_session(self.second,self.b,'acquire'),'conflict')
        for i in range(7):self.auth.login('reader','x')
        self.assertEqual(self.lease(action='check'),'ok')
    def test_08_roles_independent_accounts_and_disabled(self):
        self.lease()
        for index,role in enumerate(('root','admin','standard')):
            name=f'user{index}';uid=self.auth.create_user(name,'x',role,regions=['BRAIN'])
            token=self.auth.login(name,'x');second=self.auth.login(name,'x')
            self.assertEqual(self.lease(token,self.a),'ok')
            self.assertEqual(self.lease(second,self.b),'conflict')
            if role!='root':
                self.auth.update_user(uid,role,False,False,[],regions=['BRAIN'])
                self.assertEqual(self.lease(token,self.a,'check'),'unauthenticated')


class SingleSessionHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.data=create_fixture(self.root/'data')
        self.app=create_app({'TESTING':True,'STATE_DIR':str(self.root/'state'),'DATA_ROOT':str(self.data),
            'MAINTENANCE_ENABLED':False,'PROXY_HOPS':0,'TRUSTED_HOSTS':['atlas.test']})
        self.addCleanup(self.app.extensions['maintenance_stop'].set)
        self.app.extensions['auth'].create_user('reader','x',regions=['BRAIN'])
        self.first,self.h1=self.login();self.second,self.h2=self.login()
    def get(self,client,path,headers=None,method='GET'):
        r=client.open(path,base_url='https://atlas.test',headers=headers,method=method);r.get_data();r.close();return r
    def login(self):
        client=self.app.test_client();page=self.get(client,'/login')
        csrf=re.search(r'name="csrf" value="([^"]+)"',page.text).group(1)
        r=client.post('/login',base_url='https://atlas.test',data={'username':'reader','password':'x','csrf':csrf})
        self.assertEqual(r.status_code,302)
        page=self.get(client,'/viewer?key=BRAIN/mri-brain')
        csrf=re.search(r'name="csrf" value="([^"]+)"',page.text).group(1)
        return client,{'X-CSRF-Token':csrf,'X-Viewer-ID':secrets.token_hex(16)}
    def post(self,client,headers,action='acquire'):
        return client.post('/api/viewer-session',base_url='https://atlas.test',headers=headers,data={'action':action})
    def test_01_exact_popup_and_data_routes_no_bypass(self):
        self.assertEqual(self.post(self.first,self.h1).status_code,200)
        denied=self.post(self.second,self.h2)
        self.assertEqual(denied.status_code,409);self.assertEqual(denied.json['error'],VIEWER_CONFLICT)
        self.assertNotIn('sid',denied.text);self.assertIn('no-store',denied.headers['Cache-Control'])
        image='/data/BRAIN/mri-brain/rendered/1_Axial/default_Default/slice_0001.png'
        for path in ('/api/module?key=BRAIN/mri-brain','/api/slice?key=BRAIN/mri-brain&series=1_Axial&variant=default_Default&slice=1',
                     '/api/structure?key=BRAIN/mri-brain&taxon=1','/api/search?key=BRAIN/mri-brain&q=brain',
                     '/api/translations?key=BRAIN/mri-brain&lang=vi',image):
            for method in ('GET','HEAD'):
                self.assertEqual(self.get(self.second,path,self.h2,method).status_code,409,path)
                self.assertEqual(self.get(self.second,path,None,method).status_code,428,path)
        received=self.get(self.first,image,self.h1)
        self.assertEqual(received.status_code,200)
        original=next(self.data.rglob('slice_0001.png')).read_bytes()
        self.assertEqual(hashlib.sha256(received.data).hexdigest(),hashlib.sha256(original).hexdigest())
        for path in ('/','/account','/anatomy','/api/catalogue','/healthz'):
            self.assertEqual(self.get(self.second,path).status_code,200,path)
    def test_02_same_session_tabs_and_logout_wrong_holder(self):
        self.post(self.first,self.h1)
        cloned={**self.h1,'X-Viewer-ID':'f'*32}
        self.assertEqual(self.post(self.first,cloned).status_code,409)
        self.assertEqual(self.post(self.second,self.h2,'release').status_code,200)
        self.assertEqual(self.post(self.first,self.h1,'heartbeat').status_code,200)
        self.assertEqual(self.post(self.first,self.h1,'release').status_code,200)
        self.assertEqual(self.post(self.second,self.h2).status_code,200)
    def test_03_csrf_and_permissions(self):
        self.assertEqual(self.post(self.first,{'X-Viewer-ID':'a'*32}).status_code,400)
        self.assertEqual(self.post(self.first,{**self.h1,'X-Viewer-ID':'bad'}).status_code,428)
        self.assertEqual(self.post(self.first,self.h1,'takeover').status_code,400)
        self.assertEqual(self.get(self.second,'/api/module?key=THORAX/sample-lung',self.h2).status_code,403)
        self.assertEqual(self.get(self.second,'/admin').status_code,403)
        anonymous=self.app.test_client()
        self.assertEqual(self.get(anonymous,'/api/module?key=BRAIN/mri-brain').status_code,401)
    def test_04_frontend_gated_and_docker_packages(self):
        page=self.get(self.first,'/viewer').text
        self.assertIn('class="viewer-session-locked"',page)
        self.assertIn('id="viewerSessionDialog"',page)
        self.assertLess(page.index('/portal/static/viewer-session.js'),page.index('./app.js'))
        self.assertIn('await window.viewerSession.ready',(ROOT/'offline_anatomy_viewer/app.js').read_text(encoding='utf-8'))
        self.assertNotIn('viewerSessionDialog',(ROOT/'offline_anatomy_viewer/index.html').read_text(encoding='utf-8'))
        self.assertEqual(self.get(self.first,'/portal/static/viewer-session.js').status_code,200)
        from docker.release import sources
        paths={p.relative_to(ROOT).as_posix() for p in sources()}
        self.assertIn('docker/static/viewer-session.js',paths)
        self.assertIn('docker/templates/viewer_session.html',paths)


if __name__=='__main__':unittest.main()
