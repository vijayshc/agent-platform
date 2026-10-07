"""Walking nested agent configurations for validation.

Two gates need two different views of a definition: model-spec checks follow
Studio canvas nodes, while capability/middleware checks follow declarative graph
nodes. Only the shared prefix lives here, so the two walks cannot drift apart
into one over-broad traversal again.
"""

from __future__ import annotations

from typing import Any


def _agent_holders(config: dict[str, Any]) -> list[dict[str, Any]]:
    """The config plus its manager/aggregator/participants/nodes holders."""
    holders: list[dict[str, Any]] = [config]
    for key in ("manager", "aggregator", "manager_agent"):
        value = config.get(key)
        if isinstance(value, dict):
            holders.append(value)
    for spec in list(config.get("participants") or []) + list(config.get("nodes") or []):
        if isinstance(spec, dict):
            holders.append(spec)
    return holders


def iter_agent_configs(config: dict[str, Any]) -> list[dict[str, Any]]:
    """Agent-shaped configs: the config, its managers/participants, and graph nodes."""
    if not isinstance(config, dict):
        return []
    holders = _agent_holders(config)
    graph = config.get("graph")
    if isinstance(graph, dict):
        for node in graph.get("nodes") or []:
            if isinstance(node, dict) and isinstance(node.get("agent"), dict):
                holders.append(node["agent"])
    return holders


def iter_model_specs(config: dict[str, Any]) -> list[dict[str, Any]]:
    """Every model spec in the tree: agent specs plus Studio canvas node data."""
    if not isinstance(config, dict):
        return []
    holders = _agent_holders(config)
    studio = config.get("studio")
    if isinstance(studio, dict):
        for node in studio.get("nodes") or []:
            if isinstance(node, dict) and isinstance(node.get("data"), dict):
                holders.append(node["data"])
    specs: list[dict[str, Any]] = []
    seen: set[int] = set()
    for holder in holders:
        if id(holder) in seen:
            continue
        seen.add(id(holder))
        model = holder.get("model")
        if isinstance(model, dict):
            specs.append(model)
        elif holder.get("client") or holder.get("modelClient"):
            specs.append(holder)
    return specs
