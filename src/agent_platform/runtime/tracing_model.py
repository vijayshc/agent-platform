"""Chat-model wrapper that records LLM I/O to SpanSink (product observability).

Guardrails (summarization, limits, PII, ...) are shipped LangChain middleware;
token streaming comes from the graph's ``messages`` stream mode. This wrapper
only forwards to the bound model and records SpanSink events.
"""

from __future__ import annotations

import logging
from typing import Any, AsyncIterator, List, Optional, Sequence

from langchain_core.callbacks import (
    AsyncCallbackManagerForLLMRun,
    CallbackManagerForLLMRun,
)
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import BaseMessage
from langchain_core.outputs import ChatGenerationChunk, ChatResult

from src.agent_platform.execution.span_sink import SpanSink
from src.agent_platform.runtime.output_budget import (
    OutputBudgetExceeded,
    message_parts,
    truncated_message,
)

logger = logging.getLogger("text2sql.agent_platform")


def _summarize(messages: Sequence[BaseMessage]) -> str:
    """Compact, human-readable prompt summary for SpanSink (mirrors old _agent_node)."""
    out: list[str] = []
    for m in messages:
        content = getattr(m, "content", "") or ""
        tool_calls = getattr(m, "tool_calls", None) or ""
        snippet = f"[{m.type}]: {content} {tool_calls if tool_calls else ''}".strip()
        out.append(snippet)
    return "\n".join(out)


class TracingChatModel(BaseChatModel):
    """Wrap a chat model to record SpanSink events and apply message compaction.

    Delegates ``invoke``/``ainvoke``/``astream`` to the wrapped model (which is
    already bound to the agent's tools via ``bind_tools``) so tool-calling
    metadata flows through unchanged. ``bind_tools`` is forwarded and returns
    another instance of this wrapper so tracing survives further binding.
    """

    inner: Any
    run_id: int | None = None
    agent_name: str = "agent"
    config: dict[str, Any] = None  # type: ignore[assignment]

    @property
    def _llm_type(self) -> str:
        return getattr(self.inner, "_llm_type", "tracing-wrapper")

    @property
    def model_name(self) -> str:
        return (
            getattr(self.inner, "model_name", None)
            or getattr(self.inner, "model", None)
            or "openai"
        )

    # ---- compaction + observability helpers ---------------------------------
    def _prepare(self, messages: Sequence[BaseMessage]) -> list[BaseMessage]:
        return list(messages)

    def _guard(self, *, content: str, finish_reason: Any, tool_calls: int = 0, usage: Any = None) -> None:
        """Raise when the output cap cut this call off before it answered.

        A truncated call is a failed turn, not a turn that chose to be silent:
        letting it through ends the agent loop with an empty message, which the
        run then reports as if it were an answer.
        """
        text = truncated_message(
            content=content,
            finish_reason=finish_reason,
            tool_calls=tool_calls,
            usage=usage,
            model_name=self.model_name,
        )
        if text:
            logger.warning("model call hit its output limit: %s", text)
            raise OutputBudgetExceeded(text)

    def _guard_message(self, message: Any) -> None:
        self._guard(**message_parts(message))

    def _record(
        self,
        messages: Sequence[BaseMessage],
        result: Any,
        *,
        streamed: bool = False,
    ) -> None:
        rid = self.run_id
        if not rid:
            return
        try:
            content = str(getattr(result, "content", "") or "")
            tool_calls = getattr(result, "tool_calls", []) or []
            generic_attrs = {
                "gen_ai.request.model": self.model_name,
                "gen_ai.response.model": self.model_name,
                "gen_ai.prompt": _summarize(messages),
                "gen_ai.input.messages": _summarize(messages),
            }
            if streamed:
                generic_attrs["gen_ai.completion"] = content
            SpanSink.record_event(
                int(rid),
                "chat",
                source="llm",
                agent_name=self.agent_name,
                detail={
                    "model": self.model_name,
                    "prompt": _summarize(messages),
                    "content": content,
                    "tool_calls": tool_calls,
                    "attributes": generic_attrs,
                },
            )
        except Exception:
            logger.debug("SpanSink.record_event failed for LLM turn", exc_info=True)

    # ---- public invocation paths (preserve tool_calls) ----------------------
    def invoke(self, input: Any, config: Optional[dict] = None, **kwargs: Any) -> Any:
        prepared = self._prepare(input if isinstance(input, list) else [input])
        result = self.inner.invoke(prepared, config=config, **kwargs)
        self._record(prepared, result)
        self._guard_message(result)
        return result

    async def ainvoke(self, input: Any, config: Optional[dict] = None, **kwargs: Any) -> Any:
        prepared = self._prepare(input if isinstance(input, list) else [input])
        result = await self.inner.ainvoke(prepared, config=config, **kwargs)
        self._record(prepared, result)
        self._guard_message(result)
        return result

    async def astream(self, input: Any, config: Optional[dict] = None, **kwargs: Any) -> AsyncIterator[Any]:
        prepared = self._prepare(input if isinstance(input, list) else [input])
        full = ""
        finish: str | None = None
        tool_calls = 0
        usage: dict[str, Any] | None = None
        async for chunk in self.inner.astream(prepared, config=config, **kwargs):
            full += str(getattr(chunk, "content", "") or "")
            metadata = getattr(chunk, "response_metadata", None) or {}
            if metadata.get("finish_reason"):
                finish = str(metadata["finish_reason"])
            if getattr(chunk, "usage_metadata", None):
                usage = chunk.usage_metadata
            tool_calls += len(getattr(chunk, "tool_calls", None) or [])
            yield chunk
        # Record the aggregate streamed completion after the loop.
        self._record(prepared, type("_Streamed", (), {"content": full})(), streamed=True)
        # The cap check runs after every chunk has been forwarded: the caller
        # still sees the partial output, then the truncated call fails the turn.
        self._guard(
            content=full, finish_reason=finish, tool_calls=tool_calls, usage=usage
        )

    # ---- BaseChatModel fallbacks (rarely reached) ---------------------------
    def _generate(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: Optional[CallbackManagerForLLMRun] = None,
        **kwargs: Any,
    ) -> ChatResult:
        prepared = self._prepare(messages)
        result = self.inner._generate(prepared, stop=stop, run_manager=run_manager, **kwargs)
        message = result.generations[0].message if result.generations else None
        self._record(prepared, message)
        if message is not None:
            self._guard_message(message)
        return result

    async def _agenerate(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: Optional[AsyncCallbackManagerForLLMRun] = None,
        **kwargs: Any,
    ) -> ChatResult:
        prepared = self._prepare(messages)
        result = await self.inner._agenerate(prepared, stop=stop, run_manager=run_manager, **kwargs)
        message = result.generations[0].message if result.generations else None
        self._record(prepared, message)
        if message is not None:
            self._guard_message(message)
        return result

    async def _astream(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: Optional[AsyncCallbackManagerForLLMRun] = None,
        **kwargs: Any,
    ) -> AsyncIterator[ChatGenerationChunk]:
        prepared = self._prepare(messages)
        full = ""
        finish: str | None = None
        tool_calls = 0
        usage: dict[str, Any] | None = None
        async for chunk in self.inner._astream(prepared, stop=stop, run_manager=run_manager, **kwargs):
            message = getattr(chunk, "message", None)
            if message is not None:
                full += str(getattr(message, "content", "") or "")
                metadata = getattr(message, "response_metadata", None) or {}
                if metadata.get("finish_reason"):
                    finish = str(metadata["finish_reason"])
                if getattr(message, "usage_metadata", None):
                    usage = message.usage_metadata
                tool_calls += len(getattr(message, "tool_calls", None) or [])
            yield chunk
        self._guard(
            content=full, finish_reason=finish, tool_calls=tool_calls, usage=usage
        )

    def bind_tools(self, tools: Sequence[Any], **kwargs: Any) -> Any:
        bound = self.inner.bind_tools(tools, **kwargs)
        return TracingChatModel(
            inner=bound,
            run_id=self.run_id,
            agent_name=self.agent_name,
            config=self.config,
        )
