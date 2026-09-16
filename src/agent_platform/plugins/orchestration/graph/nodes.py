"""Runnables for the non-agent graph node kinds.

Each builder returns a plain callable (or a LangGraph prebuilt) wired from
out-of-the-box primitives: ``Command`` for routing, ``Send`` for map fan-out,
``ToolNode`` for tool steps and ``interrupt`` for human review.
"""

from __future__ import annotations

from typing import Any, Callable

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langgraph.graph import END
from langgraph.prebuilt import ToolNode
from langgraph.types import Command, Send, interrupt

from src.agent_platform.plugins.orchestration.graph.spec import (
    END_ALIASES,
    GraphNode,
    GraphSpec,
)
from src.agent_platform.runtime.langgraph_adapter import PlatformState

_ROUTER_SYSTEM = (
    "You route a request to exactly one destination. Choose the single best match "
    "and answer with the route name only."
)


def _target(node_id: str) -> Any:
    return END if node_id in END_ALIASES else node_id


def _state_messages(state: Any) -> list[BaseMessage]:
    messages = (state or {}).get("messages") if isinstance(state, dict) else None
    return list(messages or [])


def _node_model(node: GraphNode, ctx: Any, config: dict[str, Any]) -> Any:
    """The model a non-agent node runs on.

    A node may pin its own connection; otherwise it uses the workflow's model
    spec, which itself falls back to the caller's run-level model and then to
    the operator's default connection (``model_select.compile_model_client``).
    """
    from src.agent_platform.runtime.model_select import compile_model_client

    spec = dict(config.get("model") or {})
    if isinstance(node.raw.get("model"), dict):
        spec.update({k: v for k, v in node.raw["model"].items() if v is not None})
    model = compile_model_client(spec, ctx)
    if model is None:
        raise RuntimeError(f"Node '{node.label}' needs a model but the run has none.")
    return model


def build_router(node: GraphNode, spec: GraphSpec, ctx: Any, config: dict[str, Any]) -> Callable[..., Any]:
    routes = [dict(r) for r in (node.raw.get("routes") or []) if isinstance(r, dict)]
    names = [str(r.get("name") or "").strip() for r in routes if str(r.get("name") or "").strip()]
    if not names:
        raise RuntimeError(f"Router '{node.label}' has no routes.")
    destinations = {
        str(r.get("name")).strip(): str(r.get("to") or "").strip()
        for r in routes
        if str(r.get("name") or "").strip()
    }
    fallback = str(node.raw.get("default") or "").strip() or destinations[names[0]]
    # A routing loop (evaluator-optimizer and friends) needs a bound: without one
    # the graph only stops when LangGraph's recursion limit aborts the run, which
    # surfaces to the user as an error instead of an answer. ``max_visits`` makes
    # the exit explicit — the first route is the "done" branch.
    max_visits = node.raw.get("max_visits")
    try:
        max_visits = int(max_visits) if max_visits not in (None, "") else None
    except (TypeError, ValueError):
        max_visits = None
    model = _node_model(node, ctx, config)
    structured = model.with_structured_output(
        {
            "title": "route_decision",
            "type": "object",
            "properties": {"route": {"type": "string", "enum": names}},
            "required": ["route"],
        }
    )
    catalogue = "\n".join(
        f"- {r.get('name')}: {r.get('description') or r.get('name')}" for r in routes
    )

    async def route(state: PlatformState) -> Command:
        messages = _state_messages(state)
        prompt = f"{_ROUTER_SYSTEM}\n\nRoutes:\n{catalogue}"
        decision = await structured.ainvoke([SystemMessage(content=prompt), *messages[-12:]])
        picked = ""
        if isinstance(decision, dict):
            picked = str(decision.get("route") or "")
        else:
            picked = str(getattr(decision, "route", "") or "")
        visits = dict((state.get("flow") or {}).get("router_visits") or {})
        count = int(visits.get(node.id, 0)) + 1
        visits[node.id] = count
        exhausted = max_visits is not None and count > max_visits
        if exhausted:
            picked = names[0]
        target = destinations.get(picked) or fallback
        if not target:
            raise RuntimeError(f"Router '{node.label}' could not pick a destination.")
        # The decision is control-plane information: it is reported as a step,
        # never as assistant text (the graph marks routers as silent nodes).
        from langgraph.config import get_stream_writer

        writer = get_stream_writer()
        if writer is not None:
            label = next(
                (str(r.get("name")) for r in routes if str(r.get("name")) == picked), picked or fallback
            )
            suffix = f" (limit {max_visits} reached)" if exhausted else ""
            writer({"type": "progress", "message": f"{node.label} → {label}{suffix}"})
        return Command(
            goto=_target(target),
            update={"flow": {"route": picked or fallback, "router_visits": visits}},
        )

    route.__name__ = f"route_{node.id}"
    return route


def build_tool(node: GraphNode, spec: GraphSpec, ctx: Any, config: dict[str, Any]) -> Any:
    name = str(node.raw.get("tool") or node.label).strip()
    from src.agent_platform.plugins.tools.builtins import get_function_tool

    found = get_function_tool(name)
    if found is None:
        for candidate in getattr(ctx, "extra_function_tools", None) or []:
            if getattr(candidate, "name", None) == name:
                found = candidate
                break
    if found is None:
        raise RuntimeError(f"Node '{node.label}' references unknown tool '{name}'.")
    return ToolNode([found])


def build_join(node: GraphNode, spec: GraphSpec, ctx: Any, config: dict[str, Any]) -> Callable[..., Any]:
    strategy = str(node.raw.get("strategy") or "summarize").lower()

    async def combine(state: PlatformState) -> dict[str, Any]:
        messages = _state_messages(state)
        answers = [m for m in messages if isinstance(m, AIMessage) and str(m.content or "").strip()]
        if strategy == "last" or not answers:
            return {}
        if strategy == "concat":
            joined = "\n\n".join(str(m.content) for m in answers)
            return {"messages": [AIMessage(content=joined, name=node.label)]}
        model = _node_model(node, ctx, config)
        transcript = "\n\n".join(f"- {m.content}" for m in answers[-12:])
        response = await model.ainvoke(
            [
                SystemMessage(
                    content=(
                        f"You are the '{node.label}' step. Merge the answers below into one "
                        "coherent, complete answer. Keep every fact; remove duplication."
                    )
                ),
                HumanMessage(content=transcript),
            ]
        )
        return {"messages": [response]}

    combine.__name__ = f"join_{node.id}"
    return combine


def build_map(node: GraphNode, spec: GraphSpec, ctx: Any, config: dict[str, Any]) -> Callable[..., Any]:
    over = str(node.raw.get("over") or "items").strip()
    target = str(node.raw.get("to") or "").strip()
    if target not in spec.by_id:
        raise RuntimeError(f"Fan-out '{node.label}' points at unknown node '{target}'.")

    async def fan(state: PlatformState) -> Command:
        structured = state.get("structured_response") if isinstance(state, dict) else None
        raw = structured.get(over) if isinstance(structured, dict) else None
        if not isinstance(raw, list):
            raise RuntimeError(
                f"Fan-out '{node.label}' expected a list under '{over}' in the upstream "
                f"agent's structured output, but got {type(raw).__name__}. Give the upstream "
                f"agent a structured output whose schema declares '{over}', or point the "
                "fan-out at the right field."
            )
        if not raw:
            return Command(goto=END, update={"flow": {over: 0}})
        # A Send payload replaces the worker's state, so the run context travels
        # with it: state-injected tools (workspace_dir, run_id, ...) need it.
        context = {
            key: state[key]
            for key in ("workspace_dir", "run_id", "conversation_id", "user_id")
            if isinstance(state, dict) and key in state
        }
        sends = [
            Send(target, {"messages": [HumanMessage(content=str(item))], **context})
            for item in raw
        ]
        return Command(goto=sends, update={"flow": {over: len(sends)}})

    fan.__name__ = f"fan_{node.id}"
    return fan


def build_human(node: GraphNode, spec: GraphSpec, ctx: Any, config: dict[str, Any]) -> Callable[..., Any]:
    message = str(node.raw.get("message") or f"Review the run before '{node.label}' continues.")

    async def review(state: PlatformState) -> dict[str, Any]:
        decision = interrupt(
            {
                "action_requests": [
                    {"name": "human_review", "args": {"message": message}, "description": message}
                ],
                "review_configs": [
                    {"action_name": "human_review", "allowed_decisions": ["approve", "reject"]}
                ],
            }
        )
        return {"flow": {"review": decision}}

    review.__name__ = f"human_{node.id}"
    return review


def build_set_state(node: GraphNode, spec: GraphSpec, ctx: Any, config: dict[str, Any]) -> Callable[..., Any]:
    values = node.raw.get("values")
    payload = dict(values) if isinstance(values, dict) else {}

    async def assign(state: PlatformState) -> dict[str, Any]:
        return {"flow": payload}

    assign.__name__ = f"set_state_{node.id}"
    return assign


BUILDERS: dict[str, Callable[[GraphNode, GraphSpec, Any, dict[str, Any]], Any]] = {
    "router": build_router,
    "tool": build_tool,
    "join": build_join,
    "map": build_map,
    "human": build_human,
    "set_state": build_set_state,
}


def build_node(
    node: GraphNode,
    spec: GraphSpec,
    ctx: Any,
    compiled: dict[str, Any],
    config: dict[str, Any] | None = None,
) -> Any:
    """The runnable for one graph node.

    Agent and subgraph nodes are compiled by the runtime (they need models, MCP
    sessions and checkpoints), so they arrive pre-built in ``compiled``.
    """
    if node.kind in {"agent", "subgraph"}:
        runnable = compiled.get(node.id)
        if runnable is None:
            raise RuntimeError(f"Node '{node.label}' was not compiled.")
        return runnable
    builder = BUILDERS.get(node.kind)
    if builder is None:
        raise RuntimeError(f"Node '{node.label}' has unsupported kind '{node.kind}'.")
    return builder(node, spec, ctx, dict(config or {}))
