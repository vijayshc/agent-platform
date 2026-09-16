"""Declarative LangGraph workflows.

``GraphPlugin`` compiles ``config["graph"]`` (nodes + edges) into a StateGraph.
The patterns documented under "Workflows and agents" are wired from these
building blocks rather than implemented one by one; see ``spec.TEMPLATES`` for the
shipped blueprints.
"""

from __future__ import annotations

from src.agent_platform.plugins.orchestration.graph.compiler import GraphPlugin, compile_graph
from src.agent_platform.plugins.orchestration.graph.spec import (
    END_ALIASES,
    GraphEdge,
    GraphNode,
    GraphSpec,
    normalize_graph,
)

__all__ = [
    "END_ALIASES",
    "GraphEdge",
    "GraphNode",
    "GraphPlugin",
    "GraphSpec",
    "compile_graph",
    "normalize_graph",
]
