"""Resource-level tenancy store: real SQL against a real (temp) SQLite DB.

These are not mockup tests: the store's real code path runs against a real
SQLite file created by the ``temp_db`` fixture; only the connection target is
swapped (exactly the pattern ``tests/conftest.py`` already uses).
"""

from __future__ import annotations

import sqlite3

import pytest

from src.auth import resource_access

SCHEMA = """
CREATE TABLE IF NOT EXISTS roles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,
    description TEXT
);
CREATE TABLE IF NOT EXISTS user_roles (
    user_id INTEGER,
    role_id INTEGER
);
CREATE TABLE IF NOT EXISTS agent_definitions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slug TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    kind TEXT NOT NULL,
    config TEXT NOT NULL,
    published INTEGER NOT NULL DEFAULT 0,
    version INTEGER NOT NULL DEFAULT 1,
    created_by INTEGER,
    updated_by INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS agent_definition_role_access (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id INTEGER NOT NULL,
    role_id INTEGER NOT NULL,
    granted_by INTEGER,
    granted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(agent_id, role_id)
);
CREATE TABLE IF NOT EXISTS hosted_apps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slug TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    created_by INTEGER
);
CREATE TABLE IF NOT EXISTS hosted_app_role_access (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    app_id INTEGER NOT NULL,
    role_id INTEGER NOT NULL,
    granted_by INTEGER,
    granted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(app_id, role_id)
);
"""


def _connect(db_path):
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def _exec(db_path, sql: str, params=()):
    conn = _connect(db_path)
    try:
        conn.execute(sql, params)
        conn.commit()
    finally:
        conn.close()


@pytest.fixture()
def access_db(temp_db):
    """Temp DB carrying the app's role/user schema plus both legacy grant tables."""
    resource_access._reset_schema_guard()
    conn = _connect(temp_db)
    try:
        conn.executescript(SCHEMA)
        conn.execute("INSERT INTO roles (id, name) VALUES (1, 'admin')")
        conn.execute("INSERT INTO roles (id, name) VALUES (2, 'user')")
        conn.execute("INSERT INTO roles (id, name) VALUES (3, 'analyst')")
        conn.execute("INSERT INTO roles (id, name) VALUES (4, 'other')")
        conn.commit()
    finally:
        conn.close()
    yield temp_db
    resource_access._reset_schema_guard()


def _add_user_role(db_path, user_id, role_id):
    _exec(db_path, "INSERT INTO user_roles (user_id, role_id) VALUES (?, ?)", (user_id, role_id))


# --------------------------------------------------------------------------
# decision semantics
# --------------------------------------------------------------------------

def test_admin_allow(access_db):
    _add_user_role(access_db, 10, 1)
    assert resource_access.is_admin(10)
    assert resource_access.can_access("agent", 1, owner_id=999, user_id=10)
    assert resource_access.user_role_names(10) == {"admin"}


def test_owner_allow(access_db):
    assert resource_access.can_access("agent", 1, owner_id=11, user_id=11)


def test_owner_null_is_grandfathered(access_db):
    assert resource_access.can_access("agent", 1, owner_id=None, user_id=11)


def test_granted_role_allows(access_db):
    _add_user_role(access_db, 12, 3)
    resource_access.set_access("agent", 5, [3], granted_by=1)
    assert resource_access.can_access("agent", 5, owner_id=999, user_id=12)


def test_unrelated_role_denied(access_db):
    _add_user_role(access_db, 13, 4)
    resource_access.set_access("agent", 5, [3], granted_by=1)
    assert not resource_access.can_access("agent", 5, owner_id=999, user_id=13)


def test_unauthenticated_denied(access_db):
    assert not resource_access.can_access("agent", 1, owner_id=None, user_id=None)
    assert resource_access.user_role_ids(None) == set()
    assert resource_access.user_role_names(None) == set()
    assert not resource_access.is_admin(None)
    assert resource_access.filter_visible("agent", [{"id": 1, "created_by": None}], None) == []


# --------------------------------------------------------------------------
# grants: set / list / replace / validation
# --------------------------------------------------------------------------

def test_set_access_replaces_full_set(access_db):
    # list_access orders by role name: "analyst" before "user".
    assert resource_access.set_access("agent", 7, [2, 3], granted_by=1) == [
        {"role_id": 3, "role_name": "analyst"},
        {"role_id": 2, "role_name": "user"},
    ]
    assert resource_access.set_access("agent", 7, [3]) == [
        {"role_id": 3, "role_name": "analyst"}
    ]
    assert resource_access.set_access("agent", 7, []) == []
    assert resource_access.list_access("agent", 7) == []


def test_set_access_ignores_unknown_and_invalid_ids(access_db):
    result = resource_access.set_access("agent", 8, [2, 999, "nope", None, 2])
    assert result == [{"role_id": 2, "role_name": "user"}]


def test_grant_and_revoke_are_idempotent(access_db):
    assert resource_access.grant_access("agent", 9, 3, granted_by=1) == [
        {"role_id": 3, "role_name": "analyst"}
    ]
    assert resource_access.grant_access("agent", 9, 3, granted_by=1) == [
        {"role_id": 3, "role_name": "analyst"}
    ]
    assert resource_access.revoke_access("agent", 9, 3) == []
    assert resource_access.revoke_access("agent", 9, 3) == []


def test_ensure_tables_is_idempotent(access_db):
    resource_access.ensure_tables()
    resource_access.ensure_tables()
    conn = _connect(access_db)
    try:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='resource_role_access'"
        ).fetchone()
        index = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='index' AND name='idx_resource_role_access_lookup'"
        ).fetchone()
    finally:
        conn.close()
    assert row is not None and index is not None


# --------------------------------------------------------------------------
# filter_visible: ordering + O(1) queries
# --------------------------------------------------------------------------

def test_filter_visible_order_and_membership(access_db):
    _add_user_role(access_db, 12, 3)
    resource_access.set_access("agent", 2, [3], granted_by=1)
    rows = [
        {"id": 1, "created_by": None},        # grandfather
        {"id": 2, "created_by": 999},         # granted role
        {"id": 3, "created_by": 999},         # unrelated owner, no grant
        {"id": 4, "created_by": 12},          # owner
        {"id": 5},                            # missing owner key -> grandfather
    ]
    visible = resource_access.filter_visible("agent", rows, 12)
    assert [r["id"] for r in visible] == [1, 2, 4, 5]


def test_filter_visible_admin_sees_all(access_db):
    _add_user_role(access_db, 10, 1)
    rows = [{"id": i, "created_by": 999} for i in range(1, 4)]
    assert resource_access.filter_visible("agent", rows, 10) == rows


def test_filter_visible_is_constant_query_count(access_db, monkeypatch):
    _add_user_role(access_db, 12, 3)
    resource_access.set_access("agent", 2, [3], granted_by=1)
    real_connect = resource_access.db.get_db_connection
    calls = {"n": 0}

    def counting_connect():
        calls["n"] += 1
        return real_connect()

    monkeypatch.setattr(resource_access.db, "get_db_connection", counting_connect)

    one = [{"id": 1, "created_by": None}]
    resource_access.filter_visible("agent", one, 12)
    small = calls["n"]

    calls["n"] = 0
    many = [{"id": i, "created_by": 999} for i in range(1, 51)]
    resource_access.filter_visible("agent", many, 12)
    large = calls["n"]

    assert small == large, f"query count grew with row count: {small} -> {large}"
    assert large <= 4, f"filter_visible used {large} connections"


# --------------------------------------------------------------------------
# owner registry
# --------------------------------------------------------------------------

def test_register_resource_replaces_and_resolves(access_db):
    # A test-only resource type: registering a fake resolver under a real type
    # ("skill") would replace the one the shipped module registers at import and
    # leak into later test files.
    resource_access.register_resource("test_widget", lambda rid: 42 if rid == 7 else None)
    assert resource_access.is_registered("test_widget")
    assert resource_access.owner_of("test_widget", 7) == 42
    assert resource_access.owner_of("test_widget", 8) is None
    resource_access.register_resource("test_widget", lambda rid: 99)
    assert resource_access.owner_of("test_widget", 7) == 99


def test_agent_registration_resolves_real_row(access_db):
    from src.agent_platform.catalog.store import DefinitionStore

    row = DefinitionStore.save(
        slug="tenancy-agent", name="Tenancy Agent", kind="agent", config={}, created_by=42
    )
    assert resource_access.is_registered("agent")
    assert resource_access.owner_of("agent", int(row["id"])) == 42
    assert resource_access.resource_exists("agent", int(row["id"]))
    assert not resource_access.resource_exists("agent", 10_000)


# --------------------------------------------------------------------------
# legacy migration
# --------------------------------------------------------------------------

def test_migrate_legacy_grants_roundtrip_and_idempotency(access_db):
    _exec(
        access_db,
        "INSERT INTO agent_definition_role_access (agent_id, role_id, granted_by) VALUES (3, 2, 1)",
    )
    _exec(
        access_db,
        "INSERT INTO hosted_app_role_access (app_id, role_id, granted_by) VALUES (53, 2, 1)",
    )

    first = resource_access.migrate_legacy_grants()
    assert first == {"agent": 1, "hosted_app": 1}
    assert resource_access.list_access("agent", 3) == [{"role_id": 2, "role_name": "user"}]
    assert resource_access.list_access("hosted_app", 53) == [{"role_id": 2, "role_name": "user"}]

    second = resource_access.migrate_legacy_grants()
    assert second == {"agent": 0, "hosted_app": 0}
    assert resource_access.list_access("agent", 3) == [{"role_id": 2, "role_name": "user"}]
    assert resource_access.list_access("hosted_app", 53) == [{"role_id": 2, "role_name": "user"}]


def test_migrate_legacy_grants_without_legacy_tables(access_db):
    _exec(access_db, "DROP TABLE agent_definition_role_access")
    _exec(access_db, "DROP TABLE hosted_app_role_access")
    assert resource_access.migrate_legacy_grants() == {"agent": 0, "hosted_app": 0}


# --------------------------------------------------------------------------
# admin_required decorator (real Flask request, real DB)
# --------------------------------------------------------------------------

def test_admin_required_decorator(access_db):
    from flask import Blueprint, Flask

    from src.auth.decorators import ADMIN_REQUIRED_ATTR, admin_required

    app = Flask(__name__)
    app.secret_key = "test"

    # The real app registers these; the decorator's browser branch redirects to
    # auth.login and its denial branch redirects to index.
    auth_bp = Blueprint("auth", __name__)

    @auth_bp.route("/login")
    def login():
        return "login"

    app.register_blueprint(auth_bp)

    @app.route("/")
    def index():
        return "home"

    @app.route("/api/secret")
    @admin_required()
    def api_secret():
        return {"ok": True}

    @app.route("/browser-secret")
    @admin_required()
    def browser_secret():
        return "secret"

    @app.route("/api/readable")
    @admin_required(allow_read=True)
    def api_readable():
        return {"ok": True}

    _add_user_role(access_db, 10, 1)
    client = app.test_client()

    assert client.get("/api/secret").status_code == 401
    assert client.get("/browser-secret").status_code == 302
    assert getattr(api_secret, ADMIN_REQUIRED_ATTR) is True

    with client.session_transaction() as session:
        session["user_id"] = 11  # no roles
    assert client.get("/api/secret").status_code == 403
    assert client.get("/browser-secret").status_code == 302
    assert client.get("/api/readable").status_code == 200

    with client.session_transaction() as session:
        session["user_id"] = 10  # admin
    assert client.get("/api/secret").status_code == 200
