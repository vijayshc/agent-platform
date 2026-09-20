"""Conversation endpoints: the chat rail and its message turns.

A message turn creates the platform run for that conversation and drives it
through the same SSE plumbing as ``/runs``.
"""

from __future__ import annotations

import threading

from flask import Blueprint, g, jsonify, request

from src.agent_platform.api.api_helpers import (
    can_access_conversation,
    conversation_denied,
    run_model_client,
    user_can_access_definition,
)
from src.agent_platform.api.auth import api_auth_required, current_user_id
from src.agent_platform.api.run_stream import (
    HOST,
    build_run_sse_response,
    pop_cancel_event,
    run_on_loop,
    set_cancel_event,
)
from src.agent_platform.catalog.store import DefinitionStore
from src.agent_platform.conversations.store import ConversationStore
from src.agent_platform.execution.run_store import RunStore
from src.agent_platform.execution.span_sink import SpanSink
from src.agent_platform.paths import run_workspace_dir
from src.agent_platform.runtime.model_select import parse_model_payload

conversation_bp = Blueprint("conversation_api", __name__)


@conversation_bp.get("/conversations")
@api_auth_required("runs:read")
def list_conversations():
    user_id = current_user_id()
    q = request.args.get("q")
    # Pagination (limit/offset). The chat rail lazy-loads conversations so it
    # stays fast once a user has accumulated many chats.
    raw_limit = request.args.get("limit")
    raw_offset = request.args.get("offset", "0")
    try:
        offset = max(0, int(raw_offset))
    except (TypeError, ValueError):
        offset = 0
    limit: int | None = None
    if raw_limit:
        try:
            limit = max(1, int(raw_limit))
        except (TypeError, ValueError):
            limit = None

    if q:
        convs = ConversationStore.search(user_id, q, limit=limit, offset=offset)
        total = ConversationStore.count_search(user_id, q)
    else:
        convs = ConversationStore.list_for_user(user_id, limit=limit, offset=offset)
        total = ConversationStore.count_for_user(user_id)
    return jsonify({"conversations": convs, "total": total, "offset": offset})


@conversation_bp.post("/conversations")
@api_auth_required("runs:write")
def create_conversation():
    data = request.get_json(silent=True) or {}
    title = str(data.get("title") or "New chat").strip()
    agent_slug = str(data.get("agent_id") or data.get("agent_slug") or "").strip() or None
    user_id = current_user_id()
    if agent_slug:
        # A conversation binds to one agent. Creating one for an agent the
        # caller may not run would persist an unauthorized reference and set
        # the chat up for a guaranteed 403; fail closed at creation instead.
        definition = DefinitionStore.resolve(agent_slug)
        if definition is None:
            return jsonify({"error": "agent not found"}), 404
        if not user_can_access_definition(definition, user_id):
            g.audit_reason = "no access to this agent"
            return jsonify({"error": "forbidden", "message": "You do not have access to this agent"}), 403
    conv = ConversationStore.create(user_id=user_id, title=title, agent_slug=agent_slug)
    return jsonify(conv), 201


def _attach_tool_data(conv: dict, messages: list[dict]) -> list[dict]:
    """Rehydrate each turn's persisted tool-data descriptors from the archive.

    A message stores only descriptors (no rows), so a conversation's rows live
    in exactly one place -- its tool-data archive. Reading re-resolves them for
    the client; a reference whose archive is gone is dropped, which the chat
    renders as "no longer available" rather than an empty chart.
    """
    public_id = conv.get("public_id")
    if not public_id:
        return messages
    from src.agent_platform.runtime.tool_data import (
        conversation_scope,
        payloads_from_descriptors,
    )

    scope = conversation_scope(str(public_id))
    for message in messages:
        meta = message.get("meta")
        if not isinstance(meta, dict):
            continue
        descriptors = meta.get("tool_data")
        if not isinstance(descriptors, list) or not descriptors:
            continue
        payloads = payloads_from_descriptors(descriptors, scope)
        if payloads:
            meta["tool_data"] = payloads
        else:
            meta.pop("tool_data", None)
    return messages


@conversation_bp.get("/conversations/<conversation_id>")
@api_auth_required("runs:read")
def get_conversation(conversation_id: str):
    conv = ConversationStore.get(conversation_id)
    if conv is None:
        return jsonify({"error": "conversation not found"}), 404
    if not can_access_conversation(conv):
        return conversation_denied()
    messages = _attach_tool_data(conv, ConversationStore.list_messages(int(conv["id"])))
    conv_data = dict(conv)
    conv_data["messages"] = messages
    return jsonify(conv_data)


@conversation_bp.delete("/conversations/<conversation_id>")
@api_auth_required("runs:write")
def delete_conversation(conversation_id: str):
    conv = ConversationStore.get(conversation_id)
    if conv is None:
        return jsonify({"error": "conversation not found"}), 404
    if not can_access_conversation(conv):
        return conversation_denied()
    ConversationStore.delete(int(conv["id"]))
    # A conversation owns its cached tool tables, so deleting it deletes them:
    # the Parquet archive next to the conversation's checkpoint would otherwise
    # outlive every reference to it.
    if conv.get("public_id"):
        from src.agent_platform.runtime.tool_data import (
            TOOL_DATA_STORE,
            conversation_scope,
        )

        TOOL_DATA_STORE.clear(conversation_scope(str(conv["public_id"])))
    return jsonify({"success": True, "id": conv["id"]})


@conversation_bp.get("/conversations/<conversation_id>/messages")
@api_auth_required("runs:read")
def get_conversation_messages(conversation_id: str):
    conv = ConversationStore.get(conversation_id)
    if conv is None:
        return jsonify({"error": "conversation not found"}), 404
    if not can_access_conversation(conv):
        return conversation_denied()
    messages = _attach_tool_data(conv, ConversationStore.list_messages(int(conv["id"])))
    return jsonify({"messages": messages})


@conversation_bp.post("/conversations/<conversation_id>/messages")
@api_auth_required("runs:write")
def post_conversation_message(conversation_id: str):
    conv = ConversationStore.get(conversation_id)
    if conv is None:
        return jsonify({"error": "conversation not found"}), 404
    if not can_access_conversation(conv):
        return conversation_denied()
    cid = int(conv["id"])
    data = request.get_json(silent=True) or {}
    input_text = (data.get("input") or data.get("prompt") or data.get("task") or "").strip()
    agent_id = data.get("agent_id") or data.get("agent_slug") or conv.get("agent_slug")
    attachments = list(data.get("attachments") or [])
    stream = bool(data.get("stream", True))

    definition = DefinitionStore.resolve(str(agent_id)) if agent_id else None
    if definition is None:
        # A chat turn must run a real, resolvable definition -- never an
        # implicit default agent that bypasses the agent access gate.
        g.audit_reason = "agent not found"
        return jsonify({"error": "agent not found"}), 404
    if not user_can_access_definition(definition, current_user_id()):
        g.audit_reason = "no access to this agent"
        return jsonify({"error": "forbidden", "message": "You do not have access to this agent"}), 403
    client, model_error = run_model_client(data)
    if model_error is not None:
        return model_error

    user_id = current_user_id()
    run = RunStore.create(
        task=input_text,
        definition_id=definition.get("id"),
        user_id=user_id,
        conversation_id=cid,
        agent_slug=definition.get("slug") or str(agent_id or "agent"),
        entity_type=definition.get("kind") or "agent",
        entity_id=definition.get("id") or 0,
        input_payload={"task": input_text, "attachments": attachments, "model": parse_model_payload(data)},
    )
    run_id = int(run["id"])
    workspace = str(run_workspace_dir(run_id, cid))
    RunStore.update_workspace(run_id, workspace)

    ConversationStore.add_message(
        cid,
        role="user",
        content=input_text,
        run_id=run_id,
        meta={"attachments": attachments} if attachments else None,
    )

    if not stream:
        cancel_ev = threading.Event()
        set_cancel_event(run_id, cancel_ev)
        try:
            result = run_on_loop(
                HOST.run_sync,
                definition=definition,
                input_text=input_text,
                run_id=run_id,
                conversation_id=cid,
                attachments=attachments,
                cancel_event=cancel_ev,
                client=client,
            )
        finally:
            pop_cancel_event(run_id)

        updated_run = RunStore.get(run_id)
        if updated_run:
            updated_run["events"] = SpanSink.get_events(run_id)
        reply = result.get("reply") or (updated_run.get("final_reply") if updated_run else "")
        if reply and reply.strip():
            ConversationStore.add_message(cid, role="assistant", content=reply.strip(), run_id=run_id)
        return jsonify({
            "run": updated_run,
            "run_id": run_id,
            "public_id": run["public_id"],
            "conversation_id": cid,
            **result,
        }), 200

    return build_run_sse_response(run, definition, input_text, cid, client=client)
