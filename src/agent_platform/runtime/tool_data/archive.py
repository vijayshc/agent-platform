"""Durable, per-conversation storage for cached tool tables.

A conversation is a unit of work: the data behind ``D1`` must still be there
when the user comes back three turns later and asks for another view of it. The
in-process cache alone cannot promise that (a restart, a second worker, or a
turn-over sweep all lose it), so every cached table is also written next to the
conversation's checkpoint.

**Parquet** is the storage format because it matches the access pattern exactly:
a table is written once and later read *whole* to be rendered. Compared with the
JSON form of the same rows it is roughly an order of magnitude smaller (columnar
layout plus zstd) and several times faster to read back, it keeps the column
types and names, and it lets a future server-side aggregation read one column
without touching the rest. Each file is self-describing (its schema carries the
ref, tool name, call id, row count and column names), so ``index.json`` is a
lookup accelerator rather than the source of truth.

Layout::

    <conversation checkpoint dir>/tool-data/
        index.json      # {"next_index": 3, "refs": {"D1": {...}}}
        D1.parquet

Writes are atomic (temp file + ``os.replace``) and serialised per directory, so
two turns of one conversation cannot corrupt the index or leave a half-written
table behind.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path

logger = logging.getLogger("text2sql.agent_platform")

#: Subdirectory of a conversation's checkpoint directory holding cached tables.
ARCHIVE_DIRNAME = "tool-data"

INDEX_NAME = "index.json"
INDEX_VERSION = 1

#: Parquet file-level metadata keys. Stored as bytes; ``pa`` accepts both.
_META_PREFIX = "tool_data."
_META_REF = b"tool_data.ref"
_META_CALL = b"tool_data.call_id"
_META_TOOL = b"tool_data.tool_name"
_META_COLUMNS = b"tool_data.columns"
_META_TOTAL = b"tool_data.total_rows"

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


def _schema_names(columns: list[str], width: int) -> list[str]:
    """Unique, non-empty Parquet field names for ``columns``.

    A markdown table can repeat a header (``total`` twice) or leave one blank,
    and Parquet requires unique field names. The original names travel in the
    file metadata and are what callers see; these are only schema labels.
    """
    names: list[str] = []
    used: set[str] = set()
    for index in range(width):
        base = (columns[index] if index < len(columns) else "").strip() or f"col_{index + 1}"
        name = base
        suffix = 2
        while name in used:
            name = f"{base}_{suffix}"
            suffix += 1
        used.add(name)
        names.append(name)
    return names


def _json_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False).encode("utf-8")


def _meta_str(metadata: dict | None, key: bytes) -> str:
    if not metadata:
        return ""
    raw = metadata.get(key)
    if raw is None:
        return ""
    return raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)


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
            if isinstance(raw, dict):
                refs = raw.get("refs")
                data["refs"] = refs if isinstance(refs, dict) else {}
                next_index = raw.get("next_index")
                data["next_index"] = int(next_index) if isinstance(next_index, int) else 1
                data["version"] = int(raw.get("version") or INDEX_VERSION)
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
                columns = json.loads(_meta_str(metadata, _META_COLUMNS) or "[]")
                total = _meta_str(metadata, _META_TOTAL)
                refs[ref] = {
                    "call_id": _meta_str(metadata, _META_CALL),
                    "tool_name": _meta_str(metadata, _META_TOOL),
                    "columns": columns if isinstance(columns, list) else [],
                    "total_rows": int(total) if total.isdigit() else 0,
                    "cached_rows": pq.ParquetFile(path).metadata.num_rows,
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
        columns: list[str],
        rows: list[list[str]],
        total_rows: int,
        ref: str | None = None,
    ) -> str:
        """Write one table and return its reference.

        Re-saving a call id that is already archived keeps the reference it was
        first given, so a resumed run (or a replayed turn) never renumbers data
        the model has already been told about.
        """
        import pyarrow as pa
        from pyarrow import parquet as pq

        width = max(len(columns), max((len(row) for row in rows), default=0))
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
                and known.get("columns") == list(columns)
            ):
                # A resumed run replays its tool calls; the table is already on
                # disk, so do not rewrite it (nor its index entry).
                return resolved

            self.root.mkdir(parents=True, exist_ok=True)
            names = _schema_names(columns, width)
            arrays = {
                names[i]: pa.array([row[i] if i < len(row) else "" for row in rows], type=pa.string())
                for i in range(width)
            }
            table = pa.table(arrays) if arrays else pa.table({})
            table = table.replace_schema_metadata(
                {
                    _META_REF: resolved.encode("utf-8"),
                    _META_CALL: str(call_id).encode("utf-8"),
                    _META_TOOL: str(tool_name).encode("utf-8"),
                    _META_COLUMNS: _json_bytes(list(columns)),
                    _META_TOTAL: str(int(total_rows)).encode("utf-8"),
                }
            )
            path = self.root / f"{resolved}.parquet"
            tmp = self.root / f".{resolved}.parquet.tmp"
            pq.write_table(table, tmp, compression="zstd")
            os.replace(tmp, path)

            refs[resolved] = {
                "call_id": call_id,
                "tool_name": tool_name,
                "columns": list(columns),
                "total_rows": int(total_rows),
                "cached_rows": len(rows),
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
            columns = entry.get("columns") if entry else None
            if not isinstance(columns, list) or not columns:
                try:
                    columns = json.loads(_meta_str(metadata, _META_COLUMNS) or "[]")
                except Exception:
                    columns = []
            names = list(table.column_names)
            if not isinstance(columns, list) or not columns:
                columns = list(names)
            elif len(columns) < len(names):
                # A row can be wider than the header it was parsed under; the
                # schema filled the gap, so the payload's header must too.
                columns = [*columns, *names[len(columns):]]
            total = _meta_str(metadata, _META_TOTAL)
            data = table.to_pydict()
            rows = [
                ["" if data[name][i] is None else str(data[name][i]) for name in names]
                for i in range(table.num_rows)
            ]
            return {
                "call_id": (entry or {}).get("call_id") or _meta_str(metadata, _META_CALL),
                "tool_name": (entry or {}).get("tool_name") or _meta_str(metadata, _META_TOOL),
                "columns": list(columns),
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
