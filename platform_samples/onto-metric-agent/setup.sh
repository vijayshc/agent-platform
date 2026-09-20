#!/usr/bin/env bash
# Set up this module end to end:
#   1. a venv for the MCP server (MCP SDK, dbt-metricflow, dbt-duckdb)
#   2. the DuckDB warehouse (raw tables)
#   3. the dbt models and MetricFlow semantic layer
#   4. a MetricFlow config validation
#
# The server runs in this venv so its dbt/MetricFlow dependencies never touch the
# shared platform environment.
#
#   bash platform_samples/onto-metric-agent/setup.sh
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="${PYTHON:-$HOME/anaconda3/bin/python3}"

echo "==> venv"
"$PYTHON" -m venv "$HERE/.venv"
"$HERE/.venv/bin/python" -m pip install --quiet --upgrade pip
"$HERE/.venv/bin/python" -m pip install --quiet -r "$HERE/requirements.txt"

echo "==> warehouse (DuckDB raw tables)"
cd "$HERE/warehouse"
"$HERE/.venv/bin/python" build.py

echo "==> dbt seeds (reference data)"
"$HERE/.venv/bin/dbt" seed --profiles-dir . --project-dir . --quiet

echo "==> dbt models + semantic layer"
"$HERE/.venv/bin/dbt" run --profiles-dir . --project-dir . --quiet

echo "==> dbt tests"
"$HERE/.venv/bin/dbt" test --profiles-dir . --project-dir . --quiet

echo "==> validate MetricFlow configs"
"$HERE/.venv/bin/mf" validate-configs >/dev/null

echo "==> ready"
"$HERE/.venv/bin/python" -c "import dbt, duckdb, mcp; print('venv ok')"
echo "Serve over HTTP:  see the README 'Run as an HTTP MCP server' section"
