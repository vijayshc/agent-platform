"""Generic per-resource role-grant API (one endpoint pair for every asset type).

Module developers must not add their own access endpoints: granting a role on an
agent, a skill, an MCP server, a knowledge document, an LLM connection or a
hosted app all go through::

    GET  /api/v1/access/<resource_type>/<resource_id>
    PUT  /api/v1/access/<resource_type>/<resource_id>   {"role_ids": [1, 2]}

Both require any authenticated identity.  Reading or replacing a resource's
grants is restricted to an administrator or the resource's owner.

404 contract (documented, since an owner resolver cannot always distinguish a
missing row from an owner-less legacy row):

* an unregistered / unknown ``resource_type``            -> 404
* ``resource_exists`` is false                           -> 404
* otherwise the caller must be admin or the owner        -> 403 otherwise

``resource_exists`` uses the optional existence resolver a store registers with
``register_resource``; without one, a non-null owner proves existence and an
owner-less resource is reported absent (visible to all, but not manageable here
until its store registers an existence resolver).
"""

from __future__ import annotations

from flask import Blueprint, g, jsonify, request

from src.agent_platform.api.auth import api_auth_required, current_user_id
from src.auth import resource_access

access_bp = Blueprint("resource_access_api", __name__)


def _roles_payload() -> list[dict]:
    """Every role available to grant, as ``[{"id", "name"}]``."""
    from src.utils.user_manager import UserManager

    try:
        return [
            {"id": int(role.id), "name": str(role.name)}
            for role in UserManager().get_all_roles()
        ]
    except Exception:
        return []


def _authorize(resource_type: str, resource_id: int):
    """Return ``(normalized_type, None)`` or ``(None, error_response)``."""
    rtype = (resource_type or "").strip().lower()
    if rtype not in resource_access.RESOURCE_TYPES:
        return None, (
            jsonify({
                "error": "unknown_resource_type",
                "message": f"'{resource_type}' is not a tenanted resource type",
            }),
            404,
        )
    if not resource_access.is_registered(rtype):
        return None, (
            jsonify({
                "error": "unknown_resource_type",
                "message": f"No store has registered resource type '{rtype}' yet",
            }),
            404,
        )
    owner = resource_access.owner_of(rtype, resource_id)
    if not resource_access.resource_exists(rtype, resource_id):
        return None, (
            jsonify({"error": "not_found", "message": f"{rtype} {resource_id} was not found"}),
            404,
        )

    user_id = current_user_id()
    is_owner = owner is not None and user_id is not None and int(owner) == int(user_id)
    if not (resource_access.is_admin(user_id) or is_owner):
        g.audit_reason = f"not an administrator or the owner of {rtype} {resource_id}"
        return None, (
            jsonify({
                "error": "forbidden",
                "message": "Only an administrator or the resource owner can manage access",
            }),
            403,
        )
    return rtype, None


@access_bp.get("/access/<resource_type>/<int:resource_id>")
@api_auth_required()
def get_resource_access(resource_type: str, resource_id: int):
    rtype, denied = _authorize(resource_type, resource_id)
    if denied is not None:
        return denied
    return jsonify({
        "access": resource_access.list_access(rtype, resource_id),
        "roles": _roles_payload(),
    })


@access_bp.put("/access/<resource_type>/<int:resource_id>")
@api_auth_required()
def put_resource_access(resource_type: str, resource_id: int):
    rtype, denied = _authorize(resource_type, resource_id)
    if denied is not None:
        return denied

    data = request.get_json(silent=True) or {}
    role_ids = data.get("role_ids")
    if not isinstance(role_ids, list):
        return jsonify({
            "error": "invalid_role_ids",
            "message": "role_ids must be a list of role ids",
        }), 400

    resource_access.set_access(
        rtype, resource_id, role_ids, granted_by=current_user_id()
    )
    return jsonify({
        "access": resource_access.list_access(rtype, resource_id),
        "roles": _roles_payload(),
    })
