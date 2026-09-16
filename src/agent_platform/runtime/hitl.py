"""The resume contract of LangChain's ``HumanInTheLoopMiddleware``.

The runtime pauses for approval through exactly one mechanism: the library
middleware. It interrupts with
``{"action_requests": [...], "review_configs": [...]}`` and resumes with
``{"decisions": [...]}`` — one decision per action request. Every inbound path
(the HTTP ``/runs/<id>/approvals`` body, the AG-UI resume payload, an internal
caller) funnels through :func:`normalize_decisions`, so the SSE, sync and AG-UI
transports cannot disagree about the shape.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

#: Decision types the library's ``HumanInTheLoopMiddleware`` accepts.
DECISION_TYPES = ("approve", "edit", "reject", "respond")


class HitlPayloadError(ValueError):
    """An inbound resume payload is not the library's ``{"decisions": [...]}``."""


def pending_action_requests(pending: Any) -> list[dict[str, Any]]:
    """The action requests of a pending approval payload (``[]`` when none).

    A run's ``pending_json`` is the interrupt value emitted by the middleware,
    so its ``action_requests`` length is the number of decisions a resume must
    carry.
    """
    if not isinstance(pending, Mapping):
        return []
    actions = pending.get("action_requests")
    if not isinstance(actions, list):
        return []
    return [dict(action) for action in actions if isinstance(action, Mapping)]


def normalize_decisions(payload: Any) -> dict[str, Any]:
    """Return the value ``HumanInTheLoopMiddleware`` resumes with.

    Raises :class:`HitlPayloadError` for anything else, including the compiler's
    previous hand-written approve/deny body.
    """
    if not isinstance(payload, Mapping) or "decisions" not in payload:
        raise HitlPayloadError(
            'resume payload must be {"decisions": [...]} with one decision per '
            "pending action request (the previous approve/deny body is no longer "
            "accepted)"
        )
    raw = payload["decisions"]
    if not isinstance(raw, list) or not raw:
        raise HitlPayloadError('"decisions" must be a non-empty list')
    return {"decisions": [_normalize_decision(item, index) for index, item in enumerate(raw)]}


def _normalize_decision(item: Any, index: int) -> dict[str, Any]:
    if not isinstance(item, Mapping):
        raise HitlPayloadError(f"decision {index} must be an object with a 'type'")
    kind = item.get("type")
    if kind not in DECISION_TYPES:
        raise HitlPayloadError(
            f"decision {index} has type {kind!r}; allowed types: {', '.join(DECISION_TYPES)}"
        )
    if kind == "approve":
        return {"type": "approve"}
    if kind == "reject":
        message = item.get("message")
        if message is None:
            return {"type": "reject"}
        if not isinstance(message, str):
            raise HitlPayloadError(f"decision {index}: reject 'message' must be a string")
        return {"type": "reject", "message": message}
    if kind == "edit":
        edited = item.get("edited_action")
        if not isinstance(edited, Mapping) or not str(edited.get("name") or "").strip():
            raise HitlPayloadError(
                f"decision {index}: edit requires 'edited_action' with the tool 'name'"
            )
        args = edited.get("args")
        if not isinstance(args, Mapping):
            raise HitlPayloadError(f"decision {index}: 'edited_action.args' must be an object")
        return {"type": "edit", "edited_action": {"name": str(edited["name"]), "args": dict(args)}}
    message = item.get("message")
    if not isinstance(message, str) or not message:
        raise HitlPayloadError(f"decision {index}: respond requires a non-empty 'message'")
    return {"type": "respond", "message": message}
