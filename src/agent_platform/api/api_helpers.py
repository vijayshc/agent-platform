from __future__ import annotations

import re
import sqlite3
from typing import Any
from flask import g, jsonify, request

from src.agent_platform import db
from src.agent_platform.api.auth import current_user_id
from src.agent_platform.catalog.store import DefinitionStore
from src.agent_platform.catalog.validate import (
    definition_uses_scripted_client,
    scripted_client_allowed,
    validate_definition,
)
from src.models.secrets import mask_value


def owns_definition(row: dict[str, Any] | None) -> bool:
    if not row:
        return False
    user_id = current_user_id()
    if user_id is None:
        return True
    return row.get("created_by") is None or row.get("created_by") == user_id


def can_studio(min_level: str | None = None) -> bool:
    """Whether the current user may use Agent Studio.

    READ-level access can view Studio content; write endpoints must pass
    ``min_level="write"`` or omit it for a method-aware check.
    """
    user_id = current_user_id()
    if user_id is None:
        return False
    from src.auth.modules import ACCESS_READ, ACCESS_WRITE, DEFAULT_READ_METHODS
    from src.utils.user_manager import UserManager

    if min_level is None:
        method = (request.method or "GET").upper()
        min_level = ACCESS_READ if method in DEFAULT_READ_METHODS else ACCESS_WRITE
    return bool(UserManager().has_module_access(user_id, "agent_studio", min_level=min_level))


def user_can_access_definition(row: dict[str, Any] | None, user_id: int | None = None) -> bool:
    """Whether ``user_id`` may invoke/run ``row``.

    Authorization mirrors the Agent Studio "Access" chips: an admin, the
    owner, or a user holding one of the roles explicitly granted on the
    definition. API keys inherit this because they resolve to their owning
    user (see ``api_auth_required``).
    """
    if not row:
        return False
    if user_id is None:
        return False
    from src.utils.user_manager import UserManager

    try:
        um = UserManager()
        if um.has_role(user_id, "admin"):
            return True
        created_by = row.get("created_by")
        if created_by is None or int(created_by) == int(user_id):
            return True
        role_ids = {int(a["role_id"]) for a in DefinitionStore.list_access(int(row["id"]))}
        if not role_ids:
            return False
        user = um.get_user_by_id(user_id)
        if not user:
            return False
        user_role_ids = {int(r.id) for r in (user.roles or [])}
        return bool(role_ids & user_role_ids)
    except Exception:
        return False


def agent_access_allowed(row: dict[str, Any] | None, user_id: int | None = None) -> bool:
    """Whether ``user_id`` may use/edit/publish this agent definition.

    The canonical resource rule (admin, owner, or a role granted on the agent).
    ``user_id`` defaults to the current request's identity; no identity denies.
    """
    if not row or row.get("id") is None:
        return False
    uid = user_id if user_id is not None else current_user_id()
    if uid is None:
        return False
    from src.auth import resource_access

    try:
        resource_id = int(row["id"])
    except (TypeError, ValueError):
        return False
    return resource_access.can_access("agent", resource_id, row.get("created_by"), uid)


def can_manage_agent(row: dict[str, Any] | None, user_id: int | None = None) -> bool:
    """Whether the user may *manage* (delete / change grants on) an agent.

    Only the owner or an administrator: a role grant confers use and
    publish-visibility, never the right to destroy another tenant's agent.
    """
    if not row:
        return False
    uid = user_id if user_id is not None else current_user_id()
    if uid is None:
        return False
    from src.auth import resource_access

    if resource_access.is_admin(uid):
        return True
    owner = row.get("created_by")
    try:
        return owner is not None and int(owner) == int(uid)
    except (TypeError, ValueError):
        return False


def require_studio(min_level: str | None = None):
    if not can_studio(min_level=min_level):
        g.audit_reason = "missing agent_studio module access"
        return jsonify({"error": "forbidden", "message": "Agent Studio requires agent_studio module access"}), 403
    return None


def definition_summary(row: dict[str, Any]) -> dict[str, Any]:
    """Config-derived fields the Studio reads at the top level of a definition.

    ``public_definition`` promotes these out of ``config``; the full/draft shape
    must carry the same keys or the Studio list loses a definition's
    description and pattern the moment drafts are included.
    """
    cfg = row.get("config") if isinstance(row.get("config"), dict) else {}
    return {
        "description": cfg.get("description") or "",
        "tags": cfg.get("tags") or [],
        "pattern": cfg.get("pattern"),
        "model": cfg.get("model"),
    }


def attach_user_names(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Populate ``created_by_name`` / ``updated_by_name`` for definition rows.

    One query for the whole list: the Studio shows who last touched a
    definition, and resolving per row would issue N queries. The keys are always
    present (``None`` when the author is unknown or the users table is absent,
    which is the case in standalone platform tests), so the API shape is stable.
    """
    ids = {int(r[k]) for r in rows for k in ("created_by", "updated_by") if r.get(k) is not None}
    names: dict[int, str] = {}
    if ids:
        try:
            conn = db.get_db_connection()
            placeholders = ",".join("?" for _ in ids)
            found = conn.execute(
                f"SELECT id, username FROM users WHERE id IN ({placeholders})", list(ids)
            ).fetchall()
            conn.close()
            names = {r["id"]: r["username"] for r in found}
        except sqlite3.Error:
            names = {}
    for row in rows:
        for key in ("created_by", "updated_by"):
            uid = row.get(key)
            row[f"{key}_name"] = names.get(int(uid)) if uid is not None else None
    return rows


def full_definition(row: dict[str, Any]) -> dict[str, Any]:
    out = dict(row)
    out["config"] = mask_value(out.get("config") or {})
    if "id" in out and out["id"] is not None:
        out["access"] = DefinitionStore.list_access(int(out["id"]))
    # The Studio hides manage/publish affordances on definitions the caller
    # cannot administer; the server is still the authority (every mutation
    # re-checks and answers 403).
    out["can_manage"] = can_manage_agent(row)
    out.update(definition_summary(row))
    if "created_by_name" not in out:
        attach_user_names([out])
    return out


def public_definition(row: dict[str, Any]) -> dict[str, Any]:
    cfg = dict(row.get("config") or {})
    studio_meta = cfg.get("studio")
    return {
        "id": row.get("id"),
        "public_id": row.get("public_id"),
        "slug": row.get("slug"),
        "name": row.get("name"),
        "kind": row.get("kind"),
        "version": row.get("version"),
        "description": cfg.get("description") or "",
        "tags": cfg.get("tags") or [],
        "pattern": cfg.get("pattern"),
        "model": cfg.get("model"),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
        "studio": studio_meta,
    }


def unique_slug(raw_name: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", (raw_name or "").strip().lower()).strip("-") or "agent"
    candidate = base
    counter = 1
    while DefinitionStore.get_by_slug(candidate) is not None:
        counter += 1
        candidate = f"{base}-{counter}"
    return candidate


def can_access_conversation(conv: dict | None, user_id: int | None = None) -> bool:
    """Conversations are private to their owner; administrators may audit.

    A module grant (for example ``observability``) never widens this: run logs
    are the shareable artifact, conversation *content* is not. No owner and no
    admin means deny.
    """
    if not conv:
        return False
    uid = user_id if user_id is not None else current_user_id()
    if uid is None:
        return False
    from src.auth import resource_access

    if resource_access.is_admin(uid):
        return True
    owner = conv.get("user_id")
    if owner is None:
        return False
    try:
        return int(owner) == int(uid)
    except (TypeError, ValueError):
        return False


def conversation_denied():
    g.audit_reason = "no access to this conversation"
    return jsonify({"error": "forbidden", "message": "You do not have access to this conversation"}), 403


def run_model_client(data: dict | None):
    """The run-level chat client for a payload, or a clean 400 when it is unusable.

    A caller can name a connection that was deleted or disabled since it was
    stored (a stale model pick in the browser, a saved run being resumed). That
    is a request problem, so it answers 400 with the manager's own explanation
    instead of a 500 page.
    """
    from src.agent_platform.runtime.model_select import client_from_payload
    from src.utils.llm_connection_manager import LLMConnectionError

    try:
        return client_from_payload(data), None
    except LLMConnectionError as exc:
        return None, (jsonify({"error": "model_unavailable", "message": str(exc)}), 400)


def reject_invalid_definition(row: dict[str, Any] | None):
    if not row:
        return None
    report = validate_definition(row)
    if isinstance(report, dict) and not report.get("ok", True):
        errors = report.get("errors") or []
        msgs = [e.get("message") if isinstance(e, dict) else str(e) for e in errors]
        return jsonify({"error": "validation_failed", "errors": errors, "messages": msgs}), 400
    if isinstance(report, list) and report:
        return jsonify({"error": "validation_failed", "errors": [{"message": m} for m in report], "messages": report}), 400
    if definition_uses_scripted_client(row) and not scripted_client_allowed():
        err = [{"code": "scripted_client_not_allowed", "message": "Scripted test client is only allowed in test/CI environments."}]
        return jsonify({"error": "validation_failed", "errors": err, "messages": ["Scripted test client is only allowed in test/CI environments."]}), 400
    return None


def validate_definition_report(definition: dict[str, Any]) -> dict[str, Any]:
    """The Studio's validate contract (docs/agent-studio-v2.md §3).

    ``ok``/``errors``/``warnings`` are the authoring report (each entry carries the
    code the editor keys on), and ``compile`` says whether the definition also
    builds a real graph. Both halves are always present so the editor can render
    "valid but does not compile" instead of guessing.
    """
    report = validate_definition(definition)
    errors = list(report.get("errors") or [])
    warnings = list(report.get("warnings") or [])
    ok = bool(report.get("ok", not errors))
    compile_info: dict[str, Any] = {"ok": False, "kind": None, "nodes": None}
    if ok:
        from src.agent_platform.runtime.compiler import compile_definition_sync

        try:
            compiled = compile_definition_sync(definition)
            nodes = getattr(compiled.runnable, "nodes", None)
            compile_info = {
                "ok": True,
                "kind": compiled.kind,
                "nodes": len(nodes) if nodes is not None else 1,
                "name": compiled.name,
            }
        except Exception as exc:  # the compile error is the report
            compile_info = {"ok": False, "kind": None, "nodes": None, "error": str(exc)}
            errors.append({"code": "compile_failed", "message": str(exc)})
            ok = False
    return {"ok": ok, "errors": errors, "warnings": warnings, "compile": compile_info}


def validate_and_compile(definition: dict[str, Any]) -> dict[str, Any]:
    """Backwards-compatible summary of :func:`validate_definition_report`."""
    report = validate_definition_report(definition)
    compile_info = report["compile"]
    return {
        "valid": report["ok"],
        "errors": [e.get("message") if isinstance(e, dict) else str(e) for e in report["errors"]],
        "warnings": [w.get("message") if isinstance(w, dict) else str(w) for w in report["warnings"]],
        "compiled_kind": compile_info.get("kind"),
        "agent_count": compile_info.get("nodes"),
    }
