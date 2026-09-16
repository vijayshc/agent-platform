"""Chat tenancy: agents, conversations and workspaces are per-tenant and fail closed.

Real HTTP through the shipped ``/api/v1`` blueprint on Flask's test client, a
real SQLite file (the ``temp_db`` fixture) and real API-key auth.  The temp DB
is the ONLY database the platform touches: the SQLAlchemy session that
``UserManager`` uses is pointed at it too, so role grants decide exactly as they
do in production instead of falling back to the real ``text2sql.db``.

Covered requirement: a chat user may drive only agents they own or that a role
they hold was granted, and may read only their own conversations, runs and
workspace files.
"""

from __future__ import annotations

import json

import pytest

from src.agent_platform.catalog.store import DefinitionStore
from src.agent_platform.conversations.store import ConversationStore
from src.agent_platform.execution.api_keys import ApiKeyStore
from src.agent_platform.execution.run_store import RunStore
from src.agent_platform.plugins.models.scripted import reset_shared_clients
from src.auth import resource_access

SCOPES = ["agents:read", "agents:write", "runs:read", "runs:write"]

#: A model that cannot resolve, so /runs stops at model resolution with a 400
#: AFTER the access gate: 400 proves "may run" without an LLM call.
UNUSABLE_MODEL = {"client": "tenancy-nonexistent-connection", "name": None}

ADMIN_UID, OWNER_UID, GRANTED_UID, OTHER_UID = 1, 2, 3, 4
ADMIN_ROLE, ANALYST_ROLE, OTHER_ROLE = 1, 2, 3


def _agent(slug: str, created_by: int) -> dict:
    """A published scripted agent owned by ``created_by`` (real DB row)."""
    return DefinitionStore.upsert(
        slug=slug,
        name=slug,
        kind="agent",
        config={
            "kind": "agent",
            "instructions": "Reply with pong.",
            "model": {"client": "scripted", "reuse_id": slug, "responses": ["pong"]},
        },
        published=True,
        created_by=created_by,
    )


def _seed_identities(db_path):
    """Real users + roles in the temp DB (raw SQL store and ORM both read it)."""
    from sqlalchemy.orm import scoped_session, sessionmaker

    from src.models.user import Base, Role, User
    from src.utils.database import create_db_engine

    engine = create_db_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
    factory = scoped_session(sessionmaker(bind=engine))
    session = factory()
    session.add_all([
        Role(id=ADMIN_ROLE, name="admin", description="Administrator"),
        Role(id=ANALYST_ROLE, name="analyst", description="Analyst"),
        Role(id=OTHER_ROLE, name="other", description="Other"),
    ])
    session.flush()
    roles = {
        ADMIN_ROLE: session.query(Role).filter(Role.id == ADMIN_ROLE).first(),
        ANALYST_ROLE: session.query(Role).filter(Role.id == ANALYST_ROLE).first(),
        OTHER_ROLE: session.query(Role).filter(Role.id == OTHER_ROLE).first(),
    }
    for uid, name, role_id in (
        (ADMIN_UID, "admin", ADMIN_ROLE),
        (OWNER_UID, "owner", ANALYST_ROLE),
        (GRANTED_UID, "granted", ANALYST_ROLE),
        (OTHER_UID, "other", OTHER_ROLE),
    ):
        user = User(
            id=uid,
            username=name,
            email=f"{name}@tenancy.test",
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
    """Three identities with roles, a pointed-at-temp-DB ORM, and their API keys.

    ``app_client`` ships an API key for user 1; distinct keys below act as the
    owner, the role-granted user and an unrelated tenant.
    """
    engine, factory = _seed_identities(temp_db)
    monkeypatch.setattr("src.utils.database._Session", factory)
    resource_access._reset_schema_guard()

    reset_shared_clients()
    DefinitionStore.ensure_tables()
    ConversationStore.ensure_tables()
    RunStore.ensure_tables()

    keys = {
        uid: ApiKeyStore.create(user_id=uid, name=f"tenancy-{uid}", scopes=list(SCOPES))
        for uid in (OWNER_UID, GRANTED_UID, OTHER_UID)
    }

    owner_agent = _agent("owner-only", OWNER_UID)
    shared_agent = _agent("shared-granted", OWNER_UID)
    other_agent = _agent("other-agent", OTHER_UID)
    # The role grant that makes ``shared-agent`` usable by the analyst role.
    resource_access.grant_access(
        "agent", int(shared_agent["id"]), ANALYST_ROLE, granted_by=OWNER_UID
    )

    yield {
        "http": app_client,
        "keys": keys,
        "owner_only": owner_agent,
        "shared": shared_agent,
        "other": other_agent,
    }

    try:
        factory.remove()
        engine.dispose()
    except Exception:
        pass


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


def _sse_events(body: str) -> list[dict]:
    out: list[dict] = []
    for block in body.split("\n\n"):
        for line in block.split("\n"):
            if not line.startswith("data:"):
                continue
            raw = line[len("data:"):].strip()
            if raw:
                out.append(json.loads(raw))
    return out


def _thread_run_owner(thread_id: str):
    """The owner user id of the run the AG-UI thread is bound to."""
    from src.agent_platform.agui.thread_store import conversation_for_thread

    conv = conversation_for_thread(thread_id)
    assert conv is not None, f"thread {thread_id} was not bound to a conversation"
    rows = RunStore.list_for_conversation(int(conv["id"]))
    assert rows, f"no run recorded for thread {thread_id}"
    return rows[0].get("user_id")


def _run_gate(http, key: dict, slug: str):
    return _as(
        http,
        key,
        "post",
        "/api/v1/runs",
        json={"agent_id": slug, "input": "tenancy access probe", "model": UNUSABLE_MODEL},
    )


def _create_conversation(http, key: dict, slug: str, title: str = "tenancy chat"):
    return _as(http, key, "post", "/api/v1/conversations", json={"title": title, "agent_id": slug})


# --------------------------------------------------------------------------
# agent access: the run gate (POST /runs)
# --------------------------------------------------------------------------

def test_run_gate_denies_unrelated_agent(tenancy):
    resp = _run_gate(tenancy["http"], _other(tenancy), "owner-only")
    assert resp.status_code == 403, resp.get_data(as_text=True)


def test_run_gate_allows_owner(tenancy):
    resp = _run_gate(tenancy["http"], _owner(tenancy), "owner-only")
    body = resp.get_json() if resp.is_json else {}
    assert resp.status_code == 400 and body.get("error") == "model_unavailable", resp.get_data(as_text=True)


def test_run_gate_allows_role_granted_user(tenancy):
    resp = _run_gate(tenancy["http"], _granted(tenancy), "shared-granted")
    body = resp.get_json() if resp.is_json else {}
    assert resp.status_code == 400 and body.get("error") == "model_unavailable", resp.get_data(as_text=True)


def test_run_gate_denies_granted_role_user_without_the_grant(tenancy):
    resp = _run_gate(tenancy["http"], _granted(tenancy), "owner-only")
    assert resp.status_code == 403, resp.get_data(as_text=True)


def test_run_gate_denies_unrelated_user_on_granted_agent(tenancy):
    resp = _run_gate(tenancy["http"], _other(tenancy), "shared-granted")
    assert resp.status_code == 403, resp.get_data(as_text=True)


# --------------------------------------------------------------------------
# agent access: AG-UI /agui/input
# --------------------------------------------------------------------------

def test_agui_input_denied_for_unrelated_agent(tenancy):
    resp = _as(
        tenancy["http"],
        _other(tenancy),
        "post",
        "/api/v1/agui/input",
        json={"messages": [{"role": "user", "content": "hi"}], "agent_id": "owner-only", "thread_id": "th-deny"},
    )
    assert resp.status_code == 403, resp.get_data(as_text=True)


def test_agui_input_allows_owner(tenancy):
    thread_id = "th-owner"
    resp = _as(
        tenancy["http"],
        _owner(tenancy),
        "post",
        "/api/v1/agui/input",
        json={"messages": [{"role": "user", "content": "hi"}], "agent_id": "owner-only", "thread_id": thread_id},
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    types = [e["type"] for e in _sse_events(resp.get_data(as_text=True))]
    # Access is granted before the worker starts; the run itself then resolves a
    # model (no LLM connection exists in the isolated temp DB, which is a
    # separate module's concern, not tenancy).
    assert types[0] == "RUN_STARTED"
    assert _thread_run_owner(thread_id) == OWNER_UID


def test_agui_input_allows_role_granted_user(tenancy):
    thread_id = "th-granted"
    resp = _as(
        tenancy["http"],
        _granted(tenancy),
        "post",
        "/api/v1/agui/input",
        json={"messages": [{"role": "user", "content": "hi"}], "agent_id": "shared-granted", "thread_id": thread_id},
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    types = [e["type"] for e in _sse_events(resp.get_data(as_text=True))]
    assert types[0] == "RUN_STARTED"
    assert _thread_run_owner(thread_id) == GRANTED_UID


def test_agui_thread_cannot_be_hijacked_by_another_tenant(tenancy):
    http = tenancy["http"]
    owner_run = _as(
        http,
        _owner(tenancy),
        "post",
        "/api/v1/agui/input",
        json={"messages": [{"role": "user", "content": "hi"}], "agent_id": "owner-only", "thread_id": "th-private"},
    )
    assert owner_run.status_code == 200
    _sse_events(owner_run.get_data(as_text=True))

    # The other tenant may run their OWN agent, but must not drive the owner's
    # thread (which would inject a run into the owner's conversation).
    hijack = _as(
        http,
        _other(tenancy),
        "post",
        "/api/v1/agui/input",
        json={"messages": [{"role": "user", "content": "hi"}], "agent_id": "other-agent", "thread_id": "th-private"},
    )
    assert hijack.status_code == 403, hijack.get_data(as_text=True)


def test_agui_resume_cannot_target_another_tenants_run(tenancy):
    http = tenancy["http"]
    owner_conv = _create_conversation(http, _owner(tenancy), "shared-granted", "owner resume")
    conv_id = owner_conv.get_json().get("public_id") or owner_conv.get_json().get("id")
    conv_pk = ConversationStore.get(conv_id)["id"]

    run = RunStore.create(
        task="write the file",
        definition_id=int(tenancy["shared"]["id"]),
        user_id=OWNER_UID,
        conversation_id=int(conv_pk),
        agent_slug="shared-granted",
    )
    run_id = int(run["id"])
    RunStore.set_pending(run_id, {"action_requests": [{"name": "dangerous_write", "arguments": {}}]})
    # Precondition: the analyst's agent access makes the run *readable*; only
    # the conversation-ownership rule below may stop the resume.
    assert RunStore.can_view(RunStore.get(run_id), GRANTED_UID)

    resume_body = {
        "messages": [{"role": "user", "content": "hi"}],
        "agent_id": "shared-granted",
        "resume": [{"id": "int-1", "value": {"accepted": True}}],
        "forwarded_props": {"run_id": run_id},
    }
    # The analyst may use the same agent, but resuming the owner's run would
    # continue the owner's conversation under another tenant: forbidden.
    denied = _as(
        http,
        _granted(tenancy),
        "post",
        "/api/v1/agui/input",
        json={**resume_body, "thread_id": "th-granted-resume"},
    )
    assert denied.status_code == 403, denied.get_data(as_text=True)

    allowed = _as(
        http,
        _owner(tenancy),
        "post",
        "/api/v1/agui/input",
        json={**resume_body, "thread_id": "th-owner-resume"},
    )
    assert allowed.status_code == 200, allowed.get_data(as_text=True)


def test_agui_state_and_events_are_owner_scoped(tenancy):
    http = tenancy["http"]
    run = _as(
        http,
        _owner(tenancy),
        "post",
        "/api/v1/agui/input",
        json={
            "messages": [{"role": "user", "content": "hi"}],
            "agent_id": "owner-only",
            "thread_id": "th-state",
            "state": {"user": {"name": "Alice"}},
        },
    )
    assert run.status_code == 200
    _sse_events(run.get_data(as_text=True))

    own_state = _as(http, _owner(tenancy), "get", "/api/v1/agui/state?thread_id=th-state")
    assert own_state.status_code == 200
    assert own_state.get_json()["state"] == {"user": {"name": "Alice"}}

    other_state = _as(http, _other(tenancy), "get", "/api/v1/agui/state?thread_id=th-state")
    assert other_state.status_code == 403, other_state.get_data(as_text=True)

    other_events = _as(http, _other(tenancy), "get", "/api/v1/agui/events?thread_id=th-state")
    assert other_events.status_code == 403, other_events.get_data(as_text=True)


# --------------------------------------------------------------------------
# conversation ownership
# --------------------------------------------------------------------------

def test_conversation_create_is_denied_for_unrelated_agent(tenancy):
    denied = _create_conversation(tenancy["http"], _other(tenancy), "owner-only")
    assert denied.status_code == 403, denied.get_data(as_text=True)

    missing = _create_conversation(tenancy["http"], _other(tenancy), "no-such-agent")
    assert missing.status_code == 404, missing.get_data(as_text=True)


def test_conversation_list_and_messages_are_owner_scoped(tenancy):
    http = tenancy["http"]
    owned = _create_conversation(http, _owner(tenancy), "owner-only", "owner conv")
    assert owned.status_code == 201, owned.get_data(as_text=True)
    owned_id = owned.get_json().get("public_id") or owned.get_json().get("id")

    foreign = _create_conversation(http, _other(tenancy), "other-agent", "other conv")
    assert foreign.status_code == 201, foreign.get_data(as_text=True)
    foreign_id = foreign.get_json().get("public_id") or foreign.get_json().get("id")
    assert owned_id != foreign_id

    listed = _as(http, _owner(tenancy), "get", "/api/v1/conversations")
    assert listed.status_code == 200
    ids = {c.get("public_id") or c.get("id") for c in listed.get_json()["conversations"]}
    assert owned_id in ids
    assert foreign_id not in ids

    # Guessing another tenant's conversation id is forbidden on every read path.
    assert _as(http, _owner(tenancy), "get", f"/api/v1/conversations/{foreign_id}").status_code == 403
    assert _as(http, _owner(tenancy), "get", f"/api/v1/conversations/{foreign_id}/messages").status_code == 403
    assert _as(http, _owner(tenancy), "delete", f"/api/v1/conversations/{foreign_id}").status_code == 403

    # The caller's own conversation is readable.
    own = _as(http, _owner(tenancy), "get", f"/api/v1/conversations/{owned_id}")
    assert own.status_code == 200, own.get_data(as_text=True)


def test_conversation_message_denied_for_another_tenants_conversation(tenancy):
    http = tenancy["http"]
    foreign = _create_conversation(http, _other(tenancy), "other-agent", "other conv")
    foreign_id = foreign.get_json().get("public_id") or foreign.get_json().get("id")

    resp = _as(
        http,
        _owner(tenancy),
        "post",
        f"/api/v1/conversations/{foreign_id}/messages",
        json={"input": "sneak in", "agent_id": "owner-only", "stream": False},
    )
    assert resp.status_code == 403, resp.get_data(as_text=True)


# --------------------------------------------------------------------------
# run / conversation workspace files
# --------------------------------------------------------------------------

def test_workspace_files_are_owner_scoped(tenancy):
    http = tenancy["http"]
    foreign = _create_conversation(http, _other(tenancy), "other-agent", "other conv")
    foreign_id = foreign.get_json().get("public_id") or foreign.get_json().get("id")

    listed = _as(http, _owner(tenancy), "get", f"/api/v1/conversations/{foreign_id}/files")
    assert listed.status_code == 403, listed.get_data(as_text=True)

    download = _as(
        http,
        _owner(tenancy),
        "get",
        f"/api/v1/conversations/{foreign_id}/files/download?path=secret.txt",
    )
    assert download.status_code == 403, download.get_data(as_text=True)

    deleted = _as(
        http,
        _owner(tenancy),
        "delete",
        f"/api/v1/conversations/{foreign_id}/files?path=secret.txt",
    )
    assert deleted.status_code == 403, deleted.get_data(as_text=True)

    own = _create_conversation(http, _owner(tenancy), "owner-only", "owner conv")
    own_id = own.get_json().get("public_id") or own.get_json().get("id")
    own_files = _as(http, _owner(tenancy), "get", f"/api/v1/conversations/{own_id}/files")
    assert own_files.status_code == 200, own_files.get_data(as_text=True)


# --------------------------------------------------------------------------
# session identity leaks nothing beyond the caller
# --------------------------------------------------------------------------

def test_me_returns_only_the_caller_identity(tenancy):
    resp = _as(tenancy["http"], _granted(tenancy), "get", "/api/v1/me")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["user_id"] == GRANTED_UID
    assert body["username"] == "granted"
    assert "admin" not in (body.get("roles") or [])
    assert body.get("is_admin") is False
