"""Tabular ingestion for the Knowledge base (CSV / TSV / Excel).

A spreadsheet is not prose: splitting it with a character chunker scrambles
rows.  Instead each record becomes one embeddable chunk, where the caller picks
which columns carry the searchable *data* and which are *metadata* that must be
preserved but never embedded.

``build_row_chunks`` therefore returns ``{"content", "metadata"}`` records:
``content`` is the rendered data columns (embedded and stored as text),
``metadata`` is the raw metadata columns (stored structurally and shown next to
retrieval hits).  All cell values are read as text so leading zeros, dates and
long ids survive round-tripping.
"""

from __future__ import annotations

from typing import Any, Dict, List

import pandas as pd

#: Extensions we parse as row-oriented records rather than prose.
TABULAR_CONTENT_TYPES = {"csv", "tsv", "xlsx", "xls"}


def is_tabular(content_type: str | None) -> bool:
    """Whether ``content_type`` (an extension like ``csv``/``xlsx``) is tabular."""
    return (content_type or "").strip().lower().lstrip(".") in TABULAR_CONTENT_TYPES


def read_table(file_path: str, content_type: str, max_rows: int = None) -> pd.DataFrame:
    """Read a tabular file as all-string text.

    ``keep_default_na=False`` keeps blanks as ``""`` and ``dtype=str`` stops
    pandas from turning ids/dates into floats, so what is embedded is exactly
    what the spreadsheet contains.
    """
    kind = (content_type or "").strip().lower().lstrip(".")
    if kind in ("csv", "tsv"):
        # CSV is sniffed for its delimiter by the python engine; TSV is explicit
        # so the faster C engine can be used.  A UTF-8 BOM in exported CSVs is
        # absorbed by `utf-8-sig` so the first column name stays clean.
        return pd.read_csv(
            file_path,
            sep="\t" if kind == "tsv" else None,
            engine="c" if kind == "tsv" else "python",
            encoding="utf-8-sig",
            dtype=str,
            keep_default_na=False,
            nrows=max_rows,
        )
    if kind in ("xlsx", "xls"):
        return pd.read_excel(file_path, dtype=str, keep_default_na=False, nrows=max_rows)
    raise ValueError(f"Unsupported tabular type: {content_type}")


def _clean_columns(frame: pd.DataFrame) -> List[str]:
    """Column names as strings, de-duplicated, unnamed columns numbered."""
    columns: List[str] = []
    seen: Dict[str, int] = {}
    for index, raw in enumerate(frame.columns):
        name = str(raw).strip() or f"column_{index + 1}"
        if name in seen:
            seen[name] += 1
            name = f"{name}_{seen[name]}"
        else:
            seen[name] = 1
        columns.append(name)
    frame.columns = columns
    return columns


def inspect_table(file_path: str, content_type: str, sample_rows: int = 5) -> Dict[str, Any]:
    """Describe a tabular file for the upload UI: columns, preview and size."""
    frame = read_table(file_path, content_type)
    columns = _clean_columns(frame)
    rows = [
        {str(key): str(value) for key, value in record.items()}
        for record in frame.head(sample_rows).to_dict(orient="records")
    ]
    return {"columns": columns, "sample_rows": rows, "row_count": int(len(frame))}


def build_row_chunks(
    file_path: str,
    content_type: str,
    data_columns: List[str],
    metadata_columns: List[str],
) -> List[Dict[str, Any]]:
    """Turn every non-empty record into one ``{"content", "metadata"}`` chunk.

    ``data_columns`` order is preserved in the rendered content; unknown names
    are ignored.  ``metadata_columns`` are stored raw and never rendered into
    the embedded text.  A blank record (no data values) is skipped.
    """
    frame = read_table(file_path, content_type)
    columns = _clean_columns(frame)
    data_cols = [c for c in data_columns if c in columns]
    if not data_cols:
        raise ValueError("None of the selected data columns exist in this file.")
    metadata_cols = [c for c in metadata_columns if c in columns and c not in data_cols]

    chunks: List[Dict[str, Any]] = []
    for position, (_, record) in enumerate(frame.iterrows()):
        lines = [
            f"{column}: {str(record[column]).strip()}"
            for column in data_cols
            if str(record[column]).strip() != ""
        ]
        if not lines:
            continue
        metadata = {
            column: str(record[column]).strip()
            for column in metadata_cols
            if str(record[column]).strip() != ""
        }
        # Spreadsheet row number (header is row 1) so a hit can cite its record.
        metadata["row_number"] = position + 2
        chunks.append({"content": "\n".join(lines), "metadata": metadata})
    return chunks
