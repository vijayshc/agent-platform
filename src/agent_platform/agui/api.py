"""AG-UI protocol endpoints (additive transport, Flask).

Standard AG-UI servers expose a single POST endpoint that streams protocol
events as SSE. This blueprint adds that plus the auxiliary endpoints the spec
requires, all additive: the existing custom-SSE transport
(``/api/v1/agents/<id>/invoke/stream``) and the React chat are untouched.

Endpoints
    POST /api/v1/agui/input  run an AG-UI turn; streams AG-UI events (SSE)
    GET  /api/v1/agui/events AG-UI event log for a thread/run (replayable)
    GET  /api/v1/agui/state  per-thread AG-UI state snapshot

Event payload format
    ``data: <json>\n\n`` where <json> is the ``ag_ui.core`` event model dumped
    with camelCase aliases (EventEncoder's ``by_alias`` output shape).

Replay source
    The full AG-UI lifecycle (including token deltas) is captured in a bounded
    in-process log while the run streams, because the platform's span store
    intentionally omits token events. ``/events`` replays that log; when no log
    is available (e.g. after a restart) it falls back to the persisted span
    events, which carry tool/approval data.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from queue import Empty, Queue
from typing import Any

from ag_ui.encoder import EventEncoder
from flask import Blueprint, Response, current_app, g, jsonify, request, stream_with_context

from src.agent_platform.agui.bridge import (
    agui_user_text,
    build_resume_payload,
    create_agui_run,
    is_resume_request,
    resolve_attachments,
    resolve_thread_conversation,
    spawn_worker,
)
from src.agent_platform.agui.mapper import AGUIMapper
from src.agent_platform.agui.thread_store import conversation_for_thread
from src.agent_platform.api.auth import api_auth_required, current_user_id
from src.agent_platform.api.api_helpers import can_access_conversation, user_can_access_definition
from src.agent_platform.catalog.store import DefinitionStore
from src.agent_platform.conversations.store import ConversationStore
from src.agent_platform.execution.run_store import RunStore
from src.agent_platform.execution.span_sink import SpanSink

_STATE_CONFIG_KEY = "AGUI_STATE_HOLDER"
_LOGS_CONFIG_KEY = "AGUI_THREAD_LOGS"
_MAX_THREAD_LOGS = 1000


class _ThreadLog:
    """Shared, thread-safe AG-UI event log for one run.

    ``publish`` is called from the worker thread (decoded events); the log
    keeps the encoded SSE payloads so ``/events`` can replay a run's full
    lifecycle including token deltas.
    """

    def __init__(self) -> None:
        self._log: list[str] = []
        self._lock = threading.Lock()
        self._queue: Queue[str | None] = Queue()
        self.done = False

    def publish(self, event: Any) -> None:
        if self.done:
            return
        encoded = EventEncoder().encode(event)
        with self._lock:
            self._log.append(encoded)
        self._queue.put(encoded)

    def close(self) -> None:
        if self.done:
            return
        self.done = True
        self._queue.put(None)

    def snapshot_since(self, index: int) -> tuple[list[str], int]:
        with self._lock:
            return list(self._log[index:]), len(self._log)

    def wait_next(self, timeout: float) -> str | None | "timeout":
        try:
            return self._queue.get(timeout=timeout)
        except Empty:
            return "timeout"


def create_agui_blueprint() -> Blueprint:
    bp = Blueprint("agent_platform_agui", __name__, url_prefix="/agui")

    @bp.post("/input")
    @api_auth_required("runs:write")
    def agui_input():
        body = request.get_json(silent=True) or {}
        messages = body.get("messages") or []
        run_id = body.get("run_id") or body.get("runId")
        thread_id = body.get("thread_id") or body.get("threadId") or str(uuid.uuid4())
        state = body.get("state")
        forwarded = body.get("forwarded_props") or body.get("forwardedProps") or {}
        agent_id = body.get("agent_id") or body.get("agentId") or body.get("agent")
        agent_slug = agent_id or (forwarded.get("agent_id") if isinstance(forwarded, dict) else None)

        if not agent_slug:
            return jsonify({"error": "agent_id required"}), 400
        row = DefinitionStore.resolve(str(agent_slug))
        if row is None or not row.get("published"):
            return jsonify({"error": "agent not found"}), 404
        if not user_can_access_definition(row, current_user_id()):
            return jsonify({"error": "forbidden", "message": "You do not have access to this agent"}), 403

        if not isinstance(messages, list) or not messages:
            return jsonify({"error": "messages required"}), 400
        user_text = agui_user_text(messages)
        resume_request = is_resume_request(body)
        if not user_text and not resume_request:
            return jsonify({"error": "user message required"}), 400

        # An existing thread_id is bound to one tenant's conversation: a caller
        # must not be able to drive (or inject runs into) another user's chat by
        # replaying its thread id.
        existing_conv = conversation_for_thread(thread_id)
        if existing_conv is not None and not can_access_conversation(
            existing_conv, current_user_id()
        ):
            g.audit_reason = "no access to this conversation"
            return jsonify({"error": "forbidden", "message": "You do not have access to this conversation"}), 403

        conv = resolve_thread_conversation(
            thread_id,
            user_id=current_user_id(),
            agent_slug=row.get("slug") or str(agent_slug),
            first_user_text=user_text,
        )
        conv_pk = conv[0] if conv else None

        resume_payload, resume_error, target_run = build_resume_payload(
            body,
            thread_id=thread_id,
            run_ref=forwarded.get("run_id") if isinstance(forwarded, dict) else None,
        )
        if resume_error:
            return jsonify({"error": resume_error}), 409
        input_text = user_text
        if target_run is not None:
            # Forwarded resume state names a run directly; the caller must be
            # allowed to see it (owner, agent access, or observability) AND to
            # write into the conversation it belongs to -- a granted agent must
            # never let one tenant continue another tenant's chat.
            if not _can_read_agui_run(target_run):
                g.audit_reason = "no access to this run"
                return jsonify({"error": "forbidden", "message": "You do not have access to this run"}), 403
            target_conv_id = target_run.get("conversation_id")
            if target_conv_id is not None:
                target_conv = ConversationStore.get(int(target_conv_id))
                if target_conv is not None and not can_access_conversation(
                    target_conv, current_user_id()
                ):
                    g.audit_reason = "no access to this conversation"
                    return jsonify({"error": "forbidden", "message": "You do not have access to this conversation"}), 403
            if (target_run.get("agent_slug") or "") != (row.get("slug") or ""):
                return jsonify({"error": "run to resume belongs to a different agent"}), 409
            # HITL continuation reuses the paused run row (same checkpoint and
            # session), matching ``/runs/<id>/approvals`` semantics.
            run_row = target_run
            conv_pk = run_row.get("conversation_id") or conv_pk
            RunStore.set_pending(int(run_row["id"]), None)
            input_text = run_row.get("task") or user_text
        else:
            run_row = create_agui_run(
                definition=row,
                input_text=user_text,
                conversation_id=conv_pk,
                user_id=current_user_id(),
                attachments=resolve_attachments(body.get("attachments")),
            )
        agui_run_id = str(run_id) if run_id else str(run_row["id"])
        log = _register_log(thread_id, int(run_row["id"]))
        spawn_worker(
            channel=log,
            definition=row,
            input_text=input_text,
            run_id=int(run_row["id"]),
            conversation_id=conv_pk,
            thread_id=thread_id,
            agui_run_id=agui_run_id,
            resume_payload=resume_payload,
            attachments=run_row.get("staged_attachments"),
        )
        if isinstance(state, dict):
            _persist_state(thread_id, state)

        def generate():
            while True:
                chunk = log.wait_next(timeout=15)
                if chunk == "timeout":
                    current = RunStore.get(int(run_row["id"]))
                    status = (current or {}).get("status")
                    if status not in {"running", "awaiting_approval", "cancelling"}:
                        break
                    payload = {"type": "CUSTOM", "name": "status", "value": {"message": "heartbeat"}}
                    yield f"data: {json.dumps(payload)}\n\n"
                    continue
                if chunk is None:
                    break
                yield chunk

        return Response(stream_with_context(generate()), mimetype="text/event-stream")

    @bp.get("/events")
    @api_auth_required("agents:read")
    def agui_events():
        thread_id = request.args.get("thread_id") or request.args.get("threadId")
        run_ref = request.args.get("run_id") or request.args.get("runId")
        run = _resolve_run(thread_id, run_ref)
        if run is None:
            return jsonify({"error": "run not found"}), 404
        if not _can_read_agui_run(run):
            return jsonify({"error": "forbidden", "message": "You do not have access to this run"}), 403
        log = _thread_logs().get(int(run["id"]))

        if log is not None:
            def replay():
                index = 0
                while True:
                    items, index = log.snapshot_since(index)
                    for encoded in items:
                        yield encoded
                    if log.done:
                        break
                    yield "data: " + json.dumps({"type": "CUSTOM", "name": "status", "value": {"message": "heartbeat"}}) + "\n\n"
                    time.sleep(0.4)

            return Response(stream_with_context(replay()), mimetype="text/event-stream")

        return Response(
            stream_with_context(_replay_from_spans(run)),
            mimetype="text/event-stream",
        )

    @bp.get("/state")
    @api_auth_required("agents:read")
    def agui_state():
        thread_id = request.args.get("thread_id") or request.args.get("threadId")
        if not thread_id:
            return jsonify({"thread_id": None, "state": {}})
        # AG-UI state is per-thread (and therefore per-conversation); only the
        # conversation's own tenant may read it.
        conv = conversation_for_thread(thread_id)
        if conv is None:
            return jsonify({"thread_id": thread_id, "state": {}})
        if not can_access_conversation(conv, current_user_id()):
            g.audit_reason = "no access to this conversation"
            return jsonify({"error": "forbidden", "message": "You do not have access to this conversation"}), 403
        return jsonify({"thread_id": thread_id, "state": _load_state(thread_id)})

    return bp


def _resolve_run(thread_id: str | None, run_ref: str | None) -> dict[str, Any] | None:
    if run_ref:
        run = RunStore.get(run_ref)
        if run is not None:
            return run
    if not thread_id:
        return None
    conv = conversation_for_thread(thread_id)
    if conv is None:
        return None
    rows = RunStore.list_for_conversation(int(conv["id"]))
    return rows[0] if rows else None


def _can_read_agui_run(run: dict[str, Any]) -> bool:
    """AG-UI replay/resume follows the same rule as every other run-log surface."""
    return RunStore.can_view(run, current_user_id())


def _thread_logs() -> dict[Any, _ThreadLog]:
    holder = current_app.config.setdefault(_LOGS_CONFIG_KEY, {})
    return holder


def _register_log(thread_id: str, run_id: int) -> _ThreadLog:
    logs = _thread_logs()
    log = _ThreadLog()
    logs[run_id] = log
    while len(logs) > _MAX_THREAD_LOGS:
        logs.pop(next(iter(logs)), None)
    return log


def _replay_from_spans(run: dict[str, Any]):
    """Fallback replay of persisted span events as AG-UI events (no tokens)."""
    mapper = AGUIMapper()
    mapper.set_user_text(run.get("task") or "")
    last_id = 0
    while True:
        for event in SpanSink.get_events(int(run["id"])):
            event_id = event.get("id") or 0
            if event_id <= last_id:
                continue
            last_id = event_id
            for agui_event in mapper.map_event(_span_to_sse(event)):
                yield f"data: {EventEncoder().encode(agui_event)}\n\n"
        current = RunStore.get(run["id"])
        status = (current or {}).get("status")
        if status not in {"running", "awaiting_approval", "cancelling"}:
            break
        yield "data: " + json.dumps({"type": "CUSTOM", "name": "status", "value": {"message": "heartbeat"}}) + "\n\n"
        time.sleep(0.4)


def _span_to_sse(event: dict[str, Any]) -> dict[str, Any]:
    detail = event.get("detail")
    detail = detail if isinstance(detail, dict) else {}
    return {
        "type": event.get("event_type"),
        "message": detail.get("message"),
        "run_id": event.get("run_id"),
        "tool_name": event.get("tool_name"),
        "call_id": detail.get("call_id"),
        "arguments": detail.get("arguments"),
        "result": detail.get("result"),
        "action_requests": detail.get("action_requests"),
        "review_configs": detail.get("review_configs"),
        "agent": event.get("agent_name"),
        "skill": detail.get("skill"),
        "state": detail.get("state"),
    }


def _persist_state(thread_id: str, state: dict[str, Any]) -> None:
    if not thread_id:
        return
    holder = current_app.config.setdefault(_STATE_CONFIG_KEY, {})
    holder[thread_id] = state


def _load_state(thread_id: str) -> dict[str, Any]:
    holder = current_app.config.setdefault(_STATE_CONFIG_KEY, {})
    return dict(holder.get(thread_id) or {})
