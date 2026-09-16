from __future__ import annotations

from flask import Flask


def test_api_list_and_create_agents(app_client):
    res = app_client.get("/api/v1/agents")
    assert res.status_code == 200

    create_res = app_client.post(
        "/api/v1/agents",
        json={
            "name": "API Agent",
            "kind": "agent",
            "config": {
                "kind": "agent",
                "instructions": "Be helpful.",
            },
        },
    )
    assert create_res.status_code == 201
    body = create_res.get_json()
    assert body["name"] == "API Agent"
    assert body["slug"]


def test_api_create_and_get_run(app_client):
    create_agent = app_client.post(
        "/api/v1/agents",
        json={
            "name": "Runner Agent",
            "kind": "agent",
            "config": {
                "kind": "agent",
                "instructions": "Run things.",
            },
        },
    )
    assert create_agent.status_code == 201
    slug = create_agent.get_json()["slug"]

    run_res = app_client.post(
        "/api/v1/runs",
        json={
            "task": "do something",
            "agent_id": slug,
        },
    )
    assert run_res.status_code == 201
    run_body = run_res.get_json()
    assert run_body["id"]
    assert run_body["stream_url"]

    get_run = app_client.get(f"/api/v1/runs/{run_body['public_id']}")
    assert get_run.status_code == 200
    assert get_run.get_json()["id"] == run_body["id"]


def test_agent_page_routes(temp_db):
    import os
    from src.utils.template_filters import register_filters
    from src.routes.agent_routes import agent_bp
    from src.utils.user_manager import UserManager

    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    templates_dir = os.path.join(base_dir, "templates")
    static_dir = os.path.join(base_dir, "static")

    app = Flask(__name__, template_folder=templates_dir, static_folder=static_dir)
    app.secret_key = "test"
    register_filters(app)
    app.register_blueprint(agent_bp)

    @app.context_processor
    def inject_test_ctx():
        return {
            "has_endpoint_access": lambda ep: True,
            "has_any_endpoint_access": lambda eps: True,
            "session": {"user_id": 1},
            "user_manager": UserManager(),
            "browser_llm_proxy_enabled_default": False,
            "browser_llm_proxy_url": "",
            "csrf_token": lambda: "test-csrf-token",
        }

    client = app.test_client()
    with client.session_transaction() as sess:
        sess["user_id"] = 1

    res_root = client.get("/")
    assert res_root.status_code == 200
    assert b"agent-app-root" in res_root.data

    res_agent = client.get("/agent")
    assert res_agent.status_code == 200
    assert b"agent-app-root" in res_agent.data


def test_api_agent_access(app_client):
    # 1. Create an agent
    create_res = app_client.post(
        "/api/v1/agents",
        json={
            "name": "Access Controlled Agent",
            "kind": "agent",
            "config": {"kind": "agent", "instructions": "Secure agent."},
        },
    )
    assert create_res.status_code == 201
    slug = create_res.get_json()["slug"]

    # 2. Get initial access (empty)
    get_res = app_client.get(f"/api/v1/agents/{slug}/access")
    assert get_res.status_code == 200
    assert get_res.get_json() == {"access": []}

    # 3. Grant access to role 1
    grant_res = app_client.post(
        f"/api/v1/agents/{slug}/access",
        json={"role_ids": [1]},
    )
    assert grant_res.status_code == 200
    acc = grant_res.get_json()["access"]
    assert len(acc) == 1
    assert acc[0]["role_id"] == 1

    # 4. Verify access is present in list_all (include_drafts=1)
    list_res = app_client.get("/api/v1/agents?include_drafts=1")
    assert list_res.status_code == 200
    matching = [a for a in list_res.get_json()["agents"] if a["slug"] == slug]
    assert len(matching) == 1
    assert len(matching[0].get("access", [])) == 1

    # 5. Revoke access via DELETE /role/<role_id>
    revoke_res = app_client.delete(f"/api/v1/agents/{slug}/access/role/1")
    assert revoke_res.status_code == 200
    assert revoke_res.get_json() == {"access": []}

    # 6. Grant again using role_id single field
    grant_res2 = app_client.post(
        f"/api/v1/agents/{slug}/access",
        json={"role_id": 2},
    )
    assert grant_res2.status_code == 200
    assert len(grant_res2.get_json()["access"]) == 1
    assert grant_res2.get_json()["access"][0]["role_id"] == 2

    # 7. Revoke via payload DELETE /access
    revoke_res2 = app_client.delete(
        f"/api/v1/agents/{slug}/access",
        json={"role_id": 2},
    )
    assert revoke_res2.status_code == 200
    assert revoke_res2.get_json() == {"access": []}

    # 8. Delete agent
    del_res = app_client.delete(f"/api/v1/agents/{slug}")
    assert del_res.status_code == 200


def test_api_studio_roles(app_client):
    res = app_client.get("/api/v1/studio/roles")
    assert res.status_code == 200
    body = res.get_json()
    assert "roles" in body
    assert isinstance(body["roles"], list)


def _seed_published(slug: str, published: bool = True) -> None:
    from src.agent_platform.catalog.store import DefinitionStore

    DefinitionStore.upsert(
        slug=slug,
        name=slug,
        kind="agent",
        config={"kind": "agent", "instructions": "Secure agent."},
        published=published,
        created_by=99,
    )


def test_invoke_rejects_user_without_agent_access(app_client, monkeypatch):
    from src.agent_platform.api import run_routes

    _seed_published("locked-agent")
    monkeypatch.setattr(run_routes, "user_can_access_definition", lambda row, uid=None: False)
    res = app_client.post("/api/v1/agents/locked-agent/invoke", json={"input": "hi"})
    assert res.status_code == 403


def test_invoke_requires_published_agent(app_client):
    _seed_published("draft-agent", published=False)
    res = app_client.post("/api/v1/agents/draft-agent/invoke", json={"input": "hi"})
    assert res.status_code == 404


def test_run_rejects_user_without_agent_access(app_client, monkeypatch):
    from src.agent_platform.api import run_routes

    _seed_published("locked-run-agent")
    monkeypatch.setattr(run_routes, "user_can_access_definition", lambda row, uid=None: False)
    res = app_client.post("/api/v1/runs", json={"task": "do it", "agent_id": "locked-run-agent"})
    assert res.status_code == 403

