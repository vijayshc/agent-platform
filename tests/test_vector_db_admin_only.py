"""Vector DB is administrator-only — real decorators, real RBAC, isolated temp DB.

The point this file proves is the one the module grant cannot prove on its own:
a non-admin who *does* hold ``module:vector_db`` at WRITE level is still refused
every vector/embedding surface, because ``admin_required()`` composes with (and
strictly outranks) ``module_required()``.

Nothing here mocks authorization: the shipped ``vector_db_bp`` and
``admin_api_bp`` blueprints are mounted on a minimal Flask app, the real
``UserManager`` creates the roles/users, and the real decorators decide.  The
only thing swapped is the connection target — a throwaway SQLite file — exactly
the isolation pattern ``tests/conftest.py`` already uses, so the tracked
``text2sql.db`` is never touched.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from flask import Flask
from sqlalchemy.orm import scoped_session, sessionmaker

REPO_ROOT = Path(__file__).resolve().parents[1]

#: (method, path, json body) for every JSON surface that reads/writes vectors.
API_CASES = (
    ("GET", "/admin/api/vector-db/collections", None),
    ("GET", "/admin/api/vector-db/collections/__no_such_collection__/fields", None),
    ("POST", "/admin/api/vector-db/collections/__no_such_collection__/search", {"query": "needle"}),
    ("GET", "/admin/api/embeddings/migrate", None),
    ("POST", "/admin/api/embeddings/migrate", {}),
)

PAGE_PATH = "/admin/vector-db"


def _login(client, user_id: int) -> None:
    with client.session_transaction() as session:
        session["user_id"] = user_id


def _request(client, method: str, path: str, body=None):
    if method == "POST":
        return client.post(path, json=body if body is not None else {})
    return client.get(path)


@pytest.fixture()
def rbac(tmp_path, monkeypatch):
    """Real UserManager backed by a temp SQLite file (ORM + raw connection)."""
    db_path = tmp_path / "rbac.db"

    from src.utils.database import create_db_engine
    from src.models.user import Base

    engine = create_db_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)

    session_factory = scoped_session(sessionmaker(bind=engine))
    monkeypatch.setattr("src.utils.database._Session", session_factory)

    def _connect():
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        return conn

    # resource_access.is_admin() reads roles/user_roles through this raw path.
    monkeypatch.setattr("src.agent_platform.db.get_db_connection", _connect)

    from src.utils.user_manager import UserManager

    um = UserManager()
    admin_id = um.create_user("vec-admin", "vec-admin@example.com", "Pw123456!")
    admin_role_id = um.create_role("admin", "built-in administrator")
    um.add_user_to_role(admin_id, admin_role_id)

    operator_id = um.create_user("vec-operator", "vec-operator@example.com", "Pw123456!")
    operator_role_id = um.create_role("vector-operator", "explicit vector_db grant")
    um.update_role_modules(operator_role_id, {"vector_db": "write"})
    um.add_user_to_role(operator_id, operator_role_id)

    # The grant must be real, otherwise the 403 below proves nothing.
    assert um.has_any_module_access(operator_id, ("vector_db",), min_level="write")
    assert um.has_role(admin_id, "admin")

    yield {"admin_id": admin_id, "operator_id": operator_id}

    session_factory.remove()
    engine.dispose()


@pytest.fixture()
def app(rbac, tmp_path, monkeypatch):
    """Minimal app mounting the shipped blueprints + the shipped login/index."""
    from src.routes.admin_api_routes import admin_api_bp
    from src.routes.auth_routes import auth_bp
    from src.routes.security_routes import generate_csrf_token
    from src.routes.vector_db_routes import vector_db_bp
    from src.utils.feedback_manager import FeedbackManager
    import src.routes.admin_api_routes as admin_api_routes

    # Keep the embedding migration on a throwaway file: the shared
    # text2sql.db must never be mutated by tests.
    monkeypatch.setattr(
        admin_api_routes,
        "feedback_manager",
        FeedbackManager(connection_string=f"sqlite:///{tmp_path / 'feedback.db'}"),
    )

    flask_app = Flask(
        __name__,
        template_folder=str(REPO_ROOT / "templates"),
        static_folder=str(REPO_ROOT / "static"),
    )
    flask_app.secret_key = "test"
    flask_app.register_blueprint(auth_bp)
    flask_app.register_blueprint(vector_db_bp)
    flask_app.register_blueprint(admin_api_bp)

    @flask_app.context_processor
    def _csrf():
        return {"csrf_token": generate_csrf_token}

    @flask_app.route("/")
    def index():  # redirect target for the browser-navigation denial path
        return "index"

    return flask_app


@pytest.fixture()
def client(app):
    return app.test_client()


def test_route_markers_keep_module_gate_in_audit_trail(app):
    """``admin_required`` and ``module_required`` markers coexist on every route."""
    from src.auth.decorators import (
        ADMIN_REQUIRED_ATTR,
        REQUIRED_MODULES_ATTR,
        get_route_requirements,
    )

    seen = 0
    for rule in app.url_map.iter_rules():
        path = str(rule)
        if "vector-db" not in path and "embeddings" not in path:
            continue
        fn = app.view_functions[rule.endpoint]
        assert getattr(fn, ADMIN_REQUIRED_ATTR, False) is True, path
        assert getattr(fn, REQUIRED_MODULES_ATTR, None) == ("vector_db",), path
        assert get_route_requirements(fn)[0] == ("vector_db",), path
        seen += 1
    assert seen == 5


@pytest.mark.parametrize("method,path,body", API_CASES)
def test_unauthenticated_api_is_401(client, method, path, body):
    resp = _request(client, method, path, body)
    assert resp.status_code == 401, (path, resp.status_code, resp.get_data(as_text=True))
    assert resp.is_json


def test_unauthenticated_page_redirects_to_login(client):
    resp = client.get(PAGE_PATH)
    assert resp.status_code == 302
    assert "/login" in resp.headers["Location"]


@pytest.mark.parametrize("method,path,body", API_CASES)
def test_non_admin_with_module_write_grant_is_403(client, rbac, method, path, body):
    _login(client, rbac["operator_id"])
    resp = _request(client, method, path, body)
    assert resp.status_code == 403, (path, resp.status_code, resp.get_data(as_text=True))


def test_non_admin_with_module_write_grant_page_redirects_to_index(client, rbac):
    _login(client, rbac["operator_id"])
    resp = client.get(PAGE_PATH)
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/")
    assert "/login" not in resp.headers["Location"]


@pytest.mark.parametrize("method,path,body", API_CASES)
def test_admin_is_not_forbidden_by_admin_gate(client, rbac, method, path, body):
    _login(client, rbac["admin_id"])
    resp = _request(client, method, path, body)
    assert resp.status_code != 403, (path, resp.status_code, resp.get_data(as_text=True))


def test_admin_can_load_vector_db_page(client, rbac):
    _login(client, rbac["admin_id"])
    resp = client.get(PAGE_PATH)
    assert resp.status_code == 200
    assert b"agent-app-root" in resp.data
