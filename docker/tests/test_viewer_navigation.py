"""Website-only navigation contract; uses isolated synthetic account state."""
from argparse import ArgumentParser
from pathlib import Path
import json,os,re,sys,unittest
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from docker.portal import create_app
p=ArgumentParser();p.add_argument('--data-root',required=True);p.add_argument('--state-dir',required=True)
args,rest=p.parse_known_args()
state=Path(args.state_dir)
if state.exists() and any(state.iterdir()):raise RuntimeError('Choose fresh test state.')

class NavigationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=create_app({'TESTING':True,'STATE_DIR':str(state),'DATA_ROOT':args.data_root,'MAINTENANCE_ENABLED':False,'PROXY_HOPS':0,'TRUSTED_HOSTS':['atlas.test']})
        cls.app.extensions['auth'].create_user('reader','Navigation-test-only!52',modules=['BRAIN/mri-brain'])
    @classmethod
    def tearDownClass(cls):cls.app.extensions['maintenance_stop'].set()
    def setUp(self):self.client=self.app.test_client()
    def get(self,url):
        r=self.client.get(url,base_url='https://atlas.test');r.get_data();r.close();return r
    def login(self):
        token=re.search(r'name="csrf" value="([^"]+)"',self.get('/login').text).group(1)
        r=self.client.post('/login',base_url='https://atlas.test',data={'username':'reader','password':'Navigation-test-only!52','csrf':token})
        self.assertEqual(r.status_code,302)
    def test_01_anonymous_still_requires_login(self):
        r=self.get('/viewer?key=BRAIN/mri-brain');self.assertEqual(r.status_code,302);self.assertTrue(r.location.startswith('/login'))
    def test_02_toolbar_links(self):
        self.login();r=self.get('/viewer?key=BRAIN/mri-brain');self.assertEqual(r.status_code,200)
        self.assertEqual(r.text.count('id="viewerHomeLink"'),1);self.assertEqual(r.text.count('id="viewerBackLink"'),1)
        self.assertLess(r.text.index('id="viewerBackLink"'),r.text.index('id="optionsMenuButton"'))
        self.assertIn('has-website-navigation',r.text);self.assertIn('/portal/static/viewer-navigation.js',r.text)
        self.assertIn('href="/"',r.text);self.assertIn('href="/anatomy"',r.text)
        self.assertEqual(self.get('/').status_code,200);self.assertEqual(self.get('/anatomy').status_code,200)
    def test_03_assets_and_csp(self):
        self.login()
        for name in ('viewer-navigation.js','viewer-session.css'):
            r=self.get('/portal/static/'+name);self.assertEqual(r.status_code,200)
            self.assertEqual(r.data,(ROOT/'docker/static'/name).read_bytes())
        r=self.get('/viewer');self.assertIn("script-src 'self'",r.headers['Content-Security-Policy'])
        self.assertIn('./mobile_gestures.js',r.text)
        self.assertLess(r.text.index('./mobile_gestures.js'),r.text.index('./app.js'))
        gesture=self.get('/mobile_gestures.js')
        self.assertEqual(gesture.status_code,200)
        self.assertEqual(gesture.data,(ROOT/'offline_anatomy_viewer/mobile_gestures.js').read_bytes())
    def test_04_standalone_is_unchanged(self):
        self.assertNotIn('viewerHomeLink',(ROOT/'offline_anatomy_viewer/index.html').read_text(encoding='utf-8'))
        self.assertNotIn('has-website-navigation',(ROOT/'offline_anatomy_viewer/styles.css').read_text(encoding='utf-8'))
    def test_05_permissions_and_runtime_still_enforced(self):
        self.login();self.assertEqual(self.get('/viewer?key=HEAD_AND_NECK/ct-face').status_code,403)
        self.assertEqual(self.get('/api/module?key=HEAD_AND_NECK/ct-face').status_code,403)
        r=self.get('/portal/runtime.js');self.assertEqual(r.status_code,200)
        self.assertIn('"remote": true',r.text)
        self.assertIn(f'"maxBytes": {self.app.config["BROWSER_CACHE_MIB"] * 1024**2}',r.text)
        self.assertIn(f'"ttlMs": {self.app.config["CACHE_TTL"] * 1000}',r.text)
        settings=json.loads(r.text.removeprefix('window.viewerRuntime=').removesuffix(';'))
        for key,config in {'decodedImages':'DECODED_IMAGES','decodeForward':'DECODE_FORWARD',
                           'decodeBackward':'DECODE_BACKWARD','decodeConcurrency':'DECODE_CONCURRENCY',
                           'preloadConcurrency':'PRELOAD_CONCURRENCY','imageConcurrency':'IMAGE_CONCURRENCY'}.items():
            self.assertEqual(settings[key],self.app.config[config])
        page=self.get('/viewer').text
        self.assertLess(page.index('./request_queue.js'),page.index('/portal/runtime.js'))
        self.assertLess(page.index('/portal/runtime.js'),page.index('./resource_cache.js'))
        self.assertIn('id="preloadStatus"',page)
        r=self.get('/request_queue.js');self.assertEqual(r.status_code,200)
        self.assertEqual(r.data,(ROOT/'offline_anatomy_viewer/request_queue.js').read_bytes())
    def test_06_environment_overrides(self):
        values={'METADATA_CACHE_MIB':'256','BROWSER_CACHE_MIB':'768','CACHE_TTL_SECONDS':'2400',
                'DECODED_IMAGE_CACHE':'40','DECODE_FORWARD':'24','DECODE_BACKWARD':'15',
                'DECODE_CONCURRENCY':'3','PRELOAD_CONCURRENCY':'3','IMAGE_CONCURRENCY':'5'}
        with patch.dict(os.environ,values):
            app=create_app({'TESTING':True,'STATE_DIR':str(state/'overrides'),'DATA_ROOT':args.data_root,'MAINTENANCE_ENABLED':False})
        try:
            for name,key in {'METADATA_CACHE_MIB':'CACHE_MIB','BROWSER_CACHE_MIB':'BROWSER_CACHE_MIB',
                             'CACHE_TTL_SECONDS':'CACHE_TTL','DECODED_IMAGE_CACHE':'DECODED_IMAGES',
                             'DECODE_FORWARD':'DECODE_FORWARD','DECODE_BACKWARD':'DECODE_BACKWARD',
                             'DECODE_CONCURRENCY':'DECODE_CONCURRENCY','PRELOAD_CONCURRENCY':'PRELOAD_CONCURRENCY',
                             'IMAGE_CONCURRENCY':'IMAGE_CONCURRENCY'}.items():
                self.assertEqual(app.config[key],int(values[name]))
        finally:app.extensions['maintenance_stop'].set()
    def test_07_invalid_environment_rejected(self):
        for name,value in [('METADATA_CACHE_MIB','513'),('BROWSER_CACHE_MIB','1025'),('CACHE_TTL_SECONDS','59'),
                           ('DECODED_IMAGE_CACHE','65'),('PRELOAD_CONCURRENCY','5'),('IMAGE_CONCURRENCY','9')]:
            with self.subTest(name=name),patch.dict(os.environ,{name:value}),self.assertRaises(ValueError):
                create_app()

if __name__=='__main__':
    result=unittest.main(argv=[sys.argv[0],*rest],exit=False).result
    print(f'NAVIGATION_HTTP={"PASS" if result.wasSuccessful() else "FAIL"}; tests={result.testsRun}; Home,Back,RBAC,assets,CSP,offline_unchanged')
    raise SystemExit(0 if result.wasSuccessful() else 1)
