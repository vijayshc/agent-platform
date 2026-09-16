"""A real streamable-HTTP MCP server used by the live transport tests.

Not a mock: it is a genuine ``mcp`` SDK server, launched as a subprocess and
reached over the network by the app's LangGraph binding. It requires a bearer
token so the tests prove the binding's ``headers`` actually reach the server,
and it exposes deterministic tools so a live LLM run can be asserted on values
rather than on prose.

Run standalone:  python tests/mcp_http_probe_server.py <port> <token>
"""

from __future__ import annotations

import sys

from mcp.server.fastmcp import FastMCP

PROBE_TOKEN_ENV = "MCP_PROBE_TOKEN"


def build_app(token: str):
    mcp = FastMCP("http-probe", stateless_http=True)

    @mcp.tool()
    def http_probe_add(a: int, b: int) -> str:
        """Add two integers and return the exact sum."""
        return str(a + b)

    @mcp.tool()
    def http_probe_echo(text: str) -> str:
        """Echo text back verbatim."""
        return text

    app = mcp.streamable_http_app()

    async def with_auth(scope, receive, send):
        if scope["type"] != "http":
            await app(scope, receive, send)
            return
        headers = {k.lower(): v for k, v in scope.get("headers") or []}
        if headers.get(b"authorization", b"").decode() != f"Bearer {token}":
            await send({
                "type": "http.response.start",
                "status": 401,
                "headers": [(b"content-type", b"text/plain")],
            })
            await send({"type": "http.response.body", "body": b"unauthorized"})
            return
        await app(scope, receive, send)

    return with_auth


def main() -> None:
    port = int(sys.argv[1])
    token = sys.argv[2]
    import uvicorn

    uvicorn.run(build_app(token), host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()
