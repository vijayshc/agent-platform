"""Compile one agent definition into a LangGraph agent.

Two shipped runtimes are supported:

* ``agent``      → ``langchain.agents.create_agent`` (tool-calling ReAct agent)
* ``deep_agent`` → ``deepagents.create_deep_agent`` (planning, filesystem,
  memory files, subagents)

Guardrails come from ``langchain.agents.middleware`` via
:mod:`src.agent_platform.runtime.langchain_middleware`; this module only
assembles tools, prompt and options.
"""

from __future__ import annotations

import logging
from typing import Any

from src.agent_platform.plugins.registry import get_registry
from src.agent_platform.runtime.compiler import (
    DEFAULT_RECURSION_LIMIT,
    OPERATING_POLICY,
    CompileContext,
)
from src.agent_platform.runtime.langgraph_adapter import PlatformState

logger = logging.getLogger("text2sql.agent_platform")

#: Runtime ids that mean "deep agent" (``harness`` is the pre-v2 spelling).
DEEP_RUNTIMES = {"deep_agent", "harness"}


def normalize_runtime(config: dict[str, Any]) -> str:
    runtime = str(config.get("runtime") or "agent").lower()
    return "deep_agent" if runtime in DEEP_RUNTIMES else "agent"


def _model_client(config: dict[str, Any], ctx: CompileContext) -> Any:
    from src.agent_platform.runtime.model_select import (
        compile_model_client,
        generation_options,
    )
    from src.utils.llm_connection_manager import apply_model_options

    # The run owns the model choice: the connection the caller picked (chat or
    # Studio test-run) always wins, and with no pick the operator's default
    # connection is used. A model pinned in the agent definition is design-time
    # metadata only and never overrides a run — otherwise a stale pin silently
    # runs the agent on a connection nobody chose (e.g. one out of credits).
    run_client = getattr(ctx, "client", None)
    client = run_client if run_client is not None else compile_model_client(None, ctx)
    # The agent's own generation parameters (default_options / max_output_tokens)
    # override the connection's Model Parameters for this agent only.
    return apply_model_options(client, generation_options(config))


def compile_mcp_tools(config: dict[str, Any], ctx: CompileContext) -> list[Any]:
    registry = get_registry()
    plugin = registry.get("mcp_binding")
    tools: list[Any] = []
    for binding in config.get("mcp_bindings") or []:
        if plugin is None:
            continue
        spec = dict(binding)
        spec.pop("approval_mode", None)
        mcp_tool = plugin.compile(spec, ctx)
        ctx.mcp_tools.append(mcp_tool)
        tools.append(mcp_tool)
    return tools


def _skills_provider(config: dict[str, Any], ctx: CompileContext) -> Any | None:
    skill_ids = config.get("maf_skill_ids") or config.get("skill_ids") or []
    paths = config.get("skill_paths") or []
    if not skill_ids and not paths:
        return None
    plugin = get_registry().get("file_skills")
    if plugin is None:
        return None
    return plugin.compile({"skill_ids": skill_ids, "paths": paths}, ctx)


def _function_tools(config: dict[str, Any], ctx: CompileContext) -> list[Any]:
    names = config.get("function_tools") or []
    if not names and not ctx.extra_function_tools:
        return []
    plugin = get_registry().get("function_tools")
    if plugin is None:
        return list(ctx.extra_function_tools)
    return plugin.compile({"names": names}, ctx)


def response_format_for(config: dict[str, Any]) -> Any:
    """Translate ``response_format`` into a LangChain structured-output strategy."""
    spec = config.get("response_format")
    if not isinstance(spec, dict):
        return None
    schema = spec.get("schema")
    if not schema:
        raise RuntimeError("response_format needs a JSON schema.")
    from langchain.agents.structured_output import (
        AutoStrategy,
        ProviderStrategy,
        ToolStrategy,
    )

    strategy = str(spec.get("strategy") or "auto").lower()
    if strategy == "tool":
        return ToolStrategy(schema)
    if strategy == "provider":
        return ProviderStrategy(schema)
    return AutoStrategy(schema)


def effective_middleware_config(config: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """The OOTB middleware this agent installs, keyed by registry id."""
    from src.agent_platform.runtime.langchain_middleware import normalize_middleware_config

    return normalize_middleware_config(config)


def _with_approval_tools(config: dict[str, Any], declared: list[str]) -> dict[str, Any]:
    """Fold MCP tools that declare ``approval_mode: always_require`` into HITL.

    The declaration travels on the tool's metadata; the pause itself is enforced
    by ``HumanInTheLoopMiddleware``, so the names join that middleware's config.
    """
    entries = {str(k): dict(v or {}) for k, v in (config.get("middleware") or {}).items()}
    if declared:
        entry = dict(entries.get("human_in_the_loop") or {})
        entry["tools"] = list(dict.fromkeys([*(entry.get("tools") or []), *declared]))
        entries["human_in_the_loop"] = entry
    return entries


def _instructions(config: dict[str, Any], ctx: CompileContext, name: str, client: Any) -> str:
    instructions = config.get("instructions") or f"You are {name}."
    conn_instruction = getattr(client, "_llm_system_instruction", None)
    if conn_instruction:
        instructions = f"{conn_instruction}\n\n{instructions}"
    provider = _skills_provider(config, ctx)
    if provider is not None:
        ctx.skills_provider = provider  # type: ignore[attr-defined]
        skills_instruction = provider.get_instructions()
        if skills_instruction:
            instructions = f"{instructions}\n{skills_instruction}"
    instructions = f"{instructions}\n\n{OPERATING_POLICY}"
    try:
        from src.agent_platform.runtime.sandbox import capabilities_note

        instructions = f"{instructions}{capabilities_note()}"
    except Exception:
        logger.debug("sandbox capability probe unavailable", exc_info=True)
    return instructions


def _deep_agent_kwargs(
    config: dict[str, Any], ctx: CompileContext, provider: Any
) -> dict[str, Any]:
    spec = dict(config.get("deep_agent") or {})
    subagents: list[dict[str, Any]] = []
    for raw in spec.get("subagents") or []:
        if not isinstance(raw, dict) or not raw.get("name"):
            continue
        entry: dict[str, Any] = {
            "name": str(raw["name"]),
            "description": str(raw.get("description") or raw.get("name")),
        }
        if raw.get("instructions") or raw.get("system_prompt") or raw.get("prompt"):
            entry["system_prompt"] = str(
                raw.get("system_prompt") or raw.get("prompt") or raw.get("instructions")
            )
        tool_names = [str(t) for t in (raw.get("tools") or [])]
        # deepagents inherits the parent's tools when the key is absent, so an
        # explicit (possibly empty) list is only sent when the author chose one.
        if tool_names:
            from src.agent_platform.plugins.tools.builtins import get_function_tool

            entry["tools"] = [t for t in (get_function_tool(n) for n in tool_names) if t is not None]
        if raw.get("model"):
            entry["model"] = str(raw["model"])
        if raw.get("mode") in {"isolated", "fork", "handoff"}:
            entry["mode"] = raw["mode"]
        subagents.append(entry)

    # deepagents skills are directories that contain SKILL.md; the app's skill
    # packages are exactly that, so the provider's roots are handed over as-is.
    skill_paths = [str(skill.root) for skill in (provider.skills.values() if provider else [])]
    skill_paths.extend(str(p) for p in spec.get("skills") or [])

    kwargs: dict[str, Any] = {
        "subagents": subagents or None,
        "skills": skill_paths or None,
        "memory": list(spec.get("memory") or []) or None,
        "permissions": list(spec.get("permissions") or []) or None,
        "interrupt_on": dict(spec.get("interrupt_on") or {}) or None,
    }
    if ctx.workspace_dir:
        from deepagents.backends import FilesystemBackend

        kwargs["backend"] = FilesystemBackend(root_dir=ctx.workspace_dir, virtual_mode=True)
    return kwargs


def approval_tool_names(tools: list[Any]) -> list[str]:
    """Tools that declared ``approval_mode: always_require`` (MCP bindings).

    The declaration travels on the tool's metadata; the approval itself is
    enforced by ``HumanInTheLoopMiddleware``, so the names are folded into that
    middleware's configuration here.
    """
    names: list[str] = []
    for tool in tools:
        meta = getattr(tool, "metadata", None) or {}
        if meta.get("approval_mode") == "always_require":
            name = str(getattr(tool, "name", "") or "")
            if name:
                names.append(name)
    return names


async def compile_agent(
    config: dict[str, Any],
    ctx: CompileContext,
    *,
    name: str,
    extra_tools: list[Any] | None = None,
) -> Any:
    """Build a compiled agent graph for ``config``."""
    from src.agent_platform.runtime.langchain_middleware import build_middleware
    from src.agent_platform.runtime.tracing_model import TracingChatModel

    client = _model_client(config, ctx)
    provider = _skills_provider(config, ctx)
    if provider is not None:
        ctx.skills_provider = provider  # type: ignore[attr-defined]

    mcp_handles = compile_mcp_tools(config, ctx)
    if getattr(ctx, "connect_mcp", True):
        from src.agent_platform.runtime.compiler import connect_mcp_tools

        mcp_tools = await connect_mcp_tools(mcp_handles)
    else:
        mcp_tools = []
    ctx.connected_mcp_tools = mcp_tools  # type: ignore[attr-defined]

    from src.agent_platform.runtime.memory_tools import memory_tools

    skill_tools = provider.get_tools() if provider else []
    memory = memory_tools() if getattr(ctx, "store", None) is not None else []
    candidates = [
        *_function_tools(config, ctx),
        *skill_tools,
        *memory,
        *mcp_tools,
        *list(extra_tools or []),
    ]
    tools: list[Any] = []
    seen: set[str] = set()
    for tool in candidates:
        tool_name = str(getattr(tool, "name", "") or "")
        if tool_name and tool_name in seen:
            continue
        if tool_name:
            seen.add(tool_name)
        tools.append(tool)

    storage = getattr(ctx, "checkpoint_storage", None)
    store = getattr(ctx, "store", None)
    traced = TracingChatModel(inner=client, run_id=ctx.run_id, agent_name=name, config=config)

    declared = approval_tool_names(tools)
    middleware = build_middleware(
        {**config, "middleware": _with_approval_tools(config, declared)},
        ctx,
        model=traced,
    )
    instructions = _instructions(config, ctx, name, client)
    structured = response_format_for(config)
    runtime = normalize_runtime(config)

    if runtime == "deep_agent":
        from deepagents import create_deep_agent

        from src.agent_platform.runtime.langgraph_adapter import deep_platform_state

        graph = create_deep_agent(
            model=traced,
            tools=tools or None,
            system_prompt=instructions,
            middleware=middleware,
            response_format=structured,
            state_schema=deep_platform_state(),
            checkpointer=storage,
            store=store,
            name=name,
            **_deep_agent_kwargs(config, ctx, provider),
        )
    else:
        from langchain.agents import create_agent

        graph = create_agent(
            model=traced,
            tools=tools,
            system_prompt=instructions or None,
            middleware=middleware,
            response_format=structured,
            state_schema=PlatformState,
            checkpointer=storage,
            store=store,
            name=name,
        )

    # A plain agent gets the platform's step budget; deep agents come with their
    # own much larger one (planning + subagents need it), so the author's value
    # only overrides when they actually set one.
    if runtime == "deep_agent":
        if config.get("recursion_limit"):
            graph = graph.with_config({"recursion_limit": int(config["recursion_limit"])})
    else:
        graph = graph.with_config(
            {"recursion_limit": int(config.get("recursion_limit") or DEFAULT_RECURSION_LIMIT)}
        )

    from src.agent_platform.runtime.policies import apply_node_policies

    apply_node_policies(graph, config)

    graph.kind = "agent"
    graph.name = name
    graph.tools = tools
    graph.client = client
    graph.instructions = instructions
    graph.traced_model = traced
    graph.skills_provider = provider
    graph.store = store
    ctx.agents_by_name[name] = graph
    return graph
