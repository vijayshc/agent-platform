"""Security enhancement routes and re-exported security middleware.

Delegates core concerns to modular handlers in ``src/auth/``:
- CSRF protection: ``src.auth.csrf``
- Rate limiting & lockout: ``src.auth.rate_limiter``
- Request guards & headers: ``src.auth.request_guards``
"""

from datetime import datetime
import secrets
from flask import Blueprint, g, jsonify, redirect, request, session, url_for

from src.auth.csrf import csrf_protect, generate_csrf_token, validate_csrf_token
from src.auth.rate_limiter import (
    check_for_account_lockout,
    clear_login_attempts,
    failed_login_attempts,
    failed_login_lock,
    ip_login_attempts,
    ip_login_lock,
    is_rate_limited_for_login,
    rate_limit,
    rate_limit_lock,
    rate_limit_store,
    record_failed_login,
)
from src.auth.request_guards import (
    SESSION_ABSOLUTE_TIMEOUT,
    SESSION_TIMEOUT,
    add_security_headers,
    check_access_control,
    enforce_own_origin_for_writes,
    requires_fresh_login,
    session_timeout_check,
    user_manager,
    validate_password_strength,
)

security_bp = Blueprint("security", __name__)

# Register request lifecycle hooks on blueprint
security_bp.before_app_request(session_timeout_check)
security_bp.after_app_request(add_security_headers)
security_bp.before_app_request(enforce_own_origin_for_writes)
security_bp.before_app_request(check_access_control)


@security_bp.route("/reauthenticate", methods=["GET", "POST"])
@csrf_protect
def reauthenticate():
    """Require re-authentication for sensitive operations."""
    error = None
    if request.method == "POST":
        username = session.get("username")
        password = request.form.get("password")

        if not username or not password:
            error = "Username and password required"
            g.audit_reason = "username and password required"
        else:
            user_id = user_manager.authenticate(username, password)
            if user_id:
                session["login_fresh"] = True
                session["login_time"] = datetime.utcnow().timestamp()
                next_url = session.pop("next_url", url_for("index"))
                if request.is_json:
                    return jsonify({"success": True, "redirect": next_url})
                return redirect(next_url)
            else:
                error = "Invalid password"
                g.audit_reason = "invalid password"

    if request.is_json:
        return jsonify({"success": False, "error": error}), 401 if error else 200

    from src.routes.agent_routes import _render_admin_app
    return _render_admin_app()


@security_bp.route("/rotate-session", methods=["POST"])
def rotate_session():
    """Rotate session ID to prevent session fixation attacks."""
    if "user_id" not in session:
        return jsonify({"error": "Not authenticated"}), 401

    user_id = session.get("user_id")
    username = session.get("username")

    session.sid = secrets.token_urlsafe(32)
    session["user_id"] = user_id
    session["username"] = username
    session["session_start"] = datetime.utcnow().timestamp()
    session["last_active"] = datetime.utcnow().timestamp()

    return jsonify({"success": True}), 200


__all__ = [
    "security_bp",
    "generate_csrf_token",
    "validate_csrf_token",
    "csrf_protect",
    "validate_password_strength",
    "requires_fresh_login",
    "rate_limit",
    "is_rate_limited_for_login",
    "record_failed_login",
    "clear_login_attempts",
    "check_for_account_lockout",
    "rate_limit_lock",
    "failed_login_lock",
    "ip_login_lock",
    "rate_limit_store",
    "failed_login_attempts",
    "ip_login_attempts",
    "SESSION_TIMEOUT",
    "SESSION_ABSOLUTE_TIMEOUT",
    "session_timeout_check",
    "add_security_headers",
    "enforce_own_origin_for_writes",
    "check_access_control",
    "user_manager",
]
