"""LLM Manager tenancy: real SQLite, real access checks, real HTTP routes.

Nothing here mocks the changed code: connections live in the ``temp_db`` fixture
(never ``text2sql.db``), roles/users are real rows, and the admin routes run as
real Flask requests. Only the unrelated module-RBAC gate
(``UserManager.has_any_module_access``, which reads the ORM-bound production DB)
is pinned open; the resource enforcement under test is real.
"""

from __future__ import annotations

import sqlite3

import pytest

from src.auth import resource_access
from src.models.llm_connection import LLMConnection
from src.utils import llm_connection_manager as mgr

#: Minimal role schema the generic resource-access store reads.
ROLE_SCHEMA = """
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

ADMIN_ID = 10
OWNER_ID = 11
GRANTED_ID = 12
OTHER_ID = 99

ANALYST_ROLE_ID = 2


def _connect(db_path):
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


@pytest.fixture()
def tenancy_db(temp_db):
    """Temp DB with real ``roles``/``user_roles`` rows (admin + analyst)."""
    resource_access._reset_schema_guard()
    conn = _connect(temp_db)
    try:
        conn.executescript(ROLE_SCHEMA)
        conn.execute("INSERT INTO roles (id, name) VALUES (1, 'admin')")
        conn.execute("INSERT INTO roles (id, name) VALUES (2, 'analyst')")
        # user 10 is a platform admin; user 12 holds the grantable analyst role.
        conn.execute("INSERT INTO user_roles (user_id, role_id) VALUES (?, 1)", (ADMIN_ID,))
        conn.execute("INSERT INTO user_roles (user_id, role_id) VALUES (?, 2)", (GRANTED_ID,))
        conn.commit()
    finally:
        conn.close()
    try:
        yield temp_db
    finally:
        resource_access._reset_schema_guard()


def _new_connection(user_id, name="Tenant Conn", **overrides):
    payload = {
        "name": name,
        "base_url": "http://127.0.0.1:9/v1",
        "api_key": "sk-test",
        "model_name": "tenant-model",
    }
    payload.update(overrides)
    return mgr.save_connection(payload, user_id=user_id)


def _closed_port() -> int:
    """A local port nothing listens on: the connection is refused for real."""
    import socket

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


# --- owner stamping + registration ----------------------------------------

def test_create_stamps_owner_and_edits_never_rewrite_it(tenancy_db):
    conn = _new_connection(OWNER_ID)
    assert LLMConnection.get_by_id(conn.id).created_by == OWNER_ID

    # An edit (even one claimed by a different user) keeps the original owner.
    mgr.save_connection(
        {"id": conn.id, "name": "Renamed", "base_url": "https://x", "model_name": "m2"},
        user_id=OTHER_ID,
    )
    reloaded = LLMConnection.get_by_id(conn.id)
    assert reloaded.name == "Renamed"
    assert reloaded.created_by == OWNER_ID


def test_owner_null_create_is_deployment_shared(tenancy_db):
    """The seed path creates owner-less rows, which are shared with everyone."""
    conn = mgr.save_connection(
        {"name": "Default", "base_url": "https://seed", "api_key": "k", "model_name": "m"}
    )
    assert LLMConnection.get_by_id(conn.id).created_by is None
    assert mgr.can_access_connection(conn.id, None) is True
    assert mgr.can_access_connection(conn.id, OTHER_ID) is True
    assert conn.id in mgr.visible_connection_ids(OTHER_ID)
    # Only an administrator manages a deployment-level row.
    assert mgr.can_manage_connection(conn.id, OTHER_ID) is False
    assert mgr.can_manage_connection(conn.id, ADMIN_ID) is True


def test_seed_default_from_config_is_shared_and_owner_less(monkeypatch, tenancy_db):
    import config.config as cfg

    monkeypatch.setattr(cfg, "OPENROUTER_BASE_URL", "https://seed.example/v1")
    monkeypatch.setattr(cfg, "OPENROUTER_API_KEY", "sk-seed")
    monkeypatch.setattr(cfg, "OPENROUTER_MODEL", "seed-model")
    monkeypatch.setattr(cfg, "TEMPERATURE", 0.5)
    monkeypatch.setattr(cfg, "MAX_TOKENS", 999)

    mgr.seed_default_from_config()
    seeded = LLMConnection.get_default()
    assert seeded is not None and seeded.created_by is None
    # The deployment default keeps working for every authenticated user.
    assert mgr.can_access_connection(seeded.id, OTHER_ID) is True
    assert mgr.resolve("default").id == seeded.id


def test_registration_resolves_real_rows(tenancy_db):
    conn = _new_connection(OWNER_ID, name="Registered")
    assert resource_access.is_registered("llm_connection")
    assert resource_access.owner_of("llm_connection", conn.id) == OWNER_ID
    assert resource_access.resource_exists("llm_connection", conn.id) is True
    assert resource_access.resource_exists("llm_connection", 10_000) is False


def test_migration_adds_created_by_and_grandfathers_existing_rows(tenancy_db):
    """An upgraded database keeps its rows, owner-less and visible to all."""
    conn = _connect(tenancy_db)
    try:
        conn.execute(
            """
            CREATE TABLE llm_connections (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                base_url TEXT,
                api_key TEXT,
                model_name TEXT,
                system_instruction TEXT,
                extra_body TEXT,
                http_headers TEXT,
                verify_ssl INTEGER DEFAULT 1,
                is_default INTEGER DEFAULT 0,
                enabled INTEGER DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute("INSERT INTO llm_connections (name, model_name) VALUES ('Legacy', 'm')")
        conn.commit()
    finally:
        conn.close()

    LLMConnection.create_table()
    LLMConnection.create_table()  # idempotent

    check = _connect(tenancy_db)
    try:
        columns = {row["name"] for row in check.execute("PRAGMA table_info(llm_connections)")}
    finally:
        check.close()
    assert "created_by" in columns

    legacy = LLMConnection.get_by_name("Legacy")
    assert legacy.created_by is None
    assert mgr.can_access_connection(legacy.id, OTHER_ID) is True
    assert legacy.id in mgr.visible_connection_ids(OTHER_ID)


# --- decision matrix ---

def test_owner_sees_uses_and_manages_own_connection(tenancy_db):
    conn = _new_connection(OWNER_ID)
    assert mgr.can_access_connection(conn.id, OWNER_ID) is True
    assert mgr.can_manage_connection(conn.id, OWNER_ID) is True
    assert mgr.visible_connection_ids(OWNER_ID) == {conn.id}


def test_admin_sees_and_manages_every_connection(tenancy_db):
    mine = _new_connection(OWNER_ID, name="Mine")
    theirs = _new_connection(GRANTED_ID, name="Theirs")
    shared = mgr.save_connection(
        {"name": "Shared", "base_url": "https://s", "api_key": "k", "model_name": "m"}
    )

    assert mgr.visible_connection_ids(ADMIN_ID) is None  # None => all
    for cid in (mine.id, theirs.id, shared.id):
        assert mgr.can_access_connection(cid, ADMIN_ID) is True
        assert mgr.can_manage_connection(cid, ADMIN_ID) is True


def test_unrelated_user_has_no_visibility_or_control(tenancy_db):
    conn = _new_connection(OWNER_ID)
    assert mgr.visible_connection_ids(OTHER_ID) == set()
    assert mgr.can_access_connection(conn.id, OTHER_ID) is False
    assert mgr.can_manage_connection(conn.id, OTHER_ID) is False
    assert mgr.can_access_connection(conn.id, None) is False  # fail closed


def test_granted_role_may_use_but_not_manage(tenancy_db):
    conn = _new_connection(OWNER_ID)
    assert mgr.can_access_connection(conn.id, GRANTED_ID) is False

    resource_access.set_access("llm_connection", conn.id, [ANALYST_ROLE_ID], granted_by=OWNER_ID)

    assert mgr.can_access_connection(conn.id, GRANTED_ID) is True
    assert mgr.can_manage_connection(conn.id, GRANTED_ID) is False
    assert conn.id in mgr.visible_connection_ids(GRANTED_ID)


# --- model resolution fails closed ---

def test_client_from_payload_fails_closed_for_another_tenant(tenancy_db):
    from src.agent_platform.runtime.model_select import client_from_payload
    from src.utils.llm_connection_manager import LLMConnectionError

    conn = _new_connection(OWNER_ID)
    payload = {"model": {"client": str(conn.id)}}

    # A different tenant cannot select it, with or without an explicit identity.
    for uid in (OTHER_ID, None):
        with pytest.raises(LLMConnectionError) as exc:
            client_from_payload(payload, user_id=uid)
        assert "not available to this user" in str(exc.value)

    # A role grant turns selection on without granting control.
    resource_access.set_access("llm_connection", conn.id, [ANALYST_ROLE_ID], granted_by=OWNER_ID)
    granted_client = client_from_payload(payload, user_id=GRANTED_ID)
    assert granted_client._llm_connection.id == conn.id

    # The owner can always select their own connection.
    owner_client = client_from_payload(payload, user_id=OWNER_ID)
    assert owner_client._llm_connection.id == conn.id


def test_client_from_payload_denies_unknown_connection(tenancy_db):
    from src.agent_platform.runtime.model_select import client_from_payload
    from src.utils.llm_connection_manager import LLMConnectionError

    with pytest.raises(LLMConnectionError):
        client_from_payload({"model": {"client": "no-such-connection"}}, user_id=OWNER_ID)


def test_public_models_are_filtered_by_identity(tenancy_db):
    from src.agent_platform.runtime.model_select import list_public_models

    mine = _new_connection(OWNER_ID, name="Mine")
    shared = mgr.save_connection(
        {"name": "Shared", "base_url": "https://s", "api_key": "k", "model_name": "m"}
    )

    other_ids = {m["id"] for m in list_public_models(user_id=OTHER_ID)}
    assert other_ids == {str(shared.id)}  # deployment-level only
    owner_ids = {m["id"] for m in list_public_models(user_id=OWNER_ID)}
    assert owner_ids == {str(mine.id), str(shared.id)}
    assert list_public_models(user_id=ADMIN_ID)  # admin sees the full set

    resource_access.set_access("llm_connection", mine.id, [ANALYST_ROLE_ID], granted_by=OWNER_ID)
    granted_ids = {m["id"] for m in list_public_models(user_id=GRANTED_ID)}
    assert granted_ids == {str(mine.id), str(shared.id)}


# --- HTTP routes ---

@pytest.fixture()
def llm_app(tenancy_db, monkeypatch):
    """A real Flask app carrying the shipped config blueprint, temp-DB backed."""
    from flask import Flask

    from src.routes.config_routes import config_bp
    from src.utils.user_manager import UserManager

    # Module RBAC reads the ORM-bound production DB; pin the gate open so these
    # requests reach (and prove) the resource-tenancy enforcement under test.
    monkeypatch.setattr(
        UserManager,
        "has_any_module_access",
        lambda self, user_id, module_keys, min_level="read": True,
    )

    app = Flask(__name__)
    app.secret_key = "test"
    app.register_blueprint(config_bp)
    return app


def _as(app, user_id):
    client = app.test_client()
    with client.session_transaction() as session:
        session["user_id"] = user_id
    return client


def _listed_ids(response):
    body = response.get_json()
    assert response.status_code == 200, response.get_data(as_text=True)
    assert body["status"] == "success"
    return {row["id"]: row for row in body["data"]}


def test_route_list_filters_per_identity(llm_app):
    owner_conn = _new_connection(OWNER_ID, name="Owner Conn")
    other_conn = _new_connection(GRANTED_ID, name="Other Conn")

    owner_resp = _as(llm_app, OWNER_ID).get("/admin/config/llm/api/list")
    owner_rows = _listed_ids(owner_resp)
    assert set(owner_rows) == {owner_conn.id}
    assert owner_rows[owner_conn.id]["can_manage"] is True
    # Only administrators may move the global default; the UI relies on this.
    assert owner_rows[owner_conn.id]["can_set_default"] is False
    assert owner_resp.get_json()["can_set_default"] is False

    admin_resp = _as(llm_app, ADMIN_ID).get("/admin/config/llm/api/list")
    admin_rows = _listed_ids(admin_resp)
    assert set(admin_rows) == {owner_conn.id, other_conn.id}
    assert all(row["can_manage"] for row in admin_rows.values())
    assert all(row["can_set_default"] is True for row in admin_rows.values())
    assert admin_resp.get_json()["can_set_default"] is True

    # An unrelated tenant is filtered, not rejected.
    other_resp = _as(llm_app, OTHER_ID).get("/admin/config/llm/api/list")
    assert _listed_ids(other_resp) == {}
    assert other_resp.get_json()["can_set_default"] is False

    # The single-connection payload carries the same capability flags.
    owner_get = _as(llm_app, OWNER_ID).get(f"/admin/config/llm/api/get/{owner_conn.id}")
    assert owner_get.get_json()["data"]["can_set_default"] is False
    admin_get = _as(llm_app, ADMIN_ID).get(f"/admin/config/llm/api/get/{owner_conn.id}")
    assert admin_get.get_json()["data"]["can_set_default"] is True


def test_route_unrelated_user_denied_on_every_endpoint(llm_app):
    conn = _new_connection(OWNER_ID)
    client = _as(llm_app, OTHER_ID)

    assert client.get(f"/admin/config/llm/api/get/{conn.id}").status_code == 404
    assert client.post(
        "/admin/config/llm/api/save",
        json={"id": conn.id, "name": "Hijacked", "base_url": "https://x", "model_name": "m"},
    ).status_code == 403
    assert client.post(f"/admin/config/llm/api/set-default/{conn.id}").status_code == 403
    assert client.delete(f"/admin/config/llm/api/delete/{conn.id}").status_code == 403
    assert client.post("/admin/config/llm/api/test", json={"id": conn.id}).status_code == 403

    # The row is untouched.
    assert LLMConnection.get_by_id(conn.id).name == "Tenant Conn"


def test_route_granted_role_may_read_and_use_not_delete(llm_app):
    conn = _new_connection(OWNER_ID)
    resource_access.set_access("llm_connection", conn.id, [ANALYST_ROLE_ID], granted_by=OWNER_ID)
    client = _as(llm_app, GRANTED_ID)

    listed = _listed_ids(client.get("/admin/config/llm/api/list"))
    assert set(listed) == {conn.id}
    assert listed[conn.id]["can_manage"] is False

    assert client.get(f"/admin/config/llm/api/get/{conn.id}").status_code == 200
    assert client.delete(f"/admin/config/llm/api/delete/{conn.id}").status_code == 403
    assert client.post(
        "/admin/config/llm/api/save",
        json={"id": conn.id, "name": "Edit", "base_url": "https://x", "model_name": "m"},
    ).status_code == 403
    assert client.post(f"/admin/config/llm/api/set-default/{conn.id}").status_code == 403
    assert client.post("/admin/config/llm/api/test", json={"id": conn.id}).status_code == 403
    assert LLMConnection.get_by_id(conn.id).name == "Tenant Conn"


def test_route_owner_can_manage_own_connection(llm_app):
    port = _closed_port()
    conn = _new_connection(OWNER_ID, base_url=f"http://127.0.0.1:{port}/v1")
    client = _as(llm_app, OWNER_ID)

    save = client.post(
        "/admin/config/llm/api/save",
        json={
            "id": conn.id,
            "name": "Owner Conn",
            "base_url": f"http://127.0.0.1:{port}/v1",
            "model_name": "updated-model",
        },
    )
    assert save.status_code == 200, save.get_data(as_text=True)
    assert LLMConnection.get_by_id(conn.id).model_name == "updated-model"

    # A real (unreachable) test is allowed for the owner: not a 403. The manager
    # reports the real transport failure, so the route answers 200 with details.
    tested = client.post("/admin/config/llm/api/test", json={"id": conn.id})
    assert tested.status_code == 200
    assert tested.get_json()["status"] == "error"
    assert tested.get_json()["message"]

    assert client.get(f"/admin/config/llm/api/get/{conn.id}").status_code == 200

    # The global default is administrator-only (see the dedicated test below);
    # an owner is rejected even for their own connection.
    set_default = client.post(f"/admin/config/llm/api/set-default/{conn.id}")
    assert set_default.status_code == 403
    assert LLMConnection.get_by_id(conn.id).is_default == 0

    deleted = client.delete(f"/admin/config/llm/api/delete/{conn.id}")
    assert deleted.status_code == 200
    assert LLMConnection.get_by_id(conn.id) is None


def test_route_create_is_private_to_creator(llm_app):
    other_client = _as(llm_app, OTHER_ID)
    created = other_client.post(
        "/admin/config/llm/api/save",
        json={"name": "Other Own", "base_url": "https://o", "model_name": "m"},
    )
    assert created.status_code == 200, created.get_data(as_text=True)
    new_id = created.get_json()["id"]
    assert LLMConnection.get_by_id(new_id).created_by == OTHER_ID

    # It shows up for the creator and the admin, and for nobody else.
    assert new_id in _listed_ids(other_client.get("/admin/config/llm/api/list"))
    assert new_id in _listed_ids(_as(llm_app, ADMIN_ID).get("/admin/config/llm/api/list"))
    assert new_id not in _listed_ids(_as(llm_app, OWNER_ID).get("/admin/config/llm/api/list"))
    assert _as(llm_app, OWNER_ID).get(f"/admin/config/llm/api/get/{new_id}").status_code == 404


def test_route_shared_default_visible_but_admin_managed(llm_app):
    shared = mgr.save_connection(
        {"name": "Deployment Default", "base_url": "https://s", "api_key": "k", "model_name": "m"}
    )
    # The unrelated tenant sees and can use the deployment default.
    listed = _listed_ids(_as(llm_app, OTHER_ID).get("/admin/config/llm/api/list"))
    assert set(listed) == {shared.id}
    assert listed[shared.id]["can_manage"] is False
    assert _as(llm_app, OTHER_ID).get(f"/admin/config/llm/api/get/{shared.id}").status_code == 200
    # ...but cannot manage it.
    assert _as(llm_app, OTHER_ID).delete(
        f"/admin/config/llm/api/delete/{shared.id}"
    ).status_code == 403
    # The administrator can.
    assert _as(llm_app, ADMIN_ID).delete(
        f"/admin/config/llm/api/delete/{shared.id}"
    ).status_code == 200


def test_generic_access_api_manages_llm_connection_grants(tenancy_db):
    """The UI's role-grant dialog path: the shipped generic access endpoint."""
    from flask import Flask

    from src.agent_platform.api.access_routes import access_bp

    app = Flask(__name__)
    app.secret_key = "test"
    app.register_blueprint(access_bp, url_prefix="/api/v1")
    conn = _new_connection(OWNER_ID)
    owner = _as(app, OWNER_ID)

    read = owner.get(f"/api/v1/access/llm_connection/{conn.id}")
    assert read.status_code == 200, read.get_data(as_text=True)
    assert read.get_json()["access"] == []
    assert any(role["id"] == ANALYST_ROLE_ID for role in read.get_json()["roles"])

    put = owner.put(
        f"/api/v1/access/llm_connection/{conn.id}", json={"role_ids": [ANALYST_ROLE_ID]}
    )
    assert put.status_code == 200, put.get_data(as_text=True)
    assert [entry["role_id"] for entry in put.get_json()["access"]] == [ANALYST_ROLE_ID]
    assert mgr.can_access_connection(conn.id, GRANTED_ID) is True
    assert mgr.can_manage_connection(conn.id, GRANTED_ID) is False

    # An unrelated user cannot read or replace the grants; an unknown id is 404.
    assert _as(app, OTHER_ID).get(
        f"/api/v1/access/llm_connection/{conn.id}"
    ).status_code == 403
    assert owner.get("/api/v1/access/llm_connection/99999").status_code == 404


# --- global default: administrators only (deployment-wide state) ---

def test_global_default_is_admin_only(llm_app):
    """A tenant owner cannot point the global default at their private row."""
    from src.utils.llm_connection_manager import LLMConnectionError

    shared = mgr.save_connection(
        {
            "name": "Deployment",
            "base_url": "https://d",
            "api_key": "k",
            "model_name": "m",
            "is_default": True,
        }
    )
    private = _new_connection(OWNER_ID, name="Owner Private")
    owner = _as(llm_app, OWNER_ID)

    denied = owner.post(f"/admin/config/llm/api/set-default/{private.id}")
    assert denied.status_code == 403, denied.get_data(as_text=True)
    assert "administrator" in denied.get_json()["message"].lower()
    # The deployment default never moved.
    assert LLMConnection.get_by_id(shared.id).is_default == 1
    assert LLMConnection.get_by_id(private.id).is_default == 0

    # Root enforcement: the manager API rejects a non-admin actor too.
    with pytest.raises(LLMConnectionError):
        mgr.set_default(private.id, user_id=OWNER_ID)

    # A non-admin save cannot smuggle the flag in, at the route or the root.
    smuggled = owner.post(
        "/admin/config/llm/api/save",
        json={
            "id": private.id,
            "name": "Owner Private",
            "base_url": "https://p",
            "model_name": "m",
            "is_default": True,
        },
    )
    assert smuggled.status_code == 403, smuggled.get_data(as_text=True)
    assert LLMConnection.get_by_id(private.id).is_default == 0
    with pytest.raises(LLMConnectionError):
        mgr.save_connection(
            {"id": private.id, "name": "Owner Private", "is_default": True}, user_id=OWNER_ID
        )
    assert LLMConnection.get_by_id(private.id).is_default == 0

    # Editing the connection without touching the flag is still allowed.
    edit = owner.post(
        "/admin/config/llm/api/save",
        json={
            "id": private.id,
            "name": "Owner Private",
            "base_url": "https://p",
            "model_name": "m2",
        },
    )
    assert edit.status_code == 200, edit.get_data(as_text=True)
    assert LLMConnection.get_by_id(private.id).model_name == "m2"

    # An administrator owns the global default and can move it.
    admin_conn = _new_connection(ADMIN_ID, name="Admin Conn")
    allowed = _as(llm_app, ADMIN_ID).post(
        f"/admin/config/llm/api/set-default/{admin_conn.id}"
    )
    assert allowed.status_code == 200, allowed.get_data(as_text=True)
    assert LLMConnection.get_by_id(admin_conn.id).is_default == 1
    assert LLMConnection.get_by_id(shared.id).is_default == 0


def test_owner_keeps_explicit_use_of_their_private_connection(tenancy_db):
    """Admin-only default never blocks an owner selecting their own connection."""
    from src.agent_platform.runtime.model_select import client_from_payload, list_public_models
    from src.utils.llm_connection_manager import LLMConnectionError

    shared = mgr.save_connection(
        {
            "name": "Deployment",
            "base_url": "https://d",
            "api_key": "k",
            "model_name": "m",
            "is_default": True,
        }
    )
    private = _new_connection(OWNER_ID, name="Owner Private")

    # The deployment default is unchanged and is still the resolved default.
    assert mgr.resolve("default").id == shared.id

    # The owner explicitly selects their own private connection for a run.
    client = client_from_payload({"model": {"client": str(private.id)}}, user_id=OWNER_ID)
    assert client._llm_connection.id == private.id
    ids = {m["id"] for m in list_public_models(user_id=OWNER_ID)}
    assert ids == {str(private.id), str(shared.id)}

    # Another tenant still cannot select it, and the shared default stays usable.
    with pytest.raises(LLMConnectionError):
        client_from_payload({"model": {"client": str(private.id)}}, user_id=OTHER_ID)
    assert client_from_payload({"model": {"client": str(shared.id)}}, user_id=OTHER_ID) is not None
