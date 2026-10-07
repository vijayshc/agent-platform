"""Transport configuration for catalog MCP servers.

One builder serves both the live tool inspector (``catalog.mcp_discovery``)
and the run-time LangGraph binding (``plugins.mcp.binding``), so the tools the
studio shows are exactly the tools an agent receives. Keeping a second
connection path is what let ``url``/``base_url`` drift apart before.

There are two kinds of HTTP row, and they differ in exactly one way - who
authenticates the call:

* a **platform-served** row (one with a ``config.service`` block) is a module
  this host serves with ``scripts/mcp_http_service.py``. Its calls carry the
  app's own access token, minted here for the user the run belongs to, so the
  server's gate verifies that user and a tool can name them
  (``src.mcp_server_auth``). A stored ``Authorization`` header is ignored: a
  static bearer proves nothing about the caller.
* an **external** row is somebody else's endpoint, reached with the headers an
  administrator stored - it answers to its own credential, not to ours.

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


def platform_served(server: MCPServer) -> bool:
    """Whether this host runs the server (a ``config.service`` block names it)."""
    return bool((server.config or {}).get("service"))


def caller_token(user_id: int | None, server_name: str) -> str:
    """An access token naming the user a platform-served call belongs to.

    The MCP server verifies this token with the app's own key and reads the user
    from it, so the identity a tool sees is the identity the app authenticated -
    it cannot be set by the agent, the model, or the MCP client.
    """
    if user_id is None:
        raise PermissionError(
            f"MCP server {server_name!r} is served by this platform, so a run needs "
            "the calling user's identity to authenticate its calls"
        )
    from src.auth.access_tokens import issue_access_token
    from src.utils.user_manager import UserManager

    username = UserManager().get_username_by_id(int(user_id))
    if not username:
        raise ValueError(
            f"user {int(user_id)} does not exist, so no token can name the caller"
        )
    return issue_access_token(int(user_id), username=str(username))


def connection_config(
    server: MCPServer,
    *,
    workspace_dir: str | None = None,
    user_id: int | None = None,
) -> dict[str, Any]:
    """The ``MultiServerMCPClient`` connection dict for one catalog server.

    Pure transport building: no access decision and no session. Raises
    ``ValueError`` when the stored config cannot produce a connection, and
    ``PermissionError`` when a platform-served row is built without a user.
    """
    config = dict(server.config or {})
    if server.server_type == MCPServerType.HTTP.value:
        url = str(config.get("url") or "").strip()
        if not url:
            raise ValueError("HTTP server has no url")
        headers = unwrap_dict(dict(config.get("headers") or {}))
        if platform_served(server):
            for key in [name for name in headers if name.lower() == "authorization"]:
                headers.pop(key)
            headers["Authorization"] = f"Bearer {caller_token(user_id, server.name)}"
        return {
            "transport": "http",
            "url": url,
            "headers": headers,
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
    return connection_config(server, workspace_dir=workspace_dir, user_id=user_id)
