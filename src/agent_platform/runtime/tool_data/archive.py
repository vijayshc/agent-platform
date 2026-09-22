"""Durable, per-conversation storage for cached tool tables.

A conversation is a unit of work: the data behind ``D1`` must still be there
when the user comes back three turns later and asks for another view of it. The
in-process cache alone cannot promise that (a restart, a second worker, or a
turn-over sweep all lose it), so every cached table is also written next to the
conversation's checkpoint.

**Parquet is used for what it is: a typed columnar store.** Each column is written
with the type its producer declared — ``int64`` for an integer, ``float64`` for a
number, ``date32``/``time64`` for a date or time, ``bool`` for a boolean,
``string`` for text — and the declared schema also travels in the file metadata.
``decimal``, ``mixed`` and ``datetime`` are carried as text, because an exact
decimal must not become a float, a mixed column has no single Arrow type, and an
ISO-8601 datetime keeps its offset exactly. A value that comes back out is the
same value that went in: a number is a number and a missing value is ``null``,
never the text ``"NULL"``. Rendering therefore never re-derives a type from how a
value happens to look.

The file is self-describing (its schema carries the ref, tool name, call id, row
count and typed columns), so ``index.json`` is a lookup accelerator rather than
the source of truth.

Layout::

    <conversation checkpoint dir>/tool-data/
        index.json      # {"next_index": 3, "refs": {"D1": {...}}}
        D1.parquet

Writes are atomic (temp file + ``os.replace``) and serialised per directory, so
two turns of one conversation cannot corrupt the index or leave a half-written
table behind.
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any

from src.agent_platform.runtime.tool_data.table import COLUMN_TYPES, Column

logger = logging.getLogger("text2sql.agent_platform")

#: Subdirectory of a conversation's checkpoint directory holding cached tables.
ARCHIVE_DIRNAME = "tool-data"

INDEX_NAME = "index.json"
#: Bumped when the on-disk shape changes. v1 stored every column as text with a
#: bare name list; v2 stored declared types and native values; v3 carries
#: ``datetime`` as ISO text (v2 used an Arrow timestamp). An older file is not
#: read: its representation is not the one this version produces, and reading it
#: through a fallback is exactly the defect this format removes.
INDEX_VERSION = 3

#: Parquet file-level metadata keys. Stored as bytes; ``pa`` accepts both.
_META_REF = b"tool_data.ref"
_META_CALL = b"tool_data.call_id"
_META_TOOL = b"tool_data.tool_name"
_META_COLUMNS = b"tool_data.columns"
_META_TOTAL = b"tool_data.total_rows"
_META_FORMAT = b"tool_data.format"
_META_HASH = b"tool_data.content_hash"
#: Bumped when the on-disk representation changes. v2 carries datetimes as
#: ISO text (v1 used an Arrow timestamp); a v1 file is not read, because its
#: datetime rendering is not the one this version produces.
_FORMAT = b"typed-v2"

_LOCKS: dict[str, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()


def _lock_for(root: Path) -> threading.RLock:
    key = str(root)
    with _LOCKS_GUARD:
        lock = _LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _LOCKS[key] = lock
        return lock


def _json_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False).encode("utf-8")


def _row_hash(rows: list[list[Any]]) -> str:
    """A content hash of the rows, so a re-save can tell equal data from
    different data with the same shape."""
    import hashlib

    digest = hashlib.sha256()
    for row in rows:
        digest.update(json.dumps(row, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def _meta_str(metadata: dict | None, key: bytes) -> str:
    if not metadata:
        return ""
    raw = metadata.get(key)
    if raw is None:
        return ""
    return raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)


def _arrow_type(ctype: str):
    """The Arrow type that preserves ``ctype`` without loss.

    ``decimal``, ``mixed`` and ``datetime`` are carried as text: an exact decimal
    must not become a float, a mixed column has no single Arrow type, and an
    ISO-8601 datetime keeps its offset exactly (an Arrow timestamp would have to
    pick a timezone, and a column mixing naive and offset values would not
    survive). ``date`` and ``time`` have no such ambiguity and stay typed.
    """
    import pyarrow as pa

    return {
        "integer": pa.int64(),
        "number": pa.float64(),
        "decimal": pa.string(),
        "boolean": pa.bool_(),
        "string": pa.string(),
        "date": pa.date32(),
        "datetime": pa.string(),
        "time": pa.time64("us"),
        "mixed": pa.string(),
        # An all-null column has no values to type; it is stored as text and its
        # "unknown" declaration travels in the metadata.
        "unknown": pa.string(),
    }[ctype]


def _to_arrow(value: Any, ctype: str) -> Any:
    """A native JSON cell as the Arrow scalar for ``ctype``."""
    if value is None:
        return None
    if ctype == "date":
        return _dt.date.fromisoformat(value)
    if ctype == "time":
        return _dt.time.fromisoformat(value)
    if ctype == "integer":
        return int(value)
    if ctype == "number":
        return float(value)
    if ctype == "boolean":
        return bool(value)
    if ctype == "mixed":
        return json.dumps(value, ensure_ascii=False)
    if ctype == "unknown":
        return None
    # decimal, datetime and string are all text; their semantics live in the
    # declared type, and the exact text is what is preserved.
    return str(value)


def _from_arrow(value: Any, ctype: str) -> Any:
    """An Arrow scalar back as the native JSON cell the client receives."""
    if value is None:
        return None
    if ctype == "date":
        return value.isoformat()
    if ctype == "time":
        return value.isoformat()
    if ctype == "integer":
        return int(value)
    if ctype == "number":
        return float(value)
    if ctype == "boolean":
        return bool(value)
    if ctype == "mixed":
        return json.loads(value)
    if ctype == "unknown":
        return None
    return str(value)


def _parse_columns(raw: str) -> list[Column] | None:
    """Declared typed columns from a metadata JSON string, or ``None``."""
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(parsed, list) or not parsed:
        return None
    columns: list[Column] = []
    for entry in parsed:
        if not isinstance(entry, dict):
            return None
        name = entry.get("name")
        ctype = entry.get("type")
        if not isinstance(name, str) or not name or ctype not in COLUMN_TYPES:
            return None
        columns.append(Column(name=name, type=str(ctype)))
    return columns


class ToolDataArchive:
    """The Parquet + index store of one conversation (or run) directory."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self._lock = _lock_for(self.root)
        self._index: dict | None = None

    # ---------------------------------------------------------------- index
    @property
    def index_path(self) -> Path:
        return self.root / INDEX_NAME

    def _read_index(self) -> dict:
        if self._index is not None:
            return self._index
        data: dict = {"version": INDEX_VERSION, "next_index": 1, "refs": {}}
        try:
            raw = json.loads(self.index_path.read_text(encoding="utf-8"))
            if isinstance(raw, dict) and int(raw.get("version") or 0) == INDEX_VERSION:
                refs = raw.get("refs")
                data["refs"] = refs if isinstance(refs, dict) else {}
                next_index = raw.get("next_index")
                data["next_index"] = int(next_index) if isinstance(next_index, int) else 1
        except FileNotFoundError:
            pass
        except Exception:
            logger.warning("tool-data index unreadable; rebuilding from files", exc_info=True)
            data = self._rebuild_index()
        if not data["refs"] and self._parquet_files():
            data = self._rebuild_index()
        self._index = data
        return data

    def _rebuild_index(self) -> dict:
        """Reconstruct the index by reading each file's own metadata."""
        from pyarrow import parquet as pq

        refs: dict[str, dict] = {}
        highest = 0
        for path in sorted(self._parquet_files()):
            ref = path.stem
            try:
                schema = pq.read_schema(path)
                metadata = schema.metadata or {}
                columns = _parse_columns(_meta_str(metadata, _META_COLUMNS))
                if columns is None or _meta_str(metadata, _META_FORMAT) != _FORMAT.decode():
                    # A file from the untyped format. Its column types are gone;
                    # re-deriving them from the text is the defect this format
                    # removed, so the file is dropped rather than guessed at.
                    logger.info("dropping legacy untyped tool-data file %s", path)
                    continue
                total = _meta_str(metadata, _META_TOTAL)
                refs[ref] = {
                    "call_id": _meta_str(metadata, _META_CALL),
                    "tool_name": _meta_str(metadata, _META_TOOL),
                    "columns": [column.to_dict() for column in columns],
                    "total_rows": int(total) if total.isdigit() else 0,
                    "cached_rows": pq.ParquetFile(path).metadata.num_rows,
                    "hash": _meta_str(metadata, _META_HASH),
                    "file": path.name,
                }
            except Exception:
                logger.warning("dropping unreadable tool-data file %s", path, exc_info=True)
            highest = max(highest, _ref_number(ref))
        return {
            "version": INDEX_VERSION,
            "next_index": highest + 1,
            "refs": refs,
        }

    def _flush_index(self, index: dict) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        tmp = self.index_path.with_name(INDEX_NAME + ".tmp")
        tmp.write_text(json.dumps(index, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, self.index_path)

    def _parquet_files(self) -> list[Path]:
        try:
            return [p for p in self.root.glob("D*.parquet") if p.is_file()]
        except OSError:
            return []

    # ----------------------------------------------------------------- write
    def save(
        self,
        *,
        call_id: str,
        tool_name: str,
        columns: list[Column],
        rows: list[list[Any]],
        total_rows: int,
        ref: str | None = None,
    ) -> str:
        """Write one typed table and return its reference.

        Re-saving a call id that is already archived keeps the reference it was
        first given, so a resumed run (or a replayed turn) never renumbers data
        the model has already been told about.
        """
        import pyarrow as pa
        from pyarrow import parquet as pq

        schema = [column.to_dict() for column in columns]
        content_hash = _row_hash(rows)
        with self._lock:
            index = self._read_index()
            refs = index["refs"]
            resolved = ref
            if resolved is None:
                for known, entry in refs.items():
                    if call_id and entry.get("call_id") == call_id:
                        resolved = known
                        break
            if resolved is None:
                resolved = f"D{int(index.get('next_index') or 1)}"
                index["next_index"] = int(index.get("next_index") or 1) + 1

            path = self.root / f"{resolved}.parquet"
            known = refs.get(resolved)
            if (
                known is not None
                and path.exists()
                and known.get("cached_rows") == len(rows)
                and known.get("total_rows") == int(total_rows)
                and known.get("columns") == schema
                and known.get("hash") == content_hash
            ):
                # A resumed run replays its tool calls; the table is already on
                # disk, so do not rewrite it (nor its index entry). The content
                # hash is what makes this safe: the same shape with different
                # values must still be written.
                return resolved

            self.root.mkdir(parents=True, exist_ok=True)
            arrays = {}
            for position, column in enumerate(columns):
                values = [row[position] if position < len(row) else None for row in rows]
                arrays[column.name] = pa.array(
                    [_to_arrow(value, column.type) for value in values],
                    type=_arrow_type(column.type),
                )
            table = pa.table(arrays) if arrays else pa.table({})
            table = table.replace_schema_metadata(
                {
                    _META_REF: resolved.encode("utf-8"),
                    _META_CALL: str(call_id).encode("utf-8"),
                    _META_TOOL: str(tool_name).encode("utf-8"),
                    _META_COLUMNS: _json_bytes(schema),
                    _META_TOTAL: str(int(total_rows)).encode("utf-8"),
                    _META_FORMAT: _FORMAT,
                    _META_HASH: content_hash.encode("utf-8"),
                }
            )
            tmp = self.root / f".{resolved}.parquet.tmp"
            pq.write_table(table, tmp, compression="zstd")
            os.replace(tmp, path)

            refs[resolved] = {
                "call_id": call_id,
                "tool_name": tool_name,
                "columns": schema,
                "total_rows": int(total_rows),
                "cached_rows": len(rows),
                "hash": content_hash,
                "file": path.name,
                "created_at": time.time(),
            }
            self._flush_index(index)
            return resolved

    # ------------------------------------------------------------------ read
    def load(self, ref: str) -> dict | None:
        """The archived table behind ``ref``, or ``None`` when it is not here."""
        from pyarrow import parquet as pq

        with self._lock:
            entry = self._read_index()["refs"].get(ref)
            path = self.root / (entry.get("file") if entry else f"{ref}.parquet")
            if not path.exists():
                return None
            try:
                table = pq.read_table(path)
            except Exception:
                logger.warning("tool-data file %s could not be read", path, exc_info=True)
                return None
            metadata = table.schema.metadata or {}
            if _meta_str(metadata, _META_FORMAT) != _FORMAT.decode():
                # A file written by an older representation. Its values are not the
                # ones this version produces, so it is dropped rather than read
                # through a fallback — including when an old index still lists it.
                logger.info("ignoring tool-data file %s written in another format", path)
                return None
            columns = None
            if isinstance(entry, dict):
                columns = _parse_columns(json.dumps(entry.get("columns")))
            if columns is None:
                columns = _parse_columns(_meta_str(metadata, _META_COLUMNS))
            if columns is None or len(columns) != len(table.column_names):
                logger.warning("tool-data file %s has no usable typed schema", path)
                return None
            data = table.to_pydict()
            rows = [
                [_from_arrow(data[column.name][i], column.type) for column in columns]
                for i in range(table.num_rows)
            ]
            total = _meta_str(metadata, _META_TOTAL)
            return {
                "call_id": (entry or {}).get("call_id") or _meta_str(metadata, _META_CALL),
                "tool_name": (entry or {}).get("tool_name") or _meta_str(metadata, _META_TOOL),
                "columns": [column.to_dict() for column in columns],
                "rows": rows,
                "total_rows": int(total) if total.isdigit() else len(rows),
            }

    def ref_for_call(self, call_id: str) -> str | None:
        if not call_id:
            return None
        with self._lock:
            for ref, entry in self._read_index()["refs"].items():
                if entry.get("call_id") == call_id:
                    return ref
        return None

    def refs(self) -> list[str]:
        with self._lock:
            return list(self._read_index()["refs"].keys())

    def clear(self) -> None:
        with self._lock:
            for path in self.root.glob("*"):
                try:
                    path.unlink()
                except OSError:
                    logger.debug("could not remove %s", path, exc_info=True)
            self._index = {"version": INDEX_VERSION, "next_index": 1, "refs": {}}


def _ref_number(ref: str) -> int:
    digits = ref[1:] if ref[:1] in ("D", "d") else ref
    return int(digits) if digits.isdigit() else 0
