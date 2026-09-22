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
SORTS = ("asc", "desc", "x", "none")
FORMATS = ("number", "compact", "percent", "currency")
COLOR_BY = ("category", "series", "single")
LAYOUTS = ("full", "half")

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

_CHART_VALIDATOR = Draft202012Validator(CHART_SPEC_SCHEMA)
_TABLE_VALIDATOR = Draft202012Validator(TABLE_SPEC_SCHEMA)

_TABLE_KEYS = frozenset(TABLE_SPEC_SCHEMA["properties"])
#: Fields that only make sense on a chart. A table block carrying them is the
#: model using the wrong fence, which is worth saying in those words.
_CHART_ONLY_KEYS = frozenset(CHART_SPEC_SCHEMA["properties"]) - _TABLE_KEYS

FENCE_OPEN = re.compile(r"^(`{3,}|~{3,})(.*)$")
FENCE_CLOSE = re.compile(r"^(`{3,}|~{3,})\s*$")
TOKEN = re.compile(r"^\s*#(CHART|TABLE)_([A-Za-z0-9][A-Za-z0-9_-]*)\s*(.*)$")
_DATA_FENCES = frozenset({"chart", "table"})


class SpecError(ValueError):
    """A spec that cannot be drawn from the data it names."""


@dataclass
class Block:
    """One chart/table block parsed out of a reply."""

    kind: str
    ref: str
    spec: dict[str, Any]
    raw: str


def _fence_language(info: str) -> str:
    """The first word of a fence info string.

    Splits on whitespace **or** ``{`` so ```` ```chart{…} ```` is recognised as a
    chart fence here exactly as the client and the reference scan recognise it.
    """
    stripped = info.strip()
    if not stripped:
        return ""
    return re.split(r"[\s{]", stripped, maxsplit=1)[0].lower()


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


def _block_from_body(body: list[str]) -> Block | None:
    start = next((index for index, line in enumerate(body) if line.strip()), -1)
    if start < 0:
        return None
    match = TOKEN.match(body[start])
    if match is None:
        return None
    rest = "\n".join([match.group(3) or "", *body[start + 1 :]])
    kind = "chart" if match.group(1).upper() == "CHART" else "table"
    return Block(kind=kind, ref=match.group(2), spec=_parse_object(rest), raw="\n".join(body))


def split_reply(text: str) -> list[Block | str]:
    """The reply as an ordered list of markdown strings and data blocks.

    Mirrors the client parser's block boundaries. Only the two documented forms
    are blocks — a fenced ``chart``/``table`` body whose first line is the
    placeholder, or a bare placeholder line. Everything else, including a
    placeholder written inside an ordinary code fence, is prose.
    """
    lines = (text or "").replace("\r\n", "\n").split("\n")
    segments: list[Block | str] = []
    buffer: list[str] = []

    def flush() -> None:
        if buffer:
            joined = "\n".join(buffer)
            if joined.strip():
                segments.append(joined)
            buffer.clear()

    index = 0
    while index < len(lines):
        line = lines[index]
        opener = FENCE_OPEN.match(line.strip())
        if opener:
            marker = opener.group(1)[0]
            length = len(opener.group(1))
            language = _fence_language(opener.group(2) or "")
            end = index + 1
            closed = False
            while end < len(lines):
                closer = FENCE_CLOSE.match(lines[end].strip())
                if closer and closer.group(1)[0] == marker and len(closer.group(1)) >= length:
                    closed = True
                    break
                end += 1
            stop = end if closed else len(lines)
            if language in _DATA_FENCES:
                block = _block_from_body(lines[index + 1 : stop])
                if block is not None:
                    flush()
                    segments.append(block)
                    index = stop + 1 if closed else len(lines)
                    continue
            buffer.extend(lines[index:stop])
            index = stop + 1 if closed else len(lines)
            continue

        token = TOKEN.match(line)
        if token:
            kind = "chart" if token.group(1).upper() == "CHART" else "table"
            inline = (token.group(3) or "").strip()
            cursor = index
            if not inline:
                # A spec may follow on the next line, but only when that line
                # actually opens one: a bare placeholder followed by prose must
                # not swallow the prose into an empty spec.
                if index + 1 < len(lines) and lines[index + 1].strip().startswith("{"):
                    cursor = index + 1
                    inline = lines[cursor]
            while inline and _balanced_json(inline) is None and cursor + 1 < len(lines):
                cursor += 1
                inline = f"{inline}\n{lines[cursor]}"
            flush()
            segments.append(Block(kind=kind, ref=token.group(2), spec=_parse_object(inline), raw=line))
            index = cursor + 1
            continue

        buffer.append(line)
        index += 1
    flush()
    return _collapse_bare_lead_ins(segments)


def _collapse_bare_lead_ins(segments: list[Block | str]) -> list[Block | str]:
    """Drop a bare placeholder that the fenced block right after it repeats."""
    cleaned: list[Block | str] = []
    for index, segment in enumerate(segments):
        following = segments[index + 1] if index + 1 < len(segments) else None
        if (
            isinstance(segment, Block)
            and not segment.spec
            and isinstance(following, Block)
            and following.kind == segment.kind
            and following.ref == segment.ref
        ):
            continue
        cleaned.append(segment)
    return cleaned


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


# ------------------------------------------------------------------- rewriting


def _render_block(block: Block, spec: dict[str, Any], ref: str) -> str:
    token = f"#{'CHART' if block.kind == 'chart' else 'TABLE'}_{ref}"
    # A table with nothing to say but the default layout is written as the bare
    # placeholder; anything else must keep its fence so the field is not lost.
    if block.kind == "table" and spec == {"layout": "full"}:
        return token
    body = json.dumps(spec, ensure_ascii=False, indent=2)
    return f"```{block.kind}\n{token}\n{body}\n```"


def normalize_reply(
    text: str | None,
    scope: ToolDataScope | None,
    store: ToolDataStore | None = None,
) -> tuple[str, list[dict[str, Any]]]:
    """Rewrite every resolvable chart/table block with its validated spec.

    Returns the rewritten reply and a per-block report (for tracing and tests).
    A block whose reference has no cached data is left as the model wrote it, so
    the chat can say the data is gone rather than inventing a chart.
    """
    cache = store or TOOL_DATA_STORE
    segments = split_reply(text or "")
    rendered: list[str] = []
    report: list[dict[str, Any]] = []
    for segment in segments:
        if isinstance(segment, str):
            rendered.append(segment)
            continue
        table = cache.resolve(scope, segment.ref)
        if table is None:
            report.append({"kind": segment.kind, "ref": segment.ref, "status": "unresolved"})
            rendered.append(segment.raw)
            continue
        try:
            if segment.kind == "chart":
                spec, diagnostics = validate_chart(segment.spec, table)
            else:
                spec, diagnostics = validate_table(segment.spec), []
            report.append(
                {"kind": segment.kind, "ref": segment.ref, "status": "ok", "diagnostics": diagnostics}
            )
        except SpecError as exc:
            report.append(
                {"kind": segment.kind, "ref": segment.ref, "status": "error", "error": str(exc)}
            )
            spec = {"error": str(exc)}
        # The token is rewritten to the resolved reference, so a reply that used
        # the provider's long call id still names the data the payload is keyed by.
        rendered.append(_render_block(segment, spec, table.key))
    return "\n\n".join(part for part in rendered if part.strip()), report
