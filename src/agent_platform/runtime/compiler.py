"""Compile a definition into a runnable LangGraph.

This module owns the public entry points and the shared compile context; the
agent path lives in :mod:`agent_compile` and the workflow path in
:mod:`workflow_compile`. Both are thin wrappers around shipped constructors
(``create_agent``, ``create_deep_agent``, ``create_supervisor``, ``create_swarm``,
``StateGraph``) — see ``catalog/capabilities`` for the registry the editor uses.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

from src.agent_platform.plugins import register_builtin_plugins
from src.agent_platform.runtime.langgraph_adapter import is_agent, is_workflow

# Appended to every compiled agent's system prompt. Failures are the one place
# models reliably burn a whole run: they re-issue the same broken call instead of
# stopping. This is a behavioural guardrail, deliberately free of tool
# choreography so it cannot conflict with an agent's own instructions.
OPERATING_POLICY = (
    "## Operating rules\n"
    "- If a tool call or command fails, do not repeat it unchanged. Read the error, "
    "change something concrete, and try at most one alternative approach.\n"
    "- If the second attempt fails too, stop and report the blocker: what you tried, "
    "the exact error, and what you need. Do not keep retrying.\n"
    "- Never report success unless you verified it (for example, the file exists and "
    "opens). State plainly when something did not work.\n"
    "- Prefer finishing the task with what you have over exploring unrelated details."
)

logger = logging.getLogger("text2sql.agent_platform")

#: Step budget for a run whose definition sets no ``recursion_limit``. LangGraph
#: counts graph super-steps (one per node execution), so this is the run's loop
#: guard: a router that loops back without a ``max_visits`` cap can burn through
#: it. 100 gives a long tool-calling turn room to finish while still failing a
#: genuinely stuck loop.
DEFAULT_RECURSION_LIMIT = 100


@dataclass
class CompileContext:
    client: Any = None
    workspace_dir: str | None = None
    conversation_id: str | None = None
    run_id: int | None = None
    mcp_tools: list[Any] = field(default_factory=list)
    agents_by_name: dict[str, Any] = field(default_factory=dict)
    participants: list[Any] = field(default_factory=list)
    manager_agent: Any = None
    aggregator_agent: Any = None
    hitl: dict[str, Any] = field(default_factory=dict)
    extra_function_tools: list[Any] = field(default_factory=list)
    resolve_refs: bool = True
    checkpoint_storage: Any = None
    store: Any = None
    user_id: int | None = None
    require_history_persistence: bool = False
    connect_mcp: bool = True


@dataclass
class CompiledRunnable:
    kind: str
    runnable: Any
    name: str
    definition: dict[str, Any]
    mcp_tools: list[Any] = field(default_factory=list)
    session: Any = None

    @property
    def is_workflow(self) -> bool:
        return self.kind == "workflow" or is_workflow(self.runnable)

    @property
    def is_agent(self) -> bool:
        return self.kind == "agent" or is_agent(self.runnable)


async def compile_definition(
    definition: dict[str, Any],
    *,
    client: Any = None,
    workspace_dir: str | None = None,
    conversation_id: str | None = None,
    run_id: int | None = None,
    extra_function_tools: list[Any] | None = None,
    checkpoint_storage: Any = None,
    store: Any = None,
    user_id: int | None = None,
    connect_mcp: bool = True,
) -> CompiledRunnable:
    """Compile a definition into a runnable LangGraph.

    MCP bindings are connected before the agent graphs are built so the full
    tool list is handed to ``create_agent`` at construction time. Set
    ``connect_mcp=False`` for a structural compile (validation/inspection) that
    resolves server references and builds the graph without opening sessions.
    """
    register_builtin_plugins()
    config = dict(definition.get("config") or definition)
    kind = (definition.get("kind") or config.get("kind") or "agent").lower()
    pattern = (config.get("pattern") or "").lower()
    from src.agent_platform.runtime.checkpointing import ensure_store, memory_enabled

    ctx = CompileContext(
        client=client,
        workspace_dir=workspace_dir,
        conversation_id=conversation_id,
        run_id=run_id,
        hitl=dict(config.get("hitl") or {}),
        extra_function_tools=list(extra_function_tools or []),
        checkpoint_storage=checkpoint_storage,
        store=ensure_store(store) if memory_enabled(config) else None,
        user_id=user_id,
        require_history_persistence=pattern == "swarm",
        connect_mcp=connect_mcp,
    )
    name = definition.get("name") or config.get("name") or "agent"
    try:
        if kind == "workflow":
            from src.agent_platform.runtime.workflow_compile import compile_workflow

            runnable = await compile_workflow(config, ctx, name=name)
            kind_out = "workflow"
        else:
            from src.agent_platform.runtime.agent_compile import compile_agent

            runnable = await compile_agent(config, ctx, name=name)
            kind_out = "agent"
    except Exception:
        await close_mcp_tools(ctx.mcp_tools)
        raise
    return CompiledRunnable(
        kind=kind_out,
        runnable=runnable,
        name=name,
        definition=definition if "config" in definition else {"config": config, "kind": kind, "name": name},
        mcp_tools=list(ctx.mcp_tools),
    )


def compile_definition_sync(definition: dict[str, Any], **kwargs: Any) -> CompiledRunnable:
    """Blocking structural compile without MCP sessions (validation/tests)."""
    kwargs.pop("connect_mcp", None)
    return _run_blocking(compile_definition(definition, connect_mcp=False, **kwargs))


def _run_blocking(coro: Any) -> Any:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    # Already inside an event loop: run the coroutine on a worker thread loop.
    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


async def connect_mcp_tools(mcp_tools: list[Any]) -> list[Any]:
    """Open every MCP handle and return the resulting tool functions.

    Connections happen sequentially in the current task because
    ``mcp.stdio_client``'s anyio cancel scope must be exited from the same task
    that entered it.
    """
    if not mcp_tools:
        return []
    from src.agent_platform.plugins.mcp.errors import describe_error

    connected: list[Any] = []
    errors: list[str] = []
    for mcp_tool in mcp_tools:
        name = getattr(mcp_tool, "name", "mcp")
        try:
            await mcp_tool.connect()
            connected.extend(list(getattr(mcp_tool, "functions", None) or []))
        except Exception as exc:
            logger.exception("MCP connect failed for %s", name)
            errors.append(f"{name}: {describe_error(exc)}")
    if errors:
        raise RuntimeError("MCP connect failed: " + "; ".join(errors))
    return connected


async def close_mcp_tools(mcp_tools: list[Any]) -> None:
    for tool in mcp_tools or []:
        closer = getattr(tool, "close", None)
        if closer is None:
            continue
        try:
            res = closer()
            if hasattr(res, "__await__"):
                await res
        except Exception:
            logger.debug("MCP close failed", exc_info=True)
