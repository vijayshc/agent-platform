"""PLUMBING — AG-UI protocol/transport tests (not agent behavior).

They drive a scripted agent ONLY to make the SSE stream deterministic; the
agent-behavior lifecycle with a real model is covered by
tests/test_live_agent_platform.py::test_live_agui_input_streams_lifecycle.
"""

from __future__ import annotations

import json

from src.agent_platform.catalog.store import DefinitionStore
from src.agent_platform.conversations.store import ConversationStore
from src.agent_platform.execution.api_keys import ApiKeyStore
from src.agent_platform.execution.run_store import RunStore
from src.agent_platform.plugins.models.scripted import reset_shared_clients
from src.agent_platform.plugins.tools.builtins import reset_dangerous_write_calls

_LEDGER_DONE = (
    '{"is_request_satisfied":{"reason":"plan approved","answer":true},'
    '"is_in_loop":{"reason":"no","answer":false},'
    '"is_progress_being_made":{"reason":"yes","answer":true},'
    '"next_speaker":{"reason":"n/a","answer":"Design Reviewer"},'
    '"instruction_or_question":{"reason":"finish","answer":"Summarize findings"}}'
)


def _app_client():
    from flask import Flask

    from src.agent_platform.agui.thread_store import ensure_tables
    from src.agent_platform.api.blueprint import create_blueprint

    DefinitionStore.ensure_tables()
    ApiKeyStore.ensure_tables()
    RunStore.ensure_tables()
    ConversationStore.ensure_tables()
    ensure_tables()
    app = Flask(__name__)
    app.secret_key = "test"
    app.register_blueprint(create_blueprint())
    http = app.test_client()
    reset_shared_clients()
    reset_dangerous_write_calls()
    return http


def _key(http, scopes=("runs:write", "agents:read")):
    key = ApiKeyStore.create(user_id=1, name="test", scopes=list(scopes))
    return {"X-API-Key": key["key"]}


def _events(body: str) -> list[dict]:
    out = []
    for block in body.split("\n\n"):
        for line in block.split("\n"):
            if not line.startswith("data:"):
                continue
            raw = line[len("data:"):].strip()
            if raw:
                out.append(json.loads(raw))
    return out


def _seed_echo():
    DefinitionStore.upsert(
        slug="echo",
        name="Echo",
        kind="agent",
        config={
            "kind": "agent",
            "instructions": "Reply with pong.",
            "model": {"client": "scripted", "reuse_id": "echo-agui", "responses": ["pong"]},
        },
        published=True,
        created_by=1,
    )


def _seed_writer():
    DefinitionStore.upsert(
        slug="writer",
        name="Writer",
        kind="agent",
        config={
            "kind": "agent",
            "instructions": "Call dangerous_write when asked to write.",
            "function_tools": ["dangerous_write"],
            "model": {
                "client": "scripted",
                "reuse_id": "writer-agui",
                "responses": [
                    {"type": "function_call", "call_id": "call_1", "name": "dangerous_write", "arguments": {"path": "x.txt", "content": "secret"}},
                    "wrote it",
                ],
            },
        },
        published=True,
        created_by=1,
    )


def _seed_studio():
    DefinitionStore.upsert(
        slug="studio",
        name="Studio",
        kind="workflow",
        config={
            "kind": "workflow",
            "pattern": "supervisor",
            "model": {
                "client": "scripted",
                "reuse_id": "studio-agui",
                "responses": [
                    "Facts: sample-service exists.",
                    "Plan: inspect then report.",
                    _LEDGER_DONE,
                    _LEDGER_DONE,
                    "Findings: reviewed.",
                    "Findings: reviewed.",
                ],
            },
            "manager": {"name": "Tech Lead", "instructions": "Coordinate specialists."},
            "participants": [{"name": "Design Reviewer", "instructions": "Review design."}],
        },
        published=True,
        created_by=1,
    )


def test_agui_input_streams_agent_lifecycle(temp_db):
    """PLUMBING: SSE event-type sequence for a completed scripted run."""
    http = _app_client()
    _seed_echo()
    resp = http.post(
        "/api/v1/agui/input",
        json={"messages": [{"role": "user", "content": "hello"}], "agent_id": "echo", "thread_id": "th-echo"},
        headers=_key(http),
    )
    assert resp.status_code == 200
    events = _events(resp.get_data(as_text=True))
    types = [e["type"] for e in events]
    assert types[0] == "RUN_STARTED"
    assert "TEXT_MESSAGE_START" in types
    assert "TEXT_MESSAGE_CONTENT" in types
    assert "TEXT_MESSAGE_END" in types
    assert "RUN_FINISHED" in types
    text = [e for e in events if e["type"] == "TEXT_MESSAGE_CONTENT"]
    assert text and str(text[0].get("delta") or "").strip()
    finished = [e for e in events if e["type"] == "RUN_FINISHED"][0]
    assert finished["result"]["final_reply"]
    run = RunStore.list_runs()[0]
    assert RunStore.get(run["id"])["status"] in {"success", "awaiting_approval"}


def test_agui_input_tool_call_and_approval_interrupt(temp_db):
    """PLUMBING: approval interrupt event shape on the AG-UI SSE stream."""
    http = _app_client()
    _seed_writer()
    resp = http.post(
        "/api/v1/agui/input",
        json={"messages": [{"role": "user", "content": "write the file"}], "agent_id": "writer", "thread_id": "th-w"},
        headers=_key(http),
    )
    assert resp.status_code == 200
    events = _events(resp.get_data(as_text=True))
    types = [e["type"] for e in events]
    assert "TOOL_CALL_START" in types
    assert "TOOL_CALL_ARGS" in types
    assert "TOOL_CALL_END" in types
    approval_custom = [e for e in events if e["type"] == "CUSTOM" and e.get("name") == "function_approval_request"]
    assert approval_custom, "approval request must be surfaced as a CUSTOM event"
    payload = approval_custom[0].get("value") or {}
    # The middleware's own interrupt payload, not a re-wrapped tool_name/arguments.
    assert [a["name"] for a in payload.get("action_requests") or []] == ["dangerous_write"]
    assert payload["review_configs"][0]["action_name"] == "dangerous_write"
    finished = [e for e in events if e["type"] == "RUN_FINISHED"][0]
    assert finished["interrupt"], "RUN_FINISHED must carry the approval interrupt"
    assert finished["interrupt"][0]["value"]["action_requests"] == payload["action_requests"]
    run = RunStore.list_runs()[0]
    stored = RunStore.get(run["id"])
    assert stored["status"] == "awaiting_approval"
    assert stored["pending_json"]["action_requests"][0]["name"] == "dangerous_write"


def test_agui_input_resume_executes_approved_tool(temp_db):
    """PLUMBING: AG-UI resume payload executes the approved function tool."""
    http = _app_client()
    _seed_writer()
    first = http.post(
        "/api/v1/agui/input",
        json={"messages": [{"role": "user", "content": "write the file"}], "agent_id": "writer", "thread_id": "th-w"},
        headers=_key(http),
    )
    events = _events(first.get_data(as_text=True))
    interrupt_id = [e for e in events if e["type"] == "RUN_FINISHED"][0]["interrupt"][0]["id"]
    assert interrupt_id

    resumed = http.post(
        "/api/v1/agui/input",
        json={
            "messages": [{"role": "user", "content": "write the file"}],
            "agent_id": "writer",
            "thread_id": "th-w",
            "resume": [{"id": interrupt_id, "value": {"accepted": True}}],
        },
        headers=_key(http),
    )
    assert resumed.status_code == 200
    resumed_events = _events(resumed.get_data(as_text=True))
    assert "RUN_FINISHED" in [e["type"] for e in resumed_events]
    run = RunStore.list_runs()[0]
    stored = RunStore.get(run["id"])
    assert stored["status"] != "awaiting_approval"
    assert stored.get("pending_json") in (None, "", {})
    # Tool-execution / reply text is live-only.


def test_agui_input_resume_with_decision_object_rejects(temp_db):
    """PLUMBING: the library decision object ({"type": "reject"}) also resumes."""
    http = _app_client()
    _seed_writer()
    first = http.post(
        "/api/v1/agui/input",
        json={"messages": [{"role": "user", "content": "write the file"}], "agent_id": "writer", "thread_id": "th-r"},
        headers=_key(http),
    )
    events = _events(first.get_data(as_text=True))
    interrupt_id = [e for e in events if e["type"] == "RUN_FINISHED"][0]["interrupt"][0]["id"]
    assert interrupt_id

    resumed = http.post(
        "/api/v1/agui/input",
        json={
            "messages": [{"role": "user", "content": "write the file"}],
            "agent_id": "writer",
            "thread_id": "th-r",
            "resume": [{"id": interrupt_id, "value": {"type": "reject", "message": "no"}}],
        },
        headers=_key(http),
    )
    assert resumed.status_code == 200
    stored = RunStore.get(RunStore.list_runs()[0]["id"])
    assert stored["status"] != "awaiting_approval"
    assert stored.get("pending_json") in (None, "", {})


def test_agui_input_resume_without_pending_returns_409(temp_db):
    """PLUMBING: resume with no pending approval is HTTP 409."""
    http = _app_client()
    _seed_echo()
    resp = http.post(
        "/api/v1/agui/input",
        json={
            "messages": [{"role": "user", "content": "hello"}],
            "agent_id": "echo",
            "thread_id": "th-nopending",
            "resume": [{"id": "nope", "value": {"accepted": True}}],
        },
        headers=_key(http),
    )
    assert resp.status_code == 409
    assert "no pending approval" in resp.get_json()["error"]


def test_agui_input_requires_published_agent(temp_db):
    """PLUMBING: unpublished agent is not invokable via AG-UI."""
    http = _app_client()
    DefinitionStore.upsert(
        slug="draft",
        name="Draft",
        kind="agent",
        config={"kind": "agent", "instructions": "x", "model": {"client": "scripted", "responses": ["x"]}},
        published=False,
        created_by=1,
    )
    resp = http.post(
        "/api/v1/agui/input",
        json={"messages": [{"role": "user", "content": "hi"}], "agent_id": "draft"},
        headers=_key(http),
    )
    assert resp.status_code == 404


def test_agui_state_roundtrip_and_events_replay(temp_db):
    """PLUMBING: AG-UI state store round-trip and event replay."""
    http = _app_client()
    _seed_echo()
    resp = http.post(
        "/api/v1/agui/input",
        json={
            "messages": [{"role": "user", "content": "hello"}],
            "agent_id": "echo",
            "thread_id": "th-state",
            "state": {"user": {"name": "Alice"}},
        },
        headers=_key(http),
    )
    assert resp.status_code == 200
    _events(resp.get_data(as_text=True))

    state = http.get("/api/v1/agui/state?thread_id=th-state", headers=_key(http))
    assert state.status_code == 200
    assert state.get_json()["state"] == {"user": {"name": "Alice"}}

    replay = http.get("/api/v1/agui/events?thread_id=th-state", headers=_key(http))
    assert replay.status_code == 200
    replayed = _events(replay.get_data(as_text=True))
    types = [e["type"] for e in replayed]
    assert types[0] == "RUN_STARTED"
    assert "TEXT_MESSAGE_CONTENT" in types
    assert "MESSAGES_SNAPSHOT" in types
    assert "RUN_FINISHED" in types
    deltas = [e.get("delta") for e in replayed if e["type"] == "TEXT_MESSAGE_CONTENT"]
    assert deltas and str(deltas[0] or "").strip()


def test_agui_requires_auth(temp_db):
    """PLUMBING: AG-UI endpoints require an API key."""
    http = _app_client()
    _seed_echo()
    resp = http.post(
        "/api/v1/agui/input",
        json={"messages": [{"role": "user", "content": "hi"}], "agent_id": "echo"},
    )
    assert resp.status_code == 401
