"""Reverse proxy request forwarding, header sanitization, and body budgeting for hosted applications.

Extracted from hosted_app_routes.py to decouple low-level HTTP reverse proxy forwarding
from administrative CRUD routes.
"""

from __future__ import annotations

from typing import Any
from flask import jsonify, request, session

from src.hosting import identity, origin, policy, proxy, settings, supervisor
from src.utils.user_manager import UserManager

MAX_REQUEST_BYTES = settings.max_body_bytes()


def handle_origin_gate(path: str | None = None) -> Any:
    """None to serve here, a 308 to the apps origin, or a 503 refusal."""
    if origin.is_apps_origin(request):
        return None
    if not origin.available():
        return jsonify({
            "error": "apps_origin_unavailable",
            "message": (
                "Hosted apps are served from their own origin, and it is not listening. "
                f"Check HOSTED_APPS_ORIGIN_PORT. ({origin.describe()})"
            ),
        }), 503
    return origin.redirect_for(request, path)


def roles_for_user(user_id: Any) -> str:
    try:
        user = UserManager().get_user_by_id(int(user_id))
        return ",".join(sorted(role.name for role in (user.roles or []))) if user else ""
    except Exception:
        return ""


def username_for_user(user_id: Any) -> str:
    try:
        return session.get("username") or UserManager().get_username_by_id(int(user_id)) or ""
    except Exception:
        return str(user_id or "")


def forward_app_request(record: Any, subpath: str) -> Any:
    """Check IP policy, status, body budget, sanitize headers, and forward request to unix socket."""
    slug = record.slug

    # A network gate, judged before anything else: the app answers only the
    # addresses its operator named.
    ip_block = policy.check_source_ip(request.remote_addr, policy.for_record(record))
    if ip_block:
        return proxy.blocked_response(
            "Blocked by the source IP policy", ip_block, 403,
            "blocked_by_source_ip", "source IP policy",
        )

    if supervisor.get_status(slug) != "running":
        if origin.wants_html(request):
            return origin.message_page(
                request, 503, "Application not running",
                f"&lsquo;{record.name}&rsquo; is not running. An administrator can start it "
                "from Hosted Apps.",
            )
        return jsonify({
            "error": "not_running",
            "message": f"'{record.name}' is not running.",
        }), 503

    declared = request.content_length or 0
    if declared > MAX_REQUEST_BYTES:
        return jsonify({
            "error": "payload_too_large",
            "message": f"Request body exceeds {MAX_REQUEST_BYTES // (1024 * 1024)} MB",
        }), 413

    # The body is read with its own budget, not the declared one: a chunked
    # request carries no Content-Length.
    try:
        body = request.stream.read(MAX_REQUEST_BYTES + 1) if request.method not in ("GET", "HEAD") else None
    except Exception:
        return jsonify({"error": "bad_request", "message": "The request body could not be read"}), 400
    if body is not None and len(body) > MAX_REQUEST_BYTES:
        return jsonify({
            "error": "payload_too_large",
            "message": f"Request body exceeds {MAX_REQUEST_BYTES // (1024 * 1024)} MB",
        }), 413

    headers = {key: value for key, value in request.headers.items() if key.lower() not in proxy.REQUEST_DROP}
    cookie_header = proxy.forwarded_cookie_header(slug)
    if cookie_header:
        headers["Cookie"] = cookie_header
    headers["X-Forwarded-Prefix"] = f"/apps/{slug}"
    headers["X-Forwarded-Host"] = request.host
    headers["X-Forwarded-Proto"] = request.scheme
    if request.remote_addr:
        headers["X-Forwarded-For"] = request.remote_addr

    user_id = session.get("user_id")
    headers.update(identity.build_headers(user_id, username_for_user(user_id), roles_for_user(user_id)))

    return proxy.forward(record, subpath, body, headers)
