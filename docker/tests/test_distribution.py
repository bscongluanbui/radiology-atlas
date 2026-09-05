"""Source-only release checks: synthetic fixtures, real Flask auth and simulated Docker CLI."""
import hashlib
import base64
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from urllib.parse import urlencode
import yaml

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(Path(__file__).resolve().parent))
from ci_fixture import create_fixture
from docker.portal import create_app
from docker.preflight import validate
from docker.release import sources
from viewer_client import acquire
# Keep the published-image gate exercising leases without editing workflow scopes.
from test_single_session import SingleSessionTests, SingleSessionHTTPTests


class DistributionTests(unittest.TestCase):
    def test_pull_only_default_and_persistent_state(self):
        compose=yaml.safe_load((ROOT/'docker/compose.yaml').read_text())
        self.assertEqual(compose['name'],'radiology-atlas')
        services=compose['services'];viewer=services['viewer']
        self.assertNotIn('build',viewer);self.assertNotIn('ports',viewer)
        self.assertIn('ghcr.io/bscongluanbui/radiology-atlas:latest',viewer['image'])
        self.assertEqual([s for s,v in services.items() if not v.get('profiles')],['viewer'])
        self.assertIn('direct',services['caddy']['profiles'])
        self.assertTrue(viewer['volumes'][0]['read_only'])
        self.assertIn('atlas-state:/state',viewer['volumes'])
        self.assertEqual(compose['networks']['backend'],{'name':'radiology-atlas_backend','internal':True})
        self.assertEqual(viewer['environment']['PRELOAD_CONCURRENCY'],'${PRELOAD_CONCURRENCY:-2}')
        self.assertEqual(viewer['environment']['BROWSER_CACHE_MIB'],'${BROWSER_CACHE_MIB:-512}')

    def test_workflow_gates_and_pinned_actions(self):
        workflow=yaml.safe_load((ROOT/'.github/workflows/publish-viewer.yml').read_text())
        self.assertEqual(workflow['on']['push']['branches'],['main'])
        self.assertEqual(workflow['permissions'],{'contents':'read'})
        image=workflow['jobs']['image'];self.assertEqual(image['needs'],'test')
        self.assertIn("github.event_name != 'pull_request'",image['if'])
        self.assertIn("refs/heads/main",image['if'])
        for job in workflow['jobs'].values():
            for step in job['steps']:
                if 'uses' in step:self.assertRegex(step['uses'],r'^[a-z0-9-]+/[a-z0-9-]+@[0-9a-f]{40}$')
        builds=[s for s in image['steps'] if s.get('uses','').startswith('docker/build-push-action@')]
        self.assertEqual(builds[0]['with']['platforms'],'linux/amd64,linux/arm64')
        self.assertIn('sha-${{ github.sha }}',builds[0]['with']['tags'])
        self.assertTrue(builds[0]['with']['push'])

    def test_source_release_includes_ci_not_private_data(self):
        names={p.relative_to(ROOT).as_posix() for p in sources()}
        self.assertIn('.github/workflows/publish-viewer.yml',names)
        self.assertIn('docker/compose.build.yaml',names)
        self.assertIn('offline_anatomy_viewer/mobile_gestures.js',names)
        for image in ('doctor-infographics.jpg','medical-dashboard.jpg','pelvis-muscles.webp','radiology-infographics.jpg','skeleton-hud.jpg'):
            self.assertIn(f'docker/static/images/{image}',names)
        self.assertNotIn('docker/.env',names)
        self.assertFalse(any(n.endswith(('.sqlite3','.key','.pem')) or n.startswith('imaios_data/') for n in names))
        ignore=(ROOT/'docker/Dockerfile.dockerignore').read_text()
        for rule in ('docker/.env','docker/tests/','**/*.sqlite*','**/all_modules/'):
            self.assertIn(rule,ignore)

    def test_mobile_navigation_and_dark_theme_contract(self):
        site=(ROOT/'docker/templates/site_base.html').read_text(encoding='utf-8')
        portal=(ROOT/'docker/templates/base.html').read_text(encoding='utf-8')
        theme=(ROOT/'docker/static/theme.js').read_text(encoding='utf-8')
        site_css=(ROOT/'docker/static/site.css').read_text(encoding='utf-8')
        portal_css=(ROOT/'docker/static/portal.css').read_text(encoding='utf-8')
        for template in (site,portal):
            self.assertIn('/portal/static/theme.js',template)
            self.assertIn('data-theme-toggle',template)
            self.assertIn('data-nav-toggle',template)
            self.assertIn('aria-expanded="false"',template)
            self.assertIn('viewport-fit=cover',template)
        self.assertIn('radiology-atlas-theme',theme)
        self.assertIn('prefers-color-scheme: dark',theme)
        self.assertIn('localStorage.setItem',theme)
        self.assertIn('event.key !== "Escape"',theme)
        for stylesheet in (site_css,portal_css):
            self.assertIn('html[data-theme=dark]',stylesheet)
            self.assertIn('@media(max-width:720px)',stylesheet)
            self.assertIn('.mobile-nav-toggle',stylesheet)
            self.assertIn('min-height:44px',stylesheet)
        self.assertIn('docker/static/theme.js',{p.relative_to(ROOT).as_posix() for p in sources()})

    def test_local_build_separate(self):
        local=yaml.safe_load((ROOT/'docker/compose.build.yaml').read_text())['services']['viewer']
        self.assertIn('build',local)
        self.assertIn('LOCAL_VIEWER_IMAGE',local['image'])
        for name in ('deploy.sh','update.sh','check.sh'):
            self.assertNotIn('build-local.sh',(ROOT/'docker'/name).read_text())

    def test_profile_ui_and_csp_compatible_scripts(self):
        account=(ROOT/'docker/templates/account.html').read_text(encoding='utf-8')
        admin=(ROOT/'docker/templates/admin.html').read_text(encoding='utf-8')
        login=(ROOT/'docker/templates/login.html').read_text(encoding='utf-8')
        self.assertIn('action="/account/profile"',account)
        self.assertIn('action="/account/avatar"',account)
        self.assertIn('action="/account/password"',account)
        self.assertNotIn('minlength=',account+admin)
        self.assertNotRegex(admin+login,r'<script(?![^>]+src=)')
        self.assertNotRegex(admin,r'\sonclick=')
        self.assertNotIn('fonts.googleapis.com',login)
        self.assertTrue((ROOT/'docker/static/admin.js').is_file())
        self.assertTrue((ROOT/'docker/static/login.js').is_file())


class FixtureTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.data=create_fixture(self.root/'data')
        self.state=self.root/'state'
    def app(self):
        return create_app({'TESTING':True,'DATA_ROOT':str(self.data),'STATE_DIR':str(self.state),
            'MAINTENANCE_ENABLED':False,'TRUSTED_HOSTS':['bcanatomy.site'],'PROXY_HOPS':1})
    def test_preflight_real_shape_and_no_data_mutation(self):
        before={p.relative_to(self.data).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in self.data.rglob('*') if p.is_file()}
        self.assertEqual(validate(self.data,self.state),(2,2))
        after={p.relative_to(self.data).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in self.data.rglob('*') if p.is_file()}
        self.assertEqual(before,after);self.assertEqual(list(self.state.iterdir()),[])
    def test_preflight_rejects_empty_or_broken_catalogue(self):
        for text in ('{}','[]','{"modules":[]}','not-json'):
            (self.data/'module_catalogue.json').write_text(text)
            with self.assertRaises(ValueError):validate(self.data,self.state)
    def test_proxy_login_roles_image_and_restart(self):
        app=self.app();store=app.extensions['auth'];password='CI-test-only!54'
        for name,role in [('owner','root'),('reader','admin'),('limited','standard')]:
            store.create_user(name,password,role,regions=['BRAIN'] if role=='standard' else [])
        owner=next(user for user in store.users() if user['username']=='owner')
        store.update_profile(owner['id'],'Doctor@Example.com','1988')
        png=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=')
        store.set_avatar(owner['id'],png,'.png')
        profile=store.get_user(owner['id'])
        self.assertEqual((profile['email'],profile['birth_year']),('doctor@example.com',1988))
        self.assertEqual(store.avatar_path(owner['id']).read_bytes(),png)
        image='/data/{}/rendered/1_Axial/default_Default/slice_0001.png'
        headers={'X-Forwarded-Proto':'https','X-Forwarded-For':'203.0.113.9'}
        anonymous=app.test_client()
        self.assertEqual(anonymous.get('/api/catalogue',base_url='http://bcanatomy.site',headers=headers).status_code,401)
        self.assertEqual(anonymous.get('/',base_url='http://untrusted.test').status_code,400)
        for name in ('owner','reader','limited'):
            client=app.test_client()
            home=client.get('/',base_url='http://bcanatomy.site',headers=headers)
            self.assertIn('Secure',home.headers['Set-Cookie']);self.assertIn('HttpOnly',home.headers['Set-Cookie'])
            csrf=re.search(r'name="csrf" value="([^"]+)"',home.text).group(1)
            login=client.post('/login',data={'csrf':csrf,'username':name,'password':password},base_url='http://bcanatomy.site',headers=headers)
            self.assertEqual(login.status_code,302)
            viewer_headers=acquire(client,base_url='http://bcanatomy.site',headers=headers)
            def get(path):
                response=client.get(path,base_url='http://bcanatomy.site',headers=viewer_headers)
                response.get_data();response.close();return response
            self.assertEqual(get('/admin').status_code,200 if name=='owner' else 403)
            self.assertEqual(get('/api/catalogue').json['module_count'],1 if name=='limited' else 2)
            self.assertEqual(get(image.format('BRAIN/mri-brain')).status_code,200)
            self.assertEqual(get(image.format('THORAX/sample-lung')).status_code,403 if name=='limited' else 200)
            query=urlencode({'key':'BRAIN/mri-brain','series':'1_Axial','variant':'default_Default','slice':'1'})
            response=get('/api/slice?'+query)
            self.assertEqual(response.status_code,200)
            self.assertIn('no-store',response.headers['Cache-Control'])
        self.assertEqual({u['username'] for u in self.app().extensions['auth'].users()},{'owner','reader','limited'})


class ScriptTests(unittest.TestCase):
    """Fake CLI asserts command ordering/rollback; NOT evidence of a real Docker Engine run."""
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.bash=shutil.which('bash')
        if os.name=='nt':self.bash='C:/Program Files/Git/bin/bash.exe'
        if not self.bash or not Path(self.bash).exists():self.skipTest('Bash not installed')
        create_fixture(self.root/'data')
        for file in ('deploy.sh','update.sh','attach-tunnel.sh','common.sh'):
            shutil.copyfile(ROOT/'docker'/file,self.root/file)
        self.envtext=f'DDNS_HOST=bcanatomy.site\nDATA_ROOT="{(self.root/"data").as_posix()}"\nVIEWER_IMAGE=ghcr.io/bscongluanbui/radiology-atlas:latest\nTUNNEL_CONTAINER=thirsty_agnesi\n'
        (self.root/'.env').write_text(self.envtext,encoding='utf-8')
        (self.root/'fake.py').write_text('''import json,os,sys
from pathlib import Path
a=sys.argv[1:];root=Path(os.environ['MOCK_ROOT'])
with (root/'calls.jsonl').open('a') as f:f.write(json.dumps({'args':a,'image':os.environ.get('VIEWER_IMAGE')})+'\\n')
if a[:2]==['compose','pull'] and os.environ.get('FAIL_PULL'):sys.exit(1)
if a[:2]==['compose','up'] and os.environ.get('FAIL_UP') and os.environ.get('VIEWER_IMAGE')!='radiology-atlas-viewer:rollback':sys.exit(1)
if a[:3]==['compose','ps','-q']:print('viewer-cid')
if a[:2]==['inspect','--format']:
 field=a[2]
 if field=='{{.Config.Image}}':print('cloudflare/cloudflared:latest')
 elif field=='{{.State.Running}}':print('true')
 elif field=='{{.Image}}':print('sha256:old-running-image')
 elif 'NetworkSettings.Networks' in field:print('bridge'+('\\nradiology-atlas_backend' if (root/'attached').exists() else ''))
if a[:2]==['network','connect']:(root/'attached').touch()
''',encoding='utf-8')
    def run_script(self,name,**extra):
        env={**os.environ,'MOCK_ROOT':str(self.root),'PYTHON_FAKE':Path(sys.executable).as_posix(),
             'DOCKER_FAKE':(self.root/'fake.py').as_posix(),'TARGET_SCRIPT':(self.root/name).as_posix(),**extra}
        return subprocess.run([self.bash,'-c','docker() { "$PYTHON_FAKE" "$DOCKER_FAKE" "$@"; }; export -f docker; bash "$TARGET_SCRIPT"'],env=env,capture_output=True,text=True)
    def calls(self):return [json.loads(line) for line in (self.root/'calls.jsonl').read_text().splitlines()]
    def test_install_and_idempotent_attach_preserve_bridge(self):
        r=self.run_script('deploy.sh');self.assertEqual(r.returncode,0,r.stdout+r.stderr)
        self.assertIn('DEPLOY=PASS',r.stdout)
        r=self.run_script('attach-tunnel.sh');self.assertEqual(r.returncode,0,r.stderr)
        calls=[c['args'] for c in self.calls()]
        self.assertEqual(sum(c[:2]==['network','connect'] for c in calls),1)
        self.assertIn(['network','connect','radiology-atlas_backend','thirsty_agnesi'],calls)
        self.assertFalse(any('disconnect' in c or 'build' in c or 'caddy' in c for c in calls))
    def test_old_local_image_rejected_before_docker(self):
        (self.root/'.env').write_text(self.envtext.replace('ghcr.io/bscongluanbui/radiology-atlas:latest','radiology-atlas-viewer:local'))
        r=self.run_script('deploy.sh');self.assertEqual(r.returncode,2);self.assertIn('CONFIG=FAIL',r.stderr)
        self.assertFalse((self.root/'calls.jsonl').exists())
    def test_failed_pull_never_recreates(self):
        r=self.run_script('update.sh',FAIL_PULL='1');self.assertEqual(r.returncode,1)
        self.assertFalse(any(c['args'][:2]==['compose','up'] for c in self.calls()))
    def test_unhealthy_update_restores_running_image(self):
        r=self.run_script('update.sh',FAIL_UP='1');self.assertEqual(r.returncode,1)
        self.assertIn('ROLLBACK=PASS',r.stdout)
        calls=self.calls()
        self.assertIn(['image','tag','sha256:old-running-image','radiology-atlas-viewer:rollback'],[c['args'] for c in calls])
        up=[c for c in calls if c['args'][:2]==['compose','up']]
        self.assertEqual(len(up),2);self.assertEqual(up[1]['image'],'radiology-atlas-viewer:rollback')
        self.assertIn('never',up[1]['args']);self.assertNotIn('down',[a for c in calls for a in c['args']])

if __name__=='__main__':unittest.main()
