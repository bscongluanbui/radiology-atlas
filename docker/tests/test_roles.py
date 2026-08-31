"""Root/Admin/Standard, regional authorization and v1 account migration regression tests."""
from argparse import ArgumentParser
from contextlib import closing
from pathlib import Path
import hashlib,json,re,sqlite3,sys,unittest
from unittest.mock import patch
from urllib.parse import urlencode
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from docker.auth_store import AuthStore,password_hash
from docker.access_policy import can_view_module,can_manage
from docker.portal import create_app
from docker import manage

parser=ArgumentParser();parser.add_argument('--data-root',required=True);parser.add_argument('--state-dir',required=True)
args,rest=parser.parse_known_args()
STATE=Path(args.state_dir);PASSWORD='Roles-test-only!50'
if STATE.exists() and any(STATE.iterdir()):raise RuntimeError('Use a fresh isolated test state.')


class RolesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=create_app({'TESTING':True,'STATE_DIR':str(STATE/'app'),'DATA_ROOT':args.data_root,'MAINTENANCE_ENABLED':False,'PROXY_HOPS':0,'TRUSTED_HOSTS':['atlas.test']})
        cls.auth=cls.app.extensions['auth'];cls.repo=cls.app.extensions['repository']
        cls.root_id=cls.auth.create_user('chief',PASSWORD,'root')
        cls.admin_id=cls.auth.create_user('allreader',PASSWORD,'admin')
        cls.standard_id=cls.auth.create_user('brainreader',PASSWORD,regions=['BRAIN'])
        cls.auth.create_user('headreader',PASSWORD,regions=['HEAD AND NECK'])
        cls.auth.create_user('nogrants',PASSWORD)
        cls.catalogue=cls.repo.catalogue()
        brain=cls.repo.module('BRAIN/mri-brain');series=next(s for s in brain['series'] if s['slice_count']);variant=next(v for v in series['variants'] if v['slice_count'])
        cls.query={'key':'BRAIN/mri-brain','series':series['directory'],'variant':variant['directory'],'slice':variant['slices'][0]}
        cls.slice=cls.repo.slice(cls.query['key'],cls.query['series'],cls.query['variant'],cls.query['slice'])

    def setUp(self):self.client=self.app.test_client()
    def get(self,path,method='GET'):
        r=self.client.open(path,method=method,base_url='https://atlas.test');r.get_data();r.close();return r
    def csrf(self):return re.search(r'name="csrf" value="([^"]+)"',self.get('/').text).group(1)
    def post(self,path,data=None):
        form={'csrf':self.csrf(),**(data or {})};return self.client.post(path,data=form,base_url='https://atlas.test')
    def login(self,name):self.assertEqual(self.post('/login',{'username':name,'password':PASSWORD}).status_code,302)

    def test_01_root_and_admin_matrix(self):
        for user,manage_status in [('chief',200),('allreader',403)]:
            self.client=self.app.test_client();self.login(user)
            self.assertEqual(self.get('/api/catalogue').json['module_count'],84)
            self.assertEqual(self.get('/anatomy').text.count('data-module-card '),84)
            self.assertEqual(self.get('/viewer?key=HEAD_AND_NECK/ct-face').status_code,200)
            self.assertEqual(self.get('/admin').status_code,manage_status)
            self.assertEqual('href="/admin"' in self.get('/').text,user=='chief')
            self.assertEqual('href="/admin"' in self.get('/account').text,user=='chief')

    def test_02_standard_region_all_endpoints(self):
        self.login('brainreader')
        rows=self.get('/api/catalogue').json['modules'];self.assertEqual(len(rows),9)
        self.assertEqual({r['region'] for r in rows},{'BRAIN'})
        self.assertEqual(self.get('/anatomy').text.count('data-module-card '),9)
        self.assertEqual(self.get('/api/slice?'+urlencode(self.query)).status_code,200)
        self.assertEqual(self.get(self.slice['image_url']).status_code,200)
        for route in ['/viewer?key=HEAD_AND_NECK/ct-face','/portal/runtime.js?module=HEAD_AND_NECK/ct-face',
                      '/data/HEAD_AND_NECK/ct-face/normalised/x.png','/assets/module-icons/ct-face.png']:
            for method in ('GET','HEAD'):self.assertEqual(self.get(route,method).status_code,403,route)
        for op in ('module','slice','structure','search','translations'):
            for key in ('HEAD AND NECK/ct-face','HEAD_AND_NECK/ct-face'):
                self.assertEqual(self.get('/api/'+op+'?'+urlencode({'key':key})).status_code,403)
        self.assertEqual(self.get('/assets/module-icons/mri-brain.png').status_code,200)

    def test_03_regional_space_alias_and_no_grants(self):
        self.login('headreader')
        self.assertEqual(self.get('/api/catalogue').json['module_count'],16)
        for key in ('HEAD AND NECK/ct-face','HEAD_AND_NECK/ct-face'):
            self.assertEqual(self.get('/api/module?'+urlencode({'key':key})).status_code,200)
        self.assertEqual(self.get('/api/module?key=BRAIN/mri-brain').status_code,403)
        self.client=self.app.test_client();self.login('nogrants')
        self.assertEqual(self.get('/api/catalogue').json['module_count'],0)
        self.assertEqual(self.get('/viewer').status_code,403)
        self.assertEqual(self.get('/anatomy').status_code,403)
        self.assertEqual(self.get('/assets/module-icons/mri-brain.png').status_code,403)

    def test_04_admin_and_standard_cannot_manage(self):
        for name in ('allreader','brainreader'):
            self.client=self.app.test_client();self.login(name)
            for path in ('/admin/users',f'/admin/users/{self.root_id}/update',f'/admin/users/{self.root_id}/delete',f'/admin/users/{self.root_id}/revoke','/admin/cache/clear'):
                self.assertEqual(self.post(path,{'username':'escalated','password':PASSWORD,'role':'root','active':'on','regions':'THORAX'}).status_code,403)
        self.assertEqual(self.auth.get_user(self.root_id)['role'],'root')
        self.assertFalse(any(u['username']=='escalated' for u in self.auth.users()))

    def test_05_root_create_standard_and_grant_regions(self):
        self.login('chief')
        self.assertEqual(self.post('/admin/users',{'username':'regional','password':PASSWORD,'role':'standard','regions':['BRAIN','THORAX']}).status_code,302)
        user=next(u for u in self.auth.users() if u['username']=='regional')
        self.assertEqual(user['regions'],['BRAIN','THORAX']);self.assertFalse(user['all_modules'])
        self.client=self.app.test_client();self.login('regional')
        rows=self.get('/api/catalogue').json['modules'];self.assertEqual(len(rows),20)
        self.assertEqual({r['region'] for r in rows},{'BRAIN','THORAX'})

    def test_06_client_all_modules_flag_is_ignored(self):
        self.login('chief')
        self.post('/admin/users',{'username':'forgedflag','password':PASSWORD,'role':'standard','all_modules':'on'})
        user=next(u for u in self.auth.users() if u['username']=='forgedflag')
        self.assertEqual(user['role'],'standard');self.assertFalse(user['all_modules'])
        self.assertFalse(can_view_module(user,'BRAIN/mri-brain'))
        for invalid in [{'role':'superroot'},{'role':'standard','regions':['EVERYTHING']},{'role':'standard','regions':['../BRAIN']},{'role':'standard','modules':['UNKNOWN/nope']}]:
            self.post('/admin/users',{'username':'badpolicy','password':PASSWORD,**invalid})
            self.assertFalse(any(u['username']=='badpolicy' for u in self.auth.users()))

    def test_07_revoke_region_immediate_and_future_modules(self):
        uid=self.auth.create_user('revokedregion',PASSWORD,regions=['BRAIN'])
        self.login('revokedregion');self.assertEqual(self.get('/api/catalogue').json['module_count'],9)
        self.assertTrue(can_view_module(self.auth.get_user(uid),'BRAIN/future-module'))
        self.auth.update_user(uid,'standard',True,False,[],regions=['THORAX'])
        self.assertEqual(self.get('/api/catalogue').status_code,401)
        self.login('revokedregion');self.assertEqual(self.get('/api/module?key=BRAIN/mri-brain').status_code,403)
        self.assertEqual({r['region'] for r in self.get('/api/catalogue').json['modules']},{'THORAX'})

    def test_08_last_root_and_privilege_changes(self):
        for role,active in [('admin',True),('standard',True),('root',False)]:
            with self.assertRaisesRegex(ValueError,'Root'):self.auth.update_user(self.root_id,role,active,False,[])
        with self.assertRaisesRegex(ValueError,'Root'):self.auth.delete_user(self.root_id,'console')
        extra=self.auth.create_user('otherroot',PASSWORD,'root')
        token=self.auth.login('otherroot',PASSWORD)
        self.auth.update_user(extra,'admin',True,False,[])
        self.assertIsNone(self.auth.session_user(token));self.assertFalse(can_manage(self.auth.get_user(extra)))
        self.assertTrue(can_manage(self.auth.get_user(self.root_id)))

    def test_09_root_page_regions_and_credentials_hidden(self):
        self.login('chief');page=self.get('/admin')
        self.assertEqual(page.status_code,200)
        for role in ('root','admin','standard'):self.assertIn(f'value="{role}"',page.text)
        for region in {r['region'] for r in self.catalogue['modules']}:self.assertIn(f'name="regions" value="{region}"',page.text)
        self.assertNotIn('name="all_modules"',page.text)
        self.assertNotIn(PASSWORD,page.text);self.assertNotIn('$argon2',page.text)

    def test_10_deny_unknown_role_and_legacy_union(self):
        self.assertFalse(can_view_module({'active':1,'role':'unknown','all_modules':1},'BRAIN/mri-brain'))
        self.assertFalse(can_view_module({'active':1,'role':'standard','all_modules':1},'BRAIN/mri-brain'))
        self.assertFalse(can_view_module({'active':0,'role':'root'},'BRAIN/mri-brain'))
        user={'active':1,'role':'standard','regions':['THORAX'],'modules':['BRAIN/mri-brain']}
        self.assertTrue(can_view_module(user,'THORAX/lungs'));self.assertTrue(can_view_module(user,'BRAIN/mri-brain'))
        self.assertFalse(can_view_module(user,'BRAIN/ct-brain'))

    def test_11_cli_create_root_admin_and_reset_preserves_regions(self):
        state=STATE/'cli'
        for command,name in [('create-root','cliowner'),('create-admin','clireader')]:
            with patch.object(sys,'argv',['manage.py','--state-dir',str(state),command,'--username',name]),patch.object(manage,'secret',return_value=PASSWORD):
                self.assertEqual(manage.main(),0)
        store=AuthStore(state);users={u['username']:u for u in store.users()}
        self.assertEqual(users['cliowner']['role'],'root');self.assertEqual(users['clireader']['role'],'admin')
        uid=store.create_user('regionalcli',PASSWORD,regions=['BRAIN'])
        with patch.object(sys,'argv',['manage.py','--state-dir',str(state),'reset-password','--username','regionalcli']),patch.object(manage,'secret',return_value=PASSWORD+'new'):
            self.assertEqual(manage.main(),0)
        self.assertEqual(store.get_user(uid)['regions'],['BRAIN'])
        self.assertIsNotNone(store.login('regionalcli',PASSWORD+'new'))


class MigrationTests(unittest.TestCase):
    def test_12_v1_migration_backup_and_idempotence(self):
        state=STATE/'migration';state.mkdir(parents=True)
        encoded=password_hash(PASSWORD)
        with closing(sqlite3.connect(state/'accounts.sqlite3')) as db:
            db.executescript("""CREATE TABLE users(id INTEGER PRIMARY KEY,username TEXT UNIQUE NOT NULL,password TEXT NOT NULL,role TEXT NOT NULL CHECK(role IN ('admin','viewer')),active INTEGER NOT NULL DEFAULT 1,all_modules INTEGER NOT NULL DEFAULT 0,modules TEXT NOT NULL DEFAULT '[]',created REAL NOT NULL); PRAGMA user_version=1;""")
            for uid,name,role,all_modules,modules in [(1,'oldowner','admin',0,[]),(2,'oldlimited','viewer',0,['BRAIN/mri-brain']),(3,'oldfull','viewer',1,[])]:
                db.execute('INSERT INTO users VALUES (?,?,?,?,1,?,?,1)',(uid,name,encoded,role,all_modules,json.dumps(modules)))
            db.commit()
        store=AuthStore(state);users={u['username']:u for u in store.users()}
        self.assertEqual(users['oldowner']['role'],'root');self.assertEqual(users['oldfull']['role'],'admin')
        self.assertEqual(users['oldlimited']['role'],'standard');self.assertEqual(users['oldlimited']['regions'],[])
        self.assertEqual(users['oldlimited']['modules'],['BRAIN/mri-brain'])
        with store.connect() as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],2)
            self.assertTrue(all(r[0]==encoded for r in db.execute('SELECT password FROM users')))
            self.assertFalse(db.execute('PRAGMA foreign_key_check').fetchall())
        backup=state/'accounts.before-rbac-v2.sqlite3';digest=hashlib.sha256(backup.read_bytes()).hexdigest()
        with closing(sqlite3.connect(backup)) as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],1)
            self.assertNotIn('access_role',{r[1] for r in db.execute('PRAGMA table_info(users)')})
            self.assertEqual(db.execute('SELECT role FROM users WHERE id=1').fetchone()[0],'admin')
        token=store.login('oldowner',PASSWORD);store2=AuthStore(state)
        self.assertEqual(store2.session_user(token)['role'],'root')
        self.assertEqual(hashlib.sha256(backup.read_bytes()).hexdigest(),digest)

    def test_13_old_source_compatibility_is_fail_closed(self):
        store=AuthStore(STATE/'compat')
        store.create_user('newroot',PASSWORD,'root');store.create_user('newadmin',PASSWORD,'admin')
        store.create_user('newstandard',PASSWORD,regions=['BRAIN'])
        with store.connect() as db:
            rows={r['username']:dict(r) for r in db.execute('SELECT * FROM users')}
            self.assertEqual(rows['newroot']['role'],'admin')
            self.assertEqual(rows['newadmin']['role'],'viewer');self.assertEqual(rows['newadmin']['all_modules'],1)
            self.assertEqual(rows['newstandard']['role'],'viewer');self.assertEqual(rows['newstandard']['all_modules'],0)
            # v49 startup writes user_version=1. Re-upgrade must not reuse stale regions.
            db.execute('PRAGMA user_version=1')
        again=AuthStore(STATE/'compat')
        self.assertEqual(again.get_user(rows['newstandard']['id'])['regions'],[])
        self.assertEqual(again.get_user(rows['newadmin']['id'])['role'],'admin')


if __name__=='__main__':
    result=unittest.main(argv=[sys.argv[0],*rest],exit=False).result
    print(f'ROLES_TESTS={"PASS" if result.wasSuccessful() else "FAIL"}; tests={result.testsRun}; root_only_admin,admin_all,standard_regions,API_images_icons,revocation,migration,CLI')
    raise SystemExit(0 if result.wasSuccessful() else 1)
