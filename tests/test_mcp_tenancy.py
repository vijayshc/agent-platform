"""MCP server tenancy: real routes, real model, real resource-grant store.

Everything here runs the shipped code path against a real (temp) SQLite file
via the ``temp_db`` fixture; only the connection target is swapped, exactly as
``tests/conftest.py`` already does. No behaviour is faked.

The one seam patched is the *module* RBAC gate (``module_required`` ->
``UserManager.has_any_module_access``). That layer is orthogonal to resource
tenancy, needs the SQLAlchemy engine (which ``temp_db`` does not swap), and is
covered by the foundation tests; the resource-level decision under test here is
fully real.
"""

from __future__ import annotations

import asyncio
import sqlite3

import pytest
from flask import Flask

from src.auth import resource_access
from src.models.mcp_server import (
    MCPServer,
    MCPServerType,
    can_access_server,
    visible_server_ids,
)
from src.routes.mcp_admin_routes import mcp_admin_bp

ADMIN_UID = 10
OWNER_UID = 11
GRANTED_UID = 12
OUTSIDER_UID = 13

ADMIN_ROLE = 1
MEMBER_ROLE = 2
OTHER_ROLE = 3

_ROLE_SCHEMA = """
CREATE TABLE IF NOT EXISTS roles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,
    description TEXT
);
CREATE TABLE IF NOT EXISTS user_roles (
    user_id INTEGER,
    role_id INTEGER
);
"""


def _connect(db_path):
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


@pytest.fixture()
def tenancy_db(temp_db):
    """Temp DB carrying the shipped mcp_servers schema plus roles/user_roles."""
    resource_access._reset_schema_guard()
    MCPServer.create_table()
    conn = _connect(temp_db)
    try:
        conn.executescript(_ROLE_SCHEMA)
        conn.execute("INSERT INTO roles (id, name) VALUES (?, 'admin')", (ADMIN_ROLE,))
        conn.execute("INSERT INTO roles (id, name) VALUES (?, 'member')", (MEMBER_ROLE,))
        conn.execute("INSERT INTO roles (id, name) VALUES (?, 'other')", (OTHER_ROLE,))
        for uid, role in (
            (ADMIN_UID, ADMIN_ROLE),
            (OWNER_UID, MEMBER_ROLE),
            (GRANTED_UID, MEMBER_ROLE),
            (OUTSIDER_UID, OTHER_ROLE),
        ):
            conn.execute("INSERT INTO user_roles (user_id, role_id) VALUES (?, ?)", (uid, role))
        conn.commit()
    finally:
        conn.close()
    yield temp_db
    resource_access._reset_schema_guard()


@pytest.fixture()
def client(tenancy_db, monkeypatch):
    """Real mcp_admin blueprint; module gate stubbed (see module docstring)."""
    from src.utils.user_manager import UserManager

    monkeypatch.setattr(
        UserManager, "has_any_module_access", lambda self, *a, **k: True
    )
    app = Flask(__name__)
    app.secret_key = "test"
    app.config["TESTING"] = True
    app.register_blueprint(mcp_admin_bp)
    return app.test_client()


def _as(client, uid):
    with client.session_transaction() as session:
        session["user_id"] = uid


def _make_server(name, created_by, config=None):
    server = MCPServer(
        name=name,
        description="tenancy probe",
        server_type=MCPServerType.STDIO.value,
        config=config if config is not None else {},
        created_by=created_by,
    )
    server.save()
    return server


def _names(response):
    return {row["name"] for row in response.get_json()["servers"]}


# --------------------------------------------------------------------------
# visibility: owner / admin / unrelated / granted
# --------------------------------------------------------------------------

def test_owner_sees_and_uses_own_server(client):
    own = _make_server("Owned", OWNER_UID)
    _as(client, OWNER_UID)

    listing = client.get("/api/admin/mcp-servers")
    assert listing.status_code == 200
    assert _names(listing) == {"Owned"}

    detail = client.get(f"/api/admin/mcp-servers/{own.id}")
    assert detail.status_code == 200
    assert detail.get_json()["server"]["can_manage"] is True
    assert detail.get_json()["server"]["created_by"] == OWNER_UID

    # "Use" reaches the real connection builder; config {} then fails on the
    # missing command, proving access was granted but the transport was not.
    tools = client.get(f"/api/admin/mcp-servers/{own.id}/tools")
    assert tools.status_code == 200
    assert tools.get_json()["success"] is False
    assert "command" in tools.get_json()["error"]

    assert can_access_server(own.id, OWNER_UID) is True
    assert visible_server_ids(OWNER_UID) == {own.id}


def test_admin_sees_all(client):
    own = _make_server("Owned", OWNER_UID)
    legacy = _make_server("Legacy", None)
    _as(client, ADMIN_UID)

    listing = client.get("/api/admin/mcp-servers")
    assert listing.status_code == 200
    assert _names(listing) == {"Owned", "Legacy"}

    assert visible_server_ids(ADMIN_UID) is None
    for server in (own, legacy):
        assert can_access_server(server.id, ADMIN_UID) is True


def test_unrelated_user_is_isolated(client):
    own = _make_server("Owned", OWNER_UID)
    _as(client, OUTSIDER_UID)

    listing = client.get("/api/admin/mcp-servers")
    assert listing.status_code == 200
    assert listing.get_json()["servers"] == []

    assert client.get(f"/api/admin/mcp-servers/{own.id}").status_code == 404
    assert client.get(f"/api/admin/mcp-servers/{own.id}/tools").status_code == 404
    assert client.put(
        f"/api/admin/mcp-servers/{own.id}", json={"description": "hacked"}
    ).status_code == 404
    assert client.delete(f"/api/admin/mcp-servers/{own.id}").status_code == 404

    # The denied DELETE left the row untouched.
    assert MCPServer.get_by_id(own.id) is not None
    assert can_access_server(own.id, OUTSIDER_UID) is False
    assert own.id not in visible_server_ids(OUTSIDER_UID)


def test_role_grant_confers_use_not_control(client):
    own = _make_server("Shared", OWNER_UID)
    resource_access.set_access("mcp_server", own.id, [MEMBER_ROLE], granted_by=OWNER_UID)
    _as(client, GRANTED_UID)

    listing = client.get("/api/admin/mcp-servers")
    assert listing.status_code == 200
    assert _names(listing) == {"Shared"}

    detail = client.get(f"/api/admin/mcp-servers/{own.id}")
    assert detail.status_code == 200
    assert detail.get_json()["server"]["can_manage"] is False

    # Use is allowed (real connection attempt reached the config error) ...
    tools = client.get(f"/api/admin/mcp-servers/{own.id}/tools")
    assert tools.status_code == 200
    assert "command" in tools.get_json()["error"]

    # ... destructive control is not.
    assert client.put(
        f"/api/admin/mcp-servers/{own.id}", json={"description": "not mine"}
    ).status_code == 403
    assert client.delete(f"/api/admin/mcp-servers/{own.id}").status_code == 403
    assert MCPServer.get_by_id(own.id) is not None
    assert MCPServer.get_by_id(own.id).description == "tenancy probe"


def test_legacy_owner_null_rows_stay_visible(client):
    legacy = _make_server("Legacy", None)
    _as(client, OUTSIDER_UID)

    # Grandfathered: visible to every authenticated user ...
    listing = client.get("/api/admin/mcp-servers")
    assert _names(listing) == {"Legacy"}
    assert client.get(f"/api/admin/mcp-servers/{legacy.id}").status_code == 200
    assert can_access_server(legacy.id, OUTSIDER_UID) is True

    # ... but only an administrator may manage an owner-less row.
    assert client.put(
        f"/api/admin/mcp-servers/{legacy.id}", json={"description": "nope"}
    ).status_code == 403
    assert client.delete(f"/api/admin/mcp-servers/{legacy.id}").status_code == 403

    _as(client, ADMIN_UID)
    assert client.put(
        f"/api/admin/mcp-servers/{legacy.id}", json={"description": "curated"}
    ).status_code == 200
    assert MCPServer.get_by_id(legacy.id).description == "curated"


# --------------------------------------------------------------------------
# create stamps owner; get_all stays unfiltered
# --------------------------------------------------------------------------

def test_create_stamps_authenticated_owner(client):
    _as(client, OWNER_UID)
    created = client.post(
        "/api/admin/mcp-servers",
        json={"name": "Created", "server_type": "stdio", "config": {}},
    )
    assert created.status_code == 200
    row = created.get_json()["server"]
    assert row["created_by"] == OWNER_UID
    assert row["can_manage"] is True
    assert MCPServer.get_by_id(row["id"]).created_by == OWNER_UID

    _as(client, ADMIN_UID)
    admin_created = client.post(
        "/api/admin/mcp-servers",
        json={"name": "AdminMade", "server_type": "stdio", "config": {}},
    )
    assert admin_created.get_json()["server"]["created_by"] == ADMIN_UID


def test_get_all_stays_unfiltered(client, tenancy_db):
    _make_server("A", OWNER_UID)
    _make_server("B", OUTSIDER_UID)
    assert {row.name for row in MCPServer.get_all()} == {"A", "B"}
    # get_visible denies without an identity even though get_all sees rows.
    assert MCPServer.get_visible(None) == []


# --------------------------------------------------------------------------
# owner-column migration
# --------------------------------------------------------------------------

def test_migration_adds_owner_column_to_legacy_table(temp_db):
    resource_access._reset_schema_guard()
    conn = _connect(temp_db)
    try:
        conn.execute(
            """
            CREATE TABLE mcp_servers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                description TEXT,
                server_type TEXT NOT NULL,
                config TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            "INSERT INTO mcp_servers (name, server_type, config) VALUES ('Old', 'stdio', '{}')"
        )
        conn.commit()
    finally:
        conn.close()

    MCPServer.create_table()
    MCPServer.create_table()  # idempotent

    row = MCPServer.get_by_name("Old")
    assert row is not None
    assert row.created_by is None
    assert can_access_server(row.id, OUTSIDER_UID) is True  # grandfathered


# --------------------------------------------------------------------------
# frozen helper + registry
# --------------------------------------------------------------------------

def test_frozen_helpers_fail_closed(client, tenancy_db):
    own = _make_server("Owned", OWNER_UID)
    assert can_access_server(own.id, None) is False
    assert can_access_server(999_999, OWNER_UID) is False
    assert visible_server_ids(None) == set()
    assert MCPServer.get_visible(None) == []
    assert resource_access.is_registered("mcp_server") is True
    assert resource_access.owner_of("mcp_server", own.id) == OWNER_UID
    assert resource_access.resource_exists("mcp_server", own.id) is True
    assert resource_access.resource_exists("mcp_server", 999_999) is False


# --------------------------------------------------------------------------
# runtime: connection build / discovery fail closed
# --------------------------------------------------------------------------

def _binding_ctx(user_id):
    from src.agent_platform.runtime.compiler import CompileContext

    return CompileContext(user_id=user_id, workspace_dir=None)


def test_runtime_binding_fails_closed_for_non_accessible_server(client, tenancy_db):
    from src.agent_platform.plugins.mcp.binding import MCPBindingPlugin

    own = _make_server("Owned", OWNER_UID, config={"command": "echo"})
    plugin = MCPBindingPlugin()

    with pytest.raises(PermissionError):
        plugin.compile({"server_id": own.id}, _binding_ctx(OUTSIDER_UID))
    with pytest.raises(PermissionError):
        plugin.compile({"server_id": own.id}, _binding_ctx(None))
    with pytest.raises(PermissionError):
        plugin.compile({"server": "Owned"}, _binding_ctx(OUTSIDER_UID))

    handle = plugin.compile({"server_id": own.id}, _binding_ctx(OWNER_UID))
    assert handle.connection["transport"] == "stdio"
    assert handle.connection["command"] == "echo"


def test_runtime_binding_allows_granted_user(client, tenancy_db):
    from src.agent_platform.plugins.mcp.binding import MCPBindingPlugin

    own = _make_server("Shared", OWNER_UID, config={"command": "echo"})
    resource_access.set_access("mcp_server", own.id, [MEMBER_ROLE], granted_by=OWNER_UID)
    handle = MCPBindingPlugin().compile({"server_id": own.id}, _binding_ctx(GRANTED_UID))
    assert handle.connection["command"] == "echo"


def test_structural_compile_never_bypasses_the_session_gate(client, tenancy_db):
    """A non-connecting compile builds a handle; connecting it re-checks access."""
    from src.agent_platform.plugins.mcp.binding import MCPBindingPlugin
    from src.agent_platform.plugins.mcp.connection import build_connection

    own = _make_server("Owned", OWNER_UID, config={"command": "echo"})
    plugin = MCPBindingPlugin()

    ctx = _binding_ctx(OWNER_UID)
    ctx.connect_mcp = False
    handle = plugin.compile({"server_id": own.id}, ctx)
    assert handle.connection["command"] == "echo"  # built for inspection only

    # Connecting that handle still denies (raises before any session opens).
    for uid in (None, OUTSIDER_UID):
        no_access = _binding_ctx(uid)
        no_access.connect_mcp = False
        denied = plugin.compile({"server_id": own.id}, no_access)
        with pytest.raises(PermissionError):
            asyncio.run(denied.connect())

    # The enforced builder refuses the same users.
    with pytest.raises(PermissionError):
        build_connection(own, user_id=None)
    with pytest.raises(PermissionError):
        build_connection(own, user_id=OUTSIDER_UID)
    assert build_connection(own, user_id=OWNER_UID)["command"] == "echo"


def test_discovery_fails_closed(client, tenancy_db):
    from src.agent_platform.catalog.mcp_discovery import (
        discover_mcp_tools,
        list_mcp_servers,
        serialize_mcp_server,
        user_can_use_server,
    )

    own = _make_server("Owned", OWNER_UID)

    assert user_can_use_server(own.id, OUTSIDER_UID) is False
    tools, error = discover_mcp_tools(own, user_id=OUTSIDER_UID, use_cache=False)
    assert tools == [] and error == "server not accessible"
    tools, error = discover_mcp_tools(own, user_id=None, use_cache=False)
    assert tools == [] and error == "server not accessible"

    # An accessible server reaches the real transport builder (config {} has no
    # command) rather than the access denial.
    result = serialize_mcp_server(own, user_id=OWNER_UID, use_cache=False)
    assert result["tools_error"] == "stdio server has no command"

    assert list_mcp_servers(user_id=OUTSIDER_UID) == []
    assert list_mcp_servers(user_id=None) == []
    assert {row["name"] for row in list_mcp_servers(user_id=ADMIN_UID)} == {"Owned"}


def test_generic_access_api_owner_and_admin_only(tenancy_db, monkeypatch):
    """The shared /api/v1/access/mcp_server/<id> endpoints use my registration."""
    from src.agent_platform.api.access_routes import access_bp
    from src.utils.user_manager import UserManager

    # The response's role catalog is not under test; keep it off the shared DB.
    monkeypatch.setattr(UserManager, "get_all_roles", lambda self: [])

    app = Flask(__name__)
    app.secret_key = "test"
    app.config["TESTING"] = True
    app.register_blueprint(access_bp, url_prefix="/api/v1")
    http = app.test_client()

    own = _make_server("Owned", OWNER_UID)

    _as(http, OWNER_UID)
    assert http.get(f"/api/v1/access/mcp_server/{own.id}").status_code == 200
    granted = http.put(
        f"/api/v1/access/mcp_server/{own.id}", json={"role_ids": [MEMBER_ROLE]}
    )
    assert granted.status_code == 200
    assert granted.get_json()["access"] == [{"role_id": MEMBER_ROLE, "role_name": "member"}]

    _as(http, GRANTED_UID)
    assert http.get(f"/api/v1/access/mcp_server/{own.id}").status_code == 403
    assert http.put(
        f"/api/v1/access/mcp_server/{own.id}", json={"role_ids": []}
    ).status_code == 403

    _as(http, ADMIN_UID)
    assert http.get(f"/api/v1/access/mcp_server/{own.id}").status_code == 200
    assert http.get("/api/v1/access/mcp_server/999999").status_code == 404
    assert http.get("/api/v1/access/not_a_type/1").status_code == 404


def test_studio_catalog_filters_mcp_servers(client, tenancy_db):
    """The studio resource list exposes only the caller's servers."""
    from flask import session

    from src.agent_platform.api.capability_routes import _visible_mcp_servers

    own = _make_server("Owned", OWNER_UID)
    legacy = _make_server("Legacy", None)
    rows = [{"id": own.id, "name": "Owned"}, {"id": legacy.id, "name": "Legacy"}]

    def visible_for(uid):
        with client.application.test_request_context("/api/v1/studio/catalog"):
            session["user_id"] = uid
            return {row["name"] for row in _visible_mcp_servers(rows)}

    # Owner-less legacy rows are grandfathered into every authenticated view.
    assert visible_for(OWNER_UID) == {"Owned", "Legacy"}
    assert visible_for(OUTSIDER_UID) == {"Legacy"}
    assert visible_for(ADMIN_UID) == {"Owned", "Legacy"}
    with client.application.test_request_context("/api/v1/studio/catalog"):
        assert _visible_mcp_servers(rows) == []
