"""Turn raw Phoenix span records into the shape the Observability UI renders.

Phoenix stores OpenInference attributes as a nested JSON document (``llm`` ->
``model_name``, ``tool`` -> ``name``, ...), while the semantic convention keys
are dotted (``llm.model_name``).  This module flattens both into one map, pulls
out the fields a developer actually reads (kind, model, tokens, tool name, agent,
status, errors), and normalizes the wildly provider-specific ``input``/``output``
payloads into a uniform list of chat turns plus tool-call arguments so the UI can
render prompts, completions and tool IO without guessing.

Pure functions only: no I/O, so the HTTP layer in ``phoenix_traces`` stays thin.
"""

from __future__ import annotations

import json
from typing import Any

# A single value can carry a whole conversation or a large SQL result. Cap it so
# one pathological span cannot blow up the JSON response or the browser tab.
VALUE_CAP = 200_000
ATTR_CAP = 50_000

_ROLE_BY_MESSAGE_TYPE = {
    "system": "system",
    "systemmessage": "system",
    "human": "user",
    "humanmessage": "user",
    "user": "user",
    "ai": "assistant",
    "aimessage": "assistant",
    "assistant": "assistant",
    "model": "assistant",
    "tool": "tool",
    "toolmessage": "tool",
    "function": "tool",
    "functionmessage": "tool",
    "functionresult": "tool",
    "tool_call_response": "tool",
    "developer": "developer",
}


def parse_json(value: Any) -> Any:
    """Best-effort JSON decode of a string, returning the input unchanged on failure."""
    if not isinstance(value, str):
        return value
    stripped = value.strip()
    if stripped[:1] not in "{[":
        return value
    try:
        return json.loads(stripped)
    except Exception:
        return value


def flatten(value: Any, prefix: str = "") -> dict[str, Any]:
    """Flatten nested dicts to dotted keys, keeping lists as leaves."""
    out: dict[str, Any] = {}
    if isinstance(value, dict):
        for key, item in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            out.update(flatten(item, path))
    else:
        out[prefix] = value
    return out


def _cap(value: Any, limit: int = VALUE_CAP) -> Any:
    if isinstance(value, str):
        return value if len(value) <= limit else value[:limit]
    return value


def _as_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _dedupe_turns(turns: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop consecutive identical turns.

    LangGraph reports the same assistant turn on several nested spans; repeating
    it makes a prompt look far longer than the model actually saw.
    """
    out: list[dict[str, Any]] = []
    for turn in turns:
        if out and out[-1] == turn:
            continue
        out.append(turn)
    return out


def _turn_from_kwargs(role: str, kwargs: dict[str, Any]) -> dict[str, Any] | None:
    content = kwargs.get("content")
    text = _content_text(content)
    tool_calls = _tool_calls(kwargs.get("tool_calls"))
    reasoning = ""
    extra = kwargs.get("additional_kwargs")
    if isinstance(extra, dict):
        reasoning = str(extra.get("reasoning") or extra.get("reasoning_content") or "")
    if not text and not tool_calls and not reasoning:
        return None
    return {"role": role, "text": text, "tool_calls": tool_calls, "reasoning": reasoning}


def _role_of(type_name: Any) -> str | None:
    if not isinstance(type_name, str):
        return None
    key = type_name.replace("_", "").replace("-", "").lower()
    return _ROLE_BY_MESSAGE_TYPE.get(key) or _ROLE_BY_MESSAGE_TYPE.get(type_name.lower())


def _content_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        chunks: list[str] = []
        for part in content:
            if isinstance(part, str):
                chunks.append(part)
            elif isinstance(part, dict):
                ptype = str(part.get("type") or "").lower()
                if ptype in {"tool_use", "function_call", "tool_call"}:
                    chunks.append(_render_tool_call(part))
                elif "text" in part or "content" in part:
                    chunks.append(str(part.get("text") or part.get("content") or ""))
                else:
                    chunks.append(json.dumps(part, ensure_ascii=False, default=str))
        return "\n".join(chunk for chunk in chunks if chunk)
    if isinstance(content, dict):
        if "text" in content:
            return str(content.get("text") or "")
        if "content" in content:
            return _content_text(content.get("content"))
        return json.dumps(content, ensure_ascii=False, default=str)
    return str(content)


def _render_tool_call(call: Any) -> str:
    if not isinstance(call, dict):
        return str(call)
    fn = call.get("function") if isinstance(call.get("function"), dict) else {}
    name = call.get("name") or fn.get("name") or call.get("tool") or ""
    args = call.get("arguments", fn.get("arguments", call.get("args", call.get("input"))))
    if isinstance(args, (dict, list)):
        args = json.dumps(args, ensure_ascii=False, default=str)
    return f"[tool {name}] {args or ''}".strip()


def _tool_calls(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    calls: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        fn = item.get("function") if isinstance(item.get("function"), dict) else {}
        name = item.get("name") or fn.get("name") or item.get("tool") or ""
        args = item.get("args", item.get("arguments", fn.get("arguments", item.get("input"))))
        calls.append({"name": str(name), "args": args})
    return calls


def _turn_from_serialized(item: dict[str, Any]) -> dict[str, Any] | None:
    """Decode a LangChain serialized message (``{"lc": 1, "id": [...], "kwargs": {}}``)."""
    ident = item.get("id")
    type_name = ident[-1] if isinstance(ident, list) and ident else item.get("type")
    role = _role_of(type_name) or _role_of(item.get("type"))
    kwargs = item.get("kwargs") if isinstance(item.get("kwargs"), dict) else {}
    if role is None and not kwargs:
        return None
    return _turn_from_kwargs(role or "message", kwargs)


def _turn_from_dict(item: dict[str, Any]) -> dict[str, Any] | None:
    # LangGraph/AIMessage dump: {"type": "ai", "data": {"content": ...}}
    if isinstance(item.get("data"), dict) and _role_of(item.get("type")):
        return _turn_from_kwargs(_role_of(item.get("type")) or "message", item["data"])
    # LangChain serialized constructor.
    if item.get("lc") == 1 or ("id" in item and "kwargs" in item):
        return _turn_from_serialized(item)
    # OpenAI/Anthropic style.
    role = _role_of(item.get("role")) or _role_of(item.get("type"))
    if role:
        return _turn_from_kwargs(role, item)
    return None


def _walk_generations(value: Any, turns: list[dict[str, Any]]) -> None:
    """Decode LangChain ``{"generations": [[{"text": ..., "message": {...}}]]}``."""
    if isinstance(value, list):
        for item in value:
            _walk_generations(item, turns)
        return
    if not isinstance(value, dict):
        return
    message = value.get("message")
    if isinstance(message, dict):
        if "messages" in message:
            _walk_messages(message, turns)
        else:
            turn = _turn_from_dict(message)
            if turn:
                turns.append(turn)
                return
    text = value.get("text")
    if isinstance(text, str) and text.strip():
        turns.append({"role": "assistant", "text": text, "tool_calls": [], "reasoning": ""})


def _walk_messages(value: Any, turns: list[dict[str, Any]]) -> None:
    if isinstance(value, list):
        for item in value:
            _walk_messages(item, turns)
        return
    if not isinstance(value, dict):
        return
    if "messages" in value and isinstance(value["messages"], (list, dict)):
        _walk_messages(value["messages"], turns)
        return
    if "generations" in value and isinstance(value["generations"], (list, dict)):
        _walk_generations(value["generations"], turns)
        return
    if "update" in value and isinstance(value["update"], dict):
        _walk_messages(value["update"], turns)
        return
    turn = _turn_from_dict(value)
    if turn:
        turns.append(turn)


def extract_turns(value: Any) -> list[dict[str, Any]]:
    """Normalize a span's input/output payload into chat turns (empty when opaque)."""
    parsed = parse_json(value)
    if isinstance(parsed, str):
        text = parsed.strip()
        return [{"role": "text", "text": text, "tool_calls": [], "reasoning": ""}] if text else []
    turns: list[dict[str, Any]] = []
    _walk_messages(parsed, turns)
    return _dedupe_turns(turns)


def _io_value(node: dict[str, Any], attrs: dict[str, Any], side: str) -> dict[str, Any] | None:
    raw = node.get(side)
    if isinstance(raw, dict) and raw.get("value") not in (None, ""):
        return {
            "mime_type": raw.get("mime_type") or attrs.get(f"{side}.mime_type") or "text/plain",
            "value": _cap(str(raw.get("value"))),
            "turns": extract_turns(raw.get("value")),
        }
    legacy = attrs.get(f"{side}.value")
    if legacy not in (None, ""):
        return {
            "mime_type": attrs.get(f"{side}.mime_type") or "text/plain",
            "value": _cap(str(legacy)),
            "turns": extract_turns(legacy),
        }
    return None


def _normalize_events(events: Any) -> list[dict[str, Any]]:
    if not isinstance(events, list):
        return []
    out: list[dict[str, Any]] = []
    for event in events:
        if not isinstance(event, dict):
            continue
        out.append(
            {
                "name": str(event.get("name") or ""),
                "message": _cap(str(event.get("message") or "")),
                "timestamp": event.get("timestamp"),
                "attributes": flatten(event.get("attributes") or {}),
            }
        )
    return out


def _capped_attributes(attrs: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in attrs.items():
        if isinstance(value, str) and len(value) > ATTR_CAP:
            out[key] = value[:ATTR_CAP]
        else:
            out[key] = value
    return out


def normalize_span(node: dict[str, Any], *, detail: bool = True) -> dict[str, Any]:
    """One Phoenix span node -> the UI's span record.

    ``detail=False`` produces the lightweight row the waterfall needs (identity,
    timing, kind, tokens, error flag) and omits the payloads/attributes, which
    can run to hundreds of kilobytes per span.  A large trace has hundreds of
    spans, so shipping every payload in the list is what turns a trace into tens
    of megabytes; the inspector fetches one span's detail on demand instead.
    """
    attrs = flatten(parse_json(node.get("attributes") or {}))
    kind = str(node.get("spanKind") or attrs.get("openinference.span.kind") or "CHAIN").upper()
    status = str(node.get("statusCode") or "OK").upper()
    prompt_tokens = _as_int(node.get("tokenCountPrompt")) or _as_int(attrs.get("llm.token_count.prompt"))
    completion_tokens = _as_int(node.get("tokenCountCompletion")) or _as_int(attrs.get("llm.token_count.completion"))
    total_tokens = _as_int(node.get("tokenCountTotal"))
    if total_tokens is None:
        total_tokens = (prompt_tokens or 0) + (completion_tokens or 0) or None
    error = status == "ERROR"
    if not error and status == "UNSET":
        error = bool(node.get("statusMessage")) or any(
            key.startswith("exception.") for key in attrs
        )
    span: dict[str, Any] = {
        "id": node.get("spanId"),
        "parent_id": node.get("parentId") or None,
        "name": node.get("name") or "",
        "kind": kind,
        "start_time": node.get("startTime"),
        "end_time": node.get("endTime"),
        "duration_ms": node.get("latencyMs"),
        "status": status,
        "error": error,
        "status_message": "" if not detail else (node.get("statusMessage") or ""),
        "model": attrs.get("llm.model_name") or attrs.get("gen_ai.response.model") or attrs.get("metadata.ls_model_name"),
        "tool_name": attrs.get("tool.name"),
        "tool_description": None if not detail else attrs.get("tool.description"),
        "agent_name": attrs.get("metadata.agent_name") or attrs.get("graph.node.name"),
        "node": attrs.get("metadata.langgraph_node"),
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
        "has_io": bool(node.get("input") or node.get("output") or attrs.get("input.value") or attrs.get("output.value")),
    }
    if detail:
        span["input"] = _io_value(node, attrs, "input")
        span["output"] = _io_value(node, attrs, "output")
        span["attributes"] = _capped_attributes(attrs)
        span["events"] = _normalize_events(node.get("events"))
    return span


def summarize(spans: list[dict[str, Any]], trace: dict[str, Any], stored: dict[str, Any] | None = None) -> dict[str, Any]:
    """Aggregate a trace's spans into the numbers shown above the waterfall."""
    counts: dict[str, int] = {}
    for span in spans:
        key = str(span.get("kind") or "CHAIN").lower()
        counts[key] = counts.get(key, 0) + 1
    models = sorted({str(span["model"]) for span in spans if span.get("model")})
    prompt_tokens = sum(int(span.get("prompt_tokens") or 0) for span in spans)
    completion_tokens = sum(int(span.get("completion_tokens") or 0) for span in spans)
    error_count = sum(1 for span in spans if span.get("error"))
    durations = [float(span["duration_ms"]) for span in spans if span.get("duration_ms") is not None]
    cost = None
    cost_summary = trace.get("costSummary") if isinstance(trace.get("costSummary"), dict) else {}
    total = cost_summary.get("total") if isinstance(cost_summary.get("total"), dict) else {}
    if total.get("cost") not in (None, ""):
        cost = float(total["cost"])
    start = min((str(span["start_time"]) for span in spans if span.get("start_time")), default=None)
    end = max((str(span["end_time"]) for span in spans if span.get("end_time")), default=None)
    return {
        "span_count": len(spans),
        "error_count": error_count,
        "duration_ms": trace.get("latencyMs"),
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
        "cost": cost,
        "models": models,
        "kinds": counts,
        "llm_calls": counts.get("llm", 0),
        "tool_calls": counts.get("tool", 0),
        "slowest_span_ms": max(durations) if durations else None,
        "start_time": start,
        "end_time": end,
        "session_id": (stored or {}).get("session_id")
        or ((trace.get("session") or {}).get("sessionId") if isinstance(trace.get("session"), dict) else None),
        "root_span_id": (trace.get("rootSpan") or {}).get("spanId") if isinstance(trace.get("rootSpan"), dict) else None,
        "root_span_name": (trace.get("rootSpan") or {}).get("name") if isinstance(trace.get("rootSpan"), dict) else None,
    }
