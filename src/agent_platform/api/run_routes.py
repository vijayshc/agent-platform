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
from flask import Blueprint, g, jsonify, request

from src.agent_platform.api.auth import api_auth_required, current_user_id
from src.auth.decorators import module_required
from src.agent_platform.api.api_helpers import (
    run_model_client,
    user_can_access_definition,
)
from src.agent_platform.api.run_service import (
    execute_sync_run,
    list_workspace_files,
    phoenix_project_candidates,
    prepare_run,
    validate_resume_request,
)
from src.agent_platform.api.run_stream import build_run_sse_response, get_cancel_event
from src.agent_platform.catalog.store import DefinitionStore
from src.agent_platform.execution.run_store import RunStore
from src.agent_platform.execution.span_sink import SpanSink

logger = logging.getLogger("text2sql.agent_platform")
run_bp = Blueprint("run_api", __name__)

#: Statuses a cancel request may still act on. Anything else is terminal and is
#: left untouched (see ``cancel_run``).
_CANCELLABLE_STATUSES = {"running", "pending", "awaiting_approval", "cancelling"}


@run_bp.get("/phoenix/status")
@api_auth_required("runs:read")
@module_required("observability")
def phoenix_status():
    from src.services.phoenix_service import is_phoenix_healthy, start_phoenix_server

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
    run_dict["workspace_files"] = list_workspace_files(run.get("workspace_dir"))
    return jsonify(run_dict)


@run_bp.post("/runs")
@api_auth_required("runs:write")
def create_run():
    data = request.get_json(silent=True) or {}
    user_id = current_user_id()
    run, definition, task, cid, client, attachments, err, code = prepare_run(
        data,
        user_id,
        published_only=False,
        require_input=False,
        access_checker=user_can_access_definition,
    )
    if err:
        if "__flask_response__" in err:
            return err["__flask_response__"]
        if code == 403:
            g.audit_reason = "no access to this agent"
        return jsonify(err), code

    stream = bool(data.get("stream", False)) or ("text/event-stream" in request.headers.get("Accept", ""))
    if stream:
        return build_run_sse_response(run, definition, task, cid, client=client)

    return jsonify({
        "id": int(run["id"]),
        "public_id": run["public_id"],
        "status": "pending",
        "conversation_id": cid,
        "workspace_dir": run["workspace_dir"],
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

    user_id = current_user_id()
    run, definition, input_text, cid, client, attachments, err, code = prepare_run(
        data,
        user_id,
        agent_id=agent_id,
        published_only=False,
        require_input=True,
        access_checker=user_can_access_definition,
    )
    if err:
        if "__flask_response__" in err:
            return err["__flask_response__"]
        if code == 403:
            g.audit_reason = "no access to this agent"
        # The invoke route addresses one agent: a missing slug is 404, never
        # the generic "definition required" 400 the multi-source /runs keeps.
        if code == 400 and err.get("error") == "agent or definition is required":
            return jsonify({"error": "agent not found"}), 404
        return jsonify(err), code

    if stream:
        return build_run_sse_response(run, definition, input_text, cid, client=client)

    rid = int(run["id"])
    updated_run, result = execute_sync_run(
        rid, definition, input_text, cid, client, attachments=attachments
    )
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
    resume_payload, err, code = validate_resume_request(run, data)
    if err:
        return jsonify(err), code

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
    updated_run, result = execute_sync_run(
        rid, definition, "", int(cid) if cid else None, client, resume_payload=resume_payload
    )
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

    return jsonify(fetch_run_trace(run, phoenix_project_candidates(run)))


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

    return jsonify(fetch_run_span(run, span_id, phoenix_project_candidates(run)))
