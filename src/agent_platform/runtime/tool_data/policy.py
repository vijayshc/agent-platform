"""Per-tool data settings resolved from an agent definition.

Agent Studio stores the author's per-tool settings inside each MCP binding::

    mcp_bindings: [
        {
            "server_id": 3,
            "tools": ["execute_sql_query"],
            "approval": [],
            "tool_data": {
                "execute_sql_query": {"sample": true, "sample_rows": 20, "cache_rows": 5000}
            },
        }
    ]

Only tools with ``sample`` enabled become "data tools": their markdown result is
cached in full and clipped to the configured sample before the model sees it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

#: Rows sent to the model when the author enables sampling but gives no number.
DEFAULT_SAMPLE_ROWS = 20
#: Upper bound on the rows held in the intermediate cache, so a runaway query
#: can never exhaust process memory. ``cache_rows = 0`` means "all (up to here)".
MAX_CACHE_ROWS = 50_000


@dataclass(frozen=True)
class ToolDataConfig:
    """The data settings for one tool."""

    enabled: bool = False
    sample_rows: int = DEFAULT_SAMPLE_ROWS
    #: ``0`` means keep as many rows as :data:`MAX_CACHE_ROWS` allows.
    cache_rows: int = 0


@dataclass
class ToolDataPolicy:
    """The data tools of one agent, keyed by tool name."""

    tools: dict[str, ToolDataConfig] = field(default_factory=dict)

    def for_tool(self, name: str | None) -> ToolDataConfig | None:
        return self.tools.get(str(name or ""))

    @property
    def active(self) -> bool:
        return any(config.enabled for config in self.tools.values())

    @property
    def protocol_call_ids(self) -> bool:
        return bool(self.tools)


def _int_or(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))


def policy_from_config(config: dict[str, Any] | None) -> ToolDataPolicy:
    """Build the policy from an agent config's MCP bindings."""
    tools: dict[str, ToolDataConfig] = {}
    for binding in (config or {}).get("mcp_bindings") or []:
        if not isinstance(binding, dict):
            continue
        settings = binding.get("tool_data") or binding.get("toolData")
        if not isinstance(settings, dict):
            continue
        for tool_name, raw in settings.items():
            if not isinstance(raw, dict):
                continue
            if not (raw.get("sample") or raw.get("enabled")):
                continue
            sample_rows = _clamp(
                _int_or(raw.get("sample_rows", raw.get("sampleRows")), DEFAULT_SAMPLE_ROWS),
                1,
                1000,
            )
            cache_rows = _clamp(
                _int_or(raw.get("cache_rows", raw.get("cacheRows")), 0),
                0,
                MAX_CACHE_ROWS,
            )
            tools[str(tool_name)] = ToolDataConfig(
                enabled=True, sample_rows=sample_rows, cache_rows=cache_rows
            )
    return ToolDataPolicy(tools)


def cache_limit(config: ToolDataConfig) -> int:
    """The row cap applied to the intermediate cache for one tool."""
    return config.cache_rows if config.cache_rows > 0 else MAX_CACHE_ROWS
