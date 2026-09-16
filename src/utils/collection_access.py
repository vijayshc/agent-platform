"""Access control for vector-database collections.

The shared foundation (:mod:`src.auth.resource_access`) is the single source of
truth for *who* may use an asset, but it keys grants by an integer resource id.
Vector collections are named, so this module keeps the smallest possible
bridge: a registry mapping a collection **name** to a stable integer
``access_id`` and its ``owner_id``.  All policy decisions are delegated to the
shared store -- :func:`resource_access.filter_visible` for listing and
:func:`resource_access.can_access` for a single name -- so granting a role on a
collection is the exact same operation (and the exact same
``/api/v1/access/vector_collection/<access_id>`` endpoint and
``ResourceAccessControl`` UI) as every other tenanted asset.

Deny-by-default
---------------
Collections are created and granted by an administrator on the Vector DB page.
A non-admin sees a collection only when one of their roles has been granted it;
there is no "public collection" grandfathering.  A collection that has not been
registered (owner is unknown) is therefore invisible to non-admins.  Registering
a collection claims it for the acting administrator, who can then grant roles.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Any, Iterable

from src.agent_platform import db
from src.auth import resource_access

#: Canonical resource type understood by the generic access store/API.
RESOURCE_TYPE = "vector_collection"

#: Collection knowledge documents land in when the uploader does not choose one.
DEFAULT_KNOWLEDGE_COLLECTION = "knowledge_chunks"

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS vector_collection_registry (
    name TEXT PRIMARY KEY,
    access_id INTEGER,
    owner_id INTEGER,
    created_at TIMESTAMP NOT NULL
)
"""

_UNIQUE_INDEX_SQL = (
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_vector_collection_registry_access_id "
    "ON vector_collection_registry(access_id)"
)

#: DB files whose DDL already ran (tests switch files).
_ENSURED_PATHS: set[str] = set()


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


def _coerce_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


# --- schema ---------------------------------------------------------------

def ensure_schema() -> None:
    """Create the registry, backfill access ids and register the resolvers."""
    conn = db.get_db_connection()
    try:
        resource_access.ensure_tables()
        path = _db_path(conn)
        fresh = not (path and path in _ENSURED_PATHS)
        conn.execute(_CREATE_TABLE_SQL)
        if fresh:
            conn.execute(_UNIQUE_INDEX_SQL)
            conn.commit()
            if path:
                _ENSURED_PATHS.add(path)
        _backfill_access_ids(conn)
        conn.commit()
    finally:
        conn.close()
    resource_access.register_resource(
        RESOURCE_TYPE, owner_by_access_id, exists_by_access_id
    )


def _backfill_access_ids(conn) -> None:
    rows = conn.execute(
        "SELECT name FROM vector_collection_registry WHERE access_id IS NULL ORDER BY created_at, name"
    ).fetchall()
    if not rows:
        return
    base = int(
        conn.execute(
            "SELECT COALESCE(MAX(access_id), 0) FROM vector_collection_registry"
        ).fetchone()[0]
        or 0
    )
    for offset, row in enumerate(rows, start=1):
        conn.execute(
            "UPDATE vector_collection_registry SET access_id = ? WHERE name = ?",
            (base + offset, row[0]),
        )


# --- name <-> integer identity --------------------------------------------

def _next_access_id(conn) -> int:
    return int(
        conn.execute(
            "SELECT COALESCE(MAX(access_id), 0) FROM vector_collection_registry"
        ).fetchone()[0]
        or 0
    ) + 1


def register_collection(name: str, owner_id: int | None) -> dict[str, Any] | None:
    """Claim ``name`` for ``owner_id`` (idempotent; never changes an owner)."""
    cleaned = str(name or "").strip()
    if not cleaned:
        return None
    owner = _coerce_int(owner_id)
    if owner is None:
        # A null owner would be grandfathered as public by the shared store;
        # this feature is deny-by-default, so refuse to create such a row.
        raise ValueError("A collection must be registered with an owner")

    ensure_schema()
    conn = db.get_db_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT name, access_id, owner_id FROM vector_collection_registry WHERE name = ?",
            (cleaned,),
        ).fetchone()
        if row is None:
            access_id = _next_access_id(conn)
            conn.execute(
                "INSERT INTO vector_collection_registry (name, access_id, owner_id, created_at) "
                "VALUES (?, ?, ?, ?)",
                (cleaned, access_id, owner, datetime.now().isoformat()),
            )
            conn.commit()
            return {"name": cleaned, "access_id": access_id, "owner_id": owner}
        conn.commit()
        if row["access_id"] is None:
            access_id = _next_access_id(conn)
            conn.execute(
                "UPDATE vector_collection_registry SET access_id = ? WHERE name = ?",
                (access_id, cleaned),
            )
            conn.commit()
            return {"name": cleaned, "access_id": access_id, "owner_id": _coerce_int(row["owner_id"])}
        return {
            "name": cleaned,
            "access_id": _coerce_int(row["access_id"]),
            "owner_id": _coerce_int(row["owner_id"]),
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def register_missing(names: Iterable[str], owner_id: int | None) -> None:
    """Claim every not-yet-registered name for ``owner_id`` (never re-owns)."""
    owner = _coerce_int(owner_id)
    if owner is None:
        return
    ensure_schema()
    conn = db.get_db_connection()
    try:
        existing = {
            str(row[0])
            for row in conn.execute("SELECT name FROM vector_collection_registry").fetchall()
        }
    finally:
        conn.close()
    for name in names:
        if str(name) not in existing:
            register_collection(str(name), owner)


def registry_rows(names: Iterable[str] | None = None) -> dict[str, dict[str, Any]]:
    """``{name: {access_id, owner_id}}`` for the registered collections."""
    ensure_schema()
    conn = db.get_db_connection()
    try:
        rows = conn.execute(
            "SELECT name, access_id, owner_id FROM vector_collection_registry"
        ).fetchall()
    finally:
        conn.close()
    wanted = {str(n) for n in names} if names is not None else None
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        name = str(row["name"])
        if wanted is not None and name not in wanted:
            continue
        result[name] = {
            "access_id": _coerce_int(row["access_id"]),
            "owner_id": _coerce_int(row["owner_id"]),
        }
    return result


def access_id_for(name: str) -> int | None:
    """Stable integer identity for a registered collection, else ``None``."""
    row = registry_rows([str(name)]).get(str(name))
    return row["access_id"] if row else None


# --- owner registry (generic access REST API) -----------------------------

def owner_by_access_id(access_id: int) -> int | None:
    ensure_schema()
    conn = db.get_db_connection()
    try:
        row = conn.execute(
            "SELECT owner_id FROM vector_collection_registry WHERE access_id = ?",
            (int(access_id),),
        ).fetchone()
    except sqlite3.Error:
        return None
    finally:
        conn.close()
    return _coerce_int(row["owner_id"]) if row is not None else None


def exists_by_access_id(access_id: int) -> bool:
    if _coerce_int(access_id) is None:
        return False
    ensure_schema()
    conn = db.get_db_connection()
    try:
        return conn.execute(
            "SELECT 1 FROM vector_collection_registry WHERE access_id = ?",
            (int(access_id),),
        ).fetchone() is not None
    except sqlite3.Error:
        return False
    finally:
        conn.close()


# --- access decisions -----------------------------------------------------

def can_access_collection(name: str, user_id: int | None) -> bool:
    """Whether ``user_id`` may index into / read the named collection."""
    uid = _coerce_int(user_id)
    if uid is None or not name:
        return False
    if resource_access.is_admin(uid):
        return True
    row = registry_rows([str(name)]).get(str(name))
    if not row or row["access_id"] is None or row["owner_id"] is None:
        return False
    return resource_access.can_access(
        RESOURCE_TYPE, row["access_id"], row["owner_id"], uid
    )


def visible_collection_names(all_names: Iterable[str], user_id: int | None) -> list[str]:
    """The subset of ``all_names`` the user may see, preserving input order.

    Delegates the decision to :func:`resource_access.filter_visible`; an
    administrator sees every collection, everyone else only registered
    collections owned by them or granted to one of their roles.
    """
    names = [str(n) for n in all_names]
    if not names:
        return []
    uid = _coerce_int(user_id)
    if uid is None:
        return []
    if resource_access.is_admin(uid):
        return names
    rows = [
        {"name": name, "access_id": entry["access_id"], "owner_id": entry["owner_id"]}
        for name, entry in registry_rows(names).items()
        if entry["access_id"] is not None and entry["owner_id"] is not None
    ]
    visible = resource_access.filter_visible(
        RESOURCE_TYPE, rows, uid, owner_key="owner_id", id_key="access_id"
    )
    return [str(row["name"]) for row in visible]


def granted_role_names(name: str) -> list[str]:
    """Role names granted on a collection (shared store lookup)."""
    access_id = access_id_for(name)
    if access_id is None:
        return []
    return [
        entry["role_name"]
        for entry in resource_access.list_access(RESOURCE_TYPE, access_id)
    ]
