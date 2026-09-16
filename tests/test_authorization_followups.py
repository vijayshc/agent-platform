"""Authorization follow-ups: API classification, admin-only modules, boot migration.

Everything here is real integration: the shipped decorators and admin blueprints,
the real ``UserManager`` / ``resource_access`` decisions against a temp SQLite
database, and the real module-store migration entry points against a
legacy-schema temp database.  No authorization decision or migration is mocked;
only the connection target is swapped, exactly the pattern ``tests/conftest.py``
already uses, so the tracked ``text2sql.db`` is never touched.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from flask import Blueprint, Flask
from sqlalchemy.orm import scoped_session, sessionmaker

from src.auth import resource_access
from src.auth.modules import MODULES
from src.models.user import Base, Permission, Role, User
from src.utils import database

REPO_ROOT = Path(__file__).resolve().parents[1]

ADMIN_ONLY_KEYS = ("vector_db", "database", "file_browser", "users", "roles")

#: Catalog order is part of the public contract (role modal + admin rail).
EXPECTED_MODULE_ORDER = (
    "dashboard",
    "agent_studio",
    "observability",
    "skills",
    "mcp_servers",
    "knowledge",
    "vector_db",
    "database",
    "file_browser",
    "hosted_apps",
    "users",
    "roles",
    "llm",
)


def _raw_connect(db_path: Path):
    def _connect():
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        return conn

    return _connect


def _login(client, user_id: int) -> None:
    with client.session_transaction() as session:
        session["user_id"] = user_id


# ---------------------------------------------------------------------------
# shared identity fixture: a real admin plus a non-admin holding every module
# ---------------------------------------------------------------------------


@pytest.fixture()
def rbac(tmp_path, monkeypatch):
    db_path = tmp_path / "followups.db"
    engine = database.create_db_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
    session_factory = scoped_session(sessionmaker(bind=engine))
    monkeypatch.setattr(database, "_Session", session_factory)
    # resource_access / is_admin read roles + user_roles through this raw path.
    monkeypatch.setattr("src.agent_platform.db.get_db_connection", _raw_connect(db_path))
    resource_access._reset_schema_guard()

    admin_role = Role(name="admin")
    operator_role = Role(
        name="operator",
        # The non-admin explicitly holds every admin-only module, so hiding them
        # from ``/me`` can only come from the catalog flag, not a missing grant.
        permissions=[
            Permission(name=f"module:{key}") for key in (*ADMIN_ONLY_KEYS, "knowledge")
        ],
    )
    admin_user = User(
        username="root", email="root@example.com", password_hash="x", roles=[admin_role]
    )
    operator_user = User(
        username="operator",
        email="operator@example.com",
        password_hash="x",
        roles=[operator_role],
    )
    session = session_factory()
    session.add_all([admin_user, operator_user])
    session.commit()
    ids = {"admin_id": admin_user.id, "operator_id": operator_user.id, "db_path": db_path}

    from src.utils.user_manager import UserManager

    assert UserManager().has_any_module_access(
        ids["operator_id"], ("file_browser",), min_level="write"
    )
    assert UserManager().has_role(ids["admin_id"], "admin")

    try:
        yield ids
    finally:
        session_factory.remove()
        engine.dispose()
        resource_access._reset_schema_guard()


# ---------------------------------------------------------------------------
# Task 1 — ``_is_api_request`` classifies every ``/api/`` path as an API request
# ---------------------------------------------------------------------------


@pytest.fixture()
def bare_app():
    app = Flask(__name__)
    app.secret_key = "followups"
    return app


@pytest.mark.parametrize(
    "path",
    (
        "/api/v1/agents",
        "/admin/api/modules",
        "/admin/file-browser/api/list",
        "/admin/config/llm/api/list",
    ),
)
def test_is_api_request_true_for_api_paths(bare_app, path):
    from src.auth.decorators import _is_api_request

    with bare_app.test_request_context(path, method="GET"):
        assert _is_api_request() is True


@pytest.mark.parametrize(
    "path",
    ("/admin", "/admin/database/", "/admin/file-browser/", "/browser-secret"),
)
def test_is_api_request_false_for_browser_page_paths(bare_app, path):
    from src.auth.decorators import _is_api_request

    with bare_app.test_request_context(path, method="GET"):
        assert _is_api_request() is False


def test_is_api_request_true_for_json_body(bare_app):
    import json

    from src.auth.decorators import _is_api_request

    with bare_app.test_request_context(
        "/admin/database/",
        method="POST",
        data=json.dumps({"sql": "SELECT 1"}),
        content_type="application/json",
    ):
        assert _is_api_request() is True


@pytest.mark.parametrize(
    "accept",
    (
        "application/json",
        "application/json, text/plain, */*",
        "*/*;q=0.5, application/json;q=0.9",
    ),
)
def test_is_api_request_true_when_client_prefers_json(bare_app, accept):
    from src.auth.decorators import _is_api_request

    with bare_app.test_request_context(
        "/admin/database/schema", method="GET", headers={"Accept": accept}
    ):
        assert _is_api_request() is True


@pytest.mark.parametrize(
    "accept",
    (
        None,  # no Accept header at all
        "*/*",  # the default browsers/test clients send
        "text/html",
        "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "application/json;q=0.5, text/html;q=0.9",  # HTML is preferred
        "application/xml",
    ),
)
def test_is_api_request_false_when_client_does_not_prefer_json(bare_app, accept):
    from src.auth.decorators import _is_api_request

    headers = {} if accept is None else {"Accept": accept}
    with bare_app.test_request_context(
        "/admin/database/schema", method="GET", headers=headers
    ):
        assert _is_api_request() is False


@pytest.fixture()
def routes_app(rbac, tmp_path, monkeypatch):
    """Shipped file-browser + db console blueprints on a temp DB and temp root."""
    from src.routes.admin_db_routes import admin_db_bp
    from src.routes.admin_file_browser_routes import admin_file_browser_bp
    from src.routes.security_routes import generate_csrf_token
    from src.services.file_browser_service import FileBrowserService

    browse_root = tmp_path / "workspace"
    browse_root.mkdir()
    (browse_root / "readme.txt").write_text("hello", encoding="utf-8")
    service = FileBrowserService(root_path=str(browse_root), allowed_extensions={".txt"})
    monkeypatch.setattr(
        "src.routes.admin_file_browser_routes.file_browser_service", service
    )
    uri = f"sqlite:///{rbac['db_path']}"
    monkeypatch.setattr("src.routes.admin_db_routes.DATABASE_URI", uri)
    monkeypatch.setattr(database, "DATABASE_URI", uri)

    app = Flask(
        __name__,
        template_folder=str(REPO_ROOT / "templates"),
        static_folder=str(REPO_ROOT / "static"),
    )
    app.secret_key = "followups"
    app.config["TEST_DB_PATH"] = str(rbac["db_path"])
    app.context_processor(lambda: {"csrf_token": generate_csrf_token})

    auth_bp = Blueprint("auth", __name__, url_prefix="/auth")

    @auth_bp.route("/login")
    def login():
        return "login"

    app.register_blueprint(auth_bp)

    @app.route("/")
    def index():
        return "home"

    app.register_blueprint(admin_db_bp)
    app.register_blueprint(admin_file_browser_bp)
    return app


def test_plain_get_on_admin_area_api_is_403_json_not_a_redirect(rbac, routes_app):
    """The inconsistency this follow-up fixes: no JSON headers, still 403 JSON."""
    client = routes_app.test_client()
    _login(client, rbac["operator_id"])

    api = client.get("/admin/file-browser/api/list")
    assert api.status_code == 403
    assert api.is_json
    assert api.get_json()["error"] == "Permission denied"

    # Browser *pages* keep the flash-redirect contract.
    page = client.get("/admin/database/")
    assert page.status_code == 302
    assert page.headers["Location"].endswith("/")
    assert "/login" not in page.headers["Location"]


def test_unauthenticated_admin_area_api_is_401_json(routes_app):
    client = routes_app.test_client()
    resp = client.get("/admin/file-browser/api/list")
    assert resp.status_code == 401
    assert resp.is_json


def test_admin_still_reaches_admin_area_api(rbac, routes_app):
    client = routes_app.test_client()
    _login(client, rbac["admin_id"])
    resp = client.get("/admin/file-browser/api/list")
    assert resp.status_code == 200


def test_non_api_path_denial_follows_accept_negotiation(rbac, routes_app):
    """`/admin/database/schema` has no `/api/` segment: `Accept` decides.

    The tester's contract: a JSON-negotiating client gets 403 JSON, a browser
    page navigation keeps the flash-redirect.
    """
    client = routes_app.test_client()
    _login(client, rbac["operator_id"])

    wants_json = client.get(
        "/admin/database/schema", headers={"Accept": "application/json"}
    )
    assert wants_json.status_code == 403
    assert wants_json.is_json
    assert wants_json.get_json()["error"] == "Permission denied"

    wants_html = client.get("/admin/database/schema", headers={"Accept": "text/html"})
    assert wants_html.status_code == 302
    assert wants_html.headers["Location"].endswith("/")
    assert not wants_html.is_json


# ---------------------------------------------------------------------------
# Task 2 — admin-only modules are marked and hidden from non-admins
# ---------------------------------------------------------------------------


def test_catalog_marks_exactly_five_admin_only_modules():
    assert tuple(m.key for m in MODULES) == EXPECTED_MODULE_ORDER
    for module in MODULES:
        assert module.admin_only is (module.key in ADMIN_ONLY_KEYS), module.key
        assert module.as_dict()["admin_only"] is (module.key in ADMIN_ONLY_KEYS)


def test_as_dict_surfaces_admin_only_for_the_admin_api():
    """/admin/api/modules serializes ``as_dict`` verbatim; no route change needed."""
    marked = {
        module.key for module in MODULES if module.as_dict().get("admin_only") is True
    }
    assert marked == set(ADMIN_ONLY_KEYS)


def _me_client(rbac):
    from src.agent_platform.api.blueprint import create_blueprint

    app = Flask(__name__)
    app.secret_key = "followups"
    app.register_blueprint(create_blueprint())
    return app.test_client()


def test_me_hides_admin_only_modules_from_non_admin(rbac):
    client = _me_client(rbac)
    _login(client, rbac["operator_id"])
    body = client.get("/api/v1/me").get_json()

    assert body["is_admin"] is False
    for key in ADMIN_ONLY_KEYS:
        assert key not in body["module_levels"], key
        assert key not in body["modules"], key
        assert key not in {item["key"] for item in body["admin_menu"]}, key

    # A normal module the non-admin legitimately holds is untouched.
    assert body["module_levels"] == {"knowledge": "write"}
    assert [item["key"] for item in body["admin_menu"]] == ["knowledge"]


def test_me_keeps_admin_only_modules_for_admin(rbac):
    client = _me_client(rbac)
    _login(client, rbac["admin_id"])
    body = client.get("/api/v1/me").get_json()

    assert body["is_admin"] is True
    advertised = {item["key"] for item in body["admin_menu"]}
    assert set(body["modules"]) == set(EXPECTED_MODULE_ORDER)
    for key in ADMIN_ONLY_KEYS:
        assert key in body["module_levels"], key
        assert key in advertised, key


# ---------------------------------------------------------------------------
# Task 3 — startup migrates every module's tenancy columns
# ---------------------------------------------------------------------------

#: The pre-tenancy shape of each table that gained ownership columns.
LEGACY_SCHEMA = """
CREATE TABLE users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE,
    email TEXT,
    password_hash TEXT,
    is_active INTEGER DEFAULT 1
);
CREATE TABLE roles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,
    description TEXT
);
CREATE TABLE mcp_servers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    description TEXT,
    server_type TEXT NOT NULL,
    config TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE llm_connections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    base_url TEXT,
    api_key TEXT,
    model_name TEXT,
    system_instruction TEXT,
    http_headers TEXT,
    verify_ssl INTEGER DEFAULT 1,
    is_default INTEGER DEFAULT 0,
    enabled INTEGER DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE maf_skills (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    description TEXT,
    path TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE skills (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    skill_id TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    description TEXT NOT NULL,
    category TEXT NOT NULL,
    tags TEXT DEFAULT '[]',
    prerequisites TEXT DEFAULT '[]',
    steps TEXT NOT NULL,
    examples TEXT DEFAULT '[]',
    status TEXT DEFAULT 'active',
    version TEXT DEFAULT '1.0',
    created_by TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE knowledge_documents (
    id TEXT PRIMARY KEY,
    title TEXT,
    content TEXT,
    created_at TIMESTAMP
);
CREATE TABLE hosted_apps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slug TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    created_by INTEGER
);
CREATE TABLE agent_definitions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slug TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    kind TEXT NOT NULL,
    config TEXT NOT NULL,
    published INTEGER NOT NULL DEFAULT 0,
    version INTEGER NOT NULL DEFAULT 1,
    created_by INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

EXPECTED_TENANCY_COLUMNS = {
    "mcp_servers": ("created_by",),
    "llm_connections": ("created_by",),
    "maf_skills": ("created_by",),
    "skills": ("owner_id",),
    "knowledge_documents": ("owner_id", "access_id"),
    "hosted_apps": ("created_by",),
    "agent_definitions": ("created_by",),
}


@pytest.fixture()
def legacy_module_db(temp_db, monkeypatch):
    """Legacy-schema temp DB with every store's connection redirected to it."""
    connect = _raw_connect(temp_db)
    monkeypatch.setattr("src.models.skill.get_db_connection", connect)
    monkeypatch.setattr("src.models.hosted_app.get_db_connection", connect)
    resource_access._reset_schema_guard()

    conn = connect()
    try:
        conn.executescript(LEGACY_SCHEMA)
        conn.execute(
            "INSERT INTO users (id, username, email, password_hash, is_active) "
            "VALUES (1, 'root', 'root@example.com', 'x', 1)"
        )
        conn.execute("INSERT INTO roles (id, name) VALUES (1, 'admin')")
        conn.execute(
            "INSERT INTO skills (skill_id, name, description, category, steps) "
            "VALUES ('skill-1', 'Legacy skill', 'pre-ownership row', 'other', '[]')"
        )
        conn.execute(
            "INSERT INTO knowledge_documents (id, title, created_at) "
            "VALUES ('doc-1', 'Legacy doc', '2024-01-01 00:00:00')"
        )
        conn.commit()
    finally:
        conn.close()

    yield temp_db
    resource_access._reset_schema_guard()


def test_ensure_module_schema_migrates_legacy_tenancy_columns(legacy_module_db):
    from src.agent_platform.bootstrap import ensure_module_schema, missing_tenancy_columns

    ensure_module_schema()

    assert missing_tenancy_columns() == []

    conn = _raw_connect(legacy_module_db)()
    try:
        for table, columns in EXPECTED_TENANCY_COLUMNS.items():
            present = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
            for column in columns:
                assert column in present, f"{table}.{column} missing after migration"
        # The knowledge store's stable integer identity is backfilled, not just
        # added, so the generic access API can address the legacy row.
        row = conn.execute(
            "SELECT access_id FROM knowledge_documents WHERE id = 'doc-1'"
        ).fetchone()
        assert row["access_id"] == 1
    finally:
        conn.close()


def test_ensure_module_schema_is_idempotent(legacy_module_db):
    from src.agent_platform.bootstrap import ensure_module_schema, missing_tenancy_columns

    ensure_module_schema()
    ensure_module_schema()

    assert missing_tenancy_columns() == []
    # No duplicate index blow-up after a second run on the same file.
    conn = _raw_connect(legacy_module_db)()
    try:
        indexes = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index' "
                "AND name IN ('idx_skills_owner', 'idx_maf_skills_owner', "
                "'idx_knowledge_documents_access_id')"
            )
        }
    finally:
        conn.close()
    assert indexes == {
        "idx_skills_owner",
        "idx_maf_skills_owner",
        "idx_knowledge_documents_access_id",
    }


def test_initialize_runtime_runs_module_schema_after_readiness(monkeypatch):
    """Boot order: plugins -> readiness check -> module schema -> grants -> rest."""
    import src.agent_platform.bootstrap as bootstrap
    import src.agent_platform.plugins as plugins

    calls: list[str] = []
    monkeypatch.setattr(
        plugins, "register_builtin_plugins", lambda: calls.append("plugins")
    )
    monkeypatch.setattr(
        bootstrap, "assert_database_ready", lambda: calls.append("ready")
    )
    monkeypatch.setattr(
        bootstrap, "ensure_module_schema", lambda: calls.append("schema")
    )
    monkeypatch.setattr(
        bootstrap, "migrate_resource_grants", lambda: calls.append("grants")
    )
    monkeypatch.setattr(bootstrap, "_reconcile_runs", lambda: calls.append("runs"))
    monkeypatch.setattr(
        bootstrap, "unpublish_scripted_definitions", lambda: calls.append("unpublish")
    )

    bootstrap.initialize_runtime()

    assert calls == ["plugins", "ready", "schema", "grants", "runs", "unpublish"]


def test_conftest_preimport_keeps_import_time_binders_on_the_real_helper():
    """Regression guard for the ``temp_db`` bound-name leak.

    ``mcp_server``/``llm_connection``/``skill``/``hosted_app`` bind
    ``get_db_connection`` at import time.  ``conftest`` must import them before
    any test patches the helper; otherwise a ``temp_db`` test can leave them
    permanently bound to a torn-down temp connection.
    """
    import src.models.hosted_app as hosted_app_module
    import src.models.llm_connection as llm_module
    import src.models.mcp_server as mcp_module
    import src.models.skill as skill_module
    import src.utils.database as database_module

    real = database_module.get_db_connection
    for module in (mcp_module, llm_module, skill_module, hosted_app_module):
        assert module.get_db_connection is real, module.__name__
