from __future__ import annotations

import asyncio
from src.agent_platform.execution.run_store import RunStore
from src.agent_platform.plugins import register_builtin_plugins
from src.agent_platform.runtime.host import RuntimeHost
from src.agent_platform.runtime.scripted_client import ScriptedChatClient


def test_host_stream_run_basic(temp_db):
    async def _test():
        register_builtin_plugins()
        client = ScriptedChatClient(responses=["Hello from LangGraph agent!"])
        definition = {
            "kind": "agent",
            "name": "Greeter",
            "config": {
                "kind": "agent",
                "instructions": "Greet the user.",
            },
        }
        run = RunStore.create(task="hello", definition_id=None, user_id=1, agent_slug="greeter")
        run_id = int(run["id"])

        host = RuntimeHost()
        events = []
        async for event in host.stream_run(
            definition=definition,
            input_text="hello",
            run_id=run_id,
            client=client,
        ):
            events.append(event)

        types = [e.get("type") for e in events]
        assert "status" in types
        assert "token" in types or "done" in types
        assert "done" in types
        done_event = next(e for e in events if e.get("type") == "done")
        assert "Hello from LangGraph" in done_event.get("reply", "")

    asyncio.run(_test())


def test_host_approval_pause_and_resume(temp_db):
    async def _test():
        register_builtin_plugins()
        client = ScriptedChatClient(responses=[
            {"type": "function_call", "name": "dangerous_write", "arguments": {"path": "out.txt", "content": "hi"}, "call_id": "c1"},
            "File was written successfully after approval.",
        ])
        definition = {
            "kind": "agent",
            "name": "Approver",
            "config": {
                "kind": "agent",
                "instructions": "Write dangerous files.",
                "function_tools": ["dangerous_write"],
                "hitl": {"approval": ["dangerous_write"]},
            },
        }
        run = RunStore.create(task="write file", definition_id=None, user_id=1, agent_slug="approver")
        run_id = int(run["id"])

        host = RuntimeHost()
        step1_events = []
        async for event in host.stream_run(
            definition=definition,
            input_text="write file",
            run_id=run_id,
            client=client,
        ):
            step1_events.append(event)

        step1_types = [e.get("type") for e in step1_events]
        assert "approval_request" in step1_types
        paused = next(e for e in step1_events if e.get("type") == "approval_request")
        # The event carries HumanInTheLoopMiddleware's own payload unchanged.
        assert [a["name"] for a in paused["action_requests"]] == ["dangerous_write"]
        assert paused["review_configs"][0]["action_name"] == "dangerous_write"
        assert paused["action_requests"][0]["args"] == {"path": "out.txt", "content": "hi"}

        # Resume with approval
        step2_events = []
        async for event in host.stream_run(
            definition=definition,
            input_text="",
            run_id=run_id,
            client=client,
            resume_payload={"decisions": [{"type": "approve"}]},
        ):
            step2_events.append(event)

        step2_types = [e.get("type") for e in step2_events]
        assert "done" in step2_types
        saved_run = RunStore.get(run_id)
        assert saved_run["status"] == "success"

    asyncio.run(_test())
