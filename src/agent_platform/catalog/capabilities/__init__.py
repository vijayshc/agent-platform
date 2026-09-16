"""Capability registry: everything the Agent Studio can build, from the libraries.

The frontend renders its palette, pattern gallery and inspector from
``studio_catalog()``; the backend compiles from the same ids. Adding a shipped
capability therefore touches exactly one place.
"""

from __future__ import annotations

from typing import Any

from src.agent_platform.catalog.capabilities.agents import (
    NODE_KINDS,
    NODE_KINDS_BY_ID,
    PATTERNS,
    PATTERNS_BY_ID,
    RUNTIMES,
    RUNTIMES_BY_ID,
    TEMPLATES,
)
from src.agent_platform.catalog.capabilities.middleware import (
    MIDDLEWARE,
    MIDDLEWARE_BY_ID,
)

__all__ = [
    "MIDDLEWARE",
    "MIDDLEWARE_BY_ID",
    "NODE_KINDS",
    "NODE_KINDS_BY_ID",
    "PATTERNS",
    "PATTERNS_BY_ID",
    "RUNTIMES",
    "RUNTIMES_BY_ID",
    "TEMPLATES",
    "materialize_template",
    "pattern_for_template",
    "studio_catalog",
]

#: Patterns whose participants come from the canvas rather than a graph spec.
PARTICIPANT_PATTERNS = {"supervisor", "swarm"}


def _middleware_entries() -> list[dict[str, Any]]:
    """Middleware as the editor sees it: ``builder`` is the class it stands for."""
    return [
        {**entry, "builder": entry.get("builder") or entry["class"]}
        for entry in MIDDLEWARE
    ]


def studio_catalog() -> dict[str, Any]:
    """The full capability catalog served to the editor."""
    return {
        "schema": 2,
        "docs": {
            "workflows": "https://docs.langchain.com/oss/python/langgraph/workflows-agents",
            "middleware": "https://docs.langchain.com/oss/python/langchain/middleware",
            "graph_api": "https://docs.langchain.com/oss/python/langgraph/graph-api",
        },
        "runtimes": RUNTIMES,
        "patterns": PATTERNS,
        "node_kinds": NODE_KINDS,
        "middleware": _middleware_entries(),
        "templates": TEMPLATES,
    }


def pattern_for_template(template_id: str) -> dict[str, Any] | None:
    for pattern in PATTERNS:
        if pattern["template"] == template_id:
            return pattern
    return None


def materialize_template(template_id: str) -> dict[str, Any]:
    """Turn a template blueprint into a concrete ``config.graph`` spec.

    Node keys become stable ids (``<key>`` → ``<key>``), edges are rewritten to
    those ids, and every agent node gets a name derived from its label. Used by
    API clients that submit ``template`` without a canvas, and by the editor when
    a pattern is dropped on the canvas.
    """
    template = TEMPLATES.get(template_id)
    if template is None:
        raise KeyError(f"Unknown template: {template_id}")

    nodes: list[dict[str, Any]] = []
    for raw in template.get("nodes") or []:
        node = {k: v for k, v in raw.items() if k != "key"}
        node["id"] = str(raw.get("key") or f"n{len(nodes) + 1}")
        node["label"] = str(node.get("label") or node["id"])
        if node.get("kind") == "agent":
            agent = dict(node.get("agent") or {})
            agent.setdefault("name", node["label"])
            agent.setdefault("runtime", "agent")
            node["agent"] = agent
        nodes.append(node)

    edges: list[dict[str, Any]] = []
    for raw in template.get("edges") or []:
        edge = {"from": str(raw.get("from")), "to": str(raw.get("to"))}
        if raw.get("when"):
            edge["when"] = raw["when"]
        if isinstance(raw.get("map"), dict):
            edge["map"] = dict(raw["map"])
        edges.append(edge)

    return {"entry": template.get("entry") or (nodes[0]["id"] if nodes else ""), "nodes": nodes, "edges": edges}
