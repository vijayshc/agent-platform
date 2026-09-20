# MetricFlow MCP server

An MCP server for a **real dbt MetricFlow semantic layer on DuckDB**, with no
hand-authored knowledge base:

```text
MCP server: bank-metricflow
  └── the MetricFlow engine (Python API), built once and kept warm
         ├── catalog: metrics, dimensions, descriptions, annotations
         ├── queries: filters, group-by, time constraints
         └── explain: the SQL a query would run, without running it
                → dbt models → DuckDB gold tables
```

The tools carry no domain facts: no metric name, dimension, id or date is
enumerated in code. A client learns everything by investigating the semantic
layer: find the metric, read its definition, discover the values a dimension
takes, then query.

The engine is used through MetricFlow's **Python API**, not the `mf` CLI. The CLI
re-parses the dbt project on every call (~4s); a warm engine answers a query in
**16–32 ms** and lists the catalog in under 20 ms. The engine is rebuilt
automatically when dbt rewrites the semantic manifest, so model changes are picked
up without a restart.

## The tools are generic

Five catalog operations, no domain logic and no per-metric code:

| Tool | What it does |
|---|---|
| `list_metrics(search)` | find metrics by name, label or description |
| `describe_metric(metric)` | a metric's description, annotations, measures, entities and its queryable dimensions |
| `list_dimension_values(metric, dimension)` | the distinct values a dimension takes |
| `query_metric(metric, group_by, where, start_time, end_time, order, limit)` | compute a metric |
| `explain_metric(metric, group_by, where, start_time, end_time, order, limit)` | the SQL MetricFlow would run, without running it |

They carry no curated answers and no guidance documents: `list_metrics` returns
catalog facts, `describe_metric` returns the metric's own declared definition.

`query_metric` returns **only the rows** — no SQL. `explain_metric` is the opt-in
way to see the generated SQL: it builds the execution plan with
`MetricFlowEngine.explain()` and returns the plan's own rendering, so it never
touches the warehouse, works while another process holds the database, and costs a
fraction of a query. It takes the same arguments as `query_metric`.

## Where the knowledge lives — dbt, not a graph

| The client must know | Where it comes from |
|---|---|
| what a metric measures | metric `description` |
| how it can be broken down | `describe_metric` → dimensions |
| what a dimension's values are | `list_dimension_values` |
| snapshot vs window semantics | metric `config.meta.temporal_type` |
| which level of a hierarchy | dimension `description` + `config.meta.hierarchy_level` |
| the hierarchy itself | flattened columns in `dim_product` / `dim_customer` |

The hierarchy is **modeled, not traversed**: `dim_product` carries
`product_family` and `product_subfamily`; `dim_customer` carries
`legal_entity_l1`/`l2`. So a family, a sub-family, "family minus a sub-family", and
"entity including subsidiaries" are all ordinary dimension filters.

## It scales by adding to the model

Adding a metric or dimension is a YAML change; **no tool or server change**:

```yaml
# warehouse/models/marts/metrics.yml
  - name: new_metric
    description: What it measures, in one sentence.
    type: simple
    config:
      meta: {temporal_type: event}
    type_params: {measure: some_measure}
```

`list_metrics` searches whatever exists; `describe_metric` reads whatever the
manifest declares. Nothing enumerates metrics or dimensions in code, so the same
solution serves 2 or 2,000 of them.

## Setup

```bash
bash platform_samples/onto-metric-agent/setup.sh
```

Creates the module venv, builds the DuckDB warehouse, runs the dbt models and
tests, and validates the MetricFlow configs. The server runs in that venv
(`dbt-metricflow`, `dbt-duckdb`, `mcp`).

## Run as an HTTP MCP server

The five tools are served over **streamable HTTP** by
`bank_agent/metricflow_http.py` (a `FastMCP("bank-metricflow")` instance). The
platform's generic runner hosts it at a URL:

```bash
TOKEN=$(~/anaconda3/bin/python3 -c 'import secrets; print(secrets.token_urlsafe(24))')
cd <repo root>
PYTHONPATH="$PWD:$PWD/platform_samples/onto-metric-agent" \
  platform_samples/onto-metric-agent/.venv/bin/python scripts/mcp_http_service.py \
  --module bank_agent.metricflow_http --host 127.0.0.1 --port 8766 --token "$TOKEN"
```

It runs on the **module venv**, because `dbt-metricflow` / `dbt-duckdb` live there
and not in the shared platform interpreter. `scripts/mcp_http_service.py
--autostart` launches each row's service with the interpreter that runs *it* (the
platform's), so this row declares no `service` block and is started with the
command above.

Register it in **Admin → MCP Servers** as an HTTP server:

| Field | Value |
|---|---|
| URL | `http://127.0.0.1:8766/mcp` |
| Header | `Authorization: Bearer <token>` |

The server's own tools are the contract; the catalog row only points at the URL.

## Run as a stdio MCP server

`bank_agent/metricflow_server.py` exposes the same five tools over stdio for a
local MCP client:

```bash
PYTHONPATH=platform_samples/onto-metric-agent \
  platform_samples/onto-metric-agent/.venv/bin/python -m bank_agent.metricflow_server
```

## Result shapes

| Tool | Result |
|---|---|
| `list_metrics`, `describe_metric`, `list_dimension_values` | JSON |
| `query_metric` | a markdown table, so a host can cache and chart it |
| `explain_metric` | the generated SQL as text |

## Verify

`Admin → MCP Servers → Test tools` lists the tools live. Quick checks over HTTP:

```bash
curl -s -X POST http://127.0.0.1:8766/mcp \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}'
```

Ask for a metric with `query_metric` and the SQL behind it with `explain_metric`;
`aum` as of `2026-06-30` is `2,200,000`, and by segment it is PB 2.1M / Retail 0.1M.

## Layout

```text
bank_agent/
  metricflow_client.py   the MetricFlow Python engine: catalog, queries, explain, kept warm
  metricflow_tools.py    the five generic tools + MCP schemas
  metricflow_server.py   MCP server: bank-metricflow (stdio)
  metricflow_http.py     the same five tools over streamable HTTP (FastMCP)
  mcp_common.py          low-level MCP stdio server factory
warehouse/
  seed_data.py           the deterministic dataset
  build.py               raw tables → bank.duckdb
  models/staging/        one view per source table
  models/marts/          dim_product, dim_customer, fct_holding, fct_control_event, time_spine
                         + semantic_models.yml + metrics.yml + tests
setup.sh                 venv + warehouse + dbt run/test + MetricFlow validation
requirements.txt         the module venv (mcp, dbt-metricflow, dbt-duckdb, duckdb)
```
