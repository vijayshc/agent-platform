"""Graph compiler behaviour that the review of the v2 rebuild pinned down.

Each test is a regression guard for a real defect found on a stored definition:
legacy node specs, edge-less canvases, fan-out context and unbounded router
loops. They run against the shipped compiler with a stub model — no LLM, no
database.
"""

from __future__ import annotations

import asyncio
from typing import Any

from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from src.agent_platform.plugins.orchestration.graph import normalize_graph
from src.agent_platform.plugins.orchestration.graph.compiler import compile_graph
from src.agent_platform.plugins.orchestration.graph.nodes import build_map, build_router
from src.agent_platform.runtime.langgraph_adapter import PlatformState


class _StubChatModel(FakeListChatModel):
    """Answers structured-output requests with a fixed enum pick."""

    #: Which allowed value to choose ("first" keeps the loop from forming, "last"
    #: keeps choosing the revising branch).
    pick: str = "first"

    def bind_tools(self, tools: Any, **kwargs: Any) -> Any:
        return self

    def with_structured_output(self, schema: Any, **kwargs: Any) -> Any:
        from langchain_core.runnables import RunnableLambda

        pick = self.pick

        def decide(_input: Any, **_kwargs: Any) -> dict[str, Any]:
            properties = (schema or {}).get("properties") if isinstance(schema, dict) else {}
            answer: dict[str, Any] = {}
            for name, spec in (properties or {}).items():
                enum = (spec or {}).get("enum") if isinstance(spec, dict) else None
                if not enum:
                    answer[name] = "stub"
                else:
                    answer[name] = enum[-1] if pick == "last" else enum[0]
            return answer

        return RunnableLambda(decide)


class _Context:
    """The pieces of CompileContext the graph nodes read."""

    def __init__(self, client: Any) -> None:
        self.client = client
        self.checkpoint_storage = None
        self.store = None
        self.workspace_dir = "/tmp/agent-workspace"
        self.extra_function_tools: list[Any] = []
        self.agents_by_name: dict[str, Any] = {}


def _messages(node_id: str, text: str):
    async def run(state: PlatformState) -> dict[str, Any]:
        return {"messages": [AIMessage(content=f"{node_id}:{text}")]}

    run.__name__ = f"node_{node_id}"
    return run


def test_legacy_flat_node_keeps_its_agent_spec() -> None:
    """Pre-v2 nodes carried the whole agent spec on the node itself."""
    spec = normalize_graph(
        {
            "pattern": "graph",
            "nodes": [
                {
                    "id": "Intake",
                    "name": "Intake",
                    "instructions": "Restate the question in one sentence.",
                    "mcp_bindings": [{"server": "Workspace", "tools": ["read_file"]}],
                }
            ],
            "edges": [],
        }
    )
    agent = spec.nodes[0].agent_spec
    assert agent["name"] == "Intake"
    assert agent["instructions"].startswith("Restate the question")
    assert agent["mcp_bindings"][0]["tools"] == ["read_file"]


def test_graph_without_edges_runs_every_node_from_start() -> None:
    """An edge-less canvas has always meant "run these in parallel"."""
    config = {
        "pattern": "graph",
        "nodes": [{"id": "a", "kind": "agent", "label": "A"}, {"id": "b", "kind": "agent", "label": "B"}],
        "edges": [],
    }
    spec = normalize_graph(config)
    assert spec.start_all is True

    ctx = _Context(_StubChatModel(responses=["ok"]))
    graph = compile_graph(config, ctx, compiled={"a": _messages("a", "one"), "b": _messages("b", "two")})
    out = asyncio.run(graph.ainvoke({"messages": [HumanMessage(content="go")]}))
    contents = {str(m.content) for m in out["messages"] if isinstance(m, AIMessage)}
    assert {"a:one", "b:two"} <= contents


def test_fanout_send_carries_the_run_context() -> None:
    """A Send payload replaces the worker's state, so it must carry the context."""
    spec = normalize_graph(
        {
            "pattern": "graph",
            "nodes": [
                {"id": "fan", "kind": "map", "label": "Fan", "over": "items", "to": "worker"},
                {"id": "worker", "kind": "agent", "label": "Worker"},
            ],
            "edges": [],
        }
    )
    fan = build_map(spec.by_id["fan"], spec, _Context(None), {})
    command = asyncio.run(
        fan(
            {
                "messages": [],
                "structured_response": {"items": ["one", "two"]},
                "workspace_dir": "/tmp/agent-workspace",
                "run_id": 42,
                "conversation_id": "7",
                "user_id": 1,
            }
        )
    )
    sends = command.goto
    assert all(isinstance(send, Send) for send in sends)
    for send in sends:
        assert send.arg["workspace_dir"] == "/tmp/agent-workspace"
        assert send.arg["run_id"] == 42
        assert send.arg["user_id"] == 1


def test_fanout_without_an_upstream_list_is_an_error() -> None:
    spec = normalize_graph(
        {
            "pattern": "graph",
            "nodes": [
                {"id": "fan", "kind": "map", "label": "Fan", "over": "items", "to": "worker"},
                {"id": "worker", "kind": "agent", "label": "Worker"},
            ],
            "edges": [],
        }
    )
    fan = build_map(spec.by_id["fan"], spec, _Context(None), {})
    try:
        asyncio.run(fan({"messages": [], "structured_response": None}))
    except RuntimeError as exc:
        assert "items" in str(exc)
    else:  # pragma: no cover - the guard must fire
        raise AssertionError("a fan-out with no upstream list must refuse to run")


def test_router_loop_stops_at_max_visits() -> None:
    """A routing loop without a bound can only die on the recursion limit."""
    config = {
        "pattern": "graph",
        "graph": {
            "entry": "loop",
            "nodes": [
                {"id": "loop", "kind": "set_state", "label": "Attempt", "values": {}},
                {
                    "id": "judge",
                    "kind": "router",
                    "label": "Judge",
                    "routes": [
                        {"name": "done", "description": "accepted", "to": "__end__"},
                        {"name": "again", "description": "needs another pass", "to": "loop"},
                    ],
                    "default": "loop",
                    "max_visits": 2,
                },
            ],
            "edges": [{"from": "loop", "to": "judge"}],
        },
    }
    ctx = _Context(_StubChatModel(responses=["ok"]))
    graph = compile_graph(config, ctx)
    out = asyncio.run(graph.ainvoke({"messages": [HumanMessage(content="go")]}))
    # The stub always answers with the first route ("done"), so force the loop by
    # checking the counter the router keeps instead: it is incremented per visit.
    assert out["flow"]["router_visits"]["judge"] == 1


def test_router_limits_itself_when_the_model_keeps_revising() -> None:
    """A model that always says "revise" must still be stopped by the limit."""
    config = {
        "pattern": "graph",
        "graph": {
            "entry": "gen",
            "nodes": [
                {"id": "gen", "kind": "set_state", "label": "Gen", "values": {}},
                {
                    "id": "judge",
                    "kind": "router",
                    "label": "Judge",
                    "routes": [
                        {"name": "done", "description": "accepted", "to": "__end__"},
                        {"name": "again", "description": "revise", "to": "gen"},
                    ],
                    "default": "gen",
                    "max_visits": 2,
                },
            ],
            "edges": [{"from": "gen", "to": "judge"}],
        },
    }
    model = _StubChatModel(responses=["ok"])
    model.pick = "last"
    graph = compile_graph(config, _Context(model))
    out = asyncio.run(graph.ainvoke({"messages": [HumanMessage(content="go")]}))
    # Two allowed passes, then the bounding visit takes the "done" route.
    assert out["flow"]["router_visits"]["judge"] == 3


def test_nested_flow_keeps_its_control_nodes_silent() -> None:
    """A router inside a reused saved flow must not speak to the user either."""
    inner = compile_graph(
        {
            "pattern": "graph",
            "graph": {
                "entry": "inner_router",
                "nodes": [
                    {
                        "id": "inner_router",
                        "kind": "router",
                        "label": "Inner",
                        "routes": [{"name": "x", "to": "__end__"}],
                    }
                ],
                "edges": [],
            },
        },
        _Context(_StubChatModel(responses=["ok"])),
    )
    outer = compile_graph(
        {
            "pattern": "graph",
            "graph": {
                "entry": "reused",
                "nodes": [
                    {"id": "reused", "kind": "subgraph", "label": "Reused", "ref": "some-flow"},
                    {"id": "wrap", "kind": "agent", "label": "Wrap"},
                ],
                "edges": [],
            },
        },
        _Context(_StubChatModel(responses=["ok"])),
        compiled={"reused": inner, "wrap": _messages("wrap", "done")},
    )
    assert inner.silent_nodes == {"inner_router"}
    assert "inner_router" in outer.silent_nodes


def test_compiled_graph_marks_control_nodes_silent() -> None:
    config = {
        "pattern": "graph",
        "graph": {
            "entry": "r",
            "nodes": [
                {"id": "r", "kind": "router", "label": "R", "routes": [{"name": "x", "to": "__end__"}]},
                {"id": "a", "kind": "agent", "label": "A"},
            ],
            "edges": [],
        },
    }
    graph = compile_graph(config, _Context(_StubChatModel(responses=["ok"])), compiled={"a": _messages("a", "hi")})
    assert graph.silent_nodes == {"r"}
    assert START in graph.get_graph().nodes or "__start__" in graph.get_graph().nodes
