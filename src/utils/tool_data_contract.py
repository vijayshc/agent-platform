"""The typed table contract shared by data tools and the runtime.

A tool result is the one place that knows what its columns *are*: whether a value
is a measure, a label, a date or a missing value. This module is that knowledge,
in one place, so a producer (an MCP tool) and its consumer (the agent runtime and
the chat) cannot disagree.

Producer side — a tool turns native values into a declared table::

    from src.utils.tool_data_contract import build_table

    table = build_table(["month", "revenue"], [["2024-01-01", 1234.5]])
    return table.to_contract()          # -> MCP structuredContent

Consumer side — the runtime accepts only that shape, strictly::

    table = parse_contract(structured)  # None if not a table; raises if malformed

The module imports nothing outside the standard library, so a standalone stdio
MCP server can use it without pulling in the agent runtime.
"""

from __future__ import annotations

import datetime as _dt
import decimal
import json
import math
import uuid
from dataclasses import dataclass, field
from typing import Any, Iterable

#: The envelope discriminator and its version. A tool that returns a different
#: ``kind`` is not a data table and is left alone by the runtime.
CONTRACT_KIND = "tool_data_table"
#: A tool's own failure, in the same structured channel. It carries no table, so
#: the runtime passes the tool's message through instead of treating the missing
#: table as a producer bug.
ERROR_KIND = "tool_data_error"
CONTRACT_VERSION = 1

#: The complete set of column types.
#:
#: ``decimal`` is an exact number carried as a string: JSON numbers are IEEE-754
#: doubles, so a NUMERIC column or an integer wider than 2**53 would be silently
#: rounded if it travelled as a JSON number. ``mixed`` is a column whose cells are
#: not all the same runtime type (SQLite columns are dynamically typed); each cell
#: keeps the scalar it actually is. ``unknown`` is a column whose values are all
#: null: it is not a measure and not a label.
COLUMN_TYPES = frozenset(
    {
        "string",
        "integer",
        "number",
        "decimal",
        "boolean",
        "date",
        "datetime",
        "time",
        "mixed",
        "unknown",
    }
)

#: Types a chart may use as a measure.
NUMERIC_TYPES = frozenset({"integer", "number", "decimal"})
#: Types a chart may use as a time axis.
TEMPORAL_TYPES = frozenset({"date", "datetime", "time"})

#: The largest integer JSON/JavaScript represents exactly.
MAX_SAFE_INTEGER = 2**53


class ToolDataContractError(ValueError):
    """A tool claimed the typed-table contract but violated it."""


@dataclass(frozen=True)
class Column:
    """One declared result column."""

    name: str
    type: str

    def to_dict(self) -> dict[str, str]:
        return {"name": self.name, "type": self.type}

    @staticmethod
    def from_dict(raw: Any) -> "Column":
        if not isinstance(raw, dict):
            raise ToolDataContractError(f"column entry must be an object, got {type(raw).__name__}")
        name = raw.get("name")
        ctype = raw.get("type")
        if not isinstance(name, str) or not name.strip():
            raise ToolDataContractError("column 'name' must be a non-empty string")
        if ctype not in COLUMN_TYPES:
            raise ToolDataContractError(
                f"column {name!r} has type {ctype!r}; expected one of {sorted(COLUMN_TYPES)}"
            )
        return Column(name=name.strip(), type=str(ctype))


@dataclass
class ToolDataTable:
    """A tool result: declared columns, native rows, and the full row count."""

    columns: list[Column]
    rows: list[list[Any]] = field(default_factory=list)
    total_rows: int = 0

    @property
    def returned_rows(self) -> int:
        return len(self.rows)

    @property
    def truncated(self) -> bool:
        return self.returned_rows < self.total_rows

    def column(self, name: str) -> Column | None:
        for column in self.columns:
            if column.name == name:
                return column
        return None

    def to_contract(self) -> dict[str, Any]:
        """The MCP structured-content envelope for this table."""
        return {
            "kind": CONTRACT_KIND,
            "version": CONTRACT_VERSION,
            "columns": [column.to_dict() for column in self.columns],
            "rows": self.rows,
            "total_rows": self.total_rows,
        }


# --------------------------------------------------------------- producer side


def _derive_type(values: list[Any]) -> str:
    """The declared type of a column, from the values the driver returned.

    The check is on the **runtime type of the value**, never on what a string
    looks like. ``isinstance(value, int)`` is a fact about the data; deciding
    that ``"2024-01-05"`` is a date because it matches a pattern is a guess, and
    a guess is exactly what this contract exists to remove. A text column is
    text — a database that wants a time axis declares one by returning a
    ``date``/``datetime`` object.

    Two representation limits move a column off the plain numeric types:

    * an exact ``Decimal`` (or an integer too large for IEEE-754) travels as
      ``decimal``, a string, because a JSON number would round it;
    * a column whose cells are not all one runtime type travels as ``mixed``,
      each cell keeping the scalar it actually is.
    """
    if not values:
        return "unknown"
    if all(isinstance(value, bool) for value in values):
        return "boolean"
    if all(isinstance(value, int) and not isinstance(value, bool) for value in values):
        return "decimal" if any(abs(value) > MAX_SAFE_INTEGER for value in values) else "integer"
    if all(isinstance(value, (int, decimal.Decimal)) and not isinstance(value, bool) for value in values):
        return "decimal"
    if all(
        isinstance(value, (int, float, decimal.Decimal)) and not isinstance(value, bool)
        for value in values
    ):
        # A float is only safe as a JSON number while every other value is small
        # enough to be exact. One Decimal or one integer beyond 2**53 makes the
        # whole column `decimal`, so nothing in it rounds.
        if any(
            isinstance(value, decimal.Decimal)
            or (
                isinstance(value, int)
                and not isinstance(value, bool)
                and abs(value) > MAX_SAFE_INTEGER
            )
            for value in values
        ):
            return "decimal"
        return "number"
    if all(isinstance(value, _dt.datetime) for value in values):
        return "datetime"
    if all(isinstance(value, _dt.date) and not isinstance(value, _dt.datetime) for value in values):
        return "date"
    if all(isinstance(value, _dt.time) for value in values):
        return "time"
    if all(isinstance(value, (str, bytes, bytearray, uuid.UUID)) for value in values):
        return "string"
    if all(isinstance(value, (dict, list)) for value in values):
        # A JSON document (a JSONB column, an array column) carried as its JSON
        # text: it is not a scalar, so it is not a measure and not a label.
        return "string"
    # SQLite columns are dynamically typed, so a column can genuinely hold, say,
    # an integer in one row and text in the next. It is neither a measure nor a
    # label, and stringifying the numbers would lose them: ``mixed`` keeps every
    # cell as the scalar it is (a JSON document as its JSON text).
    return "mixed"


def _json_default(value: Any) -> str:
    """A JSON encoder fallback that keeps temporal values ISO-8601."""
    if isinstance(value, _dt.datetime):
        if value.tzinfo is not None:
            value = value.astimezone(_dt.timezone.utc)
        return value.isoformat()
    if isinstance(value, (_dt.date, _dt.time)):
        return value.isoformat()
    if isinstance(value, (bytes, bytearray)):
        return bytes(value).decode("utf-8", "replace")
    return str(value)


def _json_scalar(value: Any) -> Any:
    """Any driver value as a JSON-safe scalar.

    This is total on purpose: whatever a driver returns must survive
    ``json.dumps`` and satisfy :func:`parse_contract`. A temporal object becomes
    its ISO-8601 string, a mapping/sequence its JSON text, and anything else
    exotic its string form.
    """
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (bytes, bytearray)):
        return bytes(value).decode("utf-8", "replace")
    if isinstance(value, _dt.datetime):
        if value.tzinfo is not None:
            value = value.astimezone(_dt.timezone.utc)
        return value.isoformat()
    if isinstance(value, (_dt.date, _dt.time)):
        return value.isoformat()
    if isinstance(value, (dict, list, tuple)):
        try:
            return json.dumps(
                value, ensure_ascii=False, default=_json_default, allow_nan=False
            )
        except ValueError as exc:
            raise ToolDataContractError(f"a JSON value that cannot be represented: {exc}") from exc
    return str(value)


def _normalize(value: Any, ctype: str) -> Any:
    """One native cell as the JSON scalar its declared type promises."""
    if value is None:
        return None
    if ctype == "unknown":
        return None
    if ctype == "boolean":
        return bool(value)
    if ctype == "integer":
        return int(value)
    if ctype == "number":
        number = float(value)
        if not math.isfinite(number):
            raise ToolDataContractError(
                f"a non-finite number ({number!r}) cannot be represented in JSON"
            )
        return number
    if ctype == "decimal":
        # Exact, and a string so no consumer rounds it.
        try:
            number = value if isinstance(value, decimal.Decimal) else decimal.Decimal(str(value))
        except decimal.InvalidOperation as exc:
            raise ToolDataContractError(f"a decimal value must be numeric, got {value!r}") from exc
        if not number.is_finite():
            raise ToolDataContractError(
                f"a non-finite decimal ({value!r}) cannot be represented in JSON"
            )
        return str(number)
    if ctype in ("date", "datetime", "time"):
        if isinstance(value, str):
            return value
        if ctype == "datetime" and value.tzinfo is not None:
            # Keep the instant and its offset: a naive UTC value would be read
            # back in the viewer's local zone and land on another day.
            value = value.astimezone(_dt.timezone.utc)
        return value.isoformat()
    if ctype == "mixed":
        scalar = _json_scalar(value)
        if isinstance(scalar, float) and not math.isfinite(scalar):
            raise ToolDataContractError(
                f"a non-finite number ({scalar!r}) cannot be represented in JSON"
            )
        if isinstance(scalar, int) and not isinstance(scalar, bool) and abs(scalar) > MAX_SAFE_INTEGER:
            # Same reason as the `decimal` column: a JSON number this wide is
            # rounded by the browser, so it travels as its exact digits.
            return str(scalar)
        return scalar
    return _json_scalar(value) if not isinstance(value, str) else value


def build_table(columns: Iterable[str], rows: Iterable[Iterable[Any]]) -> ToolDataTable:
    """Declare and normalize a result set from its native values.

    This is the producer's whole job: hand it the column names and the values the
    database driver returned, and it produces a contract table whose types are
    derived from those values and whose cells are JSON-safe.
    """
    names = [str(name) for name in columns]
    if not names:
        raise ToolDataContractError("a table needs at least one column")
    if len(set(names)) != len(names):
        raise ToolDataContractError(f"column names must be unique, got {names}")
    materialized = [list(row) for row in rows]
    width = len(names)
    for index, row in enumerate(materialized):
        if len(row) != width:
            raise ToolDataContractError(
                f"row {index} has {len(row)} cells but the header has {width} columns"
            )
    declared: list[Column] = []
    for position, name in enumerate(names):
        present = [row[position] for row in materialized if row[position] is not None]
        declared.append(Column(name=name, type=_derive_type(present)))
    normalized = [
        [_normalize(value, declared[position].type) for position, value in enumerate(row)]
        for row in materialized
    ]
    return ToolDataTable(columns=declared, rows=normalized, total_rows=len(normalized))


def _parses_as(value: Any, ctype: str) -> bool:
    """True when ``value`` can be read as the temporal type ``ctype``."""
    if ctype == "date":
        if isinstance(value, str):
            try:
                _dt.date.fromisoformat(value)
            except ValueError:
                return False
            return True
        # A datetime is not a date, even though it subclasses one.
        return isinstance(value, _dt.date) and not isinstance(value, _dt.datetime)
    if ctype == "datetime":
        if isinstance(value, str):
            try:
                _dt.datetime.fromisoformat(value)
            except ValueError:
                return False
            return True
        return isinstance(value, _dt.datetime)
    if ctype == "time":
        if isinstance(value, str):
            try:
                _dt.time.fromisoformat(value)
            except ValueError:
                return False
            return True
        return isinstance(value, _dt.time)
    return False


def _consistent_with(values: list[Any], declared_type: str) -> bool:
    """True when every value could be a value of ``declared_type``.

    This is what lets the schema's type win over the runtime type without
    mistyping an expression: `is_active` holding 0/1 is consistent with the
    schema's BOOLEAN, but `COUNT(*) AS is_active` holding 5 is not, so the count
    keeps its value type. SQLite's 0/1 booleans are the reason the check exists.
    """
    present = [value for value in values if value is not None]
    if not present:
        return True
    if declared_type == "boolean":
        return all(
            isinstance(value, bool)
            or (isinstance(value, int) and not isinstance(value, bool) and value in (0, 1))
            for value in present
        )
    if declared_type == "integer":
        return all(isinstance(value, int) and not isinstance(value, bool) for value in present)
    if declared_type in ("number", "decimal"):
        return all(
            isinstance(value, (int, float, decimal.Decimal)) and not isinstance(value, bool)
            for value in present
        )
    if declared_type in ("date", "datetime", "time"):
        return all(_parses_as(value, declared_type) for value in present)
    if declared_type == "string":
        return all(isinstance(value, (str, bytes, bytearray, uuid.UUID)) for value in present)
    return False


def apply_declared_types(table: ToolDataTable, declared: dict[str, str]) -> ToolDataTable:
    """Give a column the schema's type wherever the values are consistent with it.

    A column the values cannot type (empty result, all null) always takes the
    declared type. A column with values takes it too when every value could be a
    value of that type — so a SQLite ``BOOLEAN`` holding 0/1 is a boolean, while
    ``COUNT(*) AS is_active`` holding 5 keeps its value type. A declared type the
    values contradict is not applied; the returned values are the truth about the
    result set.
    """
    columns: list[Column] = []
    rows = [list(row) for row in table.rows]
    changed = False
    for index, column in enumerate(table.columns):
        target = column.type
        wanted = declared.get(column.name)
        if wanted in COLUMN_TYPES and wanted != column.type:
            values = [row[index] for row in rows]
            if column.type == "unknown" or _consistent_with(values, wanted):
                target = wanted
        if target != column.type:
            changed = True
            for row in rows:
                row[index] = _normalize(row[index], target)
        columns.append(Column(column.name, target))
    if not changed:
        return table
    return ToolDataTable(columns=columns, rows=rows, total_rows=table.total_rows)


# --------------------------------------------------------------- consumer side


def _check_cell(column: Column, value: Any, row_index: int) -> None:
    ctype = column.type
    if value is None:
        return
    where = f"row {row_index}, column {column.name!r}"
    if ctype == "integer":
        if isinstance(value, bool) or not isinstance(value, int):
            raise ToolDataContractError(f"{where} is declared integer but holds {type(value).__name__}")
    elif ctype == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ToolDataContractError(f"{where} is declared number but holds {type(value).__name__}")
        if not math.isfinite(float(value)):
            raise ToolDataContractError(f"{where} is a non-finite number, which JSON cannot carry")
    elif ctype == "decimal":
        if not isinstance(value, str):
            raise ToolDataContractError(f"{where} is declared decimal but holds {type(value).__name__}")
        try:
            number = decimal.Decimal(value)
        except decimal.InvalidOperation as exc:
            raise ToolDataContractError(f"{where} is declared decimal but is not a number: {value!r}") from exc
        if not number.is_finite():
            raise ToolDataContractError(f"{where} is a non-finite decimal, which JSON cannot carry")
    elif ctype == "mixed":
        if isinstance(value, float) and not math.isfinite(value):
            raise ToolDataContractError(f"{where} is a non-finite number, which JSON cannot carry")
        if not isinstance(value, (str, int, float, bool)):
            raise ToolDataContractError(
                f"{where} is declared mixed but holds {type(value).__name__}"
            )
    elif ctype == "boolean":
        if not isinstance(value, bool):
            raise ToolDataContractError(f"{where} is declared boolean but holds {type(value).__name__}")
    elif ctype == "string":
        if not isinstance(value, str):
            raise ToolDataContractError(f"{where} is declared string but holds {type(value).__name__}")
    elif ctype == "date":
        if not isinstance(value, str):
            raise ToolDataContractError(f"{where} is declared date but holds {type(value).__name__}")
        try:
            _dt.date.fromisoformat(value)
        except ValueError as exc:
            raise ToolDataContractError(f"{where} is declared date but is not ISO-8601: {value!r}") from exc
    elif ctype == "datetime":
        if not isinstance(value, str):
            raise ToolDataContractError(
                f"{where} is declared datetime but holds {type(value).__name__}"
            )
        try:
            _dt.datetime.fromisoformat(value)
        except ValueError as exc:
            raise ToolDataContractError(
                f"{where} is declared datetime but is not ISO-8601: {value!r}"
            ) from exc
    elif ctype == "time":
        if not isinstance(value, str):
            raise ToolDataContractError(f"{where} is declared time but is not a string")
        try:
            _dt.time.fromisoformat(value)
        except ValueError as exc:
            raise ToolDataContractError(f"{where} is declared time but is invalid: {value!r}") from exc
    else:  # unknown: only null is allowed, and that case returned above
        raise ToolDataContractError(f"{where} is declared unknown but holds a value")


def contract_kind(structured: Any) -> str | None:
    """The ``kind`` a structured payload declares, or ``None``."""
    if isinstance(structured, dict):
        kind = structured.get("kind")
        if isinstance(kind, str):
            return kind
    return None


def parse_contract(structured: Any) -> ToolDataTable | None:
    """The typed table in ``structured``, or ``None`` when it is not one.

    ``None`` means "this tool result carries no table" — a plain text answer or an
    error string — and the caller leaves it untouched. A payload that declares the
    contract and breaks it raises :class:`ToolDataContractError`.
    """
    if not isinstance(structured, dict) or structured.get("kind") != CONTRACT_KIND:
        return None
    version = structured.get("version")
    if version != CONTRACT_VERSION:
        raise ToolDataContractError(
            f"unsupported table contract version {version!r}; this runtime speaks {CONTRACT_VERSION}"
        )
    raw_columns = structured.get("columns")
    if not isinstance(raw_columns, list) or not raw_columns:
        raise ToolDataContractError("'columns' must be a non-empty list")
    columns = [Column.from_dict(raw) for raw in raw_columns]
    names = [column.name for column in columns]
    if len(set(names)) != len(names):
        raise ToolDataContractError(f"column names must be unique, got {names}")
    raw_rows = structured.get("rows")
    if not isinstance(raw_rows, list):
        raise ToolDataContractError("'rows' must be a list")
    width = len(columns)
    rows: list[list[Any]] = []
    for index, raw_row in enumerate(raw_rows):
        if not isinstance(raw_row, list):
            raise ToolDataContractError(f"row {index} must be a list, got {type(raw_row).__name__}")
        if len(raw_row) != width:
            raise ToolDataContractError(
                f"row {index} has {len(raw_row)} cells but the table declares {width} columns"
            )
        for column, value in zip(columns, raw_row):
            _check_cell(column, value, index)
        rows.append(list(raw_row))
    total = structured.get("total_rows")
    if not isinstance(total, int) or isinstance(total, bool) or total < len(rows):
        raise ToolDataContractError(
            f"'total_rows' must be an integer >= the {len(rows)} rows sent, got {total!r}"
        )
    return ToolDataTable(columns=columns, rows=rows, total_rows=total)


def columns_from_payload(raw: Any) -> list[Column]:
    """Declared columns from a wire/archive payload, strictly."""
    if not isinstance(raw, list) or not raw:
        raise ToolDataContractError("payload has no columns")
    return [Column.from_dict(entry) for entry in raw]


def columns_to_json(columns: list[Column]) -> str:
    import json

    return json.dumps([column.to_dict() for column in columns], ensure_ascii=False)


def escape_cell(value: Any) -> str:
    """A markdown-safe rendering of one contract cell.

    Both a producer (rendering its own result) and the runtime (rendering a
    sample) need this, and both must agree, so it lives with the contract. It is
    a *rendering* only: nothing ever parses the result back.
    """
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "true" if value else "false"
    text = value if isinstance(value, str) else str(value)
    text = text.replace("\\", "\\\\").replace("|", "\\|")
    return text.replace("\r\n", "\\n").replace("\n", "\\n").replace("\r", "\\n")


def render_markdown(columns: list[Column], rows: Iterable[Iterable[Any]]) -> str:
    """A complete markdown table for the declared columns and rows."""
    header = "| " + " | ".join(escape_cell(column.name) for column in columns) + " |"
    separator = "| " + " | ".join("---" for _ in columns) + " |"
    body = ["| " + " | ".join(escape_cell(cell) for cell in row) + " |" for row in rows]
    return "\n".join([header, separator, *body])
