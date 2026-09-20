from __future__ import annotations

import logging
import time
from pathlib import Path
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
)
from src.agent_platform.runtime.hitl import normalize_decisions
from src.agent_platform.runtime.tool_data import resolve_tool_data, scope_from_namespace
from src.agent_platform.runtime.workspace import (
    ensure_workspace_baseline,
    seed_workspace,
    workspace_seed_for,
)

logger = logging.getLogger("text2sql.agent_platform")

# A reasoning model can stream for minutes on a single turn. Without any signal
# the run looks dead, so emit a progress event on this cadence.
PROGRESS_INTERVAL_SECONDS = 5.0
# Hard wall-clock ceiling for one run. Node timeouts use LangGraph's
# ``refresh_on="auto"``, which restarts the timer on every streamed chunk, so a
# model that dribbles tokens forever never trips them. This budget is checked on
# every chunk and therefore fires even while data keeps flowing.
DEFAULT_WALL_CLOCK_SECONDS = 900.0

#: Raised when a turn ends without any assistant text. Reporting success with an
#: empty answer (or, worse, an earlier turn's answer) hides a failed turn.
_EMPTY_ANSWER = (
    "The model finished this turn without writing an answer. Run the turn again; if it "
    "repeats, raise Max output tokens for this agent or narrow the request."
)


class NoAnswerProduced(RuntimeError):
    """The turn finished without the model writing any answer text."""


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
        # Acquire a bounded concurrency slot before any heavy work (workspace)
        # copytree, graph compile, LLM client, MCP subprocess) so a burst of
        # requests cannot starve the process and make runs impact each other.
        # The slot is released in the ``finally`` below so it is guaranteed on
        # every exit path: normal completion, error, cancel, HITL pause and
        # client disconnect (GeneratorExit at any yield).
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
        # Never surface host paths to the user: alias the roots this run can
        # mention before any event is emitted.
        register_default_path_aliases()
        register_path_alias(workspace, "<workspace>")
        # Only agents that explicitly ask for a demo fixture get one; a
        # production conversation must not receive test scaffolding.
        seed_workspace(workspace, workspace_seed_for(definition.get("config") or definition))
        # Snapshot the seeded scaffold so the Files panel can distinguish it
        # from the artifacts this run produces.
        ensure_workspace_baseline(workspace)
        attachments = stage_attachments(attachments or run.get("input_json", {}).get("attachments") or [], workspace)

        yield sse_payload("status", message="compiling", run_id=run_id)

        from contextlib import nullcontext

        # Namespace the checkpoint by a stable, non-reused UUID (the
        # conversation/run public_id) so a fresh session can never resume an
        # orphaned thread left behind by a recycled integer id.
        namespace = checkpoint_namespace(run_id=run_id, conversation_id=conversation_id)
        checkpoint_dir = checkpoint_dir_for(namespace)
        cp_cm = sqlite_checkpoint_storage(checkpoint_dir)
        # Cached tool tables share the conversation's identity, so a reference
        # the model was given in an earlier turn still resolves in this one.
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
                from src.services.otel_observability import flush_agent_traces, start_span_capture, stop_span_capture
            except Exception:
                tracker = None
                stop_span_capture = None
                flush_agent_traces = None
            root_span_id = f"run_{run_id}"
            agent_name = definition.get("name") or "agent"
            prompt = _build_prompt(input_text, attachments)

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
            # The Phoenix session groups all traces of one conversation; a run
            # without a conversation is its own session (its run public id), so
            # every run is always re-fetchable from Phoenix by session or trace.
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
            # LangGraph default env recursion_limit is 10007; pass the platform
            # default on invoke so a run's step budget is explicit and tunable
            # per agent. remaining_steps on AgentState is derived from this value.
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

                prompt = _build_prompt(input_text, attachments)
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

                # Control-plane nodes (routers, fan-outs) never speak to the user.
                silent_nodes = set(getattr(graph, "silent_nodes", None) or ())
                # Every message the thread already holds has been shown to the
                # user. On a HITL resume the interrupted turn's AIMessage is
                # committed to the state, and the middleware re-writes it (same
                # id) while the graph continues -- streaming it again would repeat
                # a line the user already read. Ids mapped below are added as they
                # are delivered, so a message re-reported by a second namespace is
                # streamed once too.
                delivered_ids = await _thread_message_ids(graph, config)

                # A resume carries the OOTB HumanInTheLoopMiddleware decisions
                # payload; normalize_decisions is the single place that turns an
                # inbound payload into the value the middleware resumes with.
                if resume_payload:
                    stream_input = Command(resume=normalize_decisions(resume_payload))
                else:
                    stream_input = {
                        "messages": [HumanMessage(content=prompt)],
                        "run_id": run_id,
                        "workspace_dir": workspace,
                        "conversation_id": str(conversation_id or ""),
                        "user_id": int(user_id or 0),
                    }

                import uuid
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
                    # Enforced per chunk so it cannot be refreshed away by a
                    # model that keeps streaming without finishing.
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
                            # Reasoning is kept in its own lane: showing it in
                            # the transcript would mix scratchpad text into the
                            # turn's answer. It is persisted so the timeline
                            # can expand it after the turn completes.
                            reasoning_text += item.get("content") or item.get("delta") or ""
                        elif item.get("type") == "token":
                            # A live preview of the model writing; the complete
                            # message reported below is what the turn answered.
                            final_text += item.get("content") or item.get("delta") or ""
                        elif item.get("type") == "chat":
                            # LangGraph reports every model call of this run as a
                            # complete AIMessage. That message -- and only the last
                            # one this run produced -- is the turn's answer: a
                            # tool-call turn answers nothing, and a looping flow's
                            # earlier attempts are superseded, not concatenated.
                            final_text = (
                                "" if item.get("intermediate") else str(item.get("content") or "")
                            )
                        yield item
                        if item.get("type") == "approval_request":
                            pending = item
                            SpanSink.record_event(run_id, item["type"], detail=item)
                        if flush_agent_traces is not None:
                            flush_agent_traces(agent_name)

                if _is_cancelled(cancel_event, run_id):
                    raise RuntimeError("cancelled")

                if pending:
                    final_text = redact_paths(final_text)
                    tool_data = resolve_tool_data(final_text, tool_scope)
                    RunStore.set_pending(run_id, pending)
                    if conversation_id:
                        _persist_assistant_message(
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
                    # The reply is what this run's own stream reported. Nothing is
                    # read back from the checkpoint: the thread also holds every
                    # earlier turn, so a turn that wrote no answer would otherwise
                    # be reported -- and persisted -- as a repeat of the previous
                    # answer. A turn with no answer is a failed turn.
                    if not final_text.strip():
                        raise NoAnswerProduced(_EMPTY_ANSWER)

                    final_text = redact_paths(final_text)
                    tool_data = resolve_tool_data(final_text, tool_scope)
                    RunStore.finish(run_id, "success", final_reply=final_text)
                    if conversation_id:
                        _persist_assistant_message(
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
                message = friendly_error(redact_paths(str(exc)))
                RunStore.finish(run_id, status, error=message)
                yield sse_payload("error", message=message, run_id=run_id)
                yield sse_payload("done", run_id=run_id, error=str(exc))
            finally:
                await _close_mcp(compiled.mcp_tools)
                if stop_span_capture is not None:
                    try:
                        stop_span_capture(tracker)
                    except Exception:
                        pass

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


#: Provider payloads are long; the user needs the cause and the next action, not
#: the whole gateway envelope.
_ERROR_CLIP = 400

_TOOL_CHOICE_HINT = (
    "\n\nHint: the request forced a tool call, which this model rejects in thinking "
    "mode. If the agent has Structured output, set its strategy to Provider (native "
    "schema); Auto and Tool both force a tool call."
)


_LOOP_HINT = (
    "\n\nHint: the flow kept looping until the graph's step budget ran out. If a router "
    "routes back into the flow it came from, set its Max passes (for example 3) so the "
    "loop can exit with an answer; the Studio's Checks tab flags this."
)


def friendly_error(message: str) -> str:
    """A readable run error: clipped provider text plus an actionable hint."""
    text = str(message or "")
    lowered = text.lower()
    clipped = text if len(text) <= _ERROR_CLIP else text[:_ERROR_CLIP] + " …"
    if "tool_choice" in lowered and "thinking mode" in lowered:
        return clipped + _TOOL_CHOICE_HINT
    if "recursion limit" in lowered:
        return clipped + _LOOP_HINT
    return clipped


async def _thread_message_ids(graph: Any, config: dict[str, Any]) -> set[str]:
    """Message ids the thread already holds, i.e. everything already delivered.

    LangGraph's ``add_messages`` uses the id as a message's identity, so the same
    id re-reported later is the same message, not a new one.
    """
    try:
        state = await graph.aget_state(config)
    except Exception:
        return set()
    messages = (getattr(state, "values", None) or {}).get("messages") or []
    return {str(m.id) for m in messages if getattr(m, "id", None)}


def _persist_assistant_message(
    conversation_id: int,
    run_id: int,
    content: str,
    pending: dict[str, Any] | None,
    agent_name: str | None = None,
    reasoning: str = "",
    tool_data: list[dict[str, Any]] | None = None,
) -> None:
    run = RunStore.get(run_id) or {}
    meta: dict[str, Any] = {
        "public_id": run.get("public_id"),
        "pending": bool(pending),
        "hitl": pending,
        "agent": agent_name or run.get("agent_slug"),
    }
    if reasoning.strip():
        # The chat timeline reads this back on reload; the live stream is
        # rendered from reasoning SSE chunks before this row exists.
        meta["reasoning"] = reasoning.strip()
    if tool_data:
        from src.agent_platform.runtime.tool_data import descriptors_from_payloads
        # Only descriptors are persisted. The rows stay in the conversation's
        # tool-data archive, which the read path resolves them from, so the
        # result is not duplicated into the message row.
        meta["tool_data"] = descriptors_from_payloads(tool_data)
    ConversationStore.upsert_assistant_for_run(
        conversation_id,
        run_id,
        content or "",
        meta=meta,
    )


def _is_cancelled(cancel_event: Any, run_id: int | None) -> bool:
    if cancel_event is not None and getattr(cancel_event, "is_set", lambda: False)():
        return True
    if run_id is None:
        return False
    current = RunStore.get(run_id)
    return bool(current and current.get("status") in {"cancelled", "cancelling"})


def _build_prompt(input_text: str, attachments: list[dict[str, Any]] | None) -> str:
    text = (input_text or "").strip()
    if not attachments:
        return text
    parts = [text] if text else []
    parts.append("\n\nAttachments:")
    for att in attachments:
        name = att.get("name") or att.get("filename") or "file"
        path = att.get("workspace_path") or att.get("path")
        parts.append(f"- {name} (saved at {path})")
    return "\n".join(parts)


def stage_attachments(attachments: list[dict[str, Any]], workspace_dir: str) -> list[dict[str, Any]]:
    import shutil
    out = []
    ws = Path(workspace_dir)
    ws.mkdir(parents=True, exist_ok=True)
    for att in attachments:
        src = att.get("path") or att.get("stored_path")
        name = att.get("name") or att.get("filename") or (Path(src).name if src else "attachment")
        dst = ws / name
        if src and Path(src).exists() and not dst.exists():
            shutil.copy2(src, dst)
        row = dict(att)
        row["workspace_path"] = str(dst)
        out.append(row)
    return out


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
