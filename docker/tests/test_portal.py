"""Run against read-only source data and an explicitly separate, empty test-state directory."""
from argparse import ArgumentParser
import hashlib
import json
from pathlib import Path
import re
import sys
import unittest
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from docker.portal import create_app
from docker.cache_store import BoundedCache

parser = ArgumentParser()
parser.add_argument("--data-root", required=True)
parser.add_argument("--state-dir", required=True)
args, remaining = parser.parse_known_args()
PASS = "Test-only!Atlas48password"


class PortalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        state = Path(args.state_dir)
        if state.exists() and any(state.iterdir()):
            raise RuntimeError("Use a new empty --state-dir; never point tests at production state.")
        cls.app = create_app({"TESTING": True, "STATE_DIR": str(state), "DATA_ROOT": args.data_root,
                              "MAINTENANCE_ENABLED": False, "PROXY_HOPS": 0,
                              "TRUSTED_HOSTS": ["atlas.test", "localhost"], "SESSION_COOKIE_SECURE": True})
        cls.auth = cls.app.extensions["auth"]
        cls.repo = cls.app.extensions["repository"]
        cls.admin_id = cls.auth.create_user("owner", PASS, "root", True)
        cls.reader_id = cls.auth.create_user("reader", PASS, modules=["BRAIN/mri-brain"])
        cls.catalogue = cls.repo.catalogue()
        cls.other = next(m["key"] for m in cls.catalogue["modules"] if m["captured"] and m["key"].startswith("HEAD AND NECK/"))
        cls.brain = cls.repo.module("BRAIN/mri-brain")
        series = next(s for s in cls.brain["series"] if any(v["slice_count"] for v in s["variants"]))
        variant = next(v for v in series["variants"] if v["slice_count"])
        cls.query = {"key": "BRAIN/mri-brain", "series": series["directory"], "variant": variant["directory"], "slice": variant["slices"][0]}
        cls.capture = cls.repo.slice(**{"key": cls.query["key"], "series": cls.query["series"], "variant": cls.query["variant"], "number": cls.query["slice"]})
        cls.image_path = cls.repo.data_file(cls.capture["image_url"].removeprefix("/data/"))
        cls.image_hash = hashlib.sha256(cls.image_path.read_bytes()).hexdigest()

    def setUp(self):
        self.client = self.app.test_client()

    def get(self, path, client=None, **kw):
        response = (client or self.client).get(path, base_url="https://atlas.test", **kw)
        response.get_data()
        response.close()
        return response

    def token(self, client=None):
        client = client or self.client
        text = self.get("/login", client).get_data(as_text=True)
        if 'name="csrf"' not in text:
            text = self.get("/account", client).get_data(as_text=True)
        return re.search(r'name="csrf" value="([^"]+)"', text).group(1)

    def post(self, path, data=None, client=None, csrf=True):
        client = client or self.client
        form = dict(data or {})
        if csrf:
            form["csrf"] = self.token(client)
        return client.post(path, data=form, base_url="https://atlas.test")

    def login(self, username="reader", client=None):
        response = self.post("/login", {"username": username, "password": PASS}, client)
        self.assertEqual(response.status_code, 302)
        return response

    def test_01_anonymous_and_static(self):
        for path in ("/api/catalogue", "/api/module?key=BRAIN/mri-brain", self.capture["image_url"]):
            self.assertEqual(self.get(path).status_code, 401, path)
            self.assertEqual(self.client.head(path, base_url="https://atlas.test").status_code, 401)
        self.assertEqual(self.get("/").status_code, 200)
        self.assertNotIn('id="anatomyViewport"', self.get("/").text)
        self.assertEqual(self.get("/healthz").json, {"status": "ok"})
        self.assertEqual(self.get("/portal/static/portal.css").status_code, 200)

    def test_02_login_csrf_cookies_headers(self):
        self.assertEqual(self.post("/login", {"username": "reader", "password": PASS}, csrf=False).status_code, 400)
        self.assertEqual(self.post("/login", {"username": "missing", "password": PASS}).status_code, 401)
        response = self.login()
        cookie = response.headers.get("Set-Cookie", "")
        for flag in ("Secure", "HttpOnly", "SameSite=Lax"):
            self.assertIn(flag, cookie)
        page = self.get("/viewer")
        self.assertIn("no-store", page.headers["Cache-Control"])
        self.assertIn("frame-ancestors 'none'", page.headers["Content-Security-Policy"])
        self.assertIn("/portal/runtime.js", page.text)
        self.assertEqual(self.get("/app.js").data, (ROOT/"offline_anatomy_viewer/app.js").read_bytes())

    def test_03_module_rbac(self):
        self.login()
        result = self.get("/api/catalogue").json
        self.assertEqual([r["key"] for r in result["modules"]], ["BRAIN/mri-brain"])
        for operation in ("module", "slice", "structure", "search", "translations"):
            for key in (self.other, self.other.replace(" ", "_")):
                self.assertEqual(self.get("/api/" + operation + "?" + urlencode({"key": key})).status_code, 403)
        self.assertEqual(self.get("/data/" + self.other.replace(" ", "_") + "/normalised/x.png").status_code, 403)
        self.assertEqual(self.get("/admin").status_code, 403)
        self.assertEqual(self.post("/admin/users", {"username": "hacker", "password": PASS, "role": "admin"}).status_code, 403)

    def test_04_anatomy_parity_and_data(self):
        self.login()
        response = self.get("/api/slice?" + urlencode(self.query))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json, self.capture)
        image = self.get(self.capture["image_url"])
        self.assertEqual(image.status_code, 200)
        self.assertEqual(hashlib.sha256(image.data).hexdigest(), self.image_hash)
        self.assertIn("no-store", image.headers["Cache-Control"])
        self.assertEqual(self.get("/api/module?key=BRAIN/mri-brain").json, self.brain)
        self.assertEqual(self.get("/api/languages").status_code, 200)
        self.assertEqual(self.get("/api/translations?key=BRAIN/mri-brain&lang=vi").status_code, 200)

    def test_05_admin_all_modules_aliases(self):
        self.login("owner")
        self.assertEqual(self.get("/api/catalogue").json["module_count"], self.catalogue["module_count"])
        response = self.get("/api/module?" + urlencode({"key": self.other.replace(" ", "_")}))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["key"], self.other)
        self.assertEqual(self.get("/admin").status_code, 200)

    def test_06_no_source_or_traversal(self):
        self.login("owner")
        for path in ("/server.py", "/docker/auth_store.py", "/.env", "/assets/module-icons/manifest.json", "/data/BRAIN/mri-brain/normalised/structures.json",
                     "/data/BRAIN/mri-brain/../../module_catalogue.json", "/data/BRAIN/mri-brain/%252e%252e/foo.png", "/api/slice?key=BRAIN/mri-brain&slice=bad"):
            self.assertIn(self.get(path).status_code, (400, 404), path)
        self.assertEqual(self.client.get("/", base_url="https://evil.test").status_code, 400)

    def test_07_empty_grants_and_revoke(self):
        uid = self.auth.create_user("empty", PASS)
        self.login("empty")
        self.assertEqual(self.get("/api/catalogue").json["module_count"], 0)
        self.assertEqual(self.get("/api/module?key=BRAIN/mri-brain").status_code, 403)
        self.auth.revoke(uid)
        self.assertEqual(self.get("/api/catalogue").status_code, 401)

    def test_08_expiry_and_disable(self):
        uid = self.auth.create_user("expire", PASS, role="admin")
        self.login("expire")
        with self.auth.connect() as db:
            db.execute("UPDATE sessions SET seen=0 WHERE user_id=?", (uid,))
        self.assertEqual(self.get("/api/catalogue").status_code, 401)
        self.login("expire")
        self.auth.update_user(uid, "admin", False, True, [])
        self.assertEqual(self.get("/api/catalogue").status_code, 401)
        self.assertEqual(self.post("/login", {"username": "expire", "password": PASS}).status_code, 401)

    def test_09_last_admin_and_delete_self(self):
        with self.assertRaises(ValueError):
            self.auth.update_user(self.admin_id, "standard", True, False, [])
        with self.assertRaises(ValueError):
            self.auth.delete_user(self.admin_id, "owner")
        self.assertEqual(self.auth.get_user(self.admin_id)["role"], "root")

    def test_10_password_change_and_logout(self):
        uid = self.auth.create_user("passwordtest", PASS, role="admin")
        second = self.app.test_client()
        self.login("passwordtest")
        self.login("passwordtest", second)
        self.assertEqual(self.post("/account", {"old_password": PASS, "password": PASS+"new"}).status_code, 302)
        self.assertEqual(self.get("/api/catalogue", second).status_code, 401)
        self.assertIsNone(self.auth.login("passwordtest", PASS))
        self.login()
        self.assertIn('"cache"', self.post("/logout").headers["Clear-Site-Data"])
        self.assertEqual(self.get("/api/catalogue").status_code, 401)

    def test_11_login_rate_and_hash_storage(self):
        for _ in range(20):
            self.assertTrue(self.auth.login_allowed("test-rate-ip", "rate-account"))
        self.assertFalse(self.auth.login_allowed("test-rate-ip", "rate-account"))
        with self.auth.connect() as db:
            encoded = db.execute("SELECT password FROM users WHERE id=?", (self.admin_id,)).fetchone()[0]
        self.assertTrue(encoded.startswith("$argon2id$"))
        self.assertNotIn(PASS, encoded)

    def test_12_ui_create_update_reset(self):
        self.login("owner")
        response = self.post("/admin/users", {"username": "uiuser", "password": PASS, "role": "standard", "modules": ["BRAIN/mri-brain"]})
        self.assertEqual(response.status_code, 302)
        user = next(u for u in self.auth.users() if u["username"] == "uiuser")
        reader = self.app.test_client()
        self.login("uiuser", reader)
        self.assertEqual(self.get("/api/catalogue", reader).json["module_count"], 1)
        self.post(f"/admin/users/{user['id']}/update", {"role": "standard", "active": "on", "password": PASS+"reset"})
        self.assertEqual(self.get("/api/catalogue", reader).status_code, 401)
        self.assertEqual(self.auth.get_user(user["id"])["modules"], [])
        self.post(f"/admin/users/{user['id']}/delete")
        self.assertIsNone(self.auth.get_user(user["id"]))

    def test_13_maintenance_budget_and_ttl(self):
        clock = [1]
        cache = BoundedCache(1024, ttl=60, max_entries=2, clock=lambda: clock[0])
        cache["one"] = "x"*100
        cache["two"] = "y"*100
        self.assertIsNotNone(cache.get("one"))
        cache["three"] = "z"*100
        self.assertIsNone(cache.get("two"))
        cache["huge"] = "x"*10000
        self.assertIsNone(cache.get("huge"))
        self.assertLessEqual(cache.bytes, 1024)
        clock[0] += 61
        cache.sweep()
        self.assertEqual(cache.stats()["entries"], 0)
        self.app.extensions["maintenance"]()
        self.login("owner")
        self.assertEqual(self.post("/admin/cache/clear").status_code, 302)
        for item in self.app.extensions["metadata_caches"].values():
            self.assertEqual(item.stats()["bytes"], 0)

    def test_14_retention(self):
        with self.auth.connect() as db:
            db.execute("INSERT INTO audit(at,actor,action,target) VALUES (0,'test','old','test')")
            db.execute("INSERT INTO rates VALUES ('old',0,1)")
        self.auth.maintenance()
        with self.auth.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM audit WHERE at=0").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM rates WHERE key='old'").fetchone()[0], 0)
        self.assertEqual(hashlib.sha256(self.image_path.read_bytes()).hexdigest(), self.image_hash)


if __name__ == "__main__":
    result = unittest.main(argv=[sys.argv[0], *remaining], exit=False)
    ok = result.result.wasSuccessful()
    print(f"PORTAL_TESTS={'PASS' if ok else 'FAIL'}; tests={result.result.testsRun}; data=read-only; passwords=argon2id")
    raise SystemExit(0 if ok else 1)
