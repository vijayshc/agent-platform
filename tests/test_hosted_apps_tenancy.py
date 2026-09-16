"""Hosted-app tenancy: serving and deploying obey the canonical resource rule.

Real integration, no mocks: a minimal Flask app registers the shipped
``hosted_app_bp`` together with the real ``login_required``/``module_required``/
``admin_required`` decorators, and the real ``resource_access`` store runs
against an isolated temp SQLite database (the same connection-swap pattern
``tests/conftest.py`` uses). The routes, the SQL and the decorators all execute;
only the database target and the hosting root are pointed at temp locations.

Covers the requirement "hosted apps - only for admin to upload/deploy" while
serving/viewing follows per-app ownership plus role grants:

* the owner can use and manage their app;
* an administrator sees every app and may use any;
* an unrelated tenant neither lists it nor opens it;
* a role-granted user may open it but cannot manage it;
* a non-admin, even holding ``module:hosted_apps``, cannot import/deploy;
* an owner-less legacy app stays usable by module holders.
"""

from __future__ import annotations

import io
import sqlite3
from types import SimpleNamespace

import pytest
from flask import Blueprint, Flask

from src.auth import resource_access
from src.auth.modules import module_permission_name
from src.models.hosted_app import HostedApp
from src.models.user import Base as UserBase

HOSTED_MODULE_PERMISSION = module_permission_name("hosted_apps")

#: Seeded identities: (user id, role id) and the app each one relates to.
ADMIN = 10
OWNER = 11              # owns app-mine
UNRELATED = 12          # owns app-other
GRANTED = 13            # holds role 4, granted app-mine in the granted test
NO_MODULE = 14

#: Roles: 1 admin, 2/3/4 carry the hosted_apps module, 5 carries none.
ROLE_ADMIN = 1
ROLE_OWNER = 2
ROLE_UNRELATED = 3
ROLE_GRANTED = 4
ROLE_NO_MODULE = 5

APP_MINE = "app-mine"
APP_LEGACY = "app-legacy"
APP_OTHER = "app-other"


def _connect(db_path):
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def _seed(db_path) -> None:
    """Real rows in the real schema: roles, users, apps (one owner-less)."""
    conn = _connect(db_path)
    try:
        conn.executescript(
            f"""
            INSERT INTO roles (id, name) VALUES
                ({ROLE_ADMIN}, 'admin'),
                ({ROLE_OWNER}, 'hosted-owner'),
                ({ROLE_UNRELATED}, 'hosted-unrelated'),
                ({ROLE_GRANTED}, 'hosted-granted'),
                ({ROLE_NO_MODULE}, 'no-module');
            INSERT INTO permissions (id, name) VALUES (1, '{HOSTED_MODULE_PERMISSION}');
            INSERT INTO role_permissions (role_id, permission_id) VALUES
                ({ROLE_OWNER}, 1), ({ROLE_UNRELATED}, 1), ({ROLE_GRANTED}, 1);
            INSERT INTO users (id, username, email, password_hash, is_active) VALUES
                ({ADMIN}, 'admin', 'admin@test', 'x', 1),
                ({OWNER}, 'owner', 'owner@test', 'x', 1),
                ({UNRELATED}, 'unrelated', 'unrelated@test', 'x', 1),
                ({GRANTED}, 'granted', 'granted@test', 'x', 1),
                ({NO_MODULE}, 'nomodule', 'nomodule@test', 'x', 1);
            INSERT INTO user_roles (user_id, role_id) VALUES
                ({ADMIN}, {ROLE_ADMIN}),
                ({OWNER}, {ROLE_OWNER}),
                ({UNRELATED}, {ROLE_UNRELATED}),
                ({GRANTED}, {ROLE_GRANTED}),
                ({NO_MODULE}, {ROLE_NO_MODULE});
            """
        )
        conn.execute(
            "INSERT INTO hosted_apps (id, slug, name, entry, created_by) VALUES (1, ?, ?, 'app.py', ?)",
            (APP_MINE, "Mine", OWNER),
        )
        conn.execute(
            "INSERT INTO hosted_apps (id, slug, name, entry, created_by) VALUES (2, ?, ?, 'app.py', NULL)",
            (APP_LEGACY, "Legacy"),
        )
        conn.execute(
            "INSERT INTO hosted_apps (id, slug, name, entry, created_by) VALUES (3, ?, ?, 'app.py', ?)",
            (APP_OTHER, "Other", UNRELATED),
        )
        conn.commit()
    finally:
        conn.close()


@pytest.fixture()
def hosted(temp_db, monkeypatch, tmp_path):
    """A real minimal app serving ``hosted_app_bp`` against an isolated temp DB."""
    import src.models.hosted_app as hosted_app_model
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # The model holds its own reference to the connection helper, so point that
    # one at the temp DB too (conftest patches the generic module).
    monkeypatch.setattr(hosted_app_model, "get_db_connection", lambda: _connect(temp_db))

    # The ORM identity tables (users/roles/permissions + assoc) in the temp DB.
    engine = create_engine(
        f"sqlite:///{temp_db}", connect_args={"check_same_thread": False}
    )
    UserBase.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    monkeypatch.setattr("src.utils.database.get_db_session", lambda: SessionLocal())
    monkeypatch.setattr("src.utils.user_manager.get_db_session", lambda: SessionLocal())

    resource_access._reset_schema_guard()
    HostedApp.create_table()
    resource_access.ensure_tables()

    # Hosting roots are throwaway: never the deployment's real apps root.
    root = tmp_path / "hosted-root"
    root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("src.hosting.settings.root", lambda: root)
    # Serve /apps/* from this blueprint instead of 308-redirecting to a second
    # listener: same-origin is a supported mode, and it keeps the test on the
    # real route/decorator path.
    monkeypatch.setattr("src.hosting.settings.same_origin_mode", lambda: True)

    _seed(temp_db)

    from src.routes.hosted_app_routes import hosted_app_bp

    app = Flask(__name__)
    app.secret_key = "test"

    auth_bp = Blueprint("auth", __name__)

    @auth_bp.route("/login")
    def login():
        return "login"

    app.register_blueprint(auth_bp)

    @app.route("/")
    def index():
        return "home"

    app.register_blueprint(hosted_app_bp)
    client = app.test_client()

    def login_as(user_id: int):
        with client.session_transaction() as session:
            session["user_id"] = user_id
            session["username"] = str(user_id)
        return client

    try:
        yield SimpleNamespace(client=client, login_as=login_as, db=temp_db, root=root)
    finally:
        engine.dispose()
        resource_access._reset_schema_guard()


def _slugs(client) -> set[str]:
    response = client.get("/admin/api/hosted-apps")
    assert response.status_code == 200, response.text[:300]
    return {row["slug"] for row in response.get_json()["apps"]}


# --------------------------------------------------------------------------
# owner
# --------------------------------------------------------------------------

def test_owner_can_use_and_manage_their_app(hosted):
    client = hosted.login_as(OWNER)
    listing = {row["slug"]: row for row in client.get("/admin/api/hosted-apps").get_json()["apps"]}
    assert APP_MINE in listing
    assert listing[APP_MINE]["can_manage"] is True
    assert set(listing[APP_MINE]["access"]) == set()

    # Access passes (the app simply is not running -> 503, never 403).
    served = client.get(f"/apps/{APP_MINE}/")
    assert served.status_code == 503, served.text[:300]
    assert served.get_json()["error"] == "not_running"

    renamed = client.put(f"/admin/api/hosted-apps/{APP_MINE}", json={"name": "Mine Renamed"})
    assert renamed.status_code == 200, renamed.text[:300]
    assert renamed.get_json()["app"]["name"] == "Mine Renamed"
    assert client.get(f"/admin/api/hosted-apps/{APP_MINE}/access").status_code == 200


# --------------------------------------------------------------------------
# administrator
# --------------------------------------------------------------------------

def test_admin_sees_all_and_can_use_any(hosted):
    client = hosted.login_as(ADMIN)
    assert _slugs(client) == {APP_MINE, APP_LEGACY, APP_OTHER}
    for slug in (APP_MINE, APP_LEGACY, APP_OTHER):
        assert client.get(f"/apps/{slug}/").status_code == 503
    # The deploy progress channel is admin-only and this admin may read it.
    assert client.get(f"/admin/api/hosted-apps/{APP_OTHER}/install").status_code == 200


# --------------------------------------------------------------------------
# unrelated tenant
# --------------------------------------------------------------------------

def test_unrelated_tenant_list_excludes_and_serving_is_denied(hosted):
    client = hosted.login_as(UNRELATED)
    slugs = _slugs(client)
    assert APP_OTHER in slugs          # their own app
    assert APP_LEGACY in slugs         # owner-less, grandfathered
    assert APP_MINE not in slugs       # another tenant's app must not leak

    for path in (f"/apps/{APP_MINE}/", f"/apps/{APP_MINE}"):
        response = client.get(path)
        assert response.status_code == 403, f"{path} -> {response.status_code}"
    # A slug that does not exist is a 404, not a leak of whether it exists.
    assert client.get("/apps/no-such-app/").status_code == 404


# --------------------------------------------------------------------------
# role grant: open yes, manage no
# --------------------------------------------------------------------------

def test_granted_role_can_open_but_cannot_manage(hosted):
    client = hosted.login_as(GRANTED)
    assert client.get(f"/apps/{APP_MINE}/").status_code == 403
    assert client.get(f"/apps/{APP_MINE}").status_code == 403
    assert APP_MINE not in _slugs(client)

    # Grant the grantee's role on the app; the same request now passes.
    HostedApp.set_access(APP_MINE, [ROLE_GRANTED], granted_by=ADMIN)

    assert client.get(f"/apps/{APP_MINE}/").status_code == 503
    listed = {row["slug"]: row for row in client.get("/admin/api/hosted-apps").get_json()["apps"]}
    assert APP_MINE in listed
    assert listed[APP_MINE]["can_manage"] is False
    assert set(listed[APP_MINE]["access"]) == {ROLE_GRANTED}

    # Managing it (owning its content, policy, logs or lifecycle) is refused.
    assert client.put(f"/admin/api/hosted-apps/{APP_MINE}", json={"name": "nope"}).status_code == 403
    assert client.get(f"/admin/api/hosted-apps/{APP_MINE}/policy").status_code == 403
    assert client.get(f"/admin/api/hosted-apps/{APP_MINE}/logs").status_code == 403
    assert client.delete(f"/admin/api/hosted-apps/{APP_MINE}").status_code == 403
    assert client.post(f"/admin/api/hosted-apps/{APP_MINE}/start").status_code == 403
    assert client.post(f"/admin/api/hosted-apps/{APP_MINE}/reinstall").status_code == 403


# --------------------------------------------------------------------------
# deploy is administrator-only
# --------------------------------------------------------------------------

def test_non_admin_with_module_cannot_import_or_install(hosted):
    client = hosted.login_as(OWNER)  # has module:hosted_apps and owns an app
    archive = io.BytesIO(b"PK\x03\x04not a real archive")
    imported = client.post(
        "/admin/api/hosted-apps/import",
        data={"file": (archive, "probe.zip")},
        content_type="multipart/form-data",
    )
    assert imported.status_code == 403, imported.text[:300]
    assert client.get(f"/admin/api/hosted-apps/{APP_MINE}/install").status_code == 403

    # The administrator passes both gates and only then fails on the empty body.
    admin = hosted.login_as(ADMIN)
    assert admin.post("/admin/api/hosted-apps/import").status_code == 400


# --------------------------------------------------------------------------
# module gate + owner-less legacy rows
# --------------------------------------------------------------------------

def test_module_gate_still_applies_without_the_module(hosted):
    client = hosted.login_as(NO_MODULE)
    assert client.get("/admin/api/hosted-apps").status_code == 403
    page = client.get(f"/apps/{APP_LEGACY}/")
    assert page.status_code in (302, 303, 403), f"unexpected {page.status_code}"


def test_ownerless_legacy_app_is_usable_but_not_manageable(hosted):
    client = hosted.login_as(OWNER)
    assert client.get(f"/apps/{APP_LEGACY}/").status_code == 503
    assert client.get(f"/apps/{APP_LEGACY}").status_code == 308

    listed = {row["slug"]: row for row in client.get("/admin/api/hosted-apps").get_json()["apps"]}
    assert listed[APP_LEGACY]["can_manage"] is False
    assert client.put(f"/admin/api/hosted-apps/{APP_LEGACY}", json={"name": "x"}).status_code == 403
    assert client.delete(f"/admin/api/hosted-apps/{APP_LEGACY}").status_code == 403
