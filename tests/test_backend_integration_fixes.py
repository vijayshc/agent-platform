"""Regression tests for three integration defects found in the live audit.

1. A model tool call inside a nested graph node was streamed once per namespace,
   so the timeline showed result-less duplicate ``tool_call`` steps.
2. ``POST /runs/<id>/cancel`` rewrote a finished run's status to ``cancelled``.
3. ``GET /agents?include_drafts=1`` / ``GET /agents/<slug>?full=1`` omitted the
   ``description``/``pattern`` fields the Studio's ``AgentDef`` reads at the top
   level, while the published listing carried them.

Live-app behaviour is additionally covered by
``temp/live_node_kinds.py`` (one ``tool_call`` per call id through a subgraph
node) and by the cross-surface consistency run.
"""

from __future__ import annotations

from src.agent_platform.api.api_helpers import definition_summary, full_definition
from src.agent_platform.execution.run_store import RunStore
from src.agent_platform.runtime.host import _duplicate_tool_call, _duplicate_tool_result


# --------------------------------------------------------------------- (1)
def test_a_tool_call_is_streamed_once_per_call_id():
    seen_calls: set[str] = set()
    first = {"type": "tool_call", "tool_name": "remember_fact", "call_id": "call_1"}
    parent_repeat = {"type": "tool_call", "tool_name": "remember_fact", "call_id": "call_1"}
    other_call = {"type": "tool_call", "tool_name": "recall_facts", "call_id": "call_2"}

    assert _duplicate_tool_call(first, seen_calls) is False
    assert _duplicate_tool_call(parent_repeat, seen_calls) is True
    assert _duplicate_tool_call(other_call, seen_calls) is False
    # Events without a call id are never dropped.
    assert _duplicate_tool_call({"type": "tool_call", "tool_name": "x"}, set()) is False
    assert _duplicate_tool_call({"type": "chat"}, set()) is False


def test_a_tool_result_is_not_confused_with_its_call():
    # The call and its result share the call id: a single "seen" set for both
    # would swallow the result.
    call = {"type": "tool_call", "tool_name": "remember_fact", "call_id": "call_1"}
    result = {"type": "tool_result", "tool_name": "remember_fact", "call_id": "call_1"}
    seen_calls: set[str] = set()
    seen_results: set[str] = set()
    assert _duplicate_tool_call(call, seen_calls) is False
    assert _duplicate_tool_result(result, seen_results) is False
    assert _duplicate_tool_result(result, seen_results) is True


def test_nested_graph_node_streams_one_tool_call_and_result(temp_db):
    """End-to-end: a graph node that calls a tool reports it exactly once.

    Before the guard, the child namespace reported the call and the parent
    namespace's node update re-reported it without a result (verified by running
    ``temp/dup_probe.py`` with ``DUP_PROBE_OFF=1``: 2 calls sharing one call id,
    1 result). The scripted client keeps this a plumbing test of the stream
    mapping; live coverage is ``temp/live_node_kinds.py``.
    """
    import asyncio

    from src.agent_platform.plugins import register_builtin_plugins
    from src.agent_platform.runtime.host import RuntimeHost
    from src.agent_platform.runtime.scripted_client import ScriptedChatClient

    definition = {
        "kind": "workflow",
        "name": "Nested call",
        "config": {
            "kind": "workflow",
            "pattern": "graph",
            "template": "custom",
            "graph": {
                "entry": "note",
                "nodes": [
                    {"id": "note", "kind": "agent", "label": "Note",
                     "agent": {"name": "Note", "runtime": "agent",
                               "instructions": "Call remember_fact.",
                               "function_tools": ["remember_fact"]}},
                    {"id": "remember", "kind": "tool", "label": "Remember", "tool": "remember_fact"},
                    {"id": "done", "kind": "set_state", "label": "Done", "values": {}},
                ],
                "edges": [
                    {"from": "note", "to": "remember"},
                    {"from": "remember", "to": "done"},
                ],
            },
        },
    }

    async def _run() -> list[dict]:
        register_builtin_plugins()
        client = ScriptedChatClient(responses=[
            {"type": "function_call", "name": "remember_fact",
             "arguments": {"fact": "the deploy code is BLUE"}, "call_id": "call_test_1"},
            "Saved.",
        ])
        run = RunStore.create(task="go", definition_id=None, user_id=1, agent_slug="nested")
        return [
            event
            async for event in RuntimeHost().stream_run(
                definition=definition, input_text="go", run_id=int(run["id"]), client=client
            )
        ]

    events = asyncio.run(_run())
    calls = [e for e in events if e.get("type") == "tool_call"]
    results = [e for e in events if e.get("type") == "tool_result"]
    assert [c.get("call_id") for c in calls] == ["call_test_1"], calls
    assert [r.get("call_id") for r in results] == ["call_test_1"], results


# --------------------------------------------------------------------- (2)
def _finished_run(status: str) -> str:
    run = RunStore.create(task="probe", definition_id=None, user_id=1, agent_slug="probe")
    RunStore.finish(int(run["id"]), status, final_reply="done")
    return str(run["public_id"])


def test_cancel_refuses_a_finished_run(app_client):
    run_id = _finished_run("success")
    resp = app_client.post(f"/api/v1/runs/{run_id}/cancel")
    assert resp.status_code == 409, resp.get_data(as_text=True)
    body = resp.get_json()
    assert body["error"] == "run already finished"
    assert body["status"] == "success"
    assert RunStore.get(run_id)["status"] == "success"


def test_cancel_still_stops_a_live_run(app_client):
    run = RunStore.create(task="probe", definition_id=None, user_id=1, agent_slug="probe")
    run_id = str(run["public_id"])
    resp = app_client.post(f"/api/v1/runs/{run_id}/cancel")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert resp.get_json()["status"] == "cancelled"
    assert RunStore.get(run_id)["status"] == "cancelled"
    # A second cancel is refused: the run is terminal now.
    again = app_client.post(f"/api/v1/runs/{run_id}/cancel")
    assert again.status_code == 409


# --------------------------------------------------------------------- (3)
def test_definition_summary_promotes_the_studio_fields():
    row = {
        "id": 7,
        "slug": "probe",
        "name": "Probe",
        "kind": "workflow",
        "published": True,
        "config": {
            "kind": "workflow",
            "description": "Routes billing questions",
            "pattern": "graph",
            "tags": ["billing"],
            "model": {"client": "default"},
            "studio": {"nodes": []},
        },
    }
    assert definition_summary(row) == {
        "description": "Routes billing questions",
        "tags": ["billing"],
        "pattern": "graph",
        "model": {"client": "default"},
    }
    full = full_definition(row)
    assert full["description"] == "Routes billing questions"
    assert full["pattern"] == "graph"
    assert full["config"]["description"] == "Routes billing questions"  # config is untouched


def test_agents_list_keeps_the_studio_shape_with_drafts(app_client):
    body = {
        "name": "Shape Probe",
        "kind": "workflow",
        "published": True,
        "config": {
            "kind": "workflow",
            "description": "Draft with a description",
            "pattern": "graph",
            "template": "custom",
            "graph": {"entry": "step", "nodes": [
                {"id": "step", "kind": "agent", "label": "Step",
                 "agent": {"name": "Step", "instructions": "Answer briefly."}},
            ], "edges": []},
        },
    }
    created = app_client.post("/api/v1/agents", json=body)
    assert created.status_code in (200, 201), created.get_data(as_text=True)
    slug = created.get_json()["slug"]

    published = app_client.get("/api/v1/agents")
    drafts = app_client.get("/api/v1/agents?include_drafts=1")
    assert drafts.status_code == 200, drafts.get_data(as_text=True)

    pub_row = next(r for r in published.get_json()["agents"] if r["slug"] == slug)
    draft_row = next(r for r in drafts.get_json()["agents"] if r["slug"] == slug)
    for key in ("description", "pattern", "tags", "model"):
        assert draft_row.get(key) == pub_row.get(key), key
    assert draft_row["description"] == "Draft with a description"
    assert draft_row["pattern"] == "graph"
    assert "created_by_name" in draft_row and "updated_by_name" in draft_row
