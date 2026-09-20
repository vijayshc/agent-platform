"""Skills tenancy: real SQL on a temp SQLite DB; only the connection target and
the orthogonal module gate are swapped. Both stores run their real paths —
legacy ``skills`` + ``/api/skills`` and on-disk ``maf_skills`` + ``/api/v1/skills``.
Delete/write is owner/admin only; a role grant is view/use, never control.
"""

from __future__ import annotations

import io
import sqlite3
import zipfile
from types import SimpleNamespace

import pytest

from src.auth import resource_access
# Import what conftest's temp_db rebinds before any fixture runs (see conftest).
import src.models.llm_connection  # noqa: F401
import src.models.mcp_server  # noqa: F401

SCHEMA = """
CREATE TABLE IF NOT EXISTS roles (
    id INTEGER PRIMARY KEY,
    name TEXT UNIQUE NOT NULL,
    description TEXT
);
CREATE TABLE IF NOT EXISTS user_roles (
    user_id INTEGER,
    role_id INTEGER
);
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    username TEXT UNIQUE NOT NULL,
    email TEXT,
    password_hash TEXT,
    is_active INTEGER DEFAULT 1
);
"""

ADMIN_ID = 10       # holds role "admin"
OWNER_ID = 11       # holds no role
ANALYST_ID = 12     # holds role "analyst"
OTHER_ID = 13       # holds role "other"

#: Packages the platform itself ships in ``src/agent_platform/skills``. They have
#: no owner row of their own, so they are grandfathered-visible to every user --
#: which is why every "what can this user see" assertion below includes them.
#: ``chart-rendering`` is the prompt the runtime loads by fixed name into any
#: agent whose tool opted into data sampling (``runtime.tool_data.prompt``).
SHIPPED_PACKAGES = {"chart-rendering"}


def _connect(db_path):
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


@pytest.fixture()
def skills_env(temp_db, monkeypatch):
    """Temp DB with roles/users plus both skill tables, isolated per test."""
    monkeypatch.setattr(
        "src.models.skill.get_db_connection", lambda: _connect(temp_db)
    )
    resource_access._reset_schema_guard()

    conn = _connect(temp_db)
    conn.executescript(SCHEMA)
    conn.executemany("INSERT INTO roles (id, name) VALUES (?, ?)", [
        (1, "admin"), (2, "user"), (3, "analyst"), (4, "other"),
    ])
    conn.executemany("INSERT INTO user_roles (user_id, role_id) VALUES (?, ?)", [
        (ADMIN_ID, 1), (ANALYST_ID, 3), (OTHER_ID, 4),
    ])
    conn.executemany(
        "INSERT INTO users (id, username, email, password_hash) VALUES (?, ?, ?, ?)",
        [(1, "admin", "admin@test", "x"), (OWNER_ID, "alice", "alice@test", "x")],
    )
    conn.commit()
    conn.close()

    from src.models.skill import Skill

    Skill.create_table()
    from src.agent_platform.catalog.skills_store import MafSkillStore

    MafSkillStore.ensure_tables()

    yield temp_db
    resource_access._reset_schema_guard()


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _add_legacy_skill(name, *, owner_id=None, created_by=None, status="active", category="other"):
    from src.models.skill import Skill

    skill = Skill(
        name=name, description=f"{name} description", category=category,
        steps=["do it"], status=status, created_by=created_by, owner_id=owner_id,
    )
    skill.save()
    return skill


def _make_package_dir(tmp_path, name, body_name=None):
    root = tmp_path / "uploads" / "maf-skills" / name
    root.mkdir(parents=True, exist_ok=True)
    (root / "SKILL.md").write_text(
        f"---\nname: {body_name or name}\ndescription: {name}\n---\n\nbody\n",
        encoding="utf-8",
    )
    return root


def _add_package(tmp_path, name, *, created_by=None):
    from src.agent_platform.catalog.skills_store import MafSkillStore

    root = _make_package_dir(tmp_path, name)
    return MafSkillStore.upsert(name, f"{name} description", str(root), created_by=created_by)


def _add_seed_package(tmp_path, name, *, created_by=None):
    """A package inside the user skills dir; a row only when ``created_by`` is set."""
    from src.agent_platform.catalog.skills_store import MafSkillStore

    root = tmp_path / "uploads" / "maf-skills" / name
    root.mkdir(parents=True)
    (root / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: seeded\n---\n\nbody\n", encoding="utf-8"
    )
    if created_by is not None:
        MafSkillStore.upsert(name, f"{name} description", str(root), created_by=created_by)
    return root


def _add_user_dir_package(tmp_path, name, *, created_by=None):
    return _add_seed_package(tmp_path, name, created_by=created_by)


def _legacy_client(monkeypatch, user_id):
    from flask import Flask

    from src.routes.skill_routes import skill_bp

    monkeypatch.setattr(
        "src.utils.user_manager.UserManager.has_any_module_access",
        lambda self, uid, keys, min_level="read": True,
    )
    app = Flask(__name__)
    app.secret_key = "test"
    app.register_blueprint(skill_bp)
    client = app.test_client()
    with client.session_transaction() as session:
        session["user_id"] = user_id
    return client


def _package_client(monkeypatch, user_id, modules_by_user=None):
    """Real package blueprint; the orthogonal module gate is stubbed."""
    from flask import Flask

    from src.agent_platform.api.access_routes import access_bp
    from src.agent_platform.api.skill_package_routes import skill_package_bp

    def _has_any(self, uid, keys, min_level="read"):
        granted = {"skills", "agent_studio"} if modules_by_user is None else modules_by_user.get(int(uid), set())
        return bool(set(keys) & set(granted))

    monkeypatch.setattr(
        "src.utils.user_manager.UserManager.has_any_module_access", _has_any
    )
    app = Flask(__name__)
    app.secret_key = "test"
    app.register_blueprint(skill_package_bp, url_prefix="/api/v1")
    app.register_blueprint(access_bp, url_prefix="/api/v1")
    client = app.test_client()
    with client.session_transaction() as session:
        session["user_id"] = user_id
    return client


# --------------------------------------------------------------------------
# legacy store: visible / access decisions
# --------------------------------------------------------------------------

def test_legacy_owner_sees_and_uses_own_skill(skills_env):
    from src.models.skill import can_access_skill_id, visible_skill_ids

    own = _add_legacy_skill("alice-skill", owner_id=OWNER_ID)
    other = _add_legacy_skill("bob-skill", owner_id=ANALYST_ID)

    assert visible_skill_ids(OWNER_ID) == {own.id}
    assert can_access_skill_id(own.id, OWNER_ID)
    assert not can_access_skill_id(other.id, OWNER_ID)
    assert visible_skill_ids(ANALYST_ID) == {other.id}


def test_legacy_admin_sees_all(skills_env):
    from src.models.skill import can_access_skill_id, visible_skill_ids

    first = _add_legacy_skill("one", owner_id=OWNER_ID)
    second = _add_legacy_skill("two", owner_id=ANALYST_ID)

    assert visible_skill_ids(ADMIN_ID) is None
    assert can_access_skill_id(first.id, ADMIN_ID)
    assert can_access_skill_id(second.id, ADMIN_ID)


def test_legacy_unrelated_user_is_denied(skills_env):
    from src.models.skill import can_access_skill_id, visible_skill_ids

    skill = _add_legacy_skill("private", owner_id=OWNER_ID)
    assert visible_skill_ids(OTHER_ID) == set()
    assert not can_access_skill_id(skill.id, OTHER_ID)
    # Unauthenticated always denies, even for an owner-less row.
    assert visible_skill_ids(None) == set()
    assert not can_access_skill_id(skill.id, None)


def test_legacy_owner_null_grandfathered(skills_env):
    from src.models.skill import can_access_skill_id, visible_skill_ids

    legacy = _add_legacy_skill("legacy", owner_id=None, created_by="ghost")
    assert legacy.id in visible_skill_ids(ANALYST_ID)
    assert can_access_skill_id(legacy.id, OTHER_ID)
    # Grandfathered for viewing, but not destructive control.
    from src.models.skill import can_manage_skill_id

    assert not can_manage_skill_id(legacy.id, ANALYST_ID)
    assert can_manage_skill_id(legacy.id, ADMIN_ID)


def test_legacy_role_grant_and_replace(skills_env):
    from src.models.skill import can_access_skill_id, visible_skill_ids

    skill = _add_legacy_skill("shared", owner_id=OWNER_ID)
    resource_access.set_access("skill", skill.id, [3], granted_by=ADMIN_ID)

    assert skill.id in visible_skill_ids(ANALYST_ID)
    assert can_access_skill_id(skill.id, ANALYST_ID)
    assert skill.id not in visible_skill_ids(OTHER_ID)

    # Replacing the grant set moves access to the "other" role.
    resource_access.set_access("skill", skill.id, [4], granted_by=ADMIN_ID)
    assert skill.id not in visible_skill_ids(ANALYST_ID)
    assert skill.id in visible_skill_ids(OTHER_ID)
    # A granted role confers use, never destructive control.
    from src.models.skill import can_manage_skill_id

    assert not can_manage_skill_id(skill.id, OTHER_ID)


def test_legacy_owner_backfill_from_username(skills_env):
    """The migration resolves the stored username to a user id, else leaves NULL."""
    conn = _connect(skills_env)
    conn.executemany(
        "INSERT INTO skills (skill_id, name, description, category, steps, created_by, owner_id) "
        "VALUES (?, ?, 'd', 'other', '[]', ?, NULL)",
        [("uuid-1", "resolved", "alice"), ("uuid-2", "unresolved", "ghost")],
    )
    conn.commit()
    conn.close()

    from src.models.skill import Skill

    Skill.create_table()  # idempotent: runs the backfill

    conn = _connect(skills_env)
    rows = {r["name"]: r["owner_id"] for r in conn.execute("SELECT name, owner_id FROM skills")}
    conn.close()
    assert rows["resolved"] == OWNER_ID
    assert rows["unresolved"] is None


def test_owner_resolvers_registered(skills_env):
    from src.models.skill import Skill
    from src.agent_platform.catalog.skill_packages import (
        SKILL_PACKAGE_RESOURCE_TYPE,
        register_skill_access,
    )

    register_skill_access()
    register_skill_access()  # idempotent

    legacy = _add_legacy_skill("registered", owner_id=OWNER_ID)
    package = _add_package(skills_env.parent, "pkg-registered", created_by=OWNER_ID)

    assert resource_access.is_registered("skill")
    assert resource_access.is_registered(SKILL_PACKAGE_RESOURCE_TYPE)
    assert resource_access.owner_of("skill", legacy.id) == OWNER_ID
    assert resource_access.resource_exists("skill", legacy.id)
    assert not resource_access.resource_exists("skill", 10_000)
    assert resource_access.owner_of(SKILL_PACKAGE_RESOURCE_TYPE, package["id"]) == OWNER_ID
    assert resource_access.resource_exists(SKILL_PACKAGE_RESOURCE_TYPE, package["id"])
    assert not resource_access.resource_exists(SKILL_PACKAGE_RESOURCE_TYPE, 10_000)


# --------------------------------------------------------------------------
# package store: visible / access decisions
# --------------------------------------------------------------------------

def test_package_owner_and_grandfather(skills_env):
    from src.agent_platform.catalog.skill_packages import (
        can_access_skill,
        can_manage_skill,
        visible_skill_names,
    )

    _add_package(skills_env.parent, "owned-pkg", created_by=OWNER_ID)
    _add_seed_package(skills_env.parent, "seed-pkg")

    owner_names = visible_skill_names(OWNER_ID)
    assert {"owned-pkg", "seed-pkg"} <= owner_names
    unrelated = visible_skill_names(ANALYST_ID)
    assert unrelated == {"seed-pkg"} | SHIPPED_PACKAGES

    assert can_access_skill("seed-pkg", OTHER_ID)          # no owner -> grandfathered
    assert not can_access_skill("owned-pkg", ANALYST_ID)
    assert can_access_skill("owned-pkg", OWNER_ID)
    assert can_access_skill("owned-pkg", ADMIN_ID)
    assert visible_skill_names(ADMIN_ID) is None           # admin -> unfiltered
    assert can_manage_skill("owned-pkg", OWNER_ID)
    assert not can_manage_skill("owned-pkg", ANALYST_ID)
    assert can_manage_skill("owned-pkg", ADMIN_ID)
    assert not can_manage_skill("seed-pkg", ANALYST_ID)


def test_package_role_grant_and_replace(skills_env):
    from src.agent_platform.catalog.skill_packages import (
        can_access_skill,
        can_manage_skill,
        visible_skill_names,
    )

    package = _add_package(skills_env.parent, "granted-pkg", created_by=OWNER_ID)
    resource_access.set_access("skill_package", package["id"], [3], granted_by=ADMIN_ID)

    assert "granted-pkg" in visible_skill_names(ANALYST_ID)
    assert can_access_skill("granted-pkg", ANALYST_ID)
    assert "granted-pkg" not in visible_skill_names(OTHER_ID)
    assert not can_manage_skill("granted-pkg", ANALYST_ID)

    resource_access.set_access("skill_package", package["id"], [4], granted_by=ADMIN_ID)
    assert "granted-pkg" not in visible_skill_names(ANALYST_ID)
    assert "granted-pkg" in visible_skill_names(OTHER_ID)


def test_upsert_and_write_package_stamp_owner(skills_env):
    from src.agent_platform.catalog.skill_packages import read_package, write_package
    from src.agent_platform.catalog.skills_store import MafSkillStore

    row = MafSkillStore.upsert("stamped", "d", str(_make_package_dir(skills_env.parent, "stamped")), created_by=OWNER_ID)
    assert row["created_by"] == OWNER_ID
    # A later touch without an actor must not steal/clear the owner.
    again = MafSkillStore.upsert("stamped", "d2", str(_make_package_dir(skills_env.parent, "stamped")), created_by=None)
    assert again["created_by"] == OWNER_ID

    write_package(name="written", description="d", instructions="i", created_by=OWNER_ID)
    assert read_package("written")["created_by"] == OWNER_ID

    from src.agent_platform.catalog.skill_artifacts import ensure_package

    ensure_package("ensured", created_by=ANALYST_ID)
    assert MafSkillStore.get_by_name("ensured")["created_by"] == ANALYST_ID
    ensure_package("ensured-2", created_by=ANALYST_ID)
    assert MafSkillStore.get_by_name("ensured-2")["created_by"] == ANALYST_ID


# --------------------------------------------------------------------------
# legacy HTTP routes: filter lists, 403 direct mutate
# --------------------------------------------------------------------------

def test_legacy_routes_filter_and_deny(skills_env, monkeypatch):
    owner_client = _legacy_client(monkeypatch, OWNER_ID)
    related_client = _legacy_client(monkeypatch, ANALYST_ID)
    admin_client = _legacy_client(monkeypatch, ADMIN_ID)

    created = owner_client.post("/api/skills", json={
        "name": "route-owned",
        "description": "owned through the route",
        "category": "other",
        "steps": ["one"],
        "status": "draft",
    })
    assert created.status_code == 200, created.get_data(as_text=True)
    payload = created.json["skill"]
    assert payload["owner_id"] == OWNER_ID
    skill_id = payload["skill_id"]

    assert len(owner_client.get("/api/skills?status=draft").json["skills"]) == 1
    assert related_client.get("/api/skills?status=draft").json["skills"] == []
    assert len(admin_client.get("/api/skills?status=draft").json["skills"]) == 1

    assert owner_client.get(f"/api/skills/{skill_id}").status_code == 200
    assert related_client.get(f"/api/skills/{skill_id}").status_code == 403
    assert related_client.put(
        f"/api/skills/{skill_id}", json={"description": "hijack"}
    ).status_code == 403
    assert related_client.delete(f"/api/skills/{skill_id}").status_code == 403

    # Admin may manage, but we stop before the vector store: no need to embed.
    assert admin_client.get(f"/api/skills/{skill_id}").status_code == 200
    assert related_client.get("/api/skills/does-not-exist").status_code == 404

    # The denied PUT changed nothing.
    from src.models.skill import Skill

    assert Skill.get_by_id(skill_id).description == "owned through the route"


def test_legacy_import_stamps_owner(skills_env, monkeypatch):
    from src.models.skill import Skill

    # Stub only vectorizer construction; the DB/owner path stays real.
    monkeypatch.setattr(
        "src.routes.skill_routes._skill_vectorizer",
        SimpleNamespace(add_skill=lambda skill: True),
    )
    client = _legacy_client(monkeypatch, OWNER_ID)
    response = client.post("/api/skills/import", json={"skills": [{
        "name": "imported",
        "description": "d",
        "category": "other",
        "steps": ["one"],
        "status": "draft",
    }]})
    assert response.status_code == 200, response.get_data(as_text=True)
    assert response.json["imported_count"] == 1
    rows = [skill for skill in Skill.get_all() if skill.name == "imported"]
    assert len(rows) == 1 and rows[0].owner_id == OWNER_ID


# --------------------------------------------------------------------------
# package HTTP routes: filtered overview + 403 direct access
# --------------------------------------------------------------------------

def test_package_overview_filters_and_denies(skills_env, monkeypatch):
    owned = _add_package(skills_env.parent, "mine", created_by=OWNER_ID)
    theirs = _add_package(skills_env.parent, "theirs", created_by=ANALYST_ID)
    _add_seed_package(skills_env.parent, "seeded")

    owner_client = _package_client(monkeypatch, OWNER_ID)
    analyst_client = _package_client(monkeypatch, ANALYST_ID)
    admin_client = _package_client(monkeypatch, ADMIN_ID)

    owner_view = owner_client.get("/api/v1/skills/overview").json
    owner_names = {pkg["name"] for pkg in owner_view["packages"]}
    assert owner_names == {"mine", "seeded"} | SHIPPED_PACKAGES
    mine = next(pkg for pkg in owner_view["packages"] if pkg["name"] == "mine")
    assert mine["id"] == owned["id"] and mine["can_manage"] is True
    seeded = next(pkg for pkg in owner_view["packages"] if pkg["name"] == "seeded")
    assert seeded["can_manage"] is False

    analyst_view = analyst_client.get("/api/v1/skills/overview").json
    assert {pkg["name"] for pkg in analyst_view["packages"]} == {"theirs", "seeded"} | SHIPPED_PACKAGES

    admin_view = admin_client.get("/api/v1/skills/overview").json
    assert {pkg["name"] for pkg in admin_view["packages"]} == {"mine", "theirs", "seeded"} | SHIPPED_PACKAGES

    # Direct object access: owner/admin 200, unrelated 403, missing 404.
    assert owner_client.get("/api/v1/skills/mine/tree").status_code == 200
    assert owner_client.get("/api/v1/skills/theirs/tree").status_code == 403
    assert analyst_client.get("/api/v1/skills/theirs/tree").status_code == 200
    assert owner_client.get("/api/v1/skills/nope/tree").status_code == 404
    assert owner_client.get("/api/v1/skills/theirs/export").status_code == 403
    assert owner_client.put("/api/v1/skills/theirs/file?path=SKILL.md", json={"content": "x"}).status_code == 403
    assert owner_client.delete("/api/v1/skills/theirs/file?path=SKILL.md").status_code == 403
    # Whole-package delete: owner/admin only.
    assert owner_client.delete("/api/v1/skills/theirs/package").status_code == 403
    assert owner_client.delete("/api/v1/skills/mine/package").status_code == 200

    # The page's own `skills` module suffices; a caller with neither is 403.
    mods = {OWNER_ID: {"skills"}, ANALYST_ID: set(), OTHER_ID: {"agent_studio"}}
    assert _package_client(monkeypatch, OWNER_ID, mods).get("/api/v1/skills/overview").status_code == 200
    assert _package_client(monkeypatch, ANALYST_ID, mods).get("/api/v1/skills/overview").status_code == 403
    assert _package_client(monkeypatch, OTHER_ID, mods).get("/api/v1/skills/overview").status_code == 200


def test_package_create_stamps_owner_and_grants(skills_env, monkeypatch):
    client = _package_client(monkeypatch, OWNER_ID)
    response = client.post("/api/v1/skills/brand-new/create", json={"description": "fresh"})
    assert response.status_code == 201, response.get_data(as_text=True)

    from src.agent_platform.catalog.skills_store import MafSkillStore

    row = MafSkillStore.get_by_name("brand-new")
    assert row is not None and row["created_by"] == OWNER_ID

    # The generic access API reads/writes the package's grants.
    grants = client.get(f"/api/v1/access/skill_package/{row['id']}")
    assert grants.status_code == 200, grants.get_data(as_text=True)

    saved = client.put(
        f"/api/v1/access/skill_package/{row['id']}", json={"role_ids": [3]}
    )
    assert saved.status_code == 200
    assert saved.json["access"] == [{"role_id": 3, "role_name": "analyst"}]
    re_read = client.get(f"/api/v1/access/skill_package/{row['id']}")
    assert [e["role_id"] for e in re_read.json["access"]] == [3]

    # A granted role now sees/uses the package, but cannot manage it.
    from src.agent_platform.catalog.skill_packages import can_manage_skill, visible_skill_names

    assert "brand-new" in visible_skill_names(ANALYST_ID)
    assert not can_manage_skill("brand-new", ANALYST_ID)

    # A grant is view/use only: reads are 200, every write is 403.
    analyst = _package_client(monkeypatch, ANALYST_ID)
    admin = _package_client(monkeypatch, ADMIN_ID)
    assert analyst.get("/api/v1/skills/brand-new/tree").status_code == 200
    assert analyst.get("/api/v1/skills/brand-new/file?path=SKILL.md").status_code == 200
    assert analyst.put("/api/v1/skills/brand-new/file?path=SKILL.md", json={"content": "x"}).status_code == 403
    assert analyst.post("/api/v1/skills/brand-new/folder", json={"path": "d"}).status_code == 403
    assert analyst.delete("/api/v1/skills/brand-new/file?path=SKILL.md").status_code == 403
    assert analyst.delete("/api/v1/skills/brand-new/package").status_code == 403
    # Owner and admin may still mutate.
    assert admin.put("/api/v1/skills/brand-new/file?path=notes.md", json={"content": "ok"}).status_code == 200
    assert client.post("/api/v1/skills/brand-new/folder", json={"path": "assets"}).status_code == 201
    assert client.delete("/api/v1/skills/brand-new/file?path=notes.md").status_code == 200


def test_package_zip_import_stamps_owner_and_blocks_cross_tenant_replace(skills_env, monkeypatch):
    def _zip_bytes(name: str, body: str) -> io.BytesIO:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("SKILL.md", f"---\nname: {name}\ndescription: z\n---\n\n{body}\n")
            archive.writestr("scripts/run.py", "print('hi')\n")
        buffer.seek(0)
        return buffer

    from src.agent_platform.catalog.skills_store import MafSkillStore

    owner_client = _package_client(monkeypatch, OWNER_ID)
    first = owner_client.post(
        "/api/v1/skills/import",
        data={"file": (_zip_bytes("zip-skill", "v1"), "zip-skill.zip")},
        content_type="multipart/form-data",
    )
    assert first.status_code == 201, first.get_data(as_text=True)
    assert [item["name"] for item in first.json["imported"]] == ["zip-skill"]
    assert MafSkillStore.get_by_name("zip-skill")["created_by"] == OWNER_ID

    analyst_client = _package_client(monkeypatch, ANALYST_ID)
    second = analyst_client.post(
        "/api/v1/skills/import",
        data={
            "file": (_zip_bytes("zip-skill", "v2"), "zip-skill.zip"),
            "replace": "1",
        },
        content_type="multipart/form-data",
    )
    assert second.status_code == 201, second.get_data(as_text=True)
    assert second.json["imported"] == []
    assert any(
        conflict["name"] == "zip-skill" and conflict["reason"] == "owned by another user"
        for conflict in second.json["conflicts"]
    )
    # The original owner and contents are untouched.
    assert MafSkillStore.get_by_name("zip-skill")["created_by"] == OWNER_ID
    from src.agent_platform.catalog.skill_packages import read_package

    assert "v1" in read_package("zip-skill")["skill_md"]


# --------------------------------------------------------------------------
# runtime: an agent run only loads skills the running user may access
# --------------------------------------------------------------------------

def test_runtime_skills_provider_is_filtered(skills_env):
    from src.agent_platform.plugins.skills.file_skills import FileSkillsPlugin

    _add_package(skills_env.parent, "owned-pkg", created_by=OWNER_ID)
    _add_seed_package(skills_env.parent, "seed-pkg")

    spec = {"skill_ids": ["owned-pkg", "seed-pkg"]}
    plugin = FileSkillsPlugin()

    owner_provider = plugin.compile(spec, SimpleNamespace(user_id=OWNER_ID))
    assert set(owner_provider.skills) == {"owned-pkg", "seed-pkg"}

    unrelated_provider = plugin.compile(spec, SimpleNamespace(user_id=ANALYST_ID))
    assert set(unrelated_provider.skills) == {"seed-pkg"}

    admin_provider = plugin.compile(spec, SimpleNamespace(user_id=ADMIN_ID))
    assert {"owned-pkg", "seed-pkg"} <= set(admin_provider.skills)

    assert plugin.compile(spec, SimpleNamespace(user_id=None)).skills == {}


def test_runtime_default_library_is_filtered(skills_env, monkeypatch):
    """With no explicit ids the plugin still filters a directory root."""
    import src.agent_platform.plugins.skills.file_skills as file_skills

    _add_user_dir_package(skills_env.parent, "dir-seed")
    _add_user_dir_package(skills_env.parent, "dir-owned", created_by=OWNER_ID)
    monkeypatch.setattr(
        file_skills, "SKILLS_DIR", skills_env.parent / "uploads" / "maf-skills"
    )
    plugin = file_skills.FileSkillsPlugin()

    assert set(plugin.compile({}, SimpleNamespace(user_id=ANALYST_ID)).skills) == {"dir-seed"}
    assert set(plugin.compile({}, SimpleNamespace(user_id=ADMIN_ID)).skills) == {"dir-seed", "dir-owned"}
