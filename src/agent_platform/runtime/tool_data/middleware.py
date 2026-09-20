"""LangChain middleware that clips tabular tool results and caches the full data."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage

from src.agent_platform.runtime.tool_data.policy import ToolDataPolicy, cache_limit
from src.agent_platform.runtime.tool_data.scope import ToolDataScope
from src.agent_platform.runtime.tool_data.store import TOOL_DATA_STORE, ToolData, ToolDataStore
from src.agent_platform.runtime.tool_data.table import (
    message_text,
    parse_markdown_table,
    render_marker,
    render_sample,
)

logger = logging.getLogger("text2sql.agent_platform")


class ToolDataMiddleware(AgentMiddleware):
    """Cache full tables, send the model a sample, and stamp a short reference.

    A tool result is the only place the model learns the reference it must use
    for ``#TABLE_D1`` / ``#CHART_D1``. A chartable result carries
    ``[data_ref=D1]``; every other tool result keeps the provider call-id marker.
    """

    def __init__(
        self,
        policy: ToolDataPolicy,
        scope: ToolDataScope | None,
        run_id: int | None,
        store: ToolDataStore | None = None,
    ) -> None:
        super().__init__()
        self.policy = policy
        self.scope = scope
        self.run_id = run_id
        self.store = store or TOOL_DATA_STORE

    def wrap_tool_call(
        self,
        request: Any,
        handler: Callable[[Any], Any],
    ) -> Any:
        return self._decorate(request, handler(request))

    async def awrap_tool_call(
        self,
        request: Any,
        handler: Callable[[Any], Any],
    ) -> Any:
        result = await handler(request)
        # Parsing and the Parquet write are CPU/disk work; keeping them off the
        # event loop stops one wide result from stalling every other run.
        return await asyncio.to_thread(self._decorate, request, result)

    def _decorate(self, request: Any, result: Any) -> Any:
        if not isinstance(result, ToolMessage):
            return result
        try:
            return self._process(request, result)
        except Exception:
            logger.exception("tool-data processing failed; passing result through")
            return result

    def _process(self, request: Any, message: ToolMessage) -> ToolMessage:
        tool_call = getattr(request, "tool_call", None) or {}
        call_id = str(tool_call.get("id") or getattr(message, "tool_call_id", "") or "")
        tool_name = str(tool_call.get("name") or getattr(message, "name", "") or "tool")
        text = message_text(getattr(message, "content", ""))
        config = self.policy.for_tool(tool_name)

        new_text = text
        total_rows = 0
        sample_rows = 0
        ref = ""
        if config is not None and config.enabled and call_id:
            table = parse_markdown_table(text)
            if table is not None and table.rows:
                total_rows = len(table.rows)
                stored = table.rows[: cache_limit(config)]
                cached = self.store.put(
                    self.scope,
                    ToolData(
                        call_id=call_id,
                        tool_name=tool_name,
                        columns=list(table.columns),
                        rows=[list(row) for row in stored],
                        total_rows=total_rows,
                    ),
                    run_id=self.run_id,
                )
                ref = cached.ref
                sample_rows = min(config.sample_rows, total_rows)
                new_text = render_sample(
                    table,
                    sample_rows=sample_rows,
                    total_rows=total_rows,
                    cache_rows=len(stored),
                )

        marker = (
            render_marker(ref, tool_name=tool_name, total_rows=total_rows, sample_rows=sample_rows)
            if ref
            else (f"[tool_call_id={call_id}]" if call_id else "")
        )
        if marker:
            new_text = f"{new_text}\n\n{marker}" if new_text else marker
        if new_text == text:
            return message
        return message.model_copy(update={"content": new_text})


def build_tool_data_middleware(
    policy: ToolDataPolicy,
    scope: ToolDataScope | None,
    run_id: int | None,
    store: ToolDataStore | None = None,
) -> ToolDataMiddleware | None:
    """The middleware for a run, or ``None`` when no tool opted into sampling."""
    if not policy.active:
        return None
    return ToolDataMiddleware(policy, scope, run_id, store=store)
