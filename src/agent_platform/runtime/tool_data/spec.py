"""Server-side authority over the chart/table a reply asks for.

The model writes a placeholder and a JSON spec in its answer. That spec is a
*request*, not a fact: it names columns that may not exist, asks for a pie of a
text column, or plots a measure that is not a measure. This module is where the
request is checked against the data it refers to — before the browser sees it —
and turned into a canonical spec the renderer can trust.

Three rules, in order:

1. **Validate.** The spec must satisfy :data:`CHART_SPEC_SCHEMA` and every column
   it names must exist in the referenced table with a type that fits its role.
2. **Repair, don't guess.** A name that differs only by case is canonicalised; a
   default is filled in. Anything ambiguous or wrong is an error.
3. **Surface.** A spec that cannot be repaired becomes an explicit error block
   the chat renders with the reason. The runtime never substitutes a different
   column, a different chart type, or a different aggregation to make a broken
   request draw something.

The rewritten reply is what is streamed, persisted and reloaded, so a chart is
reproducible: the same reply always renders the same chart.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Iterable

from jsonschema import Draft202012Validator

from src.agent_platform.runtime.tool_data.scope import ToolDataScope
from src.agent_platform.runtime.tool_data.store import TOOL_DATA_STORE, ToolData, ToolDataStore
from src.utils.tool_data_contract import NUMERIC_TYPES, Column

logger = logging.getLogger("text2sql.agent_platform")

CHART_TYPES = ("line", "area", "bar", "hbar", "stackedBar", "stackedArea", "pie", "donut", "scatter")
PIE_TYPES = ("pie", "donut")
#: Aggregations. ``none`` is written by the server for a scatter (one point per
#: row); the validator refuses it on any chart that groups rows, so it cannot be
#: used to discard data.
AGGREGATES = ("sum", "avg", "count", "min", "max", "none")
CARD_AGGREGATES = ("sum", "avg", "count", "min", "max")
#: Icon names a KPI card may carry. The model writes the kebab-case string; the
#: client maps it to a Lucide component. Closed like chart types: an unknown
#: name is rejected, so nothing arbitrary ever renders.
CARD_ICONS = (
    "trending-up",
    "trending-down",
    "wallet",
    "shopping-cart",
    "users",
    "user",
    "package",
    "truck",
    "piggy-bank",
    "target",
    "award",
    "star",
    "activity",
    "bar-chart-3",
    "line-chart",
    "percent",
    "hash",
    "calendar",
    "clock",
    "globe",
    "briefcase",
    "zap",
    "arrow-up-right",
    "arrow-down-right",
    "circle-check",
    "triangle-alert",
    "dollar-sign",
)
SORTS = ("asc", "desc", "x", "none")
FORMATS = ("number", "compact", "percent", "currency")
COLOR_BY = ("category", "series", "single")
LAYOUTS = ("full", "half", "third", "quarter")
NOTE_STYLES = ("title", "insight", "info", "warning", "success")

#: Keys the server adds to a spec it produced. They are stripped before
#: validation, so a reply is idempotent under re-validation and the model cannot
#: write its own diagnostics or fake an error.
SERVER_SPEC_KEYS = frozenset({"diagnostics", "error"})

#: The chart spec's schema. It is deliberately closed: an unknown key is a model
#: error, not something to ignore, because a field the renderer does not
#: understand is a chart that does not do what its author intended.
#:
#: `aggregate` is **required** for every chart that groups rows, so the choice of
#: how to combine them is the model's explicit decision rather than a default the
#: server silently applied. A scatter plots one point per row and must not carry
#: an aggregation at all.
CHART_SPEC_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "additionalProperties": False,
    "required": ["type", "x", "y"],
    "allOf": [
        {
            "if": {"properties": {"type": {"const": "scatter"}}, "required": ["type"]},
            "else": {"required": ["aggregate"]},
        }
    ],
    "properties": {
        "type": {"enum": list(CHART_TYPES)},
        "x": {"type": "string", "minLength": 1},
        "y": {
            "oneOf": [
                {"type": "string", "minLength": 1},
                {"type": "array", "items": {"type": "string", "minLength": 1}, "minItems": 1},
            ]
        },
        "rightAxis": {
            "type": "array",
            "items": {"type": "string", "minLength": 1},
            "minItems": 1,
        },
        "rightYLabel": {"type": "string", "maxLength": 80},
        "series": {"type": "string", "minLength": 1},
        "aggregate": {"enum": list(AGGREGATES)},
        "sort": {"enum": list(SORTS)},
        "limit": {"type": "integer", "minimum": 1, "maximum": 10000},
        "title": {"type": "string", "maxLength": 200},
        "subtitle": {"type": "string", "maxLength": 200},
        "xLabel": {"type": "string", "maxLength": 80},
        "yLabel": {"type": "string", "maxLength": 80},
        "layout": {"enum": list(LAYOUTS)},
        "valueFormat": {"enum": list(FORMATS)},
        "currency": {"type": "string", "pattern": "^[A-Za-z]{3}$"},
        "colorBy": {"enum": list(COLOR_BY)},
        "height": {"type": "integer", "minimum": 180, "maximum": 720},
        "colors": {
            "type": "array",
            "items": {"type": "string", "pattern": "^#([0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$"},
            "minItems": 1,
            "maxItems": 24,
        },
        "smooth": {"type": "boolean"},
        "showLegend": {"type": "boolean"},
        "showGrid": {"type": "boolean"},
        "stacked": {"type": "boolean"},
    },
}

TABLE_SPEC_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "title": {"type": "string", "maxLength": 200},
        "pageLength": {"type": "integer", "minimum": 5, "maximum": 200},
        "layout": {"enum": list(LAYOUTS)},
    },
}

_HEX_COLOR = {"type": "string", "pattern": "^#([0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$"}

CARD_SPEC_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "additionalProperties": False,
    "required": ["metric", "aggregate"],
    "properties": {
        "metric": {"type": "string", "minLength": 1},
        "aggregate": {"enum": list(CARD_AGGREGATES)},
        "title": {"type": "string", "maxLength": 200},
        "subtitle": {"type": "string", "maxLength": 200},
        "hint": {"type": "string", "maxLength": 200},
        "layout": {"enum": list(LAYOUTS)},
        "valueFormat": {"enum": list(FORMATS)},
        "currency": {"type": "string", "pattern": "^[A-Za-z]{3}$"},
        "deltaMetric": {"type": "string", "minLength": 1},
        "deltaAggregate": {"enum": list(CARD_AGGREGATES)},
        "icon": {"enum": list(CARD_ICONS)},
        "color": _HEX_COLOR,
        "spark": {
            "type": "object",
            "additionalProperties": False,
            "required": ["x"],
            "properties": {
                "x": {"type": "string", "minLength": 1},
                "type": {"enum": ["line", "area", "bar"]},
            },
        },
    },
}

LIST_SPEC_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "additionalProperties": False,
    "required": ["label", "value", "aggregate"],
    "properties": {
        "label": {"type": "string", "minLength": 1},
        "value": {"type": "string", "minLength": 1},
        "aggregate": {"enum": list(CARD_AGGREGATES)},
        "limit": {"type": "integer", "minimum": 2, "maximum": 20},
        "title": {"type": "string", "maxLength": 200},
        "subtitle": {"type": "string", "maxLength": 200},
        "layout": {"enum": list(LAYOUTS)},
        "valueFormat": {"enum": list(FORMATS)},
        "currency": {"type": "string", "pattern": "^[A-Za-z]{3}$"},
        "color": _HEX_COLOR,
        "variant": {"enum": ["bars", "plain", "share"]},
        "showShare": {"type": "boolean"},
    },
}

PROGRESS_SPEC_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "additionalProperties": False,
    "required": ["metric", "aggregate", "target"],
    "properties": {
        "metric": {"type": "string", "minLength": 1},
        "aggregate": {"enum": list(CARD_AGGREGATES)},
        "target": {"type": "number"},
        "title": {"type": "string", "maxLength": 200},
        "subtitle": {"type": "string", "maxLength": 200},
        "layout": {"enum": list(LAYOUTS)},
        "valueFormat": {"enum": list(FORMATS)},
        "currency": {"type": "string", "pattern": "^[A-Za-z]{3}$"},
        "color": _HEX_COLOR,
    },
}

NOTE_SPEC_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "style": {"enum": list(NOTE_STYLES)},
        "title": {"type": "string", "maxLength": 200},
        "body": {"type": "string", "maxLength": 2000},
        "layout": {"enum": list(LAYOUTS)},
    },
}

_CHART_VALIDATOR = Draft202012Validator(CHART_SPEC_SCHEMA)
_TABLE_VALIDATOR = Draft202012Validator(TABLE_SPEC_SCHEMA)
_CARD_VALIDATOR = Draft202012Validator(CARD_SPEC_SCHEMA)
_LIST_VALIDATOR = Draft202012Validator(LIST_SPEC_SCHEMA)
_PROGRESS_VALIDATOR = Draft202012Validator(PROGRESS_SPEC_SCHEMA)
_NOTE_VALIDATOR = Draft202012Validator(NOTE_SPEC_SCHEMA)

_TABLE_KEYS = frozenset(TABLE_SPEC_SCHEMA["properties"])
#: Fields that only make sense on a chart. A table block carrying them is the
#: model using the wrong fence, which is worth saying in those words.
_CHART_ONLY_KEYS = frozenset(CHART_SPEC_SCHEMA["properties"]) - _TABLE_KEYS

#: A ``#CHART_<ref>`` / ``#TABLE_<ref>`` / ``#CARD_<ref>`` / ``#LIST_<ref>`` /
#: ``#PROGRESS_<ref>`` placeholder at the start of a line, plus bare ``#NOTE``.
DATA_TOKEN = re.compile(r"^\s*#(CHART|TABLE|CARD|LIST|PROGRESS)_([A-Za-z0-9][A-Za-z0-9_-]*)\s*(.*)$")
NOTE_TOKEN = re.compile(r"^\s*#NOTE\s*(.*)$")
#: Back-compat alias: the old two-kind placeholder.
TOKEN = DATA_TOKEN


class SpecError(ValueError):
    """A spec that cannot be drawn from the data it names."""


@dataclass
class Block:
    """One chart/table block parsed out of a reply."""

    kind: str
    ref: str
    spec: dict[str, Any]
    raw: str


def _balanced_json(text: str) -> str | None:
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    return None


def _parse_object(text: str) -> dict[str, Any]:
    candidate = _balanced_json(text)
    if candidate is None:
        return {}
    try:
        parsed = json.loads(candidate)
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _collect_spec(lines: list[str], index: int, inline: str) -> tuple[dict[str, Any], int]:
    """The JSON object at/after ``index`` plus the last line it spans."""
    cursor = index
    if not inline:
        # A spec may follow on the next line, but only when that line
        # actually opens one: a bare placeholder followed by prose must not
        # swallow the prose into an empty spec.
        if index + 1 < len(lines) and lines[index + 1].strip().startswith("{"):
            cursor = index + 1
            inline = lines[cursor]
    while inline and _balanced_json(inline) is None and cursor + 1 < len(lines):
        cursor += 1
        inline = f"{inline}\n{lines[cursor]}"
    return _parse_object(inline), cursor


def extract_blocks(text: str) -> list[Block]:
    """Every chart/table/dashboard block the reply names, in order.

    Markdown structure is the renderer's business. This only pairs a
    ``#CHART_<ref>`` / ``#TABLE_<ref>`` / ``#CARD_<ref>`` / ``#LIST_<ref>`` /
    ``#PROGRESS_<ref>`` / ``#NOTE`` placeholder with the JSON object that
    follows it, so validation needs no second markdown parser.
    """
    lines = (text or "").replace("\r\n", "\n").split("\n")
    blocks: list[Block] = []
    index = 0
    while index < len(lines):
        note = NOTE_TOKEN.match(lines[index])
        if note is not None:
            inline = (note.group(1) or "").strip()
            spec, cursor = _collect_spec(lines, index, inline)
            blocks.append(
                Block(
                    kind="note",
                    ref="",
                    spec=spec,
                    raw="\n".join(lines[index : cursor + 1]),
                )
            )
            index = cursor + 1
            continue
        token = DATA_TOKEN.match(lines[index])
        if token is None:
            index += 1
            continue
        kind = token.group(1).lower()
        inline = (token.group(3) or "").strip()
        spec, cursor = _collect_spec(lines, index, inline)
        blocks.append(
            Block(
                kind=kind,
                ref=token.group(2),
                spec=spec,
                raw="\n".join(lines[index : cursor + 1]),
            )
        )
        index = cursor + 1
    return blocks


# ------------------------------------------------------------------ validation


def _resolve(columns: list[Column], name: str) -> tuple[Column, bool]:
    """The column ``name`` refers to, and whether case alone had to be fixed."""
    exact = [column for column in columns if column.name == name]
    if len(exact) == 1:
        return exact[0], False
    folded = [column for column in columns if column.name.lower() == name.lower()]
    if len(folded) == 1:
        return folded[0], True
    if len(folded) > 1:
        raise SpecError(
            f"column {name!r} is ambiguous; the result has {', '.join(c.name for c in folded)}"
        )
    raise SpecError(
        f"column {name!r} is not in this result (available: {', '.join(c.name for c in columns)})"
    )


def _as_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(item) for item in value]
    return []


def _schema_error(validator: Draft202012Validator, spec: dict[str, Any]) -> str | None:
    errors = sorted(validator.iter_errors(spec), key=lambda error: list(error.path))
    if not errors:
        return None
    first = errors[0]
    path = ".".join(str(part) for part in first.path) or "spec"
    return f"{path}: {first.message}"


def _json_type(value: Any) -> str:
    """The JSON type of a scalar, in the same vocabulary the browser uses.

    The client keys a category by ``typeof`` (``number``/``string``/…), so the
    server must too or its "grouped into N categories" diagnostic would count a
    different number of categories than the chart draws.
    """
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    return "other"


def _distinct(values: Iterable[Any]) -> int:
    """Distinct values, keyed by JSON type as well as text so ``1`` and ``"1"``
    do not collapse into one category — and ``1`` and ``1.0`` do."""
    return len({(_json_type(value), str(value)) for value in values})


def validate_chart(
    spec: dict[str, Any],
    table: ToolData,
) -> tuple[dict[str, Any], list[str]]:
    """The canonical chart spec for ``spec`` against ``table``, plus diagnostics.

    Raises :class:`SpecError` when the request cannot be drawn from this data.
    """
    # An error block re-validates to itself, so a second pass cannot turn a
    # refusal into a chart (or a different refusal). Only the server's own block —
    # an error and nothing else — is honoured; a model cannot smuggle one in
    # beside a real spec.
    if spec.get("error") and set(spec) == {"error"}:
        raise SpecError(str(spec["error"]))
    # Server-added keys are not model-authored; strip them so validating an
    # already-normalised spec is idempotent and the model cannot write its own
    # diagnostics or fake an error.
    model_spec = {key: value for key, value in spec.items() if key not in SERVER_SPEC_KEYS}
    problem = _schema_error(_CHART_VALIDATOR, model_spec)
    if problem:
        raise SpecError(problem)

    diagnostics: list[str] = []
    columns = list(table.columns)

    x_column, x_repaired = _resolve(columns, str(model_spec["x"]))
    if x_repaired:
        diagnostics.append(f"x column matched case-insensitively: {model_spec['x']} → {x_column.name}")

    y_columns: list[Column] = []
    for name in _as_list(model_spec["y"]):
        column, repaired = _resolve(columns, name)
        if repaired:
            diagnostics.append(f"y column matched case-insensitively: {name} → {column.name}")
        if column.type not in NUMERIC_TYPES:
            raise SpecError(
                f"y column {column.name!r} is {column.type}, not a measure; "
                f"chart a numeric column instead"
            )
        if column.name in [item.name for item in y_columns]:
            raise SpecError(f"y column {column.name!r} is listed more than once")
        y_columns.append(column)

    series_column: Column | None = None
    if model_spec.get("series"):
        series_column, repaired = _resolve(columns, str(model_spec["series"]))
        if repaired:
            diagnostics.append(
                f"series column matched case-insensitively: {model_spec['series']} → {series_column.name}"
            )

    if x_column.name in [column.name for column in y_columns]:
        raise SpecError(f"column {x_column.name!r} cannot be both x and y")
    if series_column is not None and series_column.name in [column.name for column in y_columns]:
        raise SpecError(f"column {series_column.name!r} cannot be both series and y")
    if series_column is not None and series_column.name == x_column.name:
        raise SpecError(f"column {series_column.name!r} cannot be both x and series")

    chart_type = str(model_spec["type"])
    if chart_type in PIE_TYPES:
        if len(y_columns) != 1:
            raise SpecError("a pie or donut chart needs exactly one y measure")
        if series_column is not None:
            raise SpecError("a pie or donut chart cannot use a series column")

    if chart_type == "scatter":
        # A scatter is one point per row. The server writes `aggregate: "none"`
        # for it; any other aggregation is refused, and `"none"` on a chart that
        # groups is refused below.
        if model_spec.get("aggregate", "none") != "none":
            raise SpecError("a scatter chart plots one point per row; it cannot aggregate")
        if series_column is not None:
            raise SpecError("a scatter chart plots one point per row; remove 'series'")
        if x_column.type not in NUMERIC_TYPES:
            raise SpecError(
                f"a scatter chart needs a numeric x; {x_column.name!r} is {x_column.type}"
            )
        aggregate = "none"
    else:
        aggregate = str(model_spec["aggregate"])
        if aggregate == "none":
            raise SpecError("'none' is only for a scatter chart, which plots one point per row")

    stacked = bool(model_spec.get("stacked")) or chart_type in ("stackedBar", "stackedArea")
    base_type = {"stackedBar": "bar", "stackedArea": "area"}.get(chart_type, chart_type)
    if stacked and base_type not in ("bar", "area"):
        raise SpecError(f"a stacked chart must be a bar or area chart, not {chart_type!r}")

    # A second axis lets two measures on incomparable scales (order counts vs
    # basket dollars) share one chart without the smaller flattening to zero.
    # It is an explicit model choice naming y measures, never a server guess.
    right_names: list[str] = []
    if model_spec.get("rightAxis"):
        if base_type not in ("bar", "line", "area"):
            raise SpecError(
                f"a second axis needs a bar, line or area chart, not {chart_type!r}"
            )
        if stacked:
            raise SpecError("a stacked chart shares one total, so it cannot use a second axis")
        y_names = [column.name for column in y_columns]
        for name in model_spec["rightAxis"]:
            match = next((item for item in y_names if item.lower() == str(name).lower()), None)
            if match is None:
                raise SpecError(f"rightAxis column {name!r} is not one of this chart's y measures")
            if match != name:
                diagnostics.append(f"rightAxis column matched case-insensitively: {name} → {match}")
            if match in right_names:
                raise SpecError(f"rightAxis column {match!r} is listed more than once")
            right_names.append(match)
        if len(right_names) >= len(y_names):
            raise SpecError("rightAxis must leave at least one measure on the left axis")

    rows = table.rows
    if chart_type != "scatter" and rows:
        x_position = columns.index(x_column)
        if series_column is not None:
            series_position = columns.index(series_column)
            groups = _distinct((row[x_position], row[series_position]) for row in rows)
            if len(rows) > groups:
                diagnostics.append(
                    f"{len(rows)} rows grouped into {groups} x/series points using {aggregate}"
                )
        else:
            groups = _distinct(row[x_position] for row in rows)
            if len(rows) > groups:
                diagnostics.append(
                    f"{len(rows)} rows grouped into {groups} categories using {aggregate}"
                )
    if table.truncated:
        diagnostics.append(
            f"built from {table.returned_rows} of {table.total_rows} rows (cache limit)"
        )

    color_by = model_spec.get("colorBy")
    if color_by is None:
        color_by = (
            "category"
            if len(y_columns) == 1 and base_type in ("bar", "hbar", "pie", "donut")
            else "series"
        )
    show_legend = model_spec.get("showLegend")
    if show_legend is None:
        show_legend = base_type in PIE_TYPES or len(y_columns) > 1 or series_column is not None

    normalized: dict[str, Any] = {
        "type": base_type,
        "x": x_column.name,
        "y": [column.name for column in y_columns],
        "aggregate": aggregate,
        "sort": str(model_spec.get("sort") or "none"),
        "layout": str(model_spec.get("layout") or "full"),
        "valueFormat": str(model_spec.get("valueFormat") or "number"),
        "colorBy": color_by,
        "height": int(model_spec.get("height") or 300),
        "stacked": stacked,
        "smooth": bool(model_spec.get("smooth", base_type in ("line", "area"))),
        "showLegend": bool(show_legend),
        "showGrid": bool(model_spec.get("showGrid", True)),
    }
    for key in ("title", "subtitle", "xLabel", "yLabel", "currency"):
        if model_spec.get(key):
            normalized[key] = model_spec[key]
    if right_names:
        normalized["rightAxis"] = list(right_names)
        diagnostics.append(
            f"{', '.join(right_names)} plotted on a right axis (separate scale)"
        )
    if model_spec.get("rightYLabel"):
        normalized["rightYLabel"] = model_spec["rightYLabel"]
    if model_spec.get("limit"):
        normalized["limit"] = int(model_spec["limit"])
        if chart_type != "scatter":
            x_position = columns.index(x_column)
            if series_column is not None:
                series_position = columns.index(series_column)
                groups = _distinct((row[x_position], row[series_position]) for row in rows)
                unit = "x/series points"
            else:
                groups = _distinct(row[x_position] for row in rows)
                unit = "categories"
            if groups > int(model_spec["limit"]):
                diagnostics.append(
                    f"kept the top {int(model_spec['limit'])} of {groups} {unit}"
                )
    if model_spec.get("colors"):
        normalized["colors"] = list(model_spec["colors"])
    if series_column is not None:
        normalized["series"] = series_column.name
    if diagnostics:
        normalized["diagnostics"] = diagnostics
    return normalized, diagnostics


def validate_table(spec: dict[str, Any]) -> dict[str, Any]:
    """The canonical table spec, or a :class:`SpecError`."""
    # An error block re-validates to itself, so a second pass cannot turn a
    # refusal into a bare table placeholder. Only the server's own block — an
    # error and nothing else — is honoured.
    if spec.get("error") and set(spec) == {"error"}:
        raise SpecError(str(spec["error"]))
    model_spec = {key: value for key, value in spec.items() if key not in SERVER_SPEC_KEYS}
    chart_fields = sorted(set(model_spec) & _CHART_ONLY_KEYS)
    if chart_fields:
        raise SpecError(
            f"this is a table block but its spec has chart fields ({', '.join(chart_fields)}); "
            f"write a ```chart fence to draw a chart"
        )
    problem = _schema_error(_TABLE_VALIDATOR, model_spec)
    if problem:
        raise SpecError(problem)
    normalized: dict[str, Any] = {"layout": str(model_spec.get("layout") or "full")}
    if model_spec.get("title"):
        normalized["title"] = model_spec["title"]
    if model_spec.get("pageLength"):
        normalized["pageLength"] = int(model_spec["pageLength"])
    return normalized


def _check_measure(columns: list[Column], name: str, aggregate: str) -> Column:
    """The measure column ``name`` refers to, checked against ``aggregate``."""
    column, _ = _resolve(columns, name)
    if aggregate == "count":
        if column.type == "unknown":
            raise SpecError(
                f"metric column {column.name!r} has no values to count; use a column with values"
            )
        return column
    if column.type not in NUMERIC_TYPES:
        raise SpecError(
            f"metric column {column.name!r} is {column.type}, not a measure; "
            f"chart a numeric column instead"
        )
    return column


def validate_card(spec: dict[str, Any], table: ToolData) -> dict[str, Any]:
    """The canonical KPI card spec, or a :class:`SpecError`."""
    if spec.get("error") and set(spec) == {"error"}:
        raise SpecError(str(spec["error"]))
    model_spec = {key: value for key, value in spec.items() if key not in SERVER_SPEC_KEYS}
    problem = _schema_error(_CARD_VALIDATOR, model_spec)
    if problem:
        raise SpecError(problem)
    columns = list(table.columns)
    metric = _check_measure(columns, str(model_spec["metric"]), str(model_spec["aggregate"]))
    diagnostics: list[str] = []
    normalized: dict[str, Any] = {
        "metric": metric.name,
        "aggregate": str(model_spec["aggregate"]),
        "layout": str(model_spec.get("layout") or "full"),
        "valueFormat": str(model_spec.get("valueFormat") or "number"),
    }
    if model_spec.get("deltaMetric"):
        delta = _check_measure(
            columns, str(model_spec["deltaMetric"]), str(model_spec.get("deltaAggregate") or "sum")
        )
        normalized["deltaMetric"] = delta.name
        normalized["deltaAggregate"] = str(model_spec.get("deltaAggregate") or "sum")
    spark = model_spec.get("spark")
    if isinstance(spark, dict) and spark.get("x"):
        x_column, _ = _resolve(columns, str(spark["x"]))
        normalized["spark"] = {
            "x": x_column.name,
            "type": str(spark.get("type") or "area"),
        }
    for key in ("title", "subtitle", "hint", "currency", "icon", "color"):
        if model_spec.get(key):
            normalized[key] = model_spec[key]
    if not table.rows:
        diagnostics.append("the cached result has no rows; the card shows 0")
    if table.truncated:
        diagnostics.append(
            f"built from {table.returned_rows} of {table.total_rows} rows (cache limit)"
        )
    if diagnostics:
        normalized["diagnostics"] = diagnostics
    return normalized


def validate_list(spec: dict[str, Any], table: ToolData) -> dict[str, Any]:
    """The canonical leaderboard spec, or a :class:`SpecError`."""
    if spec.get("error") and set(spec) == {"error"}:
        raise SpecError(str(spec["error"]))
    model_spec = {key: value for key, value in spec.items() if key not in SERVER_SPEC_KEYS}
    problem = _schema_error(_LIST_VALIDATOR, model_spec)
    if problem:
        raise SpecError(problem)
    columns = list(table.columns)
    label_column, _ = _resolve(columns, str(model_spec["label"]))
    if label_column.type == "unknown":
        raise SpecError(f"label column {label_column.name!r} has no values to list")
    value_column = _check_measure(columns, str(model_spec["value"]), str(model_spec["aggregate"]))
    if label_column.name == value_column.name:
        raise SpecError(f"column {label_column.name!r} cannot be both label and value")
    normalized: dict[str, Any] = {
        "label": label_column.name,
        "value": value_column.name,
        "aggregate": str(model_spec["aggregate"]),
        "limit": int(model_spec.get("limit") or 8),
        "layout": str(model_spec.get("layout") or "full"),
        "valueFormat": str(model_spec.get("valueFormat") or "number"),
    }
    for key in ("title", "subtitle", "currency", "color", "variant"):
        if model_spec.get(key):
            normalized[key] = model_spec[key]
    if model_spec.get("showShare") is not None:
        normalized["showShare"] = bool(model_spec["showShare"])
    if table.truncated:
        normalized["diagnostics"] = [
            f"built from {table.returned_rows} of {table.total_rows} rows (cache limit)"
        ]
    return normalized


def validate_progress(spec: dict[str, Any], table: ToolData) -> dict[str, Any]:
    """The canonical progress-to-target spec, or a :class:`SpecError`."""
    if spec.get("error") and set(spec) == {"error"}:
        raise SpecError(str(spec["error"]))
    model_spec = {key: value for key, value in spec.items() if key not in SERVER_SPEC_KEYS}
    problem = _schema_error(_PROGRESS_VALIDATOR, model_spec)
    if problem:
        raise SpecError(problem)
    try:
        target = float(model_spec["target"])
    except (TypeError, ValueError):
        raise SpecError("target must be a number")
    import math as _math

    if not _math.isfinite(target) or target <= 0:
        raise SpecError("target must be a positive number")
    columns = list(table.columns)
    metric = _check_measure(columns, str(model_spec["metric"]), str(model_spec["aggregate"]))
    normalized: dict[str, Any] = {
        "metric": metric.name,
        "aggregate": str(model_spec["aggregate"]),
        "target": target,
        "layout": str(model_spec.get("layout") or "full"),
        "valueFormat": str(model_spec.get("valueFormat") or "number"),
    }
    for key in ("title", "subtitle", "currency", "color"):
        if model_spec.get(key):
            normalized[key] = model_spec[key]
    if table.truncated:
        normalized["diagnostics"] = [
            f"built from {table.returned_rows} of {table.total_rows} rows (cache limit)"
        ]
    return normalized


def validate_note(spec: dict[str, Any]) -> dict[str, Any]:
    """The canonical textbox spec. It carries no data reference."""
    if spec.get("error") and set(spec) == {"error"}:
        raise SpecError(str(spec["error"]))
    model_spec = {key: value for key, value in spec.items() if key not in SERVER_SPEC_KEYS}
    problem = _schema_error(_NOTE_VALIDATOR, model_spec)
    if problem:
        raise SpecError(problem)
    normalized: dict[str, Any] = {
        "style": str(model_spec.get("style") or "info"),
        "layout": str(model_spec.get("layout") or "full"),
    }
    if model_spec.get("title"):
        normalized["title"] = model_spec["title"]
    if model_spec.get("body"):
        normalized["body"] = model_spec["body"]
    if not normalized.get("title") and not normalized.get("body"):
        raise SpecError("a note needs a title and/or a body to show")
    return normalized


# ------------------------------------------------------------------ validation


def validate_reply(
    text: str | None,
    scope: ToolDataScope | None,
    store: ToolDataStore | None = None,
) -> list[dict[str, Any]]:
    """The chart/table blocks in ``text`` that cannot be drawn from their data.

    Returns one ``{"kind", "ref", "error"}`` per problem block, in order. A
    reference that matches nothing is reported too: the model wrote it wrong
    (usually by appending a suffix to the ref the tool printed), and it can fix
    the reply far better than the user can.
    """
    cache = store or TOOL_DATA_STORE
    problems: list[dict[str, Any]] = []
    for block in extract_blocks(text or ""):
        if block.kind == "note":
            try:
                validate_note(block.spec)
            except SpecError as exc:
                problems.append({"kind": block.kind, "ref": block.ref, "error": str(exc)})
            continue
        table = cache.resolve(scope, block.ref)
        if table is None:
            problems.append(
                {
                    "kind": block.kind,
                    "ref": block.ref,
                    "error": (
                        f"no cached result matches the reference {block.ref!r}; use the exact "
                        "reference the tool result printed (for example D1), with no suffix"
                    ),
                }
            )
            continue
        try:
            if block.kind == "chart":
                validate_chart(block.spec, table)
            elif block.kind == "card":
                validate_card(block.spec, table)
            elif block.kind == "list":
                validate_list(block.spec, table)
            elif block.kind == "progress":
                validate_progress(block.spec, table)
            else:
                validate_table(block.spec)
        except SpecError as exc:
            problems.append({"kind": block.kind, "ref": block.ref, "error": str(exc)})
    return problems


def chart_feedback(problems: list[dict[str, Any]]) -> str:
    """The correction message sent back to the model when a block cannot be drawn.

    It asks for *only* the broken block(s), not the whole answer: the server
    keeps the previous reply and splices each corrected block back into it
    (see ``repair.splice_blocks``), so re-sending the nine blocks that already
    draw wastes tokens and risks the model dropping or rewording them. The
    ``[chart-repair]`` prefix marks this as a platform notice, so the model never
    mistakes it for something the user said.
    """
    lines = [
        "[chart-repair] Your last reply's chart/table block(s) below could not be drawn from the "
        "data they reference. Reply again with ONLY the corrected block line(s) — one "
        "#CHART_/#TABLE_/#CARD_/#LIST_/#PROGRESS_ line plus its JSON object per problem — "
        "and nothing else. Do not repeat the explanation or the blocks that already draw, "
        "do not drop or summarise anything, and do not call the tool again: "
        "the data is already cached and the server will splice your corrected block(s) "
        "back into your previous reply.",
        "",
    ]
    for problem in problems:
        ref = str(problem.get("ref") or "")
        label = f"#{str(problem['kind']).upper()}" + (f"_{ref}" if ref else "")
        lines.append(f"- {label}: {problem['error']}")
    return "\n".join(lines)
