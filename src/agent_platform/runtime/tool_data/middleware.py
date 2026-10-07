"""LangChain middleware that clips a typed tool table, caches the full one, and stamps a reference."""

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
    render_marker,
    render_sample,
)
from src.utils.tool_data_contract import (
    ERROR_KIND,
    ToolDataContractError,
    contract_kind,
    parse_contract,
)

logger = logging.getLogger("text2sql.agent_platform")


def structured_table(message: ToolMessage) -> Any:
    """The MCP structured content on a tool message, or ``None``."""
    artifact = getattr(message, "artifact", None)
    if isinstance(artifact, dict):
        return artifact.get("structured_content")
    return None


class ToolDataMiddleware(AgentMiddleware):
    """Cache a typed table in full, send the model a sample, stamp a reference."""

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
        return await asyncio.to_thread(self._decorate, request, result)

    def _decorate(self, request: Any, result: Any) -> Any:
        if not isinstance(result, ToolMessage):
            return result
        try:
            return self._process(request, result)
        except Exception as exc:
            logger.exception("tool-data processing failed")
            return result.model_copy(
                update={
                    "content": f"[tool_data error] {type(exc).__name__}: {exc}",
                    "artifact": None,
                }
            )

    def _process(self, request: Any, message: ToolMessage) -> ToolMessage:
        tool_call = getattr(request, "tool_call", None) or {}
        call_id = str(tool_call.get("id") or getattr(message, "tool_call_id", "") or "")
        tool_name = str(tool_call.get("name") or getattr(message, "name", "") or "tool")
        text = message_text(getattr(message, "content", ""))
        config = self.policy.for_tool(tool_name)

        new_text = text
        marker = f"[tool_call_id={call_id}]" if call_id else ""
        consumed = False

        if config is not None and config.enabled and call_id:
            structured = structured_table(message)
            if structured is None:
                logger.error("tool %s returned no structured content", tool_name)
                return message.model_copy(
                    update={
                        "content": (
                            f"[tool_data error] {tool_name} is configured to send a row sample "
                            f"but returned no typed table. The tool must return the "
                            f"tool_data_table contract in its structured content."
                        ),
                        "artifact": None,
                    }
                )
            if contract_kind(structured) == ERROR_KIND:
                marker = f"[tool_call_id={call_id}]" if call_id else ""
            else:
                try:
                    table = parse_contract(structured)
                except ToolDataContractError as exc:
                    logger.error("tool %s returned an invalid typed table: %s", tool_name, exc)
                    return message.model_copy(
                        update={
                            "content": (
                                f"[tool_data error] {tool_name} is configured to send a row sample, "
                                f"but returned a malformed typed table: {exc}"
                            ),
                            "artifact": None,
                        }
                    )
                if table is None:
                    logger.error("tool %s returned structured content of kind %r", tool_name, contract_kind(structured))
                    return message.model_copy(
                        update={
                            "content": (
                                f"[tool_data error] {tool_name} is configured to send a row sample "
                                f"but returned structured content that is not a tool_data_table."
                            ),
                            "artifact": None,
                        }
                    )
                stored_rows = table.rows[: cache_limit(config)]
                cached = self.store.put(
                    self.scope,
                    ToolData(
                        call_id=call_id,
                        tool_name=tool_name,
                        columns=list(table.columns),
                        rows=stored_rows,
                        total_rows=table.total_rows,
                    ),
                    run_id=self.run_id,
                )
                sample_rows = min(config.sample_rows, len(stored_rows))
                new_text = render_sample(
                    table,
                    sample_rows=sample_rows,
                    total_rows=table.total_rows,
                    cache_rows=len(stored_rows),
                )
                marker = (
                    render_marker(
                        cached.ref,
                        tool_name=tool_name,
                        columns=list(table.columns),
                        total_rows=table.total_rows,
                        sample_rows=sample_rows,
                    )
                    if cached.ref
                    else f"[tool_call_id={call_id}]"
                )
                message = message.model_copy(update={"artifact": None})
                consumed = True

        if marker:
            new_text = f"{new_text}\n\n{marker}" if new_text else marker
        if not consumed and new_text == text:
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


__all__ = [
    "ToolDataMiddleware",
    "build_tool_data_middleware",
]
