"""Upload-time ingestion options and their persistence for the Knowledge base.

Everything the uploader can tune lives here: the chunking method/size/overlap
and, for CSV/Excel, which columns are embedded data versus preserved metadata.
:class:`IngestOptions` is the single normalised shape the routes, the manager
and the async workers pass around, so validation happens once at the API edge.

Schema added idempotently by :func:`ensure_ingest_schema`:

* ``knowledge_documents.chunking_method`` / ``chunk_size`` / ``chunk_overlap``
* ``knowledge_documents.metadata_columns`` / ``data_columns`` (JSON arrays)
* ``knowledge_chunk_metadata`` -- one row per metadata key per chunk
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional

from config.config import CHUNK_SIZE, CHUNK_OVERLAP
from src.utils import chunking, collection_access

_DOCUMENT_COLUMNS: tuple[tuple[str, str], ...] = (
    ("chunking_method", "TEXT"),
    ("chunk_size", "INTEGER"),
    ("chunk_overlap", "INTEGER"),
    ("metadata_columns", "TEXT"),
    ("data_columns", "TEXT"),
    ("collection_name", "TEXT"),
)

_CHUNK_METADATA_DDL = """
CREATE TABLE IF NOT EXISTS knowledge_chunk_metadata (
    id TEXT PRIMARY KEY,
    chunk_id TEXT NOT NULL,
    document_id TEXT NOT NULL,
    key TEXT NOT NULL,
    value TEXT,
    created_at TIMESTAMP NOT NULL,
    FOREIGN KEY (document_id) REFERENCES knowledge_documents(id) ON DELETE CASCADE
)
"""


@dataclass
class IngestOptions:
    """Normalised, validated upload properties."""

    chunking_method: str = chunking.DEFAULT_METHOD
    chunk_size: int = CHUNK_SIZE
    chunk_overlap: int = CHUNK_OVERLAP
    metadata_columns: List[str] = field(default_factory=list)
    data_columns: List[str] = field(default_factory=list)
    collection_name: str = collection_access.DEFAULT_KNOWLEDGE_COLLECTION

    def to_columns(self) -> Dict[str, Any]:
        """Column values for the ``knowledge_documents`` INSERT."""
        return {
            "chunking_method": self.chunking_method,
            "chunk_size": self.chunk_size,
            "chunk_overlap": self.chunk_overlap,
            "metadata_columns": json.dumps(self.metadata_columns),
            "data_columns": json.dumps(self.data_columns),
            "collection_name": self.collection_name,
        }


def _as_list(value: Any) -> List[str]:
    """Accept a list, a JSON array string, or a comma-separated string."""
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        raw: Iterable[Any] = value
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        if text.startswith("["):
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                parsed = None
            raw = parsed if isinstance(parsed, list) else [text]
        else:
            raw = text.split(",")
    else:
        raw = [value]
    cleaned: List[str] = []
    for item in raw:
        name = str(item).strip() if item is not None else ""
        if name and name not in cleaned:
            cleaned.append(name)
    return cleaned


def normalize_options(raw: Optional[Dict[str, Any]] = None) -> IngestOptions:
    """Validate an upload payload into :class:`IngestOptions`.

    Raises ``ValueError`` with a user-facing message for any bad value so the
    route can answer 400 without touching the ingestion pipeline.
    """
    data = raw or {}
    method = chunking.normalize_method(data.get("chunking_method"))
    size = chunking.validate_size(data.get("chunk_size"), CHUNK_SIZE)
    overlap = chunking.validate_overlap(data.get("chunk_overlap"), size, CHUNK_OVERLAP)
    return IngestOptions(
        chunking_method=method,
        chunk_size=size,
        chunk_overlap=overlap,
        metadata_columns=_as_list(data.get("metadata_columns")),
        data_columns=_as_list(data.get("data_columns")),
        collection_name=str(data.get("collection_name") or "").strip()
        or collection_access.DEFAULT_KNOWLEDGE_COLLECTION,
    )


def decode_columns(value: Any) -> List[str]:
    """Read a JSON column array persisted on a document (tolerant of legacy NULL)."""
    if not value:
        return []
    if isinstance(value, list):
        return [str(v) for v in value]
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return _as_list(value)
    return [str(v) for v in parsed] if isinstance(parsed, list) else []


def ensure_ingest_schema(conn) -> None:
    """Add the ingest columns and the chunk-metadata table (idempotent)."""
    present = {row[1] for row in conn.execute("PRAGMA table_info(knowledge_documents)")}
    for name, declaration in _DOCUMENT_COLUMNS:
        if name not in present:
            conn.execute(f"ALTER TABLE knowledge_documents ADD COLUMN {name} {declaration}")
    conn.execute(_CHUNK_METADATA_DDL)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_knowledge_chunk_metadata_chunk "
        "ON knowledge_chunk_metadata(chunk_id)"
    )
    conn.commit()


def insert_chunk_metadata(conn, document_id: str, chunk_id: str, metadata: Dict[str, Any]) -> None:
    """Persist a chunk's metadata key/values (skips empty values)."""
    if not metadata:
        return
    now = datetime.now().isoformat()
    for key, value in metadata.items():
        if value is None or str(value).strip() == "":
            continue
        conn.execute(
            "INSERT INTO knowledge_chunk_metadata (id, chunk_id, document_id, key, value, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (str(uuid.uuid4()), chunk_id, document_id, str(key), str(value), now),
        )


def fetch_chunk_metadata(conn, chunk_ids: Iterable[str]) -> Dict[str, Dict[str, str]]:
    """Return ``{chunk_id: {key: value}}`` for the given chunk ids."""
    ids = [str(c) for c in chunk_ids if c]
    if not ids:
        return {}
    placeholders = ",".join("?" for _ in ids)
    rows = conn.execute(
        f"SELECT chunk_id, key, value FROM knowledge_chunk_metadata WHERE chunk_id IN ({placeholders})",
        ids,
    ).fetchall()
    result: Dict[str, Dict[str, str]] = {}
    for chunk_id, key, value in rows:
        result.setdefault(str(chunk_id), {})[str(key)] = value
    return result
