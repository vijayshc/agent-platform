"""Span/event sink for MAF OTEL exporters and native workflow/agent events."""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from typing import Any, Optional

from src.agent_platform import db
from src.agent_platform.execution.run_store import RunStore
from src.models.secrets import MASK, is_secret_key, redact_value


def _is_secret(value: Any) -> bool:
    return type(value).__name__ == "SecretString"


def _json_default(obj: Any) -> str:
    if _is_secret(obj):
        return MASK
    return str(obj)

ATTR_CAP = 500
BODY_CAP = 100_000

# MAF / OTel GenAI semantic conventions (agent_framework.observability.OtelAttr).
_PROMPT_KEYS = (
    "prompt",
    "gen_ai.prompt",
    "gen_ai.input.messages",
    "input",
    "gen_ai.user.message",
)
_RESPONSE_KEYS = (
    "response",
    "gen_ai.completion",
    "gen_ai.output.messages",
    "gen_ai.output",
    "output",
    "gen_ai.assistant.message",
    "gen_ai.choice",
)
_ARGUMENT_KEYS = (
    "arguments",
    "gen_ai.tool.call.arguments",
)
_RESULT_KEYS = (
    "result",
    "gen_ai.tool.call.result",
    "result_preview",
)
_MODEL_KEYS = (
    "model",
    "gen_ai.response.model",
    "gen_ai.request.model",
)
_BODY_EXACT = frozenset(
    _PROMPT_KEYS
    + _RESPONSE_KEYS
    + _ARGUMENT_KEYS
    + _RESULT_KEYS
    + (
        "gen_ai.system_instructions",
        "gen_ai.system.message",
        "gen_ai.tool.message",
        "completion",
        "content",
        "extra_content",
        "thought_signature",
        "_extra_content",
    )
)
_BODY_PREFIXES = (
    "gen_ai.prompt",
    "gen_ai.completion",
    "gen_ai.input",
    "gen_ai.output",
    "gen_ai.system_instructions",
    "gen_ai.tool.call.arguments",
    "gen_ai.tool.call.result",
    "gen_ai.user.message",
    "gen_ai.assistant.message",
    "gen_ai.choice",
)


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
        payload = _normalize_detail(event_type, detail_copy if isinstance(detail_copy, dict) else {})
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
            {k: _serialize_attr(k, v) for k, v in attrs.items() if k != "workflow.definition"}
        )
        if tool_name is None:
            tool_name = serialized_attrs.get("gen_ai.tool.name")
        if agent_name is None:
            agent_name = serialized_attrs.get("gen_ai.agent.name")

        io = _extract_io({"attributes": serialized_attrs}, serialized_attrs)
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
        rows = [_attach_io(row) for row in rows]
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


def _is_body_key(key: str) -> bool:
    k = str(key)
    if k in _BODY_EXACT:
        return True
    return any(k == prefix or k.startswith(prefix + ".") for prefix in _BODY_PREFIXES)


def _serialize_attr(key: str, value: Any) -> str:
    if is_secret_key(key) or _is_secret(value):
        return MASK
    text = _to_text(value)
    cap = BODY_CAP if _is_body_key(key) else ATTR_CAP
    if len(text) > cap:
        return text[:cap]
    return text


def _to_text(value: Any) -> str:
    value = redact_value(value)
    if value is None:
        return ""
    if isinstance(value, bytes):
        try:
            return redact_value(value.decode("utf-8"))
        except Exception:
            return str(value)
    if isinstance(value, str):
        return value
    try:
        return redact_value(json.dumps(value, ensure_ascii=False, default=_json_default))
    except Exception:
        return redact_value(str(value))


def _cap(value: Any, limit: int = BODY_CAP) -> Any:
    value = redact_value(value)
    if isinstance(value, (dict, list)):
        raw = json.dumps(value, ensure_ascii=False, default=_json_default)
        if len(raw) <= limit:
            return value
        return raw[:limit]
    if isinstance(value, str):
        return value if len(value) <= limit else value[:limit]
    text = _to_text(value)
    return text if len(text) <= limit else text[:limit]


def _maybe_json(value: Any) -> Any:
    if _is_secret(value):
        return MASK
    if isinstance(value, (bytes, bytearray)):
        try:
            value = value.decode("utf-8")
        except Exception:
            return redact_value(value)
    if isinstance(value, str):
        stripped = value.strip()
        if stripped[:1] in "{[":
            try:
                return redact_value(json.loads(value))
            except Exception:
                return redact_value(value)
        return redact_value(value)
    return redact_value(value)


def _parts_text(parts: Any) -> str:
    parts = redact_value(parts)
    if parts is None:
        return ""
    if isinstance(parts, str):
        return parts
    if isinstance(parts, dict):
        return redact_value(
            str(
                parts.get("content")
                or parts.get("text")
                or parts.get("response")
                or parts.get("arguments")
                or json.dumps(parts, ensure_ascii=False, default=_json_default)
            )
        )
    if isinstance(parts, list):
        chunks: list[str] = []
        for part in parts:
            if isinstance(part, str):
                chunks.append(part)
            elif isinstance(part, dict):
                ptype = str(part.get("type") or "").lower()
                text_val = part.get("content") or part.get("text") or part.get("thought") or part.get("reasoning")
                if text_val not in (None, ""):
                    chunks.append(str(text_val))
                elif ptype in {"tool_call", "function_call", "function", "tool_use"} or "function" in part or "function_call" in part:
                    fn = part.get("function") if isinstance(part.get("function"), dict) else {}
                    name = part.get("name") or fn.get("name") or (part.get("tool") if isinstance(part.get("tool"), str) else "")
                    args = part.get("arguments") if part.get("arguments") is not None else (fn.get("arguments") if fn.get("arguments") is not None else (part.get("args") if part.get("args") is not None else part.get("input")))
                    if isinstance(args, (dict, list)):
                        args_str = json.dumps(args, ensure_ascii=False, default=_json_default)
                    else:
                        args_str = str(args or "")
                    chunks.append(f"[tool {name}] {args_str}".strip())
                elif ptype in {"tool_call_response", "function_result", "tool_result"}:
                    res = part.get("response") if part.get("response") is not None else (part.get("result") if part.get("result") is not None else part.get("content"))
                    if isinstance(res, (dict, list)):
                        res_str = json.dumps(res, ensure_ascii=False, default=_json_default)
                    else:
                        res_str = str(res or "")
                    chunks.append(res_str)
                elif part.get("tool_calls"):
                    for tc in part.get("tool_calls") if isinstance(part.get("tool_calls"), list) else []:
                        if isinstance(tc, dict):
                            fn = tc.get("function") if isinstance(tc.get("function"), dict) else tc
                            name = tc.get("name") or fn.get("name") or ""
                            args = tc.get("arguments") if tc.get("arguments") is not None else fn.get("arguments")
                            if isinstance(args, (dict, list)):
                                args_str = json.dumps(args, ensure_ascii=False, default=_json_default)
                            else:
                                args_str = str(args or "")
                            chunks.append(f"[tool {name}] {args_str}".strip())
                else:
                    chunks.append(str(part))
            else:
                chunks.append(str(part))
        return redact_value("\n\n".join(chunk for chunk in chunks if chunk))
    return redact_value(str(parts))


def _format_messages(value: Any) -> Any:
    parsed = _maybe_json(value)
    if parsed is None or parsed == "":
        return None
    if isinstance(parsed, list) and parsed and isinstance(parsed[0], dict):
        first = parsed[0]
        if "role" in first or "parts" in first or "content" in first or "tool_calls" in first:
            lines: list[str] = []
            for msg in parsed:
                if not isinstance(msg, dict):
                    lines.append(str(msg))
                    continue
                role = str(msg.get("role") or "message")
                body = _parts_text(msg.get("parts") if "parts" in msg else msg.get("content"))
                if not body and msg.get("tool_calls"):
                    tc_chunks: list[str] = []
                    for tc in msg.get("tool_calls", []):
                        if isinstance(tc, dict):
                            fn = tc.get("function") if isinstance(tc.get("function"), dict) else tc
                            name = tc.get("name") or fn.get("name") or ""
                            args = tc.get("arguments") if tc.get("arguments") is not None else fn.get("arguments")
                            if isinstance(args, (dict, list)):
                                args_str = json.dumps(args, ensure_ascii=False, default=_json_default)
                            else:
                                args_str = str(args or "")
                            tc_chunks.append(f"[tool {name}] {args_str}".strip())
                    body = "\n\n".join(tc_chunks)
                elif not body and msg.get("function_call"):
                    fn = msg.get("function_call")
                    if isinstance(fn, dict):
                        name = fn.get("name") or ""
                        args = fn.get("arguments")
                        if isinstance(args, (dict, list)):
                            args_str = json.dumps(args, ensure_ascii=False, default=_json_default)
                        else:
                            args_str = str(args or "")
                        body = f"[tool {name}] {args_str}".strip()
                if not body:
                    body = str(msg.get("text") or msg.get("thought") or msg.get("reasoning") or "")
                lines.append(f"{role}:\n{body}".rstrip())
            return "\n\n".join(lines)
        if first.get("type") == "text" and "content" in first:
            return "\n".join(str(item.get("content") or "") for item in parsed if isinstance(item, dict))
    if isinstance(parsed, dict) and ("role" in parsed or "parts" in parsed or "tool_calls" in parsed):
        return _format_messages([parsed])
    return parsed


def _first(mapping: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if key in mapping and mapping[key] not in (None, ""):
            return mapping[key]
    return None


def _extract_io(detail: dict[str, Any], attrs: dict[str, Any] = None) -> dict[str, Any]:
    attrs = attrs if isinstance(attrs, dict) else {}
    if not isinstance(detail, dict):
        detail = {}
    nested = detail.get("attributes") if isinstance(detail.get("attributes"), dict) else {}
    sources = (detail, nested, attrs)

    def pick(keys: tuple[str, ...]) -> Any:
        for src in sources:
            found = _first(src, keys)
            if found not in (None, ""):
                return found
        return None

    prompt = pick(_PROMPT_KEYS)
    response = pick(_RESPONSE_KEYS)
    arguments = pick(_ARGUMENT_KEYS)
    result = pick(_RESULT_KEYS)
    model = pick(_MODEL_KEYS)

    sys_inst = pick(("gen_ai.system_instructions", "gen_ai.system.message"))
    formatted_prompt = _format_messages(prompt)
    if sys_inst not in (None, "") and (formatted_prompt or prompt):
        sys_text = _format_messages(sys_inst)
        body = formatted_prompt if formatted_prompt not in (None, "") else prompt
        if sys_text and body and str(sys_text) not in str(body):
            formatted_prompt = f"system:\n{sys_text}\n\n{body}"
    elif formatted_prompt in (None, "") and sys_inst not in (None, ""):
        formatted_prompt = _format_messages(sys_inst)

    return redact_value(
        {
            "prompt": _cap(formatted_prompt if formatted_prompt not in (None, "") else prompt),
            "response": _cap(_format_messages(response) if response not in (None, "") else None),
            "arguments": _cap(_maybe_json(arguments) if arguments not in (None, "") else None),
            "result": _cap(_maybe_json(result) if result not in (None, "") else None),
            "model": None if model in (None, "") else str(model),
        }
    )


def _normalize_detail(event_type: str, detail: dict[str, Any]) -> dict[str, Any]:
    if event_type == "tool_result" and "result" not in detail and detail.get("result_preview") not in (None, ""):
        detail["result"] = detail.get("result_preview")
    if event_type in {"chat", "token"} and "response" not in detail and detail.get("content") not in (None, ""):
        detail["response"] = detail.get("content")
    io = _extract_io(detail)
    for key in ("prompt", "response", "arguments", "result", "model"):
        if key == "response" and event_type == "invoke_agent":
            continue
        if key == "result" and event_type not in ("execute_tool", "tool_result"):
            continue
        if detail.get(key) in (None, "") and io.get(key) not in (None, ""):
            detail[key] = io[key]
    return redact_value(detail)


def _attach_io(event: dict[str, Any]) -> dict[str, Any]:
    detail = event.get("detail")
    if not isinstance(detail, dict):
        detail = {}
    io = _extract_io(detail)
    if event.get("event_type") not in ("execute_tool", "tool_result"):
        io["result"] = None
        if isinstance(detail, dict):
            detail.pop("result", None)
        event["result"] = None
    if event.get("event_type") in {"chat", "token"} and io.get("response") in (None, "") and detail.get("content"):
        io["response"] = _cap(detail.get("content"))
    for key in ("prompt", "response", "arguments", "result", "model"):
        if key == "result" and event.get("event_type") not in ("execute_tool", "tool_result"):
            continue
        if event.get(key) in (None, "") and io.get(key) not in (None, ""):
            event[key] = io[key]
        elif key not in event:
            event[key] = io.get(key)
    return event
