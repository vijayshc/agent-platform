"""Span/event sink for MAF OTEL exporters and native workflow/agent events."""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Any, Optional

from src.agent_platform.execution.span_formatter import (
    attach_io_to_event,
    extract_io,
    normalize_event_detail,
    serialize_attr,
)
from src.models.secrets import redact_value


class SpanSink:
    # Q: where does the developer trace live now?
    # A: Arize Phoenix (via OpenInference/OTEL). We therefore only keep a small
    #    bounded in-memory ring buffer per run to serve the AG-UI replay and
    #    /runs/<id>/events surface while a run is live (and for the few moments
    #    after). Nothing is written to SQLite, which removes the synchronous
    #    per-event insert that serialized concurrent runs on a single DB write
    #    lock and bloated the database with a full debug trace.

    # Per-run in-memory buffer. Keyed by integer run id. Bounded to cap memory
    # and older entries are dropped so the buffer never grows unbounded.
    _buffers: dict[int, list[dict[str, Any]]] = {}
    _lock = threading.Lock()
    _MAX_BUFFERED = 2000

    @staticmethod
    def _push(run_id: int, row: dict[str, Any]) -> None:
        with SpanSink._lock:
            buf = SpanSink._buffers.get(run_id)
            if buf is None:
                buf = []
                SpanSink._buffers[run_id] = buf
            buf.append(row)
            if len(buf) > SpanSink._MAX_BUFFERED:
                del buf[: len(buf) - SpanSink._MAX_BUFFERED]

    @staticmethod
    def _reset() -> None:
        """Clear the in-memory buffers (test isolation)."""
        with SpanSink._lock:
            SpanSink._buffers.clear()

    @staticmethod
    def _evict_old() -> None:
        # Trim the registry of buffers whose runs are no longer active (best
        # effort). Called opportunistically so memory stays bounded under load.
        with SpanSink._lock:
            if len(SpanSink._buffers) <= 256:
                return
            keys = list(SpanSink._buffers.keys())
            for k in keys[: len(keys) // 2]:
                SpanSink._buffers.pop(k, None)

    @staticmethod
    def record_event(
        run_id: int,
        event_type: str,
        source: str = "runtime",
        detail: dict[str, Any] | None = None,
        agent_name: Optional[str] = None,
        server_id: Optional[str] = None,
        tool_name: Optional[str] = None,
        span_name: Optional[str] = None,
        span_id: Optional[str] = None,
        parent_span_id: Optional[str] = None,
        duration_ms: Optional[float] = None,
        timestamp: Optional[str] = None,
    ) -> None:
        # Persist nothing. Traces live in Phoenix. Build the same redacted,
        # normalized row so the in-memory replay path stays consistent, but do
        # NOT open a SQLite connection or issue any INSERT.
        detail_copy = redact_value(dict(detail or {}))
        payload = normalize_event_detail(event_type, detail_copy if isinstance(detail_copy, dict) else {})
        payload = redact_value(payload) if isinstance(payload, dict) else payload
        if tool_name is None:
            tool_name = payload.get("tool_name") or None if isinstance(payload, dict) else None
        now = timestamp or datetime.now(timezone.utc).isoformat(sep=" ", timespec="milliseconds")
        row = {
            "run_id": run_id,
            "ts": now,
            "event_type": event_type,
            "source": source,
            "agent_name": agent_name,
            "server_id": server_id,
            "tool_name": tool_name,
            "span_name": span_name,
            "span_id": span_id,
            "parent_span_id": parent_span_id,
            "duration_ms": duration_ms,
            "detail": payload,
        }
        SpanSink._push(run_id, row)

    @staticmethod
    def record_span(run_id: int, span: Any) -> None:
        name = getattr(span, "name", "") or ""
        attrs = dict(getattr(span, "attributes", {}) or {})
        start_ns = getattr(span, "start_time", 0) or 0
        end_ns = getattr(span, "end_time", 0) or 0
        duration_ms = ((end_ns - start_ns) / 1e6) if start_ns and end_ns else None
        ts_str = None
        if start_ns:
            ts = datetime.fromtimestamp(start_ns / 1e9, tz=timezone.utc)
            ts_str = ts.isoformat(sep=" ", timespec="milliseconds")
        ctx = getattr(span, "get_span_context", lambda: None)()
        span_id = format(getattr(ctx, "span_id", 0), "016x") if ctx else None
        parent = getattr(span, "parent", None)
        parent_span_id = format(getattr(parent, "span_id", 0), "016x") if parent else None

        event_type = "span"
        tool_name = None
        agent_name = None
        if name.startswith("chat "):
            event_type = "chat"
        elif name.startswith("execute_tool "):
            event_type = "execute_tool"
            tool_name = name.replace("execute_tool ", "", 1)
            if tool_name == "load_skill":
                event_type = "skill_load"
        elif name.startswith("invoke_agent "):
            event_type = "invoke_agent"
            agent_name = name.replace("invoke_agent ", "", 1)

        serialized_attrs = redact_value(
            {k: serialize_attr(k, v) for k, v in attrs.items() if k != "workflow.definition"}
        )
        if tool_name is None:
            tool_name = serialized_attrs.get("gen_ai.tool.name")
        if agent_name is None:
            agent_name = serialized_attrs.get("gen_ai.agent.name")

        io = extract_io({"attributes": serialized_attrs}, serialized_attrs)
        detail = redact_value(
            {
                "span_name": name,
                "attributes": serialized_attrs,
                "model": io.get("model") or serialized_attrs.get("gen_ai.response.model") or serialized_attrs.get("gen_ai.request.model"),
                "input_tokens": attrs.get("gen_ai.usage.input_tokens"),
                "output_tokens": attrs.get("gen_ai.usage.output_tokens"),
            }
        )
        for key in ("prompt", "response", "arguments", "result"):
            if io.get(key) not in (None, ""):
                # invoke_agent wraps tool execution + the later chat. Its
                # gen_ai.output is the *final* assistant text, produced after
                # child tools. Promoting it to ``response`` makes the LLM
                # summary appear on an event that sorts before execute_tool.
                if key == "response" and event_type == "invoke_agent":
                    continue
                detail[key] = io[key]
        detail = redact_value(detail)

        SpanSink.record_event(
            run_id,
            event_type,
            source="otel",
            detail=detail,
            agent_name=agent_name,
            tool_name=tool_name,
            span_name=name,
            span_id=span_id,
            parent_span_id=parent_span_id,
            duration_ms=duration_ms,
            timestamp=ts_str,
        )

    @staticmethod
    def get_events(run_id: int) -> list[dict[str, Any]]:
        # Replay from the in-memory buffer only. No DB read: there is nothing on
        # disk to read any more.
        with SpanSink._lock:
            rows = list(SpanSink._buffers.get(run_id, []))
        agent_by_span_id = {
            row["span_id"]: row["agent_name"]
            for row in rows
            if row.get("span_id") and row.get("agent_name")
        }
        for row in rows:
            if not row.get("agent_name") and row.get("parent_span_id"):
                parent_agent = agent_by_span_id.get(row["parent_span_id"])
                if parent_agent:
                    row["agent_name"] = parent_agent
        rows = [attach_io_to_event(row) for row in rows]
        rows.sort(key=_event_sort_key)
        SpanSink._evict_old()
        return rows


_EVENT_TYPE_PRIORITY: dict[str, int] = {
    "status": 0,
    "chat": 1,
    "tool_call": 2,
    "approval_request": 3,
    "hitl_decision": 4,
    "execute_tool": 5,
    "skill_load": 5,
    "tool_result": 5,
    "error": 6,
    "invoke_agent": 7,
}


def _ts_ms(value: Any) -> float:
    if not value:
        return 0.0
    raw = str(value).strip().replace("T", " ", 1)
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return 0.0
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp() * 1000.0


def _event_sort_key(event: dict[str, Any]) -> tuple:
    """Order by when the span/event started, using type priority for tie-breaking."""
    return (
        _ts_ms(event.get("ts")),
        _EVENT_TYPE_PRIORITY.get(event.get("event_type", ""), 99),
        int(event.get("id") or 0),
    )
