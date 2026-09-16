"""Regression: Agent Platform API keys authenticate without a browser session.

The global ``check_access_control`` before-request hook is session/RBAC based.
Agent Platform API keys are scope-based and enforced by ``api_auth_required``
on the /api/v1 blueprint, so a valid key must not be rejected as anonymous.
"""

from __future__ import annotations


def _app():
    from flask import Flask

    from src.agent_platform.api.blueprint import create_blueprint
    from src.routes.security_routes import security_bp

    app = Flask(__name__)
    app.secret_key = "test"
    app.register_blueprint(security_bp)
    app.register_blueprint(create_blueprint())
    return app


def test_api_key_only_request_bypasses_session_rbac(temp_db):
    from src.agent_platform.execution.api_keys import ApiKeyStore

    ApiKeyStore.ensure_tables()
    key = ApiKeyStore.create(user_id=1, name="middleware-test", scopes=["agents:read"])

    app = _app()
    anonymous = app.test_client()
    assert anonymous.get("/api/v1/agents").status_code == 401

    api_key_only = app.test_client()
    api_key_only.environ_base["HTTP_X_API_KEY"] = str(key["key"])
    assert api_key_only.get("/api/v1/agents").status_code == 200


def test_invalid_api_key_is_not_trusted(temp_db):
    from src.agent_platform.execution.api_keys import ApiKeyStore

    ApiKeyStore.ensure_tables()

    app = _app()
    bogus = app.test_client()
    bogus.environ_base["HTTP_X_API_KEY"] = "apk_not-a-real-key"
    assert bogus.get("/api/v1/agents").status_code == 401
