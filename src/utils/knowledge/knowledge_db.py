"""Database schema, metadata persistence, tags, and tenancy resolution for knowledge."""

from datetime import datetime
import logging
import os
from typing import Any, Dict, List, Optional
import uuid

from src.auth import resource_access
from src.utils import collection_access, knowledge_access, knowledge_ingest

logger = logging.getLogger("text2sql.knowledge_db")


def create_tables(manager):
    """Create necessary database tables if they don't exist."""
    cursor = manager.conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS knowledge_documents (
            id TEXT PRIMARY KEY,
            original_filename TEXT NOT NULL,
            file_path TEXT NOT NULL,
            content_type TEXT,
            status TEXT NOT NULL,
            created_at TIMESTAMP NOT NULL,
            updated_at TIMESTAMP NOT NULL,
            processed_at TIMESTAMP,
            error TEXT
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS knowledge_document_tags (
            id TEXT PRIMARY KEY,
            document_id TEXT NOT NULL,
            tag TEXT NOT NULL,
            created_at TIMESTAMP NOT NULL,
            FOREIGN KEY (document_id) REFERENCES knowledge_documents(id) ON DELETE CASCADE
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS knowledge_document_roles (
            id TEXT PRIMARY KEY,
            document_id TEXT NOT NULL,
            role TEXT NOT NULL,
            created_at TIMESTAMP NOT NULL,
            FOREIGN KEY (document_id) REFERENCES knowledge_documents(id) ON DELETE CASCADE
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS knowledge_chunks (
            id TEXT PRIMARY KEY,
            document_id TEXT NOT NULL,
            chunk_index INTEGER NOT NULL,
            content TEXT NOT NULL,
            embedding_id TEXT,
            created_at TIMESTAMP NOT NULL,
            FOREIGN KEY (document_id) REFERENCES knowledge_documents(id) ON DELETE CASCADE
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS knowledge_queries (
            id TEXT PRIMARY KEY,
            user_id INTEGER,
            query TEXT NOT NULL,
            answer TEXT NOT NULL,
            created_at TIMESTAMP NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    """)

    manager.conn.commit()

    knowledge_ingest.ensure_ingest_schema(manager.conn)
    collection_access.ensure_schema()
    knowledge_access.ensure_schema()


def get_document_status(manager, document_id: str) -> Dict[str, Any]:
    """Get the processing status of a document."""
    if document_id in manager.processing_status:
        return manager.processing_status[document_id]

    cursor = manager.conn.cursor()
    cursor.execute("SELECT status, error, processed_at FROM knowledge_documents WHERE id = ?", (document_id,))
    row = cursor.fetchone()

    if not row:
        return {"status": "not_found", "message": "Document not found"}

    status, error, processed_at = row
    if status == "completed":
        return {"status": status, "message": "Document processing completed successfully", "processed_at": processed_at}
    elif status == "error":
        return {"status": status, "message": f"Error processing document: {error}"}
    else:
        return {"status": status, "message": "Document status unknown"}


def list_documents(manager, user_id: int = None, user_roles: List[str] = None) -> List[Dict[str, Any]]:
    """List documents accessible to caller."""
    visible = knowledge_access.retrieval_document_ids(user_id, user_roles)
    cursor = manager.conn.cursor()
    cursor.execute(
        "SELECT id, original_filename, content_type, status, created_at, processed_at, owner_id, "
        "chunking_method, chunk_size, chunk_overlap, metadata_columns, data_columns, collection_name "
        "FROM knowledge_documents ORDER BY created_at DESC"
    )

    documents = []
    for row in cursor.fetchall():
        (
            doc_id, filename, content_type, status, created_at, processed_at, owner_id,
            chunking_method, chunk_size, chunk_overlap, metadata_columns, data_columns, collection_name
        ) = row
        if visible is not None and doc_id not in visible:
            continue

        cursor.execute("SELECT COUNT(*) FROM knowledge_chunks WHERE document_id = ?", (doc_id,))
        chunk_count = cursor.fetchone()[0]

        tags = manager.get_document_tags(doc_id)
        allowed_roles = manager.get_document_roles(doc_id)

        documents.append({
            "id": doc_id,
            "access_id": knowledge_access.access_id_for(doc_id),
            "owner_id": None if owner_id is None else int(owner_id),
            "can_manage": knowledge_access.can_manage_document(doc_id, user_id),
            "filename": filename,
            "content_type": content_type,
            "status": status,
            "created_at": created_at,
            "processed_at": processed_at,
            "chunk_count": chunk_count,
            "tags": tags,
            "allowed_roles": allowed_roles,
            "chunking_method": chunking_method,
            "chunk_size": chunk_size,
            "chunk_overlap": chunk_overlap,
            "metadata_columns": knowledge_ingest.decode_columns(metadata_columns),
            "data_columns": knowledge_ingest.decode_columns(data_columns),
            "collection_name": collection_name or collection_access.DEFAULT_KNOWLEDGE_COLLECTION,
        })

    return documents


def get_document_tags(manager, document_id: str) -> List[str]:
    """Get tags for a specific document."""
    cursor = manager.conn.cursor()
    cursor.execute("SELECT tag FROM knowledge_document_tags WHERE document_id = ?", (document_id,))
    return [row[0] for row in cursor.fetchall()]


def get_document_roles(manager, document_id: str) -> List[str]:
    """Get granted role names for a document."""
    access_id = knowledge_access.access_id_for(document_id)
    if access_id is None:
        return []
    return [
        entry["role_name"]
        for entry in resource_access.list_access("knowledge_document", access_id)
    ]


def get_all_tags(manager, user_id: int = None, user_roles: List[str] = None) -> List[str]:
    """Get unique tags from active documents caller can access."""
    cursor = manager.conn.cursor()
    allowed_doc_ids = knowledge_access.retrieval_document_ids(user_id, user_roles)

    if allowed_doc_ids is not None:
        if len(allowed_doc_ids) == 0:
            return []
        placeholders = ",".join(["?" for _ in allowed_doc_ids])
        cursor.execute(f"""
            SELECT DISTINCT kdt.tag 
            FROM knowledge_document_tags kdt
            INNER JOIN knowledge_documents kd ON kdt.document_id = kd.id
            WHERE kd.status = 'completed' AND kdt.document_id IN ({placeholders})
            ORDER BY kdt.tag
        """, list(allowed_doc_ids))
    else:
        cursor.execute("""
            SELECT DISTINCT kdt.tag 
            FROM knowledge_document_tags kdt
            INNER JOIN knowledge_documents kd ON kdt.document_id = kd.id
            WHERE kd.status = 'completed'
            ORDER BY kdt.tag
        """)

    return [row[0] for row in cursor.fetchall()]


def get_document_ids_by_tags(manager, tags: List[str]) -> Optional[List[str]]:
    """Get document IDs that have all specified tags."""
    if not tags:
        return None
    normalized_tags = [tag.strip().lower() for tag in tags if tag.strip()]
    if not normalized_tags:
        return None

    cursor = manager.conn.cursor()
    placeholders = ",".join(["?" for _ in normalized_tags])
    query = f"""
        SELECT document_id FROM knowledge_document_tags
        WHERE tag IN ({placeholders})
        GROUP BY document_id
        HAVING COUNT(DISTINCT tag) = ?
    """
    cursor.execute(query, normalized_tags + [len(normalized_tags)])
    return [row[0] for row in cursor.fetchall()]


def add_document_tag(manager, document_id: str, tag: str) -> bool:
    """Add a tag to a document."""
    try:
        tag = tag.strip().lower()
        if not tag:
            return False

        now = datetime.now().isoformat()
        tag_id = str(uuid.uuid4())

        cursor = manager.conn.cursor()
        cursor.execute("SELECT id FROM knowledge_document_tags WHERE document_id = ? AND tag = ?", (document_id, tag))
        if cursor.fetchone():
            return True

        cursor.execute(
            "INSERT INTO knowledge_document_tags (id, document_id, tag, created_at) VALUES (?, ?, ?, ?)",
            (tag_id, document_id, tag, now)
        )
        manager.conn.commit()
        return True
    except Exception as e:
        manager.logger.info("Error adding tag to document %s: %s", document_id, e, exc_info=True)
        return False


def remove_document_tag(manager, document_id: str, tag: str) -> bool:
    """Remove a tag from a document."""
    try:
        cursor = manager.conn.cursor()
        cursor.execute(
            "DELETE FROM knowledge_document_tags WHERE document_id = ? AND tag = ?",
            (document_id, tag.strip().lower())
        )
        manager.conn.commit()
        return True
    except Exception as e:
        manager.logger.info("Error removing tag from document %s: %s", document_id, e, exc_info=True)
        return False


def get_allowed_document_ids(manager, user_roles: List[str] = None, user_id: int = None) -> Optional[List[str]]:
    """Document IDs caller may retrieve."""
    allowed = knowledge_access.retrieval_document_ids(user_id, user_roles)
    return None if allowed is None else sorted(allowed)


def retrievable_document_ids(manager, user_id: int, user_roles: List[str] = None) -> Optional[List[str]]:
    """Visible document IDs for retrieval."""
    allowed = knowledge_access.retrieval_document_ids(user_id, user_roles)
    return None if allowed is None else sorted(allowed)


def delete_document(manager, document_id: str) -> bool:
    """Delete a document and its chunks from the system."""
    try:
        cursor = manager.conn.cursor()
        cursor.execute(
            "SELECT file_path, access_id, collection_name FROM knowledge_documents WHERE id = ?",
            (document_id,)
        )
        row = cursor.fetchone()
        if not row:
            return False

        file_path, access_id = row[0], row[1]
        collection = row[2] or collection_access.DEFAULT_KNOWLEDGE_COLLECTION

        if os.path.exists(file_path):
            os.remove(file_path)

        chunk_ids = []
        cursor.execute("SELECT id FROM knowledge_chunks WHERE document_id = ?", (document_id,))
        for r in cursor.fetchall():
            chunk_ids.append(r[0])

        cursor.execute("DELETE FROM knowledge_document_roles WHERE document_id = ?", (document_id,))
        cursor.execute("DELETE FROM knowledge_document_tags WHERE document_id = ?", (document_id,))
        cursor.execute("DELETE FROM knowledge_chunk_metadata WHERE document_id = ?", (document_id,))
        cursor.execute("DELETE FROM knowledge_chunks WHERE document_id = ?", (document_id,))
        cursor.execute("DELETE FROM knowledge_documents WHERE id = ?", (document_id,))
        manager.conn.commit()

        if access_id is not None:
            resource_access.set_access("knowledge_document", int(access_id), [])

        if document_id in manager.processing_status:
            del manager.processing_status[document_id]

        if chunk_ids and manager.vector_store:
            try:
                manager.logger.info("Deleting %d chunks from vector store in batches", len(chunk_ids))
                batch_size = 15
                for i in range(0, len(chunk_ids), batch_size):
                    batch_chunk_ids = chunk_ids[i:i + batch_size]
                    for chunk_id in batch_chunk_ids:
                        try:
                            manager.vector_store.delete_by_filter(
                                collection_name=collection,
                                filter_expr={"chunk_id": chunk_id}
                            )
                        except Exception as chunk_err:
                            manager.logger.info("Error deleting chunk %s: %s", chunk_id, chunk_err)
                manager.logger.info("Successfully deleted chunks from vector store for document %s", document_id)
            except Exception as e:
                manager.logger.info("Error deleting chunks from vector store: %s", e, exc_info=True)

        return True
    except Exception as e:
        manager.logger.info("Error deleting document %s: %s", document_id, e, exc_info=True)
        return False


def get_document_info(manager, document_id: str) -> Optional[Dict[str, Any]]:
    """Get detailed metadata for a document."""
    try:
        cursor = manager.conn.cursor()
        cursor.execute(
            """SELECT id, original_filename, file_path, content_type, status, 
               created_at, updated_at, processed_at, error,
               chunking_method, chunk_size, chunk_overlap, metadata_columns, data_columns, collection_name
               FROM knowledge_documents WHERE id = ?""",
            (document_id,)
        )
        row = cursor.fetchone()
        if not row:
            return None

        (
            doc_id, filename, file_path, content_type, status, created_at, updated_at, processed_at, error,
            chunking_method, chunk_size, chunk_overlap, metadata_columns, data_columns, collection_name
        ) = row

        cursor.execute("SELECT COUNT(*) FROM knowledge_chunks WHERE document_id = ?", (doc_id,))
        chunk_count = cursor.fetchone()[0]

        tags = manager.get_document_tags(doc_id)
        allowed_roles = manager.get_document_roles(doc_id)

        return {
            "id": doc_id,
            "access_id": knowledge_access.access_id_for(doc_id),
            "original_filename": filename,
            "file_path": file_path,
            "content_type": content_type,
            "status": status,
            "created_at": created_at,
            "updated_at": updated_at,
            "processed_at": processed_at,
            "error": error,
            "chunk_count": chunk_count,
            "tags": tags,
            "allowed_roles": allowed_roles,
            "chunking_method": chunking_method,
            "chunk_size": chunk_size,
            "chunk_overlap": chunk_overlap,
            "metadata_columns": knowledge_ingest.decode_columns(metadata_columns),
            "data_columns": knowledge_ingest.decode_columns(data_columns),
            "collection_name": collection_name or collection_access.DEFAULT_KNOWLEDGE_COLLECTION,
        }
    except Exception as e:
        manager.logger.error("Error getting document info %s: %s", document_id, e, exc_info=True)
        return None


def save_query(manager, query: str, answer: str, user_id: int):
    """Save query and answer audit record."""
    query_id = str(uuid.uuid4())
    now = datetime.now().isoformat()
    cursor = manager.conn.cursor()
    cursor.execute(
        "INSERT INTO knowledge_queries (id, user_id, query, answer, created_at) VALUES (?, ?, ?, ?, ?)",
        (query_id, user_id, query, answer, now)
    )
    manager.conn.commit()
