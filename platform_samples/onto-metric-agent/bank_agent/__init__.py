"""MetricFlow MCP server sample.

Submodules are imported directly (``bank_agent.metricflow_server``,
``bank_agent.metricflow_http``, ...). This package imports nothing at package
level on purpose: only the MCP server process needs the heavy dependencies
(dbt-metricflow, dbt-duckdb), so importing the package stays cheap.
"""

__all__: list[str] = []
