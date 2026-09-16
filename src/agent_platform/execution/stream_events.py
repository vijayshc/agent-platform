"""Record a runtime stream item into the in-memory span replay buffer.

The runtime yields normalized events (token/chat/tool/status/...) for the SSE
stream and the AG-UI replay.  Phoenix is the durable trace store; this helper
only mirrors an item into :class:`SpanSink` so a *live* run can be replayed
before its spans land in Phoenix.
"""

from __future__ import annotations

from typing import Any

from src.agent_platform.execution.span_sink import SpanSink


def record_stream_event(run_id: int, item: dict[str, Any]) -> None:
    etype = item.get("type") or "event"
    detail = {k: v for k, v in item.items() if k != "type"}
    agent_name = item.get("agent") or item.get("agent_name") or item.get("author")
    tool_name = item.get("tool_name") or item.get("tool") or item.get("name")
    span_name = item.get("span_name") or (
        f"tool:{tool_name}" if tool_name else (f"agent:{agent_name}" if agent_name else None)
    )
    duration_ms = item.get("duration_ms")
    try:
        SpanSink.record_event(
            run_id,
            etype,
            detail=detail,
            agent_name=agent_name,
            tool_name=tool_name,
            span_name=span_name,
            duration_ms=duration_ms,
        )
    except Exception:
        pass
