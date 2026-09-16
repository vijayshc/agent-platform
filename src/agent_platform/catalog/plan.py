"""Static compile plan: which shipped builder a definition will use.

No model calls, no MCP sessions, no graph construction — the plan is derived from
the same registry the editor renders, so the Studio can show the author exactly
what will run ("this is ``create_supervisor`` with two ``create_agent``
specialists, each with ``SummarizationMiddleware``").
"""

from __future__ import annotations

from typing import Any

from src.agent_platform.catalog.capabilities import (
    MIDDLEWARE_BY_ID,
    NODE_KINDS_BY_ID,
    PATTERNS_BY_ID,
    RUNTIMES_BY_ID,
    pattern_for_template,
)
from src.agent_platform.runtime.agent_compile import (
    effective_middleware_config,
    normalize_runtime,
    response_format_for,
)

WORKFLOW_BUILDERS = {
    "supervisor": "langgraph_supervisor.create_supervisor",
    "swarm": "langgraph_swarm.create_swarm",
    "graph": "langgraph.graph.StateGraph",
}


def _middleware_names(config: dict[str, Any]) -> list[str]:
    names: list[str] = []
    for middleware_id in effective_middleware_config(config):
        spec = MIDDLEWARE_BY_ID.get(middleware_id)
        names.append(str(spec["class"]).rsplit(".", 1)[-1] if spec else middleware_id)
    return names


def _tool_count(config: dict[str, Any]) -> int:
    bindings = list(config.get("mcp_bindings") or [])
    declared = sum(len(b.get("tools") or []) for b in bindings if isinstance(b, dict))
    mcp_servers = len({b.get("server_id") or b.get("server") for b in bindings if isinstance(b, dict)})
    return len(config.get("function_tools") or []) + len(config.get("maf_skill_ids") or []) + declared + mcp_servers


def agent_plan(config: dict[str, Any], name: str) -> dict[str, Any]:
    runtime = normalize_runtime(config)
    runtime_spec = RUNTIMES_BY_ID.get(runtime) or RUNTIMES_BY_ID["agent"]
    plan: dict[str, Any] = {
        "name": name,
        "builder": runtime_spec["builder"],
        "runtime": runtime,
        "docs": runtime_spec["docs"],
        "tools": _tool_count(config),
        "middleware": _middleware_names(config),
    }
    if config.get("response_format"):
        try:
            strategy = type(response_format_for(config)).__name__
        except Exception:
            strategy = "invalid"
        plan["structured_output"] = strategy
    if runtime == "deep_agent":
        spec = dict(config.get("deep_agent") or {})
        plan["subagents"] = [
            str(s.get("name"))
            for s in (spec.get("subagents") or [])
            if isinstance(s, dict) and s.get("name")
        ]
        plan["skills"] = list(spec.get("skills") or [])
        plan["memory"] = list(spec.get("memory") or [])
        plan["permissions"] = len(spec.get("permissions") or [])
    return plan


def _graph_nodes(config: dict[str, Any]) -> list[dict[str, Any]]:
    from src.agent_platform.plugins.orchestration.graph import normalize_graph

    try:
        spec = normalize_graph(config, label=str(config.get("name") or "flow"))
    except Exception as exc:
        return [{"error": str(exc)}]
    nodes: list[dict[str, Any]] = []
    for node in spec.nodes:
        kind = NODE_KINDS_BY_ID.get(node.kind) or {"label": node.kind, "builder": ""}
        entry: dict[str, Any] = {
            "id": node.id,
            "kind": node.kind,
            "label": node.label,
            "builder": kind["builder"],
        }
        if node.kind == "agent":
            entry["agent"] = agent_plan(node.agent_spec, node.label)
        nodes.append(entry)
    return nodes


def compile_plan(definition: dict[str, Any]) -> dict[str, Any]:
    config = dict(definition.get("config") or definition)
    kind = str(definition.get("kind") or config.get("kind") or "agent").lower()
    name = str(definition.get("name") or config.get("name") or "agent")

    if kind != "workflow":
        return {"kind": "agent", "plan": agent_plan(config, name)}

    pattern_id = str(config.get("pattern") or "graph").lower()
    if pattern_id not in WORKFLOW_BUILDERS:
        pattern = pattern_for_template(str(config.get("template") or "custom"))
        pattern_id = pattern["id"] if pattern else "graph"
    pattern_spec = PATTERNS_BY_ID.get(pattern_id) or {}

    if pattern_id in {"supervisor", "swarm"}:
        participants = [
            agent_plan(spec, str(spec.get("name") or f"Agent {i + 1}"))
            for i, spec in enumerate(config.get("participants") or [])
            if isinstance(spec, dict)
        ]
        plan: dict[str, Any] = {
            "name": name,
            "builder": WORKFLOW_BUILDERS[pattern_id],
            "docs": pattern_spec.get("docs"),
            "participants": participants,
        }
        manager = config.get("manager")
        if isinstance(manager, dict) and pattern_id == "supervisor":
            plan["manager"] = agent_plan(manager, str(manager.get("name") or "Supervisor"))
        if config.get("start_agent"):
            plan["start_agent"] = config["start_agent"]
        if config.get("handoffs"):
            plan["handoffs"] = config["handoffs"]
        return {"kind": "workflow", "pattern": pattern_id, "plan": plan}

    nodes = _graph_nodes(config)
    return {
        "kind": "workflow",
        "pattern": pattern_id,
        "template": config.get("template") or "custom",
        "plan": {
            "name": name,
            "builder": WORKFLOW_BUILDERS["graph"],
            "docs": pattern_spec.get("docs"),
            "nodes": nodes,
            "entry": (config.get("graph") or {}).get("entry") or "",
        },
    }
