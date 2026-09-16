"""One generic role-grant store for every tenanted asset type.

Module RBAC lives in ``src/auth/modules.py``; this module adds the *resource*
level.  For every asset the decision is::

    allow iff  the user is an administrator
            OR the user is the asset's owner (``created_by``)
            OR the user holds a role explicitly granted on the asset.

``user_id is None`` always denies.  An asset with no owner
(``created_by IS NULL``) is grandfathered as visible to every authenticated
user.  Otherwise allow iff
``user_role_ids(user_id) & granted_role_ids != empty``.

Grants live in ``resource_role_access`` keyed by
``(resource_type, resource_id, role_id)``; ``RESOURCE_TYPES`` is the canonical
catalog the generic REST API accepts.  The legacy ``agent_definition_role_access``
and ``hosted_app_role_access`` tables are folded in once, idempotently, at
startup by ``migrate_legacy_grants``.

Stores register ``register_resource(type, owner_resolver, exists_resolver=None)``
at import time so the generic API can resolve owners.  Roles/users are read with
the raw SQLite connection (not the ORM): it is the hot path and it keeps the
store usable from the isolated temp-DB test fixture.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Mapping
from typing import Any, Callable

from src.agent_platform import db

#: Canonical asset types the generic access API understands.
RESOURCE_TYPES: tuple[str, ...] = (
    "agent",
    "skill",
    "skill_package",
    "mcp_server",
    "knowledge_document",
    "vector_collection",
    "llm_connection",
    "hosted_app",
)

ADMIN_ROLE_NAME = "admin"

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS resource_role_access (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  resource_type TEXT NOT NULL,
  resource_id INTEGER NOT NULL,
  role_id INTEGER NOT NULL,
  granted_by INTEGER,
  granted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(resource_type, resource_id, role_id)
)
"""

_CREATE_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_resource_role_access_lookup
  ON resource_role_access(resource_type, resource_id)
"""

#: (legacy table, id column, canonical resource type) copied by migration.
_LEGACY_GRANT_TABLES: tuple[tuple[str, str, str], ...] = (
    ("agent_definition_role_access", "agent_id", "agent"),
    ("hosted_app_role_access", "app_id", "hosted_app"),
)

_ENSURED_PATHS: set[str] = set()
_OWNER_RESOLVERS: dict[str, Callable[[int], int | None]] = {}
_EXISTS_RESOLVERS: dict[str, Callable[[int], bool]] = {}


# --- schema ---------------------------------------------------------------

def _reset_schema_guard() -> None:
    """Forget which DB files were ensured (tests switching temp DBs)."""
    _ENSURED_PATHS.clear()


def _db_path(conn) -> str:
    try:
        row = conn.execute("PRAGMA database_list").fetchone()
    except sqlite3.Error:
        return ""
    return str(row[2] or "") if row is not None else ""


def _table_exists(conn, name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)
    ).fetchone() is not None


def ensure_tables() -> None:
    """Create the generic grant table + lookup index once per database file."""
    conn = db.get_db_connection()
    try:
        path = _db_path(conn)
        if path and path in _ENSURED_PATHS:
            return
        conn.execute(_CREATE_TABLE_SQL)
        conn.execute(_CREATE_INDEX_SQL)
        conn.commit()
        if path:
            _ENSURED_PATHS.add(path)
    finally:
        conn.close()


def _write(action: Callable[[Any], None]) -> None:
    """Run ``action(conn)`` in one transaction; roll back on any error."""
    conn = db.get_db_connection()
    try:
        try:
            action(conn)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    finally:
        conn.close()


# --- coercion -------------------------------------------------------------

def _coerce_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _normalize_type(resource_type: Any) -> str | None:
    text = str(resource_type or "").strip().lower()
    return text or None


# --- identity / roles -----------------------------------------------------

def _user_roles(user_id: int | None) -> dict[int, str]:
    """``{role_id: role_name}`` for a user, in one query."""
    uid = _coerce_int(user_id)
    if uid is None:
        return {}
    conn = db.get_db_connection()
    try:
        if not (_table_exists(conn, "roles") and _table_exists(conn, "user_roles")):
            return {}
        rows = conn.execute(
            "SELECT r.id AS id, r.name AS name FROM roles r "
            "JOIN user_roles ur ON ur.role_id = r.id WHERE ur.user_id = ?",
            (uid,),
        ).fetchall()
        return {int(r["id"]): str(r["name"]) for r in rows if r["id"] is not None}
    finally:
        conn.close()


def user_role_ids(user_id: int | None) -> set[int]:
    """Role ids held by the user (empty for ``None`` / unknown user)."""
    return set(_user_roles(user_id).keys())


def user_role_names(user_id: int | None) -> set[str]:
    """Role names held by the user (empty for ``None`` / unknown user)."""
    return {name for name in _user_roles(user_id).values() if name}


def is_admin(user_id: int | None) -> bool:
    """Whether the user holds the built-in ``admin`` role."""
    return ADMIN_ROLE_NAME in _user_roles(user_id).values()


# --- grants ---------------------------------------------------------------

def _granted_role_ids(resource_type: str, resource_id: int) -> set[int]:
    ensure_tables()
    conn = db.get_db_connection()
    try:
        rows = conn.execute(
            "SELECT role_id FROM resource_role_access "
            "WHERE resource_type = ? AND resource_id = ?",
            (resource_type, resource_id),
        ).fetchall()
        return {int(r["role_id"]) for r in rows}
    finally:
        conn.close()


def _granted_by_resource(resource_type: str) -> dict[int, set[int]]:
    """Every granted ``{resource_id: {role_id}}`` pair for a type, one query."""
    ensure_tables()
    conn = db.get_db_connection()
    try:
        rows = conn.execute(
            "SELECT resource_id, role_id FROM resource_role_access WHERE resource_type = ?",
            (resource_type,),
        ).fetchall()
    finally:
        conn.close()
    grants: dict[int, set[int]] = {}
    for row in rows:
        rid, role = _coerce_int(row["resource_id"]), _coerce_int(row["role_id"])
        if rid is not None and role is not None:
            grants.setdefault(rid, set()).add(role)
    return grants


def _valid_role_ids(role_ids: Any) -> list[int]:
    """Coerce to a de-duplicated list of *existing* role ids (unknown dropped)."""
    if role_ids is None or isinstance(role_ids, (str, bytes)):
        return []
    if not isinstance(role_ids, Iterable):
        return []
    candidates: list[int] = []
    for raw in role_ids:
        rid = _coerce_int(raw)
        if rid is not None and rid not in candidates:
            candidates.append(rid)
    if not candidates:
        return []
    conn = db.get_db_connection()
    try:
        if not _table_exists(conn, "roles"):
            return candidates
        placeholders = ",".join("?" for _ in candidates)
        rows = conn.execute(
            f"SELECT id FROM roles WHERE id IN ({placeholders})", candidates
        ).fetchall()
        known = {int(r["id"]) for r in rows}
        return [rid for rid in candidates if rid in known]
    finally:
        conn.close()


def list_access(resource_type: str, resource_id: int) -> list[dict]:
    """Granted roles for one resource: ``[{"role_id", "role_name"}]``."""
    rtype, rid = _normalize_type(resource_type), _coerce_int(resource_id)
    if rtype is None or rid is None:
        return []
    ensure_tables()
    conn = db.get_db_connection()
    try:
        if _table_exists(conn, "roles"):
            rows = conn.execute(
                "SELECT a.role_id AS role_id, "
                "COALESCE(r.name, 'role-' || a.role_id) AS role_name "
                "FROM resource_role_access a LEFT JOIN roles r ON r.id = a.role_id "
                "WHERE a.resource_type = ? AND a.resource_id = ? "
                "ORDER BY r.name, a.role_id",
                (rtype, rid),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT role_id AS role_id, 'role-' || role_id AS role_name "
                "FROM resource_role_access WHERE resource_type = ? AND resource_id = ? "
                "ORDER BY role_id",
                (rtype, rid),
            ).fetchall()
        return [
            {"role_id": int(r["role_id"]), "role_name": str(r["role_name"])}
            for r in rows
        ]
    finally:
        conn.close()


def set_access(
    resource_type: str,
    resource_id: int,
    role_ids,
    granted_by: int | None = None,
) -> list[dict]:
    """Replace the full grant set atomically; unknown role ids are ignored."""
    rtype, rid = _normalize_type(resource_type), _coerce_int(resource_id)
    if rtype is None or rid is None:
        return []
    ensure_tables()
    wanted, granter = _valid_role_ids(role_ids), _coerce_int(granted_by)

    def _do(conn):
        conn.execute(
            "DELETE FROM resource_role_access WHERE resource_type = ? AND resource_id = ?",
            (rtype, rid),
        )
        for role_id in wanted:
            conn.execute(
                "INSERT OR IGNORE INTO resource_role_access "
                "(resource_type, resource_id, role_id, granted_by) VALUES (?, ?, ?, ?)",
                (rtype, rid, role_id, granter),
            )

    _write(_do)
    return list_access(rtype, rid)


def grant_access(
    resource_type: str,
    resource_id: int,
    role_id: int,
    granted_by: int | None = None,
) -> list[dict]:
    """Add one role grant (idempotent); returns the new access list."""
    rtype, rid = _normalize_type(resource_type), _coerce_int(resource_id)
    if rtype is None or rid is None:
        return []
    ensure_tables()
    wanted = _valid_role_ids([role_id])
    if not wanted:
        return list_access(rtype, rid)
    _write(lambda conn: conn.execute(
        "INSERT OR IGNORE INTO resource_role_access "
        "(resource_type, resource_id, role_id, granted_by) VALUES (?, ?, ?, ?)",
        (rtype, rid, wanted[0], _coerce_int(granted_by)),
    ))
    return list_access(rtype, rid)


def revoke_access(resource_type: str, resource_id: int, role_id: int) -> list[dict]:
    """Remove one role grant (idempotent); returns the new access list."""
    rtype, rid = _normalize_type(resource_type), _coerce_int(resource_id)
    target = _coerce_int(role_id)
    if rtype is None or rid is None or target is None:
        return []
    ensure_tables()
    _write(lambda conn: conn.execute(
        "DELETE FROM resource_role_access "
        "WHERE resource_type = ? AND resource_id = ? AND role_id = ?",
        (rtype, rid, target),
    ))
    return list_access(rtype, rid)


# --- access decisions -----------------------------------------------------

def can_access(
    resource_type: str,
    resource_id: int,
    owner_id: int | None,
    user_id: int | None,
) -> bool:
    """The canonical resource-access decision (see module docstring)."""
    uid = _coerce_int(user_id)
    if uid is None:
        return False
    if is_admin(uid):
        return True
    owner = _coerce_int(owner_id)
    if owner is None or owner == uid:
        return True
    rtype, rid = _normalize_type(resource_type), _coerce_int(resource_id)
    if rtype is None or rid is None:
        return False
    return bool(user_role_ids(uid) & _granted_role_ids(rtype, rid))


def _row_mapping(row: Any) -> Mapping:
    if isinstance(row, Mapping):
        return row
    try:
        return dict(row)
    except (TypeError, ValueError):
        return {}


def filter_visible(
    resource_type: str,
    rows: list[dict],
    user_id: int | None,
    owner_key: str = "created_by",
    id_key: str = "id",
) -> list[dict]:
    """The subset of ``rows`` the user may see, preserving input order.

    Exactly two queries regardless of ``len(rows)``: the caller's roles once,
    then every granted ``(resource_id, role_id)`` pair for the type once.  Rows
    are plain dicts (a missing key cannot match a grant); a row with no owner is
    grandfathered in.
    """
    if not rows:
        return []
    uid = _coerce_int(user_id)
    if uid is None:
        return []
    roles = _user_roles(uid)
    if ADMIN_ROLE_NAME in roles.values():
        return list(rows)
    role_ids = set(roles.keys())
    rtype = _normalize_type(resource_type)
    grants = _granted_by_resource(rtype) if rtype else {}
    visible: list[dict] = []
    for row in rows:
        data = _row_mapping(row)
        owner = _coerce_int(data.get(owner_key))
        if owner is None or owner == uid:
            visible.append(row)
            continue
        rid = _coerce_int(data.get(id_key))
        if role_ids and rid is not None and role_ids & grants.get(rid, set()):
            visible.append(row)
    return visible


# --- owner registry (used by the generic access REST API) -----------------

def register_resource(
    resource_type: str,
    owner_resolver: Callable[[int], int | None],
    exists_resolver: Callable[[int], bool] | None = None,
) -> None:
    """Register owner (and optionally existence) resolution; replaces on re-register.

    ``exists_resolver`` is additive: without it an owner-less resource is
    reported absent by :func:`resource_exists` (visible to all, but not
    manageable through the generic API).  With it, an owner-less-but-existing
    resource can be managed by an administrator.
    """
    rtype = _normalize_type(resource_type)
    if rtype is None:
        raise ValueError("resource_type is required")
    if not callable(owner_resolver):
        raise TypeError("owner_resolver must be callable")
    _OWNER_RESOLVERS[rtype] = owner_resolver
    if exists_resolver is None:
        _EXISTS_RESOLVERS.pop(rtype, None)
        return
    if not callable(exists_resolver):
        raise TypeError("exists_resolver must be callable")
    _EXISTS_RESOLVERS[rtype] = exists_resolver


def is_registered(resource_type: str) -> bool:
    """Whether an owner resolver has been registered for this type."""
    rtype = _normalize_type(resource_type)
    return bool(rtype and rtype in _OWNER_RESOLVERS)


def owner_of(resource_type: str, resource_id: int) -> int | None:
    """Resolve the owner user id for a resource, or ``None`` when unknown."""
    rtype, rid = _normalize_type(resource_type), _coerce_int(resource_id)
    if rtype is None or rid is None:
        return None
    resolver = _OWNER_RESOLVERS.get(rtype)
    return _coerce_int(resolver(rid)) if resolver is not None else None


def resource_exists(resource_type: str, resource_id: int) -> bool:
    """Whether the resource row exists (see :func:`register_resource`)."""
    rtype, rid = _normalize_type(resource_type), _coerce_int(resource_id)
    if rtype is None or rid is None:
        return False
    exists = _EXISTS_RESOLVERS.get(rtype)
    if exists is not None:
        return bool(exists(rid))
    return owner_of(rtype, rid) is not None


# --- legacy migration -----------------------------------------------------

def migrate_legacy_grants() -> dict:
    """Copy legacy per-asset grants into ``resource_role_access`` once.

    Idempotent (``INSERT OR IGNORE``) and safe on every startup: each summary
    entry is the number of rows *newly inserted* by this call, so a second call
    reports ``0``.
    """
    ensure_tables()
    summary: dict[str, int] = {rtype: 0 for _, _, rtype in _LEGACY_GRANT_TABLES}
    conn = db.get_db_connection()
    try:
        try:
            for table, id_column, rtype in _LEGACY_GRANT_TABLES:
                if not _table_exists(conn, table):
                    continue
                cursor = conn.execute(
                    f"INSERT OR IGNORE INTO resource_role_access "
                    f"(resource_type, resource_id, role_id, granted_by) "
                    f"SELECT ?, {id_column}, role_id, granted_by FROM {table}",
                    (rtype,),
                )
                summary[rtype] = max(int(cursor.rowcount or 0), 0)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    finally:
        conn.close()
    return summary
