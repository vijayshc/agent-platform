#!/usr/bin/env python3
"""MCP stdio server: the dbt MetricFlow semantic layer.

Tools: list_metrics, describe_metric, list_dimension_values, query_metric,
explain_metric.

Every number the agent reports comes from this server. Metrics, dimensions,
annotations and hierarchy live in the dbt project; this server builds the
MetricFlow engine once and serves the catalog, queries and generated SQL from
it, so it works unchanged as metrics and dimensions are added.

Run: python -m bank_agent.metricflow_server
"""

from __future__ import annotations

import logging
import threading

from .mcp_common import build_server, run_stdio
from .metricflow_tools import METRICFLOW_TOOLS, call_tool, get_client

logger = logging.getLogger("onto_metric_agent.metricflow_server")

server = build_server("bank-metricflow", METRICFLOW_TOOLS, call_tool)


def _warm() -> None:
    """Build the engine while the client handshakes.

    The cold build (dbt/metricflow imports plus the dbt project load) costs ~3s.
    Warming it off the serving path means the first tool call does not pay it.
    """
    try:
        get_client().warm()
        logger.info("MetricFlow engine warm")
    except Exception:  # noqa: BLE001 - the first tool call will surface the error
        logger.exception("MetricFlow engine warm-up failed")


def main() -> None:
    threading.Thread(target=_warm, name="engine-warmup", daemon=True).start()
    run_stdio(server)


if __name__ == "__main__":
    main()
