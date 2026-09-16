"""Run execution plumbing shared by the run and conversation endpoints.

The routes own HTTP concerns (auth, payload shapes, status codes); this module
owns the mechanics of actually driving a run: the cancel-event registry, the
blocking loop bridge, and the SSE response that streams a run's events while
persisting the assistant reply to its conversation.
"""

from __future__ import annotations

import asyncio
import json
import threading
from queue import Empty, Queue

from flask import Response, stream_with_context

from src.agent_platform.conversations.store import ConversationStore
from src.agent_platform.runtime.host import RuntimeHost

HOST = RuntimeHost()

# Process-local registry of in-flight run cancel events. Guarded by a lock so
# concurrent streaming threads (one per run) and the cancel endpoint can race
# safely.
_cancel_events: dict[int, threading.Event] = {}
_cancel_events_lock = threading.Lock()


def set_cancel_event(run_id: int, ev: threading.Event) -> None:
    with _cancel_events_lock:
        _cancel_events[int(run_id)] = ev


def pop_cancel_event(run_id: int) -> None:
    with _cancel_events_lock:
        _cancel_events.pop(int(run_id), None)


def get_cancel_event(run_id: int) -> threading.Event | None:
    with _cancel_events_lock:
        return _cancel_events.get(int(run_id))


def run_on_loop(coro_fn, *args, **kwargs):
    """Run a coroutine function to completion on a private event loop."""
    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        return loop.run_until_complete(coro_fn(*args, **kwargs))
    finally:
        loop.close()


def build_run_sse_response(
    run: dict,
    definition: dict,
    task: str = "",
    conversation_id: int | None = None,
    resume_payload: dict | None = None,
    client=None,
) -> Response:
    """Stream ``run`` as SSE (``text/event-stream``) from a worker thread."""
    rid = int(run["id"])
    cid = conversation_id
    pub_id = run["public_id"]

    cancel_ev = threading.Event()
    set_cancel_event(rid, cancel_ev)
    q: Queue = Queue()

    def _runner():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        async def _consume():
            final_text = ""
            try:
                async for event in HOST.stream_run(
                    definition=definition,
                    input_text=task,
                    run_id=rid,
                    conversation_id=int(cid) if cid else None,
                    resume_payload=resume_payload,
                    cancel_event=cancel_ev,
                    client=client,
                ):
                    event["run_id"] = rid
                    event["public_id"] = pub_id
                    event["runPublicId"] = pub_id
                    if event.get("type") == "token":
                        final_text += str(event.get("delta") or "")
                    elif event.get("type") == "text":
                        final_text = str(event.get("content") or "")
                    elif event.get("type") == "chat" and event.get("intermediate"):
                        # An intermediate model turn that precedes a tool call must
                        # not contribute to the persisted assistant reply. Reset so
                        # only the final answer is stored.
                        final_text = ""
                    q.put(event)
                if final_text.strip() and cid:
                    try:
                        ConversationStore.add_message(
                            int(cid), role="assistant", content=final_text.strip(), run_id=rid
                        )
                    except Exception:
                        pass
            except Exception as exc:
                q.put({
                    "type": "error",
                    "message": str(exc),
                    "run_id": rid,
                    "public_id": pub_id,
                    "runPublicId": pub_id,
                })
            finally:
                q.put(None)

        try:
            loop.run_until_complete(_consume())
            loop.run_until_complete(loop.shutdown_asyncgens())
        except Exception:
            pass
        finally:
            loop.close()
            pop_cancel_event(rid)
            from src.utils.database import remove_db_session

            remove_db_session()

    threading.Thread(target=_runner, daemon=True).start()

    def _event_stream():
        status_val = "resuming" if resume_payload else "starting"
        yield f"data: {json.dumps({'type': 'status', 'status': status_val, 'public_id': pub_id, 'runPublicId': pub_id, 'run_id': rid})}\n\n"
        while True:
            try:
                item = q.get(timeout=5.0)
                if item is None:
                    break
                yield f"data: {json.dumps(item)}\n\n"
            except Empty:
                yield ": keepalive\n\n"

    return Response(
        stream_with_context(_event_stream()),
        mimetype="text/event-stream",
        # No ``Connection`` header: this response has no length, so Werkzeug
        # closes the socket when the stream ends and says so. Forcing
        # ``keep-alive`` produced "keep-alive, close", which made HTTP clients
        # pool a socket the server had already closed -- their next request hung
        # on the dead connection until it timed out.
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        },
    )
