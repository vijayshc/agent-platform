from __future__ import annotations

import asyncio
from src.agent_platform.catalog.store import DefinitionStore
from src.agent_platform.execution.run_store import RunStore
from src.agent_platform.plugins import register_builtin_plugins
from src.agent_platform.runtime.host import RuntimeHost
from src.agent_platform.runtime.scripted_client import ScriptedChatClient
from src.services.otel_observability import (
    SpanTracker,
    start_span_capture,
    stop_span_capture,
    setup_observability,
)
from src.services.phoenix_service import get_phoenix_url, is_phoenix_healthy


def test_phoenix_service_url():
    url = get_phoenix_url()
    assert url.startswith("http://")
    assert "6006" in url


def test_span_capture_lifecycle():
    tracker = start_span_capture(run_id=999, agent_name="Developer", conversation_id="conv-123")
    assert isinstance(tracker, SpanTracker)
    assert tracker.run_id == 999
    stop_span_capture(tracker)


def test_langgraph_run_with_phoenix_instrumentation(temp_db):
    async def _test():
        register_builtin_plugins()
        client = ScriptedChatClient(responses=["Traced output from LangGraph"])
        definition = {
            "kind": "agent",
            "name": "TracedAgent",
            "config": {
                "kind": "agent",
                "instructions": "Respond accurately.",
            },
        }
        run = RunStore.create(task="trace test", definition_id=None, user_id=1, agent_slug="traced_agent")
        run_id = int(run["id"])

        host = RuntimeHost()
        events = []
        async for event in host.stream_run(
            definition=definition,
            input_text="trace test",
            run_id=run_id,
            client=client,
        ):
            events.append(event)

        types = [e.get("type") for e in events]
        assert "done" in types
        done_event = next(e for e in events if e.get("type") == "done")
        assert "Traced output" in done_event.get("reply", "")

    asyncio.run(_test())


def test_conversation_telemetry_uses_uuid_session_id(temp_db):
    async def _test():
        from src.agent_platform.conversations.store import ConversationStore

        register_builtin_plugins()
        client = ScriptedChatClient(responses=["Hello from session turn 1"])
        definition = {
            "kind": "agent",
            "name": "SessionTracedAgent",
            "config": {
                "kind": "agent",
                "instructions": "Respond accurately.",
            },
        }

        # Create conversation with auto-generated UUID public_id
        conv = ConversationStore.create(user_id=1, title="Test Session Conv", agent_slug="session_agent")
        cid = int(conv["id"])
        public_id = conv["public_id"]
        assert public_id and len(public_id) > 10

        run1 = RunStore.create(task="turn 1", definition_id=None, user_id=1, agent_slug="session_agent", conversation_id=cid)
        host = RuntimeHost()

        captured_configs = []
        orig_compile = host.compile

        async def _mock_compile(*args, **kwargs):
            c = await orig_compile(*args, **kwargs)
            actual_astream = c.runnable.astream

            async def _inspecting_astream(input_val, config=None, **astream_kwargs):
                captured_configs.append(dict(config or {}))
                async for chunk in actual_astream(input_val, config=config, **astream_kwargs):
                    yield chunk

            c.runnable.astream = _inspecting_astream
            return c

        host.compile = _mock_compile

        async for _ in host.stream_run(
            definition=definition,
            input_text="turn 1",
            run_id=int(run1["id"]),
            conversation_id=cid,
            client=client,
        ):
            pass

        assert len(captured_configs) == 1
        cfg = captured_configs[0]
        meta = cfg.get("metadata", {})
        # Must match conversation UUID public_id, NOT the integer cid
        assert meta.get("session_id") == public_id
        assert meta.get("conversation_id") == public_id
        assert str(cid) != public_id

    asyncio.run(_test())

