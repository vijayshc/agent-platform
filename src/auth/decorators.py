"""Route decorators for module + access-level authorization."""

from __future__ import annotations

from functools import wraps

from flask import flash, g, jsonify, redirect, request, session, url_for

from src.auth.modules import (
    ACCESS_READ,
    ACCESS_WRITE,
    DEFAULT_READ_METHODS,
    normalize_module_keys,
)
from src.utils.auth_utils import current_relative_url

# Attributes inspected by the global before-request guard.  Keeping metadata on
# the view function means route requirements are declared in exactly one place
# and can be enforced both locally (decorator) and globally (middleware).
REQUIRED_MODULES_ATTR = "__required_modules__"
READ_METHODS_ATTR = "__read_methods__"
#: Marker set by :func:`admin_required` so tooling can introspect admin routes.
ADMIN_REQUIRED_ATTR = "__admin_required__"


def current_user_id_for_rbac() -> int | None:
    """Best-effort user id for session, JWT or API-key authentication."""
    try:
        from src.agent_platform.api.auth import current_user_id
    except Exception:  # pragma: no cover - platform package is always available
        current_user_id = None  # type: ignore[assignment]

    if callable(current_user_id):
        try:
            return current_user_id()
        except RuntimeError:
            pass
    try:
        return getattr(g, "user_id", None) or session.get("user_id")
    except RuntimeError:
        return session.get("user_id")


def _is_api_request() -> bool:
    """Whether the current request expects a JSON API error rather than a redirect.

    A request is an API request when any of these hold:

    * its path carries an ``/api/`` segment (``/api/...``, ``/admin/api/...`` and
      the per-area admin APIs such as ``/admin/file-browser/api/...``),
    * it sends a JSON body (``request.is_json``), or
    * it explicitly negotiates JSON via ``Accept`` — e.g. ``Accept:
      application/json`` on a route like ``/admin/database/schema`` whose path
      carries no ``/api/`` segment.

    A bare ``*/*`` (the default browsers and test clients send, and usually a
    low-q fallback in browser headers) is **not** a JSON preference and keeps the
    flash-redirect contract, as do ``text/html`` page navigations.
    """
    if request.is_json or "/api/" in (request.path or ""):
        return True
    accept = request.accept_mimetypes
    if accept.best in (None, "*/*"):
        return False
    return accept.best_match(("application/json", "text/html")) == "application/json"


def _deny(reason: str = "Permission denied"):
    """Return the right auth response for APIs vs browser navigation."""
    _mark_denied(reason)
    if _is_api_request():
        return jsonify({"error": "Permission denied", "message": reason}), 403
    flash("You do not have permission to access this page.", "danger")
    return redirect(url_for("index"))


def _mark_denied(reason: str) -> None:
    """Record why authorization failed so the audit trail can explain it."""
    try:
        g.audit_reason = reason
    except RuntimeError:  # pragma: no cover - outside a request context
        pass


def module_required(*module_keys: str, read_methods=None):
    """Require module access for the current user.

    ``module_keys`` is any-of: access to one module is enough.
    HTTP methods in ``read_methods`` only need READ level; every other method
    needs WRITE.  Modules whose POST endpoints are pure reads can pass e.g.
    ``read_methods=("GET", "POST")``.
    """

    required = normalize_module_keys(module_keys)
    if not required:
        raise ValueError("module_required() needs at least one valid module key")

    allowed_read_methods = frozenset(
        m.upper() for m in (read_methods if read_methods is not None else DEFAULT_READ_METHODS)
    )

    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            from src.utils.user_manager import UserManager

            method = (request.method or "GET").upper()
            required_level = ACCESS_READ if method in allowed_read_methods else ACCESS_WRITE

            user_id = current_user_id_for_rbac()
            if not user_id:
                _mark_denied("authentication required")
                if _is_api_request():
                    return jsonify({"error": "Authentication required"}), 401
                return redirect(url_for("auth.login", next=current_relative_url()))

            if not UserManager().has_any_module_access(user_id, required, min_level=required_level):
                return _deny(
                    f"missing {required_level} access to module(s): {', '.join(required)}"
                )
            return fn(*args, **kwargs)

        setattr(wrapper, REQUIRED_MODULES_ATTR, tuple(required))
        setattr(wrapper, READ_METHODS_ATTR, tuple(sorted(allowed_read_methods)))
        return wrapper

    return decorator


def admin_required(*, allow_read: bool = False):
    """Require the built-in ``admin`` role.

    Unauthenticated callers get 401 (JSON for APIs, redirect to login for
    browser navigation); authenticated non-admins get 403 (JSON for APIs,
    flash + redirect to ``index`` for browser navigation).

    ``allow_read=True`` lets safe methods (GET/HEAD/OPTIONS) through for any
    authenticated identity while every mutating method still requires admin.
    Route-level module access (``module_required``) composes with this.
    """

    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            from src.auth.resource_access import is_admin

            user_id = current_user_id_for_rbac()
            if not user_id:
                _mark_denied("authentication required")
                if _is_api_request():
                    return jsonify({"error": "Authentication required"}), 401
                return redirect(url_for("auth.login", next=current_relative_url()))

            if allow_read and (request.method or "GET").upper() in DEFAULT_READ_METHODS:
                return fn(*args, **kwargs)

            if not is_admin(user_id):
                return _deny("administrator role required")
            return fn(*args, **kwargs)

        setattr(wrapper, ADMIN_REQUIRED_ATTR, True)
        return wrapper

    return decorator


def get_route_requirements(view_function) -> tuple[tuple[str, ...], frozenset[str]]:
    """Read module + read-method metadata from a registered Flask view."""
    if view_function is None:
        return (), DEFAULT_READ_METHODS
    raw = getattr(view_function, REQUIRED_MODULES_ATTR, ())
    modules = normalize_module_keys(raw)
    raw_methods = getattr(view_function, READ_METHODS_ATTR, None)
    if raw_methods is None:
        return modules, DEFAULT_READ_METHODS
    return modules, frozenset(str(m).upper() for m in raw_methods)


def get_required_modules(view_function) -> tuple[str, ...]:
    """Backward-compatible helper returning only the module marker."""
    return get_route_requirements(view_function)[0]
