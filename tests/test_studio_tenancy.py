"""Agent Studio tenancy: the catalogue only shows and mutates what the caller may access.

Real HTTP through the shipped ``/api/v1`` blueprint on Flask's test client, a
real SQLite file (the ``temp_db`` fixture) and real API-key auth. The temp DB is
the only database the platform touches: the SQLAlchemy session ``UserManager``
uses is pointed at it too, so role grants and module permissions decide exactly
as they do in production instead of falling back to the real ``text2sql.db``.

Covered requirement: a user sees agents they developed or that a role they hold
was granted, and nothing from another tenant. The chat picker (``GET /agents``)
is filtered the same way, which is the root of the cross-tenant leak this module
fixes.
"""

from __future__ import annotations

import pytest

from src.agent_platform.catalog.store import DefinitionStore
from src.agent_platform.conversations.store import ConversationStore
from src.agent_platform.execution.api_keys import ApiKeyStore
from src.auth import resource_access

SCOPES = ["agents:read", "agents:write", "runs:read", "runs:write"]

ADMIN_UID, OWNER_UID, GRANTED_UID, OTHER_UID = 1, 2, 3, 4
ADMIN_ROLE, ANALYST_ROLE, OTHER_ROLE = 1, 2, 3


def _agent(slug: str, created_by: int, *, published: bool = True) -> dict:
    """A real definition row owned by ``created_by``."""
    return DefinitionStore.upsert(
        slug=slug,
        name=slug,
        kind="agent",
        config={"kind": "agent", "instructions": f"Tenancy probe for {slug}."},
        published=published,
        created_by=created_by,
    )


def _seed_identities(db_path):
    """Users, roles and module grants in the temp DB (raw SQL and ORM agree)."""
    from sqlalchemy.orm import scoped_session, sessionmaker

    from src.models.user import Base, Permission, Role, User
    from src.utils.database import create_db_engine

    engine = create_db_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
    factory = scoped_session(sessionmaker(bind=engine))
    session = factory()
    session.add_all([
        Role(id=ADMIN_ROLE, name="admin", description="Administrator"),
        Role(id=ANALYST_ROLE, name="analyst", description="Analyst (studio user)"),
        Role(id=OTHER_ROLE, name="other", description="Other tenant (studio + observability)"),
    ])
    session.flush()

    studio = Permission(id=1, name="module:agent_studio", description="Agent Studio")
    observability = Permission(id=2, name="module:observability", description="Observability")
    session.add_all([studio, observability])
    session.flush()

    roles = {role.id: role for role in session.query(Role).all()}
    # Both non-admin tenants may open the Studio so a denial below proves the
    # *resource* rule, not merely a missing module.
    roles[ANALYST_ROLE].permissions.append(studio)
    roles[OTHER_ROLE].permissions.extend([studio, observability])

    for uid, name, role_id in (
        (ADMIN_UID, "admin", ADMIN_ROLE),
        (OWNER_UID, "owner", ANALYST_ROLE),
        (GRANTED_UID, "granted", ANALYST_ROLE),
        (OTHER_UID, "other", OTHER_ROLE),
    ):
        user = User(
            id=uid,
            username=name,
            email=f"{name}@studio-tenancy.test",
            password_hash="x",
            is_active=True,
        )
        session.add(user)
        session.flush()
        user.roles.append(roles[role_id])
    session.commit()
    factory.remove()
    return engine, factory


@pytest.fixture()
def tenancy(app_client, temp_db, monkeypatch):
    """Four identities, real rows for every tenanted collection, and their keys."""
    from src.agent_platform.catalog.skill_packages import write_package
    from src.models.llm_connection import LLMConnection
    from src.models.mcp_server import MCPServer
    from src.utils.llm_connection_manager import save_connection

    engine, factory = _seed_identities(temp_db)
    monkeypatch.setattr("src.utils.database._Session", factory)
    resource_access._reset_schema_guard()

    DefinitionStore.ensure_tables()
    ConversationStore.ensure_tables()
    MCPServer.create_table()
    LLMConnection.create_table()

    keys = {
        uid: ApiKeyStore.create(user_id=uid, name=f"studio-{uid}", scopes=list(SCOPES))
        for uid in (OWNER_UID, GRANTED_UID, OTHER_UID)
    }

    agents = {
        "owner_published": _agent("owner-published", OWNER_UID),
        "owner_draft": _agent("owner-draft", OWNER_UID, published=False),
        "shared": _agent("shared-granted", OWNER_UID),
        "other_published": _agent("other-published", OTHER_UID),
    }
    resource_access.grant_access(
        "agent", int(agents["shared"]["id"]), ANALYST_ROLE, granted_by=OWNER_UID
    )

    servers = {
        "owner": MCPServer(
            name="owner-server",
            description="owner",
            server_type="stdio",
            config={"command": "echo"},
            created_by=OWNER_UID,
        ).save(),
        "other": MCPServer(
            name="other-server",
            description="other",
            server_type="stdio",
            config={"command": "echo"},
            created_by=OTHER_UID,
        ).save(),
    }

    write_package(name="owner-skill", description="owner", instructions="owner", created_by=OWNER_UID)
    write_package(name="other-skill", description="other", instructions="other", created_by=OTHER_UID)

    connections = {
        "owner": save_connection(
            {"name": "owner-conn", "base_url": "http://127.0.0.1:9/v1", "api_key": "sk-owner", "model_name": "m"},
            user_id=OWNER_UID,
        ),
        "other": save_connection(
            {"name": "other-conn", "base_url": "http://127.0.0.1:9/v1", "api_key": "sk-other", "model_name": "m"},
            user_id=OTHER_UID,
        ),
    }

    conversation = ConversationStore.create(user_id=OWNER_UID, title="owner chat", agent_slug="owner-published")

    yield {
        "http": app_client,
        "keys": keys,
        "agents": agents,
        "servers": servers,
        "connections": connections,
        "conversation": conversation,
    }

    try:
        factory.remove()
        engine.dispose()
    except Exception:
        pass


def _admin_key(app_client) -> dict:
    """The key ``app_client`` already carries (user 1, the administrator)."""
    return {"key": app_client.environ_base["HTTP_X_API_KEY"]}


def _as(http, key: dict, method: str, path: str, **kwargs):
    headers = dict(kwargs.pop("headers", None) or {})
    headers["X-API-Key"] = key["key"]
    return getattr(http, method)(path, headers=headers, **kwargs)


def _owner(tenancy):
    return tenancy["keys"][OWNER_UID]


def _granted(tenancy):
    return tenancy["keys"][GRANTED_UID]


def _other(tenancy):
    return tenancy["keys"][OTHER_UID]


def _slugs(response) -> set[str]:
    return {a["slug"] for a in response.get_json()["agents"]}


# --------------------------------------------------------------------------
# GET /agents — the chat picker and the Studio list
# --------------------------------------------------------------------------

def test_public_list_excludes_another_tenants_agent(tenancy):
    http = tenancy["http"]
    owner = _as(http, _owner(tenancy), "get", "/api/v1/agents")
    assert owner.status_code == 200, owner.get_data(as_text=True)
    slugs = _slugs(owner)
    assert "owner-published" in slugs
    assert "other-published" not in slugs
    assert "owner-draft" not in slugs  # drafts never appear without include_drafts

    other = _as(http, _other(tenancy), "get", "/api/v1/agents")
    assert "other-published" in _slugs(other)
    assert "owner-published" not in _slugs(other)


def test_owner_sees_own_draft_with_include_drafts(tenancy):
    http = tenancy["http"]
    resp = _as(http, _owner(tenancy), "get", "/api/v1/agents?include_drafts=1")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    slugs = _slugs(resp)
    assert "owner-draft" in slugs
    assert "owner-published" in slugs
    assert "other-published" not in slugs
    row = next(a for a in resp.get_json()["agents"] if a["slug"] == "owner-draft")
    assert row["published"] is False
    assert row["can_manage"] is True  # owner may manage their own definition


def test_role_granted_user_sees_shared_agent_in_both_lists(tenancy):
    http = tenancy["http"]
    granted = _granted(tenancy)

    published = _as(http, granted, "get", "/api/v1/agents")
    assert published.status_code == 200, published.get_data(as_text=True)
    assert "shared-granted" in _slugs(published)
    assert "owner-draft" not in _slugs(published)
    assert "other-published" not in _slugs(published)

    drafts = _as(http, granted, "get", "/api/v1/agents?include_drafts=1")
    assert drafts.status_code == 200, drafts.get_data(as_text=True)
    slugs = _slugs(drafts)
    assert "shared-granted" in slugs
    assert "owner-draft" not in slugs      # owned by another tenant, not granted
    assert "owner-published" not in slugs  # published but not granted
    row = next(a for a in drafts.get_json()["agents"] if a["slug"] == "shared-granted")
    assert row["can_manage"] is False  # a grant confers use, not ownership


# --------------------------------------------------------------------------
# GET /agents/<id> — direct reads fail closed
# --------------------------------------------------------------------------

def test_unrelated_user_gets_404_on_another_tenants_agent(tenancy):
    http = tenancy["http"]
    other = _other(tenancy)
    owner_id = tenancy["agents"]["owner_published"]["id"]
    draft_id = tenancy["agents"]["owner_draft"]["id"]

    assert _as(http, other, "get", f"/api/v1/agents/{owner_id}").status_code == 404
    assert _as(http, other, "get", f"/api/v1/agents/{draft_id}").status_code == 404
    # The full Studio shape is gated by access first, then by studio access.
    assert _as(http, other, "get", f"/api/v1/agents/{owner_id}?full=1").status_code == 404


def test_accessible_published_agent_is_readable_but_draft_is_not_public(tenancy):
    http = tenancy["http"]
    granted = _granted(tenancy)
    owner = _owner(tenancy)

    shared_id = tenancy["agents"]["shared"]["id"]
    assert _as(http, granted, "get", f"/api/v1/agents/{shared_id}").status_code == 200
    assert _as(http, granted, "get", f"/api/v1/agents/{shared_id}?full=1").status_code == 200

    draft_id = tenancy["agents"]["owner_draft"]["id"]
    assert _as(http, owner, "get", f"/api/v1/agents/{draft_id}").status_code == 404
    assert _as(http, owner, "get", f"/api/v1/agents/{draft_id}?full=1").status_code == 200


# --------------------------------------------------------------------------
# PUT / DELETE / publish
# --------------------------------------------------------------------------

def test_unrelated_user_mutations_are_403(tenancy):
    http = tenancy["http"]
    other = _other(tenancy)
    agent_id = tenancy["agents"]["owner_published"]["id"]

    put = _as(http, other, "put", f"/api/v1/agents/{agent_id}", json={"name": "stolen"})
    assert put.status_code == 403, put.get_data(as_text=True)
    delete = _as(http, other, "delete", f"/api/v1/agents/{agent_id}")
    assert delete.status_code == 403, delete.get_data(as_text=True)
    publish = _as(http, other, "post", f"/api/v1/agents/{agent_id}/publish", json={})
    assert publish.status_code == 403, publish.get_data(as_text=True)
    assert DefinitionStore.get_by_id(int(agent_id)) is not None


def test_owner_can_update_publish_and_delete(tenancy):
    http = tenancy["http"]
    owner = _owner(tenancy)

    draft = _agent("owner-mutable", OWNER_UID, published=False)
    agent_id = draft["id"]

    put = _as(http, owner, "put", f"/api/v1/agents/{agent_id}", json={"name": "owner-mutable-renamed"})
    assert put.status_code == 200, put.get_data(as_text=True)
    assert put.get_json()["name"] == "owner-mutable-renamed"

    publish = _as(http, owner, "post", f"/api/v1/agents/{agent_id}/publish", json={})
    assert publish.status_code == 200, publish.get_data(as_text=True)
    assert publish.get_json()["published"] is True

    delete = _as(http, owner, "delete", f"/api/v1/agents/{agent_id}")
    assert delete.status_code == 200, delete.get_data(as_text=True)
    assert DefinitionStore.get_by_id(int(agent_id)) is None


def test_admin_can_update_publish_and_delete_another_tenants_agent(tenancy):
    http = tenancy["http"]
    admin = _admin_key(http)
    draft = _agent("owner-admin-target", OWNER_UID, published=False)
    agent_id = draft["id"]

    put = _as(http, admin, "put", f"/api/v1/agents/{agent_id}", json={"name": "admin-renamed"})
    assert put.status_code == 200, put.get_data(as_text=True)
    publish = _as(http, admin, "post", f"/api/v1/agents/{agent_id}/publish", json={})
    assert publish.status_code == 200, publish.get_data(as_text=True)
    delete = _as(http, admin, "delete", f"/api/v1/agents/{agent_id}")
    assert delete.status_code == 200, delete.get_data(as_text=True)


def test_role_granted_user_can_use_and_publish_but_not_delete(tenancy):
    http = tenancy["http"]
    granted = _granted(tenancy)
    agent_id = tenancy["agents"]["shared"]["id"]

    put = _as(http, granted, "put", f"/api/v1/agents/{agent_id}", json={"description": "granted edit"})
    assert put.status_code == 200, put.get_data(as_text=True)

    publish = _as(http, granted, "post", f"/api/v1/agents/{agent_id}/publish", json={"published": False})
    assert publish.status_code == 200, publish.get_data(as_text=True)
    assert publish.get_json()["published"] is False

    # A grant is use, never the right to destroy another tenant's agent.
    delete = _as(http, granted, "delete", f"/api/v1/agents/{agent_id}")
    assert delete.status_code == 403, delete.get_data(as_text=True)
    assert DefinitionStore.get_by_id(int(agent_id)) is not None


# --------------------------------------------------------------------------
# legacy /agents/<id>/access endpoints stay owner/admin-only
# --------------------------------------------------------------------------

def test_legacy_access_endpoints_are_owner_or_admin_only(tenancy):
    http = tenancy["http"]
    agent_id = tenancy["agents"]["shared"]["id"]

    denied = _as(http, _granted(tenancy), "get", f"/api/v1/agents/{agent_id}/access")
    assert denied.status_code == 403, denied.get_data(as_text=True)
    denied_grant = _as(
        http, _granted(tenancy), "post", f"/api/v1/agents/{agent_id}/access", json={"role_id": OTHER_ROLE}
    )
    assert denied_grant.status_code == 403, denied_grant.get_data(as_text=True)

    owner = _as(http, _owner(tenancy), "get", f"/api/v1/agents/{agent_id}/access")
    assert owner.status_code == 200, owner.get_data(as_text=True)
    assert [entry["role_id"] for entry in owner.get_json()["access"]] == [ANALYST_ROLE]

    admin = _as(http, _admin_key(http), "get", f"/api/v1/agents/{agent_id}/access")
    assert admin.status_code == 200, admin.get_data(as_text=True)


# --------------------------------------------------------------------------
# studio resources / models / mcp tools
# --------------------------------------------------------------------------

def test_studio_resources_exclude_other_tenants_collections(tenancy):
    http = tenancy["http"]
    owner = _as(http, _owner(tenancy), "get", "/api/v1/studio/resources")
    assert owner.status_code == 200, owner.get_data(as_text=True)
    body = owner.get_json()

    server_ids = {s["id"] for s in body["mcp_servers"]}
    assert tenancy["servers"]["owner"].id in server_ids
    assert tenancy["servers"]["other"].id not in server_ids

    skill_names = {s.get("name") for s in body["skills"]}
    assert "owner-skill" in skill_names
    assert "other-skill" not in skill_names

    client_ids = {c["id"] for c in body["model_clients"]}
    assert str(tenancy["connections"]["owner"].id) in client_ids
    assert str(tenancy["connections"]["other"].id) not in client_ids

    admin = _as(http, _admin_key(http), "get", "/api/v1/studio/resources")
    admin_body = admin.get_json()
    assert tenancy["servers"]["other"].id in {s["id"] for s in admin_body["mcp_servers"]}
    assert "other-skill" in {s.get("name") for s in admin_body["skills"]}
    assert str(tenancy["connections"]["other"].id) in {c["id"] for c in admin_body["model_clients"]}


def test_models_exclude_another_tenants_connection(tenancy):
    http = tenancy["http"]
    owner = _as(http, _owner(tenancy), "get", "/api/v1/models")
    assert owner.status_code == 200, owner.get_data(as_text=True)
    ids = {m["id"] for m in owner.get_json()["models"]}
    assert str(tenancy["connections"]["owner"].id) in ids
    assert str(tenancy["connections"]["other"].id) not in ids


def test_mcp_tools_are_404_for_a_non_accessible_server(tenancy):
    http = tenancy["http"]
    other = _other(tenancy)
    owner_server = tenancy["servers"]["owner"].id
    other_server = tenancy["servers"]["other"].id

    denied = _as(http, other, "get", f"/api/v1/studio/mcp-servers/{owner_server}/tools")
    assert denied.status_code == 404, denied.get_data(as_text=True)
    missing = _as(http, other, "get", "/api/v1/studio/mcp-servers/999999/tools")
    assert missing.status_code == 404, missing.get_data(as_text=True)
    # The caller's own server is not a 404 on access grounds.
    assert _as(http, other, "get", f"/api/v1/studio/mcp-servers/{other_server}/tools").status_code != 404


# --------------------------------------------------------------------------
# skills reads / mutations
# --------------------------------------------------------------------------

def test_skill_lists_and_reads_are_scoped(tenancy):
    http = tenancy["http"]
    owner = _as(http, _owner(tenancy), "get", "/api/v1/skills")
    assert owner.status_code == 200, owner.get_data(as_text=True)
    names = {s.get("name") for s in owner.get_json()["skills"]}
    assert "owner-skill" in names
    assert "other-skill" not in names

    assert _as(http, _owner(tenancy), "get", "/api/v1/skills/owner-skill").status_code == 200
    assert _as(http, _owner(tenancy), "get", "/api/v1/skills/other-skill").status_code == 404
    assert _as(http, _admin_key(http), "get", "/api/v1/skills/other-skill").status_code == 200


def test_skill_delete_of_another_tenants_package_is_403(tenancy):
    http = tenancy["http"]
    denied = _as(http, _owner(tenancy), "delete", "/api/v1/skills/other-skill")
    assert denied.status_code == 403, denied.get_data(as_text=True)
    assert _as(http, _owner(tenancy), "get", "/api/v1/skills/other-skill").status_code == 404
    assert _as(http, _admin_key(http), "get", "/api/v1/skills/other-skill").status_code == 200


# --------------------------------------------------------------------------
# conversation content is owner-or-admin only
# --------------------------------------------------------------------------

def test_can_access_conversation_is_owner_or_admin(tenancy, monkeypatch):
    from src.agent_platform.api.api_helpers import can_access_conversation

    conv = tenancy["conversation"]
    assert can_access_conversation(conv, OWNER_UID) is True
    assert can_access_conversation(conv, ADMIN_UID) is True
    # User 4 holds the observability module and still may not read the content.
    assert can_access_conversation(conv, OTHER_UID) is False
    assert can_access_conversation(conv, GRANTED_UID) is False
    assert can_access_conversation(None, OWNER_UID) is False
    # No identity at all denies, even for a row that would otherwise be owned.
    monkeypatch.setattr(
        "src.agent_platform.api.api_helpers.current_user_id", lambda: None
    )
    assert can_access_conversation(conv) is False


def test_observability_user_cannot_read_a_foreign_conversation(tenancy):
    http = tenancy["http"]
    conv = tenancy["conversation"]
    conversation_id = conv.get("public_id") or conv["id"]

    owner_read = _as(http, _owner(tenancy), "get", f"/api/v1/conversations/{conversation_id}")
    assert owner_read.status_code == 200, owner_read.get_data(as_text=True)

    foreign = _as(http, _other(tenancy), "get", f"/api/v1/conversations/{conversation_id}")
    assert foreign.status_code == 403, foreign.get_data(as_text=True)

    messages = _as(http, _other(tenancy), "get", f"/api/v1/conversations/{conversation_id}/messages")
    assert messages.status_code == 403, messages.get_data(as_text=True)
