"""Users management is administrator-only — real routes, real RBAC, temp DB.

A role holding only ``module:users`` must not enumerate, create, edit or delete
accounts (account-takeover territory), so ``@admin_required()`` fronts every
user endpoint/page and ``UserManager`` refuses the destructive administrator
operations.  Deleting an account also removes its private rows, because
``users.id`` is a recyclable SQLite rowid and a future account would otherwise
inherit them.

Nothing is mocked: the shipped ``admin_api_bp``/``admin_bp`` are mounted on a
minimal Flask app, real stores provision rows in an isolated temp SQLite DB
(raw connection **and** ORM session are redirected), and the tracked
``text2sql.db`` is guarded by snapshot-and-compare isolation (see
``real_db_untouched``).
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
import warnings
from pathlib import Path
from types import SimpleNamespace

import pytest
from flask import Blueprint, Flask
from sqlalchemy.orm import scoped_session, sessionmaker

REPO_ROOT = Path(__file__).resolve().parents[1]
REAL_DB = REPO_ROOT / "text2sql.db"

#: (method, path template, json body).  ``{user_id}`` targets the victim user.
API_CASES = (
    ("GET", "/admin/api/users", None),
    ("POST", "/admin/api/users", {
        "username": "users-sneaky", "email": "users-sneaky@example.com",
        "password": "Pw123456!", "roles": [],
    }),
    ("PUT", "/admin/api/users/{user_id}", {
        "username": "users-victim-renamed", "email": "users-victim@example.com", "roles": [],
    }),
    ("DELETE", "/admin/api/users/{user_id}", None),
)

PAGE_PATHS = ("/admin/users", "/admin/users/create", "/admin/users/{user_id}/edit")

#: Every shipped user view that must carry BOTH markers (admin + module).
USER_ENDPOINTS = (
    "admin_api.api_list_users", "admin_api.api_create_user", "admin_api.api_update_user",
    "admin_api.api_delete_user", "admin.list_users", "admin.create_user", "admin.edit_user",
)

#: Distinctive rows this file creates; none may ever appear in the tracked DB.
_LEAKED_DISTINCTIVE = (
    "users-operator", "users-victim", "users-sneaky", "users-victim-renamed", "users-solo",
    "users-admin", "users-admin2", "users-created", "users-purged", "users-reused",
)
_LEAKED_ROLES = ("users-operator",)


def _fingerprint(path: Path) -> tuple[int, str] | None:
    if not path.exists():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return path.stat().st_size, digest.hexdigest()


def _leaked_identities() -> set[str]:
    """Distinctive rows of this test that must never land in the tracked DB."""
    if not REAL_DB.exists():
        return set()
    conn = sqlite3.connect(f"file:{REAL_DB}?mode=ro", uri=True, timeout=10.0)
    try:
        hits = set()
        for sql, names in (
            ("SELECT username FROM users WHERE username IN (%s)", _LEAKED_DISTINCTIVE),
            ("SELECT name FROM roles WHERE name IN (%s)", _LEAKED_ROLES),
        ):
            hits |= {
                row[0]
                for row in conn.execute(sql % ",".join("?" * len(names)), names)
            }
    finally:
        conn.close()
    return hits


def _real_db_fds() -> list[str]:
    """This process's open file descriptors that point at the tracked DB."""
    fd_dir = "/proc/self/fd"
    if not os.path.isdir(fd_dir):
        return []
    open_paths: list[str] = []
    for name in os.listdir(fd_dir):
        try:
            target = os.readlink(os.path.join(fd_dir, name))
        except OSError:
            continue
        if target == str(REAL_DB) or target.startswith(f"{REAL_DB}-"):
            open_paths.append(target)
    return open_paths


@pytest.fixture(autouse=True)
def real_db_untouched():
    """Prove *this test* neither opened nor leaked into the tracked DB.

    Other files' background threads (embedding warm-up) legitimately hold
    descriptors on the shared DB, so absolute checks are order-dependent.  We
    snapshot what exists at setup and assert only that this test added nothing:
    no new real-DB fd and no new distinctive row (the row difference stays
    strict).  Pre-existing fds/rows and byte churn are reported as external
    contamination.
    """
    before_fds = set(_real_db_fds())
    before_rows = _leaked_identities()
    before = _fingerprint(REAL_DB)
    yield
    new_fds = set(_real_db_fds()) - before_fds
    assert new_fds == set(), f"this test opened the tracked text2sql.db: {sorted(new_fds)}"
    new_rows = _leaked_identities() - before_rows
    assert new_rows == set(), f"test rows leaked into the tracked text2sql.db: {sorted(new_rows)}"
    if _fingerprint(REAL_DB) != before:
        warnings.warn(
            "the shared text2sql.db changed during this test, but this process opened no "
            "new handle and leaked no new rows: external churn",
            RuntimeWarning, stacklevel=2,
        )
    if before_fds:
        warnings.warn(
            "pre-existing real-DB file descriptors held by other tests' background "
            f"threads: {sorted(before_fds)}",
            RuntimeWarning, stacklevel=2,
        )
    if before_rows:
        warnings.warn(
            "pre-existing real-DB rows matching this file's distinctive names: "
            f"{sorted(before_rows)}",
            RuntimeWarning, stacklevel=2,
        )


def _login(client, user_id: int) -> None:
    with client.session_transaction() as session:
        session["user_id"] = user_id


def _request(client, method: str, path: str, body=None):
    if method == "POST":
        return client.post(path, json=body if body is not None else {})
    if method == "PUT":
        return client.put(path, json=body if body is not None else {})
    if method == "DELETE":
        return client.delete(path, json=body) if body is not None else client.delete(path)
    return client.get(path)


@pytest.fixture()
def rbac(temp_db, monkeypatch):
    """Real users/roles/permissions in the temp DB (ORM session + raw reads)."""
    from src.auth import resource_access
    from src.models.user import Base
    from src.utils.database import create_db_engine
    from src.utils.user_manager import UserManager

    engine = create_db_engine(f"sqlite:///{temp_db}")
    Base.metadata.create_all(engine)
    session_factory = scoped_session(sessionmaker(bind=engine))
    # conftest redirects raw SQLite connections; UserManager builds its session
    # from this factory, so swapping the ORM engine keeps the whole identity
    # stack on the temp file too.
    monkeypatch.setattr("src.utils.database._Session", session_factory)

    resource_access._reset_schema_guard()
    resource_access.ensure_tables()

    manager = UserManager()
    admin_id = manager.create_user("admin", "admin@example.com", "Pw123456!")
    admin_role_id = manager.create_role("admin", "built-in administrator")
    manager.add_user_to_role(admin_id, admin_role_id)

    second_admin_id = manager.create_user("users-admin", "users-admin@example.com", "Pw123456!")
    manager.add_user_to_role(second_admin_id, admin_role_id)

    operator_id = manager.create_user("users-operator", "users-operator@example.com", "Pw123456!")
    operator_role_id = manager.create_role("users-operator", "explicit users module grant")
    manager.update_role_modules(operator_role_id, {"users": "write"})
    manager.add_user_to_role(operator_id, operator_role_id)

    victim_id = manager.create_user("users-victim", "users-victim@example.com", "Pw123456!")

    # Sanity: the escalation the old code allowed is real, else the 403s prove nothing.
    assert manager.has_module_access(operator_id, "users", min_level="write")
    assert not resource_access.is_admin(operator_id)
    assert resource_access.is_admin(admin_id)
    assert resource_access.is_admin(second_admin_id)
    assert manager._active_admin_count(manager._get_session()) == 2

    try:
        yield SimpleNamespace(
            manager=manager, db_path=temp_db, admin_id=admin_id, admin_role_id=admin_role_id,
            second_admin_id=second_admin_id, operator_id=operator_id,
            operator_role_id=operator_role_id, victim_id=victim_id,
        )
    finally:
        session_factory.remove()
        engine.dispose()
        resource_access._reset_schema_guard()


@pytest.fixture()
def app(rbac, monkeypatch):
    """Minimal app mounting the shipped admin blueprints and login/index."""
    import src.routes.admin_api_routes as admin_api_routes
    from src.routes.admin_api_routes import admin_api_bp
    from src.routes.admin_routes import admin_bp
    from src.utils.feedback_manager import FeedbackManager

    # Never let the module-level embeddings manager point at the shared DB.
    monkeypatch.setattr(
        admin_api_routes, "feedback_manager",
        FeedbackManager(connection_string=f"sqlite:///{rbac.db_path / 'feedback.db'}"),
    )

    flask_app = Flask(
        __name__, template_folder=str(REPO_ROOT / "templates"),
        static_folder=str(REPO_ROOT / "static"),
    )
    flask_app.secret_key = "users-test"
    flask_app.jinja_env.globals["csrf_token"] = lambda: "test-csrf"

    auth_bp = Blueprint("auth", __name__, url_prefix="/auth")

    @auth_bp.route("/login")
    def login():  # redirect target for unauthenticated browser navigation
        return "login"

    flask_app.register_blueprint(auth_bp)

    @flask_app.route("/")
    def index():  # redirect target for authenticated non-admin denial
        return "index"

    flask_app.register_blueprint(admin_api_bp)
    flask_app.register_blueprint(admin_bp)
    return flask_app


@pytest.fixture()
def client(app):
    return app.test_client()


# decorators: both gates survive on every user view

def test_route_markers_keep_admin_and_module_gates(app):
    from src.auth.decorators import (
        ADMIN_REQUIRED_ATTR, REQUIRED_MODULES_ATTR, get_route_requirements,
    )

    for endpoint in USER_ENDPOINTS:
        view = app.view_functions[endpoint]
        assert getattr(view, ADMIN_REQUIRED_ATTR, False) is True, endpoint
        assert getattr(view, REQUIRED_MODULES_ATTR, None), endpoint
        assert get_route_requirements(view)[0] == ("users",), endpoint


# unauthenticated

@pytest.mark.parametrize("method,path,body", API_CASES)
def test_unauthenticated_api_is_401(client, rbac, method, path, body):
    target = path.format(user_id=rbac.victim_id)
    response = _request(client, method, target, body)
    assert response.status_code == 401, (target, response.status_code)
    assert response.is_json


@pytest.mark.parametrize("path", PAGE_PATHS)
def test_unauthenticated_page_redirects_to_login(client, rbac, path):
    response = client.get(path.format(user_id=rbac.victim_id))
    assert response.status_code == 302
    assert "/auth/login" in response.headers["Location"]


# authenticated non-admin that explicitly holds module:users

@pytest.mark.parametrize("method,path,body", API_CASES)
def test_non_admin_with_users_module_is_403(client, rbac, method, path, body):
    _login(client, rbac.operator_id)
    target = path.format(user_id=rbac.victim_id)
    response = _request(client, method, target, body)
    assert response.status_code == 403, (target, response.status_code, response.get_data(as_text=True))
    assert response.is_json


def test_non_admin_cannot_create_a_user(client, rbac):
    _login(client, rbac.operator_id)
    response = client.post("/admin/api/users", json={
        "username": "users-sneaky", "email": "users-sneaky@example.com",
        "password": "Pw123456!", "roles": [rbac.admin_role_id],
    })
    assert response.status_code == 403
    assert rbac.manager.get_user_by_username("users-sneaky") is None


def test_non_admin_cannot_modify_or_delete_the_victim(client, rbac):
    _login(client, rbac.operator_id)

    updated = client.put(f"/admin/api/users/{rbac.victim_id}", json={
        "username": "users-victim-renamed", "email": "users-victim@example.com",
        "roles": [rbac.admin_role_id],
    })
    assert updated.status_code == 403
    victim = rbac.manager.get_user_by_id(rbac.victim_id)
    assert victim.username == "users-victim"
    assert [role.name for role in victim.roles] == []

    deleted = client.delete(f"/admin/api/users/{rbac.victim_id}")
    assert deleted.status_code == 403
    assert rbac.manager.get_user_by_id(rbac.victim_id) is not None


@pytest.mark.parametrize("path", PAGE_PATHS)
def test_non_admin_user_pages_redirect_to_index(client, rbac, path):
    _login(client, rbac.operator_id)
    response = client.get(path.format(user_id=rbac.victim_id))
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/")
    assert "/auth/login" not in response.headers["Location"]
    assert b"agent-app-root" not in response.data


# admin keeps working unchanged

@pytest.mark.parametrize("path", PAGE_PATHS)
def test_admin_can_load_every_user_page(client, rbac, path):
    _login(client, rbac.admin_id)
    response = client.get(path.format(user_id=rbac.victim_id))
    assert response.status_code == 200
    assert b"agent-app-root" in response.data


def test_admin_can_drive_every_user_endpoint(client, rbac):
    _login(client, rbac.admin_id)

    listing = client.get("/admin/api/users")
    assert listing.status_code == 200
    assert {u["username"] for u in listing.get_json()["users"]} >= {"admin", "users-victim"}

    created = client.post("/admin/api/users", json={
        "username": "users-created", "email": "users-created@example.com",
        "password": "Pw123456!", "roles": [rbac.operator_role_id],
    })
    assert created.status_code == 201
    created_id = created.get_json()["user"]["id"]

    updated = client.put(f"/admin/api/users/{created_id}", json={
        "username": "users-created", "email": "users-created@example.com",
        "roles": [rbac.operator_role_id], "is_active": True,
    })
    assert updated.status_code == 200
    assert updated.get_json()["user"]["is_active"] is True

    deleted = client.delete(f"/admin/api/users/{created_id}")
    assert deleted.status_code == 200
    assert rbac.manager.get_user_by_id(created_id) is None


# escalation guards in UserManager (surfaced as 403 by the API)

def test_deleting_the_builtin_admin_user_is_refused(client, rbac):
    from src.utils.user_manager import EscalationGuardError

    _login(client, rbac.second_admin_id)  # a different admin, so self-delete is not it
    response = client.delete(f"/admin/api/users/{rbac.admin_id}")
    assert response.status_code == 403
    assert "admin" in response.get_json()["error"].lower()

    with pytest.raises(EscalationGuardError):
        rbac.manager.delete_user(rbac.admin_id)
    assert rbac.manager.get_user_by_id(rbac.admin_id) is not None


def test_deactivating_the_builtin_admin_user_is_refused(client, rbac):
    from src.utils.user_manager import EscalationGuardError

    _login(client, rbac.second_admin_id)
    response = client.put(f"/admin/api/users/{rbac.admin_id}", json={
        "username": "admin", "email": "admin@example.com",
        "is_active": False, "roles": [rbac.admin_role_id],
    })
    assert response.status_code == 403

    with pytest.raises(EscalationGuardError):
        rbac.manager.update_user(rbac.admin_id, "admin", "admin@example.com", is_active=False)
    assert rbac.manager.get_user_by_id(rbac.admin_id).is_active is True


def test_admin_self_delete_of_the_builtin_admin_is_403(client, rbac):
    """Self-delete must not turn the guard's 403 into an unrelated 400."""
    _login(client, rbac.admin_id)
    response = client.delete(f"/admin/api/users/{rbac.admin_id}")
    assert response.status_code == 403
    assert "admin" in response.get_json()["error"].lower()
    assert rbac.manager.get_user_by_id(rbac.admin_id) is not None


def test_forbidden_change_wins_over_field_validation(client, rbac):
    """A protected target with an incomplete payload is 403, not 400."""
    _login(client, rbac.second_admin_id)
    response = client.put(f"/admin/api/users/{rbac.admin_id}", json={"is_active": False})
    assert response.status_code == 403
    assert rbac.manager.get_user_by_id(rbac.admin_id).is_active is True


def test_malformed_update_for_a_normal_user_is_still_400(client, rbac):
    _login(client, rbac.admin_id)
    response = client.put(f"/admin/api/users/{rbac.victim_id}", json={"is_active": True})
    assert response.status_code == 400
    assert "required" in response.get_json()["error"].lower()


def test_last_active_admin_cannot_be_deleted_deactivated_or_stripped(client, rbac):
    from src.utils.user_manager import EscalationGuardError

    manager = rbac.manager
    # Hand the role to a non-built-in account and clear it everywhere else so
    # the last-active guard is exercised on its own.
    solo_id = manager.create_user("users-solo", "users-solo@example.com", "Pw123456!")
    manager.add_user_to_role(solo_id, rbac.admin_role_id)
    manager.remove_user_from_role(rbac.second_admin_id, rbac.admin_role_id)
    manager.remove_user_from_role(rbac.admin_id, rbac.admin_role_id)
    assert manager._active_admin_count(manager._get_session()) == 1

    with pytest.raises(EscalationGuardError):
        manager.delete_user(solo_id)
    with pytest.raises(EscalationGuardError):
        manager.update_user(solo_id, "users-solo", "users-solo@example.com", is_active=False)
    with pytest.raises(EscalationGuardError):
        manager.remove_user_from_role(solo_id, rbac.admin_role_id)

    _login(client, solo_id)
    strip = client.put(f"/admin/api/users/{solo_id}", json={
        "username": "users-solo", "email": "users-solo@example.com", "roles": [],
    })
    assert strip.status_code == 403
    deactivate = client.put(f"/admin/api/users/{solo_id}", json={
        "username": "users-solo", "email": "users-solo@example.com",
        "is_active": False, "roles": [rbac.admin_role_id],
    })
    assert deactivate.status_code == 403
    # Self-deletion is refused as 403 too: the last administrator is removable
    # by nobody, not even themselves.
    assert client.delete(f"/admin/api/users/{solo_id}").status_code == 403

    surviving = manager.get_user_by_id(solo_id)
    assert surviving is not None and surviving.is_active is True
    assert manager.has_role(solo_id, "admin")


def test_non_last_admin_role_can_still_be_modified(client, rbac):
    manager = rbac.manager
    extra_id = manager.create_user("users-admin2", "users-admin2@example.com", "Pw123456!")
    manager.add_user_to_role(extra_id, rbac.admin_role_id)

    # Removing the admin role is fine while the built-in admin remains active.
    assert manager.remove_user_from_role(extra_id, rbac.admin_role_id) is True
    assert not manager.has_role(extra_id, "admin")
    manager.add_user_to_role(extra_id, rbac.admin_role_id)

    _login(client, rbac.admin_id)
    deactivated = client.put(f"/admin/api/users/{extra_id}", json={
        "username": "users-admin2", "email": "users-admin2@example.com",
        "is_active": False, "roles": [rbac.admin_role_id],
    })
    assert deactivated.status_code == 200
    assert deactivated.get_json()["user"]["is_active"] is False

    assert client.delete(f"/admin/api/users/{extra_id}").status_code == 200
    assert manager.get_user_by_id(extra_id) is None
    assert manager.get_user_by_id(rbac.admin_id).is_active is True


# account deletion purges the private data a recycled id would inherit

#: Legacy tables absent from a database created by current code; DDL is shipped prod.
_LEGACY_DDL = """
CREATE TABLE IF NOT EXISTS knowledge_queries (id TEXT PRIMARY KEY, user_id INTEGER, query TEXT NOT NULL, answer TEXT NOT NULL, created_at TIMESTAMP NOT NULL, FOREIGN KEY (user_id) REFERENCES users(id));
CREATE TABLE IF NOT EXISTS code_generation_history (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, username TEXT, project_name TEXT NOT NULL, mapping_name TEXT NOT NULL, status TEXT NOT NULL, started_at TEXT NOT NULL, completed_at TEXT, duration_seconds REAL, table_names TEXT, output_file TEXT, code_lines INTEGER, had_existing_code INTEGER DEFAULT 0, error_message TEXT, FOREIGN KEY (user_id) REFERENCES users(id));
CREATE TABLE IF NOT EXISTS audit_logs (id INTEGER NOT NULL, user_id INTEGER, action VARCHAR(100) NOT NULL, details TEXT, timestamp DATETIME, ip_address VARCHAR(50), query_text TEXT, sql_query TEXT, response TEXT, PRIMARY KEY (id), FOREIGN KEY(user_id) REFERENCES users (id));
CREATE TABLE IF NOT EXISTS agent_definition_access (id INTEGER PRIMARY KEY AUTOINCREMENT, agent_id INTEGER NOT NULL, user_id INTEGER NOT NULL, granted_by INTEGER, granted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, UNIQUE(agent_id, user_id));
CREATE TABLE IF NOT EXISTS agent_definitions (id INTEGER PRIMARY KEY AUTOINCREMENT, slug TEXT NOT NULL UNIQUE, name TEXT NOT NULL, kind TEXT NOT NULL, config TEXT NOT NULL, published INTEGER NOT NULL DEFAULT 0, version INTEGER NOT NULL DEFAULT 1, created_by INTEGER, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, updated_by INTEGER);
"""


def _scalar(db_path, sql: str, params=()):
    conn = sqlite3.connect(str(db_path))
    try:
        return conn.execute(sql, params).fetchone()[0]
    finally:
        conn.close()


def _user_references(db_path, user_id: int) -> dict[str, int]:
    """Every row left in any table whose id column still points at ``user_id``."""
    id_columns = ("user_id", "created_by", "owner_id", "updated_by", "granted_by")
    conn = sqlite3.connect(str(db_path))
    try:
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        refs: dict[str, int] = {}
        for table in tables:
            present = {r[1] for r in conn.execute(f"PRAGMA table_info('{table}')")}
            for column in id_columns:
                if column not in present:
                    continue
                count = conn.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE {column} = ?", (user_id,)
                ).fetchone()[0]
                if count:
                    refs[f"{table}.{column}"] = count
        return refs
    finally:
        conn.close()


def test_deleting_a_user_purges_private_data_and_reuse_inherits_nothing(client, rbac):
    from src.agent_platform.agui.thread_store import bind
    from src.agent_platform.conversations.store import ConversationStore
    from src.agent_platform.execution.api_keys import ApiKeyStore
    from src.agent_platform.execution.run_store import RunStore
    from src.utils.database import get_db_connection

    manager, db = rbac.manager, rbac.db_path
    victim_id = manager.create_user("users-purged", "users-purged@example.com", "Pw123456!")

    convo = ConversationStore.create(user_id=victim_id, title="private", agent_slug="analyst")
    convo_id = int(convo["id"])
    ConversationStore.add_message(convo_id, "user", "private question")
    RunStore.create(task="private run", entity_id=1, conversation_id=convo_id, user_id=victim_id)
    ApiKeyStore.create(user_id=victim_id, name="victim key")
    bind(f"thread-{victim_id}", convo_id)

    conn = get_db_connection()
    try:
        conn.executescript(_LEGACY_DDL)
        conn.execute(
            "INSERT INTO agent_attachments (public_id, conversation_id, user_id, filename, path) "
            "VALUES (?, ?, ?, 'secret.txt', '/tmp/secret.txt')", (f"att-{victim_id}", convo_id, victim_id),
        )
        conn.execute("INSERT INTO agent_sessions (conversation_id, session_json) VALUES (?, '{}')", (convo_id,))
        conn.execute("INSERT INTO knowledge_queries VALUES ('kq', ?, 'q', 'a', '2020-01-01')", (victim_id,))
        conn.execute(
            "INSERT INTO code_generation_history (user_id, username, project_name, mapping_name, status, started_at) "
            "VALUES (?, 'users-purged', 'p', 'm', 'done', '2020-01-01')", (victim_id,),
        )
        conn.execute("INSERT INTO audit_logs (id, user_id, action) VALUES (1, ?, 'login')", (victim_id,))
        conn.execute(
            "INSERT INTO agent_definition_access (agent_id, user_id, granted_by) VALUES (1, ?, ?)",
            (victim_id, victim_id),
        )
        conn.execute(
            "INSERT INTO agent_definitions (slug, name, kind, config, created_by) "
            "VALUES ('victim-agent', 'Victim', 'agent', '{}', ?)", (victim_id,),
        )
        conn.execute(
            "INSERT INTO resource_role_access (resource_type, resource_id, role_id, granted_by) "
            "VALUES ('agent', 1, ?, ?)", (rbac.operator_role_id, victim_id),
        )
        conn.commit()
    finally:
        conn.close()

    _login(client, rbac.admin_id)
    assert client.delete(f"/admin/api/users/{victim_id}").status_code == 200
    assert manager.get_user_by_id(victim_id) is None

    # No table anywhere still points at the deleted account...
    assert _user_references(db, victim_id) == {}
    # ...the deleted user's chat children are gone with the conversation...
    for table in ("agent_messages", "agent_sessions", "agui_threads", "agent_attachments"):
        assert _scalar(db, f"SELECT COUNT(*) FROM {table} WHERE conversation_id = ?", (convo_id,)) == 0, table

    # Audit rows are retained but de-identified; shared assets move to the admin.
    assert _scalar(db, "SELECT COUNT(*) FROM audit_logs WHERE action = 'login'") == 1
    assert _scalar(db, "SELECT COUNT(*) FROM audit_logs WHERE user_id IS NULL") == 1
    assert _scalar(db, "SELECT created_by FROM agent_definitions WHERE slug = 'victim-agent'") == rbac.admin_id

    # SQLite recycles the freed rowid: the next account must inherit nothing.
    reused_id = manager.create_user("users-reused", "users-reused@example.com", "Pw123456!")
    assert reused_id == victim_id
    for table in ("agent_conversations", "agent_runs", "api_keys"):
        assert _scalar(db, f"SELECT COUNT(*) FROM {table}") == 0, table
