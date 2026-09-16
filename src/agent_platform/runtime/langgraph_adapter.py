from __future__ import annotations

import logging
from functools import lru_cache
from typing import Annotated, Any, Sequence, TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages

logger = logging.getLogger("text2sql.agent_platform")


def _reduce_active_agent(current: str | None, new_val: str | list[str] | None) -> str | None:
    if isinstance(new_val, list):
        return str(new_val[-1]) if new_val else current
    return str(new_val) if new_val is not None else current


def _merge_flow(current: dict[str, Any] | None, new_val: dict[str, Any] | None) -> dict[str, Any]:
    """Flow state is a scratchpad: later writes win, keys accumulate."""
    merged = dict(current or {})
    merged.update(new_val or {})
    return merged


def _last_write(current: Any, new_val: Any) -> Any:
    """Last write wins, including an explicit ``None``.

    ``create_agent`` clears ``structured_response`` between turns by writing
    ``None``; a reducer that skipped nulls would make one turn's structured
    output leak into the next (a fan-out would then re-run the previous list).
    """
    return new_val


def _keep_value(current: Any, new_val: Any) -> Any:
    """Last non-null write wins.

    Parallel branches are handed the same run context and echo it back in their
    state update. Without a reducer LangGraph refuses the step ("can receive only
    one value per step"); with one, the identical values collapse to one.
    """
    return current if new_val is None else new_val


class PlatformState(TypedDict, total=False):
    messages: Annotated[Sequence[BaseMessage], add_messages]
    active_agent: Annotated[str, _reduce_active_agent]
    #: Scratchpad for graph flows (router decisions, fan-out size, human reviews).
    flow: Annotated[dict[str, Any], _merge_flow]
    #: Set by ``create_agent(response_format=...)``: the planner's structured
    #: output, which the fan-out node reads to build its Send list.
    structured_response: Annotated[Any, _last_write]
    # Run context: every node receives it and writes it back unchanged, so the
    # keys need a reducer for concurrent branches to be possible at all.
    workspace_dir: Annotated[str, _keep_value]
    conversation_id: Annotated[str, _keep_value]
    run_id: Annotated[int, _keep_value]
    user_id: Annotated[int, _keep_value]


def is_agent(obj: Any) -> bool:
    if obj is None:
        return False
    return getattr(obj, "kind", None) == "agent" or hasattr(obj, "run") or hasattr(obj, "invoke") or hasattr(obj, "ainvoke")


def is_workflow(obj: Any) -> bool:
    if obj is None:
        return False
    return getattr(obj, "kind", "workflow") == "workflow" or hasattr(obj, "nodes") or hasattr(obj, "builder")

@lru_cache(maxsize=1)
def deep_platform_state() -> Any:
    """The deep-agent state schema: ``DeepAgentState`` + the platform context.

    Deep agents run through their own state schema, so the run context
    (workspace_dir, run_id, ...) has to be declared on it for the platform's
    state-injected tools to work inside a deep agent.
    """
    from deepagents import DeepAgentState

    class DeepPlatformState(DeepAgentState, total=False):
        flow: Annotated[dict[str, Any], _merge_flow]
        workspace_dir: Annotated[str, _keep_value]
        conversation_id: Annotated[str, _keep_value]
        run_id: Annotated[int, _keep_value]
        user_id: Annotated[int, _keep_value]

    return DeepPlatformState
