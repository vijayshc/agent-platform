"""The smallest platform MCP server: it answers who is calling.

Every platform-served MCP server is gated by ``src.mcp_server_auth``, which
verifies the app access token minted for the user whose run the call belongs to
and refuses anything else with ``401``. This module is that contract in its
smallest form - the reference to copy when adding a server:

    mcp = FastMCP("Identity")                 # module-level instance

    @mcp.tool()
    def who_is_calling() -> str:
        caller = current_caller()             # verified, never client-supplied
        ...

Serve it (a catalog row with ``config.service`` autostarts it at boot)::

    python scripts/mcp_http_service.py \\
        --module platform_samples.mcp_servers.identity --port 8767
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from src.mcp_server_auth import current_caller

mcp = FastMCP("Identity")


@mcp.tool()
def who_is_calling() -> str:
    """Name the application user whose run triggered this call.

    Returns the username and user id the platform authenticated, for example
    "admin (user 1)". The value comes from the verified token, never from the
    caller's arguments.
    """
    caller = current_caller()
    return f"{caller.username} (user {caller.user_id})"


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
