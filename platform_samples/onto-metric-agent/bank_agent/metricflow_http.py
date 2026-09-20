#!/usr/bin/env python3
"""MCP over streamable HTTP: the dbt MetricFlow semantic layer.

The stdio server (``metricflow_server.py``) serves the sample's own agent. This
module exposes the *same* five generic tools through a ``FastMCP`` instance so
``scripts/mcp_http_service.py`` can host them at a URL and the agent platform can
bind them like any other HTTP MCP server:

    list_metrics(search)          find metrics by name, label or description
    describe_metric(metric)       read a metric's definition and its dimensions
    list_dimension_values(...)    the distinct values a dimension takes
    query_metric(...)             compute a metric
    explain_metric(...)           the SQL that would run, without running it

There is no per-metric code: adding a metric or dimension to the dbt project
makes it available here with no change to this file.

``query_metric`` returns a **markdown table**, not JSON, because the platform's
tool-data feature parses the first markdown table out of a tool result and caches
it for ``#TABLE_``/``#CHART_`` rendering. The catalog tools stay JSON: they are
read, never charted. ``explain_metric`` returns SQL text, so it is neither.

Run over HTTP (from the module venv, so dbt-metricflow is importable) — see the
README "Run as an HTTP MCP server" section for the full command::

    PYTHONPATH="$PWD:$PWD/platform_samples/onto-metric-agent" \\
      platform_samples/onto-metric-agent/.venv/bin/python scripts/mcp_http_service.py \\
      --module bank_agent.metricflow_http --host 127.0.0.1 --port 8766 --token "$TOKEN"
"""

from __future__ import annotations

import json
import logging
import threading
from typing import Any

from mcp.server.fastmcp import FastMCP

from .metricflow_tools import (
    describe_metric as _describe_metric,
    explain_metric as _explain_metric,
    get_client,
    list_dimension_values as _list_dimension_values,
    list_metrics as _list_metrics,
    query_metric as _query_metric,
)

logger = logging.getLogger("onto_metric_agent.metricflow_http")

mcp = FastMCP("bank-metricflow")


# --- rendering ------------------------------------------------------------


def _json(payload: Any) -> str:
    return json.dumps(payload, default=str)


def _as_list(value: Any) -> list[str]:
    """Accept a list, a JSON-encoded list, or a single string from any client."""
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        try:
            parsed = json.loads(text)
        except ValueError:
            return [text]
        value = parsed
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    return [str(item) for item in value]


def _cell(value: Any) -> str:
    if value is None:
        return "NULL"
    return str(value).replace("|", "\\|")


def _constraint_lines(payload: dict[str, Any]) -> list[str]:
    """The query's constraints, one per line, without a pipe (tables follow)."""
    lines = [f"metric: {payload.get('metric')}"]
    if payload.get("group_by"):
        lines.append("group_by: " + ", ".join(str(item) for item in payload["group_by"]))
    if payload.get("where"):
        lines.append("where: " + " AND ".join(str(item) for item in payload["where"]))
    if payload.get("start_time") or payload.get("end_time"):
        lines.append(f"time: {payload.get('start_time') or '...'} .. {payload.get('end_time') or '...'}")
    return lines


def _header(payload: dict[str, Any]) -> str:
    """Constraints plus the result summary for a computed query."""
    lines = _constraint_lines(payload)
    if payload.get("value") is not None:
        lines.append(f"value: {payload['value']}")
    lines.append(f"rows: {payload.get('row_count', len(payload.get('rows') or []))}")
    return "\n".join(lines)


def _explain_header(payload: dict[str, Any]) -> str:
    """Constraints for an explain, which has no rows to summarise."""
    lines = _constraint_lines(payload)
    if payload.get("order"):
        lines.append("order: " + ", ".join(str(item) for item in payload["order"]))
    if payload.get("limit"):
        lines.append(f"limit: {payload['limit']}")
    return "\n".join(lines)


def _render_query(payload: Any) -> str:
    """A markdown table (the shape the platform's tool-data cache parses)."""
    if not isinstance(payload, dict):
        return str(payload)
    if payload.get("error"):
        return f"query_metric error: {payload['error']}"
    columns = [str(column) for column in payload.get("columns") or []]
    rows = payload.get("rows") or []
    header = _header(payload)
    if not columns or not rows:
        return f"{header}\n\nNo rows returned."
    lines = [
        "| " + " | ".join(columns) + " |",
        "| " + " | ".join(["---"] * len(columns)) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(_cell(row.get(column)) for column in columns) + " |")
    return f"{header}\n\n" + "\n".join(lines)


def _render_explain(payload: Any) -> str:
    """The generated SQL as text — nothing here is tabular or chartable."""
    if not isinstance(payload, dict):
        return str(payload)
    if payload.get("error"):
        return f"explain_metric error: {payload['error']}"
    sql = str(payload.get("sql") or "").strip()
    header = _explain_header(payload)
    if not sql:
        return f"{header}\n\nNo SQL was generated."
    return f"{header}\n\n{sql}"


# --- tools ----------------------------------------------------------------


@mcp.tool()
def list_metrics(search: str = "", limit: int = 50) -> str:
    """Find metrics by a case-insensitive search term matched against each metric's
    name, label and description. Returns the matching metrics with their description,
    label, annotations and source semantic models, plus the total match count."""
    return _json(_list_metrics(search=search.strip() or None, limit=int(limit) if limit else 50))


@mcp.tool()
def describe_metric(metric: str) -> str:
    """Read one metric's declared definition: description, label, annotations,
    measures, entities, and its queryable dimensions with their descriptions and
    annotations."""
    return _json(_describe_metric(metric))


@mcp.tool()
def list_dimension_values(
    metric: str,
    dimension: str,
    start_time: str = "",
    end_time: str = "",
) -> str:
    """The distinct values a dimension takes, together with that dimension's
    description, label and annotations, optionally bounded by a time window. Use the
    description to tell dimensions apart: the same value can appear in more than one."""
    return _json(
        _list_dimension_values(
            metric,
            dimension,
            start_time.strip() or None,
            end_time.strip() or None,
        )
    )


@mcp.tool()
def query_metric(
    metric: str,
    group_by: list[str] | None = None,
    where: list[str] | None = None,
    start_time: str = "",
    end_time: str = "",
    order: list[str] | None = None,
    limit: int = 0,
) -> str:
    """Compute a metric with MetricFlow. `group_by` is a list of dimension or entity
    names. `where` is a list of MetricFlow filter expressions of the form
    "{{ Dimension('<name>') }} <operator> <value>" or "{{ Entity('<name>') }} IN (<values>)".
    `start_time`/`end_time` bound a time dimension (inclusive, YYYY-MM-DD). Returns the
    rows as a markdown table, with `value` when the result is a single number."""
    result = _query_metric(
        metric,
        group_by=_as_list(group_by),
        where=_as_list(where),
        start_time=start_time.strip() or None,
        end_time=end_time.strip() or None,
        order=_as_list(order),
        limit=int(limit) if limit else None,
    )
    return _render_query(result)


@mcp.tool()
def explain_metric(
    metric: str,
    group_by: list[str] | None = None,
    where: list[str] | None = None,
    start_time: str = "",
    end_time: str = "",
    order: list[str] | None = None,
    limit: int = 0,
) -> str:
    """Show the SQL MetricFlow would run for a metric query, without running it.
    Takes the same arguments as `query_metric`. Use it to show or verify the
    generated SQL; it never executes and returns no rows."""
    result = _explain_metric(
        metric,
        group_by=_as_list(group_by),
        where=_as_list(where),
        start_time=start_time.strip() or None,
        end_time=end_time.strip() or None,
        order=_as_list(order),
        limit=int(limit) if limit else None,
    )
    return _render_explain(result)


# --- startup --------------------------------------------------------------


def _warm() -> None:
    """Build the engine off the serving path.

    The cold build (dbt/metricflow imports plus the dbt project load) costs ~3s;
    warming it at startup means the first tool call does not pay it.
    """
    try:
        get_client().warm()
        logger.info("MetricFlow engine warm")
    except Exception:  # noqa: BLE001 - the first tool call will surface the error
        logger.exception("MetricFlow engine warm-up failed")


threading.Thread(target=_warm, name="engine-warmup", daemon=True).start()


def main() -> None:
    """Serve over stdio (the HTTP service hosts this module instead)."""
    logging.basicConfig(level=logging.ERROR)
    mcp.run()


if __name__ == "__main__":
    main()
