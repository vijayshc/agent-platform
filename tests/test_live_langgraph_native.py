"""Live tests for native LangGraph features (real app + real LLM).

These hit a running Flask process and the real model. Assertions require
tool execution and persisted memory — not just a 200.

  python -m pytest tests/test_live_langgraph_native.py -v
"""

from __future__ import annotations

from livehelpers import (
    DEFAULT_MODEL,
    SHORT,
    LiveClient,
    _has_tool_named,
    _refetch,
    _reply,
)


def test_live_memory_store_roundtrip(api: LiveClient):
    """remember_fact must execute, then recall_facts must return the stored fact."""
    agent = api.create_agent(
        "Memory Agent",
        {
            "kind": "agent",
            "runtime": "agent",
            "instructions": (
                "You MUST call remember_fact when the user asks you to remember something. "
                "You MUST call recall_facts when asked what you remember. "
                "Do not invent memories."
            ),
            "model": DEFAULT_MODEL,
            "default_options": SHORT,
        },
    )
    slug = agent["slug"]
    conv = api.create_conversation(slug, "memory")
    cid = conv.get("public_id") or conv.get("id")

    first = api.invoke(slug, "Remember this exactly: favorite color is teal.", conversation_id=cid)
    run1_id = (first.get("run") or {}).get("public_id") or (first.get("run") or {}).get("id")
    run1 = api.wait_for_run(str(run1_id))
    run1 = _refetch(api, str(run1_id))
    assert run1["status"] == "success", run1.get("error")
    assert _has_tool_named(run1, "remember_fact"), run1.get("events")

    second = api.invoke(slug, "Call recall_facts with query all. What color did I ask you to remember?", conversation_id=cid)
    run2_id = (second.get("run") or {}).get("public_id") or (second.get("run") or {}).get("id")
    run2 = api.wait_for_run(str(run2_id))
    run2 = _refetch(api, str(run2_id))
    reply = _reply(second, run2)
    assert run2["status"] == "success", run2.get("error") or reply
    assert _has_tool_named(run2, "recall_facts"), run2.get("events")
    assert "teal" in reply.lower(), reply


def test_live_injected_workspace_hitl(api: LiveClient):
    """dangerous_write uses InjectedState workspace_dir and native interrupt() HITL."""
    agent = api.create_agent(
        "Writer HITL",
        {
            "kind": "agent",
            "runtime": "agent",
            "instructions": "When asked to write a file, call dangerous_write. Do not narrate.",
            "function_tools": ["dangerous_write"],
            "hitl": {"approval": ["dangerous_write"]},
            "model": DEFAULT_MODEL,
            "default_options": SHORT,
        },
    )
    slug = agent["slug"]
    first = api.http.post(
        f"{api.base}/api/v1/agents/{slug}/invoke",
        json={"input": "Write hello-native to path native.txt using dangerous_write.", "stream": False},
        timeout=(10, 180),
    )
    assert first.status_code < 400, first.text[:800]
    body = first.json()
    assert body.get("status") == "awaiting_approval", body
    run_id = (body.get("run") or {}).get("public_id")
    assert run_id, body
    run = api.wait_for_run(str(run_id))
    assert run["status"] == "awaiting_approval", run.get("error")

    second = api.http.post(
        f"{api.base}/api/v1/runs/{run_id}/approvals",
        json={"decisions": [{"type": "approve"}], "stream": False},
        timeout=(10, 180),
    )
    assert second.status_code == 200, second.text[:800]
    run2 = api.wait_for_run(str(run_id), timeout=120)
    run2 = _refetch(api, str(run_id))
    assert run2["status"] == "success", run2.get("error")
    assert _has_tool_named(run2, "dangerous_write")
    files = " ".join(run2.get("workspace_files") or [])
    assert "native.txt" in files, files[:400]
