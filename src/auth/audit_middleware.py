"""Central audit capture for the authorization layer.

Registered once from ``app.py`` and deliberately generic: it reads the module
metadata that ``@module_required`` already attaches to every protected view,
plus the authorization decision recorded during the request, so new modules and
endpoints are audited without adding any audit code to them.

Policy (see ``src.auth.audit`` for the constants):

* every request to a module-protected route (admin areas, file browser,
  database console, knowledge, skills, …) — allowed or rejected;
* every state-changing request (POST/PUT/PATCH/DELETE) anywhere in the app,
  including the agent APIs;
* downloads, uploads, exports and other path-marked data movements;
* authentication boundaries: login, logout, password change/reset, re-auth.

Read-only noise (assets, health probes, plain GETs of ungated APIs) is skipped.
"""

from __future__ import annotations

import logging

from flask import current_app, g, request, session

from src.auth import audit
from src.auth.decorators import current_user_id_for_rbac, get_route_requirements
from src.auth.modules import ACCESS_READ, ACCESS_WRITE

logger = logging.getLogger("text2sql.audit")

#: Paths that never carry user intent worth auditing.
SKIP_PREFIXES = ("/static", "/favicon", "/health", "/robots.txt")

#: Human-readable action labels for endpoints the path does not describe well.
_ACTION_LABELS = {
    "auth.change_password": "password change",
    "auth.reset_password": "password reset",
    "auth.reset_password_request": "password reset request",
    "security.reauthenticate": "re-authentication",
}


def register_audit_middleware(app) -> None:
    """Install the two hooks that capture the trail.

    ``before_request`` builds the audit plan (including data the view may later
    consume, such as uploaded file names and the login identity) and
    ``after_request`` writes the outcome.  Registering before the blueprints
    guarantees the plan exists even when the authorization guard rejects the
    request first.
    """
    app.before_request(_plan_request)
    app.after_request(_write_response_audit)


def _plan_request():
    """Describe the request if it is auditable; never influences the response."""
    try:
        g.audit_plan = _build_plan()
    except Exception:  # pragma: no cover - auditing must never break a request
        logger.exception("Failed to build audit plan for %s", request.path)
        g.audit_plan = None
    return None


def _build_plan() -> dict | None:
    if not audit.enabled():
        return None

    path = request.path or ""
    if not path or path.startswith(SKIP_PREFIXES):
        return None

    method = (request.method or "GET").upper()
    if method == "OPTIONS":
        # CORS preflight carries no user intent.
        return None

    endpoint = request.endpoint
    if not endpoint:
        # Unrouted requests (scanners, typos) carry no user identity to audit.
        return None

    view = current_app.view_functions.get(endpoint)
    modules, read_methods = get_route_requirements(view)
    level = ACCESS_READ if method in read_methods else ACCESS_WRITE
    uploads = _uploaded_names()
    marked = any(marker in path.lower() for marker in audit.AUDITED_PATH_MARKERS)
    auth_event = endpoint if audit.AUTH_EVENT_METHODS.get(endpoint) == method else None
    action_label = audit.AUDITED_ACTION_ENDPOINTS.get(endpoint.rsplit(".", 1)[-1])

    if not (modules or marked or auth_event or action_label or method in audit.AUDITED_METHODS):
        return None

    user_id = current_user_id_for_rbac()
    submitted_user = (
        _submitted_username()
        if auth_event in ("auth.login", "auth.reset_password_request")
        else None
    )
    state_change = method in audit.AUDITED_METHODS and not auth_event

    # A login attempt is attributed to the account being claimed; a password
    # reset request is anonymous but names the account it targets.
    username = _resolve_username(user_id)
    if not username and auth_event == "auth.login":
        username = submitted_user

    target = audit.safe_query_target(request.args)
    if target is None and state_change:
        target = _json_body_target()
    if target is None and auth_event == "auth.reset_password_request" and submitted_user:
        target = f"username={submitted_user}"

    return {
        "endpoint": f"{method} {audit.redact_path(path)}",
        "method": method,
        "user_id": user_id,
        "username": username,
        "action": _classify_action(method, path, endpoint, uploads, action_label),
        "modules": modules,
        "level": level,
        "target": target,
        "files": uploads,
        "auth_event": auth_event,
        "ip": request.remote_addr,
        "forwarded_for": _forwarded_for(),
    }


def _write_response_audit(response):
    plan = getattr(g, "audit_plan", None)
    if not plan or response is None:
        return response
    try:
        status, reason = _outcome(response)
        # API-key and Bearer callers are only identified once the credential has
        # been verified, which happens after the plan was built.  Their identity
        # outranks the session snapshot: a credential header is what authorized
        # the request.
        user_id = getattr(g, "user_id", None) or plan.get("user_id")
        username = _resolve_username(user_id) or plan.get("username")
        action = _final_action(plan, status)
        comment = audit.build_comment(
            action,
            module=",".join(plan["modules"]) or None,
            # Access level is only meaningful for module-protected routes.
            level=plan["level"] if plan["modules"] else None,
            target=plan.get("target"),
            files=plan.get("files") or None,
            auth=_credential_type(),
            reason=reason,
            http_status=getattr(response, "status_code", None),
            forwarded_for=plan.get("forwarded_for"),
        )
        audit.record(
            status=status,
            endpoint=plan["endpoint"],
            comment=comment,
            ip=plan.get("ip"),
            username=username,
        )
    except Exception:  # pragma: no cover - auditing must never break a request
        logger.exception("Failed to write audit event for %s", plan.get("endpoint"))
    return response


def _final_action(plan: dict, status: str) -> str:
    if plan.get("auth_event") == "auth.login":
        return "login success" if status == audit.STATUS_SUCCESS else "login failed"
    if plan.get("auth_event") == "auth.logout":
        return "logout"
    return plan.get("action") or "access"


def _outcome(response) -> tuple[str, str | None]:
    """Translate the authorization decision and HTTP status into SUCCESS/REJECT."""
    code = int(getattr(response, "status_code", 0) or 0)
    # ``g.audit_reason`` is set by the authorization layer whenever it denies.
    reason = getattr(g, "audit_reason", None)

    if reason:
        return audit.STATUS_REJECT, reason
    if code >= 400:
        return audit.STATUS_REJECT, f"http {code}"
    return audit.STATUS_SUCCESS, None


def _classify_action(
    method: str,
    path: str,
    endpoint: str,
    uploads: tuple[str, ...],
    action_label: str | None = None,
) -> str:
    lowered = (path or "").lower()
    if action_label:
        return action_label
    if uploads:
        return "file upload"
    if "download" in lowered:
        return "file download"
    if "export" in lowered:
        return "data export"
    if "backup" in lowered:
        return "backup"
    if endpoint == "auth.login":
        return "login attempt"
    if endpoint == "auth.logout":
        return "logout"
    label = _ACTION_LABELS.get(endpoint)
    if label:
        return label
    if method in audit.AUDITED_METHODS:
        return "write action"
    return "read access"


def _json_body_target() -> str | None:
    """Whitelisted target carried in a JSON request body (never a secret)."""
    if not request.is_json:
        return None
    try:
        return audit.safe_body_target(request.get_json(silent=True))
    except Exception:  # pragma: no cover - defensive
        return None


def _uploaded_names() -> tuple[str, ...]:
    """File names being uploaded with this request (multipart only)."""
    if "multipart/form-data" not in (request.content_type or "").lower():
        return ()
    try:
        return tuple(
            str(item.filename)
            for item in request.files.values()
            if getattr(item, "filename", None)
        )
    except Exception:  # pragma: no cover - defensive
        return ()


def _submitted_username() -> str | None:
    """Username claimed by a login attempt (never the password)."""
    payload = request.get_json(silent=True)
    if isinstance(payload, dict) and payload.get("username"):
        return str(payload["username"]).strip() or None
    return (request.form.get("username") or "").strip() or None


def _resolve_username(user_id) -> str | None:
    if not user_id:
        return None
    try:
        if session.get("user_id") == user_id and session.get("username"):
            return str(session["username"])
    except Exception:  # pragma: no cover - defensive
        pass
    try:
        from src.utils.user_manager import UserManager

        return UserManager().get_username_by_id(int(user_id)) or f"user:{user_id}"
    except Exception:  # pragma: no cover - defensive
        return f"user:{user_id}"


def _credential_type() -> str | None:
    """How the caller authenticated, when it was not a browser session."""
    auth_type = getattr(g, "auth_type", None)
    if auth_type in ("api_key", "token"):
        return str(auth_type)
    return None


def _forwarded_for() -> str | None:
    """First hop of X-Forwarded-For, only when it adds information."""
    value = (request.headers.get("X-Forwarded-For") or "").split(",")[0].strip()
    if value and value != (request.remote_addr or ""):
        return value
    return None
