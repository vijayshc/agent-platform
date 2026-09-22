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
from src.agent_platform.runtime.tool_data.spec import normalize_reply, validate_chart, validate_table
from src.agent_platform.runtime.tool_data.store import TOOL_DATA_STORE, ToolData, ToolDataStore
from src.agent_platform.runtime.tool_data.table import (
    ERROR_KIND,
    Column,
    ToolDataContractError,
    ToolDataTable,
    apply_declared_types,
    build_table,
    contract_kind,
    parse_contract,
    render_markdown,
    render_sample,
)

__all__ = [
    "ERROR_KIND",
    "PROMPT_SKILL_NAME",
    "TOOL_DATA_STORE",
    "Column",
    "ToolData",
    "ToolDataArchive",
    "ToolDataConfig",
    "ToolDataContractError",
    "ToolDataMiddleware",
    "ToolDataPolicy",
    "ToolDataScope",
    "ToolDataStore",
    "ToolDataTable",
    "apply_declared_types",
    "build_table",
    "build_tool_data_middleware",
    "contract_kind",
    "conversation_scope",
    "descriptors_from_payloads",
    "ensure_prompt_skill",
    "load_protocol_instructions",
    "normalize_reply",
    "parse_contract",
    "payloads_from_descriptors",
    "policy_from_config",
    "referenced_call_ids",
    "render_markdown",
    "render_sample",
    "resolve_references",
    "resolve_tool_data",
    "scope_for",
    "scope_from_namespace",
    "tool_data_protocol_note",
    "validate_chart",
    "validate_table",
]
