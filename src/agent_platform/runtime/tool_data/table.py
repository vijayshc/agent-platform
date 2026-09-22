"""Model-facing rendering of a typed tool table.

The data channel is the typed contract (:mod:`src.utils.tool_data_contract`).
This module only turns a declared table into the text the model reads — a
markdown sample and the reference marker — and that rendering is never parsed
back. Escaping therefore exists to keep the markdown intact, not to make it
machine-readable.
"""

from __future__ import annotations

from typing import Any

from src.utils.tool_data_contract import (  # re-exported for the runtime package
    COLUMN_TYPES,
    ERROR_KIND,
    NUMERIC_TYPES,
    TEMPORAL_TYPES,
    Column,
    ToolDataContractError,
    ToolDataTable,
    apply_declared_types,
    build_table,
    columns_from_payload,
    columns_to_json,
    contract_kind,
    escape_cell,
    parse_contract,
    render_markdown,
)

__all__ = [
    "COLUMN_TYPES",
    "ERROR_KIND",
    "NUMERIC_TYPES",
    "TEMPORAL_TYPES",
    "Column",
    "ToolDataContractError",
    "ToolDataTable",
    "apply_declared_types",
    "build_table",
    "columns_from_payload",
    "columns_to_json",
    "contract_kind",
    "message_text",
    "parse_contract",
    "render_marker",
    "render_sample",
]


def render_sample(
    table: ToolDataTable,
    *,
    sample_rows: int,
    total_rows: int,
    cache_rows: int,
) -> str:
    """A markdown table with the header plus at most ``sample_rows`` data rows.

    The omitted-row count is stated so the model knows the result was clipped and
    how large the full result is.
    """
    taken = table.rows[: max(0, sample_rows)]
    rendered = render_markdown(table.columns, taken)
    omitted = total_rows - len(taken)
    if omitted > 0:
        rendered += (
            f"\n\n… {omitted} more row{'s' if omitted != 1 else ''} omitted "
            f"({total_rows} total rows, {cache_rows} cached)."
        )
    return rendered


def render_marker(
    ref: str,
    *,
    tool_name: str,
    columns: list[Column],
    total_rows: int,
    sample_rows: int,
) -> str:
    """The machine-readable footer every cached data result carries.

    The column list is part of the marker because a reference outlives the turn
    that created it: a model asked to re-plot ``D1`` nine turns later must know
    what ``D1`` holds without the original result still being in context.
    """
    schema = ", ".join(f"{column.name} ({column.type})" for column in columns)
    return "\n".join(
        [
            f"[data_ref={ref}]",
            f"[tool_data: {tool_name} — {total_rows} rows cached, {sample_rows} shown]",
            f"[columns: {schema}]",
            f"render with #TABLE_{ref} or #CHART_{ref}",
        ]
    )


def message_text(content: Any) -> str:
    """Flatten a tool message's content (str or content-block list) to text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") in (None, "text"):
                parts.append(str(block.get("text") or ""))
        return "".join(parts)
    if content is None:
        return ""
    return str(content)
