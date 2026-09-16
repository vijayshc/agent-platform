"""Production catalog must not list or publish ScriptedChatClient.

ScriptedChatClient stays registered for E2E (AGENT_PLATFORM_E2E=1) and unit
plumbing. Production resources, create-as-published, and publish reject it.
"""

from __future__ import annotations

from flask import Flask

from src.agent_platform.api.blueprint import create_blueprint
from src.agent_platform.catalog.store import DefinitionStore
from src.agent_platform.catalog.validate import validate_definition
from src.agent_platform.execution.api_keys import ApiKeyStore
from src.agent_platform.bootstrap import unpublish_scripted_definitions


SCRIPTED_CFG = {
    "kind": "agent",
    "instructions": "Reply with pong.",
    "model": {"client": "scripted", "responses": ["pong"]},
}


def _studio_client(temp_db):
    DefinitionStore.ensure_tables()
    ApiKeyStore.ensure_tables()
    key = ApiKeyStore.create(user_id=1, name="studio", scopes=["agents:read", "agents:write"])
    app = Flask(__name__)
    app.secret_key = "test"
    app.register_blueprint(create_blueprint())
    return app.test_client(), {"X-API-Key": key["key"]}


def test_validate_definition_rejects_scripted_unless_e2e(monkeypatch):
    monkeypatch.delenv("AGENT_PLATFORM_E2E", raising=False)
    report = validate_definition({"kind": "agent", "name": "Nope", "config": SCRIPTED_CFG})
    assert report["ok"] is False
    assert any(e.get("code") == "scripted_client_not_allowed" for e in report["errors"])

    monkeypatch.setenv("AGENT_PLATFORM_E2E", "1")
    report = validate_definition({"kind": "agent", "name": "Ok", "config": SCRIPTED_CFG})
    assert report["ok"] is True


def test_studio_resources_omit_scripted_unless_e2e(temp_db, monkeypatch):
    http, headers = _studio_client(temp_db)
    monkeypatch.delenv("AGENT_PLATFORM_E2E", raising=False)
    body = http.get("/api/v1/studio/resources", headers=headers).get_json()
    ids = {c["id"] for c in body.get("model_clients") or []}
    assert "scripted" not in ids
    assert "ollama" not in ids
    assert "foundry" not in ids
    assert "anthropic" not in ids
    assert "bedrock" not in ids
    assert "default" not in ids

    monkeypatch.setenv("AGENT_PLATFORM_E2E", "1")
    body = http.get("/api/v1/studio/resources", headers=headers).get_json()
    ids = {c["id"] for c in body.get("model_clients") or []}
    assert "scripted" in ids
    assert "ollama" not in ids


def test_create_as_published_and_publish_reject_scripted_unless_e2e(temp_db, monkeypatch):
    http, headers = _studio_client(temp_db)
    monkeypatch.delenv("AGENT_PLATFORM_E2E", raising=False)

    created = http.post(
        "/api/v1/agents",
        json={"name": "Scripted Nope", "published": True, "config": SCRIPTED_CFG},
        headers=headers,
    )
    assert created.status_code == 400, created.get_data(as_text=True)
    body = created.get_json()
    assert any(e.get("code") == "scripted_client_not_allowed" for e in body.get("errors") or [])

    draft = http.post(
        "/api/v1/agents",
        json={"name": "Scripted Draft", "slug": "scripted-draft", "published": False, "config": SCRIPTED_CFG},
        headers=headers,
    )
    assert draft.status_code == 201, draft.get_data(as_text=True)
    assert draft.get_json()["published"] is False

    pub = http.post(
        f"/api/v1/agents/{draft.get_json()['slug']}/publish",
        json={"published": True},
        headers=headers,
    )
    assert pub.status_code == 400, pub.get_data(as_text=True)
    pub_body = pub.get_json()
    assert any(e.get("code") == "scripted_client_not_allowed" for e in pub_body.get("errors") or [])

    monkeypatch.setenv("AGENT_PLATFORM_E2E", "1")
    allowed = http.post(
        "/api/v1/agents",
        json={"name": "Scripted E2E", "slug": "scripted-e2e", "published": True, "config": SCRIPTED_CFG},
        headers=headers,
    )
    assert allowed.status_code == 201, allowed.get_data(as_text=True)
    assert allowed.get_json()["published"] is True

    draft2 = http.post(
        "/api/v1/agents",
        json={"name": "Scripted E2E Draft", "slug": "scripted-e2e-draft", "config": SCRIPTED_CFG},
        headers=headers,
    )
    assert draft2.status_code == 201
    pub2 = http.post(
        f"/api/v1/agents/{draft2.get_json()['slug']}/publish",
        json={"published": True},
        headers=headers,
    )
    assert pub2.status_code == 200, pub2.get_data(as_text=True)
    assert pub2.get_json()["published"] is True


def test_unpublish_leftover_scripted_definitions(temp_db, monkeypatch):
    monkeypatch.delenv("AGENT_PLATFORM_E2E", raising=False)
    DefinitionStore.ensure_tables()
    DefinitionStore.upsert(
        slug="leftover-scripted",
        name="Leftover Scripted",
        kind="agent",
        config=SCRIPTED_CFG,
        published=True,
    )
    DefinitionStore.upsert(
        slug="real-agent",
        name="Real Agent",
        kind="agent",
        config={"kind": "agent", "instructions": "Go.", "model": {"client": "default"}},
        published=True,
    )
    n = unpublish_scripted_definitions()
    assert n >= 1
    leftover = DefinitionStore.get_by_slug("leftover-scripted")
    real = DefinitionStore.get_by_slug("real-agent")
    assert leftover["published"] is False
    assert real["published"] is True
