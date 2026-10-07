from __future__ import annotations

from flask import Blueprint, jsonify, request, session

from src.agent_platform.api.api_helpers import (
    agent_access_allowed,
    attach_user_names,
    can_manage_agent,
    full_definition,
    public_definition,
    reject_invalid_definition,
    require_studio,
    unique_slug,
    validate_definition_report,
)
from src.agent_platform.api.auth import api_auth_required, current_user_id
from src.auth import resource_access
from src.auth.decorators import module_required
from src.agent_platform.catalog.store import DefinitionStore
from src.agent_platform.catalog.validate import definition_uses_scripted_client
from src.agent_platform.execution.api_keys import ApiKeyStore
from src.agent_platform.plugins import register_builtin_plugins
from src.agent_platform.plugins.registry import get_registry

catalog_bp = Blueprint("catalog_api", __name__)


@catalog_bp.get("/plugins")
@api_auth_required("agents:read")
@module_required("agent_studio")
def plugins():
    register_builtin_plugins()
    return jsonify({"plugins": get_registry().inspector_catalog()})


@catalog_bp.get("/agents")
@api_auth_required("agents:read")
def list_agents():
    """Published agents the caller may use; drafts only with studio access.

    This is the picker the chat UI reads, so a definition owned by another
    tenant (and not granted to one of the caller's roles) must never appear.
    """
    uid = current_user_id()
    include_drafts = (request.args.get("include_drafts") or "").lower() in {"1", "true", "yes"}
    if include_drafts:
        denied = require_studio()
        if denied:
            return denied
        # The Studio lists drafts with the same shape as the published list, so
        # description/pattern/author resolve for every row (one name query).
        rows = attach_user_names(resource_access.filter_visible("agent", DefinitionStore.list_all(), uid))
        return jsonify({"agents": [full_definition(r) for r in rows]})
    # Author names resolve in one query for the whole list (never N+1).
    rows = attach_user_names(resource_access.filter_visible("agent", DefinitionStore.list_published(), uid))
    return jsonify({"agents": [public_definition(r) for r in rows]})


@catalog_bp.get("/agents/<agent_id>")
@api_auth_required("agents:read")
def get_agent(agent_id: str):
    row = DefinitionStore.resolve(agent_id, published_only=False)
    if row is None:
        return jsonify({"error": "agent not found"}), 404
    if not agent_access_allowed(row):
        return jsonify({"error": "agent not found"}), 404
    want_full = (request.args.get("full") or "").lower() in {"1", "true", "yes"}
    if want_full:
        denied = require_studio()
        if denied:
            return denied
        return jsonify(full_definition(row))
    if not row.get("published"):
        # The public (non-full) shape is the published contract; a draft is
        # only reachable as a full definition through the Studio.
        return jsonify({"error": "agent not found"}), 404
    return jsonify(public_definition(row))


@catalog_bp.post("/agents")
@api_auth_required("agents:write")
def create_agent():
    denied = require_studio()
    if denied:
        return denied
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip() or "Untitled"
    slug = unique_slug(data.get("slug") or name)
    kind = (data.get("kind") or (data.get("config") or {}).get("kind") or "agent").lower()
    if kind not in {"agent", "workflow"}:
        return jsonify({"error": "kind must be agent or workflow"}), 400
    config = dict(data.get("config") or {})
    config.setdefault("kind", kind)
    published = bool(data.get("published"))
    if published:
        denied_pub = reject_invalid_definition({"slug": slug, "name": name, "kind": kind, "config": config})
        if denied_pub:
            return denied_pub
    row = DefinitionStore.save(
        slug=slug,
        name=name,
        kind=kind,
        config=config,
        published=published,
        created_by=current_user_id(),
        updated_by=current_user_id(),
        bump_version=False,
    )
    return jsonify(full_definition(row)), 201


@catalog_bp.put("/agents/<agent_id>")
@api_auth_required("agents:write")
def update_agent(agent_id: str):
    denied = require_studio()
    if denied:
        return denied
    row = DefinitionStore.resolve(agent_id)
    if row is None:
        return jsonify({"error": "agent not found"}), 404
    if not agent_access_allowed(row):
        return jsonify({"error": "forbidden", "message": "You do not have access to this agent"}), 403
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or row.get("name") or "").strip() or row["name"]
    slug = (data.get("slug") or row.get("slug") or "").strip() or row["slug"]
    if slug != row.get("slug"):
        other = DefinitionStore.get_by_slug(slug)
        if other and int(other["id"]) != int(row["id"]):
            slug = unique_slug(slug)
    kind = (data.get("kind") or row.get("kind") or "agent").lower()
    config = dict(data.get("config") or row.get("config") or {})
    config["kind"] = kind
    published = data.get("published")
    will_publish = bool(row.get("published")) if published is None else bool(published)
    if will_publish and (published or definition_uses_scripted_client({"kind": kind, "config": config})):
        denied_pub = reject_invalid_definition({"slug": slug, "name": name, "kind": kind, "config": config})
        if denied_pub:
            return denied_pub
    bump = False
    saved = DefinitionStore.save(
        definition_id=int(row["id"]),
        slug=slug,
        name=name,
        kind=kind,
        config=config,
        published=None if published is None else bool(published),
        updated_by=current_user_id(),
        bump_version=bump,
    )
    return jsonify(full_definition(saved))


@catalog_bp.delete("/agents/<agent_id>")
@api_auth_required("agents:write")
def delete_agent(agent_id: str):
    denied = require_studio()
    if denied:
        return denied
    row = DefinitionStore.resolve(agent_id)
    if row is None:
        return jsonify({"error": "agent not found"}), 404
    # Deleting an agent is owner-or-admin: a role granted on the agent confers
    # use, never the right to destroy another tenant's work.
    if not can_manage_agent(row):
        return jsonify({"error": "forbidden", "message": "Only the owner or an administrator may delete this agent"}), 403
    DefinitionStore.delete(int(row["id"]))
    return jsonify({"success": True, "id": row["id"]})


@catalog_bp.post("/agents/<agent_id>/publish")
@api_auth_required("agents:write")
def publish_agent(agent_id: str):
    denied = require_studio()
    if denied:
        return denied
    row = DefinitionStore.resolve(agent_id)
    if row is None:
        return jsonify({"error": "agent not found"}), 404
    if not agent_access_allowed(row):
        return jsonify({"error": "forbidden", "message": "You do not have access to this agent"}), 403
    data = request.get_json(silent=True) or {}
    published = True if "published" not in data else bool(data.get("published"))
    if published:
        denied_pub = reject_invalid_definition(row)
        if denied_pub:
            return denied_pub
    saved = DefinitionStore.publish(int(row["id"]), published, actor=current_user_id())
    return jsonify(full_definition(saved))


@catalog_bp.get("/agents/<agent_id>/versions")
@api_auth_required("agents:read")
def list_agent_versions(agent_id: str):
    denied = require_studio()
    if denied:
        return denied
    row = DefinitionStore.resolve(agent_id)
    if row is None:
        return jsonify({"error": "agent not found"}), 404
    if not agent_access_allowed(row):
        return jsonify({"error": "agent not found"}), 404
    return jsonify({"versions": DefinitionStore.list_versions(int(row["id"]))})


@catalog_bp.get("/agents/<agent_id>/versions/<int:ver>")
@api_auth_required("agents:read")
def get_agent_version(agent_id: str, ver: int):
    denied = require_studio()
    if denied:
        return denied
    row = DefinitionStore.resolve(agent_id)
    if row is None:
        return jsonify({"error": "agent not found"}), 404
    if not agent_access_allowed(row):
        return jsonify({"error": "agent not found"}), 404
    snap = DefinitionStore.get_version(int(row["id"]), int(ver))
    if snap is None:
        return jsonify({"error": "version not found"}), 404
    return jsonify(snap)


@catalog_bp.post("/agents/<agent_id>/rollback")
@api_auth_required("agents:write")
def rollback_agent(agent_id: str):
    denied = require_studio()
    if denied:
        return denied
    row = DefinitionStore.resolve(agent_id)
    if row is None:
        return jsonify({"error": "agent not found"}), 404
    if not agent_access_allowed(row):
        return jsonify({"error": "forbidden", "message": "You do not have access to this agent"}), 403
    data = request.get_json(silent=True) or {}
    ver = data.get("version")
    try:
        ver = int(ver)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return jsonify({"error": "version is required"}), 400
    restored = DefinitionStore.rollback(int(row["id"]), ver, actor=current_user_id())
    if restored is None:
        return jsonify({"error": "version not found"}), 404
    return jsonify(full_definition(restored))


@catalog_bp.post("/agents/<agent_id>/clone")
@api_auth_required("agents:write")
def clone_agent(agent_id: str):
    denied = require_studio()
    if denied:
        return denied
    row = DefinitionStore.resolve(agent_id)
    if row is None:
        return jsonify({"error": "agent not found"}), 404
    if not agent_access_allowed(row):
        return jsonify({"error": "agent not found"}), 404
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or f"{row.get('name')} copy").strip() or f"{row.get('name')} copy"
    slug = unique_slug((data.get("slug") or name).strip() or name)
    kind = (row.get("kind") or "agent").lower()
    config = dict(row.get("config") or {})
    config["kind"] = kind
    cloned = DefinitionStore.save(
        slug=slug,
        name=name,
        kind=kind,
        config=config,
        published=False,
        created_by=current_user_id(),
        updated_by=current_user_id(),
        bump_version=False,
    )
    return jsonify(full_definition(cloned)), 201


@catalog_bp.post("/agents/<agent_id>/validate")
@api_auth_required("agents:write")
@module_required("agent_studio", read_methods=("GET", "HEAD", "OPTIONS", "POST"))
def validate_agent(agent_id: str):
    denied = require_studio("read")
    if denied:
        return denied
    data = request.get_json(silent=True) or {}
    row = DefinitionStore.resolve(agent_id)
    # A stored definition the caller cannot access must not feed the report
    # (neither its config nor its existence), so it is treated as absent and
    # only an inline draft in the request body is validated.
    if row is not None and not agent_access_allowed(row):
        row = None
    definition = {
        "slug": data.get("slug") or (row or {}).get("slug"),
        "name": data.get("name") or (row or {}).get("name"),
        "kind": data.get("kind") or (row or {}).get("kind") or "agent",
        "config": data.get("config") or (row or {}).get("config") or {},
    }
    if row is None and not data.get("config"):
        return jsonify({"error": "agent not found"}), 404
    return jsonify(validate_definition_report(definition, user_id=current_user_id()))


@catalog_bp.post("/agents/validate")
@api_auth_required("agents:write")
@module_required("agent_studio", read_methods=("GET", "HEAD", "OPTIONS", "POST"))
def validate_draft():
    denied = require_studio("read")
    if denied:
        return denied
    data = request.get_json(silent=True) or {}
    return jsonify(
        validate_definition_report(
            {
                "slug": data.get("slug"),
                "name": data.get("name"),
                "kind": data.get("kind") or (data.get("config") or {}).get("kind") or "agent",
                "config": data.get("config") or {},
            },
            user_id=current_user_id(),
        )
    )


@catalog_bp.get("/skills")
@api_auth_required("agents:read")
def list_skills():
    denied = require_studio()
    if denied:
        return denied
    from src.agent_platform.catalog.skill_packages import list_packages, visible_skill_names

    packages = list_packages()
    allowed = visible_skill_names(current_user_id())
    if allowed is not None:
        packages = [pkg for pkg in packages if str(pkg.get("name")) in allowed]
    return jsonify({"skills": packages})


@catalog_bp.get("/skills/<name>")
@api_auth_required("agents:read")
def get_skill(name: str):
    denied = require_studio()
    if denied:
        return denied
    from src.agent_platform.catalog.skill_packages import can_access_skill, read_package

    if not can_access_skill(name, current_user_id()):
        return jsonify({"error": "skill not found"}), 404
    row = read_package(name)
    if row is None:
        return jsonify({"error": "skill not found"}), 404
    return jsonify(row)


@catalog_bp.post("/skills")
@api_auth_required("agents:write")
def create_skill():
    denied = require_studio()
    if denied:
        return denied
    from src.agent_platform.catalog.skill_packages import (
        can_access_skill,
        package_dir,
        slugify_skill,
        write_package,
    )

    data = request.get_json(silent=True) or {}
    name = str(data.get("name") or "")
    slug = slugify_skill(name)
    # A create whose name already exists is an overwrite of that package: the
    # caller must be able to see it, or a POST would silently replace another
    # tenant's skill files.
    if package_dir(slug) is not None and not can_access_skill(slug, current_user_id()):
        return jsonify({"error": "forbidden", "message": f"You do not have access to skill '{name}'"}), 403
    try:
        row = write_package(
            name=name,
            description=str(data.get("description") or ""),
            instructions=str(data.get("instructions") or ""),
            scripts=list(data.get("scripts") or []),
            created_by=current_user_id(),
            actor_id=current_user_id(),
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except PermissionError as exc:
        return jsonify({"error": "forbidden", "message": str(exc)}), 403
    return jsonify(row), 201


@catalog_bp.put("/skills/<name>")
@api_auth_required("agents:write")
def update_skill(name: str):
    denied = require_studio()
    if denied:
        return denied
    from src.agent_platform.catalog.skill_packages import write_package

    data = request.get_json(silent=True) or {}
    try:
        row = write_package(
            name=str(data.get("name") or name),
            description=str(data.get("description") or ""),
            instructions=str(data.get("instructions") or ""),
            scripts=list(data.get("scripts") or []),
            previous_name=name,
            created_by=current_user_id(),
            actor_id=current_user_id(),
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except PermissionError as exc:
        return jsonify({"error": "forbidden", "message": str(exc)}), 403
    return jsonify(row)


@catalog_bp.delete("/skills/<name>")
@api_auth_required("agents:write")
def delete_skill(name: str):
    denied = require_studio()
    if denied:
        return denied
    from src.agent_platform.catalog.skill_packages import delete_package

    try:
        ok = delete_package(name, actor_id=current_user_id())
    except PermissionError as exc:
        return jsonify({"error": "forbidden", "message": str(exc)}), 403
    if not ok:
        return jsonify({"error": "skill not found"}), 404
    return jsonify({"success": True})


@catalog_bp.get("/api-keys")
@api_auth_required("agents:read")
def list_api_keys():
    denied = require_studio()
    if denied:
        return denied
    return jsonify({"api_keys": ApiKeyStore.list_keys()})


@catalog_bp.post("/api-keys")
@api_auth_required("agents:write")
def create_api_key():
    denied = require_studio()
    if denied:
        return denied
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip() or "Default Key"
    scopes = list(data.get("scopes") or ["runs:write", "agents:read"])
    uid = getattr(request, "agent_user_id", None) or session.get("user_id") or 1
    row = ApiKeyStore.create(user_id=int(uid), name=name, scopes=scopes)
    return jsonify(row), 201


@catalog_bp.delete("/api-keys/<int:key_id>")
@api_auth_required("agents:write")
def delete_api_key(key_id: int):
    denied = require_studio()
    if denied:
        return denied
    ok = ApiKeyStore.delete(key_id)
    if not ok:
        return jsonify({"error": "key not found"}), 404
    return jsonify({"success": True})


from src.agent_platform.api.studio_service import (
    fetch_mcp_server_tools,
    fetch_studio_resources,
    fetch_studio_roles,
    resolve_agent_for_management,
)


def _studio_resources() -> dict:
    return fetch_studio_resources(current_user_id())


@catalog_bp.get("/models")
@api_auth_required("runs:read")
def list_models():
    from src.agent_platform.runtime.model_select import list_public_models

    return jsonify({"models": list_public_models(current_user_id())})


@catalog_bp.get("/studio/resources")
@api_auth_required("agents:read")
def studio_resources():
    denied = require_studio()
    if denied:
        return denied
    return jsonify(_studio_resources())


@catalog_bp.get("/studio/mcp-servers/<int:server_id>/tools")
@api_auth_required("agents:read")
def studio_mcp_server_tools(server_id: int):
    """Live list_tools for a single MCP server (studio inspector)."""
    denied = require_studio()
    if denied:
        return denied
    data, code = fetch_mcp_server_tools(server_id, current_user_id())
    if data is None:
        return jsonify({"error": "server not found"}), code
    return jsonify(data)


@catalog_bp.get("/studio/roles")
@api_auth_required("agents:read")
def studio_roles():
    denied = require_studio()
    if denied:
        return denied
    return jsonify({"roles": fetch_studio_roles()})


def _agent_owner_or_admin(agent_id: str):
    """Resolve an agent for the legacy access endpoints (owner/admin only)."""
    row, err, code = resolve_agent_for_management(agent_id)
    if err:
        return None, (jsonify(err), code)
    return row, None


@catalog_bp.get("/agents/<agent_id>/access")
@api_auth_required("agents:read")
def get_agent_access(agent_id: str):
    denied = require_studio()
    if denied:
        return denied
    row, error = _agent_owner_or_admin(agent_id)
    if error is not None:
        return error
    return jsonify({"access": DefinitionStore.list_access(int(row["id"]))})


@catalog_bp.post("/agents/<agent_id>/access")
@api_auth_required("agents:write")
def grant_agent_access(agent_id: str):
    denied = require_studio()
    if denied:
        return denied
    row, error = _agent_owner_or_admin(agent_id)
    if error is not None:
        return error
    data = request.get_json(silent=True) or {}
    role_ids = data.get("role_ids")
    if role_ids is None and "role_id" in data:
        role_ids = [data.get("role_id")]
    if not role_ids or not isinstance(role_ids, list):
        return jsonify({"error": "role_ids is required"}), 400
    uid = current_user_id()
    for rid in role_ids:
        try:
            DefinitionStore.grant_role_access(int(row["id"]), int(rid), granted_by=uid)
        except (TypeError, ValueError):
            continue
    return jsonify({"access": DefinitionStore.list_access(int(row["id"]))})


@catalog_bp.delete("/agents/<agent_id>/access/role/<int:role_id>")
@api_auth_required("agents:write")
def revoke_agent_access(agent_id: str, role_id: int):
    denied = require_studio()
    if denied:
        return denied
    row, error = _agent_owner_or_admin(agent_id)
    if error is not None:
        return error
    DefinitionStore.revoke_role_access(int(row["id"]), int(role_id))
    return jsonify({"access": DefinitionStore.list_access(int(row["id"]))})


@catalog_bp.delete("/agents/<agent_id>/access")
@api_auth_required("agents:write")
def revoke_agent_access_payload(agent_id: str):
    denied = require_studio()
    if denied:
        return denied
    row, error = _agent_owner_or_admin(agent_id)
    if error is not None:
        return error
    data = request.get_json(silent=True) or {}
    role_ids = data.get("role_ids")
    if role_ids is None and "role_id" in data:
        role_ids = [data.get("role_id")]
    if role_ids and isinstance(role_ids, list):
        for rid in role_ids:
            try:
                DefinitionStore.revoke_role_access(int(row["id"]), int(rid))
            except (TypeError, ValueError):
                continue
    return jsonify({"access": DefinitionStore.list_access(int(row["id"]))})

