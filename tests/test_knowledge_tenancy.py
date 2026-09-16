"""Knowledge document tenancy: real SQL against a real (temp) SQLite DB.

No mockups: the real ``knowledge_access`` / ``KnowledgeManager`` code paths run
against a real SQLite file (``temp_db``) and a real temp uploads directory; only
the connection target is swapped, exactly as ``tests/conftest.py`` does.

Covers: owner / admin / unrelated visibility, role-grant view-but-not-delete,
owner-less legacy visibility, stable ``access_id`` backfill, idempotent
role-NAME migration into the generic store, and retrieval never returning
another tenant's chunks.
"""

from __future__ import annotations

import logging
import sqlite3
import threading

import pytest

from src.auth import resource_access
from src.utils import knowledge_access

ROLE_ADMIN, ROLE_USER, ROLE_ANALYST, ROLE_OTHER = 1, 2, 3, 4
ADMIN, OWNER, ANALYST, OTHER, OTHER_TENANT = 10, 11, 12, 13, 14

_SCHEMA = """
CREATE TABLE IF NOT EXISTS roles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,
    description TEXT
);
CREATE TABLE IF NOT EXISTS user_roles (
    user_id INTEGER,
    role_id INTEGER
);
"""


def _connect(db_path):
    conn = sqlite3.connect(str(db_path), timeout=10.0)
    conn.row_factory = sqlite3.Row
    return conn


def _exec(db_path, sql: str, params=()):
    conn = _connect(db_path)
    try:
        conn.execute(sql, params)
        conn.commit()
    finally:
        conn.close()


def _rows(db_path, sql: str, params=()):
    conn = _connect(db_path)
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


@pytest.fixture()
def knowledge(temp_db, monkeypatch, tmp_path):
    """Real temp DB + real temp uploads dir with a minimally-wired manager."""
    import src.utils.knowledge_manager as km_module
    from src.utils.knowledge_manager import KnowledgeManager

    resource_access._reset_schema_guard()
    knowledge_access._reset_schema_guard()

    # KnowledgeManager binds get_db_connection at import; patch THAT name.
    monkeypatch.setattr(km_module, "get_db_connection", lambda: _connect(temp_db))
    uploads = tmp_path / "knowledge_uploads"
    uploads.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(km_module, "UPLOADS_DIR", str(uploads))

    conn = _connect(temp_db)
    try:
        conn.executescript(_SCHEMA)
        for role_id, name in (
            (ROLE_ADMIN, "admin"), (ROLE_USER, "user"),
            (ROLE_ANALYST, "analyst"), (ROLE_OTHER, "other"),
        ):
            conn.execute("INSERT INTO roles (id, name) VALUES (?, ?)", (role_id, name))
        for user_id, role_id in (
            (ADMIN, ROLE_ADMIN), (ANALYST, ROLE_ANALYST), (OTHER, ROLE_OTHER),
        ):
            conn.execute("INSERT INTO user_roles (user_id, role_id) VALUES (?, ?)", (user_id, role_id))
        conn.commit()
    finally:
        conn.close()

    manager = object.__new__(KnowledgeManager)
    manager._local = threading.local()
    manager.processing_status = {}
    manager.logger = logging.getLogger("text2sql.knowledge.test")
    manager.vector_store = None
    manager.llm_engine = None
    manager.md_converter = None
    # Real schema creation path (also runs knowledge_access.ensure_schema()).
    manager._create_tables()

    yield manager, temp_db

    resource_access._reset_schema_guard()
    knowledge_access._reset_schema_guard()


def _add_document(db_path, doc_id, owner_id, status="completed"):
    _exec(
        db_path,
        "INSERT INTO knowledge_documents "
        "(id, original_filename, file_path, content_type, status, created_at, updated_at, owner_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (doc_id, f"{doc_id}.txt", f"/tmp/{doc_id}.txt", "txt", status,
         "2024-01-01T00:00:00", "2024-01-01T00:00:00", owner_id),
    )
    return knowledge_access.access_id_for(doc_id)


def _add_chunk(db_path, chunk_id, doc_id, index=0, content="chunk"):
    _exec(
        db_path,
        "INSERT INTO knowledge_chunks (id, document_id, chunk_index, content, embedding_id, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (chunk_id, doc_id, index, content, chunk_id, "2024-01-01T00:00:00"),
    )


# --------------------------------------------------------------------------
# visibility
# --------------------------------------------------------------------------

def test_owner_sees_own_document(knowledge):
    manager, db_path = knowledge
    _add_document(db_path, "doc-owner", OWNER)

    assert knowledge_access.can_access_document("doc-owner", OWNER)
    assert "doc-owner" in knowledge_access.visible_document_ids(OWNER)
    assert [d["id"] for d in manager.list_documents(user_id=OWNER)] == ["doc-owner"]


def test_admin_sees_all(knowledge):
    manager, db_path = knowledge
    _add_document(db_path, "doc-a", OWNER)
    _add_document(db_path, "doc-b", OTHER_TENANT)

    assert knowledge_access.visible_document_ids(ADMIN) is None
    assert knowledge_access.can_access_document("doc-a", ADMIN)
    assert {d["id"] for d in manager.list_documents(user_id=ADMIN)} == {"doc-a", "doc-b"}


def test_unrelated_user_is_isolated(knowledge):
    manager, db_path = knowledge
    _add_document(db_path, "doc-a", OWNER)

    assert knowledge_access.visible_document_ids(OTHER) == set()
    assert not knowledge_access.can_access_document("doc-a", OTHER)
    assert manager.list_documents(user_id=OTHER) == []
    assert manager.get_all_tags(user_id=OTHER) == []

    # delete/mutate requires owner or admin; a role-less unrelated user is denied
    access_id = knowledge_access.access_id_for("doc-a")
    owner = resource_access.owner_of("knowledge_document", access_id)
    can_manage = resource_access.is_admin(OTHER) or owner == OTHER
    assert not can_manage
    assert resource_access.is_admin(ADMIN)


def test_no_user_id_denies_direct_access(knowledge):
    _, db_path = knowledge
    _add_document(db_path, "doc-a", OWNER)

    assert knowledge_access.visible_document_ids(None) == set()
    assert not knowledge_access.can_access_document("doc-a", None)


# --------------------------------------------------------------------------
# role grants
# --------------------------------------------------------------------------

def test_role_grant_allows_view_not_delete(knowledge):
    manager, db_path = knowledge
    access_id = _add_document(db_path, "doc-a", OWNER)
    knowledge_access.apply_role_names("doc-a", ["analyst"], granted_by=OWNER)

    assert knowledge_access.can_access_document("doc-a", ANALYST)
    assert "doc-a" in knowledge_access.visible_document_ids(ANALYST)
    assert [d["id"] for d in manager.list_documents(user_id=ANALYST)] == ["doc-a"]

    # A grant confers view/use only: delete still needs owner or admin.
    owner = resource_access.owner_of("knowledge_document", access_id)
    assert owner == OWNER
    assert not (resource_access.is_admin(ANALYST) or owner == ANALYST)

    assert manager.get_document_roles("doc-a") == ["analyst"]


def test_owner_null_legacy_document_visible_to_authenticated(knowledge):
    manager, db_path = knowledge
    _add_document(db_path, "doc-legacy", None)

    assert knowledge_access.can_access_document("doc-legacy", OTHER)
    assert "doc-legacy" in knowledge_access.visible_document_ids(OTHER)
    assert [d["id"] for d in manager.list_documents(user_id=OTHER)] == ["doc-legacy"]


def test_list_payload_reports_ownership_and_can_manage(knowledge):
    """The admin UI needs owner_id + can_manage to label/act correctly."""
    manager, db_path = knowledge
    _add_document(db_path, "doc-owned", OWNER)
    _add_document(db_path, "doc-legacy", None)

    by_id = {d["id"]: d for d in manager.list_documents(user_id=OWNER)}
    assert by_id["doc-owned"]["owner_id"] == OWNER
    assert by_id["doc-owned"]["can_manage"] is True
    assert by_id["doc-legacy"]["owner_id"] is None
    assert by_id["doc-legacy"]["can_manage"] is False

    # Admin manages every document, including owner-less legacy rows.
    admin_rows = {d["id"]: d for d in manager.list_documents(user_id=ADMIN)}
    assert admin_rows["doc-owned"]["can_manage"] is True
    assert admin_rows["doc-legacy"]["can_manage"] is True

    # A granted role can view/use but never manage.
    knowledge_access.apply_role_names("doc-owned", ["analyst"], granted_by=OWNER)
    assert knowledge_access.can_manage_document("doc-owned", ANALYST) is False
    assert knowledge_access.can_manage_document("doc-owned", OWNER) is True
    assert knowledge_access.can_manage_document("doc-legacy", OWNER) is False

    analyst = {d["id"]: d for d in manager.list_documents(user_id=ANALYST)}
    assert analyst["doc-owned"]["can_manage"] is False
    assert analyst["doc-owned"]["owner_id"] == OWNER
    assert analyst["doc-owned"]["allowed_roles"] == ["analyst"]


def test_roles_backward_compat_names_map_to_ids(knowledge):
    _, db_path = knowledge
    _add_document(db_path, "doc-a", OWNER)
    knowledge_access.apply_role_names("doc-a", ["analyst"], granted_by=OWNER)

    # Legacy user_roles= callers (no user id) still see the granted document.
    assert "doc-a" in knowledge_access.retrieval_document_ids(None, ["analyst"])
    assert knowledge_access.retrieval_document_ids(None, ["nope"]) == set()
    assert knowledge_access.retrieval_document_ids(None, ["admin"]) is None


# --------------------------------------------------------------------------
# stable integer identity
# --------------------------------------------------------------------------

def test_access_id_backfill_is_stable_and_monotonic(knowledge):
    _, db_path = knowledge
    for index, doc_id in enumerate(("doc-1", "doc-2", "doc-3"), start=1):
        _exec(
            db_path,
            "INSERT INTO knowledge_documents "
            "(id, original_filename, file_path, content_type, status, created_at, updated_at, owner_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (doc_id, f"{doc_id}.txt", "/tmp/x.txt", "txt", "completed",
             f"2024-01-0{index}T00:00:00", f"2024-01-0{index}T00:00:00", OWNER),
        )

    knowledge_access.ensure_schema()
    first = {row["id"]: row["access_id"] for row in _rows(db_path, "SELECT id, access_id FROM knowledge_documents")}
    assert set(first.values()) == {1, 2, 3}
    assert first["doc-1"] == 1 and first["doc-3"] == 3

    knowledge_access.ensure_schema()
    second = {row["id"]: row["access_id"] for row in _rows(db_path, "SELECT id, access_id FROM knowledge_documents")}
    assert second == first

    _add_document(db_path, "doc-4", OWNER)
    assert knowledge_access.access_id_for("doc-4") == 4
    assert knowledge_access.access_id_for("missing") is None


# --------------------------------------------------------------------------
# legacy role-NAME migration
# --------------------------------------------------------------------------

def test_legacy_role_names_migrate_idempotently(knowledge):
    _, db_path = knowledge
    access_id = _add_document(db_path, "doc-a", OTHER_TENANT)
    _exec(
        db_path,
        "INSERT INTO knowledge_document_roles (id, document_id, role, created_at) "
        "VALUES (?, ?, ?, ?)",
        ("lr-1", "doc-a", "analyst", "2024-01-01T00:00:00"),
    )

    # Force the one-time migration path (fixture already ensured the schema).
    knowledge_access._reset_schema_guard()
    resource_access._reset_schema_guard()
    knowledge_access.ensure_schema()

    grants = _rows(
        db_path,
        "SELECT role_id FROM resource_role_access "
        "WHERE resource_type = 'knowledge_document' AND resource_id = ?",
        (access_id,),
    )
    assert [row["role_id"] for row in grants] == [ROLE_ANALYST]
    assert knowledge_access.can_access_document("doc-a", ANALYST)

    # Second run inserts nothing new.
    knowledge_access._reset_schema_guard()
    knowledge_access.ensure_schema()
    assert len(_rows(
        db_path,
        "SELECT role_id FROM resource_role_access "
        "WHERE resource_type = 'knowledge_document' AND resource_id = ?",
        (access_id,),
    )) == 1

    # The legacy table is kept readable for rollback.
    legacy = _rows(db_path, "SELECT role FROM knowledge_document_roles WHERE document_id = 'doc-a'")
    assert [row["role"] for row in legacy] == ["analyst"]


# --------------------------------------------------------------------------
# upload path stamps the uploader
# --------------------------------------------------------------------------

def test_text_ingest_stamps_owner_and_grants(knowledge):
    manager, db_path = knowledge

    document_id = manager.process_text_content(
        "Onboarding", "notes", "hello tenancy", tags=["policy"],
        allowed_roles=["analyst"], owner_id=OWNER,
    )

    row = _rows(db_path, "SELECT owner_id, access_id FROM knowledge_documents WHERE id = ?", (document_id,))[0]
    assert row["owner_id"] == OWNER
    access_id = row["access_id"]
    assert access_id is not None
    assert knowledge_access.access_id_for(document_id) == access_id

    grants = _rows(
        db_path,
        "SELECT role_id FROM resource_role_access "
        "WHERE resource_type = 'knowledge_document' AND resource_id = ?",
        (access_id,),
    )
    assert [r["role_id"] for r in grants] == [ROLE_ANALYST]
    assert knowledge_access.can_access_document(document_id, ANALYST)
    assert not knowledge_access.can_access_document(document_id, OTHER)


# --------------------------------------------------------------------------
# retrieval tenancy (security-critical)
# --------------------------------------------------------------------------

def test_retrieval_returns_zero_chunks_from_other_tenant(knowledge):
    manager, db_path = knowledge
    _add_document(db_path, "doc-a", OWNER)
    _add_document(db_path, "doc-b", OTHER_TENANT)
    _add_chunk(db_path, "chunk-a", "doc-a", 0, "tenant A secret")
    _add_chunk(db_path, "chunk-b", "doc-b", 0, "tenant B secret")

    # Real manager method used by get_answer to build the vector-store filter.
    allowed = manager._retrievable_document_ids(OWNER)
    assert allowed == ["doc-a"]

    placeholders = ",".join("?" for _ in allowed)
    visible_chunks = _rows(
        db_path,
        f"SELECT document_id FROM knowledge_chunks WHERE document_id IN ({placeholders})",
        allowed,
    )
    assert [row["document_id"] for row in visible_chunks] == ["doc-a"]
    # Both tenants' chunks really exist in the shared table; only A's pass the
    # retrieval filter.
    all_chunks = {row["document_id"] for row in _rows(db_path, "SELECT document_id FROM knowledge_chunks")}
    assert all_chunks == {"doc-a", "doc-b"}
    assert "doc-b" not in {row["document_id"] for row in visible_chunks}

    # Admin scope is unrestricted (None) and a background/no-user scope can
    # never include a tenant-owned document.
    assert manager._retrievable_document_ids(ADMIN) is None
    background = knowledge_access.retrieval_document_ids(None)
    assert "doc-b" not in background and "doc-a" not in background


def test_vector_search_scope_narrows_knowledge_collection(knowledge):
    from src.utils import vector_search

    _, db_path = knowledge
    _add_document(db_path, "doc-a", OWNER)
    _add_document(db_path, "doc-b", OTHER_TENANT)

    scope = vector_search._knowledge_scope("knowledge_chunks", OWNER)
    assert scope == {"doc-a"}
    assert vector_search._knowledge_scope("knowledge_chunks", ADMIN) is None
    assert vector_search._knowledge_scope("other_collection", OWNER) is None

    scoped = vector_search._with_scope({"tag": "policy"}, scope)
    assert scoped == {"$and": [{"tag": "policy"}, {"document_id": {"$in": ["doc-a"]}}]}


# --------------------------------------------------------------------------
# generic access API identity
# --------------------------------------------------------------------------

def test_owner_and_existence_resolve_by_access_id(knowledge):
    _, db_path = knowledge
    access_id = _add_document(db_path, "doc-a", OWNER)

    assert resource_access.is_registered("knowledge_document")
    assert resource_access.owner_of("knowledge_document", access_id) == OWNER
    assert resource_access.resource_exists("knowledge_document", access_id)
    assert not resource_access.resource_exists("knowledge_document", 999_999)
