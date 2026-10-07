from __future__ import annotations

import logging
import time
import uuid
from typing import Any, AsyncIterator

from langchain_core.messages import HumanMessage
from langgraph.types import Command

from src.agent_platform.conversations.store import ConversationStore
from src.agent_platform.execution.run_store import RunStore
from src.agent_platform.execution.span_sink import SpanSink
from src.agent_platform.execution.stream_events import record_stream_event
from src.agent_platform.paths import checkpoint_dir_for, run_workspace_dir
from src.agent_platform.runtime.checkpointing import (
    checkpoint_namespace,
    memory_enabled,
    sqlite_checkpoint_storage,
    sqlite_store_storage,
)
from src.agent_platform.runtime.compiler import (
    DEFAULT_RECURSION_LIMIT,
    CompiledRunnable,
    compile_definition,
)
from src.agent_platform.runtime.events import (
    map_agent_update,
    redact_paths,
    register_default_path_aliases,
    register_path_alias,
    sse_payload,
    thread_message_ids,
)
from src.agent_platform.runtime.hitl import normalize_decisions
from src.agent_platform.runtime.persistence import (
    persist_run_reply,
    prepare_reply,
)
from src.agent_platform.runtime.run_errors import friendly_error
from src.agent_platform.runtime.staging import (
    build_run_prompt,
    stage_attachments,
)
from src.agent_platform.runtime.tool_data import (
    DISCARD_DRAFT,
    scope_from_namespace,
)
from src.agent_platform.runtime.workspace import (
    ensure_workspace_baseline,
    seed_workspace,
    workspace_seed_for,
)

logger = logging.getLogger("text2sql.agent_platform")

PROGRESS_INTERVAL_SECONDS = 5.0
DEFAULT_WALL_CLOCK_SECONDS = 900.0

_EMPTY_ANSWER = (
    "The model finished this turn without writing an answer. Run the turn again; if it "
    "repeats, raise Max output tokens for this agent or narrow the request."
)


class NoAnswerProduced(RuntimeError):
    """The turn finished without the model writing any answer text."""


def _is_cancelled(cancel_event: Any, run_id: int | None) -> bool:
    if cancel_event is not None and getattr(cancel_event, "is_set", lambda: False)():
        return True
    if run_id is None:
        return False
    current = RunStore.get(run_id)
    return bool(current and current.get("status") in {"cancelled", "cancelling"})


async def _close_mcp(tools: list[Any]) -> None:
    for tool in tools or []:
        closer = getattr(tool, "close", None)
        if closer is None:
            continue
        try:
            res = closer()
            if hasattr(res, "__await__"):
                await res
        except Exception:
            pass


class RuntimeHost:
    async def compile(
        self,
        definition: dict[str, Any],
        *,
        workspace_dir: str | None = None,
        conversation_id: str | None = None,
        run_id: int | None = None,
        client: Any = None,
        checkpoint_storage: Any = None,
        store: Any = None,
        user_id: int | None = None,
    ) -> CompiledRunnable:
        return await compile_definition(
            definition,
            client=client,
            workspace_dir=workspace_dir,
            conversation_id=conversation_id,
            run_id=run_id,
            checkpoint_storage=checkpoint_storage,
            store=store,
            user_id=user_id,
        )

    async def stream_run(
        self,
        *,
        definition: dict[str, Any],
        input_text: str,
        run_id: int,
        conversation_id: int | None = None,
        attachments: list[dict[str, Any]] | None = None,
        client: Any = None,
        resume_payload: dict[str, Any] | None = None,
        cancel_event: Any = None,
        checkpoint_id: str | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        from src.agent_platform.runtime.concurrency import acquire_run_slot, release_run_slot

        slot_sem = None
        try:
            slot_sem = acquire_run_slot()
        except RuntimeError as exc:
            yield sse_payload("error", message=str(exc), run_id=run_id)
            yield sse_payload("done", run_id=run_id, error=str(exc))
            return

        try:
            async for item in self._stream_impl(
                definition=definition,
                input_text=input_text,
                run_id=run_id,
                conversation_id=conversation_id,
                attachments=attachments,
                client=client,
                resume_payload=resume_payload,
                cancel_event=cancel_event,
                checkpoint_id=checkpoint_id,
            ):
                yield item
        finally:
            if slot_sem is not None:
                release_run_slot(slot_sem)

    async def _stream_impl(
        self,
        *,
        definition: dict[str, Any],
        input_text: str,
        run_id: int,
        conversation_id: int | None = None,
        attachments: list[dict[str, Any]] | None = None,
        client: Any = None,
        resume_payload: dict[str, Any] | None = None,
        cancel_event: Any = None,
        checkpoint_id: str | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        run = RunStore.get(run_id) or {}
        workspace = run.get("workspace_dir") or str(
            run_workspace_dir(run_id, conversation_id)
        )
        register_default_path_aliases()
        register_path_alias(workspace, "<workspace>")
        seed_workspace(workspace, workspace_seed_for(definition.get("config") or definition))
        ensure_workspace_baseline(workspace)
        attachments = stage_attachments(attachments or run.get("input_json", {}).get("attachments") or [], workspace)

        yield sse_payload("status", message="compiling", run_id=run_id)

        from contextlib import nullcontext

        namespace = checkpoint_namespace(run_id=run_id, conversation_id=conversation_id)
        checkpoint_dir = checkpoint_dir_for(namespace)
        cp_cm = sqlite_checkpoint_storage(checkpoint_dir)
        tool_scope = scope_from_namespace(namespace, checkpoint_dir)
        def_cfg = dict(definition.get("config") or definition)
        store_enabled = memory_enabled(def_cfg)
        store_cm = sqlite_store_storage() if store_enabled else nullcontext(None)
        user_id = run.get("user_id")

        async with cp_cm as storage, store_cm as store:
            try:
                compiled = await self.compile(
                    definition,
                    workspace_dir=workspace,
                    conversation_id=str(conversation_id) if conversation_id else None,
                    run_id=run_id,
                    client=client,
                    checkpoint_storage=storage,
                    store=store,
                    user_id=int(user_id) if user_id else None,
                )
            except Exception as exc:
                logger.exception("compile failed")
                RunStore.finish(run_id, "error", error=str(exc))
                yield sse_payload("error", message=str(exc), run_id=run_id)
                yield sse_payload("done", run_id=run_id, error=str(exc))
                return

            try:
                from src.services.otel_observability import flush_agent_traces
            except Exception:
                flush_agent_traces = None
            root_span_id = f"run_{run_id}"
            agent_name = definition.get("name") or "agent"
            prompt = build_run_prompt(input_text, attachments)

            if not resume_payload:
                SpanSink.record_event(
                    run_id,
                    "invoke_agent",
                    agent_name=agent_name,
                    span_name=f"invoke_agent {agent_name}",
                    span_id=root_span_id,
                    parent_span_id=None,
                    detail={"prompt": prompt, "input": prompt},
                )

            SpanSink.record_event(run_id, "status", detail={"message": "running"})
            yield sse_payload("status", message="running", run_id=run_id)

            last_agent = None
            final_text = ""
            reasoning_text = ""
            pending = None
            chart_repair = False
            graph = compiled.runnable

            conv_public_id = None
            if conversation_id:
                try:
                    conv = ConversationStore.get(conversation_id)
                    if conv:
                        conv_public_id = conv.get("public_id")
                except Exception:
                    pass

            run_public_id = run.get("public_id") if run else str(run_id)
            session_id = conv_public_id or (str(conversation_id) if conversation_id else "") or str(run_public_id)
            try:
                RunStore.set_trace(run_id, session_id=session_id, project=agent_name)
            except Exception:
                logger.debug("could not persist Phoenix session id for run %s", run_id, exc_info=True)

            try:
                from src.services.otel_observability import get_agent_tracer_callback
                callbacks = get_agent_tracer_callback(agent_name)
            except Exception:
                callbacks = []

            def_cfg = dict(definition.get("config") or definition)
            recursion_limit = int(def_cfg.get("recursion_limit") or DEFAULT_RECURSION_LIMIT)
            configurable = {"thread_id": namespace, "run_id": run_id}
            if checkpoint_id:
                configurable["checkpoint_id"] = checkpoint_id
            config = {
                "configurable": configurable,
                "recursion_limit": recursion_limit,
                "metadata": {
                    "run_id": str(run_id),
                    "run_public_id": str(run_public_id),
                    "agent_name": agent_name,
                    "conversation_id": session_id,
                    "session_id": session_id,
                },
                "callbacks": callbacks,
            }

            try:
                if _is_cancelled(cancel_event, run_id):
                    raise RuntimeError("cancelled")

                prompt = build_run_prompt(input_text, attachments)
                timeout_cfg = def_cfg.get("timeout") if isinstance(def_cfg.get("timeout"), dict) else {}
                wall_clock = float(
                    timeout_cfg.get("wall_clock") or def_cfg.get("wall_clock") or DEFAULT_WALL_CLOCK_SECONDS
                )
                if wall_clock <= 0:
                    wall_clock = DEFAULT_WALL_CLOCK_SECONDS
                run_started = time.monotonic()
                run_deadline = run_started + wall_clock
                last_progress = run_started
                stream_chunks = 0

                silent_nodes = set(getattr(graph, "silent_nodes", None) or ())
                delivered_ids = await thread_message_ids(graph, config)

                base_input = {
                    "run_id": run_id,
                    "workspace_dir": workspace,
                    "conversation_id": str(conversation_id or ""),
                    "user_id": int(user_id or 0),
                }
                if resume_payload:
                    stream_input: Any = Command(resume=normalize_decisions(resume_payload))
                else:
                    stream_input = {"messages": [HumanMessage(content=prompt)], **base_input}

                final_text = ""
                pending = None
                # Refs whose rows were already pushed as incremental `tool_data`
                # events. The full table is cached server-side long before the
                # final answer streams, so rows for a `#CHART_D1` tag can be
                # delivered the moment the tag appears in the token stream —
                # the client renders each completed block without waiting for
                # the whole turn.
                sent_tool_refs: set[str] = set()
                async for event in graph.astream(
                    stream_input,
                    config=config,
                    stream_mode=["messages", "updates", "custom"],
                    subgraphs=True,
                ):
                    if _is_cancelled(cancel_event, run_id):
                        raise RuntimeError("cancelled")
                    stream_chunks += 1
                    now = time.monotonic()
                    if now > run_deadline:
                        raise RuntimeError(
                            f"Run exceeded its {int(wall_clock)}s wall-clock limit and was stopped. "
                            "Reduce the task scope, or raise timeout.wall_clock for this agent."
                        )
                    if now - last_progress >= PROGRESS_INTERVAL_SECONDS:
                        last_progress = now
                        yield sse_payload(
                            "progress",
                            phase="model",
                            elapsed=round(now - run_started, 1),
                            chunks=stream_chunks,
                            run_id=run_id,
                        )
                    mapped, last_agent = map_agent_update(
                        event,
                        last_agent=last_agent,
                        silent_nodes=silent_nodes,
                        delivered=delivered_ids,
                    )
                    mapped = [redact_paths(item) for item in mapped]
                    for item in mapped:
                        record_stream_event(run_id, item)
                        if item.get("type") == "agent_switch":
                            to_agent = item.get("agent")
                            if to_agent and to_agent not in {"tools", "approval", "agent"}:
                                SpanSink.record_event(
                                    run_id,
                                    "agent_switch",
                                    agent_name=to_agent,
                                    span_name=f"Switch: {to_agent}",
                                    span_id=f"switch_{uuid.uuid4().hex[:8]}",
                                    parent_span_id=root_span_id,
                                    detail={"to_agent": to_agent},
                                )
                        elif item.get("type") == "reasoning":
                            reasoning_text += item.get("content") or item.get("delta") or ""
                        elif item.get("type") == "token":
                            final_text += item.get("content") or item.get("delta") or ""
                        elif item.get("type") == "status":
                            if item.get("message") == DISCARD_DRAFT:
                                chart_repair = True
                        elif item.get("type") == "chat":
                            final_text = (
                                "" if item.get("intermediate") else str(item.get("content") or "")
                            )
                        yield item
                        # Push cached rows for newly referenced blocks immediately,
                        # so the UI can draw each chart/table/card as soon as its
                        # tag has streamed instead of waiting for `done`.
                        if item.get("type") in ("token", "chat") and final_text:
                            try:
                                from src.agent_platform.runtime.tool_data import (
                                    referenced_call_ids,
                                    resolve_references,
                                )

                                fresh = [
                                    ref
                                    for ref in referenced_call_ids(final_text)
                                    if ref not in sent_tool_refs
                                ]
                                if fresh:
                                    payloads = resolve_references(fresh, tool_scope)
                                    for payload in payloads:
                                        key = str(
                                            payload.get("call_id")
                                            or payload.get("ref")
                                            or ""
                                        )
                                        if key:
                                            sent_tool_refs.add(key)
                                    # Only refs that resolved to cached rows are
                                    # marked sent; unresolved refs are retried on
                                    # later chunks (the tool result may land after
                                    # the model already named the ref).
                                    if payloads:
                                        incremental = sse_payload(
                                            "tool_data",
                                            tool_data=payloads,
                                            run_id=run_id,
                                        )
                                        # Deliberately not mirrored into SpanSink:
                                        # rows would bloat the in-memory replay
                                        # buffer; the SSE stream is the only
                                        # consumer that needs them.
                                        yield incremental
                            except Exception:
                                logger.debug(
                                    "incremental tool_data emit failed", exc_info=True
                                )
                        if item.get("type") == "approval_request":
                            pending = item
                            SpanSink.record_event(run_id, item["type"], detail=item)
                        if flush_agent_traces is not None:
                            flush_agent_traces(agent_name)

                if _is_cancelled(cancel_event, run_id):
                    raise RuntimeError("cancelled")

                if pending:
                    final_text, tool_data = prepare_reply(final_text, tool_scope)
                    RunStore.set_pending(run_id, pending)
                    if conversation_id:
                        persist_run_reply(
                            conversation_id,
                            run_id,
                            final_text,
                            pending=pending,
                            agent_name=agent_name,
                            reasoning=reasoning_text,
                            tool_data=tool_data,
                        )
                    yield sse_payload("status", message="awaiting_approval", run_id=run_id)
                    yield sse_payload(
                        "done",
                        run_id=run_id,
                        reply=final_text,
                        status="awaiting_approval",
                        tool_data=tool_data,
                    )
                else:
                    if not final_text.strip():
                        raise NoAnswerProduced(_EMPTY_ANSWER)

                    final_text, tool_data = prepare_reply(final_text, tool_scope)
                    RunStore.finish(run_id, "success", final_reply=final_text)
                    if conversation_id:
                        persist_run_reply(
                            conversation_id,
                            run_id,
                            final_text,
                            pending=None,
                            agent_name=agent_name,
                            reasoning=reasoning_text,
                            tool_data=tool_data,
                        )
                    yield sse_payload("done", run_id=run_id, reply=final_text, tool_data=tool_data)

            except Exception as exc:
                logger.exception("run failed")
                status = "cancelled" if str(exc) == "cancelled" or _is_cancelled(cancel_event, run_id) else "error"
                message = friendly_error(redact_paths(str(exc)), chart_repair=chart_repair)
                RunStore.finish(run_id, status, error=message)
                yield sse_payload("error", message=message, run_id=run_id)
                yield sse_payload("done", run_id=run_id, error=str(exc))
            finally:
                await _close_mcp(compiled.mcp_tools)

    async def run_sync(self, **kwargs: Any) -> dict[str, Any]:
        events: list[dict[str, Any]] = []
        async for event in self.stream_run(**kwargs):
            events.append(event)
        reply = ""
        error = None
        status = "success"
        for event in events:
            if event.get("type") == "done":
                reply = event.get("reply") or reply
                error = event.get("error") or error
            if event.get("type") == "error":
                error = event.get("message")
                status = "error" if status != "cancelled" else status
            if event.get("type") == "approval_request":
                status = "awaiting_approval"
            if event.get("message") == "cancelled" or event.get("error") == "cancelled":
                status = "cancelled"
        run = RunStore.get(kwargs.get("run_id"))
        if run and run.get("status") in {"awaiting_approval", "cancelled", "error", "success"}:
            status = run["status"]
        return {"events": events, "reply": reply, "error": error, "status": status}


__all__ = [
    "RuntimeHost",
    "NoAnswerProduced",
    "PROGRESS_INTERVAL_SECONDS",
    "DEFAULT_WALL_CLOCK_SECONDS",
    "stage_attachments",
    "build_run_prompt",
    "prepare_reply",
    "persist_run_reply",
]
