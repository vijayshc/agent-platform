"""Validation for v2 capability fields: middleware, patterns and graph flows.

Split out of :mod:`validate` so each file stays small. Everything here reports
actionable, author-facing messages and never silently repairs a definition.
"""

from __future__ import annotations

from typing import Any

from src.agent_platform.catalog.capabilities import (
    MIDDLEWARE_BY_ID,
    NODE_KINDS_BY_ID,
    PATTERNS,
    PATTERNS_BY_ID,
    RUNTIMES_BY_ID,
    TEMPLATES,
)

AGENT_RUNTIMES = set(RUNTIMES_BY_ID)
WORKFLOW_PATTERNS = {p["id"] for p in PATTERNS} | {"supervisor", "swarm", "graph"}
TEMPLATE_IDS = set(TEMPLATES)


def agent_shaped_configs(config: dict[str, Any]) -> list[dict[str, Any]]:
    """The config plus every nested agent spec (participants, manager, graph nodes)."""
    holders: list[dict[str, Any]] = [config]
    for key in ("manager", "aggregator", "manager_agent"):
        value = config.get(key)
        if isinstance(value, dict):
            holders.append(value)
    for spec in list(config.get("participants") or []) + list(config.get("nodes") or []):
        if isinstance(spec, dict):
            holders.append(spec)
    graph = config.get("graph")
    if isinstance(graph, dict):
        for node in graph.get("nodes") or []:
            if isinstance(node, dict) and isinstance(node.get("agent"), dict):
                holders.append(node["agent"])
    return holders


def validate_middleware(config: dict[str, Any], errors: list[dict[str, str]]) -> None:
    for holder in agent_shaped_configs(config):
        middleware = holder.get("middleware")
        if middleware is None:
            continue
        name = str(holder.get("name") or holder.get("id") or config.get("name") or "agent")
        if middleware == [] or middleware == {}:
            # Definitions saved before the middleware registry stored an empty
            # list here; it means "no middleware".
            continue
        if not isinstance(middleware, dict):
            errors.append(
                {
                    "code": "bad_middleware",
                    "message": (
                        f"middleware for '{name}' must be an object keyed by middleware id "
                        "(for example {\"todo_list\": {}})."
                    ),
                }
            )
            continue
        for middleware_id, values in middleware.items():
            spec = MIDDLEWARE_BY_ID.get(str(middleware_id))
            if spec is None:
                errors.append(
                    {
                        "code": "unknown_middleware",
                        "message": (
                            f"'{middleware_id}' is not a known middleware for '{name}'. "
                            "Available: " + ", ".join(sorted(MIDDLEWARE_BY_ID)) + "."
                        ),
                    }
                )
                continue
            if values is not None and not isinstance(values, dict):
                errors.append(
                    {
                        "code": "bad_middleware",
                        "message": f"middleware '{middleware_id}' for '{name}' must be an object.",
                    }
                )
                continue
            if middleware_id == "human_in_the_loop" and not (values or {}).get("tools"):
                errors.append(
                    {
                        "code": "empty_approval_list",
                        "message": (
                            f"human_in_the_loop for '{name}' needs at least one tool name, "
                            "otherwise the agent pauses before nothing."
                        ),
                    }
                )


#: Spellings that mean a known runtime ("harness" is the pre-v2 name for the
#: deep-agent runtime; the compiler normalises it).
RUNTIME_ALIASES = {"harness": "deep_agent"}


def validate_runtime(config: dict[str, Any], errors: list[dict[str, str]]) -> None:
    for holder in agent_shaped_configs(config):
        runtime = holder.get("runtime")
        if runtime is None:
            continue
        if RUNTIME_ALIASES.get(str(runtime).lower(), str(runtime).lower()) not in AGENT_RUNTIMES:
            errors.append(
                {
                    "code": "unknown_runtime",
                    "message": (
                        f"Unknown agent runtime '{runtime}'. Available: "
                        + ", ".join(sorted(AGENT_RUNTIMES))
                        + "."
                    ),
                }
            )
        spec = holder.get("deep_agent")
        if isinstance(spec, dict) and spec:
            names: list[str] = []
            for sub in spec.get("subagents") or []:
                if not isinstance(sub, dict):
                    continue
                sub_name = str(sub.get("name") or "").strip()
                if not sub_name:
                    errors.append(
                        {"code": "subagent_without_name", "message": "Every subagent needs a name."}
                    )
                elif sub_name in names:
                    errors.append(
                        {
                            "code": "duplicate_subagent",
                            "message": f"Subagent '{sub_name}' is declared twice; names must be unique.",
                        }
                    )
                else:
                    names.append(sub_name)
            if str(holder.get("runtime") or config.get("runtime") or "agent").lower() not in {
                "deep_agent",
                "harness",
            }:
                errors.append(
                    {
                        "code": "deep_agent_options_on_agent",
                        "message": (
                            f"deep_agent options are set on '{holder.get('name') or 'agent'}', "
                            "which runs the plain agent runtime. Switch the runtime to Deep Agent."
                        ),
                    }
                )


def validate_response_format(
    config: dict[str, Any],
    errors: list[dict[str, str]],
    warnings: list[dict[str, str]] | None = None,
) -> None:
    for holder in agent_shaped_configs(config):
        spec = holder.get("response_format")
        if spec is None:
            continue
        name = str(holder.get("name") or holder.get("id") or "agent")
        if not isinstance(spec, dict) or not isinstance(spec.get("schema"), dict):
            errors.append(
                {
                    "code": "bad_response_format",
                    "message": f"response_format for '{name}' needs a JSON schema object.",
                }
            )
            continue
        strategy = str(spec.get("strategy") or "auto").lower()
        if strategy not in {"auto", "tool", "provider"}:
            errors.append(
                {
                    "code": "bad_response_format",
                    "message": f"response_format strategy must be auto, tool or provider (got {strategy}).",
                }
            )
        if strategy in {"auto", "tool"} and warnings is not None:
            warnings.append(
                {
                    "code": "structured_output_forces_tool_call",
                    "message": (
                        f"Structured output for '{name}' uses the '{strategy}' strategy, which "
                        "forces a tool call. Reasoning models (including the default connection) "
                        "reject that; switch to Provider unless you know your model accepts it."
                    ),
                }
            )
        if not str(spec["schema"].get("title") or "").strip():
            # The schema is sent to the provider as a named response format (or
            # converted into a tool); without a title the request is rejected at
            # run time, so it is refused at authoring time instead.
            errors.append(
                {
                    "code": "response_format_without_title",
                    "message": (
                        f"Structured output for '{name}' needs a schema title "
                        '(for example "WorkItems").'
                    ),
                }
            )


def validate_pattern(config: dict[str, Any], kind: str, errors: list[dict[str, str]]) -> str:
    pattern = str(config.get("pattern") or "").strip().lower()
    if kind != "workflow":
        if pattern:
            errors.append(
                {
                    "code": "pattern_on_agent",
                    "message": "A single agent cannot have an orchestration pattern.",
                }
            )
        return ""
    if not pattern:
        pattern = "graph"
    if pattern not in WORKFLOW_PATTERNS:
        errors.append(
            {
                "code": "unknown_pattern",
                "message": (
                    f"Unknown orchestration pattern '{pattern}'. Available: "
                    + ", ".join(sorted(WORKFLOW_PATTERNS))
                    + "."
                ),
            }
        )
    template = str(config.get("template") or "").strip()
    if template and template not in TEMPLATE_IDS:
        errors.append(
            {
                "code": "unknown_template",
                "message": f"Unknown workflow template '{template}'. Available: "
                + ", ".join(sorted(TEMPLATE_IDS))
                + ".",
            }
        )
    return pattern


def _participants_of(config: dict[str, Any]) -> list[dict[str, Any]]:
    return [spec for spec in (config.get("participants") or []) if isinstance(spec, dict)]


def validate_workflow_body(
    config: dict[str, Any], pattern: str, errors: list[dict[str, str]], warnings: list[dict[str, str]]
) -> None:
    if pattern in {"supervisor", "swarm"}:
        participants = _participants_of(config)
        if not participants:
            spec = PATTERNS_BY_ID.get(pattern) or {}
            minimum = int(spec.get("min_agents") or 1)
            label = spec.get("label") or pattern
            errors.append(
                {
                    "code": "no_participants",
                    "message": (
                        f"A {label} needs at least {minimum} participant agent"
                        f"{'' if minimum == 1 else 's'}; connect agents to the "
                        f"{label.lower()} node on the canvas."
                    ),
                }
            )
        for spec in participants:
            if spec.get("ref"):
                continue
            if not str(spec.get("instructions") or "").strip():
                name = spec.get("name") or spec.get("id") or "agent"
                errors.append(
                    {"code": "missing_instructions", "message": f"Participant '{name}' is missing instructions."}
                )
        if pattern == "supervisor" and not (config.get("manager") or config.get("manager_agent")):
            errors.append({"code": "missing_manager", "message": "Supervisor workflow has no manager."})
        manager = config.get("manager")
        if pattern == "supervisor" and isinstance(manager, dict) and manager.get("middleware"):
            warnings.append(
                {
                    "code": "manager_middleware_ignored",
                    "message": (
                        f"'{manager.get('name') or 'The manager'}' configures guardrails, but a "
                        "supervisor's router runs through langgraph-supervisor, which does not "
                        "apply agent middleware. Move them to the specialists."
                    ),
                }
            )
        return

    validate_graph(config, errors, warnings)


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


def validate_capabilities(
    config: dict[str, Any], kind: str, errors: list[dict[str, str]], warnings: list[dict[str, str]]
) -> None:
    """All v2 checks, in one call from :func:`validate_definition`."""
    validate_runtime(config, errors)
    validate_middleware(config, errors)
    validate_response_format(config, errors, warnings)
    pattern = validate_pattern(config, kind, errors)
    if kind == "workflow" and pattern:
        validate_workflow_body(config, pattern, errors, warnings)
    elif kind == "workflow":
        validate_graph(config, errors, warnings)
    _validate_pattern_participants(config, pattern, errors)


def _validate_pattern_participants(
    config: dict[str, Any], pattern: str, errors: list[dict[str, str]]
) -> None:
    """A pattern that needs more agents than the canvas holds is a dead end."""
    if pattern not in PATTERNS_BY_ID:
        return
    minimum = int(PATTERNS_BY_ID[pattern].get("min_agents") or 0)
    if not minimum:
        return
    agents = _count_agents(config) if pattern not in {"supervisor", "swarm"} else len(_participants_of(config))
    if agents and agents < minimum:
        errors.append(
            {
                "code": "too_few_agents",
                "message": (
                    f"{PATTERNS_BY_ID[pattern]['label']} needs at least {minimum} agents "
                    f"(the canvas has {agents})."
                ),
            }
        )


def _count_agents(config: dict[str, Any]) -> int:
    graph = config.get("graph")
    if isinstance(graph, dict) and graph.get("nodes"):
        return len(
            [
                n
                for n in graph["nodes"]
                if isinstance(n, dict) and str(n.get("kind") or "agent") == "agent"
            ]
        )
    return len(_participants_of(config))
