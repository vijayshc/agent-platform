"""Observability tenancy: run logs are visible only to the agents' tenants.

Real integration test on the shipped ``/api/v1`` blueprint with the isolated
temp DB (``app_client``) and real API-key auth.  Runs are created through
``RunStore`` and definitions through ``DefinitionStore`` -- the same code paths
the HTTP API uses -- so the list/get/stream access decisions run against real
SQL rows.

Rule under test (``RunStore.can_view``):
    owner  OR  can_access("agent", definition_id, definition_owner, user)
Runs with no definition are owner-only; direct access to an invisible run is
403; administrators see everything; the raw Phoenix proxy is admin-only.
"""

from __future__ import annotations

import sqlite3

import pytest

_ROLE_SCHEMA = """
CREATE TABLE IF NOT EXISTS roles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,
    description TEXT
);
CREATE TABLE IF NOT EXISTS user_roles (user_id INTEGER, role_id INTEGER);
"""


def _connect(path):
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


@pytest.fixture()
def obs(app_client, temp_db):
    """A real temp-DB tenancy: 3 roles, 5 users, 5 definitions, 7 runs, 5 keys."""
    from src.agent_platform.catalog.store import DefinitionStore
    from src.agent_platform.execution.api_keys import ApiKeyStore
    from src.agent_platform.execution.run_store import RunStore
    from src.auth import resource_access

    resource_access._reset_schema_guard()

    conn = _connect(temp_db)
    try:
        conn.executescript(_ROLE_SCHEMA)
        for role_id, name in ((1, "admin"), (2, "obs-alpha"), (3, "obs-beta")):
            conn.execute("INSERT INTO roles (id, name) VALUES (?, ?)", (role_id, name))
        # user 1 is the admin API-key owner created by ``app_client``.
        for user_id, role_id in ((1, 1), (30, 2), (40, 3)):
            conn.execute("INSERT INTO user_roles (user_id, role_id) VALUES (?, ?)", (user_id, role_id))
        conn.commit()
    finally:
        conn.close()

    def make_def(slug: str, owner: int | None) -> dict:
        return DefinitionStore.upsert(
            slug=slug,
            name=slug,
            kind="agent",
            config={"kind": "agent", "instructions": "obs tenancy probe"},
            published=True,
            created_by=owner,
        )

    def make_run(definition: dict | None, owner: int) -> dict:
        return RunStore.create(
            task=f"probe {definition['slug'] if definition else 'no-definition'}",
            definition_id=definition["id"] if definition else None,
            user_id=owner,
            agent_slug=definition["slug"] if definition else None,
        )

    definitions = {
        "owned_by_10": make_def("obs-owned-10", 10),
        "owned_by_20": make_def("obs-owned-20", 20),
        "granted_alpha": make_def("obs-granted-alpha", 20),
        "granted_beta": make_def("obs-granted-beta", 20),
        "grandfathered": make_def("obs-grandfathered", None),
    }
    resource_access.set_access("agent", definitions["granted_alpha"]["id"], [2], granted_by=1)
    resource_access.set_access("agent", definitions["granted_beta"]["id"], [3], granted_by=1)

    runs = {
        "owned_by_10": make_run(definitions["owned_by_10"], 10),
        "owned_by_20": make_run(definitions["owned_by_20"], 20),
        "granted_alpha": make_run(definitions["granted_alpha"], 20),
        "granted_beta": make_run(definitions["granted_beta"], 20),
        "grandfathered": make_run(definitions["grandfathered"], 20),
        "nodef_10": make_run(None, 10),
        "nodef_20": make_run(None, 20),
    }

    scopes = ["agents:read", "agents:write", "runs:read", "runs:write"]
    keys = {
        uid: ApiKeyStore.create(user_id=uid, name=f"obs-key-{uid}", scopes=scopes)["key"]
        for uid in (1, 10, 20, 30, 40)
    }

    def client_for(uid: int):
        client = app_client.application.test_client()
        client.environ_base["HTTP_X_API_KEY"] = keys[uid]
        return client

    return {
        "definitions": definitions,
        "runs": runs,
        "client_for": client_for,
        "keys": keys,
    }


def _run_ids(client) -> set:
    response = client.get("/api/v1/runs?limit=200")
    assert response.status_code == 200, response.get_data(as_text=True)
    return {row["id"] for row in response.get_json()["runs"]}


# --------------------------------------------------------------------------
# list scoping
# --------------------------------------------------------------------------

def test_list_excludes_other_tenant_and_includes_role_granted(obs):
    alpha = obs["client_for"](30)  # holds the role granted on "granted_alpha"
    visible = _run_ids(alpha)

    assert obs["runs"]["owned_by_10"]["id"] not in visible
    assert obs["runs"]["owned_by_20"]["id"] not in visible
    assert obs["runs"]["granted_beta"]["id"] not in visible  # other tenant's grant
    assert obs["runs"]["nodef_20"]["id"] not in visible      # no definition: owner only
    assert obs["runs"]["granted_alpha"]["id"] in visible     # granted to caller's role
    assert obs["runs"]["grandfathered"]["id"] in visible     # unowned definition


def test_list_is_owner_scoped_for_a_user_without_grants(obs):
    owner = obs["client_for"](10)
    visible = _run_ids(owner)

    assert obs["runs"]["owned_by_10"]["id"] in visible
    assert obs["runs"]["nodef_10"]["id"] in visible
    assert obs["runs"]["owned_by_20"]["id"] not in visible
    assert obs["runs"]["granted_alpha"]["id"] not in visible


def test_list_limit_counts_visible_runs(obs):
    owner = obs["client_for"](10)  # exactly two visible runs
    response = owner.get("/api/v1/runs?limit=1")
    assert response.status_code == 200
    assert len(response.get_json()["runs"]) == 1


def test_admin_sees_every_run(obs):
    admin = obs["client_for"](1)
    visible = _run_ids(admin)
    assert {run["id"] for run in obs["runs"].values()} <= visible


# --------------------------------------------------------------------------
# direct access fails closed
# --------------------------------------------------------------------------

def test_direct_access_to_invisible_run_is_403(obs):
    alpha = obs["client_for"](30)
    other = obs["runs"]["owned_by_20"]

    for path in (
        f"/api/v1/runs/{other['public_id']}",
        f"/api/v1/runs/{other['public_id']}/stream",
        f"/api/v1/runs/{other['public_id']}/spans",
        f"/api/v1/runs/{other['public_id']}/events",
        f"/api/v1/runs/{other['public_id']}/trace",
    ):
        response = alpha.get(path)
        assert response.status_code == 403, f"{path} -> {response.status_code}"

    cancel = alpha.post(f"/api/v1/runs/{other['public_id']}/cancel")
    assert cancel.status_code == 403

    resume = alpha.post(
        f"/api/v1/runs/{other['public_id']}/resume", json={"decisions": []}
    )
    assert resume.status_code == 403


def test_role_granted_user_can_open_and_read_spans(obs):
    alpha = obs["client_for"](30)
    public_id = obs["runs"]["granted_alpha"]["public_id"]

    detail = alpha.get(f"/api/v1/runs/{public_id}")
    assert detail.status_code == 200
    assert detail.get_json()["id"] == obs["runs"]["granted_alpha"]["id"]

    assert alpha.get(f"/api/v1/runs/{public_id}/spans").status_code == 200
    assert alpha.get(f"/api/v1/runs/{public_id}/events").status_code == 200


def test_role_granted_user_can_read_the_run_trace_payload(obs):
    """The trace endpoint answers for a visible run and never leaks another's.

    These ``RunStore`` runs have no Phoenix trace, so the payload is the
    ``available: false`` shape; what matters here is that the tenancy gate runs
    on the *run* (403 for a stranger) and that a permitted caller gets a
    well-formed, non-error payload.
    """
    alpha = obs["client_for"](30)
    public_id = obs["runs"]["granted_alpha"]["public_id"]

    response = alpha.get(f"/api/v1/runs/{public_id}/trace")
    assert response.status_code == 200, response.get_data(as_text=True)
    payload = response.get_json()
    assert payload["available"] is False
    assert payload["reason"] in {"no_trace", "trace_not_found", "phoenix_unavailable"}
    assert payload["spans"] == []

    stranger = obs["client_for"](40)
    denied = stranger.get(f"/api/v1/runs/{public_id}/trace")
    assert denied.status_code == 403

    missing = alpha.get("/api/v1/runs/does-not-exist/trace")
    assert missing.status_code == 404


def test_run_without_definition_is_owner_only(obs):
    public_id = obs["runs"]["nodef_10"]["public_id"]

    owner = obs["client_for"](10)
    assert owner.get(f"/api/v1/runs/{public_id}").status_code == 200

    stranger = obs["client_for"](30)
    assert stranger.get(f"/api/v1/runs/{public_id}").status_code == 403

    admin = obs["client_for"](1)
    assert admin.get(f"/api/v1/runs/{public_id}").status_code == 200


def test_admin_can_open_any_run(obs):
    admin = obs["client_for"](1)
    for run in obs["runs"].values():
        assert admin.get(f"/api/v1/runs/{run['public_id']}").status_code == 200


# --------------------------------------------------------------------------
# raw Phoenix proxy: administrators only
# --------------------------------------------------------------------------

def test_phoenix_proxy_is_admin_only(app_client, obs):
    from src.agent_platform.api.phoenix_proxy import phoenix_proxy_bp

    # ``app_client`` mounts only the /api/v1 blueprint; the proxy lives on the
    # app itself (app.py), so mount it here before the first request.
    app_client.application.register_blueprint(phoenix_proxy_bp)

    observability_user = obs["client_for"](30)
    denied = observability_user.get("/api/v1/phoenix/proxy/")
    assert denied.status_code == 403

    admin = obs["client_for"](1)
    allowed = admin.get("/api/v1/phoenix/proxy/")
    assert allowed.status_code != 403
