"""Website flow and boundaries, using synthetic accounts and real read-only catalogue."""
from argparse import ArgumentParser
import hashlib
from pathlib import Path
import re
import sys
import unittest
from urllib.parse import parse_qs, urlencode, urlsplit
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from docker.portal import create_app
from docker.site_catalogue import group_catalogue, MODALITY_GROUPS

p=ArgumentParser();p.add_argument('--data-root',required=True);p.add_argument('--state-dir',required=True)
args,rest=p.parse_known_args()
PASSWORD='Website-unit-test!49'


class WebsiteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        state=Path(args.state_dir)
        if state.exists() and any(state.iterdir()):raise RuntimeError('Choose a new empty test state, not production.')
        cls.app=create_app({'TESTING':True,'STATE_DIR':str(state),'DATA_ROOT':args.data_root,'MAINTENANCE_ENABLED':False,'PROXY_HOPS':0,'TRUSTED_HOSTS':['atlas.test'],'SESSION_COOKIE_SECURE':True})
        cls.auth=cls.app.extensions['auth'];cls.repo=cls.app.extensions['repository']
        cls.auth.create_user('owner',PASSWORD,'root',True)
        cls.reader_id=cls.auth.create_user('reader',PASSWORD,modules=['BRAIN/mri-brain','HEAD AND NECK/ct-face'])
        cls.auth.create_user('empty',PASSWORD)
        cls.catalogue=cls.repo.catalogue()

    def setUp(self):self.client=self.app.test_client()
    def get(self,path,**kwargs):
        r=self.client.get(path,base_url='https://atlas.test',**kwargs);r.get_data();r.close();return r
    def csrf(self):
        text=self.get('/').text
        return re.search(r'name="csrf" value="([^"]+)"',text).group(1)
    def post_login(self,username='reader',next='/anatomy',password=PASSWORD,csrf=True):
        data={'username':username,'password':password,'next':next}
        if csrf:data['csrf']=self.csrf()
        return self.client.post('/login',data=data,base_url='https://atlas.test',headers={'Accept':'application/json'})
    def login(self,user='reader'):
        r=self.post_login(user);self.assertEqual(r.status_code,200,r.text);return r

    def test_01_public_home_private_anatomy(self):
        home=self.get('/');self.assertEqual(home.status_code,200)
        self.assertIn('data-login-trigger',home.text);self.assertIn('id="loginDialog"',home.text)
        self.assertIn('data-anatomy-link',home.text)
        self.assertNotIn('data-module-card',home.text);self.assertNotIn('anatomyViewport',home.text)
        for path in ['/anatomy','/viewer?key=BRAIN/mri-brain','/portal/runtime.js?module=BRAIN/mri-brain']:
            r=self.get(path);self.assertEqual(r.status_code,302);self.assertTrue(r.location.startswith('/login'))
        self.assertEqual(self.get('/api/catalogue').status_code,401)
        self.assertEqual(self.get('/assets/module-icons/mri-brain.png').status_code,302)

    def test_02_ajax_login_and_module_destination(self):
        r=self.post_login(next='/viewer?key=HEAD_AND_NECK/ct-face')
        self.assertEqual(r.status_code,200)
        self.assertEqual(r.json['redirect'],'/viewer?key=HEAD_AND_NECK%2Fct-face')
        page=self.get(r.json['redirect']);self.assertEqual(page.status_code,200)
        self.assertIn('/portal/runtime.js?module=HEAD+AND+NECK%2Fct-face',page.text)
        self.assertIn('id="anatomyViewport"',page.text)
        self.assertIn('href="/anatomy"',page.text)
        self.assertEqual(self.get('/app.js').data,(ROOT/'offline_anatomy_viewer/app.js').read_bytes())

    def test_03_invalid_login_and_csrf(self):
        self.assertEqual(self.post_login(password='wrong').status_code,401)
        self.assertEqual(self.post_login(csrf=False).status_code,400)
        self.assertEqual(self.get('/api/catalogue').status_code,401)

    def test_04_safe_next(self):
        for bad in ['https://evil.test/','//evil.test/path','/\\evil.test','/admin/../evil','javascript:alert(1)','/data/BRAIN/mri-brain/foo.png']:
            c=self.app.test_client();self.client=c
            r=self.post_login(next=bad);self.assertEqual(r.status_code,200)
            self.assertEqual(r.json['redirect'],'/anatomy')

    def test_05_restricted_catalogue(self):
        self.login();page=self.get('/anatomy')
        self.assertEqual(page.status_code,200)
        self.assertEqual(page.text.count('data-module-card '),2)
        self.assertIn('Mở module MRI brain',page.text);self.assertIn('Mở module CT face',page.text)
        self.assertNotIn('Mở module CT brain',page.text)
        self.assertNotIn('value="PET-CT"',page.text)
        self.assertIn('name="modality" value="MRI"',page.text)
        self.assertIn('name="modality" value="CT"',page.text)
        self.assertIn('data-region-toggle="HEAD AND NECK"',page.text)

    def test_06_direct_link_rbac(self):
        self.login()
        for key in ['BRAIN/ct-brain','THORAX/lungs']:
            self.assertEqual(self.get('/viewer?'+urlencode({'key':key})).status_code,403)
            self.assertEqual(self.get('/portal/runtime.js?'+urlencode({'module':key})).status_code,403)
        self.assertEqual(self.get('/viewer?key=HEAD_AND_NECK/ct-face').status_code,200)
        self.assertEqual(self.get('/viewer?key=BRAIN/missing-module').status_code,404)

    def test_07_empty_permissions(self):
        self.login('empty')
        for path in ['/anatomy','/viewer']:
            r=self.get(path);self.assertEqual(r.status_code,403)
            self.assertIn('Tài khoản chưa được cấp module',r.text)
            self.assertNotIn('id="loginDialog"',r.text)
        self.assertEqual(self.get('/').status_code,200)

    def test_08_admin_catalogue_all_source_modalities(self):
        self.login('owner');page=self.get('/anatomy')
        self.assertEqual(page.status_code,200)
        self.assertEqual(page.text.count('data-module-card '),self.catalogue['module_count'])
        for kind in {m['modality'] for m in self.catalogue['modules']}:
            self.assertIn(f'name="modality" value="{kind}"',page.text)
        pending=next(m for m in self.catalogue['modules'] if not m['captured'])
        self.assertEqual(self.get('/viewer?'+urlencode({'key':pending['key']})).status_code,404)
        self.assertIn('aria-disabled="true"',page.text)
        self.assertNotIn('href="/viewer?key='+pending['key'],page.text)

    def test_09_revoke_session_closes_gate(self):
        uid=self.auth.create_user('revoketest',PASSWORD,modules=['BRAIN/mri-brain'])
        self.login('revoketest');self.auth.revoke(uid)
        self.assertEqual(self.get('/anatomy').status_code,302)
        self.assertEqual(self.get('/viewer?key=BRAIN/mri-brain').status_code,302)
        self.assertEqual(self.get('/api/catalogue').status_code,401)

    def test_10_semantic_grouping_and_assets(self):
        grouped=group_catalogue(self.catalogue['modules'])
        flattened=[m for g in grouped for m in g['modules']]
        self.assertEqual({m['key'] for m in flattened},{m['key'] for m in self.catalogue['modules']})
        self.assertEqual(len(flattened),self.catalogue['module_count'])
        for path in ['/portal/static/site.css','/portal/static/site.js','/portal/static/site-mark.svg','/portal/static/body-navigation.svg']:
            r=self.get(path);self.assertEqual(r.status_code,200)
            self.assertIn('nosniff',r.headers['X-Content-Type-Options'])
        body=self.get('/portal/static/body-navigation.svg')
        self.assertIn("sandbox",body.headers['Content-Security-Policy'])

    def test_11_native_login_fallback(self):
        path='/login?'+urlencode({'next':'/viewer?key=BRAIN/mri-brain'})
        form=self.get(path);self.assertEqual(form.status_code,200)
        self.assertIn('name="next"',form.text)
        token=re.search(r'name="csrf" value="([^"]+)"',form.text).group(1)
        r=self.client.post('/login',data={'csrf':token,'username':'reader','password':PASSWORD,'next':'/viewer?key=BRAIN/mri-brain'},base_url='https://atlas.test')
        self.assertEqual(r.status_code,302);self.assertEqual(r.location,'/viewer?key=BRAIN%2Fmri-brain')

    def test_12_invalid_host_and_request(self):
        self.assertEqual(self.client.get('/',base_url='https://evil.test').status_code,400)
        self.login()
        for key in ['../BRAIN/mri-brain','BRAIN/../../secret','BRAIN/mri-brain<script>']:
            self.assertIn(self.get('/viewer?'+urlencode({'key':key})).status_code,(400,404))


if __name__=='__main__':
    result=unittest.main(argv=[sys.argv[0],*rest],exit=False).result
    print(f'WEBSITE_TESTS={"PASS" if result.wasSuccessful() else "FAIL"}; tests={result.testsRun}; public_home,login_popup_contract,RBAC,deep_link,source_grouping')
    raise SystemExit(0 if result.wasSuccessful() else 1)
