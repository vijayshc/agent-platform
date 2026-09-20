"""Client for the dbt MetricFlow semantic layer, through its Python API.

The `mf` CLI re-parses the dbt project on every invocation (~4s); the Python
engine is built once and kept warm, so a query is milliseconds. The engine also
exposes the catalog — metrics with their descriptions, labels, ``config.meta``
annotations and dimensions — so this client is the single source the tools read.

The engine is rebuilt automatically when dbt rewrites the semantic manifest, so a
long-lived server picks up model changes without a restart.
"""

from __future__ import annotations

import contextlib
import logging
import os
import sys
import threading
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

MODULE_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PROJECT_DIR = MODULE_ROOT / "warehouse"

#: dbt and MetricFlow are chatty on the root logger; the MCP server owns stdout.
for _name in ("dbt", "metricflow", "metricflow_semantics"):
    logging.getLogger(_name).setLevel(logging.WARNING)


class MetricFlowError(RuntimeError):
    """MetricFlow could not build, resolve or execute the request."""


@dataclass
class MetricFlowClient:
    project_dir: Path = DEFAULT_PROJECT_DIR
    _engine: Any = field(default=None, init=False)
    _manifest_mtime: float = field(default=0.0, init=False)
    _metrics: dict[str, Any] | None = field(default=None, init=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    # --- engine lifecycle -------------------------------------------------

    def _manifest_path(self) -> Path:
        return Path(self.project_dir) / "target" / "semantic_manifest.json"

    def _build(self) -> Any:
        from dbt_metricflow.cli.dbt_connectors.adapter_backed_client import AdapterBackedSqlClient
        from dbt_metricflow.cli.dbt_connectors.dbt_config_accessor import dbtArtifacts, dbtProjectMetadata
        from metricflow.engine.metricflow_engine import MetricFlowEngine
        from metricflow_semantics.model.semantic_manifest_lookup import SemanticManifestLookup

        project = Path(self.project_dir).resolve()
        if not (project / "dbt_project.yml").exists():
            raise MetricFlowError(f"dbt project not found at {project}")

        # The profile's DuckDB path is relative to the process cwd, which for an
        # MCP server is not the project dir. Pin it unless the deployment set one.
        os.environ.setdefault("BANK_DUCKDB", str(project / "bank.duckdb"))

        # dbt prints to stdout; the MCP stdio transport owns stdout, so keep the
        # build's output on stderr.
        with contextlib.redirect_stdout(sys.stderr):
            metadata = dbtProjectMetadata.load_from_paths(profiles_path=project, project_path=project)
            artifacts = dbtArtifacts.load_from_project_metadata(metadata)
            return MetricFlowEngine(
                semantic_manifest_lookup=SemanticManifestLookup(artifacts.semantic_manifest),
                sql_client=AdapterBackedSqlClient(artifacts.adapter),
            )

    def engine(self) -> Any:
        """The warm engine, rebuilt when dbt rewrites the semantic manifest.

        Guarded by a lock so a background warm-up and a concurrent first tool call
        cannot build it twice.
        """
        manifest = self._manifest_path()
        mtime = manifest.stat().st_mtime if manifest.exists() else 0.0
        if self._engine is not None and mtime == self._manifest_mtime:
            return self._engine
        with self._lock:
            if self._engine is None or mtime != self._manifest_mtime:
                self._engine = self._build()
                self._manifest_mtime = mtime
                self._metrics = None
        return self._engine

    def warm(self) -> None:
        """Build the engine now. Called at server start so the first call is fast."""
        self.engine()

    def reload(self) -> None:
        self._engine = None
        self._manifest_mtime = 0.0
        self._metrics = None

    # --- catalog ----------------------------------------------------------

    def _metrics_by_name(self) -> dict[str, Any]:
        if self._metrics is None:
            self._metrics = {metric.name: metric for metric in self.engine().list_metrics(include_dimensions=False)}
        return self._metrics

    def list_metrics(self, search: str | None = None, limit: int | None = 50) -> dict[str, Any]:
        """Metrics matching a search term over name, label and description."""
        needle = (search or "").strip().lower()
        rows = []
        for metric in self._metrics_by_name().values():
            haystack = " ".join(
                str(value or "") for value in (metric.name, metric.label, metric.description)
            ).lower()
            if needle and needle not in haystack:
                continue
            rows.append(
                {
                    "name": metric.name,
                    "description": metric.description,
                    "label": metric.label,
                    "meta": _meta(metric),
                    "semantic_models": [ref.semantic_model_name for ref in metric.semantic_models or []],
                }
            )
        rows.sort(key=lambda row: row["name"])
        total = len(rows)
        if limit is not None:
            rows = rows[:limit]
        return {"metrics": rows, "total": total, "returned": len(rows), "search": search}

    def describe_metric(self, name: str) -> dict[str, Any]:
        """Everything declared about one metric, including its queryable dimensions."""
        metric = self._metrics_by_name().get(name)
        if metric is None:
            return {"error": f"unknown metric '{name}'", "known": sorted(self._metrics_by_name())}
        dimensions = []
        for dimension in self.engine().list_dimensions(metric_names=[name]):
            dimensions.append(
                {
                    "name": dimension.dunder_name,
                    "description": dimension.description,
                    "label": dimension.label,
                    "meta": _meta(dimension),
                    "type": str(getattr(dimension.type, "value", dimension.type)),
                }
            )
        return {
            "name": metric.name,
            "description": metric.description,
            "label": metric.label,
            "type": str(getattr(metric.type, "value", metric.type)),
            "meta": _meta(metric),
            "filter": _filter_sql(metric.filter),
            "measures": _measures(metric),
            "semantic_models": [ref.semantic_model_name for ref in metric.semantic_models or []],
            "entities": _entities(self.engine(), name),
            "dimensions": sorted(dimensions, key=lambda item: item["name"]),
            "dimension_count": len(dimensions),
        }

    def list_dimensions(self, metric: str) -> list[str]:
        return [dimension.dunder_name for dimension in self.engine().list_dimensions(metric_names=[metric])]

    def dimension(self, metric: str, dimension: str) -> dict[str, Any]:
        """What a dimension means, so a value can be read in context."""
        for item in self.engine().list_dimensions(metric_names=[metric]):
            if item.dunder_name == dimension:
                return {
                    "name": item.dunder_name,
                    "description": item.description,
                    "label": item.label,
                    "meta": _meta(item),
                    "type": str(getattr(item.type, "value", item.type)),
                }
        return {}

    def list_dimension_values(
        self,
        metric: str,
        dimension: str,
        start_time: str | None = None,
        end_time: str | None = None,
    ) -> dict[str, Any]:
        """The values a dimension takes, together with what the dimension means.

        A value can appear in more than one dimension (a family and its root
        product can share a name), so the meaning travels with the values.
        """
        info = self.dimension(metric, dimension)
        if not info:
            raise MetricFlowError(f"unknown dimension '{dimension}' for metric '{metric}'")
        values = list(
            self.engine().get_dimension_values(
                metric_names=[metric],
                get_group_by_values=dimension,
                time_constraint_start=_timestamp(start_time),
                time_constraint_end=_timestamp(end_time),
            )
        )
        return {**info, "metric": metric, "values": values}

    def catalog(self) -> dict[str, Any]:
        """Metric names and the union of dimension names, for binding validation."""
        dimensions: set[str] = set()
        for name in self._metrics_by_name():
            dimensions.update(self.list_dimensions(name))
        return {"metrics": set(self._metrics_by_name()), "dimensions": dimensions}

    # --- query ------------------------------------------------------------

    def query(
        self,
        metric: str,
        group_by: list[str] | None = None,
        where: list[str] | None = None,
        start_time: str | None = None,
        end_time: str | None = None,
        order: list[str] | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        """Run a metric query and return its rows (plus the single value, if any)."""
        from metricflow.engine.metricflow_engine import MetricFlowQueryRequest

        request = MetricFlowQueryRequest.create(
            metric_names=[metric],
            group_by_names=list(group_by or []),
            where_constraints=list(where or []),
            time_constraint_start=_timestamp(start_time),
            time_constraint_end=_timestamp(end_time),
            order_by_names=list(order or []),
            limit=int(limit) if limit else None,
        )
        try:
            result = self.engine().query(request)
        except Exception as exc:  # noqa: BLE001 - every resolution/execution failure
            raise MetricFlowError(str(exc)) from exc

        table = result.result_df
        columns = list(table.column_names)
        rows = [
            {name: table.get_cell_value(row, index) for index, name in enumerate(columns)}
            for row in range(table.row_count)
        ]
        value = None
        if len(rows) == 1 and len(rows[0]) == 1:
            value = _number(next(iter(rows[0].values())))
        return {
            "metric": metric,
            "group_by": group_by or [],
            "where": where or [],
            "start_time": start_time,
            "end_time": end_time,
            "columns": columns,
            "rows": rows,
            "row_count": len(rows),
            "value": value,
        }

    def explain(
        self,
        metric: str,
        group_by: list[str] | None = None,
        where: list[str] | None = None,
        start_time: str | None = None,
        end_time: str | None = None,
        order: list[str] | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        """The SQL MetricFlow would run for this request, without running it.

        ``engine.explain`` builds the execution plan only, so this never touches
        the warehouse: it costs a fraction of a query and works while another
        process holds the database. The SQL is the plan's own rendering, not a
        reconstruction. Every task's statement is returned, so multi-task plans
        are covered where ``MetricFlowExplainResult.sql_statement`` raises.
        """
        from metricflow.engine.metricflow_engine import MetricFlowQueryRequest

        request = MetricFlowQueryRequest.create(
            metric_names=[metric],
            group_by_names=list(group_by or []),
            where_constraints=list(where or []),
            time_constraint_start=_timestamp(start_time),
            time_constraint_end=_timestamp(end_time),
            order_by_names=list(order or []),
            limit=int(limit) if limit else None,
        )
        try:
            plan = self.engine().explain(request)
        except Exception as exc:  # noqa: BLE001 - every resolution failure
            raise MetricFlowError(str(exc)) from exc

        statements = []
        for task in plan.execution_plan.tasks:
            statement = getattr(task, "sql_statement", None)
            if statement is not None:
                statements.append(statement.without_descriptions.sql.strip())
        return {
            "metric": metric,
            "group_by": group_by or [],
            "where": where or [],
            "start_time": start_time,
            "end_time": end_time,
            "order": order or [],
            "limit": limit,
            "sql": "\n\n".join(statements),
        }


def _meta(node: Any) -> dict:
    """dbt ``meta`` for a metric or dimension (``config.meta``)."""
    config = getattr(node, "config", None)
    meta = getattr(config, "meta", None) if config is not None else None
    if not meta:
        meta = getattr(node, "metadata", None)
    return meta if isinstance(meta, dict) else {}


def _measures(metric: Any) -> list[str]:
    params = getattr(metric, "type_params", None)
    names: list[str] = []
    measure = getattr(params, "measure", None)
    if measure is not None and getattr(measure, "name", None):
        names.append(measure.name)
    for item in getattr(params, "input_measures", None) or []:
        if getattr(item, "name", None) and item.name not in names:
            names.append(item.name)
    return names


def _entities(engine: Any, metric: str) -> list[dict]:
    try:
        found = engine.entities_for_metrics(metric_names=[metric])
    except Exception:  # noqa: BLE001 - entities are descriptive, not required
        return []
    rows = []
    for entity in found:
        rows.append(
            {
                "name": getattr(entity, "name", None),
                "type": str(getattr(getattr(entity, "type", None), "value", getattr(entity, "type", None))),
            }
        )
    return sorted(rows, key=lambda row: str(row["name"]))


def _filter_sql(where_filter: Any) -> str | None:
    filters = getattr(where_filter, "where_filters", None) or []
    clauses = [
        getattr(item, "where_sql_template", None) for item in filters if getattr(item, "where_sql_template", None)
    ]
    return " AND ".join(clauses) if clauses else None


def _timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    text = str(value).strip().replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text)
    except ValueError as exc:
        raise MetricFlowError(f"invalid timestamp '{value}'; expected YYYY-MM-DD") from exc


def _number(value: Any) -> float | int | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return int(number) if number.is_integer() else number
