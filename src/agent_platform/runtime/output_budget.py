"""Fail a model call the output-token cap cut off before it could answer.

A reasoning model spends its output budget on reasoning tokens before it writes
any content. When the cap runs out mid-reasoning the provider reports
``finish_reason="length"`` with an empty message, and the agent loop ends the
turn with no answer at all. Nothing downstream can tell that apart from "the
model chose to say nothing", so the truncation is turned into an error at the
model boundary, where the provider's own finish reason is still visible.
"""

from __future__ import annotations

from typing import Any

#: Provider finish reasons that mean "the output cap stopped this call".
#: OpenAI-compatible gateways use ``length``; some use ``max_tokens``.
TRUNCATED_FINISH_REASONS = frozenset({"length", "max_tokens", "max_output_tokens"})

_RAISE_HINT = (
    "Raise Max output tokens for this agent (or the connection's Model "
    "Parameters) and run again."
)


class OutputBudgetExceeded(RuntimeError):
    """The model was cut off by its output-token cap before it finished."""


def _int_or_none(value: Any) -> int | None:
    return int(value) if isinstance(value, int) and not isinstance(value, bool) else None


def truncated_message(
    *,
    content: str,
    finish_reason: str | None,
    tool_calls: int = 0,
    usage: dict[str, Any] | None = None,
    model_name: str | None = None,
) -> str | None:
    """The error text for a truncated call, or ``None`` when it finished."""
    if str(finish_reason or "").lower() not in TRUNCATED_FINISH_REASONS:
        return None
    output_tokens = _int_or_none((usage or {}).get("output_tokens"))
    reasoning_tokens = _int_or_none(((usage or {}).get("output_token_details") or {}).get("reasoning"))
    budget = f"{output_tokens}-token output limit" if output_tokens else "output-token limit"
    who = model_name or "The model"
    if not content.strip() and not tool_calls:
        detail = (
            "all of it went to reasoning"
            if reasoning_tokens and reasoning_tokens == output_tokens
            else "no answer was written"
        )
        return f"{who} hit its {budget} and returned no answer ({detail}). {_RAISE_HINT}"
    return f"{who} hit its {budget} and was cut off mid-answer. {_RAISE_HINT}"


def message_parts(message: Any) -> dict[str, Any]:
    """Read a LangChain chat message (or chunk) as :func:`truncated_message` input."""
    metadata = getattr(message, "response_metadata", None) or {}
    return {
        "content": str(getattr(message, "content", "") or ""),
        "finish_reason": metadata.get("finish_reason"),
        "tool_calls": len(getattr(message, "tool_calls", None) or []),
        "usage": getattr(message, "usage_metadata", None) or {},
    }
