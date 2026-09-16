"""The capability registry is the contract between the Studio and the runtime.

These tests pin the out-of-the-box surface: every runtime, workflow pattern,
node kind and middleware the editor can offer must compile to a real LangGraph
object built from the shipped constructor, and every capability id must survive
the round trip registry → config → compile.
"""

from __future__ import annotations

import pytest
from langchain_core.language_models.fake_chat_models import FakeListChatModel

from src.agent_platform.catalog.capabilities import (
    MIDDLEWARE,
    MIDDLEWARE_BY_ID,
    NODE_KINDS,
    PATTERNS,
    PATTERNS_BY_ID,
    RUNTIMES,
    TEMPLATES,
    materialize_template,
    studio_catalog,
)
from src.agent_platform.catalog.plan import compile_plan
from src.agent_platform.catalog.validate import validate_definition
from src.agent_platform.runtime.compiler import compile_definition_sync

AGENT = {"name": "Helper", "instructions": "You are a concise helper."}


def _definition(kind: str, config: dict) -> dict:
    return {"name": config.get("name") or "Probe", "kind": kind, "config": {"kind": kind, **config}}


class _StubChatModel(FakeListChatModel):
    """A chat model that also answers structured-output requests.

    Nodes that classify (routers) call ``with_structured_output`` while the graph
    is built, so the stub has to answer that too — with the first allowed value
    of each requested property.
    """

    def bind_tools(self, tools: Any, **kwargs: Any) -> Any:
        """Supervisor/swarm graphs bind handoff tools while they are built."""
        return self

    def with_structured_output(self, schema: Any, **kwargs: Any) -> Any:
        from langchain_core.runnables import RunnableLambda

        def decide(_input: Any, **_kwargs: Any) -> dict[str, Any]:
            properties = (schema or {}).get("properties") if isinstance(schema, dict) else {}
            answer: dict[str, Any] = {}
            for name, spec in (properties or {}).items():
                enum = (spec or {}).get("enum") if isinstance(spec, dict) else None
                answer[name] = enum[0] if enum else "stub"
            return answer

        return RunnableLambda(decide)


def _compile(definition: dict):
    """Structural compile with a stub model.

    The tests are about the wiring (which shipped builder each capability maps
    to), so they must not depend on the operator's LLM connections being
    configured — a stub keeps them hermetic and fast.
    """
    return compile_definition_sync(definition, client=_StubChatModel(responses=["ok"]))


def _node_names(compiled) -> list[str]:
    return list(getattr(compiled.runnable, "nodes", {}) or {})


def test_catalog_exposes_every_library_capability() -> None:
    catalog = studio_catalog()
    assert {r["id"] for r in catalog["runtimes"]} == {"agent", "deep_agent"}
    assert {p["id"] for p in catalog["patterns"]} >= {
        "supervisor",
        "swarm",
        "sequential",
        "parallel",
        "routing",
        "orchestrator_worker",
        "evaluator_optimizer",
        "graph",
    }
    # Every entry names the shipped constructor it stands for.
    for entry in [*catalog["runtimes"], *catalog["patterns"], *catalog["node_kinds"], *catalog["middleware"]]:
        assert entry["builder"] or entry.get("class"), entry
    assert len(catalog["middleware"]) == len(MIDDLEWARE) >= 16
    assert all(entry["id"] in MIDDLEWARE_BY_ID for entry in catalog["middleware"])


@pytest.mark.parametrize("runtime", [r["id"] for r in RUNTIMES])
def test_every_runtime_compiles(runtime: str) -> None:
    config = dict(AGENT, runtime=runtime)
    if runtime == "deep_agent":
        config["deep_agent"] = {
            "subagents": [
                {"name": "researcher", "description": "Finds facts", "instructions": "Research."}
            ]
        }
    compiled = _compile(_definition("agent", config))
    assert compiled.kind == "agent"
    assert "model" in _node_names(compiled)


@pytest.mark.parametrize("pattern", [p["id"] for p in PATTERNS])
def test_every_pattern_compiles(pattern: str) -> None:
    spec = PATTERNS_BY_ID[pattern]
    template = spec["template"]
    if pattern in {"supervisor", "swarm"}:
        config = {
            "pattern": pattern,
            "manager": {"name": "Lead", "instructions": "Delegate."},
            "participants": [
                {"name": "One", "instructions": "Do the first part."},
                {"name": "Two", "instructions": "Do the second part."},
            ],
        }
    elif template == "custom":
        # "custom" is the empty canvas: the author wires it, so the test wires one.
        config = {
            "pattern": "graph",
            "template": "custom",
            "graph": {
                "entry": "solo",
                "nodes": [{"id": "solo", "kind": "agent", "label": "Solo", "agent": dict(AGENT)}],
                "edges": [],
            },
        }
    else:
        config = {"pattern": "graph", "template": template}
    compiled = _compile(_definition("workflow", config))
    assert compiled.kind == "workflow"
    assert _node_names(compiled)


@pytest.mark.parametrize("template_id", sorted(TEMPLATES))
def test_every_shipped_template_validates(template_id: str) -> None:
    """A blueprint the gallery offers must be publishable as it ships.

    ``custom`` is the empty canvas: it has nothing to validate until the author
    wires it, and a draft may be saved empty.
    """
    if not TEMPLATES[template_id]["nodes"]:
        assert materialize_template(template_id)["nodes"] == []
        return
    report = validate_definition(
        _definition("workflow", {"pattern": "graph", "template": template_id})
    )
    assert report["ok"], report["errors"]


def test_generated_templates_use_only_known_kinds() -> None:
    for template_id in TEMPLATES:
        graph = materialize_template(template_id)
        for node in graph["nodes"]:
            assert node["kind"] in {k["id"] for k in NODE_KINDS}
            if node["kind"] == "agent":
                assert node["agent"]["instructions"]


def test_every_configured_middleware_becomes_a_library_instance() -> None:
    from src.agent_platform.runtime.compiler import CompileContext
    from src.agent_platform.runtime.langchain_middleware import build_middleware

    # Configs use only optional arguments, so a model is enough to construct one.
    values = {
        "summarization": {"trigger_tokens": 4000, "keep_messages": 10},
        "context_editing": {"trigger_tokens": 90000, "keep_tool_uses": 2},
        "todo_list": {},
        "human_in_the_loop": {"tools": ["dangerous_write"]},
        "model_call_limit": {"run_limit": 5},
        "tool_call_limit": {"tool_name": "search", "run_limit": 3},
        "model_retry": {"max_retries": 1},
        "tool_retry": {"max_retries": 1},
        "tool_error": {},
        "tool_selection": {"max_tools": 4},
        "provider_tool_search": {},
        "pii": {"rules": [{"type": "email", "strategy": "redact"}]},
        "file_search": {},
        "tool_emulator": {"tools": ["search"]},
    }
    from langchain_core.language_models.fake_chat_models import FakeListChatModel

    # A real chat model: SummarizationMiddleware/LLMToolEmulator wrap it.
    model = FakeListChatModel(responses=["ok"])
    built = build_middleware({"middleware": values}, CompileContext(workspace_dir="/tmp"), model=model)
    names = {type(m).__name__ for m in built}
    for middleware_id in values:
        expected = MIDDLEWARE_BY_ID[middleware_id]["class"].rsplit(".", 1)[-1]
        assert expected in names, middleware_id


def test_unknown_middleware_refuses_to_compile() -> None:
    with pytest.raises(RuntimeError, match="Unknown middleware"):
        _compile(_definition("agent", dict(AGENT, middleware={"telepathy": {}})))


def test_legacy_token_budget_maps_to_summarization() -> None:
    compiled = _compile(_definition("agent", dict(AGENT, maxContextWindowTokens=12000)))
    assert any("SummarizationMiddleware" in name for name in _node_names(compiled))


def test_legacy_approval_list_maps_to_human_in_the_loop() -> None:
    compiled = _compile(_definition("agent", dict(AGENT, hitl={"approval": ["dangerous_write"]})))
    assert any("HumanInTheLoopMiddleware" in name for name in _node_names(compiled))


def test_validation_reports_authoring_mistakes() -> None:
    cases = {
        "unknown_middleware": dict(AGENT, middleware={"nope": {}}),
        "unknown_runtime": dict(AGENT, runtime="magic"),
        "response_format_without_title": dict(
            AGENT, response_format={"strategy": "provider", "schema": {"type": "object"}}
        ),
    }
    for expected, config in cases.items():
        report = validate_definition(_definition("agent", config))
        assert not report["ok"]
        assert expected in {error["code"] for error in report["errors"]}, expected


def test_validation_reports_graph_mistakes() -> None:
    report = validate_definition(
        _definition(
            "workflow",
            {
                "pattern": "graph",
                "graph": {
                    "entry": "r",
                    "nodes": [
                        {"id": "r", "kind": "router", "label": "Route", "routes": [{"name": "x", "to": "ghost"}]},
                        {"id": "t", "kind": "tool", "label": "Tool", "tool": "missing_tool"},
                    ],
                    "edges": [],
                },
            },
        )
    )
    codes = {error["code"] for error in report["errors"]}
    assert {"route_target_missing", "unknown_tool"} <= codes


def test_plan_describes_the_builders_that_will_run() -> None:
    plan = compile_plan(
        _definition(
            "workflow",
            {
                "pattern": "supervisor",
                "manager": {"name": "Lead", "instructions": "Delegate."},
                "participants": [dict(AGENT, middleware={"todo_list": {}})],
            },
        )
    )
    assert plan["plan"]["builder"] == "langgraph_supervisor.create_supervisor"
    participant = plan["plan"]["participants"][0]
    assert participant["builder"] == "langchain.agents.create_agent"
    assert "TodoListMiddleware" in participant["middleware"]


def test_graph_flow_plan_lists_node_kinds() -> None:
    plan = compile_plan(_definition("workflow", {"pattern": "graph", "template": "orchestrator_worker"}))
    builders = {node["builder"] for node in plan["plan"]["nodes"]}
    assert "langgraph.types.Send" in builders
    assert "langchain.agents.create_agent" in builders
