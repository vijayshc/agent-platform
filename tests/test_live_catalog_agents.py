import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import pytest
from platform_samples.agents import (
    TEXT2SQL_AGENT,
    DEVELOPER,
    DEEP_AGENT,
    RESEARCH_SUPERVISOR,
    SUPPORT_SWARM,
    REVIEW_GRAPH,
)
from src.agent_platform.runtime.compiler import compile_definition_sync
from src.utils.llm_connection_manager import build_client, resolve

ALL_AGENTS = [
    ("text2sql", TEXT2SQL_AGENT),
    ("developer", DEVELOPER),
    ("deep-agent", DEEP_AGENT),
    ("research-supervisor", RESEARCH_SUPERVISOR),
    ("support-swarm", SUPPORT_SWARM),
    ("review-graph", REVIEW_GRAPH),
]


@pytest.mark.parametrize("slug,agent_def", ALL_AGENTS)
def test_agent_compilation(slug, agent_def):
    conn = resolve()
    client = build_client(conn)
    compiled = compile_definition_sync(agent_def, client=client)
    assert compiled is not None


@pytest.mark.parametrize("slug,agent_def", [
    ("developer", DEVELOPER),
    ("review-graph", REVIEW_GRAPH),
])
def test_agent_graph_execution(slug, agent_def):
    import asyncio

    async def _test():
        conn = resolve()
        client = build_client(conn)
        compiled = compile_definition_sync(agent_def, client=client)
        input_state = {"messages": [{"role": "user", "content": "Ping"}]}
        res = await compiled.runnable.ainvoke(input_state)
        assert res is not None
        assert "messages" in res
        assert len(res["messages"]) > 0

    asyncio.run(_test())
