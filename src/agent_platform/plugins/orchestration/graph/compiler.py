"""Compile a declarative graph spec into a LangGraph StateGraph.

Everything here is composition of shipped primitives: ``StateGraph``,
``add_edge``, ``START``/``END`` and the ``Command``/``Send`` routers that node
builders return. No workflow pattern is hand-written: prompt chaining,
parallelization, routing, orchestrator-worker and evaluator-optimizer all fall
out of how the author wires nodes and edges.
"""

from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph

from src.agent_platform.plugins.orchestration.graph.nodes import build_node
from src.agent_platform.plugins.orchestration.graph.spec import GraphSpec, normalize_graph
from src.agent_platform.runtime.langgraph_adapter import PlatformState

#: Node kinds that choose their own destination (Command): never given a static
#: outgoing edge, and never auto-terminated.
COMMAND_KINDS = {"router", "map"}


def compile_graph(
    config: dict[str, Any],
    ctx: Any,
    *,
    compiled: dict[str, Any] | None = None,
    name: str | None = None,
) -> Any:
    graph_spec = normalize_graph(config, label=str(config.get("name") or name or "flow"))
    compiled = dict(compiled or {})

    builder = StateGraph(PlatformState)
    for node in graph_spec.nodes:
        builder.add_node(node.id, build_node(node, graph_spec, ctx, compiled, config))

    if graph_spec.start_all:
        # No edges declared: every node is a root and they run concurrently.
        for node in graph_spec.nodes:
            builder.add_edge(START, node.id)
    else:
        builder.add_edge(START, graph_spec.entry)

    for edge in graph_spec.edges:
        source = graph_spec.by_id.get(edge.source)
        if source is None:
            continue
        if edge.source in _command_nodes(graph_spec):
            # Routers and fan-outs already jump with Command(goto=...); a static
            # edge from them would let the graph continue down both branches.
            continue
        builder.add_edge(edge.source, edge.target)

    command_nodes = _command_nodes(graph_spec)
    for node in graph_spec.nodes:
        if node.id in command_nodes:
            continue
        if not graph_spec.outgoing(node.id) and not graph_spec.start_all:
            builder.add_edge(node.id, END)

    graph = builder.compile(
        checkpointer=getattr(ctx, "checkpoint_storage", None),
        store=getattr(ctx, "store", None),
        name=name or graph_spec.label,
    )
    graph.kind = "workflow"
    graph.name = name or graph_spec.label
    graph.flow_spec = graph_spec
    # Control-plane nodes of nested flows are control-plane for this run too: a
    # router inside a reused "saved flow" node must not stream its decision JSON
    # into the parent's transcript. Subgraphs already carry their own set, so
    # nesting any number of levels deep propagates.
    silent = set(command_nodes)
    for node in graph_spec.nodes:
        silent |= set(getattr(compiled.get(node.id), "silent_nodes", None) or ())
    graph.silent_nodes = silent
    return graph


def _command_nodes(graph_spec: GraphSpec) -> set[str]:
    return {n.id for n in graph_spec.nodes if n.kind in COMMAND_KINDS}


class GraphPlugin:
    """Plugin entry point: the runtime hands over compiled agent nodes."""

    type_id = "graph"
    kind = "orchestration"
    label = "Graph"
    icon = "workflow"
    schema = {
        "type": "object",
        "properties": {
            "graph": {"type": "object"},
            "template": {"type": "string"},
        },
    }

    def compile(self, spec: dict[str, Any], ctx: Any) -> Any:
        compiled = dict(spec.get("compiled") or {})
        config = spec.get("config") if isinstance(spec.get("config"), dict) else spec
        return compile_graph(
            config,
            ctx,
            compiled=compiled,
            name=str(spec.get("name") or config.get("name") or "flow"),
        )
