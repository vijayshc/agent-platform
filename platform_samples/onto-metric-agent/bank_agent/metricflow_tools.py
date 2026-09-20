"""Generic MetricFlow tools — the whole agent surface for the semantic layer.

Five catalog operations, nothing domain-specific:

  list_metrics(search)      find metrics by name/label/description
  describe_metric(metric)   read a metric's definition and its dimensions
  list_dimension_values()   the values a dimension takes
  query_metric()            compute a metric
  explain_metric()          the SQL MetricFlow would run, without running it

There is no per-metric code and no curated answer: adding a metric or a dimension
to the dbt project makes it available here with no change to this file. Everything
comes from the MetricFlow engine, so the catalog the agent sees is exactly the
semantic layer's own.
"""

from __future__ import annotations

import json
from typing import Any

from .metricflow_client import MetricFlowClient, MetricFlowError

_CLIENT: MetricFlowClient | None = None


def get_client() -> MetricFlowClient:
    global _CLIENT
    if _CLIENT is None:
        _CLIENT = MetricFlowClient()
    return _CLIENT


def reset() -> None:
    global _CLIENT
    _CLIENT = None


def _as_list(value: Any) -> list[str]:
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


# --- tools ----------------------------------------------------------------


def list_metrics(search: str | None = None, limit: int = 50) -> dict[str, Any]:
    """Find metrics by a search term over name, label and description."""
    try:
        return get_client().list_metrics(search=search, limit=int(limit) if limit else None)
    except MetricFlowError as exc:
        return {"error": str(exc)}


def describe_metric(metric: str) -> dict[str, Any]:
    """A metric's declared definition plus its queryable dimensions."""
    try:
        return get_client().describe_metric(metric)
    except MetricFlowError as exc:
        return {"error": str(exc), "metric": metric}


def list_dimension_values(
    metric: str,
    dimension: str,
    start_time: str | None = None,
    end_time: str | None = None,
) -> dict[str, Any]:
    """The distinct values a dimension takes, with the dimension's meaning."""
    try:
        return get_client().list_dimension_values(metric, dimension, start_time, end_time)
    except MetricFlowError as exc:
        return {"error": str(exc), "metric": metric, "dimension": dimension}


def query_metric(
    metric: str,
    group_by: list[str] | None = None,
    where: list[str] | None = None,
    start_time: str | None = None,
    end_time: str | None = None,
    order: list[str] | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """Compute a metric."""
    try:
        return get_client().query(
            metric,
            group_by=_as_list(group_by),
            where=_as_list(where),
            start_time=start_time,
            end_time=end_time,
            order=_as_list(order),
            limit=int(limit) if limit else None,
        )
    except MetricFlowError as exc:
        return {"error": str(exc), "metric": metric}


def explain_metric(
    metric: str,
    group_by: list[str] | None = None,
    where: list[str] | None = None,
    start_time: str | None = None,
    end_time: str | None = None,
    order: list[str] | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """The SQL MetricFlow would run for this query, without running it."""
    try:
        return get_client().explain(
            metric,
            group_by=_as_list(group_by),
            where=_as_list(where),
            start_time=start_time,
            end_time=end_time,
            order=_as_list(order),
            limit=int(limit) if limit else None,
        )
    except MetricFlowError as exc:
        return {"error": str(exc), "metric": metric}


# --- MCP tool schemas -----------------------------------------------------

METRICFLOW_TOOLS: list[dict] = [
    {
        "name": "list_metrics",
        "description": (
            "Find metrics by a case-insensitive search term matched against each metric's name, "
            "label and description. Returns the matching metrics with their description, label, "
            "annotations and source semantic models, plus the total match count."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "search": {"type": "string"},
                "limit": {"type": "integer"},
            },
        },
    },
    {
        "name": "describe_metric",
        "description": (
            "Read one metric's declared definition: description, label, annotations, measures, "
            "entities, and its queryable dimensions with their descriptions and annotations."
        ),
        "input_schema": {"type": "object", "properties": {"metric": {"type": "string"}}, "required": ["metric"]},
    },
    {
        "name": "list_dimension_values",
        "description": (
            "The distinct values a dimension takes, together with that dimension's description, "
            "label and annotations, optionally bounded by a time window. Use the description to "
            "tell dimensions apart: the same value can appear in more than one."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "metric": {"type": "string"},
                "dimension": {"type": "string"},
                "start_time": {"type": "string"},
                "end_time": {"type": "string"},
            },
            "required": ["metric", "dimension"],
        },
    },
    {
        "name": "query_metric",
        "description": (
            "Compute a metric with MetricFlow. `group_by` is a list of dimension or entity names. "
            "`where` is a list of MetricFlow filter expressions of the form "
            "\"{{ Dimension('<name>') }} <operator> <value>\" or \"{{ Entity('<name>') }} IN (<values>)\". "
            "`start_time`/`end_time` bound a time dimension (inclusive, YYYY-MM-DD). Returns `rows`, "
            "and `value` when the result is a single number."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "metric": {"type": "string"},
                "group_by": {"type": "array", "items": {"type": "string"}},
                "where": {"type": "array", "items": {"type": "string"}},
                "start_time": {"type": "string"},
                "end_time": {"type": "string"},
                "order": {"type": "array", "items": {"type": "string"}},
                "limit": {"type": "integer"},
            },
            "required": ["metric"],
        },
    },
    {
        "name": "explain_metric",
        "description": (
            "Show the SQL MetricFlow would run for a metric query, without running it. "
            "Takes the same arguments as `query_metric`. Use it to show or verify the "
            "generated SQL; it never executes and returns no rows."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "metric": {"type": "string"},
                "group_by": {"type": "array", "items": {"type": "string"}},
                "where": {"type": "array", "items": {"type": "string"}},
                "start_time": {"type": "string"},
                "end_time": {"type": "string"},
                "order": {"type": "array", "items": {"type": "string"}},
                "limit": {"type": "integer"},
            },
            "required": ["metric"],
        },
    },
]

DISPATCH = {
    "list_metrics": lambda **kw: list_metrics(kw.get("search"), kw.get("limit", 50)),
    "describe_metric": lambda **kw: describe_metric(kw["metric"]),
    "list_dimension_values": lambda **kw: list_dimension_values(
        kw["metric"], kw["dimension"], kw.get("start_time"), kw.get("end_time")
    ),
    "query_metric": lambda **kw: query_metric(
        kw["metric"],
        kw.get("group_by"),
        kw.get("where"),
        kw.get("start_time"),
        kw.get("end_time"),
        kw.get("order"),
        kw.get("limit"),
    ),
    "explain_metric": lambda **kw: explain_metric(
        kw["metric"],
        kw.get("group_by"),
        kw.get("where"),
        kw.get("start_time"),
        kw.get("end_time"),
        kw.get("order"),
        kw.get("limit"),
    ),
}


def call_tool(name: str, arguments: dict | None = None) -> Any:
    """Dispatch a tool call, returning a readable error instead of raising."""
    if name not in DISPATCH:
        return {"error": f"unknown tool {name}", "known_tools": sorted(DISPATCH)}
    try:
        return DISPATCH[name](**(arguments or {}))
    except KeyError as exc:
        return {"error": f"missing required argument {exc}", "tool": name}
    except Exception as exc:  # noqa: BLE001 - surfaced to the agent as an observation
        return {"error": f"{type(exc).__name__}: {exc}", "tool": name}
