from __future__ import annotations

import json

from src.agent_platform.agui.mapper import AGUIMapper, CONFIRM_TOOL_NAME


def _fields(event) -> dict:
    return event.model_dump(by_alias=True, exclude_none=True)


def test_token_events_open_and_close_message():
    mapper = AGUIMapper()
    out = mapper.map_event({"type": "token", "content": "Hell"})
    assert len(out) == 2
    assert out[0].type.value == "TEXT_MESSAGE_START"
    assert out[1].type.value == "TEXT_MESSAGE_CONTENT"
    assert out[1].delta == "Hell"
    out = mapper.map_event({"type": "token", "content": "o"})
    assert len(out) == 1
    assert out[0].delta == "o"
    assert mapper.map_event({"type": "token", "content": ""}) == []
    closed = mapper.close()
    assert [e.type.value for e in closed] == ["TEXT_MESSAGE_END"]


def test_tool_call_and_result_roundtrip():
    mapper = AGUIMapper()
    mapper.set_user_text("read the file")
    out = mapper.map_event({"type": "tool_call", "tool_name": "read_file", "call_id": "c1", "arguments": {"path": "x"}})
    assert [e.type.value for e in out] == ["TOOL_CALL_START", "TOOL_CALL_ARGS"]
    assert out[0].tool_call_name == "read_file"
    assert json.loads(out[1].delta) == {"path": "x"}
    out = mapper.map_event({"type": "tool_result", "tool_name": "read_file", "call_id": "c1", "result": "body"})
    assert [e.type.value for e in out] == ["TOOL_CALL_END", "TOOL_CALL_RESULT"]
    assert out[1].content == "body"
    snapshot = mapper.snapshot()
    assert snapshot is not None
    messages = [m.model_dump(by_alias=True, exclude_none=True) for m in snapshot.messages]
    assert messages[0]["role"] == "user"
    assert messages[1]["role"] == "assistant"
    assert messages[1]["toolCalls"][0]["function"]["name"] == "read_file"
    assert messages[2]["role"] == "tool"
    assert messages[2]["toolCallId"] == "c1"


def test_tool_result_without_started_call_skips_end():
    mapper = AGUIMapper()
    out = mapper.map_event({"type": "tool_result", "tool_name": "t", "call_id": "c9", "result": "x"})
    assert [e.type.value for e in out] == ["TOOL_CALL_RESULT"]


def test_approval_request_carries_the_middleware_payload():
    """The CUSTOM event and interrupt carry action_requests/review_configs."""
    mapper = AGUIMapper()
    actions = [{"name": "dangerous_write", "args": {"path": "x"}, "description": "write x"}]
    configs = [{"action_name": "dangerous_write", "allowed_decisions": ["approve", "reject"]}]
    out = mapper.map_event(
        {
            "type": "approval_request",
            "action_requests": actions,
            "review_configs": configs,
            "agent": "Writer",
        }
    )
    types = [e.type.value for e in out]
    assert types == ["CUSTOM", "TOOL_CALL_START", "TOOL_CALL_ARGS", "TOOL_CALL_END"]
    assert out[0].name == "function_approval_request"
    assert out[0].value["action_requests"] == actions
    assert out[0].value["review_configs"] == configs
    assert out[1].tool_call_name == CONFIRM_TOOL_NAME
    assert len(mapper.interrupts) == 1
    assert mapper.interrupts[0]["id"].endswith(":0")
    assert mapper.interrupts[0]["value"]["type"] == "function_approval_request"
    assert mapper.interrupts[0]["value"]["action_index"] == 0
    assert mapper.interrupts[0]["value"]["action_requests"] == actions


def test_approval_request_with_multiple_actions_emits_one_interrupt_each():
    """One interrupt per action request keeps resume answers 1:1 with decisions."""
    mapper = AGUIMapper()
    actions = [
        {"name": "dangerous_write", "args": {"path": "a"}, "description": "write a"},
        {"name": "dangerous_write", "args": {"path": "b"}, "description": "write b"},
    ]
    configs = [{"action_name": "dangerous_write", "allowed_decisions": ["approve", "reject"]}]
    out = mapper.map_event({"type": "approval_request", "action_requests": actions, "review_configs": configs})
    types = [e.type.value for e in out]
    assert types == [
        "CUSTOM",
        "TOOL_CALL_START",
        "TOOL_CALL_ARGS",
        "TOOL_CALL_END",
        "TOOL_CALL_START",
        "TOOL_CALL_ARGS",
        "TOOL_CALL_END",
    ]
    assert [i["value"]["action_index"] for i in mapper.interrupts] == [0, 1]
    assert [i["value"]["action_requests"][i["value"]["action_index"]]["args"]["path"] for i in mapper.interrupts] == [
        "a",
        "b",
    ]


def test_agent_switch_emits_step_activity():
    mapper = AGUIMapper()
    out = mapper.map_event({"type": "agent_switch", "agent": "Design Reviewer"})
    assert [e.type.value for e in out] == ["STEP_STARTED"]
    out = mapper.map_event({"type": "agent_switch", "agent": "Backend Reviewer"})
    assert [e.type.value for e in out] == ["STEP_FINISHED", "STEP_STARTED"]
    assert out[0].step_name == "Design Reviewer"
    assert out[1].step_name == "Backend Reviewer"


def test_status_error_skill_load_and_done_mapping():
    mapper = AGUIMapper()
    out = mapper.map_event({"type": "status", "message": "running", "run_id": 5})
    assert out[0].type.value == "CUSTOM"
    assert out[0].name == "status"

    out = mapper.map_event({"type": "error", "message": "boom"})
    assert out[0].type.value == "RUN_ERROR"
    assert out[0].message == "boom"

    out = mapper.map_event({"type": "skill_load", "skill": "design-review", "agent": "Echo"})
    assert out[0].type.value == "CUSTOM"
    assert out[0].name == "skill_load"
    assert out[0].value["skill"] == "design-review"

    assert mapper.map_event({"type": "done", "run_id": 5}) == []


def test_snapshot_omits_empty_runs():
    mapper = AGUIMapper()
    assert mapper.snapshot() is None
    mapper.set_user_text("hello")
    snapshot = mapper.snapshot()
    assert snapshot is not None
    assert len(snapshot.messages) == 2