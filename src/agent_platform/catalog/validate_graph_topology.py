"""Validation for graph flow topologies: cycles, reachability, fanout, routes.

Extracted from validate_flow.py to keep graph theory analysis separate from
runtime and capability checks.
"""

from __future__ import annotations

from typing import Any

from src.agent_platform.catalog.capabilities import NODE_KINDS_BY_ID


def validate_graph(
    config: dict[str, Any], errors: list[dict[str, str]], warnings: list[dict[str, str]]
) -> None:
    """Check the declarative graph spec (nodes, edges, router routes, refs)."""
    from src.agent_platform.plugins.orchestration.graph import END_ALIASES, normalize_graph

    try:
        spec = normalize_graph(config, label=str(config.get("name") or "flow"))
    except Exception as exc:
        errors.append({"code": "bad_graph", "message": str(exc)})
        return

    for node in spec.nodes:
        kind_spec = NODE_KINDS_BY_ID.get(node.kind)
        if kind_spec is None:
            errors.append(
                {"code": "unknown_node_kind", "message": f"Node '{node.label}' has unknown kind '{node.kind}'."}
            )
            continue
        if node.kind == "agent":
            instructions = str(node.agent_spec.get("instructions") or "").strip()
            if not instructions:
                errors.append(
                    {
                        "code": "missing_instructions",
                        "message": f"Agent node '{node.label}' is missing instructions.",
                    }
                )
        if node.kind == "router":
            routes = [r for r in (node.raw.get("routes") or []) if isinstance(r, dict)]
            if not routes:
                errors.append(
                    {"code": "router_without_routes", "message": f"Router '{node.label}' has no routes."}
                )
            if _router_loops_back(spec, node) and not node.raw.get("max_visits"):
                warnings.append(
                    {
                        "code": "router_loop_without_limit",
                        "message": (
                            f"'{node.label}' routes back into the flow it came from and has no "
                            "Max passes limit, so it can only stop when the graph's step budget "
                            "runs out. Set Max passes (for example 3)."
                        ),
                    }
                )
            fallback = str(node.raw.get("default") or "").strip()
            if fallback and fallback not in spec.by_id and fallback not in END_ALIASES:
                errors.append(
                    {
                        "code": "route_default_missing",
                        "message": (
                            f"The fallback route of '{node.label}' points at '{fallback}', "
                            "which is not on the canvas."
                        ),
                    }
                )
            for route in routes:
                target = str(route.get("to") or "").strip()
                if target and target not in spec.by_id and target not in END_ALIASES:
                    errors.append(
                        {
                            "code": "route_target_missing",
                            "message": (
                                f"Route '{route.get('name')}' of '{node.label}' points at "
                                f"'{target}', which is not on the canvas."
                            ),
                        }
                    )
        if node.kind == "map":
            target = str(node.raw.get("to") or "").strip()
            if target not in spec.by_id:
                errors.append(
                    {
                        "code": "fanout_target_missing",
                        "message": f"Fan-out '{node.label}' needs a worker node.",
                    }
                )
            over = str(node.raw.get("over") or "").strip()
            if not over:
                errors.append(
                    {"code": "fanout_without_list", "message": f"Fan-out '{node.label}' needs a list field."}
                )
            elif not _producer_declares(spec, node.id, over):
                errors.append(
                    {
                        "code": "fanout_without_producer",
                        "message": (
                            f"Fan-out '{node.label}' reads '{over}', but the agent before it does "
                            f"not produce a '{over}' list. Give that agent a structured output "
                            f"whose schema declares '{over}'."
                        ),
                    }
                )
        if node.kind == "tool":
            tool_name = str(node.raw.get("tool") or "").strip()
            if not tool_name:
                errors.append({"code": "tool_node_without_tool", "message": f"Tool node '{node.label}' has no tool."})
            else:
                from src.agent_platform.plugins.tools.builtins import get_function_tool

                if get_function_tool(tool_name) is None:
                    errors.append(
                        {
                            "code": "unknown_tool",
                            "message": f"Tool node '{node.label}' references unknown tool '{tool_name}'.",
                        }
                    )
        if node.kind == "join" and not spec.incoming(node.id):
            warnings.append(
                {
                    "code": "join_nothing_to_combine",
                    "message": (
                        f"'{node.label}' combines parallel branches but nothing leads into it, so "
                        "it has nothing to merge."
                    ),
                }
            )
        if node.kind == "subgraph" and not str(node.raw.get("ref") or "").strip():
            errors.append(
                {"code": "subgraph_without_ref", "message": f"Saved flow '{node.label}' has no definition selected."}
            )

    if spec.entry not in spec.by_id:
        errors.append({"code": "missing_entry", "message": "The flow has no valid starting node."})

    reachable = _reachable(spec)
    orphans = [n for n in spec.nodes if n.id not in reachable]
    if orphans:
        warnings.append(
            {
                "code": "disconnected_nodes",
                "message": "Not reachable from the start node: "
                + ", ".join(n.label for n in orphans[:6])
                + ".",
            }
        )

    if not spec.start_all and not any(e.source == spec.entry for e in spec.edges) and len(spec.nodes) > 1:
        warnings.append(
            {"code": "entry_without_edges", "message": "The start node has no outgoing edge."}
        )


def _producer_declares(spec: Any, node_id: str, field: str) -> bool:
    """Whether an upstream agent's structured output declares ``field``."""
    for edge in spec.incoming(node_id):
        producer = spec.by_id.get(edge.source)
        if producer is None or producer.kind != "agent":
            continue
        schema = ((producer.raw.get("agent") or producer.raw).get("response_format") or {}).get("schema")
        if isinstance(schema, dict) and field in (schema.get("properties") or {}):
            return True
    return False


def _router_loops_back(spec: Any, node: Any) -> bool:
    """Whether following a router's targets can return to a node it already ran after."""
    targets = [
        str(route.get("to") or "")
        for route in (node.raw.get("routes") or [])
        if isinstance(route, dict)
    ]
    if not targets:
        return False
    # A router loops when one of its destinations can reach it again.
    adjacency: dict[str, list[str]] = {n.id: [] for n in spec.nodes}
    for edge in spec.edges:
        adjacency.setdefault(edge.source, []).append(edge.target)
    for other in spec.nodes:
        if other.kind == "router":
            for route in other.raw.get("routes") or []:
                if isinstance(route, dict) and str(route.get("to")) in adjacency:
                    adjacency[other.id].append(str(route["to"]))
    seen: set[str] = set()
    queue = list(targets)
    while queue:
        current = queue.pop()
        if current == node.id:
            return True
        if current in seen or current not in adjacency:
            continue
        seen.add(current)
        queue.extend(adjacency.get(current, []))
    return False


def _reachable(spec: Any) -> set[str]:
    if getattr(spec, "start_all", False):
        return {node.id for node in spec.nodes}
    targets = {e.target for e in spec.edges}
    adjacency: dict[str, list[str]] = {n.id: [] for n in spec.nodes}
    for edge in spec.edges:
        adjacency.setdefault(edge.source, []).append(edge.target)
    for node in spec.nodes:
        if node.kind == "router":
            for route in node.raw.get("routes") or []:
                if isinstance(route, dict) and route.get("to") in adjacency:
                    adjacency[node.id].append(str(route["to"]))
        if node.kind == "map":
            # A fan-out reaches its worker through Send, not through an edge.
            target = str(node.raw.get("to") or "")
            if target in adjacency:
                adjacency[node.id].append(target)
    seen: set[str] = set()
    queue = [spec.entry]
    while queue:
        current = queue.pop()
        for nxt in adjacency.get(current, []):
            if nxt in seen or nxt not in adjacency:
                continue
            seen.add(nxt)
            queue.append(nxt)
    return seen | ({spec.entry} if spec.entry in adjacency else set()) | targets
