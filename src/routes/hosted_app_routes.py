"""Serving hosted apps, plus the admin API that manages them.

An app is reachable at exactly one URL - ``/apps/<slug>/`` - on the platform's
own port. It has no port of its own: the platform owns the listening socket and
the app inherits the descriptor, so ``socket()``/``bind()``/``connect()`` are all
denied to it.

Because this is an ordinary authenticated Flask blueprint, hosted-app traffic
goes through the same authorization choke point and audit middleware as every
other page: nothing about the app can exempt it from either.
"""

from __future__ import annotations

import os
import re

from flask import Blueprint, abort, g, jsonify, redirect, request, session

from src.auth import resource_access
from src.auth.decorators import admin_required, module_required
from src.hosting import capabilities, identity, installer, origin, policy, proxy, settings, supervisor
from src.models.hosted_app import HOSTED_APP_RESOURCE_TYPE, HostedApp
from src.utils.auth_utils import login_required
from src.utils.user_manager import UserManager

hosted_app_bp = Blueprint("hosted_apps", __name__)

#: Slug shape accepted in URLs; anything else is a 404 rather than a 500.
SLUG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")

#: Cap on a request body forwarded to a hosted app (bytes).
MAX_REQUEST_BYTES = settings.max_body_bytes()


def _app_payload(record) -> dict:
    """The app plus the facts only the supervisor knows: where it lives, and who may open it."""
    payload = record.to_dict()
    payload["access"] = [row["role_id"] for row in HostedApp.list_access(record.slug)]
    payload["workspace"] = str(supervisor.app_root(record.slug))
    payload["can_manage"] = _user_can_manage(record, session.get("user_id"))
    return payload


def _user_can_use(record, user_id) -> bool:
    """Whether this user may open this application.

    The canonical resource decision, shared with every other tenanted asset:
    an administrator may open anything; the owner may open their own app; a row
    with no owner is grandfathered to every authenticated user; otherwise the
    caller must hold a role granted on this app. Module access is the separate
    coarse gate and is enforced by the route decorator.
    """
    return resource_access.can_access(
        HOSTED_APP_RESOURCE_TYPE, record.id, record.created_by, user_id
    )


def _user_can_manage(record, user_id) -> bool:
    """Whether this user may change this application or its lifecycle.

    Deliberately narrower than *using* an app: renaming it, changing its
    content policy or access list, starting/stopping it, reinstalling it (which
    runs its dependency install), reading its logs or deleting it belongs to an
    administrator or to the person who imported it. A role granted access may
    open the app but must not be able to take it over, including its logs and
    its install pipeline.
    """
    if user_id is None:
        return False
    try:
        if UserManager().has_role(user_id, "admin"):
            return True
        return record.created_by is not None and int(record.created_by) == int(user_id)
    except Exception:
        return False


def _manage_denied():
    g.audit_reason = "not an administrator or the owner of this hosted application"
    return jsonify({
        "error": "forbidden",
        "message": "Only an administrator or the application's owner can manage this application",
    }), 403


def _access_denied():
    g.audit_reason = "no access to this hosted application"
    if "/api/" in request.path or request.is_json:
        return jsonify({"error": "forbidden",
                        "message": "You do not have access to this application"}), 403
    if not origin.is_apps_origin(request):
        abort(403)  # the platform's 403 page, rendered by the SPA
    from flask import make_response

    return origin.message_page(
        request, 403, "Forbidden", "You do not have access to this application."
    )


def _not_found(slug: str):
    """A missing app, as a page for a browser and JSON for an API client."""
    if origin.wants_html(request):
        return origin.message_page(
            request, 404, "Application not found",
            f"There is no hosted application named &lsquo;{slug}&rsquo;.",
        )
    return jsonify({"error": "not_found", "message": f"No hosted app named '{slug}'"}), 404


def _origin_gate(path: str | None = None):
    """``None`` to serve here, a 308 to the apps origin, or a 503 refusal.

    App content is never served on the platform's own origin: a document there is
    same-origin with the platform's own JavaScript, so it could call platform APIs
    as the signed-in user. A request that *arrives* on the apps origin is served
    (that origin may be an operator-provided process this one knows nothing
    about); a request on the platform origin is redirected there, and refused with
    an explanation when that origin is not available - falling back to same-origin
    serving would silently restore exactly what this removes.
    """
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


def _roles_for(user_id) -> str:
    try:
        user = UserManager().get_user_by_id(int(user_id))
        return ",".join(sorted(role.name for role in (user.roles or []))) if user else ""
    except Exception:
        return ""


def _username_for(user_id) -> str:
    try:
        return session.get("username") or UserManager().get_username_by_id(int(user_id)) or ""
    except Exception:
        return str(user_id or "")


# --- admin page -----------------------------------------------------------

@hosted_app_bp.route("/admin/hosted-apps")
@login_required
@module_required("hosted_apps")
def hosted_apps_page():
    """Render the React admin page; the client routes on the pathname."""
    from src.routes.agent_routes import _render_admin_app

    return _render_admin_app()


# --- serving --------------------------------------------------------------

@hosted_app_bp.route("/apps/<slug>", methods=["GET", "HEAD"])
@login_required
@module_required("hosted_apps")
def hosted_app_root(slug: str):
    """Without the trailing slash, relative links in the app resolve one level up."""
    if not SLUG_PATTERN.match(slug):
        return jsonify({"error": "not_found", "message": f"No hosted app named '{slug}'"}), 404
    if not origin.is_apps_origin(request):
        # Straight to the apps origin, trailing slash included: one hop, not two.
        gate = _origin_gate(path=f"/apps/{slug}/")
        if gate is not None:
            return gate
    # This route is the bare form of the app URL, so it carries the same
    # per-application gate as the content route below: a redirect is not a way
    # around the access rule.
    record = HostedApp.get_by_slug(slug)
    if record is None:
        return _not_found(slug)
    if not _user_can_use(record, session.get("user_id")):
        return _access_denied()
    return redirect(f"/apps/{slug}/", code=308)


@hosted_app_bp.route("/apps/<slug>/", defaults={"subpath": ""},
                     methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"])
@hosted_app_bp.route("/apps/<slug>/<path:subpath>",
                     methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"])
@login_required
@module_required("hosted_apps")
def hosted_app_proxy(slug: str, subpath: str):
    if not SLUG_PATTERN.match(slug):
        return jsonify({"error": "not_found", "message": f"No hosted app named '{slug}'"}), 404
    gate = _origin_gate()
    if gate is not None:
        return gate
    record = HostedApp.get_by_slug(slug)
    if record is None:
        return _not_found(slug)
    # A network gate, judged before anything else: the app answers only the
    # addresses its operator named. The admin API that sets this list lives on the
    # platform's own path, so a wrong list is always fixable.
    ip_block = policy.check_source_ip(request.remote_addr, policy.for_record(record))
    if ip_block:
        return proxy.blocked_response(
            "Blocked by the source IP policy", ip_block, 403,
            "blocked_by_source_ip", "source IP policy",
        )
    if not _user_can_use(record, session.get("user_id")):
        return _access_denied()
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
    # request carries no Content-Length, so trusting the header alone let an
    # arbitrarily large body be buffered in the platform process (measured).
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
    headers.update(identity.build_headers(user_id, _username_for(user_id), _roles_for(user_id)))

    return proxy.forward(record, subpath, body, headers)


# --- admin API ------------------------------------------------------------

@hosted_app_bp.get("/admin/api/hosted-apps")
@login_required
@module_required("hosted_apps")
def list_hosted_apps():
    supervisor.reconcile()
    caps = capabilities.detect()
    separate_origin = not settings.same_origin_mode()
    # Only the apps this caller may open: the canonical decision, in one pass
    # over the rows (two queries regardless of how many apps exist). An admin
    # sees every app, an owner sees their own, a granted role sees its apps, and
    # an owner-less legacy row is visible to every authenticated user.
    records = HostedApp.get_all()
    visible_ids = {
        row["id"]
        for row in resource_access.filter_visible(
            HOSTED_APP_RESOURCE_TYPE,
            [record.to_dict() for record in records],
            session.get("user_id"),
        )
    }
    apps = [_app_payload(record) for record in records if record.id in visible_ids]
    return jsonify({
        "apps": apps,
        "origin": {
            "separate": separate_origin,
            "url": origin.apps_origin_for(request) if separate_origin else "",
            "text": origin.describe(),
        },
        "capabilities": caps,
        "capabilities_text": capabilities.describe(caps),
        "tier_guide": capabilities.tier_guide(),
        "platform_controls": capabilities.platform_controls(),
        "configured_tier": capabilities.settings.configured_tier(),
    })


@hosted_app_bp.post("/admin/api/hosted-apps/import")
@login_required
@module_required("hosted_apps")
@admin_required()
def import_hosted_app():
    upload = request.files.get("file")
    if upload is None:
        return jsonify({"error": "A .zip file is required"}), 400
    filename = (upload.filename or "").strip()
    if filename and not filename.lower().endswith(".zip"):
        return jsonify({"error": "Only .zip archives are supported"}), 400

    from src.hosting.archive import MAX_ARCHIVE_BYTES, ArchiveError

    blob = upload.read(MAX_ARCHIVE_BYTES + 1)
    if len(blob) > MAX_ARCHIVE_BYTES:
        return jsonify({"error": f"Archive is larger than {MAX_ARCHIVE_BYTES // (1024 * 1024)} MB"}), 413
    try:
        # Returns as soon as the files are extracted: the dependency install
        # runs in the background and is watched through the install endpoint.
        start_when_ready = str(request.form.get("start") or "").lower() in {"1", "true", "yes", "on"}
        record = installer.begin_install(
            blob, created_by=session.get("user_id"), start_when_ready=start_when_ready
        )
    except ArchiveError as exc:
        return jsonify({"error": str(exc)}), 400
    except supervisor.HostingError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:  # surfaced to the UI verbatim
        return jsonify({"error": f"Import failed: {exc}"}), 500

    start_when_ready = str(request.form.get("start") or "").lower() in {"1", "true", "yes"}
    return jsonify({"app": _app_payload(record), "start_when_ready": start_when_ready}), 202


@hosted_app_bp.get("/admin/api/hosted-apps/<slug>/install")
@login_required
@module_required("hosted_apps")
@admin_required()
def hosted_app_install_status(slug: str):
    """Progress of an install, with the log so far, for the import dialog.

    Deploying is administrator-only, and this is the deploy's progress channel,
    so it is administrator-only too; ``_user_can_manage`` is kept as the second,
    resource-scoped gate.
    """
    record = HostedApp.get_by_slug(slug)
    if record is None:
        return jsonify({"error": "not found"}), 404
    if not _user_can_manage(record, session.get("user_id")):
        return _manage_denied()
    return jsonify(installer.install_status(slug))


@hosted_app_bp.put("/admin/api/hosted-apps/<slug>")
@login_required
@module_required("hosted_apps")
def update_hosted_app(slug: str):
    """Rename an application, change its description, or set its boot behaviour.

    The slug - and so the URL - is deliberately immutable, so links and audit
    history stay valid.
    """
    record = HostedApp.get_by_slug(slug)
    if record is None:
        return jsonify({"error": "not found"}), 404
    if not _user_can_manage(record, session.get("user_id")):
        return _manage_denied()
    data = request.get_json(silent=True) or {}
    name = data.get("name")
    if name is not None and not str(name).strip():
        return jsonify({"error": "Name cannot be empty"}), 400
    autostart = data.get("autostart")
    if autostart is not None and not isinstance(autostart, bool):
        return jsonify({"error": "autostart must be true or false"}), 400
    record = HostedApp.update_details(
        slug, name=None if name is None else str(name),
        description=None if "description" not in data else str(data.get("description") or ""),
        autostart=autostart,
    )
    return jsonify({"app": _app_payload(record) if record else None})


@hosted_app_bp.route("/admin/api/hosted-apps/<slug>/access", methods=["GET", "PUT", "POST"])
@login_required
@module_required("hosted_apps")
def hosted_app_access(slug: str):
    """Role grants for one application, plus the roles available to grant."""
    record = HostedApp.get_by_slug(slug)
    if record is None:
        return jsonify({"error": "not found"}), 404
    if not _user_can_manage(record, session.get("user_id")):
        return _manage_denied()

    if request.method == "PUT":
        # Replace the whole grant set (used by scripts and tests).
        data = request.get_json(silent=True) or {}
        role_ids = data.get("roles")
        if not isinstance(role_ids, list):
            return jsonify({"error": "roles must be a list of role ids"}), 400
        HostedApp.set_access(slug, role_ids, granted_by=session.get("user_id"))
    elif request.method == "POST":
        # Grant one or more roles without disturbing the others, so the dialog
        # can act immediately the way the Agent Studio access dialog does.
        data = request.get_json(silent=True) or {}
        role_ids = data.get("role_ids", [])
        if not isinstance(role_ids, list) or not role_ids:
            return jsonify({"error": "role_ids must be a non-empty list"}), 400
        current = [row["role_id"] for row in HostedApp.list_access(slug)]
        HostedApp.set_access(slug, current + [int(r) for r in role_ids if str(r).isdigit()],
                             granted_by=session.get("user_id"))

    roles = []
    try:
        for role in UserManager().get_all_roles():
            roles.append({
                "id": role.id,
                "name": role.name,
                "description": getattr(role, "description", "") or "",
                "user_count": len(getattr(role, "users", []) or []),
            })
    except Exception:
        roles = []
    return jsonify({"access": HostedApp.list_access(slug), "roles": roles})


@hosted_app_bp.delete("/admin/api/hosted-apps/<slug>/access/role/<int:role_id>")
@login_required
@module_required("hosted_apps")
def revoke_hosted_app_access(slug: str, role_id: int):
    record = HostedApp.get_by_slug(slug)
    if record is None:
        return jsonify({"error": "not found"}), 404
    if not _user_can_manage(record, session.get("user_id")):
        return _manage_denied()
    remaining = [row["role_id"] for row in HostedApp.list_access(slug) if int(row["role_id"]) != role_id]
    HostedApp.set_access(slug, remaining, granted_by=session.get("user_id"))
    return jsonify({"access": HostedApp.list_access(slug)})


@hosted_app_bp.post("/admin/api/hosted-apps/<slug>/<action>")
@login_required
@module_required("hosted_apps")
def control_hosted_app(slug: str, action: str):
    record = HostedApp.get_by_slug(slug)
    if record is None:
        return jsonify({"error": "not found"}), 404
    # Start/stop/restart change the shared runtime (the socket, the process), so
    # they are owner-or-admin exactly like the other lifecycle operations. A
    # role granted *access* to the app may open it but not decide whether it is
    # running; the module holder that can only open an app must not be able to
    # stop everyone else's.
    if not _user_can_manage(record, session.get("user_id")):
        return _manage_denied()
    if action == "start":
        if record.status == "installing":
            return jsonify({
                "ok": False, "app": _app_payload(record),
                "message": "Still installing: watch the install log until it finishes.",
            }), 409
        ok, message = supervisor.start(slug)
    elif action == "stop":
        ok, message = supervisor.stop(slug)
    elif action == "restart":
        ok, message = supervisor.restart(slug)
    elif action == "reinstall":
        ok, message = installer.reinstall(slug)
    else:
        return jsonify({"error": f"Unknown action '{action}'"}), 400
    record = HostedApp.get_by_slug(slug)
    return jsonify({
        "ok": ok,
        "message": message,
        "app": _app_payload(record) if record else None,
    }), (200 if ok else 409)


@hosted_app_bp.route("/admin/api/hosted-apps/<slug>/policy", methods=["GET", "PUT"])
@login_required
@module_required("hosted_apps")
def hosted_app_content_policy(slug: str):
    """Which content types *this* application may send back, and how much of each."""
    record = HostedApp.get_by_slug(slug)
    if record is None:
        return jsonify({"error": "not found"}), 404
    if not _user_can_manage(record, session.get("user_id")):
        return _manage_denied()
    if request.method == "PUT":
        data = request.get_json(silent=True) or {}
        limits = data.get("content_type_limits")
        if limits is not None and not isinstance(limits, dict):
            return jsonify({"error": "content_type_limits must be an object of byte caps"}), 400
        source_ips = data.get("source_ip_allowlist")
        if source_ips is not None and not isinstance(source_ips, list):
            return jsonify({"error": "source_ip_allowlist must be a list of address rules"}), 400
        invalid = policy.invalid_ip_rules(source_ips) if source_ips is not None else []
        if invalid:
            return jsonify({
                "error": "invalid_ip_rule",
                "message": "Not an address, range or wildcard: " + ", ".join(invalid),
            }), 400
        current = policy.for_record(record)
        policy.save_for_slug(slug, {
            "allowed_content_types": data.get("allowed_content_types", current["allowed_content_types"]),
            "block_attachments": data.get("block_attachments", current["block_attachments"]),
            "content_type_limits": current["content_type_limits"] if limits is None else limits,
            "source_ip_allowlist": current["source_ip_allowlist"] if source_ips is None else source_ips,
        })
        record = HostedApp.get_by_slug(slug)
    return jsonify({
        "policy": policy.for_record(record),
        "recommended": list(policy.RECOMMENDED_CONTENT_TYPES),
        "max_limit_bytes": policy.MAX_LIMIT_BYTES,
    })


@hosted_app_bp.get("/admin/api/hosted-apps/<slug>/logs")
@login_required
@module_required("hosted_apps")
def hosted_app_logs(slug: str):
    """The tail of an application's log: newest line first, in ``log``."""
    record = HostedApp.get_by_slug(slug)
    if record is None:
        return jsonify({"error": "not found"}), 404
    if not _user_can_manage(record, session.get("user_id")):
        return _manage_denied()
    try:
        lines = max(1, min(int(request.args.get("lines", 200)), 2000))
    except ValueError:
        lines = 200
    source = "install" if request.args.get("source") == "install" else "app"
    return jsonify({"slug": slug, "source": source, "log": supervisor.logs(slug, lines, source)})


@hosted_app_bp.delete("/admin/api/hosted-apps/<slug>")
@login_required
@module_required("hosted_apps")
def delete_hosted_app(slug: str):
    record = HostedApp.get_by_slug(slug)
    if record is None:
        return jsonify({"error": "not found"}), 404
    if not _user_can_manage(record, session.get("user_id")):
        return _manage_denied()
    ok, message = supervisor.uninstall(slug)
    return jsonify({"ok": ok, "message": message})
