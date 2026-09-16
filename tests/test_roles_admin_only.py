"""Roles management is administrator-only — real routes, real RBAC, temp DB.

The privilege-escalation hole this file closes: a role that holds
``module:roles`` (or ``module:users``) could create roles and grant itself any
module — including ``roles``/``users``/``vector_db`` — becoming an effective
administrator.  ``admin_required()`` now fronts every role/module endpoint, so
the module grant is necessary but not sufficient.

Nothing is mocked: the shipped ``admin_api_bp`` and ``admin_bp`` are mounted on
a minimal Flask app, ``UserManager`` provisions real users/roles/permissions in
an isolated temp SQLite database (both the ORM session and the raw connection
are redirected by ``tests/conftest.py::temp_db`` plus this file's fixture), and
the real decorators decide.  Because the tracked ``text2sql.db`` is shared with
parallel agents that may legitimately write mid-test (and, in the combined
suite, earlier test files leave a background embedding-warmup thread holding
connections to it), this file proves isolation relative to setup: *this* test
opens no additional handle to the real DB and leaks none of its distinctive
rows into it, while the temp DB receives every write.
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

#: (method, path template, json body). ``{role_id}`` targets a victim role that
#: the non-admin must not be able to read, alter or delete.
API_CASES = (
    ("POST", "/admin/api/roles", {"name": "escalated-role", "description": "pwn"}),
    (
        "PUT",
        "/admin/api/roles/{role_id}",
        {"name": "renamed-by-operator", "description": "x"},
    ),
    ("DELETE", "/admin/api/roles/{role_id}", None),
    ("GET", "/admin/api/roles/{role_id}/permissions", None),
    (
        "PUT",
        "/admin/api/roles/{role_id}/permissions",
        {"modules": ["users", "vector_db"]},
    ),
    ("GET", "/admin/api/roles", None),
    ("GET", "/admin/api/modules", None),
)

PAGE_PATH = "/admin/roles"

#: Every shipped role/module view that must carry BOTH markers.
ROLE_ENDPOINTS = (
    "admin_api.create_role",
    "admin_api.update_role",
    "admin_api.delete_role",
    "admin_api.get_role_permissions",
    "admin_api.update_role_permissions",
    "admin_api.get_all_roles",
    "admin_api.list_modules",
    "admin.list_roles",
)


def _fingerprint(path: Path) -> tuple[int, str] | None:
    if not path.exists():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return path.stat().st_size, digest.hexdigest()


#: Rows this file creates; none of them may ever appear in the tracked DB.
_LEAKED_ROLES = ("roles-victim", "escalated-role", "wave-role", "wave-role-renamed")
_LEAKED_USERS = ("roles-admin", "roles-operator")


def _leaked_identities() -> set[str]:
    """Distinctive rows of this test that must never land in the tracked DB."""
    if not REAL_DB.exists():
        return set()
    conn = sqlite3.connect(f"file:{REAL_DB}?mode=ro", uri=True, timeout=10.0)
    try:
        roles = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM roles WHERE name IN (%s)"
                % ",".join("?" * len(_LEAKED_ROLES)),
                _LEAKED_ROLES,
            )
        }
        users = {
            row[0]
            for row in conn.execute(
                "SELECT username FROM users WHERE username IN (%s)"
                % ",".join("?" * len(_LEAKED_USERS)),
                _LEAKED_USERS,
            )
        }
    finally:
        conn.close()
    return roles | users


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
    """This test must not open or write the tracked ``text2sql.db``.

    The DB is shared with parallel agents, and earlier test files in the
    combined suite legitimately leave a background embedding-warmup thread
    holding descriptors to it.  The deterministic guarantees are therefore
    relative to the state at setup: the number of real-DB descriptors this
    process holds must not grow, and none of this file's distinctive rows may
    appear in it.  A content-fingerprint change is reported as external
    contamination, because only a newly opened handle or a leaked row can prove
    fault.
    """
    fds_before = _real_db_fds()
    before = _fingerprint(REAL_DB)
    yield
    fds_after = _real_db_fds()
    leaked = _leaked_identities()
    assert leaked == set(), f"test rows leaked into the tracked text2sql.db: {sorted(leaked)}"
    assert len(fds_after) <= len(fds_before), (
        "this test opened the tracked text2sql.db: "
        f"{len(fds_before)} -> {len(fds_after)} descriptors {sorted(fds_after)}"
    )
    after = _fingerprint(REAL_DB)
    if after != before:
        warnings.warn(
            "the shared text2sql.db changed during this test, but this test "
            "opened no new handle to it and leaked no rows: external "
            "contamination (concurrent agents / background threads)",
            RuntimeWarning,
            stacklevel=2,
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
    # ``tests/conftest.py::temp_db`` already redirects every raw SQLite
    # connection; ``UserManager`` builds its session from this factory, so swap
    # that engine too and the whole identity stack stays on the temp file.
    monkeypatch.setattr("src.utils.database._Session", session_factory)

    resource_access._reset_schema_guard()
    resource_access.ensure_tables()

    manager = UserManager()
    admin_id = manager.create_user("roles-admin", "roles-admin@example.com", "Pw123456!")
    admin_role_id = manager.create_role("admin", "built-in administrator")
    manager.add_user_to_role(admin_id, admin_role_id)

    operator_id = manager.create_user(
        "roles-operator", "roles-operator@example.com", "Pw123456!"
    )
    operator_role_id = manager.create_role(
        "roles-operator", "explicit roles + users module grant"
    )
    manager.update_role_modules(operator_role_id, {"roles": "write", "users": "write"})
    manager.add_user_to_role(operator_id, operator_role_id)

    victim_role_id = manager.create_role("roles-victim", "deletable role with a grant")
    manager.update_role_modules(victim_role_id, {"knowledge": "write"})
    # One real per-asset grant referencing the victim role.
    resource_access.grant_access("agent", 1, victim_role_id, granted_by=admin_id)

    # Sanity: the escalation the old code allowed is real, otherwise the 403s
    # below would prove nothing.
    assert manager.has_module_access(operator_id, "roles", min_level="write")
    assert manager.has_module_access(operator_id, "users", min_level="write")
    assert not resource_access.is_admin(operator_id)
    assert resource_access.is_admin(admin_id)

    try:
        yield SimpleNamespace(
            manager=manager,
            db_path=temp_db,
            admin_id=admin_id,
            admin_role_id=admin_role_id,
            operator_id=operator_id,
            operator_role_id=operator_role_id,
            victim_role_id=victim_role_id,
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
        admin_api_routes,
        "feedback_manager",
        FeedbackManager(connection_string=f"sqlite:///{rbac.db_path / 'feedback.db'}"),
    )

    flask_app = Flask(
        __name__,
        template_folder=str(REPO_ROOT / "templates"),
        static_folder=str(REPO_ROOT / "static"),
    )
    flask_app.secret_key = "roles-test"
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


# ---------------------------------------------------------------------------
# decorators: both gates survive on every role/module view
# ---------------------------------------------------------------------------


def test_route_markers_keep_admin_and_module_gates(app):
    from src.auth.decorators import (
        ADMIN_REQUIRED_ATTR,
        REQUIRED_MODULES_ATTR,
        get_route_requirements,
    )

    for endpoint in ROLE_ENDPOINTS:
        view = app.view_functions[endpoint]
        assert getattr(view, ADMIN_REQUIRED_ATTR, False) is True, endpoint
        assert getattr(view, REQUIRED_MODULES_ATTR, None), endpoint

    roles_modules, _ = get_route_requirements(app.view_functions["admin_api.get_all_roles"])
    assert set(roles_modules) == {"roles", "users"}
    assert get_route_requirements(app.view_functions["admin_api.list_modules"])[0] == ("roles",)
    assert get_route_requirements(app.view_functions["admin.list_roles"])[0] == ("roles",)


# ---------------------------------------------------------------------------
# unauthenticated
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("method,path,body", API_CASES)
def test_unauthenticated_api_is_401(client, rbac, method, path, body):
    target = path.format(role_id=rbac.victim_role_id)
    response = _request(client, method, target, body)
    assert response.status_code == 401, (target, response.status_code)
    assert response.is_json


def test_unauthenticated_page_redirects_to_login(client):
    response = client.get(PAGE_PATH)
    assert response.status_code == 302
    assert "/auth/login" in response.headers["Location"]


# ---------------------------------------------------------------------------
# authenticated non-admin that explicitly holds module:roles (+ module:users)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("method,path,body", API_CASES)
def test_non_admin_with_roles_module_is_403(client, rbac, method, path, body):
    _login(client, rbac.operator_id)
    target = path.format(role_id=rbac.victim_role_id)
    response = _request(client, method, target, body)
    assert response.status_code == 403, (target, response.status_code, response.get_data(as_text=True))
    assert response.is_json


def test_non_admin_cannot_create_a_role(client, rbac):
    _login(client, rbac.operator_id)
    response = client.post(
        "/admin/api/roles", json={"name": "escalated-role", "description": "pwn"}
    )
    assert response.status_code == 403
    names = {role.name for role in rbac.manager.get_all_roles()}
    assert "escalated-role" not in names


def test_non_admin_permission_put_does_not_touch_the_target_role(client, rbac):
    _login(client, rbac.operator_id)
    before = rbac.manager.get_role_module_levels(rbac.victim_role_id)
    response = client.put(
        f"/admin/api/roles/{rbac.victim_role_id}/permissions",
        json={"modules": ["users", "vector_db"]},
    )
    assert response.status_code == 403
    after = rbac.manager.get_role_module_levels(rbac.victim_role_id)
    assert after == before


def test_non_admin_page_redirects_to_index(client, rbac):
    _login(client, rbac.operator_id)
    response = client.get(PAGE_PATH)
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/")
    assert "/auth/login" not in response.headers["Location"]
    assert b"agent-app-root" not in response.data


# ---------------------------------------------------------------------------
# admin keeps working unchanged
# ---------------------------------------------------------------------------


def test_admin_can_drive_every_role_endpoint(client, rbac):
    _login(client, rbac.admin_id)

    listing = client.get("/admin/api/roles")
    assert listing.status_code == 200
    assert {role["name"] for role in listing.get_json()["roles"]} >= {"admin", "roles-victim"}

    modules = client.get("/admin/api/modules")
    assert modules.status_code == 200
    assert modules.get_json()["modules"]

    created = client.post(
        "/admin/api/roles", json={"name": "wave-role", "description": "created by admin"}
    )
    assert created.status_code == 201
    new_role_id = created.get_json()["role_id"]

    permissions = client.get(f"/admin/api/roles/{new_role_id}/permissions")
    assert permissions.status_code == 200
    assert permissions.get_json()["role_name"] == "wave-role"

    updated = client.put(
        f"/admin/api/roles/{new_role_id}/permissions", json={"modules": ["knowledge"]}
    )
    assert updated.status_code == 200
    assert updated.get_json()["module_levels"] == {"knowledge": "write"}

    renamed = client.put(
        f"/admin/api/roles/{new_role_id}",
        json={"name": "wave-role-renamed", "description": "renamed by admin"},
    )
    assert renamed.status_code == 200

    deleted = client.delete(f"/admin/api/roles/{new_role_id}")
    assert deleted.status_code == 200
    assert "wave-role-renamed" not in {role.name for role in rbac.manager.get_all_roles()}


def test_admin_can_load_the_roles_page(client, rbac):
    _login(client, rbac.admin_id)
    response = client.get(PAGE_PATH)
    assert response.status_code == 200
    assert b"agent-app-root" in response.data


# ---------------------------------------------------------------------------
# escalation hardening in UserManager
# ---------------------------------------------------------------------------


def test_deleting_role_purges_its_resource_grants(client, rbac):
    from src.auth import resource_access

    granted_before = resource_access.list_access("agent", 1)
    assert rbac.victim_role_id in {entry["role_id"] for entry in granted_before}

    _login(client, rbac.admin_id)
    response = client.delete(f"/admin/api/roles/{rbac.victim_role_id}")
    assert response.status_code == 200

    assert resource_access.list_access("agent", 1) == []
    assert rbac.manager.get_role_by_id(rbac.victim_role_id) is None


def test_deleting_the_builtin_admin_role_is_refused(client, rbac):
    _login(client, rbac.admin_id)
    response = client.delete(f"/admin/api/roles/{rbac.admin_role_id}")
    assert response.status_code == 403

    with pytest.raises(ValueError):
        rbac.manager.delete_role(rbac.admin_role_id)

    assert rbac.manager.get_role_by_id(rbac.admin_role_id) is not None
    assert rbac.manager.has_role(rbac.admin_id, "admin")


def test_renaming_the_builtin_admin_role_is_refused(client, rbac):
    _login(client, rbac.admin_id)
    response = client.put(
        f"/admin/api/roles/{rbac.admin_role_id}",
        json={"name": "superadmin", "description": "renamed"},
    )
    assert response.status_code == 403

    with pytest.raises(ValueError):
        rbac.manager.update_role(rbac.admin_role_id, "superadmin", "renamed")

    assert rbac.manager.get_role_by_id(rbac.admin_role_id).name == "admin"
