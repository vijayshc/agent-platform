"""Live MCP tool discovery from the MCPServer registry.

Discovery connects with the same ``langchain_mcp_adapters`` client and the same
transport configuration the run-time binding uses, so the inspector reports the
tool names and schemas an agent will actually receive.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.tools import load_mcp_tools

from src.agent_platform.plugins.mcp.connection import build_connection
from src.agent_platform.plugins.mcp.errors import describe_error

logger = logging.getLogger("text2sql.agent_platform")

_CACHE_TTL_S = 45.0
_CACHE_TIMEOUT_S = 12.0
_cache: dict[int, tuple[float, list[dict[str, Any]], str | None]] = {}

#: Discovery error returned when the caller's user may not use the server.
_NOT_ACCESSIBLE = "server not accessible"


def user_can_use_server(server_id: int, user_id: int | None) -> bool:
    """Whether ``user_id`` may connect to / list tools of ``server_id``.

    The canonical decision lives on the model helper; this is the discovery
    layer's spelling of it (and its frozen interface for the Studio agent).
    """
    from src.models.mcp_server import can_access_server

    try:
        return can_access_server(int(server_id), user_id)
    except (TypeError, ValueError):
        return False


def _current_user_id() -> int | None:
    """The authenticated user id when inside a request, else ``None`` (deny)."""
    try:
        from src.agent_platform.api.auth import current_user_id

        return current_user_id()
    except RuntimeError:
        return None


def resolve_user_id(user_id: int | None) -> int | None:
    """The explicit user id, or the request's identity when none was passed."""
    return user_id if user_id is not None else _current_user_id()



def _normalize_tool(tool: Any) -> dict[str, Any] | None:
    """A LangChain MCP tool as ``{name, description, parameters}``.

    ``parameters`` is the model-facing JSON Schema (``tool_call_schema``), the
    same object the agent's model is handed -- ``tool.args`` alone is only the
    bare properties map and loses required/title/description.
    """
    name = str(getattr(tool, "name", "") or "").strip()
    if not name:
        return None
    out: dict[str, Any] = {
        "name": name,
        "description": str(getattr(tool, "description", "") or "").strip(),
    }
    call_schema = getattr(tool, "tool_call_schema", None)
    if call_schema is not None and hasattr(call_schema, "model_json_schema"):
        out["parameters"] = call_schema.model_json_schema()
    elif isinstance(call_schema, dict) and call_schema:
        out["parameters"] = call_schema
    return out


async def _list_tools_live(
    server: Any, user_id: int | None
) -> tuple[list[dict[str, Any]], str | None]:
    try:
        connection = build_connection(server, user_id=user_id)
    except ValueError as exc:
        return [], str(exc)
    client = MultiServerMCPClient({server.name: connection}, handle_tool_errors=True)
    async with client.session(server.name) as session:
        raw = await load_mcp_tools(session, handle_tool_errors=True)

    tools: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in raw:
        row = _normalize_tool(item)
        if not row or row["name"] in seen:
            continue
        seen.add(row["name"])
        tools.append(row)
    return tools, None


def _run_async(coro):
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    result: dict[str, Any] = {}

    def _worker() -> None:
        result["value"] = asyncio.run(coro)

    import threading

    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()
    thread.join()
    if "value" not in result:
        raise RuntimeError("MCP discovery thread failed")
    return result["value"]


async def _list_with_timeout(
    server: Any, timeout_s: float, user_id: int | None
) -> tuple[list[dict[str, Any]], str | None]:
    try:
        return await asyncio.wait_for(
            _list_tools_live(server, user_id), timeout=timeout_s
        )
    except asyncio.TimeoutError:
        return [], f"timed out listing tools after {int(timeout_s)}s"
    except Exception as exc:
        logger.debug(
            "MCP list_tools failed for %s", getattr(server, "name", server), exc_info=True
        )
        return [], describe_error(exc)


def discover_mcp_tools(
    server: Any,
    *,
    user_id: int | None = None,
    use_cache: bool = True,
    timeout_s: float = _CACHE_TIMEOUT_S,
) -> tuple[list[dict[str, Any]], str | None]:
    """``(tool_rows, error)`` for one server; cached for ``_CACHE_TTL_S``.

    The caller's user must be able to use the server; no identity (or no
    access) fails closed before any connection is attempted.
    """
    uid = resolve_user_id(user_id)
    if not user_can_use_server(getattr(server, "id", None), uid):
        return [], _NOT_ACCESSIBLE
    sid = int(getattr(server, "id", 0) or 0)
    if use_cache and sid:
        hit = _cache.get(sid)
        if hit and (time.monotonic() - hit[0]) < _CACHE_TTL_S:
            return hit[1], hit[2]
    tools, error = _run_async(_list_with_timeout(server, timeout_s, uid))
    if sid:
        _cache[sid] = (time.monotonic(), tools, error)
    return tools, error


def serialize_mcp_server(
    server: Any,
    *,
    user_id: int | None = None,
    use_cache: bool = True,
    timeout_s: float = _CACHE_TIMEOUT_S,
) -> dict[str, Any]:
    details, error = discover_mcp_tools(
        server, user_id=user_id, use_cache=use_cache, timeout_s=timeout_s
    )
    return {
        "id": server.id,
        "name": server.name,
        "description": server.description,
        "server_type": server.server_type,
        "tools": [row["name"] for row in details],
        "tool_details": details,
        "tools_error": error,
    }


def list_mcp_servers(
    *,
    user_id: int | None = None,
    use_cache: bool = True,
    timeout_s: float = _CACHE_TIMEOUT_S,
) -> list[dict[str, Any]]:
    """Every server the user may see, with its live tool list, concurrently.

    The registry is access-filtered first, so a server the user cannot use is
    never connected to and never appears in the result. No identity -> ``[]``.
    """
    from src.models.mcp_server import MCPServer, visible_server_ids

    uid = resolve_user_id(user_id)
    if uid is None:
        return []
    servers = MCPServer.get_all()
    allowed = visible_server_ids(uid)
    if allowed is not None:
        servers = [server for server in servers if int(server.id) in allowed]
    if not servers:
        return []

    async def _one(server: Any) -> dict[str, Any]:
        sid = int(getattr(server, "id", 0) or 0)
        hit = _cache.get(sid) if use_cache and sid else None
        if hit and (time.monotonic() - hit[0]) < _CACHE_TTL_S:
            details, error = hit[1], hit[2]
        else:
            details, error = await _list_with_timeout(server, timeout_s, uid)
            if sid:
                _cache[sid] = (time.monotonic(), details, error)
        return {
            "id": server.id,
            "name": server.name,
            "description": server.description,
            "server_type": server.server_type,
            "tools": [row["name"] for row in details],
            "tools_error": error,
        }

    async def _all() -> list[dict[str, Any]]:
        return list(await asyncio.gather(*(_one(s) for s in servers)))

    return _run_async(_all())


def clear_mcp_tool_cache(server_id: int | None = None) -> None:
    """Drop cached discovery results (call after a server's config changes)."""
    if server_id is None:
        _cache.clear()
        return
    _cache.pop(int(server_id), None)
