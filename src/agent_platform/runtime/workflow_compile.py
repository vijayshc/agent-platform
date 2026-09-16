"""Compile a workflow definition (supervisor, swarm or a graph flow).

* ``supervisor`` → ``langgraph_supervisor.create_supervisor``
* ``swarm``      → ``langgraph_swarm.create_swarm``
* everything else (the documented workflow patterns and custom graphs) → the
  declarative :mod:`plugins.orchestration.graph` compiler

Participants may be inline agent specs, references to saved agents, or
references to saved workflows — a compiled supervisor used as a participant is
how hierarchical teams are built.
"""

from __future__ import annotations

from typing import Any

from src.agent_platform.plugins.registry import get_registry
from src.agent_platform.runtime.compiler import CompileContext

#: Keys a participant inherits from its parent workflow unless it overrides them.
INHERITED_KEYS = (
    "model",
    "default_options",
    "max_output_tokens",
    "maxContextWindowTokens",
    "max_context_window_tokens",
    "mcp_bindings",
    "function_tools",
    "maf_skill_ids",
    "runtime",
    "hitl",
    "middleware",
    "response_format",
    "deep_agent",
)

PARTICIPANT_PATTERNS = {"supervisor", "swarm"}


def _participant_name(spec: Any, ctx: CompileContext) -> str:
    if not isinstance(spec, dict):
        spec = {"ref": spec}
    if "ref" in spec and ctx.resolve_refs:
        from src.agent_platform.catalog.store import DefinitionStore

        row = DefinitionStore.resolve(spec["ref"])
        if row is None:
            raise RuntimeError(f"Unknown participant ref: {spec['ref']}")
        return str(row.get("name") or spec["ref"])
    return str(spec.get("name") or spec.get("role") or spec.get("agent") or spec.get("id") or "Agent")


def _merge_parent(spec: dict[str, Any], parent_config: dict[str, Any]) -> dict[str, Any]:
    merged = dict(parent_config)
    for key in INHERITED_KEYS:
        if key not in spec and key in parent_config:
            merged[key] = parent_config[key]
    merged.update(spec)
    return merged


async def resolve_member(
    spec: Any,
    ctx: CompileContext,
    parent_config: dict[str, Any],
    *,
    extra_tools: list[Any] | None = None,
) -> Any:
    """Compile one participant: a saved definition, or an inline agent spec."""
    if spec is None:
        raise RuntimeError("Empty participant")
    if not isinstance(spec, dict):
        spec = {"ref": spec}
    if "ref" in spec and ctx.resolve_refs:
        from src.agent_platform.catalog.store import DefinitionStore

        row = DefinitionStore.resolve(spec["ref"])
        if row is None:
            raise RuntimeError(f"Unknown participant ref: {spec['ref']}")
        nested = dict(row.get("config") or {})
        nested_name = str(row.get("name") or spec["ref"])
        if (row.get("kind") or nested.get("kind") or "agent").lower() == "workflow":
            if extra_tools:
                raise RuntimeError(
                    f"'{nested_name}' is a workflow, so per-participant handoff tools "
                    "cannot be attached. Reference it as an agent instead."
                )
            return await compile_workflow(nested, ctx, name=nested_name)
        from src.agent_platform.runtime.agent_compile import compile_agent

        return await compile_agent(nested, ctx, name=nested_name, extra_tools=extra_tools)

    name = str(spec.get("name") or spec.get("role") or spec.get("agent") or spec.get("id") or "Agent")
    from src.agent_platform.runtime.agent_compile import compile_agent

    return await compile_agent(
        _merge_parent(spec, parent_config), ctx, name=name, extra_tools=extra_tools
    )


async def _compile_participants(config: dict[str, Any], ctx: CompileContext) -> list[Any]:
    specs = list(config.get("participants") or config.get("agents") or [])
    if str(config.get("pattern") or "").lower() == "swarm":
        from src.agent_platform.plugins.orchestration.swarm import (
            swarm_handoff_map,
            swarm_handoff_tools,
        )

        names = [_participant_name(spec, ctx) for spec in specs]
        allowed = swarm_handoff_map(config, names)
        members: list[Any] = []
        for spec, member_name in zip(specs, names):
            tools = swarm_handoff_tools(member_name, allowed.get(member_name, []))
            members.append(await resolve_member(spec, ctx, config, extra_tools=tools))
        return members
    return [await resolve_member(spec, ctx, config) for spec in specs]


async def _compile_graph_nodes(config: dict[str, Any], ctx: CompileContext) -> dict[str, Any]:
    """Pre-compile the agent and saved-flow nodes of a graph spec."""
    from src.agent_platform.plugins.orchestration.graph import normalize_graph
    from src.agent_platform.runtime.agent_compile import compile_agent

    spec = normalize_graph(config, label=str(config.get("name") or "flow"))
    compiled: dict[str, Any] = {}
    for node in spec.nodes:
        if node.kind == "agent":
            merged = _merge_parent(node.agent_spec, config)
            merged["name"] = node.agent_spec.get("name") or node.label
            compiled[node.id] = await compile_agent(merged, ctx, name=str(merged["name"]))
        elif node.kind == "subgraph":
            ref = str(node.raw.get("ref") or "").strip()
            if not ref:
                raise RuntimeError(f"Saved flow '{node.label}' has no definition selected.")
            compiled[node.id] = await resolve_member({"ref": ref}, ctx, config)
    return compiled


async def compile_workflow(config: dict[str, Any], ctx: CompileContext, *, name: str) -> Any:
    pattern = str(config.get("pattern") or "").strip().lower()
    if not pattern:
        pattern = "graph" if (config.get("graph") or config.get("template")) else "graph"

    compiled_nodes: dict[str, Any] = {}
    if pattern in PARTICIPANT_PATTERNS:
        participants = await _compile_participants(config, ctx)
        ctx.participants = participants
    else:
        compiled_nodes = await _compile_graph_nodes(config, ctx)
        participants = [
            runnable for runnable in compiled_nodes.values() if getattr(runnable, "kind", None) == "agent"
        ]
        ctx.participants = participants

    manager_spec = config.get("manager") or config.get("manager_agent")
    if manager_spec:
        manager_cfg = {k: v for k, v in config.items() if k not in {"mcp_bindings", "function_tools"}}
        if isinstance(manager_spec, str) and manager_spec in ctx.agents_by_name:
            ctx.manager_agent = ctx.agents_by_name[manager_spec]
        else:
            ctx.manager_agent = await resolve_member(manager_spec, ctx, manager_cfg)

    spec = dict(config)
    spec["participants"] = participants
    if compiled_nodes:
        spec["compiled"] = compiled_nodes
    if ctx.manager_agent is not None:
        spec["manager_agent"] = ctx.manager_agent

    registry = get_registry()
    plugin = registry.get("supervisor" if pattern == "supervisor" else "swarm" if pattern == "swarm" else "graph")
    if plugin is None or plugin.kind != "orchestration":
        raise RuntimeError(f"Unknown orchestration pattern: {pattern}")
    spec["name"] = name
    graph = plugin.compile(spec, ctx)

    from src.agent_platform.runtime.policies import apply_node_policies

    apply_node_policies(graph, config)
    return graph
