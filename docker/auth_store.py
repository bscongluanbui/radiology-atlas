"""Small, transactional account store. No default credentials or plaintext passwords."""
from __future__ import annotations

import hashlib
from contextlib import closing, contextmanager
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import threading
import time

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, InvalidHashError
from .access_policy import ROLE_LABELS

HASHER = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=2)
HASH_SLOTS = threading.BoundedSemaphore(2)
DUMMY_HASH = HASHER.hash(secrets.token_urlsafe(24))
USERNAME = re.compile(r"^[a-z0-9][a-z0-9_.-]{2,47}$")
EMAIL = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
LATEST_SCHEMA = 3


def password_hash(password):
    if not isinstance(password, str) or not 1 <= len(password) <= 256:
        raise ValueError("Mật khẩu cần từ 1 đến 256 ký tự.")
    with HASH_SLOTS:
        return HASHER.hash(password)


def password_matches(encoded, password):
    if not isinstance(password, str) or len(password) > 256:
        return False
    try:
        with HASH_SLOTS:
            return HASHER.verify(encoded or DUMMY_HASH, password)
    except (VerificationError, InvalidHashError):
        return False


class AuthStore:
    def __init__(self, state_dir, clock=time.time):
        self.directory = Path(state_dir).resolve()
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = self.directory / "accounts.sqlite3"
        self.clock = clock
        with self.connect() as db:
            db.execute("PRAGMA auto_vacuum=INCREMENTAL")
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript('''
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY, username TEXT NOT NULL UNIQUE,
                    password TEXT NOT NULL, role TEXT NOT NULL CHECK(role IN ('admin','viewer')),
                    active INTEGER NOT NULL DEFAULT 1, all_modules INTEGER NOT NULL DEFAULT 0,
                    modules TEXT NOT NULL DEFAULT '[]', created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS sessions (
                    token TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    created REAL NOT NULL, seen REAL NOT NULL);
                CREATE INDEX IF NOT EXISTS sessions_user ON sessions(user_id);
                CREATE TABLE IF NOT EXISTS rates (key TEXT PRIMARY KEY, started REAL NOT NULL, count INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS audit (
                    id INTEGER PRIMARY KEY, at REAL NOT NULL, actor TEXT NOT NULL,
                    action TEXT NOT NULL, target TEXT NOT NULL);
            ''')
            self._upgrade_schema(db)
        os.chmod(self.path, 0o600)

    def _upgrade_schema(self, db):
        """Upgrade roles and optional profile fields without changing credentials.

        Legacy role=admin is reserved for Root. New Admin is physically viewer +
        all_modules=1, so old code never grants it account-management privileges.
        Region grants are separate; old code ignores them rather than widening access.
        """
        db.execute("BEGIN IMMEDIATE")
        version = db.execute("PRAGMA user_version").fetchone()[0]
        starting_version = version
        if version > LATEST_SCHEMA:
            raise RuntimeError("Account schema is newer than this application.")
        columns = {r[1] for r in db.execute("PRAGMA table_info(users)")}
        if version < 2:
            backup = self.directory / "accounts.before-rbac-v2.sqlite3"
            if db.execute("SELECT COUNT(*) FROM users").fetchone()[0] and not backup.exists():
                # A second read connection snapshots committed v1 rows while this
                # connection's RESERVED lock prevents another writer during upgrade.
                fd = os.open(backup, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                os.close(fd)
                try:
                    with closing(sqlite3.connect(self.path)) as source, closing(sqlite3.connect(backup)) as destination:
                        source.backup(destination)
                except Exception:
                    backup.unlink()
                    raise
                os.chmod(backup, 0o600)
            if "access_role" not in columns:
                db.execute("ALTER TABLE users ADD COLUMN access_role TEXT NOT NULL DEFAULT 'standard' CHECK(access_role IN ('root','admin','standard'))")
            if "regions" not in columns:
                db.execute("ALTER TABLE users ADD COLUMN regions TEXT NOT NULL DEFAULT '[]'")
            db.execute("""UPDATE users SET access_role=CASE WHEN role='admin' THEN 'root'
                          WHEN all_modules=1 THEN 'admin' ELSE 'standard' END, regions='[]'""")
            # Existing passwords/IDs/module grants remain intact. Re-login under
            # the new policy; upgrading again after v49 clears stale region grants.
            db.execute("DELETE FROM sessions")
            db.execute("PRAGMA user_version=2")
            self._audit(db, "migration", "upgrade-roles-v2", "root/admin/standard")
            version = 2
        if version < 3:
            backup = self.directory / "accounts.before-profile-v3.sqlite3"
            # A database upgraded directly from v1 already has an exact v1
            # snapshot above.  Create this second snapshot only when v2 was the
            # committed schema at startup; otherwise another connection would
            # still see v1 while this transaction is applying the v2 changes.
            if starting_version == 2 and db.execute("SELECT COUNT(*) FROM users").fetchone()[0] and not backup.exists():
                fd = os.open(backup, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                os.close(fd)
                try:
                    with closing(sqlite3.connect(self.path)) as source, closing(sqlite3.connect(backup)) as destination:
                        source.backup(destination)
                except Exception:
                    backup.unlink(missing_ok=True)
                    raise
                os.chmod(backup, 0o600)
            columns = {r[1] for r in db.execute("PRAGMA table_info(users)")}
            if "email" not in columns:
                db.execute("ALTER TABLE users ADD COLUMN email TEXT NOT NULL DEFAULT ''")
            if "birth_year" not in columns:
                db.execute("ALTER TABLE users ADD COLUMN birth_year INTEGER")
            if "avatar" not in columns:
                db.execute("ALTER TABLE users ADD COLUMN avatar TEXT NOT NULL DEFAULT ''")
            db.execute("PRAGMA user_version=3")
            self._audit(db, "migration", "upgrade-profile-v3", "email/birth-year/avatar")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA journal_size_limit=8388608")
        # Persistent records have a hard ceiling in addition to retention rules.
        db.execute("PRAGMA max_page_count=16384")
        try:
            with db:
                yield db
        finally:
            db.close()

    def secret(self):
        path = self.directory / "session.key"
        if not path.exists():
            try:
                fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError:
                pass
            else:
                with os.fdopen(fd, "wb") as stream:
                    stream.write(secrets.token_bytes(64))
        value = path.read_bytes()
        if len(value) != 64:
            raise RuntimeError("Invalid session.key; restore the state backup.")
        return value

    @staticmethod
    def public(row):
        if row is None:
            return None
        item = dict(row)
        item.pop("password", None)
        item["modules"] = json.loads(item["modules"])
        item["role"] = item.pop("access_role")
        item["regions"] = json.loads(item["regions"])
        return item

    def users(self):
        with self.connect() as db:
            return [self.public(row) for row in db.execute("SELECT * FROM users ORDER BY username")]

    def get_user(self, uid):
        with self.connect() as db:
            return self.public(db.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone())

    def _audit(self, db, actor, action, target):
        db.execute("INSERT INTO audit(at,actor,action,target) VALUES (?,?,?,?)",
                   (self.clock(), str(actor)[:48], action, str(target)[:48]))
        db.execute("DELETE FROM audit WHERE id NOT IN (SELECT id FROM audit ORDER BY id DESC LIMIT 10000)")

    @staticmethod
    def validate_policy(role, modules, regions=()):
        if role not in ROLE_LABELS:
            raise ValueError("Vai trò không hợp lệ.")
        if len(modules) > 2000 or any(not isinstance(m, str) or len(m) > 180 for m in modules):
            raise ValueError("Danh sách module không hợp lệ.")
        if len(regions) > 100 or any(not isinstance(r, str) or not 1 <= len(r) <= 80 or any(c in r for c in '/\\\r\n') for r in regions):
            raise ValueError("Danh sách vùng không hợp lệ.")

    def create_user(self, username, password, role="standard", all_modules=False, modules=(), actor="console", *, regions=()):
        username = username.strip().lower()
        if not USERNAME.fullmatch(username):
            raise ValueError("Tên đăng nhập: 3–48 ký tự a-z, 0-9, dấu chấm, gạch ngang hoặc gạch dưới.")
        self.validate_policy(role, modules, regions)
        if role == "standard" and all_modules:
            raise ValueError("Standard cần được cấp vùng cụ thể; chọn Admin nếu cần xem toàn bộ.")
        encoded = password_hash(password)
        try:
            with self.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                if db.execute("SELECT COUNT(*) FROM users").fetchone()[0] >= 500:
                    raise ValueError("Đã đạt giới hạn 500 tài khoản.")
                uid = db.execute("INSERT INTO users(username,password,role,all_modules,modules,created,access_role,regions) VALUES (?,?,?,?,?,?,?,?)",
                                 (username, encoded, "admin" if role == "root" else "viewer", int(role in ("root", "admin")),
                                  json.dumps(sorted(set(modules))), self.clock(), role, json.dumps(sorted(set(regions))))).lastrowid
                self._audit(db, actor, "create-user", username)
                return uid
        except sqlite3.IntegrityError as error:
            raise ValueError("Tên đăng nhập đã tồn tại.") from error

    def update_user(self, uid, role, active, all_modules, modules, password="", actor="console", *, regions=()):
        self.validate_policy(role, modules, regions)
        if role == "standard" and all_modules:
            raise ValueError("Standard cần được cấp vùng cụ thể; chọn Admin nếu cần xem toàn bộ.")
        encoded = password_hash(password) if password else None
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            user = db.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
            if not user:
                raise ValueError("Tài khoản không tồn tại.")
            if user["access_role"] == "root" and user["active"] and (role != "root" or not active):
                if db.execute("SELECT COUNT(*) FROM users WHERE access_role='root' AND active=1").fetchone()[0] <= 1:
                    raise ValueError("Cần giữ ít nhất một Root đang hoạt động.")
            db.execute("UPDATE users SET role=?,access_role=?,active=?,all_modules=?,modules=?,regions=? WHERE id=?",
                       ("admin" if role == "root" else "viewer", role, int(bool(active)), int(role in ("root", "admin")),
                        json.dumps(sorted(set(modules))), json.dumps(sorted(set(regions))), uid))
            if encoded:
                db.execute("UPDATE users SET password=? WHERE id=?", (encoded, uid))
            db.execute("DELETE FROM sessions WHERE user_id=?", (uid,))
            self._audit(db, actor, "update-user/revoke-sessions", user["username"])

    def update_profile(self, uid, email="", birth_year=""):
        email = str(email or "").strip().lower()
        if len(email) > 254 or (email and not EMAIL.fullmatch(email)):
            raise ValueError("Địa chỉ email không hợp lệ.")
        if birth_year in (None, ""):
            year = None
        else:
            try:
                year = int(birth_year)
            except (TypeError, ValueError) as error:
                raise ValueError("Năm sinh cần là một số hợp lệ.") from error
            current_year = time.gmtime(self.clock()).tm_year
            if not 1900 <= year <= current_year:
                raise ValueError(f"Năm sinh cần nằm trong khoảng 1900–{current_year}.")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            user = db.execute("SELECT username FROM users WHERE id=?", (uid,)).fetchone()
            if not user:
                raise ValueError("Tài khoản không tồn tại.")
            db.execute("UPDATE users SET email=?,birth_year=? WHERE id=?", (email, year, uid))
            self._audit(db, user["username"], "update-profile", user["username"])

    def set_avatar(self, uid, payload, suffix):
        if suffix not in {".png", ".jpg"}:
            raise ValueError("Ảnh đại diện cần là PNG hoặc JPEG.")
        directory = self.directory / "avatars"
        directory.mkdir(mode=0o700, exist_ok=True)
        filename = f"user-{int(uid)}-{secrets.token_hex(8)}{suffix}"
        path = directory / filename
        temporary = directory / (filename + ".tmp")
        with open(temporary, "xb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
        old = ""
        try:
            with self.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                user = db.execute("SELECT username,avatar FROM users WHERE id=?", (uid,)).fetchone()
                if not user:
                    raise ValueError("Tài khoản không tồn tại.")
                old = user["avatar"]
                db.execute("UPDATE users SET avatar=? WHERE id=?", (filename, uid))
                self._audit(db, user["username"], "update-avatar", user["username"])
        except Exception:
            path.unlink(missing_ok=True)
            raise
        if old and old != filename:
            self._unlink_avatar(old)
        return filename

    def avatar_path(self, uid):
        with self.connect() as db:
            row = db.execute("SELECT avatar FROM users WHERE id=?", (uid,)).fetchone()
        if not row or not row["avatar"]:
            return None
        filename = row["avatar"]
        if Path(filename).name != filename or not filename.startswith(f"user-{int(uid)}-"):
            return None
        path = self.directory / "avatars" / filename
        return path if path.is_file() else None

    def remove_avatar(self, uid):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            user = db.execute("SELECT username,avatar FROM users WHERE id=?", (uid,)).fetchone()
            if not user:
                raise ValueError("Tài khoản không tồn tại.")
            old = user["avatar"]
            db.execute("UPDATE users SET avatar='' WHERE id=?", (uid,))
            self._audit(db, user["username"], "remove-avatar", user["username"])
        if old:
            self._unlink_avatar(old)

    def _unlink_avatar(self, filename):
        if filename and Path(filename).name == filename:
            (self.directory / "avatars" / filename).unlink(missing_ok=True)

    def delete_user(self, uid, actor):
        avatar = ""
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            user = db.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
            if not user:
                raise ValueError("Tài khoản không tồn tại.")
            if user["username"] == actor:
                raise ValueError("Hãy dùng quản trị viên khác để xóa tài khoản này.")
            if user["access_role"] == "root" and user["active"]:
                if db.execute("SELECT COUNT(*) FROM users WHERE access_role='root' AND active=1").fetchone()[0] <= 1:
                    raise ValueError("Cần giữ ít nhất một Root đang hoạt động.")
            avatar = user["avatar"]
            db.execute("DELETE FROM users WHERE id=?", (uid,))
            self._audit(db, actor, "delete-user", user["username"])
        if avatar:
            self._unlink_avatar(avatar)

    def login_allowed(self, ip, username):
        now = self.clock()
        allowed = True
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM rates WHERE started<?", (now - 900,))
            for prefix, value, window, limit in (("ip", ip, 60, 20), ("user", username, 900, 20)):
                key = prefix + ":" + hashlib.sha256(value.encode()).hexdigest()
                row = db.execute("SELECT * FROM rates WHERE key=?", (key,)).fetchone()
                if not row or row["started"] <= now - window:
                    db.execute("INSERT OR REPLACE INTO rates VALUES (?,?,1)", (key, now))
                else:
                    db.execute("UPDATE rates SET count=count+1 WHERE key=?", (key,))
                    allowed &= row["count"] < limit
            db.execute("DELETE FROM rates WHERE key NOT IN (SELECT key FROM rates ORDER BY started DESC LIMIT 4096)")
        return allowed

    def login(self, username, password):
        with self.connect() as db:
            row = db.execute("SELECT * FROM users WHERE username=?", (username.lower(),)).fetchone()
        valid = password_matches(row["password"] if row else None, password)
        if not valid or not row or not row["active"]:
            return None
        token = secrets.token_urlsafe(48)
        now = self.clock()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            # Reject a concurrent password/policy change between verify and insert.
            current = db.execute("SELECT * FROM users WHERE id=?", (row["id"],)).fetchone()
            if not current or dict(current) != dict(row):
                return None
            db.execute("INSERT INTO sessions VALUES (?,?,?,?)", (self.digest(token), row["id"], now, now))
            db.execute("DELETE FROM sessions WHERE user_id=? AND token NOT IN (SELECT token FROM sessions WHERE user_id=? ORDER BY created DESC,rowid DESC LIMIT 5)", (row["id"], row["id"]))
            self._audit(db, username, "login", username)
        return token

    @staticmethod
    def digest(token):
        return hashlib.sha256(token.encode()).hexdigest()

    def session_user(self, token, idle=1800, lifetime=28800):
        if not token:
            return None
        now = self.clock()
        with self.connect() as db:
            row = db.execute("SELECT u.*,s.seen,s.created AS session_created FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token=?", (self.digest(token),)).fetchone()
            if not row or not row["active"] or row["seen"] < now-idle or row["session_created"] < now-lifetime:
                db.execute("DELETE FROM sessions WHERE token=?", (self.digest(token),))
                return None
            if row["seen"] < now-60:
                db.execute("UPDATE sessions SET seen=? WHERE token=?", (now, self.digest(token)))
            return self.public(row)

    def revoke(self, uid, actor="console"):
        with self.connect() as db:
            db.execute("DELETE FROM sessions WHERE user_id=?", (uid,))
            self._audit(db, actor, "revoke-sessions", uid)

    def logout(self, token):
        with self.connect() as db:
            db.execute("DELETE FROM sessions WHERE token=?", (self.digest(token or ""),))

    def change_password(self, uid, old, new):
        with self.connect() as db:
            row = db.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
        if not row or not password_matches(row["password"], old):
            raise ValueError("Mật khẩu hiện tại chưa đúng.")
        encoded = password_hash(new)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            changed = db.execute("UPDATE users SET password=? WHERE id=? AND password=?", (encoded, uid, row["password"])).rowcount
            if not changed:
                raise ValueError("Tài khoản vừa thay đổi; hãy đăng nhập lại.")
            db.execute("DELETE FROM sessions WHERE user_id=?", (uid,))
            self._audit(db, row["username"], "change-password", row["username"])

    def maintenance(self, idle=1800, lifetime=28800):
        now = self.clock()
        with self.connect() as db:
            db.execute("DELETE FROM sessions WHERE seen<? OR created<?", (now-idle, now-lifetime))
            db.execute("DELETE FROM rates WHERE started<?", (now-900,))
            db.execute("DELETE FROM audit WHERE at<?", (now-30*86400,))
            db.commit()
            db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            db.execute("PRAGMA incremental_vacuum(128)")

    def recent_audit(self):
        with self.connect() as db:
            return [dict(row) for row in db.execute("SELECT * FROM audit ORDER BY id DESC LIMIT 50")]
