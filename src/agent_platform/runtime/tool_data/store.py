"""The cache of full tool-result tables, keyed by conversation.

A tool that returns tabular data is usually returning far more rows than the
model should ever read. The middleware clips the result to a sample and hands
the model a short reference (``D1``, ``D2``, …); the full table is cached here so
``#TABLE_D1`` / ``#CHART_D1`` can be rendered without calling the tool again.

The cache has two tiers:

* **memory** — an LRU of recently used tables, bounded by a row budget, so the
  common case (render the result you just produced) never touches the disk;
* **archive** — one Parquet file per table in the conversation's checkpoint
  directory (see :mod:`archive`), so a reference stays valid for the whole
  conversation and survives a restart.

Both tiers are scoped by :class:`~...tool_data.scope.ToolDataScope`. References
are handed out monotonically *per conversation*, never reused, so a table cached
in turn 1 is still ``D1`` in turn 9 — which is what lets the model re-plot
earlier data instead of re-running the query.
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field

from src.agent_platform.runtime.tool_data.archive import ToolDataArchive
from src.agent_platform.runtime.tool_data.scope import ToolDataScope

#: How long an untouched conversation stays resident in memory. The archive is
#: the durable tier, so dropping a bucket only costs a re-read.
DEFAULT_TTL_SECONDS = 1800

#: Rows kept across all tables of one conversation before the least recently
#: used table is dropped from memory (it stays in the archive).
DEFAULT_MEMORY_ROW_BUDGET = 200_000


@dataclass
class ToolData:
    """One cached tool result."""

    call_id: str
    ref: str = ""
    tool_name: str = ""
    columns: list[str] = field(default_factory=list)
    rows: list[list[str]] = field(default_factory=list)
    total_rows: int = 0

    @property
    def returned_rows(self) -> int:
        return len(self.rows)

    @property
    def truncated(self) -> bool:
        return self.returned_rows < self.total_rows

    @property
    def key(self) -> str:
        """The identifier the client resolves against (the short ref)."""
        return self.ref or self.call_id

    def to_payload(self) -> dict:
        return {
            "call_id": self.key,
            "ref": self.ref,
            "source_call_id": self.call_id,
            "tool_name": self.tool_name,
            "columns": self.columns,
            "rows": self.rows,
            "total_rows": self.total_rows,
            "returned_rows": self.returned_rows,
            "truncated": self.truncated,
        }


class _Bucket:
    """One conversation's memory tier."""

    __slots__ = ("items", "by_call", "runs", "archive", "rows", "touched", "next_index")

    def __init__(self, archive: ToolDataArchive | None) -> None:
        self.items: OrderedDict[str, ToolData] = OrderedDict()
        self.by_call: dict[str, str] = {}
        self.runs: dict[int, list[str]] = {}
        self.archive = archive
        self.rows = 0
        self.touched = time.monotonic()
        self.next_index = 1

    def remember(self, data: ToolData, budget: int) -> None:
        ref = data.ref or data.call_id
        existing = self.items.get(ref)
        if existing is not None:
            self.rows -= existing.returned_rows
            self.items.pop(ref, None)
        self.items[ref] = data
        self.rows += data.returned_rows
        self.by_call[data.call_id] = ref
        while self.rows > budget and len(self.items) > 1:
            _, dropped = self.items.popitem(last=False)
            self.rows -= dropped.returned_rows

    def forget(self, ref: str) -> None:
        dropped = self.items.pop(ref, None)
        if dropped is not None:
            self.rows -= dropped.returned_rows


class ToolDataStore:
    """Conversation-scoped cache of cached tool tables."""

    def __init__(
        self,
        ttl_seconds: float = DEFAULT_TTL_SECONDS,
        memory_row_budget: int = DEFAULT_MEMORY_ROW_BUDGET,
    ) -> None:
        self._lock = threading.Lock()
        self._buckets: dict[str, _Bucket] = {}
        self._archives: dict[str, ToolDataArchive] = {}
        self._ttl = ttl_seconds
        self._budget = memory_row_budget

    # ------------------------------------------------------------- internals
    def _archive_for(self, scope: ToolDataScope) -> ToolDataArchive | None:
        if not scope.persistent or scope.root is None:
            return None
        archive = self._archives.get(scope.key)
        if archive is None:
            archive = ToolDataArchive(scope.root)
            self._archives[scope.key] = archive
        return archive

    def _bucket_locked(self, scope: ToolDataScope) -> _Bucket:
        bucket = self._buckets.get(scope.key)
        if bucket is None:
            bucket = _Bucket(self._archive_for(scope))
            self._buckets[scope.key] = bucket
        return bucket

    def _to_data(self, ref: str, payload: dict) -> ToolData:
        return ToolData(
            call_id=str(payload.get("call_id") or ""),
            ref=ref,
            tool_name=str(payload.get("tool_name") or ""),
            columns=list(payload.get("columns") or []),
            rows=[list(row) for row in payload.get("rows") or []],
            total_rows=int(payload.get("total_rows") or 0),
        )

    # ---------------------------------------------------------------- public
    def put(
        self,
        scope: ToolDataScope | None,
        data: ToolData,
        run_id: int | None = None,
    ) -> ToolData:
        """Cache a result, archive it, and give it its conversation reference.

        Re-caching the same tool call (a resumed run re-processing a message)
        keeps the original reference instead of consuming a new one.
        """
        if scope is None:
            return data
        with self._lock:
            self._sweep_locked()
            bucket = self._bucket_locked(scope)
            ref = bucket.by_call.get(data.call_id) or (
                bucket.archive.ref_for_call(data.call_id) if bucket.archive else None
            )
            if bucket.archive is not None:
                ref = bucket.archive.save(
                    call_id=data.call_id,
                    tool_name=data.tool_name,
                    columns=list(data.columns),
                    rows=[list(row) for row in data.rows],
                    total_rows=data.total_rows,
                    ref=ref,
                )
            elif not ref:
                ref = f"D{bucket.next_index}"
                bucket.next_index += 1
            data.ref = ref
            bucket.remember(data, self._budget)
            if run_id is not None:
                cached = bucket.runs.setdefault(int(run_id), [])
                if ref not in cached:
                    cached.append(ref)
            bucket.touched = time.monotonic()
        return data

    def resolve(self, scope: ToolDataScope | None, token: str) -> ToolData | None:
        """Resolve a model-written reference to a cached result.

        Accepts, in order: the exact ``D1`` reference, a bare ``1`` (the letter
        dropped), the real provider ``tool_call_id``, and any of those with a
        trailing label the model appended (``D1_line``). Call ids are opaque, so
        peeling ``_segment`` pieces is how ``<id>_line`` still resolves.
        """
        if scope is None:
            return None
        text = str(token or "").strip()
        if not text:
            return None
        candidates = [text]
        if text.isdigit():
            candidates.insert(0, f"D{text}")
        # Peel trailing `_segment` labels, longest first.
        for base in list(candidates):
            piece = base
            while "_" in piece:
                piece = piece.rsplit("_", 1)[0]
                if piece:
                    candidates.append(piece)
        with self._lock:
            bucket = self._buckets.get(scope.key)
            if bucket is not None:
                for candidate in candidates:
                    data = bucket.items.get(candidate)
                    if data is not None:
                        bucket.items.move_to_end(candidate)
                        bucket.touched = time.monotonic()
                        return data
                    ref = bucket.by_call.get(candidate)
                    if ref and ref in bucket.items:
                        bucket.items.move_to_end(ref)
                        bucket.touched = time.monotonic()
                        return bucket.items[ref]
            archive = self._archive_for(scope)
            if archive is None:
                return None
            for candidate in candidates:
                ref = archive.ref_for_call(candidate) or candidate
                payload = archive.load(ref)
                if payload is not None:
                    data = self._to_data(ref, payload)
                    if bucket is None:
                        bucket = self._bucket_locked(scope)
                    bucket.remember(data, self._budget)
                    bucket.touched = time.monotonic()
                    return data
        return None

    def for_run(self, scope: ToolDataScope | None, run_id: int | None) -> list[ToolData]:
        """The results cached during one run, in reference order."""
        if scope is None or run_id is None:
            return []
        with self._lock:
            bucket = self._buckets.get(scope.key)
            refs = list(bucket.runs.get(int(run_id), [])) if bucket else []
        resolved: list[ToolData] = []
        for ref in refs:
            data = self.resolve(scope, ref)
            if data is not None:
                resolved.append(data)
        return resolved

    def clear(self, scope: ToolDataScope | None) -> None:
        """Drop a conversation's cache: memory and archived files alike.

        The archive is rebuilt from the scope when it is not resident, so a
        conversation whose files were written by another process (or an earlier
        boot) is still emptied.
        """
        if scope is None:
            return
        with self._lock:
            bucket = self._buckets.pop(scope.key, None)
            archive = self._archives.pop(scope.key, None)
            if archive is None:
                archive = bucket.archive if bucket is not None else self._archive_for(scope)
            if archive is not None:
                archive.clear()

    def _sweep_locked(self) -> None:
        now = time.monotonic()
        stale = [
            key
            for key, bucket in self._buckets.items()
            if now - bucket.touched > self._ttl
        ]
        for key in stale:
            self._buckets.pop(key, None)
            self._archives.pop(key, None)


#: The process-wide cache every runtime reads and writes.
TOOL_DATA_STORE = ToolDataStore()
