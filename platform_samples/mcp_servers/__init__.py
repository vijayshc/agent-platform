"""Bundled MCP tool servers (stdio modules served over HTTP on request).

Each module exposes a module-level ``mcp = FastMCP(...)`` instance, so it can
be launched as a stdio subprocess by an MCP client, or served over streamable
HTTP by ``scripts/mcp_http_service.py``.
"""
