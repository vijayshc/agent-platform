"""Request lifecycle security guards, headers, session checks, and access control."""

import functools
import logging
import re
from datetime import datetime
from urllib.parse import urlparse, urlsplit

from flask import current_app, flash, g, jsonify, redirect, request, session, url_for

from config.config import BROWSER_LLM_PROXY_URL
from src.auth.decorators import get_route_requirements
from src.utils.auth_utils import current_relative_url
from src.utils.user_manager import UserManager

logger = logging.getLogger("text2sql")
user_manager = UserManager()

# Session configurations
SESSION_TIMEOUT = 30 * 60  # 30 minutes in seconds
SESSION_ABSOLUTE_TIMEOUT = 24 * 60 * 60  # 24 hours in seconds


def validate_password_strength(password: str) -> tuple[bool, str]:
    """Validate password strength based on security best practices."""
    if len(password) < 12:
        return False, "Password must be at least 12 characters long"

    if not re.search(r"[A-Z]", password):
        return False, "Password must contain at least one uppercase letter"

    if not re.search(r"[a-z]", password):
        return False, "Password must contain at least one lowercase letter"

    if not re.search(r"[0-9]", password):
        return False, "Password must contain at least one number"

    if not re.search(r"[^A-Za-z0-9]", password):
        return False, "Password must contain at least one special character"

    common_passwords = ["Password123!", "Admin123!", "Welcome123!"]
    if password in common_passwords:
        return False, "Password is too common"

    return True, "Password is strong"


def requires_fresh_login(f):
    """Decorator to require a fresh login for sensitive operations."""
    @functools.wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get("user_id"):
            g.audit_reason = "authentication required"
            return redirect(url_for("auth.login"))

        # Check if login is fresh (less than 10 minutes old)
        if not session.get("login_fresh") or datetime.utcnow().timestamp() - session.get("login_time", 0) > 600:
            g.audit_reason = "fresh login required"
            session["next_url"] = current_relative_url()
            return redirect(url_for("security.reauthenticate"))

        return f(*args, **kwargs)
    return decorated_function


def session_timeout_check():
    """Check and enforce session timeout before each request."""
    if request.path.startswith("/static"):
        return None

    if "user_id" in session:
        now = datetime.utcnow().timestamp()
        # Check absolute timeout
        if "session_start" in session and now - session["session_start"] > SESSION_ABSOLUTE_TIMEOUT:
            g.audit_reason = "session expired"
            session.clear()
            return redirect(url_for("auth.login"))

        # Check inactivity timeout
        if "last_active" in session and now - session["last_active"] > SESSION_TIMEOUT:
            g.audit_reason = "session timed out"
            session.clear()
            return redirect(url_for("auth.login"))

        session["last_active"] = now
    return None


def add_security_headers(response):
    """Add security-related headers to all responses."""
    connect_sources = ["'self'"]
    if BROWSER_LLM_PROXY_URL:
        parsed_proxy = urlparse(BROWSER_LLM_PROXY_URL)
        if parsed_proxy.scheme and parsed_proxy.netloc:
            connect_sources.append(f"{parsed_proxy.scheme}://{parsed_proxy.netloc}")

    csp_directives = [
        "default-src 'self'",
        "script-src 'self' 'unsafe-inline' 'unsafe-eval' https://cdn.jsdelivr.net https://cdnjs.cloudflare.com https://code.jquery.com https://cdn.datatables.net",
        "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://cdnjs.cloudflare.com https://fonts.googleapis.com https://cdn.datatables.net",
        "font-src 'self' data: https://fonts.gstatic.com https://cdn.jsdelivr.net https://cdnjs.cloudflare.com https://cdn.datatables.net",
        "img-src 'self' data: blob: https://cdn.datatables.net",
        "worker-src 'self' blob:",
        f"connect-src {' '.join(connect_sources)} http://127.0.0.1:6006 http://localhost:6006",
        "frame-src 'self' http://127.0.0.1:6006 http://localhost:6006",
    ]
    response.headers["Content-Security-Policy"] = "; ".join(csp_directives)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["X-XSS-Protection"] = "1; mode=block"

    if "Cache-Control" not in response.headers:
        response.headers["Cache-Control"] = "no-store, max-age=0"

    return response


# Agent-execution endpoints authorize per-agent via the user -> role -> agent
# check inside the route. The session/endpoint RBAC below cannot know which
# agent is being addressed, so authenticated callers are deferred to it.
_AGENT_EXEC_ENDPOINTS = frozenset(
    {
        "run_api.invoke_agent",
        "run_api.invoke_agent_stream",
        "run_api.create_run",
        "run_api.post_conversation_message",
        "agent_platform_agui.agui_input",
    }
)


def _extract_credential() -> str:
    header = request.headers.get("X-API-Key") or request.headers.get("Authorization") or ""
    if header.lower().startswith("bearer "):
        header = header[7:]
    return header.strip()


def _resolve_credential_user() -> tuple[str, int | None] | None:
    """Validate an API key or JWT access token from request headers.

    Returns ``(auth_type, user_id)`` for a valid credential, else ``None``.
    """
    raw = _extract_credential()
    if not raw:
        return None
    from src.agent_platform.execution.api_keys import ApiKeyStore

    key = ApiKeyStore.verify(raw)
    if key is not None:
        return ("api_key", key.get("user_id"))

    from src.auth.access_tokens import read_access_token

    token_user = read_access_token(raw)
    if token_user is not None:
        return ("token", token_user)
    return None


def enforce_own_origin_for_writes():
    """Refuse a state-changing request that a *different* origin initiated."""
    if (request.method or "").upper() in ("GET", "HEAD", "OPTIONS", "TRACE"):
        return None
    origin = (request.headers.get("Origin") or "").strip()
    if not origin:
        return None

    from config import config as platform_config

    allowed = {request.host}
    configured_platform = str(getattr(platform_config, "HOSTED_APPS_PLATFORM_ORIGIN", "") or "").strip()
    if configured_platform:
        allowed.add(urlsplit(configured_platform).netloc)

    if origin == "null" or urlsplit(origin).netloc not in allowed:
        g.audit_reason = f"cross-origin write refused (origin {origin!r})"
        logger.warning("Refused %s %s from origin %s", request.method, request.path, origin)
        if request.path.startswith("/api/") or request.path.startswith("/admin/api/") or request.is_json:
            return jsonify({"error": "Cross-origin request refused"}), 403
        return (
            "<!doctype html><meta charset='utf-8'><title>Forbidden</title>"
            "<h1>Cross-origin request refused</h1>"
            "<p>This action must be started from the platform's own pages.</p>",
            403,
            {"Content-Type": "text/html; charset=utf-8"},
        )
    return None


def check_access_control():
    """Enforce module authorization for every view that declares one or more modules."""
    if request.path.startswith("/static"):
        return None

    endpoint = request.endpoint
    if not endpoint:
        return None

    view = current_app.view_functions.get(endpoint)
    required_modules, read_methods = get_route_requirements(view)
    is_api_request = (
        request.path.startswith("/api/")
        or request.path.startswith("/admin/api/")
        or bool(request.is_json)
    )
    if not required_modules:
        return None

    user_id = session.get("user_id")

    if request.path.startswith("/api/v1/"):
        resolved = _resolve_credential_user()
        if resolved is not None:
            auth_type, cred_user_id = resolved
            if endpoint not in _AGENT_EXEC_ENDPOINTS:
                user_id = cred_user_id
                g.user_id = cred_user_id
                g.auth_type = auth_type

    if not user_id:
        g.audit_reason = "authentication required"
        if is_api_request:
            return jsonify({"error": "Authentication required"}), 401
        return redirect(url_for("auth.login", next=current_relative_url()))

    from src.auth.modules import ACCESS_READ, ACCESS_WRITE

    method = (request.method or "GET").upper()
    required_level = ACCESS_READ if method in read_methods else ACCESS_WRITE
    if not user_manager.has_any_module_access(user_id, required_modules, min_level=required_level):
        g.audit_reason = f"missing {required_level} access to module(s): {', '.join(required_modules)}"
        logger.warning(
            "Access denied for user %s to endpoint %s (modules: %s, level: %s)",
            user_id,
            endpoint,
            ", ".join(required_modules),
            required_level,
        )
        if is_api_request:
            return jsonify({"error": "Permission denied"}), 403
        flash("You do not have permission to access this page.", "danger")
        return redirect(url_for("index"))

    return None
