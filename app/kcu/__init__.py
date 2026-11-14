"""KCU – Vereins-App des Kicker Club United."""
import os
import secrets
from datetime import date

from flask import Flask, abort, g, render_template, request, session
from werkzeug.middleware.proxy_fix import ProxyFix

from . import auth, db, logic, media

VERSION = "2.9.2"


def _secret_key(data_dir):
    env = os.environ.get("KCU_SECRET")
    if env:
        return env
    path = os.path.join(data_dir, "secret_key")
    if not os.path.exists(path):
        with open(path, "w") as f:
            f.write(secrets.token_hex(32))
        os.chmod(path, 0o600)
    with open(path) as f:
        return f.read().strip()


def create_app():
    app = Flask(__name__)
    data_dir = os.environ.get("KCU_DATA", "/data")
    app.config.update(
        DATA_DIR=data_dir,
        PHOTOS_DIR=os.environ.get("KCU_PHOTOS", "/photos"),
        MAX_CONTENT_LENGTH=60 * 1024 * 1024,   # max. 60 MB pro Upload (Cloudflare-Limit: 100 MB)
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=auth.cf_mode(),
        SESSION_COOKIE_NAME="kcu_session",
        TEMPLATES_AUTO_RELOAD=False,
    )
    os.makedirs(data_dir, exist_ok=True)
    app.secret_key = _secret_key(data_dir)
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    db.init_db(app)
    app.teardown_appcontext(db.close_db)

    # --- Template-Helfer ---
    app.jinja_env.filters["euro"] = logic.euro
    app.jinja_env.filters["date_de"] = logic.date_de
    app.jinja_env.filters["ym_de"] = logic.ym_de
    app.jinja_env.filters["markdown"] = media.render_markdown

    @app.context_processor
    def inject():
        return {
            "user": g.get("user"),
            "settings": g.get("settings", {}),
            "can": auth.can,
            "dev_mode": auth.dev_mode(),
            "csrf_token": session.get("csrf", ""),
            "ROLE_LABEL": auth.ROLE_LABEL,
            "today": date.today().isoformat(),
            "version": VERSION,
        }

    PUBLIC = {"static", "main.manifest", "main.health", "main.service_worker", "main.calendar_feed", "main.impressum", "manifest", "health",
              "kicker.gast_tv", "kicker.gast_status"}

    @app.before_request
    def before():
        if request.endpoint in PUBLIC:
            return
        g.settings = db.get_settings()
        auth.load_user()
        if "csrf" not in session:
            session["csrf"] = secrets.token_urlsafe(24)
        if g.user is None:
            reason = "config" if not (auth.cf_mode() or auth.dev_mode()) else ("unknown" if g.email else "nologin")
            return render_template("noaccess.html", reason=reason, email=g.email), 403
        if request.method == "POST":
            token = request.form.get("csrf") or request.headers.get("X-CSRF-Token")
            if not token or token != session.get("csrf"):
                abort(400, "Sitzung abgelaufen – bitte Seite neu laden und nochmal versuchen.")

    @app.after_request
    def headers(resp):
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        resp.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        return resp

    @app.errorhandler(400)
    @app.errorhandler(403)
    @app.errorhandler(404)
    @app.errorhandler(413)
    def errors(e):
        code = getattr(e, "code", 500)
        msg = getattr(e, "description", "")
        if code == 413:
            msg = "Die Datei ist zu groß (max. 60 MB)."
        return render_template("error.html", code=code, message=msg), code

    from .views import bp
    app.register_blueprint(bp)
    from .kicker import bp as kicker_bp
    app.register_blueprint(kicker_bp)

    if os.environ.get("KCU_SCHEDULER", "1") != "0":
        from . import notify
        notify.start_scheduler(app)
    return app
