"""Bridge ``RuntimeHost.stream_run`` outcomes into AG-UI protocol events.

The platform's execution surface is unchanged: ``RuntimeHost.stream_run``
(``runtime/host.py``) keeps driving the run, persisting sessions, checkpoints,
approvals, and span events. This module is the AG-UI boundary: it feeds the
AG-UI input/resume payloads into the platform and converts the platform's
custom SSE events (``runtime/events.py``) into AG-UI events via
:class:`src.agent_platform.agui.mapper.AGUIMapper`.

AG-UI thread mapping
    ``thread_id`` maps 1:1 to a platform conversation
    (``agent_conversations.public_id``); a fresh thread creates the
    conversation. Multi-turn continuity therefore comes from the platform's
    session persistence (``agent_sessions``/RunStore), same as the React chat.

AG-UI resume mapping
    A ``resume`` payload (or ``forwarded_props.resume`` / ``forwardedProps``
    ``command.resume``, matching the installed package's extraction rules) is
    the HITL continuation. It is resolved against the target run's
    ``pending_json`` (the ``HumanInTheLoopMiddleware`` interrupt payload) and
    converted into that middleware's own ``{"decisions": [...]}`` resume value.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import AsyncIterator
from typing import Any

from ag_ui.core import RunErrorEvent, RunFinishedEvent, RunStartedEvent

from src.agent_platform.agui.mapper import AGUIMapper
from src.agent_platform.agui.thread_store import bind as bind_thread, conversation_for_thread
from src.agent_platform.api.auth import current_user_id
from src.agent_platform.conversations.store import ConversationStore
from src.agent_platform.execution.run_store import RunStore
from src.agent_platform.paths import run_workspace_dir
from src.agent_platform.runtime.hitl import (
    DECISION_TYPES,
    HitlPayloadError,
    normalize_decisions,
    pending_action_requests,
)
from src.agent_platform.runtime.host import RuntimeHost, stage_attachments
from src.agent_platform.runtime.workspace import seed_workspace, workspace_seed_for

_HOST = RuntimeHost()


def agui_user_text(messages: list[dict[str, Any]] | None) -> str:
    """Extract the latest user text from an AG-UI ``messages`` array."""
    for message in reversed(list(messages or [])):
        if not isinstance(message, dict):
            continue
        role = str(message.get("role") or "")
        if role != "user":
            continue
        content = message.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = [str(p.get("text") or "") for p in content if isinstance(p, dict)]
            return "".join(parts)
    return ""


def extract_resume_payload(input_data: dict[str, Any]) -> Any:
    """Extract the resume payload using the installed package's locations."""
    resume = input_data.get("resume")
    if resume is not None:
        return resume
    forwarded = input_data.get("forwarded_props") or input_data.get("forwardedProps")
    if not isinstance(forwarded, dict):
        return None
    command = forwarded.get("command")
    if isinstance(command, dict) and command.get("resume") is not None:
        return command.get("resume")
    return forwarded.get("resume")


def _normalize_interrupts(payload: Any) -> list[dict[str, Any]]:
    if payload is None:
        return []
    if isinstance(payload, list):
        candidates = payload
    elif isinstance(payload, dict):
        if isinstance(payload.get("interrupts"), list):
            candidates = payload["interrupts"]
        elif isinstance(payload.get("interrupt"), list):
            candidates = payload["interrupt"]
        else:
            candidates = [payload]
    else:
        return []
    out: list[dict[str, Any]] = []
    for item in candidates:
        if not isinstance(item, dict):
            continue
        interrupt_id = item.get("id") or item.get("interruptId") or item.get("toolCallId")
        if not interrupt_id:
            continue
        value = item.get("value")
        if value is None and "response" in item:
            value = item.get("response")
        index = value.get("action_index") if isinstance(value, dict) else None
        out.append({
            "id": str(interrupt_id),
            "value": value,
            "action_index": index if isinstance(index, int) else 0,
        })
    return out


def _decision_from_value(value: Any, interrupt_id: str) -> dict[str, Any]:
    """One AG-UI interrupt answer as a ``HumanInTheLoopMiddleware`` decision.

    A client may answer with the library's decision object verbatim
    (``{"type": "reject", "message": "no"}``), with the ``approve``/``reject``
    word, or with the AG-UI convention (``{"accepted": true}`` /
    ``{"accepted": false, "message": ...}``).
    """
    if isinstance(value, dict) and value.get("type") in DECISION_TYPES:
        return normalize_decisions({"decisions": [value]})["decisions"][0]
    if isinstance(value, bool):
        return {"type": "approve"} if value else {"type": "reject"}
    if isinstance(value, str) and value.strip().lower() in {"approve", "reject"}:
        return {"type": value.strip().lower()}
    if isinstance(value, dict) and "accepted" in value:
        if bool(value.get("accepted")):
            return {"type": "approve"}
        message = value.get("message") or value.get("reason")
        return {"type": "reject", "message": str(message)} if message else {"type": "reject"}
    raise HitlPayloadError(
        f"interrupt {interrupt_id} must answer approve or reject — "
        '{"accepted": true|false} or a decision such as {"type": "approve"}'
    )


def resolve_agui_run(thread_id: str, run_ref: str | None) -> dict[str, Any] | None:
    """Resolve the platform run a resume targets (explicit ref or latest pending).

    ``list_for_conversation`` returns raw rows (``pending_json`` still a JSON
    string); re-fetch with ``RunStore.get`` so the pending payload is parsed.
    """
    if run_ref:
        run = RunStore.get(run_ref)
        if run is not None:
            return run
    if not thread_id:
        return None
    conv = conversation_for_thread(thread_id)
    if conv is None:
        return None
    for row in RunStore.list_for_conversation(int(conv["id"])):
        if row.get("status") != "awaiting_approval":
            continue
        run = RunStore.get(row["id"])
        if run is not None:
            return run
    return None


def is_resume_request(input_data: dict[str, Any]) -> bool:
    """True when the request carries an AG-UI resume payload (HITL continuation)."""
    return extract_resume_payload(input_data) is not None


def build_resume_payload(
    input_data: dict[str, Any],
    *,
    thread_id: str | None,
    run_ref: str | None,
) -> tuple[dict[str, Any] | None, str | None, dict[str, Any] | None]:
    """Convert an AG-UI resume payload into the middleware's decisions value.

    The answers are matched to the target run's pending
    ``HumanInTheLoopMiddleware`` interrupt (``action_index`` marks which action
    request an answer belongs to), so a resume always carries exactly one
    decision per action request. Returns ``(resume_payload, error, target_run)``
    — the resolved awaiting run is returned as well so callers can carry its
    identity onto the new run.
    """
    resume = extract_resume_payload(input_data)
    if resume is None:
        return None, None, None
    target = resolve_agui_run(thread_id or "", run_ref)
    if target is None:
        return None, "no pending approval to resume on this thread", None
    actions = pending_action_requests(target.get("pending_json"))
    if not actions:
        return None, "the target run has no pending approval to resume", None
    interrupts = sorted(_normalize_interrupts(resume), key=lambda item: item["action_index"])
    if not interrupts:
        return None, "resume must carry the interrupt id(s) emitted by this run", None
    try:
        decisions = [_decision_from_value(item["value"], item["id"]) for item in interrupts]
    except HitlPayloadError as exc:
        return None, str(exc), None
    if len(decisions) != len(actions):
        return None, (
            f"the pending approval asks for {len(actions)} decision(s), one per "
            f"action request, but the resume carried {len(decisions)}"
        ), None
    return {"decisions": decisions}, None, target


def resolve_thread_conversation(
    thread_id: str | None,
    *,
    user_id: int | None,
    agent_slug: str,
    first_user_text: str,
) -> tuple[int, str] | None:
    """Map an AG-UI thread_id to a platform conversation (creating it on first use)."""
    if not thread_id:
        return None
    conv = conversation_for_thread(thread_id)
    if conv is None:
        conv = ConversationStore.create(user_id=user_id, agent_slug=agent_slug)
        title = first_user_text.strip().split("\n")[0][:80] or "New chat"
        ConversationStore.set_title(int(conv["id"]), title)
        bind_thread(thread_id, int(conv["id"]))
    return int(conv["id"]), conv["public_id"]


def resolve_attachments(raw: Any) -> list[dict[str, Any]]:
    """Resolve AG-UI-referenced attachments to platform attachment rows.

    Mirrors ``api/blueprint._resolve_attachments``: entries with a
    ``public_id`` and no ``path`` are looked up in the attachment store; only
    attachments owned by the caller (or operator) are returned.
    """
    resolved: list[dict[str, Any]] = []
    for att in raw or []:
        item = dict(att) if isinstance(att, dict) else {"filename": str(att)}
        pid = item.get("public_id") or item.get("id")
        if pid and not item.get("path"):
            row = ConversationStore.get_attachment(str(pid))
            if row is None:
                continue
            try:
                if row.get("user_id") is not None and int(row["user_id"]) != int(current_user_id()):
                    continue
            except (TypeError, ValueError):
                continue
            item = {**row, **item}
        resolved.append(item)
    return resolved


def create_agui_run(
    *,
    definition: dict[str, Any],
    input_text: str,
    conversation_id: int | None,
    user_id: int | None,
    attachments: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Create the platform run row for an AG-UI turn (mirrors ``_invoke``)."""
    run = RunStore.create(
        task=input_text,
        definition_id=definition.get("id"),
        conversation_id=conversation_id,
        user_id=user_id,
        agent_slug=definition.get("slug"),
        entity_type=definition.get("kind") or "agent",
        entity_id=definition.get("id") or 0,
        workspace_dir=None,
        input_payload={"attachments": attachments or []},
    )
    workspace = str(run_workspace_dir(run["id"], conversation_id))
    seed_workspace(workspace, workspace_seed_for(definition.get("config") or definition))
    staged = stage_attachments(attachments or [], workspace)
    RunStore.update_workspace(int(run["id"]), workspace)
    return {**run, "staged_attachments": staged}


async def bridge_stream(
    *,
    definition: dict[str, Any],
    input_text: str,
    run_id: int,
    conversation_id: int | None,
    thread_id: str,
    agui_run_id: str,
    resume_payload: dict[str, Any] | None = None,
    attachments: list[dict[str, Any]] | None = None,
    cancel_event: threading.Event | None = None,
) -> AsyncIterator[Any]:
    """Run via RuntimeHost and yield AG-UI events (RunStarted → … → RunFinished).

    ``thread_id``/``agui_run_id`` are the AG-UI identity carried on events; the
    platform DB identity is ``run_id``/``conversation_id``.
    """
    mapper = AGUIMapper()
    mapper.set_user_text(input_text)
    errored = False
    final_reply: str | None = None

    yield RunStartedEvent(thread_id=thread_id, run_id=agui_run_id)

    try:
        async for sse in _HOST.stream_run(
            definition=definition,
            input_text=input_text,
            run_id=run_id,
            conversation_id=conversation_id,
            attachments=attachments,
            resume_payload=resume_payload,
            cancel_event=cancel_event,
        ):
            if sse.get("type") == "error":
                errored = True
            elif sse.get("type") == "done":
                final_reply = sse.get("reply") or final_reply
            for event in mapper.map_event(sse):
                yield event
    except Exception as exc:  # pragma: no cover - defensive boundary
        errored = True
        yield RunErrorEvent(message=str(exc), code=type(exc).__name__)

    for event in mapper.close():
        yield event
    snapshot = mapper.snapshot()
    if snapshot is not None:
        yield snapshot
    if not errored:
        yield RunFinishedEvent(
            thread_id=thread_id,
            run_id=agui_run_id,
            result={"final_reply": final_reply} if final_reply else None,
            interrupt=mapper.interrupts or None,
        )


def spawn_worker(
    *,
    channel: Any,
    definition: dict[str, Any],
    input_text: str,
    run_id: int,
    conversation_id: int | None,
    thread_id: str,
    agui_run_id: str,
    resume_payload: dict[str, Any] | None = None,
    attachments: list[dict[str, Any]] | None = None,
) -> threading.Event:
    """Run the bridge on a dedicated event loop and publish to the channel."""
    cancel = threading.Event()

    def worker() -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        async def consume() -> None:
            try:
                async for event in bridge_stream(
                    definition=definition,
                    input_text=input_text,
                    run_id=run_id,
                    conversation_id=conversation_id,
                    thread_id=thread_id,
                    agui_run_id=agui_run_id,
                    resume_payload=resume_payload,
                    attachments=attachments,
                    cancel_event=cancel,
                ):
                    channel.publish(event)
            except Exception as exc:  # pragma: no cover - defensive boundary
                channel.publish(RunErrorEvent(message=str(exc), code=type(exc).__name__))
            finally:
                channel.close()

        try:
            loop.run_until_complete(consume())
        finally:
            loop.close()
            from src.utils.database import remove_db_session

            remove_db_session()

    threading.Thread(target=worker, daemon=True, name=f"agui-run-{run_id}").start()
    return cancel
