from __future__ import annotations

from typing import Any


def checkpoint_storage(ctx: Any, spec: dict[str, Any] | None = None) -> Any:
    if spec and spec.get("checkpoint_storage") is not None:
        return spec["checkpoint_storage"]
    return getattr(ctx, "checkpoint_storage", None)


def resolve_participant(ref: Any, participants: list[Any]) -> Any | None:
    if ref is None:
        return None
    if not isinstance(ref, str):
        return ref
    for item in participants or []:
        if getattr(item, "name", None) == ref:
            return item
        if getattr(item, "id", None) == ref:
            return item
    return None


def result_text(item: Any) -> str:
    if item is None:
        return ""
    resp = getattr(item, "agent_response", None) or item
    text = getattr(resp, "text", None)
    if text:
        return str(text)
    messages = getattr(resp, "messages", None) or []
    for msg in reversed(list(messages)):
        chunk = getattr(msg, "text", None)
        if chunk:
            return str(chunk)
    return ""
