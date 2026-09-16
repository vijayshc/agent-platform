from __future__ import annotations

import contextlib
from typing import Any

from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.tools import load_mcp_tools

from src.agent_platform.catalog.mcp_discovery import user_can_use_server
from src.agent_platform.plugins.mcp.connection import build_connection, connection_config
from src.models.mcp_server import MCPServer


class MCPBindingHandle:
    """Holds a MultiServerMCPClient session for one catalog MCP server.

    ``user_id`` is the running user the binding was compiled for. It is
    re-checked when the session opens, so a handle can never connect a server
    its user cannot access — including a structurally-compiled handle that is
    connected by hand.
    """

    def __init__(
        self,
        name: str,
        connection: dict[str, Any],
        *,
        server: MCPServer,
        user_id: int | None = None,
        workspace_dir: str | None = None,
        allowed_tools: list[str] | None = None,
        approval_mode: Any = None,
    ) -> None:
        self.name = name
        self.connection = connection
        self.server = server
        self.user_id = user_id
        self.workspace_dir = workspace_dir
        self.allowed_tools = allowed_tools
        self.approval_mode = approval_mode
        self.functions: list[Any] = []
        self._stack: Any = None
        self._client: MultiServerMCPClient | None = None

    async def connect(self) -> None:
        # Fail closed at the session boundary: no identity / no access means no
        # connection, and the enforced builder is the only one used here.
        self.connection = build_connection(
            self.server, user_id=self.user_id, workspace_dir=self.workspace_dir
        )
        self._stack = contextlib.AsyncExitStack()
        self._client = MultiServerMCPClient(
            {self.name: self.connection},
            handle_tool_errors=True,
        )
        session = await self._stack.enter_async_context(self._client.session(self.name))
        tools = await load_mcp_tools(session, handle_tool_errors=True)
        allowed = set(self.allowed_tools) if self.allowed_tools else None
        approvals = _approval_names(self.approval_mode)
        filtered = []
        for tool in tools:
            if allowed and tool.name not in allowed:
                continue
            if tool.name in approvals:
                meta = dict(getattr(tool, "metadata", None) or {})
                meta["approval_mode"] = "always_require"
                tool.metadata = meta
            filtered.append(tool)
        self.functions = filtered

    async def close(self) -> None:
        if self._stack:
            try:
                await self._stack.aclose()
            except Exception:
                pass
            self._stack = None
            self._client = None


def _approval_names(approval_mode: Any) -> set[str]:
    if isinstance(approval_mode, dict):
        return {
            str(n)
            for n in (
                approval_mode.get("always_require_approval")
                or approval_mode.get("approval")
                or []
            )
        }
    if isinstance(approval_mode, (list, tuple, set)):
        return {str(n) for n in approval_mode}
    return set()


class MCPBindingPlugin:
    type_id = "mcp_binding"
    kind = "tool"
    label = "MCP Binding"
    icon = "plug"
    schema = {
        "type": "object",
        "properties": {
            "server": {"type": "string"},
            "server_id": {"type": "integer"},
            "tools": {"type": "array", "items": {"type": "string"}},
            "approval": {"type": "array", "items": {"type": "string"}},
        },
    }

    def compile(self, spec: dict[str, Any], ctx: Any) -> MCPBindingHandle:
        server = None
        server_id = spec.get("server_id")
        server_name = spec.get("server") or spec.get("server_name")
        if server_id is not None:
            server = MCPServer.get_by_id(int(server_id))
        elif server_name:
            server = MCPServer.get_by_name(str(server_name))
        if server is None:
            raise KeyError(f"Unknown MCP server: {server_id or server_name}")

        allowed_tools = spec.get("tools")
        approval_mode = spec.get("approval_mode")
        if approval_mode is None and "approval" in spec:
            approval_mode = {"always_require_approval": list(spec["approval"])}

        user_id = getattr(ctx, "user_id", None)
        workspace_dir = getattr(ctx, "workspace_dir", None)
        # A connecting compile is a run: gate it now, before any handle exists.
        # ``connect()`` re-checks, so the gate also holds for a structural
        # compile (``connect_mcp=False``) whose handle is connected later.
        if getattr(ctx, "connect_mcp", True) and not user_can_use_server(server.id, user_id):
            raise PermissionError(
                f"MCP server {server.name!r} is not accessible to this run's user"
            )

        # Pure build: validates the transport config at compile time without
        # authorizing a session; ``connect()`` rebuilds through the enforced path.
        connection = connection_config(server, workspace_dir=workspace_dir)
        return MCPBindingHandle(
            name=server.name,
            connection=connection,
            server=server,
            user_id=user_id,
            workspace_dir=workspace_dir,
            allowed_tools=allowed_tools,
            approval_mode=approval_mode,
        )
