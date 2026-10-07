"""Assistant reply preparation and conversation message persistence."""

from typing import Any, List, Optional, Tuple
from src.agent_platform.conversations.store import ConversationStore
from src.agent_platform.execution.run_store import RunStore
from src.agent_platform.runtime.events import redact_paths
from src.agent_platform.runtime.tool_data import resolve_tool_data


def prepare_reply(
    final_text: str,
    tool_scope: Any,
) -> Tuple[str, List[dict[str, Any]]]:
    """Redact the reply and package the cached data its blocks reference."""
    text = redact_paths(final_text)
    return text, resolve_tool_data(text, tool_scope)


def persist_run_reply(
    conversation_id: int,
    run_id: int,
    content: str,
    pending: Optional[dict[str, Any]],
    agent_name: Optional[str] = None,
    reasoning: str = "",
    tool_data: Optional[List[dict[str, Any]]] = None,
) -> None:
    """Persist assistant message and metadata into ConversationStore."""
    run = RunStore.get(run_id) or {}
    meta: dict[str, Any] = {
        "public_id": run.get("public_id"),
        "pending": bool(pending),
        "hitl": pending,
        "agent": agent_name or run.get("agent_slug"),
    }
    if reasoning.strip():
        meta["reasoning"] = reasoning.strip()
    if tool_data:
        from src.agent_platform.runtime.tool_data import descriptors_from_payloads
        meta["tool_data"] = descriptors_from_payloads(tool_data)
    ConversationStore.upsert_assistant_for_run(
        conversation_id,
        run_id,
        content or "",
        meta=meta,
    )


__all__ = [
    "prepare_reply",
    "persist_run_reply",
]
