from __future__ import annotations

from src.agent_platform.plugins import register_builtin_plugins
from src.agent_platform.runtime.compiler import compile_definition_sync
from platform_samples.agents import TEXT2SQL_AGENT
from src.agent_platform.runtime.scripted_client import ScriptedChatClient


def test_studio_definition_compiles():
    register_builtin_plugins()
    client = ScriptedChatClient(responses=["ok"])
    compiled = compile_definition_sync(
        {
            "kind": "agent",
            "name": "Text2SQL Agent",
            "config": dict(TEXT2SQL_AGENT),
        },
        client=client,
    )
    assert compiled.kind == "agent"
    assert compiled.is_agent
    assert compiled.name == "Text2SQL Agent"
