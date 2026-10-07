"""GenAI OTel semantic normalization and message formatting for span events.

Extracted from span_sink.py to decouple attribute formatting and GenAI OpenInference
semantic conventions from the in-memory event buffer.
"""

from __future__ import annotations

import json
from typing import Any

from src.models.secrets import MASK, is_secret_key, redact_value

ATTR_CAP = 500
BODY_CAP = 100_000

# MAF / OTel GenAI semantic conventions (agent_framework.observability.OtelAttr).
PROMPT_ATTRS = (
    "prompt",
    "gen_ai.prompt",
    "gen_ai.input.messages",
    "input",
    "gen_ai.user.message",
)
RESPONSE_ATTRS = (
    "response",
    "gen_ai.completion",
    "gen_ai.output.messages",
    "gen_ai.output",
    "output",
    "gen_ai.assistant.message",
    "gen_ai.choice",
)
ARGUMENT_ATTRS = (
    "arguments",
    "gen_ai.tool.call.arguments",
)
RESULT_ATTRS = (
    "result",
    "gen_ai.tool.call.result",
    "result_preview",
)
MODEL_ATTRS = (
    "model",
    "gen_ai.response.model",
    "gen_ai.request.model",
)
SYSTEM_INSTRUCTION_ATTRS = (
    "gen_ai.system_instructions",
    "gen_ai.system.message",
)

BODY_EXACT = frozenset(
    PROMPT_ATTRS
    + RESPONSE_ATTRS
    + ARGUMENT_ATTRS
    + RESULT_ATTRS
    + SYSTEM_INSTRUCTION_ATTRS
    + (
        "gen_ai.tool.message",
        "completion",
        "content",
        "extra_content",
        "thought_signature",
        "_extra_content",
    )
)

BODY_PREFIXES = (
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


def _is_secret(value: Any) -> bool:
    return type(value).__name__ == "SecretString"


def _json_default(obj: Any) -> str:
    if _is_secret(obj):
        return MASK
    return str(obj)


def is_body_key(key: str) -> bool:
    k = str(key)
    if k in BODY_EXACT:
        return True
    return any(k == prefix or k.startswith(prefix + ".") for prefix in BODY_PREFIXES)


def serialize_attr(key: str, value: Any) -> str:
    if is_secret_key(key) or _is_secret(value):
        return MASK
    text = to_text(value)
    cap_limit = BODY_CAP if is_body_key(key) else ATTR_CAP
    if len(text) > cap_limit:
        return text[:cap_limit]
    return text


def to_text(value: Any) -> str:
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


def cap(value: Any, limit: int = BODY_CAP) -> Any:
    value = redact_value(value)
    if isinstance(value, (dict, list)):
        raw = json.dumps(value, ensure_ascii=False, default=_json_default)
        if len(raw) <= limit:
            return value
        return raw[:limit]
    if isinstance(value, str):
        return value if len(value) <= limit else value[:limit]
    text = to_text(value)
    return text if len(text) <= limit else text[:limit]


def maybe_json(value: Any) -> Any:
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


def parts_text(parts: Any) -> str:
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


def format_messages(value: Any) -> Any:
    parsed = maybe_json(value)
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
                body = parts_text(msg.get("parts") if "parts" in msg else msg.get("content"))
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
        return format_messages([parsed])
    return parsed


def _first(mapping: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if key in mapping and mapping[key] not in (None, ""):
            return mapping[key]
    return None


def extract_io(detail: dict[str, Any], attrs: dict[str, Any] = None) -> dict[str, Any]:
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

    prompt = pick(PROMPT_ATTRS)
    response = pick(RESPONSE_ATTRS)
    arguments = pick(ARGUMENT_ATTRS)
    result = pick(RESULT_ATTRS)
    model = pick(MODEL_ATTRS)

    sys_inst = pick(SYSTEM_INSTRUCTION_ATTRS)
    formatted_prompt = format_messages(prompt)
    if sys_inst not in (None, "") and (formatted_prompt or prompt):
        sys_text = format_messages(sys_inst)
        body = formatted_prompt if formatted_prompt not in (None, "") else prompt
        if sys_text and body and str(sys_text) not in str(body):
            formatted_prompt = f"system:\n{sys_text}\n\n{body}"
    elif formatted_prompt in (None, "") and sys_inst not in (None, ""):
        formatted_prompt = format_messages(sys_inst)

    return redact_value(
        {
            "prompt": cap(formatted_prompt if formatted_prompt not in (None, "") else prompt),
            "response": cap(format_messages(response) if response not in (None, "") else None),
            "arguments": cap(maybe_json(arguments) if arguments not in (None, "") else None),
            "result": cap(maybe_json(result) if result not in (None, "") else None),
            "model": None if model in (None, "") else str(model),
        }
    )


def normalize_event_detail(event_type: str, detail: dict[str, Any]) -> dict[str, Any]:
    if event_type == "tool_result" and "result" not in detail and detail.get("result_preview") not in (None, ""):
        detail["result"] = detail.get("result_preview")
    if event_type in {"chat", "token"} and "response" not in detail and detail.get("content") not in (None, ""):
        detail["response"] = detail.get("content")
    io = extract_io(detail)
    for key in ("prompt", "response", "arguments", "result", "model"):
        if key == "response" and event_type == "invoke_agent":
            continue
        if key == "result" and event_type not in ("execute_tool", "tool_result"):
            continue
        if detail.get(key) in (None, "") and io.get(key) not in (None, ""):
            detail[key] = io[key]
    return redact_value(detail)


def attach_io_to_event(event: dict[str, Any]) -> dict[str, Any]:
    detail = event.get("detail")
    if not isinstance(detail, dict):
        detail = {}
    io = extract_io(detail)
    if event.get("event_type") not in ("execute_tool", "tool_result"):
        io["result"] = None
        if isinstance(detail, dict):
            detail.pop("result", None)
        event["result"] = None
    if event.get("event_type") in {"chat", "token"} and io.get("response") in (None, "") and detail.get("content"):
        io["response"] = cap(detail.get("content"))
    for key in ("prompt", "response", "arguments", "result", "model"):
        if key == "result" and event.get("event_type") not in ("execute_tool", "tool_result"):
            continue
        if event.get(key) in (None, "") and io.get(key) not in (None, ""):
            event[key] = io[key]
        elif key not in event:
            event[key] = io.get(key)
    return event
