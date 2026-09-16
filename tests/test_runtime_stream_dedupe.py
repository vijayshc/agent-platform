"""The stream delivers each message exactly once.

LangGraph identifies a message by its ``id``: ``add_messages`` replaces a
repeated id instead of appending it. On a HITL resume the middleware re-writes
the interrupted turn's AIMessage with the same id, and a parent namespace
re-reports a subgraph's messages -- both are already on the user's screen, so
mapping them again repeated an assistant line before every approval.
"""

from __future__ import annotations

from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage

from src.agent_platform.runtime.events import map_agent_update

TOOL_CALL = {"name": "write_file", "args": {"path": "a.py"}, "id": "call_1", "type": "tool_call"}


def _types(events: list[dict]) -> list[str]:
    return [e["type"] for e in events]


def test_complete_message_is_delivered_once():
    delivered: set[str] = set()
    message = AIMessage(content="Rewriting order_service.py in full:", id="lc_run--1", tool_calls=[TOOL_CALL])

    first, _ = map_agent_update(message, delivered=delivered)
    assert _types(first) == ["token", "chat", "tool_call"]

    # The resumed HITL middleware re-writes the very same message.
    again, _ = map_agent_update(message, delivered=delivered)
    assert again == []


def test_message_ids_already_in_the_thread_are_skipped():
    # What the state holds before a resume: the interrupted turn's message.
    delivered = {"lc_run--1"}
    events, _ = map_agent_update(AIMessage(content="old line", id="lc_run--1"), delivered=delivered)
    assert events == []


def test_chunks_stream_and_do_not_consume_the_message_id():
    delivered: set[str] = set()

    chunk, _ = map_agent_update(AIMessageChunk(content="Rewriting ", id="lc_run--1"), delivered=delivered)
    assert _types(chunk) == ["token"]

    # The complete message still arrives with the tool calls the UI needs.
    complete, _ = map_agent_update(
        AIMessage(content="Rewriting order_service.py in full:", id="lc_run--1", tool_calls=[TOOL_CALL]),
        delivered=delivered,
    )
    assert _types(complete) == ["token", "chat", "tool_call"]


def test_tool_message_reported_by_both_stream_modes_is_delivered_once():
    delivered: set[str] = set()
    tool = ToolMessage(content="wrote 3 bytes", tool_call_id="call_1", id="tool-1")

    assert _types(map_agent_update(tool, delivered=delivered)[0]) == ["tool_result"]
    assert map_agent_update(tool, delivered=delivered)[0] == []


def test_messages_without_an_id_are_never_deduped():
    delivered: set[str] = set()
    assert _types(map_agent_update(AIMessage(content="hi"), delivered=delivered)[0]) == ["token", "chat"]
    assert _types(map_agent_update(AIMessage(content="hi"), delivered=delivered)[0]) == ["token", "chat"]


def test_without_a_delivered_set_nothing_is_deduped():
    message = AIMessage(content="x", id="lc_run--1")
    assert _types(map_agent_update(message)[0]) == ["token", "chat"]
    assert _types(map_agent_update(message)[0]) == ["token", "chat"]
