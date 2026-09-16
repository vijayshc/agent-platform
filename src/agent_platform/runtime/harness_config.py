"""Legacy harness options still understood by the compiler.

Context management is now ``SummarizationMiddleware`` /
``ContextEditingMiddleware`` (see ``catalog/capabilities/middleware.py``); the
only thing that survives here is the token budget accessor used to migrate
definitions authored before that registry existed.
"""

from __future__ import annotations

from typing import Any


def context_window_tokens(config: dict[str, Any], default: int = 32000) -> int:
    """How many tokens of history one model call may carry.

    ``maxContextWindowTokens`` (the studio's "Context window", also accepted
    snake_cased from the API) bounds the conversation sent to the model.
    ``default_options.max_tokens`` is the model's *output* cap and is handled by
    the model client, never used as a history budget: an agent whose replies are
    capped at 300 tokens still gets its full conversation.
    """
    harness = config.get("harness") or {}
    raw = (
        config.get("maxContextWindowTokens")
        or config.get("max_context_window_tokens")
        or harness.get("max_context_window_tokens")
    )
    try:
        limit = int(raw)
    except (TypeError, ValueError):
        return default
    return limit if limit > 0 else default
