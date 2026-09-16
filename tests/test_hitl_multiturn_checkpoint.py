"""Regression: HITL approval resume works within a multi-turn conversation.

This exercises the conversation path (conversation_id set), where the LangGraph
thread is namespaced by the conversation's UUID public_id. A HITL pause must
resume on the SAME thread (so intervening tool state survives), and a later
independent conversation must NOT inherit this thread's history.
"""

from __future__ import annotations

import asyncio
import sqlite3
import msgpack

from src.agent_platform.conversations.store import ConversationStore
from src.agent_platform.execution.run_store import RunStore
from src.agent_platform.plugins import register_builtin_plugins
from src.agent_platform.runtime.host import RuntimeHost
from src.agent_platform.runtime.scripted_client import ScriptedChatClient
from src.agent_platform.paths import checkpoint_dir_for


def _uq(cp_dir) -> str | None:
    db = cp_dir / "checkpoints.db"
    return str(db) if db.exists() else None


def test_hitl_resume_within_conversation(temp_db):
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

        # Create a conversation so the thread is namespaced by conversation UUID.
        conv = ConversationStore.create(user_id=1, title="HITL conv", agent_slug="approver")
        cid = int(conv["id"])
        conv_public = conv["public_id"]
        namespace = f"conv-{conv_public}"
        cp_dir = checkpoint_dir_for(namespace)

        run = RunStore.create(task="write file", definition_id=None, user_id=1, agent_slug="approver", conversation_id=cid)
        run_id = int(run["id"])

        host = RuntimeHost()

        # Turn 1: triggers HITL approval request. Use conversation_id.
        t1 = []
        async for event in host.stream_run(
            definition=definition,
            input_text="write file",
            run_id=run_id,
            conversation_id=cid,
            client=client,
        ):
            t1.append(event)
        t1_types = [e.get("type") for e in t1]
        assert "approval_request" in t1_types, t1_types
        paused = next(e for e in t1 if e.get("type") == "approval_request")
        assert [a["name"] for a in paused["action_requests"]] == ["dangerous_write"], paused

        # Resume the SAME run on the SAME conversation -> should continue, not restart.
        t2 = []
        async for event in host.stream_run(
            definition=definition,
            input_text="",
            run_id=run_id,
            conversation_id=cid,
            client=client,
            resume_payload={"decisions": [{"type": "approve"}]},
        ):
            t2.append(event)
        t2_types = [e.get("type") for e in t2]
        assert "done" in t2_types, t2_types
        saved = RunStore.get(run_id)
        assert saved["status"] == "success", saved["status"]

        # Multi-turn: a second run on the same conversation must share the thread.
        run2 = RunStore.create(task="do it again", definition_id=None, user_id=1, agent_slug="approver", conversation_id=cid)
        run2_id = int(run2["id"])
        t3 = []
        async for event in host.stream_run(
            definition=definition,
            input_text="do it again",
            run_id=run2_id,
            conversation_id=cid,
            client=ScriptedChatClient(responses=["done again"]),
        ):
            t3.append(event)
        assert "done" in [e.get("type") for e in t3]

        # Verify the thread checkpoint exists under the UUID namespace and
        # accumulated the multi-turn history (>= human+ai turns).
        cp = _uq(cp_dir)
        assert cp is not None, f"expected UUID checkpoint at {cp_dir}"
        conn = sqlite3.connect(cp)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("SELECT checkpoint, metadata FROM checkpoints ORDER BY checkpoint_id")
        rows = cur.fetchall()
        conn.close()
        last = msgpack.unpackb(rows[-1]["checkpoint"], raw=False)
        msgs = last["channel_values"]["messages"]
        # Turn1 human + tool-call + tool-msg + final + turn3 human + ai -> many messages
        assert len(msgs) >= 6, len(msgs)

        # Cross-conversation isolation: a fresh conversation must NOT inherit history.
        conv2 = ConversationStore.create(user_id=1, title="Other conv", agent_slug="approver")
        cid2 = int(conv2["id"])
        run3 = RunStore.create(task="fresh", definition_id=None, user_id=1, agent_slug="approver", conversation_id=cid2)
        run3_id = int(run3["id"])
        t4 = []
        async for event in host.stream_run(
            definition=definition,
            input_text="fresh",
            run_id=run3_id,
            conversation_id=cid2,
            client=ScriptedChatClient(responses=["fresh start"]),
        ):
            t4.append(event)
        assert "done" in [e.get("type") for e in t4]

        cp2 = _uq(checkpoint_dir_for(f"conv-{conv2['public_id']}"))
        assert cp2 is not None
        conn = sqlite3.connect(cp2)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("SELECT checkpoint, metadata FROM checkpoints ORDER BY checkpoint_id")
        rows2 = cur.fetchall()
        conn.close()
        last2 = msgpack.unpackb(rows2[-1]["checkpoint"], raw=False)
        msgs2 = last2["channel_values"]["messages"]
        # Fresh conversation must start with only its own human message (+ tool set up)
        assert len(msgs2) <= 4, f"fresh conversation leaked {len(msgs2)} messages"
        first = msgpack.unpackb(msgs2[0].data, raw=False)
        assert first[2]["content"] == "fresh"

    asyncio.run(_test())
