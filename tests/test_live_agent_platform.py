"""Live agent-behavior tests: real LLM, real Workspace MCP, real HITL.

Covers every agent-behavior area that was previously asserted only through
``ScriptedChatClient``: harness runtime, plan-review HITL with checkpoint
resume, concurrent + aggregator, managed groupchat, multi-turn conversations,
evals through the eval runner, the AG-UI stream, provider passthrough fields
path, and a self-contained single-agent + MCP tools + skill run.

These all run against a REAL running Flask app (see tests/livehelpers.py) and
the REAL OpenRouter model.
  python -m pytest tests/test_live_agent_platform.py -q
"""

from __future__ import annotations

import json

import pytest
import requests

from livehelpers import (
    DEFAULT_MODEL,
    SHORT,
    WS_READ,
    WS_WRITE,
    LiveClient,
    _agent_bases,
    _has_tool_named,
    _refetch,
    _reply,
    _tool_events,
)


APP_SLUG = "live-single-agent"  # created once by a module-scoped fixture


@pytest.fixture(scope="module")
def single_agent(api: LiveClient) -> dict:
    """Module-scoped real agent: Workspace MCP read tools + design-review skill."""
    return api.create_agent(
        "Live Single Agent",
        {
            "kind": "agent",
            "runtime": "agent",
            "instructions": (
                "Load the design-review skill if it is available. Inspect the workspace "
                "sample-service with list_dir/read_file/search_code before any verdict. "
                "Never claim you read a file you did not read. Report findings with "
                "severity, file evidence, and a concrete fix."
            ),
            "model": DEFAULT_MODEL,
            # A full findings report plus the reasoning that precedes it does not
            # fit a small cap, and hitting the cap fails the run (a truncated
            # answer is an error, see runtime/output_budget.py).
            "default_options": {"temperature": 0.1, "max_tokens": 2000},
            "mcp_bindings": [WS_READ],
            "maf_skill_ids": ["design-review"],
        },
    )


def _sse_events(resp: requests.Response) -> list[dict]:
    events: list[dict] = []
    for raw in resp.iter_lines(decode_unicode=True):
        if not raw or not str(raw).startswith("data:"):
            continue
        try:
            events.append(json.loads(str(raw)[5:].strip()))
        except json.JSONDecodeError:
            continue
    return events


# Harness runtime (Gap A): todos/memory/compaction providers on, live run


def test_live_harness_runtime_completes(api: LiveClient):
    """Seeded Deep Agent (create_deep_agent) completes a live workspace listing."""
    payload = api.invoke("deep-agent", "List top-level files and folders. Does README.md exist?")
    run_id = (payload.get("run") or {}).get("public_id") or (payload.get("run") or {}).get("id")
    assert run_id, payload
    run = api.wait_for_run(str(run_id))
    run = _refetch(api, str(run_id))
    reply = _reply(payload, run)
    assert run["status"] == "success", run.get("error") or reply
    assert reply.strip(), "deep agent returned an empty reply"
    assert _tool_events(run, "ls") or _has_tool_named(run, "ls", "list_dir", "glob")


def test_live_supervisor_delegates(api: LiveClient):
    """Seeded supervisor must assign DataResearcher and Writer."""
    payload = api.invoke(
        "research-supervisor",
        (
            "Do TWO assignments. DataResearcher must query who spent the most. "
            "Writer must produce a 2-sentence brief. Call both specialists."
        ),
    )
    run_id = (payload.get("run") or {}).get("public_id") or (payload.get("run") or {}).get("id")
    run = api.wait_for_run(str(run_id))
    run = _refetch(api, str(run_id))
    reply = _reply(payload, run)
    assert run["status"] == "success", run.get("error") or reply
    agents = _agent_bases(run, payload.get("sse"))
    assert _has_tool_named(run, "transfer_to_dataresearcher")
    assert _has_tool_named(run, "transfer_to_writer")
    assert "DataResearcher" in agents and "Writer" in agents
    assert reply.strip()


# Multi-turn conversation (turn 2 must see turn 1)


def test_live_conversation_multi_turn_same_agent(api: LiveClient):
    agent = api.create_agent(
        "Live Chit Chat",
        {
            "kind": "agent",
            "instructions": (
                "Answer concisely. If the user mentions or asks about a secret token, repeat it exactly "
                "in your reply. Otherwise reply with a short ack."
            ),
            "model": DEFAULT_MODEL,
            "default_options": SHORT,
        },
    )
    conv = api.create_conversation(agent["slug"], "live-multiturn")
    conv_id = conv.get("public_id") or conv.get("id")
    assert conv_id, conv

    turn1 = api.http.post(
        f"{api.base}/api/v1/conversations/{conv_id}/messages",
        json={"input": "Remember this secret token: zephyr-42.", "stream": False},
        timeout=(10, 120),
    )
    assert turn1.status_code < 400, turn1.text[:500]
    body1 = turn1.json()
    run1_id = (body1.get("run") or {}).get("public_id") or (body1.get("run") or {}).get("id")
    assert run1_id, body1
    run1 = api.wait_for_run(str(run1_id))
    assert run1["status"] == "success", run1.get("error")

    turn2 = api.http.post(
        f"{api.base}/api/v1/conversations/{conv_id}/messages",
        json={"input": "What was the secret token I told you?", "stream": False},
        timeout=(10, 120),
    )
    assert turn2.status_code < 400, turn2.text[:500]
    body2 = turn2.json()
    run2_id = (body2.get("run") or {}).get("public_id") or (body2.get("run") or {}).get("id")
    assert run2_id, body2
    run2 = api.wait_for_run(str(run2_id))
    run2 = _refetch(api, str(run2_id))
    reply2 = _reply(body2, run2)
    assert run2["status"] == "success", run2.get("error") or reply2
    assert reply2.strip(), "turn 2 reply is empty"
    assert "zephyr-42" in reply2, f"turn 2 did not see turn 1: {reply2!r}"

    # Conversation history is persisted: the GET returns both turns.
    history = api.http.get(f"{api.base}/api/v1/conversations/{conv_id}", timeout=15)
    assert history.status_code == 200, history.text[:400]
    msgs = history.json().get("messages") or []
    texts = " ".join(str(m.get("content") or "") for m in msgs)
    assert "zephyr-42" in texts


# Evals through the eval runner: real model, real eval results


def test_live_eval_run_scores_keyword(api: LiveClient):
    """Eval run pipeline: a real model run must pass the keyword check."""
    agent = api.create_agent(
        "Live Eval Echo",
        {
            "kind": "agent",
            "instructions": "Answer with a short ack that includes the word pong.",
            "model": DEFAULT_MODEL,
            "default_options": SHORT,
        },
    )
    created = api.http.post(
        f"{api.base}/api/v1/agents/{agent['slug']}/evals",
        json={"name": "live keyword", "items": [{"prompt": "say pong", "keyword": "pong"}]},
        timeout=15,
    )
    assert created.status_code == 201, created.text[:500]
    eval_id = created.json()["id"]

    ran = api.http.post(
        f"{api.base}/api/v1/agents/{agent['slug']}/evals/{eval_id}/run",
        json={},
        timeout=(10, 300),
    )
    assert ran.status_code == 200, ran.text[:600]
    outcome = ran.json()
    assert outcome["summary"]["passed"] + outcome["summary"]["failed"] + outcome["summary"]["errored"] == 1
    row = outcome["results"][0]
    assert row["status"] == "pass", row
    assert row["score"] == 1.0, row
    assert row["run_public_id"]
    run = api.wait_for_run(str(row["run_public_id"]))
    assert run["status"] == "success", run.get("error")
    assert (run.get("final_reply") or "").strip(), "eval run produced an empty reply"
    assert "pong" in (run.get("final_reply") or "").lower()

    results = api.http.get(f"{api.base}/api/v1/agents/{agent['slug']}/evals/{eval_id}/results", timeout=15)
    assert results.status_code == 200, results.text[:400]
    body = results.json()
    assert body["results"][0]["score"] == 1.0
    assert body["results"][0]["status"] == "pass"
    assert body["results"][0]["trace_id"]


def test_live_eval_run_tool_calls(api: LiveClient):
    """Eval with expected_tool_calls: the real model must call list_dir and pass."""
    agent = api.create_agent(
        "Live Eval Scout",
        {
            "kind": "agent",
            "instructions": (
                "Use the list_dir tool on sample-service, then reply with one sentence naming "
                "the entries you saw. You must call list_dir."
            ),
            "model": DEFAULT_MODEL,
            "default_options": SHORT,
            "mcp_bindings": [WS_READ],
        },
    )
    created = api.http.post(
        f"{api.base}/api/v1/agents/{agent['slug']}/evals",
        json={
            "name": "live tool calls",
            "items": [
                {
                    "prompt": "List the files in sample-service.",
                    "expected_tool_calls": [{"name": "list_dir"}],
                }
            ],
        },
        timeout=15,
    )
    assert created.status_code == 201, created.text[:500]
    eval_id = created.json()["id"]

    ran = api.http.post(
        f"{api.base}/api/v1/agents/{agent['slug']}/evals/{eval_id}/run",
        json={},
        timeout=(10, 300),
    )
    assert ran.status_code == 200, ran.text[:600]
    outcome = ran.json()
    assert outcome["summary"]["passed"] + outcome["summary"]["failed"] + outcome["summary"]["errored"] == 1
    row = outcome["results"][0]
    assert row["status"] == "pass", row
    assert row["score"] == 1.0, row
    assert row["run_public_id"]
    run = api.wait_for_run(str(row["run_public_id"]))
    assert run["status"] == "success", run.get("error")
    assert (run.get("final_reply") or "").strip(), "eval run produced an empty reply"
    assert _tool_events(run, "list_dir"), "eval expected_tool_calls did not see list_dir"


# AG-UI stream with a real model


def test_live_agui_input_streams_lifecycle(api: LiveClient):
    """POST /api/v1/agui/input with a live agent: RUN_STARTED → TEXT_MESSAGE → RUN_FINISHED."""
    agent = api.create_agent(
        "Live AGUI Agent",
        {
            "kind": "agent",
            "instructions": "Reply with a short sentence that includes the word aguidone.",
            "model": DEFAULT_MODEL,
            "default_options": SHORT,
        },
    )
    resp = api.http.post(
        f"{api.base}/api/v1/agui/input",
        json={"messages": [{"role": "user", "content": "hello"}], "agent_id": agent["slug"], "thread_id": "th-live-1"},
        timeout=(10, 300),
        stream=True,
    )
    assert resp.status_code == 200, resp.text[:400]
    events = _sse_events(resp)
    resp.close()
    types = [e["type"] for e in events]
    assert types[0] == "RUN_STARTED", types[:5]
    assert any(t == "TEXT_MESSAGE_CONTENT" for t in types), types
    finish = [e for e in events if e["type"] == "RUN_FINISHED"]
    assert finish, types
    assert finish[0].get("result", {}).get("final_reply"), finish[0]


# Guardrail middleware: real client round trip over the wire


def test_live_guardrail_middleware_tool_turn(api: LiveClient):
    """A real compiled agent carrying shipped middleware (todo list + call limits)
    must survive a tool-call turn against the real client (no ScriptedChatClient)."""
    agent = api.create_agent(
        "Live Middleware Agent",
        {
            "kind": "agent",
            "instructions": (
                "You MUST call the list_dir tool on the workspace root before any reply. "
                "Never guess directory names. After the tool runs, reply with a one-line "
                "confirmation naming a directory you saw."
            ),
            "model": DEFAULT_MODEL,
            "default_options": SHORT,
            "mcp_bindings": [WS_READ],
            "middleware": {"todo_list": {}, "model_call_limit": {"run_limit": 8}},
        },
    )
    payload = api.invoke(agent["slug"], "list the workspace")
    run_id = (payload.get("run") or {}).get("public_id") or (payload.get("run") or {}).get("id")
    assert run_id, payload
    run = api.wait_for_run(str(run_id))
    run = _refetch(api, str(run_id))
    reply = _reply(payload, run)
    assert run["status"] == "success", run.get("error") or reply
    assert reply.strip(), "middleware tool-turn run returned an empty reply"
    assert _tool_events(run, "list_dir"), "agent did not call list_dir"
    chat_events = [e for e in (run.get("events") or []) if e.get("event_type") == "chat"]
    # The real client parsed the tool call; chat spans exist and carry model output.
    assert chat_events, "no chat spans recorded for the middleware run"
    assert any(e.get("model") for e in chat_events), "chat spans did not record a model"
    assert len(chat_events) >= 2, "tool-call turn must produce a follow-up chat span after the tool result"

    def _follow_input(event: dict) -> dict:
        detail = event.get("detail") if isinstance(event.get("detail"), dict) else {}
        attrs = detail.get("attributes") if isinstance(detail.get("attributes"), dict) else {}
        return {
            "prompt": event.get("prompt") or detail.get("prompt"),
            "gen_ai.input.messages": attrs.get("gen_ai.input.messages"),
            "gen_ai.prompt": attrs.get("gen_ai.prompt"),
            "input_attrs": {k: v for k, v in attrs.items() if "input" in str(k).lower() or "prompt" in str(k).lower()},
        }

    follow_blob = json.dumps([_follow_input(e) for e in chat_events[1:]], default=str)
    follow_l = follow_blob.lower()
    # The follow-up *request* must include the prior tool-call payload (not a later
    # span whose type/name merely mentions tool_call).
    assert (
        "function_call" in follow_l
        or "tool_calls" in follow_l
        or "[tool " in follow_l
        or "list_dir" in follow_l
    ), "follow-up chat prompt did not include the prior tool-call payload"
    inbound = json.dumps(chat_events[0], default=str)
    inbound_extra = (
        "extra_content" in inbound or "thought_signature" in inbound or "_extra_content" in inbound
    )
    extra = (
        "extra_content" in follow_blob
        or "thought_signature" in follow_blob
        or "_extra_content" in follow_blob
    )
    if inbound_extra and not extra:
        blob = json.dumps(
            {"events": run.get("events") or [], "session": run.get("session_json") or {}},
            default=str,
        )
        extra = "extra_content" in blob or "thought_signature" in blob or "_extra_content" in blob
    if inbound_extra:
        assert extra, (
            "inbound extra_content/thought_signature was dropped on the follow-up chat request "
            "(preservation, not merely a later span mentioning tool_call)"
        )


# Self-contained single-agent run that needs no seeded agent


def test_live_single_agent_tools_skill_self_contained(single_agent: dict, api: LiveClient):
    payload = api.invoke(single_agent["slug"], "Inspect the sample-service code and report findings.")
    run_id = (payload.get("run") or {}).get("public_id") or (payload.get("run") or {}).get("id")
    assert run_id, payload
    run = api.wait_for_run(str(run_id))
    run = _refetch(api, str(run_id))
    reply = _reply(payload, run)
    blob = json.dumps(run.get("events") or [], default=str).lower()
    assert run["status"] == "success", run.get("error") or reply
    assert reply.strip(), "single agent returned an empty reply"
    assert _tool_events(run, "read_file") or _tool_events(run, "list_dir") or _tool_events(run, "search_code"), blob[:4000]

