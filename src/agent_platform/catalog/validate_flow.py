"""Validation for v2 capability fields: middleware, patterns and graph flows.

Split out of :mod:`validate` so each file stays small. Everything here reports
actionable, author-facing messages and never silently repairs a definition.
"""

from __future__ import annotations

from typing import Any

from src.agent_platform.catalog.capabilities import (
    MIDDLEWARE_BY_ID,
    PATTERNS,
    PATTERNS_BY_ID,
    RUNTIMES_BY_ID,
    TEMPLATES,
)

AGENT_RUNTIMES = set(RUNTIMES_BY_ID)
WORKFLOW_PATTERNS = {p["id"] for p in PATTERNS} | {"supervisor", "swarm", "graph"}
TEMPLATE_IDS = set(TEMPLATES)


from src.agent_platform.catalog.traversal import iter_agent_configs
from src.agent_platform.catalog.validate_graph_topology import validate_graph

#: Every nested agent-shaped config, walked by the shared traversal.
agent_shaped_configs = iter_agent_configs


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
