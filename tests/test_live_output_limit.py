"""Live: a model that hits its output cap fails the run, loudly and once.

Regression this guards: when the cap was reached mid-reasoning the model
returned an empty message, and the run reported -- and persisted -- the
*previous* turn's answer, so a multi-turn conversation looked like the agent
repeating itself instead of reporting the failure.

Runs against the REAL app (see tests/livehelpers.py) and the REAL model.
  python -m pytest tests/test_live_output_limit.py -q
"""

from __future__ import annotations

from livehelpers import DEFAULT_MODEL, LiveClient

#: Small enough that a long answer cannot fit, large enough that a one-word
#: answer does.
TIGHT_CAP = {"temperature": 0.2, "max_tokens": 300}
LONG_TASK = "Write a 4000-word essay on distributed consensus. Do not stop early."
SHORT_TASK = "Reply with exactly one word: READY"


def _cap_agent(api: LiveClient, name: str) -> dict:
    return api.create_agent(
        name,
        {
            "kind": "agent",
            "runtime": "agent",
            "instructions": "You are a writer. Answer every request in full.",
            "model": DEFAULT_MODEL,
            "default_options": TIGHT_CAP,
        },
    )


def _assistant_messages(api: LiveClient, conversation_id: str) -> list[dict]:
    r = api.http.get(f"{api.base}/api/v1/conversations/{conversation_id}/messages", timeout=30)
    assert r.status_code < 400, r.text[:400]
    return [m for m in (r.json().get("messages") or []) if m.get("role") == "assistant"]


def test_output_cap_is_reported_as_a_run_error(api: LiveClient):
    agent = _cap_agent(api, "Live Cap Single")

    payload = api.invoke(agent["slug"], LONG_TASK)
    run = payload["run"]

    assert run["status"] == "error", run
    error = (run.get("error") or "").lower()
    assert "output limit" in error, error
    assert "max output tokens" in error, error
    assert not (run.get("final_reply") or "").strip(), "a failed turn must not report a reply"
    assert any(e.get("type") == "error" for e in payload["sse"]), payload["sse"][-6:]


def test_multiturn_failed_turn_never_repeats_the_previous_answer(api: LiveClient):
    agent = _cap_agent(api, "Live Cap Multiturn")
    conv = api.create_conversation(agent["slug"], "output cap multiturn")
    cid = str(conv.get("public_id") or conv.get("id"))

    first = api.invoke(agent["slug"], SHORT_TASK, conversation_id=cid)
    assert first["status"] == "success", first
    first_reply = (first["run"].get("final_reply") or "").strip()
    assert first_reply, first

    second = api.invoke(agent["slug"], LONG_TASK, conversation_id=cid)
    run = second["run"]
    assert run["status"] == "error", run
    assert "output limit" in (run.get("error") or "").lower(), run.get("error")
    assert not (run.get("final_reply") or "").strip(), "the previous answer was replayed"
    assert first_reply not in (run.get("final_reply") or "")

    # The transcript keeps exactly one copy of the first answer: the failed turn
    # must not persist the previous answer under its own run.
    replies = [m["content"].strip() for m in _assistant_messages(api, cid)]
    assert replies == [first_reply], replies
