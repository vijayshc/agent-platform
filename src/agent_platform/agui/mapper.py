"""Map the platform's custom SSE events (runtime/events.py) to AG-UI protocol events.

The additive AG-UI transport keeps RuntimeHost and its custom SSE taxonomy
(``runtime/events.py``) as the single execution surface; this module is the
boundary mapper that translates each custom SSE event into one or more AG-UI
protocol events (https://github.com/ag-ui/ag-ui-protocol).

Mapping table (what AG-UI carries natively vs. custom):

+---------------------+----------------------------------------------------------+
| platform SSE event  | AG-UI event(s)                                            |
+---------------------+----------------------------------------------------------+
| status              | CUSTOM ``status`` (protocol has no lifecycle-status event)|
| token               | TEXT_MESSAGE_START / TEXT_MESSAGE_CONTENT / _END          |
| tool_call           | TOOL_CALL_START + TOOL_CALL_ARGS                          |
| tool_result         | TOOL_CALL_END + TOOL_CALL_RESULT                          |
| approval_request    | CUSTOM ``function_approval_request`` (the middleware's    |
|                     | ``action_requests``/``review_configs`` payload) + one     |
|                     | synthetic ``confirm_changes`` TOOL_CALL_* and interrupt   |
|                     | per action request                                        |
| skill_load          | CUSTOM ``skill_load`` (AG-UI has no skill event type)     |
| agent_switch        | STEP_STARTED / STEP_FINISHED (executor step activity)     |
| error               | RUN_ERROR                                                 |
| done                | RUN_FINISHED (emitted by the bridge, carries interrupts)  |
+---------------------+----------------------------------------------------------+

AG-UI types that genuinely cannot carry a distinct event are carried as
CUSTOM events with stable names so third-party clients can still render them
(skill_load). The stream is closed by ``src.agent_platform.agui.bridge`` with a
MESSAGES_SNAPSHOT and a RUN_FINISHED carrying any pending approval interrupts.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from ag_ui.core import (
    BaseEvent,
    CustomEvent,
    MessagesSnapshotEvent,
    RunErrorEvent,
    StepFinishedEvent,
    StepStartedEvent,
    TextMessageContentEvent,
    TextMessageEndEvent,
    TextMessageStartEvent,
    ToolCallArgsEvent,
    ToolCallEndEvent,
    ToolCallResultEvent,
    ToolCallStartEvent,
)

CONFIRM_TOOL_NAME = "confirm_changes"


def _event_id() -> str:
    return str(uuid.uuid4())


def _json_args(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value)


class AGUIMapper:
    """Stateful translator from platform SSE events to AG-UI events.

    One instance per run: it tracks the open text message, started tool calls,
    current executor step, and the interrupt list so that (a) TOOL_CALL_END is
    only emitted for calls this run actually started, and (b) RUN_FINISHED can
    carry the pending approval interrupts.
    """

    def __init__(self) -> None:
        self.message_id: str | None = None
        self.steps: list[str] = []
        self.started_calls: set[str] = set()
        self.tool_calls: list[dict[str, Any]] = []
        self.tool_results: list[dict[str, Any]] = []
        self.interrupts: list[dict[str, Any]] = []
        self.user_text: str = ""
        self.agent_step: str | None = None

    def set_user_text(self, text: str) -> None:
        """Record the user turn so the final MESSAGES_SNAPSHOT is complete."""
        self.user_text = text or ""

    def map_event(self, event: dict[str, Any]) -> list[BaseEvent]:
        """Translate a single platform SSE event into AG-UI events."""
        etype = event.get("type")
        if etype == "token":
            return self._token(event)
        if etype == "tool_call":
            return self._tool_call(event)
        if etype == "tool_result":
            return self._tool_result(event)
        if etype == "approval_request":
            return self._approval_request(event)
        if etype == "skill_load":
            return [CustomEvent(name="skill_load", value={"skill": event.get("skill"), "agent": event.get("agent")})]
        if etype == "agent_switch":
            return self._agent_switch(event)
        if etype == "status":
            return self._status(event)
        if etype == "error":
            return [RunErrorEvent(message=str(event.get("message") or "run failed"), code="run_error")]
        if etype == "done":
            # RUN_FINISHED is emitted by the bridge (it owns run lifecycle).
            return []
        return []

    def _token(self, event: dict[str, Any]) -> list[BaseEvent]:
        chunk = str(event.get("content") or "")
        if not chunk:
            return []
        out: list[BaseEvent] = []
        if self.message_id is None:
            self.message_id = _event_id()
            out.append(TextMessageStartEvent(message_id=self.message_id, role="assistant"))
        out.append(TextMessageContentEvent(message_id=self.message_id, delta=chunk))
        return out

    def _close_text(self) -> list[BaseEvent]:
        if self.message_id is None:
            return []
        out = [TextMessageEndEvent(message_id=self.message_id)]
        self.message_id = None
        return out

    def _tool_call(self, event: dict[str, Any]) -> list[BaseEvent]:
        call_id = str(event.get("call_id") or _event_id())
        name = event.get("tool_name") or "tool"
        args = _json_args(event.get("arguments"))
        self.started_calls.add(call_id)
        self.tool_calls.append(
            {
                "id": call_id,
                "type": "function",
                "function": {"name": name, "arguments": args},
            }
        )
        out: list[BaseEvent] = [
            ToolCallStartEvent(tool_call_id=call_id, tool_call_name=str(name))
        ]
        if event.get("arguments") is not None:
            out.append(ToolCallArgsEvent(tool_call_id=call_id, delta=args))
        return out

    def _tool_result(self, event: dict[str, Any]) -> list[BaseEvent]:
        call_id = str(event.get("call_id") or "")
        content = str(event.get("result") or "")
        out: list[BaseEvent] = []
        if call_id and call_id in self.started_calls:
            out.append(ToolCallEndEvent(tool_call_id=call_id))
        result_id = _event_id()
        self.tool_results.append(
            {
                "id": result_id,
                "role": "tool",
                "toolCallId": call_id,
                "content": content,
            }
        )
        if call_id:
            out.append(
                ToolCallResultEvent(
                    message_id=result_id,
                    tool_call_id=call_id,
                    content=content,
                    role="tool",
                )
            )
        return out

    def _approval_request(self, event: dict[str, Any]) -> list[BaseEvent]:
        """Map a HITL approval to the AG-UI interrupt convention.

        AG-UI has no approval event type; the installed
        ``agent_framework_ag_ui`` emitter surfaces approvals the same way: a
        CUSTOM ``function_approval_request`` event plus synthetic
        ``confirm_changes`` tool calls plus RUN_FINISHED interrupts so the
        client can render and answer them.

        The event carries ``HumanInTheLoopMiddleware``'s own payload —
        ``action_requests`` and ``review_configs``. One interrupt is emitted per
        action request (id ``<request_id>:<index>``, ``action_index`` on the
        value) so answers map 1:1 onto the decisions the middleware expects.
        """
        actions = [a for a in event.get("action_requests") or [] if isinstance(a, dict)]
        configs = [c for c in event.get("review_configs") or [] if isinstance(c, dict)]
        if not actions:
            # Spans persisted before the runtime moved to the library middleware
            # carry no action_requests; there is nothing to render or resume.
            return []
        req_id = str(event.get("request_id") or _event_id())
        out: list[BaseEvent] = [
            CustomEvent(
                name="function_approval_request",
                value={"id": req_id, "action_requests": actions, "review_configs": configs},
            )
        ]
        for index, action in enumerate(actions):
            name = action.get("name")
            arguments = action.get("args")
            interrupt_id = f"{req_id}:{index}"
            self.interrupts.append(
                {
                    "id": interrupt_id,
                    "value": {
                        "type": "function_approval_request",
                        "action_index": index,
                        "action_requests": actions,
                        "review_configs": configs,
                    },
                }
            )
            confirm_args = {
                "function_name": name,
                "function_call_id": interrupt_id,
                "function_arguments": arguments or {},
                "steps": [{"description": f"Execute {name}", "status": "enabled"}],
            }
            out.extend(
                [
                    ToolCallStartEvent(tool_call_id=interrupt_id, tool_call_name=CONFIRM_TOOL_NAME),
                    ToolCallArgsEvent(tool_call_id=interrupt_id, delta=json.dumps(confirm_args)),
                    ToolCallEndEvent(tool_call_id=interrupt_id),
                ]
            )
            self.tool_calls.append(
                {
                    "id": interrupt_id,
                    "type": "function",
                    "function": {"name": CONFIRM_TOOL_NAME, "arguments": json.dumps(confirm_args)},
                }
            )
        return out

    def _agent_switch(self, event: dict[str, Any]) -> list[BaseEvent]:
        out: list[BaseEvent] = []
        if self.agent_step:
            out.append(StepFinishedEvent(step_name=self.agent_step))
        step = event.get("agent") or event.get("agent_name") or ""
        self.agent_step = str(step) if step else None
        if self.agent_step:
            out.append(StepStartedEvent(step_name=self.agent_step))
        return out

    def _status(self, event: dict[str, Any]) -> list[BaseEvent]:
        message = event.get("message")
        if not message:
            return []
        return [
            CustomEvent(
                name="status",
                value={"message": str(message), "run_id": event.get("run_id"), "state": event.get("state")},
            )
        ]

    def close(self) -> list[BaseEvent]:
        """Close open text message and executor steps at end of stream."""
        out = self._close_text()
        if self.agent_step:
            out.append(StepFinishedEvent(step_name=self.agent_step))
            self.agent_step = None
        self.steps = []
        return out

    def snapshot(self) -> MessagesSnapshotEvent | None:
        """Build the final MESSAGES_SNAPSHOT from what this run streamed."""
        messages: list[dict[str, Any]] = []
        if self.user_text:
            messages.append({"id": _event_id(), "role": "user", "content": self.user_text})
        assistant: dict[str, Any] = {"id": _event_id(), "role": "assistant"}
        if self.tool_calls:
            assistant["toolCalls"] = [dict(tc) for tc in self.tool_calls]
        if self.tool_calls or self.user_text or self.tool_results:
            messages.append(assistant)
        messages.extend(self.tool_results)
        if len(messages) <= 1 and not self.tool_calls:
            return None
        return MessagesSnapshotEvent(messages=messages)
