from __future__ import annotations

from pathlib import Path
from typing import Any
from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)

BODY_CAP = 16384

SSE_TYPES = (
    "status",
    "token",
    "reasoning",
    "progress",
    "agent_switch",
    "tool_call",
    "tool_result",
    "approval_request",
    "skill_load",
    "error",
    "done",
)


def sse_payload(event_type: str, **fields: Any) -> dict[str, Any]:
    payload = {"type": event_type}
    payload.update(fields)
    return payload


def _clip_body(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return value
    text = str(value)
    return text if len(text) <= BODY_CAP else text[:BODY_CAP]




def _plain_text_content(msg: Any) -> str:
    """The answer text in a message, excluding reasoning/tool content blocks.

    New content-block adapters can keep reasoning blocks in ``content``; those
    must not be stringified into the transcript as a Python list literal.
    """
    content = getattr(msg, "content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
                continue
            if not isinstance(block, dict):
                continue
            if block.get("type") in {"reasoning", "thinking", "tool_call", "tool_use", "function_call"}:
                continue
            text = block.get("text")
            if isinstance(text, str):
                parts.append(text)
        return "".join(parts)
    return ""


def _reasoning_text(msg: Any) -> str:
    """Reasoning carried by a provider chunk.

    LangGraph's ``messages`` stream is provider-agnostic, so reasoning can
    arrive either as a v1 content block or as an OpenAI-compatible gateway
    extra (``reasoning`` / ``reasoning_content``). The runtime normalizes both
    into the same SSE event; nothing here creates reasoning for models that did
    not emit it.
    """
    additional = getattr(msg, "additional_kwargs", None)
    if isinstance(additional, dict):
        for key in ("reasoning_content", "reasoning"):
            value = additional.get(key)
            if isinstance(value, str) and value:
                return value
            if isinstance(value, list):
                text = "".join(
                    str(part.get("text") or part.get("content") or "")
                    for part in value
                    if isinstance(part, dict)
                )
                if text:
                    return text
    content = getattr(msg, "content", None)
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if not isinstance(block, dict) or block.get("type") not in {"reasoning", "thinking"}:
                continue
            text = block.get("reasoning") or block.get("text") or block.get("thinking")
            if isinstance(text, str) and text:
                parts.append(text)
        if parts:
            return "".join(parts)
    return ""


def content_to_dict(content: Any) -> dict[str, Any]:
    if content is None:
        return {}
    if isinstance(content, dict):
        return content
    if hasattr(content, "to_dict"):
        try:
            return content.to_dict()
        except Exception:
            pass
    data: dict[str, Any] = {"type": getattr(content, "type", "text")}
    for attr in (
        "text",
        "name",
        "call_id",
        "arguments",
        "result",
        "id",
        "tool_name",
        "error_code",
        "message",
    ):
        value = getattr(content, attr, None)
        if value is not None:
            data[attr] = value
    return data


#: Node names LangGraph creates for the agent's own plumbing: the model and tool
#: nodes, and every node a middleware contributes. They are steps of one agent,
#: not agents of their own, so they never raise an ``agent_switch``.
_INTERNAL_NODES = {
    "tools",
    "agent",
    "model",
    "pre_model_hook",
    "post_model_hook",
    "__start__",
    "__end__",
}


def is_internal_node(name: str | None) -> bool:
    """Whether a graph node is plumbing rather than a participant.

    Subgraph node names arrive as ``<node>:<thread-uuid>`` (for example
    ``tools:f6ca1ce9-...``), and middleware contribute nodes named after their
    class (``SummarizationMiddleware.before_model``), so the name is reduced to
    its node part before matching.
    """
    if not name:
        return False
    node = str(name).split(":", 1)[0]
    if node in _INTERNAL_NODES:
        return True
    return "Middleware." in name or name.endswith("Middleware")


def map_agent_update(
    update: Any,
    *,
    last_agent: str | None = None,
    skip_tokens: bool = False,
    silent_nodes: set[str] | None = None,
    delivered: set[str] | None = None,
) -> tuple[list[dict[str, Any]], str | None]:
    """Map one LangGraph stream event to SSE payloads.

    ``delivered`` is the set of message ids already sent to the client for this
    run. LangGraph treats a message id as its identity (``add_messages`` replaces
    rather than appends a repeated id), so a message reported twice -- the HITL
    middleware re-writing the interrupted turn's AIMessage on resume, a parent
    namespace re-reporting a subgraph's messages -- must be streamed once. Ids of
    messages this call maps are added to the set.
    """
    events: list[dict[str, Any]] = []

    # Handle LangGraph subgraphs tuple: (namespace, mode, data) or (mode, data)
    if isinstance(update, tuple):
        if len(update) == 3:
            namespace, mode, data = update
            subgraph_agent = namespace[-1] if isinstance(namespace, (tuple, list)) and namespace else None
            if subgraph_agent and subgraph_agent != last_agent and not is_internal_node(subgraph_agent):
                events.append(sse_payload("agent_switch", agent=subgraph_agent))
                last_agent = subgraph_agent
            update = (mode, data)

        if len(update) == 2 and isinstance(update[0], str):
            mode, data = update
            if mode == "messages":
                msg, meta = data if isinstance(data, tuple) and len(data) == 2 else (data, {})
                node_name = (meta.get("langgraph_node") if isinstance(meta, dict) else None) or last_agent
                # Control-plane nodes (routers, fan-outs) decide where the flow
                # goes; their model output is a decision, not conversation, and
                # must never reach the user's transcript.
                if silent_nodes and node_name in silent_nodes:
                    return events, last_agent
                # Only stream LLM token deltas (AIMessage/AIMessageChunk) as tokens.
                # ToolMessage/SystemMessage/HumanMessage must NOT be rendered as text
                # tokens (e.g. a load_skill result is a ToolMessage whose body would
                # otherwise leak into the assistant reply). Reuse _map_message so
                # ToolMessage -> tool_result and AIMessage tool_calls -> tool_call.
                if isinstance(msg, (AIMessage, AIMessageChunk, ToolMessage)):
                    sub_events, last_agent = _map_message(
                        msg, last_agent=last_agent or node_name, delivered=delivered
                    )
                    events.extend(sub_events)
                return events, last_agent
            elif mode == "updates":
                sub_events, last_agent = map_agent_update(
                    data,
                    last_agent=last_agent,
                    skip_tokens=True,
                    silent_nodes=silent_nodes,
                    delivered=delivered,
                )
                events.extend(sub_events)
                return events, last_agent
            elif mode == "custom":
                events.extend(_map_custom(data, last_agent=last_agent))
                return events, last_agent

    agent_name = getattr(update, "author_name", None) or getattr(update, "agent_id", None) or last_agent
    if agent_name and agent_name != last_agent:
        events.append(sse_payload("agent_switch", agent=agent_name))
        last_agent = agent_name

    if isinstance(update, dict):
        # LangGraph node update dict e.g. {'agent': {'messages': [...]}} or {'__interrupt__': ...}
        if "__interrupt__" in update:
            intr = update["__interrupt__"]
            interrupts = list(intr) if isinstance(intr, (tuple, list)) else [intr]
            for item in interrupts:
                value = getattr(item, "value", item)
                if not isinstance(value, dict):
                    raise RuntimeError(
                        "Unsupported interrupt payload "
                        f"{value!r}: the runtime pauses only through "
                        "HumanInTheLoopMiddleware, whose interrupt value is "
                        '{"action_requests": [...], "review_configs": [...]}.'
                    )
                # The library payload is emitted unchanged (its action_requests
                # and review_configs become the event's fields); the SSE
                # discriminator and the agent label are the only additions.
                events.append(sse_payload("approval_request", agent=agent_name, **value))
            return events, last_agent

        for node_name, node_data in update.items():
            if silent_nodes and node_name in silent_nodes:
                continue
            if node_name and node_name != last_agent and not is_internal_node(node_name):
                events.append(sse_payload("agent_switch", agent=node_name))
                last_agent = node_name
            msgs = []
            if isinstance(node_data, dict):
                msgs = node_data.get("messages") or []
                if "active_agent" in node_data and node_data["active_agent"] != last_agent:
                    last_agent = node_data["active_agent"]
                    events.append(sse_payload("agent_switch", agent=last_agent))
            elif isinstance(node_data, list):
                msgs = node_data
            for m in msgs:
                sub_events, last_agent = _map_message(
                    m, last_agent=last_agent, skip_tokens=skip_tokens, delivered=delivered
                )
                events.extend(sub_events)
        return events, last_agent

    if isinstance(update, BaseMessage):
        return _map_message(update, last_agent=last_agent, skip_tokens=skip_tokens, delivered=delivered)

    text = getattr(update, "text", None) or getattr(update, "content", None) or ""
    if text and not skip_tokens:
        events.append(sse_payload("token", content=str(text), agent=agent_name))
    return events, last_agent


def _map_custom(data: Any, *, last_agent: str | None) -> list[dict[str, Any]]:
    if data is None:
        return []
    if isinstance(data, dict):
        msg = data.get("message") or data.get("content") or data.get("text")
        event_type = str(data.get("type") or "progress")
        if event_type not in SSE_TYPES:
            event_type = "progress"
        payload = sse_payload(event_type, message=str(msg or ""), agent=last_agent)
        for key, value in data.items():
            if key not in payload:
                payload[key] = value
        return [payload]
    return [sse_payload("progress", message=str(data), agent=last_agent)]


def _map_message(
    msg: Any,
    *,
    last_agent: str | None = None,
    skip_tokens: bool = False,
    delivered: set[str] | None = None,
) -> tuple[list[dict[str, Any]], str | None]:
    events: list[dict[str, Any]] = []
    if isinstance(msg, AIMessage) or isinstance(msg, AIMessageChunk):
        content = getattr(msg, "content", "")
        complete = not isinstance(msg, AIMessageChunk)
        # A message the client already has is not replayed at all: not as text,
        # not as a tool call.
        if complete and _already_delivered(msg, delivered):
            return events, last_agent

        if not skip_tokens:
            reasoning = _reasoning_text(msg)
            if reasoning:
                events.append(
                    sse_payload(
                        "reasoning",
                        content=reasoning,
                        reasoning_id=str(getattr(msg, "id", "") or ""),
                        agent=last_agent,
                    )
                )
        content_text = _plain_text_content(msg)
        if content_text and not skip_tokens:
            events.append(sse_payload("token", content=content_text, agent=last_agent))
        if complete:
            # Only a complete AIMessage carries reliable tool_calls. Streaming
            # AIMessageChunks arrive incrementally and their tool_calls may have
            # partial/empty args (e.g. args={} before the arguments stream in),
            # which would emit a misleading tool_call event. Emit tool_call only
            # from the complete AIMessage (the "updates" node output), never from
            # a chunk.
            tool_calls = getattr(msg, "tool_calls", [])
            events.append(
                sse_payload(
                    "chat",
                    content=content_text,
                    tool_calls=tool_calls,
                    intermediate=bool(tool_calls),
                    agent=last_agent,
                )
            )
            for tc in getattr(msg, "tool_calls", []):
                name = tc.get("name")
                payload = sse_payload(
                    "tool_call",
                    tool_name=name,
                    call_id=tc.get("id"),
                    arguments=tc.get("args"),
                    agent=last_agent,
                )
                events.append(payload)
                if name == "load_skill":
                    events.append(sse_payload("skill_load", skill=tc.get("args"), agent=last_agent))
    elif isinstance(msg, ToolMessage):
        if _already_delivered(msg, delivered):
            return events, last_agent
        events.append(
            sse_payload(
                "tool_result",
                tool_name=getattr(msg, "name", None) or getattr(msg, "tool_name", "tool"),
                call_id=getattr(msg, "tool_call_id", None),
                result=_clip_body(getattr(msg, "content", "")),
                agent=last_agent,
            )
        )
    return events, last_agent


def _already_delivered(msg: Any, delivered: set[str] | None) -> bool:
    """True for a message the client already has; records the ones it does not.

    Only complete messages take part: a streamed chunk shares its id with the
    complete message that follows it, and the complete message is the one that
    carries the tool calls.
    """
    if delivered is None:
        return False
    message_id = getattr(msg, "id", None)
    if not message_id:
        return False
    key = str(message_id)
    if key in delivered:
        return True
    delivered.add(key)
    return False


def map_workflow_event(event: Any, *, last_agent: str | None = None) -> tuple[list[dict[str, Any]], str | None]:
    return map_agent_update(event, last_agent=last_agent)


def _extract_text(item: Any) -> str:
    if item is None:
        return ""
    if isinstance(item, str):
        return item
    if isinstance(item, (list, tuple)):
        return "\n\n".join(c for c in (_extract_text(p) for p in item) if c)
    if isinstance(item, dict):
        msgs = item.get("messages") or []
        if msgs:
            return _extract_text(msgs)
    if hasattr(item, "content"):
        return str(item.content)
    if hasattr(item, "text"):
        return str(item.text)
    return ""


# ---------------------------------------------------------------------------
# Host-path redaction
#
# Tool output, commands and the model's own summary routinely contain absolute
# host paths (the repository root, the run workspace, the interpreter). Those are
# deployment details the user does not need, and they leak into the chat
# transcript, the run record and any exported transcript. Redacting at the event
# boundary is the one place that catches every source, including paths printed by
# a command the model chose to run.
# ---------------------------------------------------------------------------

_PATH_ALIASES: list[tuple[str, str]] = []


def register_path_alias(absolute: str | Path | None, alias: str) -> None:
    """Register a host path that should be shown to users as ``alias``.

    Longest paths win, so a workspace is replaced before the repository root that
    contains it.
    """
    if not absolute:
        return
    text = str(absolute).rstrip("/")
    if not text:
        return
    for index, (existing, _) in enumerate(_PATH_ALIASES):
        if existing == text:
            _PATH_ALIASES[index] = (text, alias)
            break
    else:
        _PATH_ALIASES.append((text, alias))
    _PATH_ALIASES.sort(key=lambda pair: len(pair[0]), reverse=True)


def redact_paths(value: Any) -> Any:
    """Recursively replace registered host paths with their alias."""
    if isinstance(value, str):
        return _redact_text(value)
    if isinstance(value, dict):
        return {key: redact_paths(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact_paths(item) for item in value]
    return value


def _redact_text(text: str) -> str:
    if not text or not _PATH_ALIASES or "/" not in text:
        return text
    out = text
    for absolute, alias in _PATH_ALIASES:
        if absolute in out:
            out = out.replace(absolute, alias)
    return out


def register_default_path_aliases() -> None:
    """Alias the well-known roots once, at import/startup time."""
    from src.agent_platform.paths import (
        APP_ROOT,
        uploads_dir,
        workspace_root,
    )

    # Longest match wins, so register full interpreter paths before their dirs
    # and the workspace before the repository root that contains it.
    home = Path.home()
    register_path_alias("/tmp/agent-platform-bin/python3", "python3")
    register_path_alias("/tmp/agent-platform-bin/python", "python")
    register_path_alias(home / "anaconda3" / "bin" / "python3", "python")
    register_path_alias(home / "anaconda3" / "bin" / "python", "python")
    register_path_alias(workspace_root(), "<workspace>")
    register_path_alias(uploads_dir(), "<uploads>")
    register_path_alias(APP_ROOT, "<app>")
    register_path_alias(home, "~")
