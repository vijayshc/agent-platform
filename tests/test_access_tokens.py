"""JWT access tokens: issued at login, accepted as Bearer credentials."""

from __future__ import annotations


def _app():
    from flask import Flask

    app = Flask(__name__)
    app.secret_key = "test-secret"
    return app


def test_access_token_roundtrip():
    from src.agent_platform.api.tokens import issue_access_token, read_access_token

    app = _app()
    with app.app_context():
        token = issue_access_token(42)
        assert read_access_token(token) == 42


def test_garbage_token_rejected():
    from src.agent_platform.api.tokens import read_access_token

    app = _app()
    with app.app_context():
        assert read_access_token("not.a.jwt") is None
        assert read_access_token("") is None


def test_token_signed_with_other_secret_rejected():
    from src.agent_platform.api.tokens import issue_access_token, read_access_token

    other = _app()
    other.secret_key = "different-secret"
    with other.app_context():
        token = issue_access_token(7)
    app = _app()
    with app.app_context():
        assert read_access_token(token) is None


def test_bearer_token_authenticates_blueprint(temp_db):
    from src.agent_platform.api.blueprint import create_blueprint
    from src.agent_platform.api.tokens import issue_access_token

    app = _app()
    app.register_blueprint(create_blueprint())
    with app.app_context():
        token = issue_access_token(1)

    client = app.test_client()
    # No session cookie and no API key — the Bearer token alone must authorize.
    resp = client.get("/api/v1/agents", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200


def test_token_reaches_agent_route_for_role_check(temp_db, monkeypatch):
    """The global RBAC hook must defer agent-execution routes to the route's
    user -> role -> agent check (here forced to deny)."""
    from flask import Flask

    from src.agent_platform.api import run_routes
    from src.agent_platform.api.blueprint import create_blueprint
    from src.agent_platform.api.tokens import issue_access_token
    from src.agent_platform.catalog.store import DefinitionStore
    from src.routes.security_routes import security_bp

    DefinitionStore.upsert(
        slug="token-agent",
        name="Token Agent",
        kind="agent",
        config={"kind": "agent", "instructions": "x"},
        published=True,
        created_by=99,
    )
    monkeypatch.setattr(run_routes, "user_can_access_definition", lambda row, uid=None: False)

    app = Flask(__name__)
    app.secret_key = "test-secret"
    app.register_blueprint(security_bp)
    app.register_blueprint(create_blueprint())
    with app.app_context():
        token = issue_access_token(1)

    client = app.test_client()
    resp = client.post(
        "/api/v1/agents/token-agent/invoke",
        json={"input": "hi"},
        headers={"Authorization": f"Bearer {token}"},
    )
    # 403 (not 401) proves the request passed the session RBAC hook and was
    # rejected by the per-agent role check.
    assert resp.status_code == 403
    assert "access" in (resp.get_json() or {}).get("message", "").lower()
