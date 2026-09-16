"""Studio MCP tools endpoint (live list_tools for the agent editor)."""

from __future__ import annotations

import pytest
from flask import Flask

from src.agent_platform.api.catalog_routes import catalog_bp
from src.agent_platform.catalog.store import DefinitionStore
from src.agent_platform.execution.api_keys import ApiKeyStore
from src.models.mcp_server import MCPServer, MCPServerType


@pytest.fixture()
def studio_client(temp_db):
    DefinitionStore.ensure_tables()
    ApiKeyStore.ensure_tables()
    MCPServer.create_table()
    key = ApiKeyStore.create(user_id=1, name="studio", scopes=["agents:read", "agents:write"])

    app = Flask(__name__)
    app.secret_key = "test"
    app.config["TESTING"] = True
    app.register_blueprint(catalog_bp, url_prefix="/api/v1")

    return app.test_client(), {"X-API-Key": key["key"]}


def test_studio_mcp_tools_unknown_server_404(studio_client):
    http, headers = studio_client
    res = http.get("/api/v1/studio/mcp-servers/999999/tools", headers=headers)
    assert res.status_code == 404
    assert res.get_json()["error"] == "server not found"


def test_studio_mcp_tools_returns_serialized_server(studio_client):
    http, headers = studio_client
    server = MCPServer(
        name="StudioToolsProbe",
        description="KB tools",
        server_type=MCPServerType.STDIO.value,
        config={},
    )
    server.save()

    res = http.get(f"/api/v1/studio/mcp-servers/{server.id}/tools", headers=headers)
    assert res.status_code == 200
    body = res.get_json()
    assert body["id"] == server.id
    assert body["name"] == "StudioToolsProbe"
    assert body["description"] == "KB tools"
    assert body["server_type"] == MCPServerType.STDIO.value
    assert body["tools"] == []
    assert body["tool_details"] == []
    # Shipped serializer: stdio with no command cannot open a session.
    assert body["tools_error"] == "stdio server has no command"
