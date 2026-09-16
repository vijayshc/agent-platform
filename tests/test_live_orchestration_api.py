"""Live tests against the running app and real LLM. Seeded catalog only.

  LIVE_AGENT_TIMEOUT=180 python -m pytest tests/test_live_orchestration_api.py -q
"""

from __future__ import annotations

from livehelpers import (
    LiveClient,
    _agent_bases,
    _has_tool_named,
    _refetch,
    _reply,
    _tool_events,
)


def _finish(api: LiveClient, payload: dict) -> tuple[dict, str]:
    run_id = (payload.get("run") or {}).get("public_id") or (payload.get("run") or {}).get("id")
    assert run_id, payload
    run = api.wait_for_run(str(run_id))
    run = _refetch(api, str(run_id))
    reply = _reply(payload, run)
    assert run["status"] == "success", run.get("error") or reply
    assert reply.strip()
    return run, reply


def test_live_text2sql_top_customer(api: LiveClient):
    payload = api.invoke("text2sql", "which customer purchased the most")
    run, reply = _finish(api, payload)
    assert _tool_events(run, "execute_sql_query") or _has_tool_named(run, "execute_sql_query")
    assert "john doe" in reply.lower() or "1559" in reply


def test_live_supervisor_delegates_two_specialists(api: LiveClient):
    payload = api.invoke(
        "research-supervisor",
        "How many customers are there, and who spent the most? Have DataResearcher gather facts, then Writer summarize.",
    )
    run, reply = _finish(api, payload)
    agents = _agent_bases(run, payload.get("sse"))
    assert "DataResearcher" in agents
    assert "Writer" in agents
    assert _has_tool_named(run, "transfer_to_dataresearcher")
    assert _has_tool_named(run, "transfer_to_writer")
    assert _has_tool_named(run, "execute_sql_query")
    assert "john doe" in reply.lower() or "5" in reply


def test_live_swarm_handoff_sql_then_code(api: LiveClient):
    conv = api.create_conversation("support-swarm", "swarm sql then code")
    cid = conv.get("id")
    first = api.invoke("support-swarm", "which customer purchased the most", conversation_id=cid)
    run1, reply1 = _finish(api, first)
    agents1 = _agent_bases(run1, first.get("sse"))
    assert "SqlDesk" in agents1
    assert _has_tool_named(run1, "transfer_to_sqldesk")
    assert "john doe" in reply1.lower() or "1559" in reply1.lower()

    second = api.invoke("support-swarm", "list the top-level workspace folders", conversation_id=cid)
    run2, reply2 = _finish(api, second)
    agents2 = _agent_bases(run2, second.get("sse"))
    assert "CodeDesk" in agents2
    assert _has_tool_named(run2, "transfer_to_codedesk") or _has_tool_named(run2, "list_dir")
    assert reply2.strip()


def test_live_graph_pipeline_nodes(api: LiveClient):
    payload = api.invoke("review-graph", "which customer purchased the most")
    run, reply = _finish(api, payload)
    agents = _agent_bases(run, payload.get("sse"))
    assert {"Intake", "Analyst", "Closer"} <= agents, agents
    assert "john doe" in reply.lower() or "1559" in reply.lower() or "customer" in reply.lower()
