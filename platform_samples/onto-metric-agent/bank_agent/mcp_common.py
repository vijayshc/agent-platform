"""Build a low-level MCP stdio server from a list of tool specs.

The official SDK ``Server`` is used (rather than FastMCP's decorator-per-function
style) because the tool schemas already live in the tool modules; this keeps one
source of truth for the MCP servers, the agent and the docs.

``validate_input=False`` is deliberate. The SDK would otherwise jsonschema-validate
arguments before the handler runs, and OpenAI-compatible gateways routinely send a
nested object or array as a JSON *string*; that would be rejected before the tool
layer can decode it. Decoding and argument errors are handled there instead, and
returned to the model as observations it can correct.
"""

from __future__ import annotations

import json
from typing import Any, Callable

import anyio
from mcp import types
from mcp.server import Server
from mcp.server.stdio import stdio_server


def build_server(name: str, specs: list[dict], dispatch: Callable[[str, dict | None], Any]) -> Server:
    server = Server(name)

    @server.list_tools()
    async def _list_tools() -> list[types.Tool]:
        return [
            types.Tool(
                name=spec["name"],
                description=spec["description"],
                inputSchema=spec["input_schema"],
            )
            for spec in specs
        ]

    @server.call_tool(validate_input=False)
    async def _call_tool(tool_name: str, arguments: dict[str, Any] | None) -> types.CallToolResult:
        result = dispatch(tool_name, arguments or {})
        payload = result if isinstance(result, dict) else {"result": result}
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=json.dumps(payload, default=str))],
            structuredContent=payload,
            isError=isinstance(result, dict) and "error" in result,
        )

    return server


def run_stdio(server: Server) -> None:
    """Serve MCP over stdin/stdout until the client closes the pipe."""

    async def _serve() -> None:
        async with stdio_server() as (read_stream, write_stream):
            await server.run(read_stream, write_stream, server.create_initialization_options())

    anyio.run(_serve)
