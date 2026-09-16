"""Normalise a definition's declarative graph spec.

The spec is the semantic layer of a workflow: nodes with a kind and edges
between them. ``config["studio"]`` is only the canvas layout and is never read
here. Older definitions stored the graph as a flat list of agent specs; they are
migrated on read (a node without ``kind`` is an agent node).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.agent_platform.catalog.capabilities import materialize_template

END_ALIASES = {"__end__", "end", "END", ""}

NODE_KINDS = {"agent", "router", "tool", "join", "map", "human", "subgraph", "set_state"}


#: Node keys that describe the graph wiring, never the agent behind a node.
#: Everything else on an agent node belongs to its agent spec, which is how the
#: pre-v2 canvas stored nodes (the whole spec lived on the node itself).
GRAPH_ONLY_KEYS = {
    "id", "kind", "label", "position", "agent", "type", "x", "y", "width", "height",
    "selected", "dragging", "routes", "default", "tool", "strategy", "over", "to",
    "message", "ref", "values",
}


@dataclass(frozen=True)
class GraphNode:
    id: str
    kind: str
    label: str
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def agent_spec(self) -> dict[str, Any]:
        """The agent spec of an agent node.

        Nodes written by the current canvas nest the spec under ``agent``; nodes
        written by the pre-v2 canvas carry it on the node itself. Both shapes are
        read, so a saved definition keeps its instructions, tools and guardrails.
        """
        nested = self.raw.get("agent")
        spec = dict(nested) if isinstance(nested, dict) else {}
        for key, value in self.raw.items():
            if key in GRAPH_ONLY_KEYS:
                continue
            spec.setdefault(key, value)
        spec.setdefault("name", self.label or self.id)
        return spec


@dataclass(frozen=True)
class GraphEdge:
    source: str
    target: str
    when: str | None = None
    send_over: str | None = None


@dataclass(frozen=True)
class GraphSpec:
    nodes: list[GraphNode]
    edges: list[GraphEdge]
    entry: str
    label: str = "flow"
    #: True when the spec declared no edges at all: every node then runs from
    #: START in parallel, which is what a canvas full of unconnected agents has
    #: always meant (and what the pre-v2 graph plugin compiled).
    start_all: bool = False

    @property
    def by_id(self) -> dict[str, GraphNode]:
        return {n.id: n for n in self.nodes}

    def outgoing(self, node_id: str) -> list[GraphEdge]:
        return [e for e in self.edges if e.source == node_id]

    def incoming(self, node_id: str) -> list[GraphEdge]:
        return [e for e in self.edges if e.target == node_id]


def _node_from_raw(raw: dict[str, Any], index: int) -> GraphNode:
    if not isinstance(raw, dict):
        raise RuntimeError(f"Graph node #{index + 1} is not an object")
    node_id = str(raw.get("id") or raw.get("name") or f"n{index + 1}")
    kind = str(raw.get("kind") or ("agent" if (raw.get("instructions") or raw.get("agent")) else "agent"))
    if kind not in NODE_KINDS:
        raise RuntimeError(f"Graph node '{node_id}' has unknown kind '{kind}'")
    label = str(raw.get("label") or raw.get("name") or node_id)
    return GraphNode(id=node_id, kind=kind, label=label, raw=dict(raw))


def _edge_from_raw(raw: dict[str, Any]) -> GraphEdge | None:
    if not isinstance(raw, dict):
        return None
    source = str(raw.get("from") or raw.get("source") or "")
    target = str(raw.get("to") or raw.get("target") or "")
    if not source:
        return None
    send_over = None
    send_to = None
    mapping = raw.get("map")
    if isinstance(mapping, dict):
        send_over = str(mapping.get("over") or "") or None
        send_to = str(mapping.get("to") or "") or None
    if not target and send_to:
        target = send_to
    if not target:
        return None
    return GraphEdge(source=source, target=target, when=raw.get("when"), send_over=send_over)


def _from_participants(config: dict[str, Any]) -> dict[str, Any]:
    """Migrate a participant list into graph nodes/edges (sequential by default)."""
    participants = list(config.get("participants") or [])
    nodes: list[dict[str, Any]] = []
    for index, spec in enumerate(participants):
        if not isinstance(spec, dict):
            spec = {"name": str(spec)}
        node = {"id": str(spec.get("id") or spec.get("name") or f"n{index + 1}"),
                "kind": "agent", "label": str(spec.get("name") or f"Agent {index + 1}"),
                "agent": dict(spec)}
        nodes.append(node)
    edges = [
        {"from": nodes[i]["id"], "to": nodes[i + 1]["id"]}
        for i in range(len(nodes) - 1)
    ]
    return {"entry": nodes[0]["id"] if nodes else "", "nodes": nodes, "edges": edges}


def raw_graph(config: dict[str, Any]) -> dict[str, Any]:
    graph = config.get("graph")
    if isinstance(graph, dict) and (graph.get("nodes") or graph.get("entry")):
        return graph
    template = str(config.get("template") or "").strip()
    if template and template != "custom":
        try:
            materialized = materialize_template(template)
        except KeyError:
            materialized = {"nodes": [], "edges": [], "entry": ""}
        if materialized["nodes"]:
            return materialized
    nodes = list(config.get("nodes") or [])
    if nodes:
        return {"entry": config.get("entry") or "", "nodes": nodes, "edges": config.get("edges") or []}
    if config.get("participants"):
        return _from_participants(config)
    return {"entry": "", "nodes": [], "edges": []}


def normalize_graph(config: dict[str, Any], *, label: str = "flow") -> GraphSpec:
    graph = raw_graph(config)
    nodes = [_node_from_raw(raw, i) for i, raw in enumerate(graph.get("nodes") or [])]
    if not nodes:
        raise RuntimeError("This workflow has no nodes yet. Add an agent to the canvas.")

    known = {n.id for n in nodes}
    edges: list[GraphEdge] = []
    for raw in graph.get("edges") or []:
        edge = _edge_from_raw(raw)
        if edge is None:
            continue
        if edge.source not in known:
            raise RuntimeError(f"Edge starts at unknown node '{edge.source}'")
        if edge.target not in known and edge.target not in END_ALIASES:
            raise RuntimeError(f"Edge points at unknown node '{edge.target}'")
        edges.append(edge)

    entry = str(graph.get("entry") or "").strip()
    if entry not in known:
        entry = _default_entry(nodes, edges)
    start_all = not edges and len(nodes) > 1
    return GraphSpec(nodes=nodes, edges=edges, entry=entry, label=label, start_all=start_all)


def _default_entry(nodes: list[GraphNode], edges: list[GraphEdge]) -> str:
    """The only node nothing points at; the first node when that is ambiguous."""
    targets = {e.target for e in edges}
    roots = [n.id for n in nodes if n.id not in targets]
    return roots[0] if roots else nodes[0].id


def route_target(route_entry: Any) -> str | None:
    """The node a router route points at (``__end__`` means END)."""
    if isinstance(route_entry, dict):
        return str(route_entry.get("to") or "") or None
    if isinstance(route_entry, str):
        return route_entry or None
    return None
