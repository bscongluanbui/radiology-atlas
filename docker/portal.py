"""Production gateway: import the local viewer, enforce authentication on every resource."""
from __future__ import annotations

import atexit
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import secrets
import sys
import threading
from urllib.parse import parse_qs, urlencode, urlsplit

from flask import Flask, abort, flash, g, jsonify, redirect, render_template, request, send_file, session, url_for
from werkzeug.exceptions import HTTPException, SecurityError
from werkzeug.middleware.proxy_fix import ProxyFix

from .auth_store import AuthStore
from .cache_store import BoundedCache
from .site_catalogue import REGIONS, MODALITY_GROUPS, group_catalogue
from .access_policy import ROLE_LABELS, can_manage, can_view_module

ROOT = Path(__file__).resolve().parent.parent
VIEWER = ROOT / "offline_anatomy_viewer"
sys.path.insert(0, str(VIEWER))
from server import AnatomyRepository, load_json, safe_key  # shared, unchanged anatomy code
from anatomy_language import languages, load_pack


def integer_env(name, default, low, high):
    value = int(os.environ.get(name, default))
    if not low <= value <= high:
        raise ValueError(f"{name} must be between {low} and {high}")
    return value


def create_app(config=None):
    app = Flask(__name__, static_url_path="/portal/static")
    app.config.update(
        STATE_DIR=os.environ.get("STATE_DIR", "/state"),
        DATA_ROOT=os.environ.get("DATA_ROOT", "/data"),
        SESSION_COOKIE_NAME="atlas_session", SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SECURE=True, SESSION_COOKIE_SAMESITE="Lax",
        MAX_CONTENT_LENGTH=65536, MAX_FORM_MEMORY_SIZE=65536, MAX_FORM_PARTS=2000,
        TRUSTED_HOSTS=[h for h in os.environ.get("ALLOWED_HOSTS", "localhost,127.0.0.1").split(",") if h],
        PROXY_HOPS=1, MAINTENANCE_ENABLED=True,
        CACHE_MIB=integer_env("METADATA_CACHE_MIB", 512, 8, 512),
        CACHE_TTL=integer_env("CACHE_TTL_SECONDS", 1800, 60, 86400),
        BROWSER_CACHE_MIB=integer_env("BROWSER_CACHE_MIB", 512, 16, 1024),
        DECODED_IMAGES=integer_env("DECODED_IMAGE_CACHE", 32, 8, 64),
        DECODE_FORWARD=integer_env("DECODE_FORWARD", 20, 1, 48),
        DECODE_BACKWARD=integer_env("DECODE_BACKWARD", 11, 0, 24),
        DECODE_CONCURRENCY=integer_env("DECODE_CONCURRENCY", 2, 1, 4),
        PRELOAD_CONCURRENCY=integer_env("PRELOAD_CONCURRENCY", 2, 1, 4),
        IMAGE_CONCURRENCY=integer_env("IMAGE_CONCURRENCY", 4, 2, 8),
        SESSION_IDLE=integer_env("SESSION_IDLE_SECONDS", 1800, 300, 86400),
        SESSION_LIFETIME=integer_env("SESSION_LIFETIME_SECONDS", 28800, 900, 604800),
    )
    if config:
        app.config.update(config)
    auth = AuthStore(app.config["STATE_DIR"])
    app.secret_key = auth.secret()
    app.config["PERMANENT_SESSION_LIFETIME"] = app.config["SESSION_LIFETIME"]
    repository = AnatomyRepository(Path(app.config["DATA_ROOT"]))
    repository.validate()
    module_icons = (load_json(VIEWER / "assets/module-icons/manifest.json", {}) or {}).get("icons", {})
    repo_lock = threading.RLock()
    caches = {name: BoundedCache(app.config["CACHE_MIB"] * 1024**2 // 3, ttl=app.config["CACHE_TTL"])
              for name in ("_structure_cache", "_point_cache", "_cross_reference_cache")}
    for name, cache in caches.items():
        setattr(repository, name, cache)
    app.extensions.update(auth=auth, repository=repository, metadata_caches=caches)
    if app.config["PROXY_HOPS"]:
        # Only Caddy or the trusted cloudflared connector reaches this internal network.
        # Port 8080 is never published; preserve Host and trust exactly one proxy hop.
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=0, x_port=0)

    stop = threading.Event()
    app.extensions["maintenance_stop"] = stop

    def maintenance():
        for cache in caches.values():
            cache.sweep()
        auth.maintenance(app.config["SESSION_IDLE"], app.config["SESSION_LIFETIME"])

    def maintain_loop():
        while not stop.wait(60):
            try:
                maintenance()
            except Exception:
                app.logger.exception("Cache/account retention maintenance failed")

    app.extensions["maintenance"] = maintenance
    if app.config["MAINTENANCE_ENABLED"]:
        threading.Thread(target=maintain_loop, daemon=True, name="atlas-retention").start()
        atexit.register(stop.set)

    def csrf():
        if "csrf" not in session:
            session["csrf"] = secrets.token_urlsafe(32)
        return session["csrf"]

    @app.context_processor
    def context():
        return {"csrf_token": csrf, "current_user": getattr(g, "user", None), "role_labels": ROLE_LABELS}

    @app.template_filter("timestamp")
    def timestamp(value):
        return datetime.fromtimestamp(value, timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    @app.before_request
    def authentication():
        if isinstance(request.routing_exception, SecurityError):
            raise request.routing_exception
        g.user = auth.session_user(session.get("sid"), app.config["SESSION_IDLE"], app.config["SESSION_LIFETIME"])
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            expected = session.get("csrf", "")
            supplied = request.form.get("csrf", "") or request.headers.get("X-CSRF-Token", "")
            if not expected or not secrets.compare_digest(expected, supplied):
                abort(400, "Phiên biểu mẫu đã hết hạn. Hãy tải lại trang.")
        if request.endpoint in ("home", "login", "static", "health"):
            return None
        if g.user is None:
            session.pop("sid", None)
            if request.path.startswith(("/api/", "/data/")):
                return jsonify(error="Vui lòng đăng nhập lại."), 401
            return redirect(url_for("login", next=login_destination(request.full_path)))
        if request.path.startswith("/admin") and not can_manage(g.user):
            abort(403)

    @app.after_request
    def headers(response):
        # Never persist authenticated data in shared browser/proxy disk caches.
        response.headers["Cache-Control"] = "private, no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; connect-src 'self'; font-src 'self'; "
            "frame-ancestors 'none'; base-uri 'none'; form-action 'self'; object-src 'none'")
        response.headers["Vary"] = "Cookie"
        if response.mimetype == "image/svg+xml":
            response.headers["Content-Security-Policy"] = "default-src 'none'; style-src 'unsafe-inline'; img-src 'self' data:; sandbox"
        return response

    @app.errorhandler(Exception)
    def errors(error):
        if isinstance(error, HTTPException):
            status, message = error.code, error.description
            if status == 403:
                message = "Tài khoản này chưa được cấp quyền truy cập nội dung này."
        elif isinstance(error, FileNotFoundError):
            status, message = 404, "Dữ liệu chưa có hoặc đang cập nhật."
        elif isinstance(error, (ValueError, TypeError)):
            status, message = 400, "Tham số dữ liệu không hợp lệ."
        else:
            app.logger.exception("Request failed")
            status, message = 500, "Lỗi xử lý; xem nhật ký server."
        if request.path.startswith(("/api/", "/data/")) or request.accept_mimetypes.best == "application/json":
            return jsonify(error=message, status=status), status
        return render_template("error.html", status=status, message=message), status

    @app.get("/healthz")
    def health():
        return jsonify(status="ok")

    def login_destination(value):
        """Only local application destinations, never user-controlled external redirects."""
        if not value or len(value) > 1024 or "\\" in value or any(ord(c) < 32 for c in value):
            return "/anatomy"
        parsed = urlsplit(value)
        if parsed.scheme or parsed.netloc or parsed.path not in {"/", "/anatomy", "/viewer", "/account", "/admin"}:
            return "/anatomy"
        if parsed.path == "/viewer":
            key = parse_qs(parsed.query).get("key", [""])[0]
            return "/viewer" + ("?" + urlencode({"key": key[:180]}) if key else "")
        return parsed.path

    @app.get("/")
    @app.get("/index.html")
    def home():
        return render_template("home.html", login_open=request.args.get("login") == "1",
                               next_url=login_destination(request.args.get("next")))

    @app.route("/login", methods=["GET", "POST"])
    def login():
        destination = login_destination(request.form.get("next") if request.method == "POST" else request.args.get("next"))
        ajax = request.accept_mimetypes.best == "application/json"
        def failure(message, status, headers=None):
            response = (jsonify(error=message) if ajax else render_template("login.html", error=message, next_url=destination))
            return response, status, headers or {}
        if request.method == "POST":
            username = request.form.get("username", "").strip().lower()[:48]
            if not auth.login_allowed(request.remote_addr or "unknown", username):
                return failure("Bạn đã thử nhiều lần. Vui lòng thử lại sau.", 429, {"Retry-After": "900"})
            token = auth.login(username, request.form.get("password", ""))
            if token:
                session.clear()
                session["sid"] = token
                csrf()
                session.permanent = True
                if ajax:
                    return jsonify(redirect=destination)
                return redirect(destination)
            return failure("Tên đăng nhập hoặc mật khẩu chưa đúng.", 401)
        if g.user:
            return redirect(destination)
        return render_template("login.html", next_url=destination)

    @app.post("/logout")
    def logout():
        auth.logout(session.get("sid"))
        session.clear()
        response = redirect(url_for("home"))
        response.headers["Clear-Site-Data"] = '"cache"'
        return response

    @app.route("/account", methods=["GET", "POST"])
    def account():
        if request.method == "POST":
            try:
                if not auth.login_allowed("password-change:" + str(g.user["id"]), g.user["username"]):
                    abort(429)
                auth.change_password(g.user["id"], request.form.get("old_password", ""), request.form.get("password", ""))
            except ValueError as error:
                flash(str(error), "error")
            else:
                session.clear()
                return redirect(url_for("login"))
        return render_template("account.html")

    def module_keys():
        # Reading the small catalogue keeps newly collected modules discoverable.
        rows = (load_json(repository.catalogue_path, {}) or {}).get("modules", [])
        return [f"{r.get('region') or 'OTHER'}/{r['slug']}" for r in rows if r.get("slug")]

    def canonical_key(value):
        region, slug = safe_key(value)
        normal = (region.replace(" ", "_"), slug)
        matches = [key for key in module_keys() if (key.split("/")[0].replace(" ", "_"), key.split("/")[1]) == normal]
        if len(matches) != 1:
            abort(404)
        return matches[0]

    def allowed(key):
        return can_view_module(g.user, key)

    def require_module(value):
        key = canonical_key(value)
        if not allowed(key):
            abort(403)
        return key

    @app.get("/anatomy")
    def anatomy():
        with repo_lock:
            catalogue = repository.catalogue()
        modules = [m for m in catalogue["modules"] if allowed(m["key"])]
        if not modules:
            return render_template("anatomy_denied.html"), 403
        groups = group_catalogue(modules)
        kinds = {m["modality"] for m in modules}
        families = [(name, [kind for kind in members if kind in kinds]) for name, members in MODALITY_GROUPS]
        extras = sorted(kinds - {kind for _, members in MODALITY_GROUPS for kind in members})
        if extras:
            families.append(("Other", extras))
        return render_template("anatomy.html", groups=groups,
                               modalities=[(name, members) for name, members in families if members],
                               total=len(modules), ready=sum(bool(m["captured"]) for m in modules))

    @app.get("/api/<operation>")
    def api(operation):
        if operation == "languages":
            return jsonify(source_locale="en", languages=languages())
        if operation == "catalogue":
            with repo_lock:
                result = repository.catalogue()
            result["modules"] = [m for m in result["modules"] if allowed(m["key"])]
            result["module_count"] = len(result["modules"])
            result["captured_module_count"] = sum(bool(m["captured"]) for m in result["modules"])
            return jsonify(result)
        if operation not in {"module", "slice", "structure", "search", "translations"}:
            abort(404)
        key = require_module(request.args.get("key", ""))
        with repo_lock:
            if operation == "module":
                result = repository.module(key)
            elif operation == "slice":
                result = repository.slice(key, request.args.get("series", ""), request.args.get("variant", ""), int(request.args.get("slice", "0")))
            elif operation == "structure":
                result = repository.structure(key, request.args.get("taxon", ""), request.args.get("ta"))
            elif operation == "search":
                result = {"results": repository.search(key, request.args.get("q", "")[:200], locale=request.args.get("lang", "en"))}
            else:
                result = load_pack(key, request.args.get("lang", "en"))
        return jsonify(result)

    @app.get("/data/<path:relative>")
    def data(relative):
        parts = relative.split("/")
        if len(parts) < 3 or "\\" in relative or any(p in (".", "..", "") for p in parts):
            abort(404)
        key = require_module("/".join(parts[:2]))
        path = repository.data_file("/".join([key, *parts[2:]]))
        # Do not expose raw captures, arbitrary files or source code via /data.
        # SVG is allowed only as an image: its own CSP disallows scripts entirely.
        if path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp", ".avif", ".svg"}:
            abort(404)
        response = send_file(path, conditional=False, etag=False)
        return response

    @app.get("/portal/runtime.js")
    def runtime():
        settings = {"remote": True, "maxBytes": app.config["BROWSER_CACHE_MIB"] * 1024**2, "ttlMs": app.config["CACHE_TTL"] * 1000,
                    "decodedImages": app.config["DECODED_IMAGES"], "decodeForward": app.config["DECODE_FORWARD"],
                    "decodeBackward": app.config["DECODE_BACKWARD"], "decodeConcurrency": app.config["DECODE_CONCURRENCY"],
                    "preloadConcurrency": app.config["PRELOAD_CONCURRENCY"], "imageConcurrency": app.config["IMAGE_CONCURRENCY"]}
        if request.args.get("module"):
            settings["moduleKey"] = require_module(request.args["module"])
        return app.response_class("window.viewerRuntime=" + json.dumps(settings) + ";", mimetype="application/javascript")

    @app.get("/viewer")
    def viewer():
        if g.user["role"] == "standard" and not g.user["regions"] and not g.user["modules"]:
            return render_template("anatomy_denied.html"), 403
        key = require_module(request.args["key"]) if request.args.get("key") else ""
        if key:
            with repo_lock:
                available = next((m["captured"] for m in repository.catalogue()["modules"] if m["key"] == key), False)
            if not available:
                abort(404, "Module này chưa có đủ dữ liệu để mở.")
        html = (VIEWER / "index.html").read_text(encoding="utf-8")
        html = html.replace('class="app-shell menu-open"', 'class="app-shell menu-open has-website-navigation"')
        panel_toolbar = '<div class="left-topbar-controls" role="toolbar" aria-label="Left-side viewer panels">'
        html = html.replace(panel_toolbar, panel_toolbar + render_template("viewer_navigation.html"))
        html = html.replace("</head>", '<link rel="stylesheet" href="/portal/static/viewer-session.css"></head>')
        runtime_url = "/portal/runtime.js" + ("?" + urlencode({"module": key}) if key else "")
        html = html.replace('<script src="./resource_cache.js"', f'<script src="{runtime_url}" defer></script><script src="./resource_cache.js"')
        # SSR markup only; all anatomical UI/logic still comes from the local viewer.
        html = html.replace("</body>", render_template("session_link.html") + '<script src="/portal/static/viewer-navigation.js" defer></script></body>')
        return html

    @app.get("/<path:asset>")
    def viewer_asset(asset):
        if asset in {"app.js", "anatomy_language.js", "request_queue.js", "resource_cache.js", "styles.css"}:
            return send_file(VIEWER / asset, conditional=False, etag=False)
        if asset.startswith("assets/module-icons/") and asset.endswith(".png"):
            name = asset.removeprefix("assets/module-icons/")
            if "/" not in name and "\\" not in name and name != ".png":
                owners = [key for key, icon in module_icons.items() if icon.get("file") == name]
                if not owners:
                    abort(404)
                if not any(allowed(canonical_key(key)) for key in owners):
                    abort(403)
                return send_file(VIEWER / asset, conditional=False, etag=False)
        abort(404)

    def submitted_policy():
        modules = request.form.getlist("modules")
        if not set(modules).issubset(set(module_keys())):
            raise ValueError("Module chưa có trong danh mục.")
        regions = request.form.getlist("regions")
        if not set(regions).issubset({key.split("/", 1)[0] for key in module_keys()}):
            raise ValueError("Vùng giải phẫu chưa có trong danh mục.")
        role = request.form.get("role", "standard")
        if role not in ROLE_LABELS:
            raise ValueError("Vai trò không hợp lệ.")
        # No client-supplied all_modules override for Standard.
        return role, modules, regions

    @app.get("/admin")
    def admin():
        with repo_lock:
            modules = repository.catalogue()["modules"]
        names = {"_structure_cache": "Structures", "_point_cache": "Slice targets", "_cross_reference_cache": "Cross references"}
        return render_template("admin.html", users=auth.users(), modules=modules, regions=group_catalogue(modules),
                               cache_stats={names[k]: c.stats() for k, c in caches.items()}, audit=auth.recent_audit(),
                               browser_cache_mib=app.config["BROWSER_CACHE_MIB"], ttl=app.config["CACHE_TTL"],
                               decoded_images=app.config["DECODED_IMAGES"], preload_concurrency=app.config["PRELOAD_CONCURRENCY"])

    @app.post("/admin/users")
    def create_user():
        try:
            role, modules, regions = submitted_policy()
            auth.create_user(request.form.get("username", ""), request.form.get("password", ""), role,
                             modules=modules, regions=regions, actor=g.user["username"])
            flash("Đã tạo tài khoản.")
        except ValueError as error:
            flash(str(error), "error")
        return redirect(url_for("admin"))

    @app.post("/admin/users/<int:uid>/<action>")
    def edit_user(uid, action):
        try:
            if action == "delete":
                auth.delete_user(uid, g.user["username"])
            elif action == "revoke":
                auth.revoke(uid, g.user["username"])
            elif action == "update":
                role, modules, regions = submitted_policy()
                auth.update_user(uid, role, request.form.get("active") == "on", False, modules,
                                 request.form.get("password", ""), g.user["username"], regions=regions)
            else:
                abort(404)
            flash("Đã cập nhật; các phiên đăng nhập của tài khoản này đã được thu hồi.")
        except ValueError as error:
            flash(str(error), "error")
        return redirect(url_for("admin"))

    @app.post("/admin/cache/clear")
    def clear_cache():
        with repo_lock:
            for cache in caches.values():
                cache.clear()
        maintenance()
        flash("Đã xóa cache metadata RAM của server. Cache trình duyệt tự hết hạn hoặc được xóa khi tải lại trang.")
        return redirect(url_for("admin"))

    return app
