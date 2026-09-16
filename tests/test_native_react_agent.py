from __future__ import annotations

import asyncio

from src.agent_platform.execution.run_store import RunStore
from src.agent_platform.plugins import register_builtin_plugins
from src.agent_platform.runtime.host import RuntimeHost
from src.agent_platform.runtime.scripted_client import ScriptedChatClient


def _writer_def() -> dict:
    return {
        "kind": "agent",
        "name": "Writer",
        "config": {
            "kind": "agent",
            "instructions": "Write files.",
            "function_tools": ["dangerous_write"],
            "hitl": {"approval": ["dangerous_write"]},
        },
    }


def test_native_react_agent_invokes_tools(temp_db):
    """The agent graph compiled with create_react_agent must run tool calls natively.

    Guards the create_react_agent migration: a tool call directed by the LLM must
    execute through ToolNode (after approval) and its result must appear in the
    final transcript.
    """
    async def _test():
        register_builtin_plugins()
        client = ScriptedChatClient(responses=[
            {"type": "function_call", "name": "dangerous_write", "arguments": {"path": "a.txt", "content": "x"}, "call_id": "c1"},
            "wrote it",
        ])
        run = RunStore.create(task="write", definition_id=None, user_id=1, agent_slug="writer")
        run_id = int(run["id"])
        host = RuntimeHost()

        step1 = []
        async for event in host.stream_run(
            definition=_writer_def(),
            input_text="write",
            run_id=run_id,
            client=client,
        ):
            step1.append(event)
        step1_types = [e.get("type") for e in step1]
        assert "approval_request" in step1_types

        step2 = []
        async for event in host.stream_run(
            definition=_writer_def(),
            input_text="",
            run_id=run_id,
            client=client,
            resume_payload={"decisions": [{"type": "approve"}]},
        ):
            step2.append(event)

        # The tool_call is surfaced before the interrupt (step 1); the resume runs
        # the tool and streams the final assistant reply.
        types = [e.get("type") for e in step2]
        assert "tool_result" in types, "expected a tool_result event from natively executed tool"
        assert "done" in types
        done = next(e for e in step2 if e.get("type") == "done")
        assert "wrote it" in done.get("reply", "")

    asyncio.run(_test())


def test_native_react_agent_tokens_after_tool_roundtrip(temp_db):
    """Token streaming must still work after the loop rounds through a tool."""
    async def _test():
        register_builtin_plugins()
        client = ScriptedChatClient(responses=[
            {"type": "function_call", "name": "dangerous_write", "arguments": {"path": "a.txt", "content": "x"}, "call_id": "c1"},
            "final tiger",
        ])
        run = RunStore.create(task="write", definition_id=None, user_id=1, agent_slug="writer")
        run_id = int(run["id"])
        host = RuntimeHost()

        step1 = []
        async for event in host.stream_run(
            definition=_writer_def(),
            input_text="write",
            run_id=run_id,
            client=client,
        ):
            step1.append(event)

        tokens = []
        async for event in host.stream_run(
            definition=_writer_def(),
            input_text="",
            run_id=run_id,
            client=client,
            resume_payload={"decisions": [{"type": "approve"}]},
        ):
            if event.get("type") == "token":
                tokens.append(event.get("content") or event.get("delta") or "")

        assert "".join(tokens).strip() == "final tiger", tokens

    asyncio.run(_test())
