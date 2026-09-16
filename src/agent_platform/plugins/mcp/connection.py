"""Transport configuration for catalog MCP servers.

One builder serves both the live tool inspector (``catalog.mcp_discovery``)
and the run-time LangGraph binding (``plugins.mcp.binding``), so the tools the
studio shows are exactly the tools an agent receives. Keeping a second
connection path is what let ``url``/``base_url`` drift apart before.

:func:`connection_config` is the pure transport builder; :func:`build_connection`
is the enforced entry point every session-opening caller uses. Splitting them
lets a structural compile (validation/inspection) surface a malformed config as
a compile error without opening — or authorizing — a session.
"""

from __future__ import annotations

from typing import Any

from src.agent_platform.paths import pythonpath_env
from src.models.mcp_server import MCPServer, MCPServerType, can_access_server
from src.models.secrets import unwrap_dict


def connection_config(
    server: MCPServer,
    *,
    workspace_dir: str | None = None,
) -> dict[str, Any]:
    """The ``MultiServerMCPClient`` connection dict for one catalog server.

    Pure transport building: no access decision and no session. Raises
    ``ValueError`` when the stored config cannot produce a connection.
    """
    config = dict(server.config or {})
    if server.server_type == MCPServerType.HTTP.value:
        url = str(config.get("url") or "").strip()
        if not url:
            raise ValueError("HTTP server has no url")
        return {
            "transport": "http",
            "url": url,
            "headers": unwrap_dict(dict(config.get("headers") or {})),
        }

    command = str(config.get("command") or "").strip()
    if not command:
        raise ValueError("stdio server has no command")
    env = dict(config.get("env") or {})
    env["PYTHONPATH"] = pythonpath_env()
    if workspace_dir:
        env["AGENT_WORKSPACE"] = str(workspace_dir)
    return {
        "transport": "stdio",
        "command": command,
        "args": list(config.get("args") or []),
        "env": unwrap_dict(env),
    }


def build_connection(
    server: MCPServer,
    *,
    user_id: int | None,
    workspace_dir: str | None = None,
) -> dict[str, Any]:
    """Access-checked connection dict — the only builder a session may open.

    The caller's user must be able to use the server: an unknown user (``None``)
    or an inaccessible server raises ``PermissionError``. This path never falls
    back to allowing the connection.
    """
    if not can_access_server(int(server.id or 0), user_id):
        raise PermissionError(
            f"MCP server {server.name!r} is not accessible to this user"
        )
    return connection_config(server, workspace_dir=workspace_dir)
