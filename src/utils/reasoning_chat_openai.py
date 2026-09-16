"""``ChatOpenAI`` subclass that round-trips provider reasoning fields.

OpenAI-compatible gateways for reasoning models (DeepSeek thinking mode, GLM,
Qwen, ...) return a provider-specific ``reasoning_content`` / ``reasoning``
field on assistant messages and require it to be echoed back on the next
request. ``langchain-openai`` deliberately does not extract or resend it, so
multi-turn and tool-loop calls fail with HTTP 400
("The `reasoning_content` in the thinking mode must be passed back to the API").

This subclass captures the field from both streamed deltas and full responses,
stores it on the ``AIMessage.additional_kwargs`` (so it survives checkpointing),
and re-serializes it into the request payload. It is inert for models that do
not emit reasoning.
"""

from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGenerationChunk, ChatResult
from langchain_openai import ChatOpenAI

#: Provider field names we round-trip. ``reasoning`` is what the gateway we
#: use today emits; ``reasoning_content`` covers DeepSeek/vLLM/Ollama-style
#: OpenAI-compatible deployments.
_REASONING_KEYS = ("reasoning_content", "reasoning")


def _reasoning_fields(raw: Any) -> dict[str, Any]:
    """Extract reasoning fields from a raw message/delta (dict or model)."""
    out: dict[str, Any] = {}
    for key in _REASONING_KEYS:
        value = raw.get(key) if isinstance(raw, dict) else getattr(raw, key, None)
        if isinstance(value, str):
            if value:
                out[key] = value
            continue
        if isinstance(value, list):
            text = "".join(
                str(part.get("text") or part.get("content") or "")
                for part in value
                if isinstance(part, dict)
            )
            if text:
                out[key] = text
            continue
        if value:
            out[key] = value
    return out


class ReasoningChatOpenAI(ChatOpenAI):
    """ChatOpenAI that preserves reasoning fields across turns and tool loops."""

    def _convert_chunk_to_generation_chunk(
        self,
        chunk: dict,
        default_chunk_class: type,
        base_generation_info: dict | None,
    ) -> ChatGenerationChunk | None:
        generation_chunk = super()._convert_chunk_to_generation_chunk(
            chunk, default_chunk_class, base_generation_info
        )
        if generation_chunk is None:
            return None
        choices = chunk.get("choices") or chunk.get("chunk", {}).get("choices") or []
        for choice in choices:
            delta = choice.get("delta") or {}
            fields = _reasoning_fields(delta)
            if fields:
                generation_chunk.message.additional_kwargs.update(fields)
            break
        return generation_chunk

    def _create_chat_result(  # type: ignore[override]
        self,
        response: dict | Any,
        generation_info: dict | None = None,
    ) -> ChatResult:
        result = super()._create_chat_result(response, generation_info)
        choices = (
            response.get("choices")
            if isinstance(response, dict)
            else getattr(response, "choices", None)
        )
        for generation, choice in zip(result.generations or [], choices or []):
            message = (
                choice.get("message")
                if isinstance(choice, dict)
                else getattr(choice, "message", None)
            )
            fields = _reasoning_fields(message)
            if fields and isinstance(generation.message, AIMessage):
                generation.message.additional_kwargs.update(fields)
        return result

    def _get_request_payload(
        self,
        input_: Any,
        *,
        stop: list[str] | None = None,
        **kwargs: Any,
    ) -> dict:
        payload = super()._get_request_payload(input_, stop=stop, **kwargs)
        messages = input_.to_messages() if hasattr(input_, "to_messages") else input_
        payload_messages = payload.get("messages") or []
        for source, target in zip(messages or [], payload_messages or []):
            if not isinstance(source, AIMessage):
                continue
            for key in _REASONING_KEYS:
                value = source.additional_kwargs.get(key)
                if value:
                    target[key] = value
        return payload
