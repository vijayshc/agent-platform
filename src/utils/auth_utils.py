"""
Authentication utilities for the Text2SQL application.
Contains decorators and helper functions for authentication and authorization.
"""

from urllib.parse import urlsplit, urlunsplit

from flask import g, session, redirect, url_for, request
from functools import wraps


def current_relative_url() -> str:
    """Where to send the browser back to after signing in - as a local path.

    The login endpoint only honours a ``next`` target that starts with ``/``, so
    that the parameter can never be used as an open redirect (a full URL would
    let an attacker bounce a user to another site). ``request.url`` is absolute,
    so using it here silently dropped the destination and every deep link - a
    hosted app URL, an admin page - landed on the home page after login instead
    of the page that was asked for.
    """
    if request.query_string:
        return f"{request.path}?{request.query_string.decode('utf-8', 'replace')}"
    return request.path or "/"


def safe_next_target(raw) -> str | None:
    """Reduce a post-login destination to a same-origin relative URL.

    A path such as ``/apps/demo/?tab=2`` is returned unchanged. An absolute URL
    on this host - which links and bookmarks issued before this helper existed
    still carry - is reduced to its path and query, so those keep working.
    Anything else (another origin, a protocol-relative ``//host`` URL, a
    ``javascript:`` scheme) is refused with ``None``.
    """
    target = str(raw or "").strip()
    if not target or "\n" in target or "\r" in target:
        return None
    if target[0] == "/":
        # "//host" and "/\host" are network-path references, not local paths.
        return None if len(target) > 1 and target[1] in "/\\" else target
    parts = urlsplit(target)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return None
    if parts.netloc.lower() != request.host.lower():
        return None
    return urlunsplit(("", "", parts.path or "/", parts.query, ""))


def login_required(f):
    """
    Decorator for routes that require login.
    Redirects to login page if user is not authenticated.
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('user_id'):
            # Explain the rejection in the audit trail.
            g.audit_reason = "authentication required"
            return redirect(url_for('auth.login', next=current_relative_url()))
        return f(*args, **kwargs)
    decorated_function.__name__ = f.__name__
    return decorated_function
