"""Studio capability catalog and static compile plan.

``GET /studio/catalog`` is what the editor renders its palette, pattern gallery
and inspector from; ``POST /studio/plan`` answers "what will this definition
actually run?" without compiling or calling a model.
"""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from src.agent_platform.api.api_helpers import require_studio
from src.agent_platform.api.auth import api_auth_required, current_user_id
from src.agent_platform.catalog.capabilities import studio_catalog
from src.agent_platform.catalog.plan import compile_plan

capability_bp = Blueprint("capability_api", __name__)


def _visible_mcp_servers(servers: list[dict]) -> list[dict]:
    """The caller's subset of the studio resource list's MCP servers.

    ``_studio_resources`` reads the unfiltered registry; this trims it to the
    servers the running user may see (no identity -> none).
    """
    from src.models.mcp_server import visible_server_ids

    allowed = visible_server_ids(current_user_id())
    if allowed is None:
        return list(servers)
    return [server for server in servers if server.get("id") in allowed]


@capability_bp.get("/studio/catalog")
@api_auth_required("agents:read")
def get_studio_catalog():
    denied = require_studio()
    if denied:
        return denied
    from src.agent_platform.api.catalog_routes import _studio_resources

    catalog = studio_catalog()
    resources = _studio_resources()
    resources["mcp_servers"] = _visible_mcp_servers(resources.get("mcp_servers") or [])
    catalog["resources"] = resources
    return jsonify(catalog)


@capability_bp.post("/studio/plan")
@api_auth_required("agents:read")
def post_studio_plan():
    denied = require_studio()
    if denied:
        return denied
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict) or not (data.get("config") or data.get("kind")):
        return jsonify({"error": "a definition body is required"}), 400
    return jsonify(compile_plan(data))
