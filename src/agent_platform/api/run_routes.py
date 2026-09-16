"""Agent-run HTTP API: create, list, inspect, resume, cancel and stream runs.

Tenancy / module gates
----------------------
* ``/phoenix/status`` and ``/phoenix/projects`` are the observability *area*
  endpoints and keep the ``observability`` module gate they have always had.
* The run endpoints (``/runs``, ``/runs/<id>``, ``/runs/<id>/stream``,
  ``/runs/<id>/spans``, ``/runs/<id>/events``, ``/runs/<id>/trace``,
  ``/runs/<id>/resume``, ``/runs/<id>/cancel``) have **no module gate today**;
  access is decided
  per run by :func:`_can_access_run` (``RunStore.can_view``): the caller owns
  the run, or the caller can access the run's agent definition.  Runs with no
  definition are owner-only.  Direct access to an invisible run fails closed
  with 403.  Listing is scoped in SQL so ``limit`` counts visible runs.
* ``create_run`` / ``invoke_agent`` keep their existing
  ``user_can_access_definition`` gate.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from flask import Blueprint, g, jsonify, request

from src.agent_platform.api.auth import api_auth_required, current_user_id
from src.auth.decorators import module_required
from src.agent_platform.api.api_helpers import (
    can_access_conversation,
    conversation_denied,
    run_model_client,
    user_can_access_definition,
)
from src.agent_platform.api.run_stream import (
    HOST,
    build_run_sse_response,
    get_cancel_event,
    pop_cancel_event,
    run_on_loop,
    set_cancel_event,
)
from src.agent_platform.catalog.store import DefinitionStore
from src.agent_platform.conversations.store import ConversationStore
from src.agent_platform.execution.run_store import RunStore
from src.agent_platform.execution.span_sink import SpanSink
from src.agent_platform.paths import run_workspace_dir
from src.agent_platform.runtime.hitl import (
    HitlPayloadError,
    normalize_decisions,
    pending_action_requests,
)
from src.agent_platform.runtime.model_select import parse_model_payload

logger = logging.getLogger("text2sql.agent_platform")
run_bp = Blueprint("run_api", __name__)

#: Statuses a cancel request may still act on. Anything else is terminal and is
#: left untouched (see ``cancel_run``).
_CANCELLABLE_STATUSES = {"running", "pending", "awaiting_approval", "cancelling"}


def _list_workspace_files(workspace_dir: str | None) -> list[str]:
    if not workspace_dir:
        return []
    root = Path(workspace_dir)
    if not root.is_dir():
        return []
    # ``.agent-workspace-baseline.json`` is the platform's own snapshot of the
    # seeded scaffold; the agent did not produce it, so it is not a deliverable.
    internal = {".agent-workspace-baseline.json"}
    out: list[str] = []
    for path in root.rglob("*"):
        if path.is_file() and path.name not in internal:
            out.append(str(path.relative_to(root)))
    return sorted(out)


@run_bp.get("/phoenix/status")
@api_auth_required("runs:read")
@module_required("observability")
def phoenix_status():
    from src.services.phoenix_service import get_phoenix_url, is_phoenix_healthy, start_phoenix_server

    url = get_phoenix_url()
    healthy = is_phoenix_healthy()
    if not healthy:
        start_phoenix_server()
        healthy = is_phoenix_healthy()
    return jsonify({"url": "/api/v1/phoenix/proxy", "healthy": healthy, "project": "default"})


@run_bp.get("/phoenix/projects")
@api_auth_required("runs:read")
@module_required("observability")
def phoenix_projects():
    from src.services.phoenix_service import list_phoenix_projects

    projects = list_phoenix_projects()
    return jsonify({"projects": projects})


@run_bp.get("/runs")
@api_auth_required("runs:read")
def list_runs():
    limit = int(request.args.get("limit") or 50)
    user_id = current_user_id()
    definition_ids = RunStore.visible_definition_ids(user_id)
    runs = RunStore.list_runs(
        limit=limit,
        viewer_id=user_id,
        definition_ids=definition_ids,
    )
    from src.agent_platform.execution.run_queries import count_visible_runs

    # A real total so the UI can say "25 of 312" instead of implying the visible
    # page *is* everything (silent truncation hides the run you are hunting).
    total = count_visible_runs(user_id, definition_ids)
    return jsonify({"runs": runs, "total": total})


@run_bp.get("/runs/<run_id>")
@api_auth_required("runs:read")
def get_run(run_id: str):
    run = RunStore.get(run_id)
    if run is None:
        return jsonify({"error": "run not found"}), 404
    denied = _can_access_run_or_403(run)
    if denied:
        return denied
    rid = int(run["id"])
    run_dict = dict(run)
    events = SpanSink.get_events(rid)
    run_dict["events"] = events
    run_dict["spans"] = events
    run_dict["workspace_files"] = _list_workspace_files(run.get("workspace_dir"))
    return jsonify(run_dict)


@run_bp.post("/runs")
@api_auth_required("runs:write")
def create_run():
    data = request.get_json(silent=True) or {}
    task = (data.get("task") or data.get("prompt") or data.get("input") or "").strip()
    agent_id = data.get("agent_id") or data.get("agent") or data.get("slug")
    inline_def = data.get("definition")
    conversation_id = data.get("conversation_id")
    attachments = list(data.get("attachments") or [])

    definition = None
    if inline_def and isinstance(inline_def, dict):
        definition = inline_def
    elif agent_id:
        definition = DefinitionStore.resolve(str(agent_id))

    if definition is None:
        return jsonify({"error": "agent or definition is required"}), 400

    user_id = current_user_id()
    if not user_can_access_definition(definition, user_id):
        g.audit_reason = "no access to this agent"
        return jsonify({"error": "forbidden", "message": "You do not have access to this agent"}), 403

    # Resolve the caller's model before anything is persisted: an unusable pick
    # must fail the request without leaving a conversation, message, or run behind.
    client, model_error = run_model_client(data)
    if model_error is not None:
        return model_error

    if conversation_id is not None:
        try:
            conv = ConversationStore.get(conversation_id)
            if conv is not None and not can_access_conversation(conv, user_id):
                return conversation_denied()
            if conv is None:
                conv = ConversationStore.create(user_id=user_id, agent_slug=definition.get("slug") or str(agent_id))
            conversation_id = int(conv["id"])
        except Exception:
            conv = ConversationStore.create(user_id=user_id, agent_slug=definition.get("slug") or str(agent_id))
            conversation_id = int(conv["id"])
        ConversationStore.add_message(int(conversation_id), "user", task, meta={"attachments": attachments} if attachments else None)

    run = RunStore.create(
        task=task,
        definition_id=definition.get("id"),
        user_id=user_id,
        conversation_id=int(conversation_id) if conversation_id else None,
        agent_slug=definition.get("slug"),
        entity_type=definition.get("kind") or "agent",
        entity_id=definition.get("id") or 0,
        input_payload={"task": task, "attachments": attachments, "model": parse_model_payload(data)},
    )
    run_id = int(run["id"])
    workspace = str(run_workspace_dir(run_id, conversation_id))
    RunStore.update_workspace(run_id, workspace)

    stream = bool(data.get("stream", False)) or ("text/event-stream" in request.headers.get("Accept", ""))
    if stream:
        return build_run_sse_response(run, definition, task, conversation_id, client=client)

    return jsonify({
        "id": run_id,
        "public_id": run["public_id"],
        "status": "pending",
        "conversation_id": conversation_id,
        "workspace_dir": workspace,
        "stream_url": f"/api/v1/runs/{run['public_id']}/stream",
    }), 201


def _can_access_run(run: dict | None, user_id: int | None = None) -> bool:
    """Whether the caller may inspect/resume this run.

    The rule is the run store's canonical one: the caller owns the run, or the
    caller can access the run's agent definition (admin, definition owner,
    unowned definition, or a role granted on the definition).  Runs with no
    definition are owner-only.  ``RunStore.can_view`` is shared with the AG-UI
    replay/resume endpoints so every run-log surface agrees.
    """
    uid = user_id if user_id is not None else current_user_id()
    return RunStore.can_view(run, uid)


def _can_access_run_or_403(run: dict | None):
    if _can_access_run(run):
        return None
    g.audit_reason = "no access to this run"
    return jsonify({"error": "forbidden", "message": "You do not have access to this run"}), 403


@run_bp.get("/runs/<run_id>/stream")
@api_auth_required("runs:read")
def stream_run_sse(run_id: str):
    run = RunStore.get(run_id)
    if run is None:
        return jsonify({"error": "run not found"}), 404
    denied = _can_access_run_or_403(run)
    if denied:
        return denied
    cid = run.get("conversation_id")
    payload = run.get("input_json") or {}
    task = payload.get("task") or run.get("task") or ""
    definition_id = run.get("definition_id")
    definition = DefinitionStore.get(int(definition_id)) if definition_id else None
    if not definition:
        definition = {"kind": run.get("entity_type") or "agent", "name": run.get("agent_slug") or "agent"}
    # A model that became unusable since the run was stored answers 400 JSON,
    # like this endpoint's other request errors (404 run, 403 access), rather
    # than opening an event stream only to fail inside it.
    client, model_error = run_model_client(payload)
    if model_error is not None:
        return model_error
    return build_run_sse_response(run, definition, task, int(cid) if cid else None, client=client)


def _invoke_agent_route(agent_id: str, stream: bool = False):
    data = request.get_json(silent=True) or {}
    stream_param = data.get("stream")
    if stream_param is not None:
        stream = bool(stream_param)
    elif "text/event-stream" in request.headers.get("Accept", ""):
        stream = True

    definition = DefinitionStore.resolve(str(agent_id), published_only=True)
    if definition is None:
        return jsonify({"error": "agent not found"}), 404
    if not user_can_access_definition(definition, current_user_id()):
        g.audit_reason = "no access to this agent"
        return jsonify({"error": "forbidden", "message": "You do not have access to this agent"}), 403

    input_text = (data.get("input") or data.get("prompt") or data.get("task") or "").strip()
    if not input_text:
        return jsonify({"error": "input is required"}), 400

    conversation_id = data.get("conversation_id")
    attachments = list(data.get("attachments") or [])
    user_id = current_user_id()

    # Resolve the caller's model before anything is persisted: an unusable pick
    # must fail the request without leaving a conversation, message, or run behind.
    client, model_error = run_model_client(data)
    if model_error is not None:
        return model_error

    if conversation_id is not None:
        try:
            conv = ConversationStore.get(int(conversation_id))
            if conv is not None and not can_access_conversation(conv, user_id):
                return conversation_denied()
            if conv is None:
                conv = ConversationStore.create(user_id=user_id, agent_slug=definition.get("slug") or str(agent_id))
                conversation_id = int(conv["id"])
        except Exception:
            conv = ConversationStore.create(user_id=user_id, agent_slug=definition.get("slug") or str(agent_id))
            conversation_id = int(conv["id"])
        ConversationStore.add_message(int(conversation_id), "user", input_text, meta={"attachments": attachments} if attachments else None)

    run = RunStore.create(
        task=input_text,
        definition_id=definition.get("id"),
        user_id=user_id,
        conversation_id=int(conversation_id) if conversation_id else None,
        agent_slug=definition.get("slug"),
        entity_type=definition.get("kind") or "agent",
        entity_id=definition.get("id") or 0,
        input_payload={"task": input_text, "attachments": attachments, "model": parse_model_payload(data)},
    )
    rid = int(run["id"])
    workspace = str(run_workspace_dir(rid, conversation_id))
    RunStore.update_workspace(rid, workspace)

    if stream:
        return build_run_sse_response(run, definition, input_text, conversation_id, client=client)

    cancel_ev = threading.Event()
    set_cancel_event(rid, cancel_ev)
    try:
        result = run_on_loop(
            HOST.run_sync,
            definition=definition,
            input_text=input_text,
            run_id=rid,
            conversation_id=conversation_id,
            attachments=attachments,
            cancel_event=cancel_ev,
            client=client,
        )
    finally:
        pop_cancel_event(rid)

    updated_run = RunStore.get(rid)
    if updated_run:
        updated_run["events"] = SpanSink.get_events(rid)
    return jsonify({"run": updated_run, **result})


@run_bp.post("/agents/<agent_id>/invoke")
@api_auth_required("runs:write")
def invoke_agent(agent_id: str):
    return _invoke_agent_route(agent_id, stream=False)


@run_bp.post("/agents/<agent_id>/invoke/stream")
@api_auth_required("runs:write")
def invoke_agent_stream(agent_id: str):
    return _invoke_agent_route(agent_id, stream=True)


@run_bp.post("/runs/<run_id>/resume")
@run_bp.post("/runs/<run_id>/approvals")
@run_bp.post("/runs/<run_id>/decision")
@api_auth_required("runs:write")
def resume_run(run_id: str):
    """Resume a paused run with HumanInTheLoopMiddleware's own decisions body.

    The body is exactly ``{"decisions": [...]}`` (plus optional ``stream``): one
    decision per action request of the pending interrupt. Anything else —
    including the previous approve/deny shape — is a ``bad_decision``.
    """
    run = RunStore.get(run_id)
    if run is None:
        return jsonify({"error": "run not found"}), 404
    denied = _can_access_run_or_403(run)
    if denied:
        return denied
    data = request.get_json(silent=True) or {}
    unknown = sorted(set(data) - {"decisions", "stream"})
    if unknown:
        return jsonify({
            "error": "bad_decision",
            "message": (
                f"unexpected field(s) {', '.join(unknown)}; the resume body is "
                '{"decisions": [...]} (plus optional "stream")'
            ),
        }), 400
    try:
        resume_payload = normalize_decisions(data)
    except HitlPayloadError as exc:
        return jsonify({"error": "bad_decision", "message": str(exc)}), 400

    pending = run.get("pending_json")
    expected = len(pending_action_requests(pending))
    if not expected:
        return jsonify({
            "error": "bad_decision",
            "message": "this run has no pending approval to resume",
        }), 400
    if len(resume_payload["decisions"]) != expected:
        return jsonify({
            "error": "bad_decision",
            "message": (
                f"the pending approval asks for {expected} decision(s), one per "
                f"action request, but {len(resume_payload['decisions'])} were sent"
            ),
        }), 400

    cid = run.get("conversation_id")
    definition_id = run.get("definition_id")
    definition = DefinitionStore.resolve(definition_id or run.get("agent_slug")) or {"kind": "agent", "name": "agent"}
    stream = bool(data.get("stream", False)) or ("text/event-stream" in request.headers.get("Accept", ""))
    client, model_error = run_model_client(run.get("input_json") or {})
    if model_error is not None:
        return model_error

    if stream:
        return build_run_sse_response(run, definition, "", int(cid) if cid else None, resume_payload, client=client)

    rid = int(run["id"])
    cancel_ev = threading.Event()
    set_cancel_event(rid, cancel_ev)
    try:
        result = run_on_loop(
            HOST.run_sync,
            definition=definition,
            input_text="",
            run_id=rid,
            conversation_id=int(cid) if cid else None,
            resume_payload=resume_payload,
            cancel_event=cancel_ev,
            client=client,
        )
    finally:
        pop_cancel_event(rid)

    updated_run = RunStore.get(rid)
    if updated_run:
        updated_run["events"] = SpanSink.get_events(rid)
    return jsonify({"run": updated_run, **result})


@run_bp.post("/runs/<run_id>/cancel")
@api_auth_required("runs:write")
def cancel_run(run_id: str):
    run = RunStore.get(run_id)
    if run is None:
        return jsonify({"error": "run not found"}), 404
    denied = _can_access_run_or_403(run)
    if denied:
        return denied
    rid = int(run["id"])
    # A finished run cannot be cancelled: rewriting its status would destroy the
    # record of what actually happened (and silently un-finish a successful run).
    if run.get("status") not in _CANCELLABLE_STATUSES:
        return jsonify({
            "error": "run already finished",
            "id": rid,
            "status": run.get("status"),
        }), 409
    ev = get_cancel_event(rid)
    if ev:
        ev.set()
    RunStore.finish(rid, "cancelled")
    return jsonify({"success": True, "id": rid, "status": "cancelled"})


@run_bp.get("/runs/<run_id>/spans")
@api_auth_required("runs:read")
def get_run_spans(run_id: str):
    run = RunStore.get(run_id)
    if run is None:
        return jsonify({"error": "run not found"}), 404
    denied = _can_access_run_or_403(run)
    if denied:
        return denied
    spans = SpanSink.get_events(int(run["id"]))
    return jsonify({"spans": spans})


@run_bp.get("/runs/<run_id>/events")
@api_auth_required("runs:read")
def get_run_events(run_id: str):
    run = RunStore.get(run_id)
    if run is None:
        return jsonify({"error": "run not found"}), 404
    denied = _can_access_run_or_403(run)
    if denied:
        return denied
    events = SpanSink.get_events(int(run["id"]))
    return jsonify({"events": events})


def _phoenix_project_candidates(run: dict) -> list[str]:
    """Phoenix project names that may hold this run's trace.

    The tracer project is the agent definition's display name; the stored
    ``phoenix_project`` is authoritative, the definition covers runs recorded
    before that column existed, and the slug is a last-resort alias.  Only used
    to *find* the run's own trace, never to widen what the caller may read.
    """
    names: list[str] = []
    definition_id = run.get("definition_id")
    if definition_id:
        try:
            definition = DefinitionStore.get(int(definition_id))
        except Exception:
            definition = None
        if definition and definition.get("name"):
            names.append(str(definition["name"]))
    if run.get("agent_slug"):
        names.append(str(run["agent_slug"]))
    return names


@run_bp.get("/runs/<run_id>/trace")
@api_auth_required("runs:read")
def get_run_trace(run_id: str):
    """The run's interactions, read live from Phoenix (read-only).

    The caller passes a *run* id, and access is decided on that run exactly like
    every other run endpoint, so a Phoenix trace can never be fetched for a run
    the caller cannot already see.  Expected Phoenix conditions (unreachable, no
    trace yet) return ``available: false`` rather than an error.
    """
    run = RunStore.get(run_id)
    if run is None:
        return jsonify({"error": "run not found"}), 404
    denied = _can_access_run_or_403(run)
    if denied:
        return denied
    from src.services.phoenix_traces import fetch_run_trace

    return jsonify(fetch_run_trace(run, _phoenix_project_candidates(run)))


@run_bp.get("/runs/<run_id>/trace/spans/<span_id>")
@api_auth_required("runs:read")
def get_run_trace_span(run_id: str, span_id: str):
    """One span of the run's Phoenix trace, with its prompts/tools/attributes.

    Split from the trace list on purpose: payloads are large, a trace can hold
    hundreds of spans, and the waterfall only needs the run's trace *shape*.
    """
    run = RunStore.get(run_id)
    if run is None:
        return jsonify({"error": "run not found"}), 404
    denied = _can_access_run_or_403(run)
    if denied:
        return denied
    from src.services.phoenix_traces import fetch_run_span

    return jsonify(fetch_run_span(run, span_id, _phoenix_project_candidates(run)))
