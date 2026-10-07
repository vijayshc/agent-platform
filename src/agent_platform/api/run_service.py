"""Execution, preparation, and HITL decision service for agent runs.

Decouples run lifecycle orchestration, workspace crawling, and HITL decision
normalization from HTTP routing.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from src.agent_platform.api.api_helpers import (
    can_access_conversation,
    can_studio,
    conversation_denied,
    draft_run_denied,
    run_model_client,
)
from src.agent_platform.api.run_stream import (
    HOST,
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


def list_workspace_files(workspace_dir: str | None) -> list[str]:
    """List relative paths of deliverables in a run's workspace."""
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


def prepare_run(
    data: dict[str, Any],
    user_id: int | None,
    *,
    access_checker: Any,
    agent_id: str | None = None,
    published_only: bool = False,
    require_input: bool = False,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, str, int | None, Any, list[Any], dict[str, Any] | None, int]:
    """Prepare run prerequisites: resolve definition, model, conversation, and create run record.

    Returns:
        (run, definition, task, conversation_id, client, attachments, error_response_dict, error_status_code)
    """
    task = (data.get("task") or data.get("prompt") or data.get("input") or "").strip()
    if require_input and not task:
        return None, None, "", None, None, [], {"error": "input is required"}, 400

    aid = agent_id or data.get("agent_id") or data.get("agent") or data.get("slug")
    inline_def = data.get("definition")
    attachments = list(data.get("attachments") or [])
    conversation_id = data.get("conversation_id")

    definition = None
    if inline_def and isinstance(inline_def, dict):
        definition = inline_def
    elif aid:
        definition = DefinitionStore.resolve(str(aid), published_only=published_only)

    if definition is None:
        err = "agent not found" if published_only else "agent or definition is required"
        return None, None, task, None, None, attachments, {"error": err}, 404 if published_only else 400

    if not access_checker(definition, user_id):
        # The run gate names the denial: an EXISTING agent the caller cannot
        # access is 403 (the guarded run-gate contract). 404 is reserved for
        # genuinely absent agents, resolved above (``definition is None``).
        # Catalog reads keep their 404-hides-existence contract; this is the
        # run path, not a read.
        return None, None, task, None, None, attachments, {
            "error": "forbidden",
            "message": "You do not have access to this agent",
        }, 403

    # Drafts need the Studio author gate on top of agent access (owner /
    # granted role / admin + agent_studio write). Denied strangers already
    # left via the 403 above; this answers 403 to agent-authorized callers without it.
    if not definition.get("published"):
        if not can_studio(min_level="write"):
            from flask import g as _g

            _g.audit_reason = "missing agent_studio module access"
            return None, None, task, None, None, attachments, {
                "error": "forbidden",
                "message": "Agent Studio requires agent_studio module access",
            }, 403
        # Belt-and-braces: the draft gate's canonical rule must agree with the
        # caller's access checker before a draft run is created.
        _draft_denied = draft_run_denied(definition, user_id)
        if _draft_denied is not None:
            _err, _code = _draft_denied
            return None, None, task, None, None, attachments, _err, _code

    client, model_error = run_model_client(data)
    if model_error is not None:
        # model_error is a Flask response tuple or Response
        return None, None, task, None, None, attachments, {"__flask_response__": model_error}, 400

    if conversation_id is not None:
        conv = ConversationStore.get(conversation_id)
        if conv is not None and not can_access_conversation(conv, user_id):
            return None, None, task, None, None, attachments, {"__flask_response__": conversation_denied()}, 403
        if conv is None:
            conv = ConversationStore.create(user_id=user_id, agent_slug=definition.get("slug") or str(aid))
        conversation_id = int(conv["id"])
        if task:
            ConversationStore.add_message(
                int(conversation_id), "user", task, meta={"attachments": attachments} if attachments else None
            )

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
    rid = int(run["id"])
    workspace = str(run_workspace_dir(rid, conversation_id))
    RunStore.update_workspace(rid, workspace)
    run["workspace_dir"] = workspace

    return run, definition, task, conversation_id, client, attachments, None, 200


def execute_sync_run(
    run_id: int,
    definition: dict[str, Any],
    input_text: str,
    conversation_id: int | None,
    client: Any,
    attachments: list[Any] | None = None,
    resume_payload: dict[str, Any] | None = None,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Synchronously execute a run on the event loop and return updated run + result dict."""
    cancel_ev = threading.Event()
    set_cancel_event(run_id, cancel_ev)
    try:
        kwargs: dict[str, Any] = {
            "definition": definition,
            "input_text": input_text,
            "run_id": run_id,
            "conversation_id": conversation_id,
            "cancel_event": cancel_ev,
            "client": client,
        }
        if attachments is not None:
            kwargs["attachments"] = attachments
        if resume_payload is not None:
            kwargs["resume_payload"] = resume_payload

        result = run_on_loop(HOST.run_sync, **kwargs)
    finally:
        pop_cancel_event(run_id)

    updated_run = RunStore.get(run_id)
    if updated_run:
        updated_run["events"] = SpanSink.get_events(run_id)
    # The assistant reply is persisted by RuntimeHost (persist_run_reply), which
    # owns it for every execution path; writing it here too duplicates the row.
    return updated_run, result


def validate_resume_request(
    run: dict[str, Any], data: dict[str, Any]
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, int]:
    """Validate HITL decision payload against pending approval requests.

    Returns:
        (resume_payload, error_dict, status_code)
    """
    unknown = sorted(set(data) - {"decisions", "stream"})
    if unknown:
        return None, {
            "error": "bad_decision",
            "message": (
                f"unexpected field(s) {', '.join(unknown)}; the resume body is "
                '{"decisions": [...]} (plus optional "stream")'
            ),
        }, 400
    try:
        resume_payload = normalize_decisions(data)
    except HitlPayloadError as exc:
        return None, {"error": "bad_decision", "message": str(exc)}, 400

    pending = run.get("pending_json")
    expected = len(pending_action_requests(pending))
    if not expected:
        return None, {
            "error": "bad_decision",
            "message": "this run has no pending approval to resume",
        }, 400
    if len(resume_payload["decisions"]) != expected:
        return None, {
            "error": "bad_decision",
            "message": (
                f"the pending approval asks for {expected} decision(s), one per "
                f"action request, but {len(resume_payload['decisions'])} were sent"
            ),
        }, 400

    return resume_payload, None, 200


def phoenix_project_candidates(run: dict[str, Any]) -> list[str]:
    """Phoenix project names that may hold this run's trace."""
    names: list[str] = []
    definition_id = run.get("definition_id")
    if definition_id:
        definition = DefinitionStore.get(int(definition_id))
        if definition and definition.get("name"):
            names.append(str(definition["name"]))
    if run.get("agent_slug"):
        names.append(str(run["agent_slug"]))
    return names
