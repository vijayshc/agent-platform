"""Parse a markdown table out of a tool result and render its sample.

Tool results that carry tabular data are expected in GFM markdown-table form
(the shape MCP database servers already return). Parsing is deliberately
tolerant: leading/trailing pipes are optional, cells are trimmed, and escaped
pipes (``\\|``) are kept inside a cell.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

_SEPARATOR_CELL = re.compile(r"^:?-{1,}:?$")


@dataclass
class ParsedTable:
    """One markdown table: the header columns and the data rows."""

    columns: list[str] = field(default_factory=list)
    rows: list[list[str]] = field(default_factory=list)


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
    return str(content or "")


def _split_row(line: str) -> list[str]:
    text = line.strip()
    if text.startswith("|"):
        text = text[1:]
    if text.endswith("|"):
        text = text[:-1]
    cells: list[str] = []
    buf: list[str] = []
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == "\\" and i + 1 < len(text) and text[i + 1] == "|":
            buf.append("|")
            i += 2
            continue
        if ch == "|":
            cells.append("".join(buf).strip())
            buf = []
            i += 1
            continue
        buf.append(ch)
        i += 1
    cells.append("".join(buf).strip())
    return cells


def _is_separator(line: str) -> bool:
    if "|" not in line or "-" not in line:
        return False
    cells = _split_row(line)
    if not cells:
        return False
    return all(_SEPARATOR_CELL.match(cell or "") for cell in cells)


def parse_markdown_table(text: str) -> ParsedTable | None:
    """The first markdown table in ``text``, or ``None`` when there is none."""
    if not text or "|" not in text:
        return None
    lines = text.splitlines()
    for i in range(len(lines) - 1):
        if "|" not in lines[i] or "|" not in lines[i + 1]:
            continue
        if not _is_separator(lines[i + 1]):
            continue
        columns = _split_row(lines[i])
        if not columns or not any(columns):
            continue
        rows: list[list[str]] = []
        for j in range(i + 2, len(lines)):
            line = lines[j]
            if not line.strip() or "|" not in line:
                break
            cells = _split_row(line)
            if len(cells) < len(columns):
                cells += [""] * (len(columns) - len(cells))
            rows.append(cells[: len(columns)])
        return ParsedTable(columns=columns, rows=rows)
    return None


def _render_row(cells: list[str]) -> str:
    safe = [str(cell).replace("|", "\\|") for cell in cells]
    return "| " + " | ".join(safe) + " |"


def render_sample(
    table: ParsedTable,
    *,
    sample_rows: int,
    total_rows: int,
    cache_rows: int,
) -> str:
    """A markdown table with the header plus at most ``sample_rows`` data rows.

    The omitted-row count is stated so the model knows the result was clipped and
    how large the full result is.
    """
    header = _render_row(table.columns)
    separator = _render_row(["---"] * len(table.columns))
    taken = table.rows[: max(0, sample_rows)]
    body = [_render_row(row) for row in taken]
    rendered = "\n".join([header, separator, *body])
    omitted = total_rows - len(taken)
    if omitted > 0:
        rendered += (
            f"\n\n… {omitted} more row{'s' if omitted != 1 else ''} omitted "
            f"({total_rows} total rows, {cache_rows} cached)."
        )
    return rendered


def render_marker(ref: str, *, tool_name: str, total_rows: int, sample_rows: int) -> str:
    """The machine-readable footer every cached data result carries.

    ``ref`` is the short conversation reference (``D1``) the model is asked to
    copy into a placeholder — never the provider's long tool call id.
    """
    lines = [f"[data_ref={ref}]"]
    if total_rows > 0:
        lines.append(
            f"[tool_data: {tool_name} — {total_rows} rows cached, "
            f"{sample_rows} shown; render with #TABLE_{ref} or #CHART_{ref}]"
        )
    return "\n".join(lines)
