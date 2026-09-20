"""Conversation-scoped tool-result data cache for charts and full tables.

A tool that returns a markdown table can be larger than the model should ever
read. This package keeps the full parsed table in a cache scoped to the
conversation while only a configured number of sample rows reach the model. The
model refers back to the cached result by a short reference (``D1``) printed at
the end of every tool response, and the chat renders ``#TABLE_D1`` /
``#CHART_D1`` from the cached rows.

The cache outlives the turn: it is memory-first with a Parquet archive in the
conversation's checkpoint directory, so a reference stays valid for as long as
the conversation does (and across a restart).

Nothing here changes a tool that was not opted in through the agent's
per-tool data settings; such results pass through untouched apart from the
call-id marker.
"""

from __future__ import annotations

from src.agent_platform.runtime.tool_data.archive import ToolDataArchive
from src.agent_platform.runtime.tool_data.middleware import (
    ToolDataMiddleware,
    build_tool_data_middleware,
)
from src.agent_platform.runtime.tool_data.policy import (
    ToolDataConfig,
    ToolDataPolicy,
    policy_from_config,
)
from src.agent_platform.runtime.tool_data.protocol import (
    descriptors_from_payloads,
    payloads_from_descriptors,
    referenced_call_ids,
    resolve_references,
    resolve_tool_data,
)
from src.agent_platform.runtime.tool_data.prompt import (
    PROMPT_SKILL_NAME,
    ensure_prompt_skill,
    load_protocol_instructions,
    tool_data_protocol_note,
)
from src.agent_platform.runtime.tool_data.scope import (
    ToolDataScope,
    conversation_scope,
    scope_for,
    scope_from_namespace,
)
from src.agent_platform.runtime.tool_data.store import TOOL_DATA_STORE, ToolData, ToolDataStore

__all__ = [
    "PROMPT_SKILL_NAME",
    "TOOL_DATA_STORE",
    "ToolData",
    "ToolDataArchive",
    "ToolDataConfig",
    "ToolDataMiddleware",
    "ToolDataPolicy",
    "ToolDataScope",
    "ToolDataStore",
    "build_tool_data_middleware",
    "conversation_scope",
    "descriptors_from_payloads",
    "ensure_prompt_skill",
    "load_protocol_instructions",
    "payloads_from_descriptors",
    "policy_from_config",
    "referenced_call_ids",
    "resolve_references",
    "resolve_tool_data",
    "scope_for",
    "scope_from_namespace",
    "tool_data_protocol_note",
]
