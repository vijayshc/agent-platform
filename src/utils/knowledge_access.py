"""Document-level tenancy for the Knowledge module.

Knowledge document ids are UUID strings, but the generic access API
``/api/v1/access/<resource_type>/<int:resource_id>`` keys on an integer.  Each
document therefore carries a stable, unique ``access_id`` assigned once and
never reused.  **``access_id`` is the id testers and the UI must pass to the
generic API** (and to the shared ``ResourceAccessControl``); the UUID remains
the app-facing document id.

Schema added idempotently to ``knowledge_documents`` by :func:`ensure_schema`::

    owner_id  INTEGER   uploader user id; NULL is a legacy/public document
    access_id INTEGER   stable integer identity (UNIQUE index)

Access semantics are exactly the shared foundation's
(:mod:`src.auth.resource_access`): no user denies, admin sees everything,
``owner_id IS NULL`` is grandfathered visible to authenticated users, the owner
sees their own document, otherwise the user's roles must intersect the
document's granted roles.

Required public API
-------------------
``can_access_document``, ``visible_document_ids``, ``register_knowledge_access``,
``access_id_for``.  Supporting helpers used by the manager/routes
(``ensure_schema``, ``retrieval_document_ids``, ``apply_role_names``) live here
too so no tenancy logic leaks back into ``knowledge_manager``.

No-user retrieval rule (security-critical)
------------------------------------------
Retrieval can run with no authenticated user (the stdio MCP ``search_knowledge``
tool, background jobs).  For that case the rule is:

* **admin user id** -> every document (``None`` scope),
* **authenticated user id** -> that user's visible documents,
* **no user id** -> only ``owner_id IS NULL`` (legacy/public) documents.

Tenant-owned documents are NEVER retrievable without an identity, so a
background caller can read public knowledge but can never read another
tenant's document content.  Legacy ``user_roles=`` callers additionally get the
documents granted to those role names.
"""

from __future__ import annotations

import sqlite3
from typing import Iterable

from src.agent_platform import db
from src.auth import resource_access

#: Canonical resource type understood by the generic access store/API.
RESOURCE_TYPE = "knowledge_document"

_COLUMNS: tuple[tuple[str, str], ...] = (("owner_id", "INTEGER"), ("access_id", "INTEGER"))

_UNIQUE_INDEX_SQL = (
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_knowledge_documents_access_id "
    "ON knowledge_documents(access_id)"
)

_MIGRATE_LEGACY_SQL = """
INSERT OR IGNORE INTO resource_role_access
    (resource_type, resource_id, role_id, granted_by)
SELECT ?, d.access_id, r.id, NULL
FROM knowledge_document_roles kdr
JOIN knowledge_documents d ON d.id = kdr.document_id
JOIN roles r ON r.name = kdr.role
WHERE d.access_id IS NOT NULL
"""

#: DB files whose DDL + legacy migration already ran (tests switch files).
_ENSURED_PATHS: set[str] = set()


def _reset_schema_guard() -> None:
    """Forget which DB files were ensured (tests switching temp DBs)."""
    _ENSURED_PATHS.clear()


# --- connection / schema helpers -----------------------------------------

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


def _coerce_int(value) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _add_columns(conn) -> None:
    present = {row[1] for row in conn.execute("PRAGMA table_info(knowledge_documents)")}
    for name, declaration in _COLUMNS:
        if name not in present:
            conn.execute(f"ALTER TABLE knowledge_documents ADD COLUMN {name} {declaration}")


def _backfill_access_ids(conn) -> None:
    """Assign ascending ``access_id`` to every NULL row, deterministically."""
    rows = conn.execute(
        "SELECT id FROM knowledge_documents WHERE access_id IS NULL "
        "ORDER BY created_at, id"
    ).fetchall()
    if not rows:
        return
    base = int(
        conn.execute(
            "SELECT COALESCE(MAX(access_id), 0) FROM knowledge_documents"
        ).fetchone()[0]
        or 0
    )
    for offset, row in enumerate(rows, start=1):
        conn.execute(
            "UPDATE knowledge_documents SET access_id = ? WHERE id = ?",
            (base + offset, row[0]),
        )


def _migrate_legacy_roles(conn) -> int:
    """Copy legacy role-NAME grants into the generic store (idempotent)."""
    if not (_table_exists(conn, "knowledge_document_roles") and _table_exists(conn, "roles")):
        return 0
    cursor = conn.execute(_MIGRATE_LEGACY_SQL, (RESOURCE_TYPE,))
    return max(int(cursor.rowcount or 0), 0)


def ensure_schema() -> None:
    """Add the tenancy columns, the unique access_id index and legacy grants.

    Idempotent and safe before ``knowledge_documents`` exists (returns early).
    Backfills run on every call so a row inserted after the first call still
    gets a stable ``access_id``.
    """
    conn = db.get_db_connection()
    try:
        if not _table_exists(conn, "knowledge_documents"):
            return
        # Make sure the generic grant table exists before this connection starts
        # writing: resource_access uses its own connection and a write there
        # while this one holds an uncommitted transaction would lock.
        resource_access.ensure_tables()
        path = _db_path(conn)
        fresh = not (path and path in _ENSURED_PATHS)
        if fresh:
            _add_columns(conn)
            conn.execute(_UNIQUE_INDEX_SQL)
            conn.commit()
            if path:
                _ENSURED_PATHS.add(path)
        _backfill_access_ids(conn)
        conn.commit()
        if fresh:
            _migrate_legacy_roles(conn)
        conn.commit()
    finally:
        conn.close()


# --- stable integer identity ---------------------------------------------

def _assign_access_id(document_id: str) -> int | None:
    """Allocate the next access_id under a write lock (never reuse an id)."""
    conn = db.get_db_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        base = conn.execute(
            "SELECT COALESCE(MAX(access_id), 0) FROM knowledge_documents"
        ).fetchone()[0]
        next_id = int(base or 0) + 1
        cursor = conn.execute(
            "UPDATE knowledge_documents SET access_id = ? WHERE id = ?",
            (next_id, document_id),
        )
        if cursor.rowcount == 0:
            conn.rollback()
            return None
        conn.commit()
        return next_id
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def access_id_for(document_id: str) -> int | None:
    """Stable integer identity for a document, or ``None`` when it has no row."""
    if not document_id:
        return None
    ensure_schema()
    conn = db.get_db_connection()
    try:
        row = conn.execute(
            "SELECT access_id FROM knowledge_documents WHERE id = ?", (str(document_id),)
        ).fetchone()
    except sqlite3.Error:
        return None
    finally:
        conn.close()
    if row is None:
        return None
    value = row["access_id"] if hasattr(row, "keys") else row[0]
    if value is not None:
        return int(value)
    return _assign_access_id(str(document_id))


# --- owner registry (generic access REST API) -----------------------------

def _owner_by_access_id(access_id: int) -> int | None:
    ensure_schema()
    conn = db.get_db_connection()
    try:
        row = conn.execute(
            "SELECT owner_id FROM knowledge_documents WHERE access_id = ?", (access_id,)
        ).fetchone()
    except sqlite3.Error:
        return None
    finally:
        conn.close()
    if row is None:
        return None
    return _coerce_int(row["owner_id"] if hasattr(row, "keys") else row[0])


def _exists_by_access_id(access_id: int) -> bool:
    if _coerce_int(access_id) is None:
        return False
    ensure_schema()
    conn = db.get_db_connection()
    try:
        return conn.execute(
            "SELECT 1 FROM knowledge_documents WHERE access_id = ?", (int(access_id),)
        ).fetchone() is not None
    except sqlite3.Error:
        return False
    finally:
        conn.close()


def register_knowledge_access() -> None:
    """Register the ``knowledge_document`` owner/exists resolvers (idempotent)."""
    resource_access.register_resource(
        RESOURCE_TYPE, _owner_by_access_id, _exists_by_access_id
    )


# --- access decisions -----------------------------------------------------

def can_access_document(document_id: str, user_id: int | None) -> bool:
    """Whether ``user_id`` may see/use the document (no user denies)."""
    uid = _coerce_int(user_id)
    if uid is None or not document_id:
        return False
    ensure_schema()
    conn = db.get_db_connection()
    try:
        row = conn.execute(
            "SELECT owner_id, access_id FROM knowledge_documents WHERE id = ?",
            (str(document_id),),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return False
    owner = _coerce_int(row["owner_id"] if hasattr(row, "keys") else row[0])
    access_id = _coerce_int(row["access_id"] if hasattr(row, "keys") else row[1])
    if access_id is None:
        access_id = access_id_for(document_id)
    return resource_access.can_access(RESOURCE_TYPE, access_id, owner, uid)


def can_manage_document(document_id: str, user_id: int | None) -> bool:
    """Whether ``user_id`` may delete/retag/manage grants on the document.

    Only the owner or an administrator.  A role grant confers *view/use*, never
    destructive control; an owner-less legacy document is managed by
    administrators only (every authenticated user may still read it).
    """
    uid = _coerce_int(user_id)
    if uid is None or not document_id:
        return False
    if resource_access.is_admin(uid):
        return True
    access_id = access_id_for(document_id)
    if access_id is None:
        return False
    owner = resource_access.owner_of(RESOURCE_TYPE, access_id)
    return owner is not None and int(owner) == uid


def _document_rows() -> list[dict]:
    conn = db.get_db_connection()
    try:
        rows = conn.execute(
            "SELECT id, owner_id, access_id FROM knowledge_documents"
        ).fetchall()
    finally:
        conn.close()
    return [
        {
            "id": _coerce_int(row["access_id"]),
            "owner_id": _coerce_int(row["owner_id"]),
            "document_id": str(row["id"]),
        }
        for row in rows
    ]


def visible_document_ids(user_id: int | None) -> set[str] | None:
    """Document ids visible to the user; ``None`` means every document (admin).

    ``user_id is None`` (no identity) returns an empty set: direct access never
    falls back to public documents.  Use :func:`retrieval_document_ids` for the
    documented no-user retrieval rule.
    """
    uid = _coerce_int(user_id)
    if uid is None:
        return set()
    if resource_access.is_admin(uid):
        return None
    ensure_schema()
    rows = _document_rows()
    visible = resource_access.filter_visible(
        RESOURCE_TYPE, rows, uid, owner_key="owner_id", id_key="id"
    )
    return {row["document_id"] for row in visible}


def _public_document_ids() -> set[str]:
    conn = db.get_db_connection()
    try:
        rows = conn.execute(
            "SELECT id FROM knowledge_documents WHERE owner_id IS NULL"
        ).fetchall()
    finally:
        conn.close()
    return {str(row[0]) for row in rows}


def _role_ids_for_names(names: Iterable[str]) -> list[int]:
    cleaned = [str(name).strip() for name in (names or []) if str(name).strip()]
    if not cleaned:
        return []
    conn = db.get_db_connection()
    try:
        if not _table_exists(conn, "roles"):
            return []
        placeholders = ",".join("?" for _ in cleaned)
        rows = conn.execute(
            f"SELECT id FROM roles WHERE name IN ({placeholders})", cleaned
        ).fetchall()
        return [int(row[0]) for row in rows]
    finally:
        conn.close()


def _documents_granted_to_roles(role_ids: list[int]) -> set[str]:
    if not role_ids:
        return set()
    resource_access.ensure_tables()
    conn = db.get_db_connection()
    try:
        placeholders = ",".join("?" for _ in role_ids)
        rows = conn.execute(
            f"SELECT d.id FROM knowledge_documents d "
            f"JOIN resource_role_access a ON a.resource_id = d.access_id "
            f"WHERE a.resource_type = ? AND a.role_id IN ({placeholders})",
            [RESOURCE_TYPE, *role_ids],
        ).fetchall()
    finally:
        conn.close()
    return {str(row[0]) for row in rows}


def retrieval_document_ids(
    user_id: int | None, user_roles: Iterable[str] | None = None
) -> set[str] | None:
    """Document ids retrieval may read; ``None`` means everything (admin).

    See the module docstring for the no-user rule.  ``user_roles`` keeps legacy
    ``user_roles=`` callers working by mapping role names to ids against the
    generic store instead of the retired ``knowledge_document_roles`` names.
    """
    uid = _coerce_int(user_id)
    if uid is not None:
        return visible_document_ids(uid)
    ensure_schema()
    public = _public_document_ids()
    names = [str(name) for name in (user_roles or []) if str(name).strip()]
    if not names:
        return public
    if resource_access.ADMIN_ROLE_NAME in names:
        return None
    return public | _documents_granted_to_roles(_role_ids_for_names(names))


# --- legacy role-name grants ----------------------------------------------

def apply_role_names(
    document_id: str, role_names: Iterable[str] | None, granted_by: int | None = None
) -> list[dict]:
    """Translate role NAMES to ids and replace the document's generic grants."""
    access_id = access_id_for(document_id)
    if access_id is None:
        return []
    role_ids = _role_ids_for_names(role_names)
    if not role_ids:
        return resource_access.list_access(RESOURCE_TYPE, access_id)
    return resource_access.set_access(
        RESOURCE_TYPE, access_id, role_ids, granted_by=_coerce_int(granted_by)
    )


# Import-time registration so the generic access API can resolve owners.
register_knowledge_access()
