"""Orchestration-run plumbing tests (compile/struct), no agent-behavior mocks.

Agent-behavior coverage (real LLM runs) lives in the live suites:
``tests/test_live_orchestration_api.py`` and ``tests/test_live_agent_platform.py``
The unit tests here only verify that seeded
workflow definitions compile into the correct MAF shape, so they are plumbing
unit tests and must NOT use ScriptedChatClient as a run engine.
"""

from __future__ import annotations

from src.agent_platform.catalog.store import DefinitionStore
from src.agent_platform.execution.run_store import RunStore
from src.agent_platform.plugins import register_builtin_plugins
from src.agent_platform.runtime.compiler import compile_definition_sync
from platform_samples.agents import seed_definitions


def test_seeded_orchestration_samples_compile(temp_db):
    # plumbing unit test (compile shape only, no run): live counterparts run
    # each pattern end to end in test_live_orchestration_api.py /
    # test_live_agent_platform.py.
    register_builtin_plugins()
    from platform_samples.feed import feed_mcp_servers

    feed_mcp_servers()
    seed_definitions()
    # Seed the default LLM connection (from config) so compiling the seeded
    # definitions resolves the REAL client path; nothing runs here, so no model
    # call is made.
    from src.utils.llm_connection_manager import seed_default_from_config

    seed_default_from_config()
    for slug in (
        "research-supervisor",
        "support-swarm",
        "review-graph",
    ):
        row = DefinitionStore.get_by_slug(slug)
        assert row, slug
        compiled = compile_definition_sync(row)
        assert compiled.is_workflow, slug
        assert compiled.kind == "workflow", slug
        assert (row.get("config") or {}).get("pattern") in {"supervisor", "swarm", "graph"}, slug
