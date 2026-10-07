"""CSRF protection utilities for the Text2SQL application."""

import functools
import logging
import secrets
from flask import abort, request, session

logger = logging.getLogger("text2sql")


def generate_csrf_token() -> str:
    """Generate a new CSRF token and store in session."""
    if "_csrf_token" not in session:
        session["_csrf_token"] = secrets.token_hex(16)
    return session["_csrf_token"]


def validate_csrf_token(token: str | None) -> bool:
    """Validate CSRF token against the one in session."""
    session_token = session.get("_csrf_token")
    if not session_token or not token:
        return False
    return session_token == token


def csrf_protect(f):
    """Decorator to check for CSRF token in POST/PUT/DELETE requests."""
    @functools.wraps(f)
    def decorated_function(*args, **kwargs):
        if request.method in ["POST", "PUT", "DELETE"]:
            json_body = request.get_json(silent=True) or {}
            token = (
                request.form.get("_csrf_token")
                or request.headers.get("X-CSRF-Token")
                or (json_body.get("_csrf_token") if isinstance(json_body, dict) else None)
            )
            if not token or not validate_csrf_token(token):
                logger.warning(f"CSRF validation failed for {request.path}")
                abort(403, description="CSRF validation failed")
        return f(*args, **kwargs)
    return decorated_function
