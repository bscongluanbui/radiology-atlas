"""Self-service revocation is authenticated, CSRF protected and user scoped."""
import sys
import shutil
import subprocess
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_single_session


class LogoutAllTests(test_single_session.SingleSessionHTTPTests):
    @unittest.skipUnless(shutil.which('node'), 'Node.js is required for the frontend regression')
    def test_viewer_session_frontend(self):
        for name in ('test_viewer_session.cjs', 'test_latest_frame.cjs', 'test_adaptive_preload.cjs'):
            result = subprocess.run([shutil.which('node'), str(Path(__file__).with_name(name))],
                                    capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_all_own_sessions_and_lease_revoked_other_user_unchanged(self):
        auth = self.app.extensions['auth']
        other = auth.create_user('other', 'x', regions=['BRAIN'])
        other_token = auth.login('other', 'x')
        self.assertEqual(auth.viewer_session(other_token, 'c'*32, 'acquire'), 'ok')
        self.assertEqual(self.post(self.first, self.h1).status_code, 200)
        response = self.first.post('/account/logout-all', base_url='https://atlas.test',
                                   headers=self.h1, data={'uid': other})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers['Location'], '/login')
        self.assertEqual(response.headers['Clear-Site-Data'], '"cache"')
        for client, headers in ((self.first, self.h1), (self.second, self.h2)):
            self.assertEqual(self.post(client, headers).status_code, 401)
            self.assertEqual(self.get(client, '/api/catalogue').status_code, 401)
        self.assertIsNotNone(auth.session_user(other_token))
        self.assertEqual(auth.viewer_session(other_token, 'c'*32, 'check'), 'ok')
        with auth.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM viewer_leases').fetchone()[0], 1)

    def test_post_csrf_and_login_required(self):
        self.assertIn(self.get(self.first, '/account/logout-all').status_code, (404, 405))
        response = self.first.post('/account/logout-all', base_url='https://atlas.test')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.post(self.first, self.h1).status_code, 200)
        anonymous = self.app.test_client()
        response = anonymous.post('/account/logout-all', base_url='https://atlas.test', headers=self.h1)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.post(self.first, self.h1, 'heartbeat').status_code, 200)

    def test_account_contains_csrf_protected_action(self):
        page = self.get(self.first, '/account').text
        self.assertIn('action="/account/logout-all" method="post"', page)
        self.assertIn('Đăng xuất tất cả phiên', page)


if __name__ == '__main__':
    unittest.main()
