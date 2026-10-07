"""Studio inspector and resource discovery service.

Extracted from catalog_routes.py to keep route handlers focused on HTTP endpoints
and request/response mapping.
"""

from __future__ import annotations

from typing import Any

from src.agent_platform import db
from src.agent_platform.api.api_helpers import can_manage_agent
from src.agent_platform.catalog.store import DefinitionStore


def fetch_studio_resources(user_id: int | None) -> dict[str, Any]:
    """Collate MCP servers, skills, tools, and model clients for Studio canvas."""
    from src.agent_platform.catalog.skill_packages import list_packages, visible_skill_names
    from src.agent_platform.plugins import register_builtin_plugins
    from src.agent_platform.plugins.registry import get_registry
    from src.agent_platform.plugins.tools.builtins import list_function_tools
    from src.agent_platform.runtime.model_select import studio_model_clients
    from src.models.mcp_server import MCPServer

    register_builtin_plugins()
    registry = get_registry()

    # Same create-if-missing contract as the LLM registry: this endpoint must
    # answer on a database whose MCP table has not been materialised yet.
    MCPServer.create_table()
    servers = [
        {"id": s.id, "name": s.name, "server_type": s.server_type}
        for s in MCPServer.get_visible(user_id)
    ]

    skills = list_packages()
    allowed_skills = visible_skill_names(user_id)
    if allowed_skills is not None:
        skills = [pkg for pkg in skills if str(pkg.get("name")) in allowed_skills]

    return {
        "mcp_servers": servers,
        "skills": skills,
        "function_tools": list_function_tools(),
        "model_clients": studio_model_clients(user_id),
        "plugins": registry.inspector_catalog(),
    }


def fetch_studio_roles() -> list[dict[str, Any]]:
    """List all roles and their user counts for Studio ACL management."""
    conn = db.get_db_connection()
    try:
        has_roles = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='roles'"
        ).fetchone()
        if not has_roles:
            return []
        rows = conn.execute(
            """
            SELECT r.id, r.name, r.description, COUNT(ur.user_id) AS user_count
            FROM roles r
            LEFT JOIN user_roles ur ON ur.role_id = r.id
            GROUP BY r.id
            ORDER BY r.name, r.id
            """
        ).fetchall()
    finally:
        conn.close()

    return [
        {
            "id": r["id"],
            "name": r["name"],
            "description": r["description"],
            "user_count": r["user_count"],
        }
        for r in rows
    ]


def fetch_mcp_server_tools(server_id: int, user_id: int | None) -> tuple[dict[str, Any] | None, int]:
    """Live list_tools for a single MCP server (studio inspector)."""
    from src.agent_platform.catalog.mcp_discovery import serialize_mcp_server
    from src.models.mcp_server import MCPServer, can_access_server

    if not can_access_server(server_id, user_id):
        return None, 404
    server = MCPServer.get_by_id(server_id)
    if not server:
        return None, 404
    return serialize_mcp_server(server, use_cache=False), 200


def resolve_agent_for_management(agent_id: str) -> tuple[dict[str, Any] | None, dict[str, str] | None, int]:
    """Resolve an agent for access management endpoints (owner/admin only)."""
    row = DefinitionStore.resolve(agent_id, published_only=False)
    if row is None:
        return None, {"error": "agent not found"}, 404
    if not can_manage_agent(row):
        return None, {
            "error": "forbidden",
            "message": "Only an administrator or the agent owner can manage access",
        }, 403
    return row, None, 200
