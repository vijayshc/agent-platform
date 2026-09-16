from flask import Flask, render_template, request, jsonify, session, redirect, url_for, flash, current_app, g
from src.utils.schema_manager import SchemaManager
from src.utils.user_manager import UserManager
from src.utils.template_filters import register_filters
from src.routes.auth_routes import auth_bp, admin_required
from src.routes.admin_routes import admin_bp
from src.routes.admin_api_routes import admin_api_bp
from src.routes.admin_db_routes import admin_db_bp
from src.routes.admin_file_browser_routes import admin_file_browser_bp
from src.routes.security_routes import security_bp, generate_csrf_token
from src.routes.vector_db_routes import vector_db_bp
from src.routes.knowledge_routes import knowledge_bp
from src.routes.agent_routes import agent_bp, _render_agent_app
from src.agent_platform.api.blueprint import create_blueprint as create_agent_platform_blueprint
from src.routes.browser_llm_proxy_routes import browser_llm_proxy_bp
from src.routes.skill_routes import skill_bp
from src.routes.hosted_app_routes import hosted_app_bp
from src.models.user import Permissions
from config.config import (
    SECRET_KEY,
    DEBUG,
    SESSION_COOKIE_SECURE,
    AUTH_PROVIDER,
    BROWSER_LLM_PROXY_ENABLED,
    BROWSER_LLM_PROXY_URL,
    BROWSER_LLM_PROXY_TIMEOUT_SECONDS,
    UPLOADS_DIR,
)
import logging
import os
import sys
import time
import signal
import asyncio

from src.utils.auth_utils import login_required

logger = logging.getLogger('text2sql')


app = Flask(__name__)
app.config['SECRET_KEY'] = SECRET_KEY
app.config['DEBUG'] = DEBUG
app.config['TEMPLATES_AUTO_RELOAD'] = DEBUG
app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 0 if DEBUG else 31536000
app.jinja_env.auto_reload = DEBUG
app.config['SESSION_COOKIE_SECURE'] = SESSION_COOKIE_SECURE
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['PERMANENT_SESSION_LIFETIME'] = 86400
app.config['UPLOAD_FOLDER'] = UPLOADS_DIR

register_filters(app)
user_manager = UserManager()

# Audit capture is installed before the blueprints so it sees every request the
# authorization layer handles, including the ones it rejects.
from src.auth.audit_middleware import register_audit_middleware  # noqa: E402

register_audit_middleware(app)

# Bind the signed-in account around each request so every log line - including
# Werkzeug's own access line - names it (see src/utils/log_filters.py).
from src.utils.log_filters import install_request_user_logging  # noqa: E402

install_request_user_logging(app)

# Register blueprints
app.register_blueprint(auth_bp)
app.register_blueprint(admin_bp)
app.register_blueprint(admin_api_bp)
app.register_blueprint(admin_db_bp)
app.register_blueprint(admin_file_browser_bp)
app.register_blueprint(security_bp)
app.register_blueprint(vector_db_bp)
app.register_blueprint(knowledge_bp)
app.register_blueprint(agent_bp)
app.register_blueprint(browser_llm_proxy_bp)
app.register_blueprint(create_agent_platform_blueprint())
from src.agent_platform.api.phoenix_proxy import phoenix_proxy_bp
app.register_blueprint(phoenix_proxy_bp)
from src.routes.config_routes import config_bp
app.register_blueprint(config_bp)
from src.routes.mcp_admin_routes import mcp_admin_bp
app.register_blueprint(mcp_admin_bp)
app.register_blueprint(skill_bp)
app.register_blueprint(hosted_app_bp)

@app.context_processor
def inject_csrf_token():
    return dict(csrf_token=generate_csrf_token)

@app.context_processor
def inject_browser_llm_proxy_config():
    return dict(
        browser_llm_proxy_enabled_default=BROWSER_LLM_PROXY_ENABLED,
        browser_llm_proxy_url=BROWSER_LLM_PROXY_URL,
    )

@app.route('/')
@login_required
def index():
    return _render_agent_app()

@app.route('/api/workspaces', methods=['GET'])
@login_required
def get_workspaces():
    try:
        workspaces = SchemaManager().get_workspaces()
        return jsonify({"workspaces": workspaces})
    except Exception as e:
        logger.exception(f"Error retrieving workspaces: {str(e)}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/tables', methods=['GET'])
@login_required
def get_tables_for_workspace():
    workspace_name = request.args.get('workspace', 'Default')
    try:
        tables = SchemaManager().get_table_names(workspace_name)
        return jsonify({"tables": tables})
    except Exception as e:
        logger.exception(f"Error retrieving tables: {str(e)}")
        return jsonify({"error": str(e)}), 500

@app.context_processor
def inject_user():
    user = None
    is_admin = False
    if session.get('user_id'):
        user_id = session.get('user_id')
        user = user_manager.get_user_by_id(user_id)
        is_admin = user_manager.has_role(user_id, 'admin')
    
    return dict(
        current_user=user,
        is_admin=is_admin,
        user_manager=user_manager,
        has_permission=lambda permission: user_manager.has_permission(session.get('user_id'), permission) if session.get('user_id') else False,
        has_module_access=lambda module_key, min_level='read': user_manager.has_module_access(session.get('user_id'), module_key, min_level) if session.get('user_id') else False,
        user_modules=(user_manager.get_user_modules(session.get('user_id')) if session.get('user_id') else set()),
        module_levels=(user_manager.get_user_module_levels(session.get('user_id')) if session.get('user_id') else {}),
        permissions=Permissions,
        auth_provider=AUTH_PROVIDER,
        is_ldap=(AUTH_PROVIDER == 'ldap')
    )

@app.errorhandler(404)
def page_not_found(e):
    from src.hosting import origin

    if origin.is_apps_origin(request):
        return origin.plain_page(404, "Not found", "No such page on the hosted-apps origin.")
    from src.routes.agent_routes import _render_agent_app

    resp = _render_agent_app(error_code=404)
    return resp, 404

@app.errorhandler(403)
def forbidden(e):
    from src.hosting import origin

    if origin.is_apps_origin(request):
        return origin.plain_page(403, "Forbidden", "You do not have access to this resource.")
    from src.routes.agent_routes import _render_agent_app

    resp = _render_agent_app(error_code=403)
    return resp, 403

@app.errorhandler(500)
def server_error(e):
    from src.hosting import origin

    if origin.is_apps_origin(request):
        return origin.plain_page(500, "Server error", "The platform could not serve this request.")
    from src.routes.agent_routes import _render_agent_app

    resp = _render_agent_app(error_code=500)
    return resp, 500

@app.route('/reload', methods=['POST'])
@login_required
@admin_required
def reload_app():
    '''Restart the platform. POST only: a state-changing GET is reachable from a
    hosted app's page with an <img> tag, which the origin guard cannot see.'''
    # This route terminates the process, so the central audit middleware can
    # never reach its after-request hook: the restart is recorded here instead.
    from src.auth import audit

    session_user_id = session.get('user_id')
    audit.record(
        status=audit.STATUS_SUCCESS,
        endpoint=f"{request.method} {request.path}",
        comment=audit.build_comment("application reload", http_status=200),
        ip=request.remote_addr,
        username=session.get('username')
        or (user_manager.get_username_by_id(session_user_id) if session_user_id else None),
    )
    # The middleware must not also write this event if the process survives the
    # signal (e.g. a deployment that handles SIGUSR1).
    g.audit_plan = None
    logger.info("Manual reload requested")
    os.kill(os.getpid(), signal.SIGUSR1)
    return "Reloading..."

def _initialize_hosted_apps():
    """Probe the isolation tier once and bring up any autostart hosted apps.

    The probe result is logged in full because it is the single fact that
    decides how much a hosted app can reach - and it is recorded per app in the
    audit trail when the app starts.
    """
    from src.hosting import capabilities, settings

    if not settings.enabled():
        logger.info("Hosted apps: disabled (set HOSTED_APPS_ENABLED=true to enable)")
        return

    # The schema (including hosted_apps) belongs to scripts/setup_platform.py:
    # boot reads state, it does not create it.
    report = capabilities.startup_report()
    logger.info(
        "Hosted apps: enabled | tier=%s (configured=%s) | userns=%s landlock=%s seccomp=%s | root=%s",
        report["tier"], report["tier_configured"], report["userns"],
        report["landlock"], report["seccomp"], report["root"],
    )
    logger.info("Hosted apps: %s", capabilities.describe())
    if settings.same_origin_mode():
        logger.warning(
            "Hosted apps: HOSTED_APPS_ORIGIN_PORT=0 - app content is served from the "
            "platform's own origin, so any hosted app can call platform APIs as the "
            "signed-in user. Set a port (default 5001) or HOSTED_APPS_ORIGIN."
        )
    for warning in capabilities.exposure_warnings():
        logger.warning("Hosted apps: %s", warning)
    if report["tier"] is None:
        logger.error("Hosted apps: %s", report["tier_reason"])
        return

    try:
        from src.hosting import installer, supervisor

        for slug in installer.recover_interrupted_installs():
            logger.warning("Hosted app '%s': the install was interrupted; it can be retried", slug)
        for slug, message in supervisor.reap_orphans():
            logger.warning("Hosted app '%s': %s", slug, message)
        def _report(slug: str, ok: bool, message: str) -> None:
            (logger.info if ok else logger.error)("Hosted app '%s': %s", slug, message)

        if not settings.autostart():
            # The operator switched boot-time startup off; apps still start on
            # request. Saying so is the point: a silent skip looks like a bug.
            logger.info(
                "Hosted apps: nothing started at boot (HOSTED_APPS_AUTOSTART=false); "
                "start an app from Hosted Apps when you want it"
            )
        else:
            starting = supervisor.autostart_all(on_result=_report)
            if starting:
                logger.info("Hosted apps: starting %s in the background", ", ".join(starting))
    except Exception as exc:  # a broken app must never stop the platform booting
        logger.exception("Hosted apps: autostart failed: %s", exc)


def init_app_with_context(app):
    with app.app_context():
        logger.info("Initializing application-wide components")
        # Table creation, roles, the admin user, and sample content are one-time
        # work owned by scripts/setup_platform.py and scripts/feed_samples.py.
        # Boot only wires the runtime and verifies the schema is there.
        try:
            from src.agent_platform.bootstrap import initialize_runtime

            initialize_runtime()
            logger.info("Agent platform initialized")

            from src.services.otel_observability import setup_observability
            setup_observability()
            logger.info("OpenTelemetry observability initialized")
        except RuntimeError:
            # Uninitialized database: refuse to serve a half-working platform and
            # make the fix (the setup script) impossible to miss.
            logger.exception("Agent platform not initialized - refusing to start")
            raise
        except Exception:
            logger.exception("Failed to initialize agent platform")
        _initialize_hosted_apps()

        logger.info("Application-wide components initialization complete")

#: ``APP_ORIGIN_ONLY=1`` runs a process whose only job is to serve ``/apps/*`` on
#: the hosted-apps origin (see src/hosting/origin.py). It deliberately skips the
#: platform's one-time boot work - orphan reaping, hosted-app autostart - which
#: belongs to the main instance.
ORIGIN_ONLY = os.getenv("APP_ORIGIN_ONLY", "").strip() == "1"

if ORIGIN_ONLY:
    from src.hosting.origin import make_wsgi

    app.wsgi_app = make_wsgi(app.wsgi_app)
    logger.info("Hosted apps origin process: serving only /apps/* on port %s", os.getenv("PORT", "5000"))
else:
    init_app_with_context(app)


def shutdown_handler(signal_received=None, frame=None):
    print("Shutting down application...")
    try:
        from src.hosting import origin, supervisor

        supervisor.shutdown_all()
        origin.stop()
    except Exception as e:
        print(f"Error stopping hosted apps: {e}")

    # Close pooled DB connections and fold the WAL back into the main file so
    # a restart never leaves an unrecovered WAL behind.
    try:
        from src.utils.database import checkpoint_wal, dispose_db_engine

        dispose_db_engine()
        checkpoint_wal()
    except Exception as e:
        print(f"Error closing database connections: {e}")

    print("Application shutdown complete.")
    sys.exit(0)

signal.signal(signal.SIGINT, shutdown_handler)
signal.signal(signal.SIGTERM, shutdown_handler)

@app.teardown_appcontext
def teardown_app_context(exception):
    # Return this thread's ORM connection to the pool. Without this, each
    # request thread keeps holding a SQLite connection, and long-lived
    # connections across threads are what corrupt the database.
    from src.utils.database import remove_db_session

    remove_db_session()

if __name__ == '__main__':
    try:
        print("Starting Flask app:")
        print(f"DEBUG mode: {app.debug}")
        print(f"TEMPLATES_AUTO_RELOAD: {app.config['TEMPLATES_AUTO_RELOAD']}")
        print(f"JINJA auto_reload: {app.jinja_env.auto_reload}")
        print(f"Working directory: {os.getcwd()}")

        # Surface an already-corrupt database at boot instead of failing later.
        try:
            from src.utils.database import quick_check

            status = quick_check()
            print(f"Database quick_check: {status}")
            if status != "ok":
                logging.getLogger('text2sql').warning("Database quick_check reported: %s", status)
        except Exception as exc:
            print(f"Database quick_check failed: {exc}")

        # The hosted-apps origin: a second listener on this host that serves only
        # /apps/*, so app content never shares the platform's origin. No-op when
        # the deployment provides the origin itself (HOSTED_APPS_ORIGIN), when
        # hosting is disabled, or when this process *is* that origin.
        if not ORIGIN_ONLY:
            from src.hosting import origin

            origin.serve(app)

        app.run(
            host='0.0.0.0',
            # PORT lets start.sh serve an instance on another port (a review
            # copy, a second checkout) without editing this file.
            port=int(os.getenv('PORT', 5000)),
            debug=DEBUG,
            use_reloader=False,
            threaded=True,
        )
    finally:
        shutdown_handler()