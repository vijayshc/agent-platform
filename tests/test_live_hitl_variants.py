"""Live HITL variants + file-history two-turn (real model).

Covers:
- skill-provider HITL resume (write_file)
- file-history HITL resume (write_file)
- function-tool HITL (dangerous_write always_require)
- history_provider=file two-turn WITHOUT HITL (turn 2 must see turn 1)

  python -m pytest tests/test_live_hitl_variants.py -q
"""

from __future__ import annotations

from livehelpers import (
    DEFAULT_MODEL,
    SHORT,
    WS_WRITE,
    LiveClient,
    _refetch,
    _reply,
    _tool_events,
)

#: A resumed run has to reason over the tool result and then answer, which does
#: not fit SHORT's 300-token cap. Hitting that cap now fails the run: a truncated
#: answer is reported as an error instead of being passed off as the reply.
RESUME_BUDGET = {"temperature": 0.1, "max_tokens": 2000}


def test_live_hitl_skill_resume_writes_file(api: LiveClient):
    """Skill-provider agent calls a write tool; approval must resume and persist history."""
    agent = api.create_agent(
        "Live Skill Writer",
        {
            "kind": "agent",
            "instructions": (
                "Load the design-review skill, then you MUST call write_file with path "
                "notes/skill-note.md and content SKILL-NOTE-OK. Do not answer before the "
                "tool runs."
            ),
            "model": DEFAULT_MODEL,
            "default_options": RESUME_BUDGET,
            "mcp_bindings": [WS_WRITE],
            "maf_skill_ids": ["design-review"],
        },
    )
    first = api.http.post(
        f"{api.base}/api/v1/agents/{agent['slug']}/invoke",
        json={"input": "write the note", "stream": False},
        timeout=(10, 300),
    )
    assert first.status_code < 400, first.text[:500]
    body = first.json()
    assert body.get("status") == "awaiting_approval", body
    run_id = (body.get("run") or {}).get("public_id")
    assert run_id, body

    run = api.wait_for_run(str(run_id))
    assert run["status"] == "awaiting_approval", run.get("error")
    pending = run.get("pending_json") or {}
    assert pending.get("action_requests"), pending
    assert pending["action_requests"][0]["name"] == "write_file", pending

    second = api.http.post(
        f"{api.base}/api/v1/runs/{run_id}/approvals",
        json={"decisions": [{"type": "approve"}], "stream": False},
        timeout=(10, 300),
    )
    assert second.status_code == 200, second.text[:500]
    run = api.wait_for_run(str(run_id), timeout=max(30, 120))
    run = _refetch(api, str(run_id))
    reply = run.get("final_reply") or ""
    assert run["status"] == "success", run.get("error") or reply
    assert _tool_events(run, "write_file"), "resumed run did not execute write_file"
    files = " ".join(run.get("workspace_files") or [])
    assert "notes/skill-note.md" in files or "skill-note.md" in files, files[:400]


def test_live_hitl_file_history_resume(api: LiveClient):
    """history_provider: file + HITL: pause, approve, resume, tool executes."""
    agent = api.create_agent(
        "Live File Writer",
        {
            "kind": "agent",
            "instructions": (
                "You MUST call write_file with path notes/file-note.md and content FILE-NOTE-OK. "
                "Do not answer before the tool runs."
            ),
            "model": DEFAULT_MODEL,
            "default_options": SHORT,
            "mcp_bindings": [WS_WRITE],
            "history_provider": "file",
        },
    )
    first = api.http.post(
        f"{api.base}/api/v1/agents/{agent['slug']}/invoke",
        json={"input": "write the note", "stream": False},
        timeout=(10, 300),
    )
    assert first.status_code < 400, first.text[:500]
    body = first.json()
    assert body.get("status") == "awaiting_approval", body
    run_id = (body.get("run") or {}).get("public_id")
    assert run_id, body

    run = api.wait_for_run(str(run_id))
    assert run["status"] == "awaiting_approval", run.get("error")
    pending = run.get("pending_json") or {}
    assert pending.get("action_requests"), pending
    assert pending["action_requests"][0]["name"] == "write_file", pending

    second = api.http.post(
        f"{api.base}/api/v1/runs/{run_id}/approvals",
        json={"decisions": [{"type": "approve"}], "stream": False},
        timeout=(10, 300),
    )
    assert second.status_code == 200, second.text[:500]
    run = api.wait_for_run(str(run_id), timeout=max(30, 120))
    run = _refetch(api, str(run_id))
    reply = run.get("final_reply") or ""
    assert run["status"] == "success", run.get("error") or reply
    assert _tool_events(run, "write_file"), "file-history resumed run did not execute write_file"
    files = " ".join(run.get("workspace_files") or [])
    assert "notes/file-note.md" in files or "file-note.md" in files, files[:400]


def test_live_hitl_function_tool_approval(api: LiveClient):
    """Function-tool dangerous_write always requires HITL; approve then the write lands."""
    agent = api.create_agent(
        "Live Function Writer",
        {
            "kind": "agent",
            "instructions": (
                "You MUST call dangerous_write with path vault/seal.txt and content SEALED. "
                "Do not invent a success message before the tool returns."
            ),
            "model": DEFAULT_MODEL,
            "default_options": RESUME_BUDGET,
            "function_tools": ["dangerous_write"],
        },
    )
    first = api.http.post(
        f"{api.base}/api/v1/agents/{agent['slug']}/invoke",
        json={"input": "seal the ledger", "stream": False},
        timeout=(10, 300),
    )
    assert first.status_code < 400, first.text[:500]
    body = first.json()
    assert body.get("status") == "awaiting_approval", body
    run_id = (body.get("run") or {}).get("public_id")
    assert run_id, body

    run = api.wait_for_run(str(run_id))
    assert run["status"] == "awaiting_approval", run.get("error")
    pending = run.get("pending_json") or {}
    assert pending.get("action_requests"), pending
    assert pending["action_requests"][0]["name"] == "dangerous_write", pending

    second = api.http.post(
        f"{api.base}/api/v1/runs/{run_id}/approvals",
        json={"decisions": [{"type": "approve"}], "stream": False},
        timeout=(10, 300),
    )
    assert second.status_code == 200, second.text[:500]
    run = api.wait_for_run(str(run_id), timeout=max(30, 120))
    run = _refetch(api, str(run_id))
    reply = run.get("final_reply") or ""
    assert run["status"] == "success", run.get("error") or reply
    assert _tool_events(run, "dangerous_write"), "function-tool run did not execute dangerous_write"
    files = " ".join(run.get("workspace_files") or [])
    assert "vault/seal.txt" in files or "seal.txt" in files, files[:400]


def _stream_events(api: LiveClient, path: str, body: dict) -> list[dict]:
    """Read a whole SSE response (the run pauses, so the stream terminates)."""
    import json

    events: list[dict] = []
    r = api.http.post(f"{api.base}{path}", json=body, stream=True, timeout=(10, 300))
    assert r.status_code < 400, r.text[:500]
    for raw in r.iter_lines(decode_unicode=True):
        if not raw or not str(raw).startswith("data:"):
            continue
        try:
            events.append(json.loads(str(raw)[5:].strip()))
        except json.JSONDecodeError:
            continue
    r.close()
    return events


def _chat_lines(events: list[dict]) -> list[str]:
    return [str(e.get("content")) for e in events if e.get("type") == "chat" and e.get("content")]


def _tool_calls(events: list[dict]) -> list[tuple[str, str]]:
    return [(str(e.get("tool_name")), str(e.get("arguments"))) for e in events if e.get("type") == "tool_call"]


def test_live_hitl_resume_does_not_replay_the_interrupted_turn(api: LiveClient):
    """Regression: a resume re-streamed the interrupted turn's text and tool call.

    The middleware re-writes the interrupted AIMessage (same id) when the graph
    continues, and the mapper streamed it again -- so the transcript showed the
    same assistant line before every approval, even though the model never
    produced it twice (Phoenix shows one call per turn).
    """
    agent = api.create_agent(
        "Live Resume Dedupe",
        {
            "kind": "agent",
            "instructions": (
                "Call dangerous_write exactly twice, one call per turn and in order: "
                "first vault/one.txt with content ONE, then vault/two.txt with content TWO. "
                "Write one short sentence before each call. Never batch the two calls."
            ),
            "model": DEFAULT_MODEL,
            "default_options": RESUME_BUDGET,
            "function_tools": ["dangerous_write"],
        },
    )
    first = _stream_events(
        api, f"/api/v1/agents/{agent['slug']}/invoke", {"input": "write both notes", "stream": True}
    )
    run_id = next((e.get("public_id") for e in first if e.get("public_id")), None)
    assert run_id, first[-4:]
    run = api.wait_for_run(str(run_id))
    assert run["status"] == "awaiting_approval", run.get("error")

    first_lines = _chat_lines(first)
    first_calls = _tool_calls(first)
    assert first_lines and first_calls, first

    resumed = _stream_events(
        api,
        f"/api/v1/runs/{run_id}/approvals",
        {"decisions": [{"type": "approve"}], "stream": True},
    )
    resumed_lines = _chat_lines(resumed)
    resumed_calls = _tool_calls(resumed)

    # The interrupted turn is history: only what this resume produced is streamed.
    assert first_lines[-1] not in resumed_lines, resumed_lines
    assert first_calls[0] not in resumed_calls, resumed_calls
    # ...and the resume still reports its own work.
    assert resumed_calls, resumed
    assert any(e.get("type") == "tool_result" for e in resumed), resumed


def test_live_file_history_two_turn_without_hitl(api: LiveClient):
    """Multi-turn: turn 2 must recall turn 1 from the LangGraph checkpoint thread."""
    agent = api.create_agent(
        "Live File History Chat",
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
    conv = api.create_conversation(agent["slug"], "live-file-hist")
    conv_id = conv.get("public_id") or conv.get("id")
    assert conv_id, conv

    turn1 = api.http.post(
        f"{api.base}/api/v1/conversations/{conv_id}/messages",
        json={"input": "Remember this secret token: maple-file-77.", "stream": False},
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
    assert "maple-file-77" in reply2, f"turn 2 did not see turn 1: {reply2!r}"

    # Conversation memory is LangGraph thread checkpoints, not JSONL files.
