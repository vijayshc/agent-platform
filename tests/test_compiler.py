from __future__ import annotations

import pytest
from langchain_core.messages import HumanMessage, AIMessage
from src.agent_platform.catalog.store import DefinitionStore
from src.agent_platform.plugins import register_builtin_plugins
from src.agent_platform.runtime.compiler import compile_definition_sync
from src.agent_platform.runtime.scripted_client import ScriptedChatClient


def test_compiler_harness_uses_deep_agent():
    register_builtin_plugins()
    client = ScriptedChatClient(responses=["ack"])
    compiled = compile_definition_sync(
        {
            "kind": "agent",
            "name": "Deep",
            "config": {
                "kind": "agent",
                "runtime": "harness",
                "instructions": "Plan then answer.",
            },
        },
        client=client,
    )
    assert compiled.kind == "agent"
    assert compiled.name == "Deep"


def test_compiler_emits_agent():
    register_builtin_plugins()
    client = ScriptedChatClient(responses=["ack"])
    compiled = compile_definition_sync(
        {
            "kind": "agent",
            "name": "Echo",
            "config": {
                "kind": "agent",
                "instructions": "You reply with a short ack.",
            },
        },
        client=client,
    )
    assert compiled.kind == "agent"
    assert compiled.is_agent
    assert compiled.name == "Echo"


def test_compiler_agent_with_function_tools():
    register_builtin_plugins()
    client = ScriptedChatClient(responses=["done"])
    compiled = compile_definition_sync(
        {
            "kind": "agent",
            "name": "Writer",
            "config": {
                "kind": "agent",
                "instructions": "Write files.",
                "function_tools": ["dangerous_write"],
            },
        },
        client=client,
    )
    assert compiled.kind == "agent"
    assert len(getattr(compiled.runnable, "tools", [])) >= 1


def test_compiler_agent_with_hitl_approval_list():
    register_builtin_plugins()
    client = ScriptedChatClient(responses=["done"])
    compiled = compile_definition_sync(
        {
            "kind": "agent",
            "name": "Writer",
            "config": {
                "kind": "agent",
                "instructions": "Write files.",
                "function_tools": ["dangerous_write"],
                "hitl": {"approval": ["dangerous_write"]},
            },
        },
        client=client,
    )
    assert compiled.kind == "agent"
    tools = getattr(compiled.runnable, "tools", [])
    assert len(tools) >= 1
    mode = getattr(tools[0], "approval_mode", None) or (getattr(tools[0], "metadata", {}) or {}).get("approval_mode")
    assert mode == "always_require"


def test_compiler_supervisor_swarm_and_graph():
    register_builtin_plugins()
    client = ScriptedChatClient(responses=["ok"])
    supervisor = compile_definition_sync(
        {
            "kind": "workflow",
            "name": "Team",
            "config": {
                "kind": "workflow",
                "pattern": "supervisor",
                "manager": {"name": "Lead", "instructions": "Delegate."},
                "participants": [
                    {"name": "Alpha", "instructions": "Do alpha."},
                    {"name": "Beta", "instructions": "Do beta."},
                ],
            },
        },
        client=client,
    )
    assert supervisor.kind == "workflow"
    swarm = compile_definition_sync(
        {
            "kind": "workflow",
            "name": "Desk",
            "config": {
                "kind": "workflow",
                "pattern": "swarm",
                "start_agent": "Triage",
                "handoffs": [{"from": "Triage", "to": "Specialist"}],
                "participants": [
                    {"name": "Triage", "instructions": "Route."},
                    {"name": "Specialist", "instructions": "Answer."},
                ],
            },
        },
        client=client,
    )
    assert swarm.kind == "workflow"
    graph = compile_definition_sync(
        {
            "kind": "workflow",
            "name": "Pipe",
            "config": {
                "kind": "workflow",
                "pattern": "graph",
                "entry": "First",
                "nodes": [
                    {"id": "First", "name": "First", "instructions": "Start."},
                    {"id": "Second", "name": "Second", "instructions": "Finish."},
                ],
                "edges": [{"from": "First", "to": "Second"}],
            },
        },
        client=client,
    )
    assert graph.kind == "workflow"
