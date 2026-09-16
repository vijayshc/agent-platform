from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator, Iterator, List, Optional, Sequence
from pydantic import Field

from langchain_core.callbacks import AsyncCallbackManagerForLLMRun, CallbackManagerForLLMRun
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult


class ScriptedChatClient(BaseChatModel):
    queue: list[Any] = Field(default_factory=list)
    delay_s: float = 0.0
    calls: list[list[BaseMessage]] = Field(default_factory=list)
    _tools: list[Any] = []

    def __init__(self, responses: Sequence[Any] | None = None, delay_s: float = 0, **kwargs: Any) -> None:
        q = list(responses or ["ok"])
        super().__init__(queue=q, delay_s=float(delay_s or 0), **kwargs)

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def enqueue(self, item: Any) -> None:
        self.queue.append(item)

    def _next_message(self) -> AIMessage:
        item = self.queue.pop(0) if self.queue else "ok"
        if isinstance(item, AIMessage):
            return item
        if isinstance(item, BaseMessage):
            return AIMessage(content=str(getattr(item, "content", "")))
        if isinstance(item, dict):
            if item.get("type") == "function_call":
                return AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": item.get("name"),
                            "args": item.get("arguments") or {},
                            "id": item.get("call_id") or "call_1",
                        }
                    ],
                )
            if item.get("type") == "json" or "next_speaker" in item or "terminate" in item:
                import json
                return AIMessage(content=json.dumps(item))
            text = item.get("text") or item.get("content") or str(item)
            return AIMessage(content=str(text))
        return AIMessage(content=str(item))

    def _generate(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: Optional[CallbackManagerForLLMRun] = None,
        **kwargs: Any,
    ) -> ChatResult:
        self.calls.append(list(messages))
        msg = self._next_message()
        return ChatResult(generations=[ChatGeneration(message=msg)])

    async def _agenerate(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: Optional[AsyncCallbackManagerForLLMRun] = None,
        **kwargs: Any,
    ) -> ChatResult:
        self.calls.append(list(messages))
        if self.delay_s > 0:
            await asyncio.sleep(self.delay_s)
        msg = self._next_message()
        return ChatResult(generations=[ChatGeneration(message=msg)])

    async def _astream(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: Optional[AsyncCallbackManagerForLLMRun] = None,
        **kwargs: Any,
    ) -> AsyncIterator[ChatGenerationChunk]:
        self.calls.append(list(messages))
        if self.delay_s > 0:
            await asyncio.sleep(self.delay_s)
        msg = self._next_message()
        if msg.tool_calls:
            import json

            chunk = ChatGenerationChunk(
                message=AIMessageChunk(
                    content=msg.content,
                    tool_call_chunks=[
                        {
                            "name": tc["name"],
                            "args": json.dumps(tc.get("args")) if isinstance(tc.get("args"), (dict, list)) else str(tc.get("args") or "{}"),
                            "id": tc.get("id"),
                            "index": i,
                        }
                        for i, tc in enumerate(msg.tool_calls)
                    ],
                )
            )
            if run_manager:
                await run_manager.on_llm_new_token(msg.content, chunk=chunk)
            yield chunk
        else:
            text = str(msg.content or "")
            chunk = ChatGenerationChunk(message=AIMessageChunk(content=text))
            if run_manager:
                await run_manager.on_llm_new_token(text, chunk=chunk)
            yield chunk

    def bind_tools(self, tools: Sequence[Any], **kwargs: Any) -> Any:
        bound = self.copy()
        bound._tools = list(tools)
        return bound
