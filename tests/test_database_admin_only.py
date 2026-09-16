"""Database console is admin-only: real routes, real decorators, real temp DB.

This registers the shipped ``admin_db_bp`` (with its real ``admin_required`` /
``module_required`` decorators) on a minimal Flask app whose every DB access is
redirected to an isolated SQLite file — never the shared ``text2sql.db``.

The security property under test is *"module granted is not enough"*: a role
that explicitly holds the ``module:database`` write permission must still be
denied unless it is the built-in ``admin`` role.
"""

from __future__ import annotations

import os
import sqlite3

import pytest
from flask import Blueprint, Flask

from src.auth.modules import MODULE_PERMISSION_NAMES

#: Roles/users: role 1 = admin (user 10), role 2 = db_operator holding the
#: ``module:database`` WRITE permission (user 11).  ``widgets`` gives the
#: console a real table to read and mutate.
SEED = """
INSERT INTO roles (id, name, description) VALUES
    (1, 'admin', 'Built-in administrator'),
    (2, 'db_operator', 'Database console operator');
INSERT INTO permissions (id, name, description) VALUES
    (1, 'module:database', 'Database module (write)');
INSERT INTO role_permissions (role_id, permission_id) VALUES (2, 1);
INSERT INTO users (id, username, email, password_hash, is_active) VALUES
    (10, 'admin', 'admin@example.com', 'x', 1),
    (11, 'dbop', 'dbop@example.com', 'x', 1);
INSERT INTO user_roles (user_id, role_id) VALUES (10, 1), (11, 2);
CREATE TABLE widgets (id INTEGER PRIMARY KEY, name TEXT NOT NULL);
INSERT INTO widgets (name) VALUES ('alpha'), ('beta');
"""


def _connect(db_path):
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def _login(client, user_id: int) -> None:
    with client.session_transaction() as session:
        session["user_id"] = user_id


@pytest.fixture()
def db_console_app(temp_db, monkeypatch):
    """Minimal app + real admin_db blueprint, all DB reads/writes on temp_db."""
    from src.models.user import Base
    from src.routes.admin_db_routes import admin_db_bp
    from src.routes.security_routes import generate_csrf_token
    from src.utils.database import create_db_engine

    uri = f"sqlite:///{temp_db}"
    # DatabaseManager / get_db_connection resolve DATABASE_URI at call time, so
    # repointing the module global keeps every console query off the shared DB.
    monkeypatch.setattr("src.utils.database.DATABASE_URI", uri)
    monkeypatch.setattr("src.routes.admin_db_routes.DATABASE_URI", uri)
    # UserManager caches its ORM session factory process-wide; drop the cache so
    # the module-access check also reads this temp file.
    monkeypatch.setattr("src.utils.database._Session", None)

    engine = create_db_engine(uri)
    Base.metadata.create_all(engine)  # the app's real user/role/permission schema
    engine.dispose()

    conn = _connect(temp_db)
    try:
        conn.executescript(SEED)
        conn.commit()
    finally:
        conn.close()

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    app = Flask(
        __name__,
        template_folder=os.path.join(root, "templates"),
        static_folder=os.path.join(root, "static"),
    )
    app.secret_key = "test"
    app.config["TEST_DB_PATH"] = str(temp_db)
    app.context_processor(lambda: {"csrf_token": generate_csrf_token})

    # The decorators' browser branch redirects here; the real app registers it.
    auth_bp = Blueprint("auth", __name__)

    @auth_bp.route("/login")
    def login():
        return "login"

    app.register_blueprint(auth_bp)

    @app.route("/")
    def index():
        return "home"

    app.register_blueprint(admin_db_bp)
    return app


def _table_exists(db_path, name: str) -> bool:
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)
        ).fetchone()
        return row is not None
    finally:
        conn.close()


# --------------------------------------------------------------------------
# shipped route decoration
# --------------------------------------------------------------------------

def test_every_console_route_carries_admin_and_module_markers():
    """The global module guard reads these markers; both must survive wrapping."""
    from src.auth.decorators import ADMIN_REQUIRED_ATTR, REQUIRED_MODULES_ATTR

    from src.routes import admin_db_routes as mod

    for view in (mod.db_query_editor, mod.get_db_schema, mod.execute_query):
        assert getattr(view, ADMIN_REQUIRED_ATTR) is True
        # The module gate is kept for the global choke point + audit trail.
        assert getattr(view, REQUIRED_MODULES_ATTR) == ("database",)


# --------------------------------------------------------------------------
# unauthenticated -> 401 / redirect
# --------------------------------------------------------------------------

def test_unauthenticated_console_is_denied(db_console_app):
    client = db_console_app.test_client()

    page = client.get("/admin/database/")
    assert page.status_code == 302
    assert "/login" in page.headers["Location"]

    schema = client.get("/admin/database/schema")
    assert schema.status_code == 302

    api_schema = client.get(
        "/admin/database/schema", headers={"Content-Type": "application/json"}
    )
    assert api_schema.status_code == 401

    execute = client.post("/admin/database/execute", json={"sql": "SELECT 1"})
    assert execute.status_code == 401
    assert execute.get_json() == {"error": "Authentication required"}


# --------------------------------------------------------------------------
# module granted is NOT enough
# --------------------------------------------------------------------------

def test_non_admin_with_database_module_write_is_still_denied(db_console_app):
    from src.auth.modules import ACCESS_WRITE
    from src.utils.user_manager import UserManager

    # Sanity: user 11 really does hold WRITE on the database module, so a
    # denial below can only come from the missing admin role.
    assert "module:database" in MODULE_PERMISSION_NAMES
    assert UserManager().has_any_module_access(11, ("database",), min_level=ACCESS_WRITE)

    client = db_console_app.test_client()
    _login(client, 11)

    # Page shell: browser branch redirects, never renders the React console.
    page = client.get("/admin/database/")
    assert page.status_code == 302
    assert "agent-app-root" not in page.get_data(as_text=True)

    # JSON API clients get the explicit 403 on both data endpoints.
    api_schema = client.get(
        "/admin/database/schema", headers={"Content-Type": "application/json"}
    )
    assert api_schema.status_code == 403
    execute = client.post(
        "/admin/database/execute", json={"sql": "CREATE TABLE pwned (id INTEGER)"}
    )
    assert execute.status_code == 403

    # Browser-style GET on the schema endpoint is likewise denied and leaks
    # nothing about the platform schema.
    browser_schema = client.get("/admin/database/schema")
    assert browser_schema.status_code == 302
    assert b"widgets" not in browser_schema.data

    # The denied DDL must never have executed.
    assert not _table_exists(db_console_app.config["TEST_DB_PATH"], "pwned")


# --------------------------------------------------------------------------
# admin keeps working unchanged
# --------------------------------------------------------------------------

def test_admin_can_use_the_console(db_console_app):
    client = db_console_app.test_client()
    _login(client, 10)

    page = client.get("/admin/database/")
    assert page.status_code == 200
    assert "agent-app-root" in page.get_data(as_text=True)

    schema = client.get("/admin/database/schema")
    assert schema.status_code == 200
    table_names = {table["name"] for table in schema.get_json()["tables"]}
    assert "widgets" in table_names

    execute = client.post(
        "/admin/database/execute",
        json={"sql": "SELECT name FROM widgets ORDER BY id"},
    )
    assert execute.status_code == 200
    body = execute.get_json()
    assert body["success"] is True
    assert body["isSelect"] is True
    assert body["rowCount"] == 2
    assert [row["name"] for row in body["data"]] == ["alpha", "beta"]
