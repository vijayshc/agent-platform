"""Text-to-SQL FastMCP server (stdio). Exposes schema discovery and SQL execution tools."""

from __future__ import annotations

import logging
import os
import re
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any

logging.basicConfig(stream=sys.stderr, level=logging.INFO, force=True)
for _h in logging.root.handlers:
    _h.setStream(sys.stderr)

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("Text2SQL")

DEFAULT_DB_PATH = Path(os.environ.get("TEXT2SQL_DB_PATH") or "text2sql.db").resolve()
SCHEMA_FILE = Path(os.environ.get("SCHEMA_PATH") or "config/data/schema.json").resolve()
CONDITION_FILE = Path(os.environ.get("CONDITION_PATH") or "config/data/condition.json").resolve()


def _get_db_path() -> str:
    return str(DEFAULT_DB_PATH)


@mcp.tool()
def list_workspaces() -> str:
    """List all database workspaces and their available tables.

    Returns:
        Formatted summary of workspaces, descriptions, and contained tables.
    """
    from src.utils.schema_manager import SchemaManager

    mgr = SchemaManager(schema_file_path=str(SCHEMA_FILE) if SCHEMA_FILE.exists() else None)
    workspaces = mgr.get_workspaces()
    if not workspaces:
        return "No workspaces found."

    lines = ["# Available Workspaces\n"]
    for ws in workspaces:
        name = ws.get("name", "unknown")
        desc = ws.get("description") or "No description"
        tables = mgr.get_tables(workspace_name=name)
        tbl_names = [t.get("name", "") for t in tables]
        lines.append(f"- **{name}**: {desc} (Tables: {', '.join(tbl_names) if tbl_names else 'none'})")
    return "\n".join(lines)


@mcp.tool()
def list_tables(workspace_name: str = "") -> str:
    """List all tables and their descriptions in a given workspace.

    Args:
        workspace_name: Optional workspace name. If omitted, lists tables across available workspaces.

    Returns:
        Markdown-formatted list of tables and column counts.
    """
    from src.utils.schema_manager import SchemaManager

    mgr = SchemaManager(schema_file_path=str(SCHEMA_FILE) if SCHEMA_FILE.exists() else None)
    ws_name = workspace_name.strip() or "dfd"
    tables = mgr.get_tables(workspace_name=ws_name)
    if not tables:
        workspaces = mgr.get_workspaces()
        for ws in workspaces:
            tables = mgr.get_tables(workspace_name=ws.get("name", ""))
            if tables:
                ws_name = ws.get("name", "")
                break

    if not tables:
        return f"No tables found for workspace '{workspace_name}'."

    lines = [f"# Tables in Database (Workspace: {ws_name})\n"]
    for t in tables:
        name = t.get("name", "")
        desc = t.get("description") or "No description"
        col_count = len(t.get("columns", []))
        lines.append(f"- **{name}** ({col_count} columns): {desc}")
    return "\n".join(lines)


@mcp.tool()
def get_table_schema(table_names: str, workspace_name: str = "") -> str:
    """Get detailed column definitions, data types, descriptions, and primary keys for specified table(s).

    Args:
        table_names: Comma-separated list of table names, e.g. 'customers, orders' or 'products'.
        workspace_name: Optional workspace name.

    Returns:
        Detailed markdown table specifications for the requested tables.
    """
    from src.utils.schema_manager import SchemaManager

    mgr = SchemaManager(schema_file_path=str(SCHEMA_FILE) if SCHEMA_FILE.exists() else None)
    names = [n.strip() for n in table_names.split(",") if n.strip()]
    if not names:
        return "Please provide at least one table name."

    ws_name = workspace_name.strip() or "dfd"
    lines = []
    for name in names:
        table = mgr.get_table_by_name(table_name=name, workspace_name=ws_name)
        if not table:
            for ws in mgr.get_workspaces():
                table = mgr.get_table_by_name(table_name=name, workspace_name=ws.get("name", ""))
                if table:
                    break

        if not table:
            lines.append(f"### Table: `{name}` (Not Found)\n")
            continue

        desc = table.get("description") or "No description"
        lines.append(f"### Table: `{name}`\nDescription: {desc}\n")
        lines.append("| Column | Type | Primary Key | Description |")
        lines.append("| --- | --- | --- | --- |")
        for col in table.get("columns", []):
            c_name = col.get("name", "")
            c_type = col.get("data_type", "TEXT")
            c_pk = "Yes" if col.get("is_primary_key") else "No"
            c_desc = col.get("description") or ""
            lines.append(f"| `{c_name}` | `{c_type}` | {c_pk} | {c_desc} |")
        lines.append("")

    return "\n".join(lines)


@mcp.tool()
def get_join_conditions(table_names: str = "", workspace_name: str = "") -> str:
    """Retrieve predefined join conditions and foreign key relationships between tables.

    Args:
        table_names: Optional comma-separated table names to filter join conditions for, e.g. 'orders, customers'.
        workspace_name: Optional workspace name.

    Returns:
        Markdown list of join relationships, join types, and connecting columns.
    """
    from src.utils.schema_manager import SchemaManager

    mgr = SchemaManager(
        schema_file_path=str(SCHEMA_FILE) if SCHEMA_FILE.exists() else None,
        condition_file_path=str(CONDITION_FILE) if CONDITION_FILE.exists() else None,
    )
    ws_name = workspace_name.strip() or "dfd"
    filter_tables = [t.strip() for t in table_names.split(",") if t.strip()]
    conditions = mgr.get_join_conditions(tables=filter_tables, workspace_name=ws_name) if filter_tables else mgr.joins

    if not conditions:
        return "No specific predefined join conditions found for the requested tables."

    lines = ["# Join Conditions\n"]
    for c in conditions:
        t1 = c.get("table1", "")
        t2 = c.get("table2", "")
        ctype = c.get("type", "INNER JOIN")
        clause = c.get("condition") or f"{t1}.{c.get('column1', '')} = {t2}.{c.get('column2', '')}"
        lines.append(f"- **{t1}** ↔ **{t2}** (`{ctype}`): `{clause}`")
    return "\n".join(lines)


@mcp.tool()
def search_similar_queries(query: str, limit: int = 3) -> str:
    """Search historical user feedback and verified queries for few-shot guidance.

    Args:
        query: The user's natural language question or keywords.
        limit: Maximum number of similar examples to return (default 3).

    Returns:
        Markdown block containing verified query-to-SQL reference pairs.
    """
    from src.utils.feedback_manager import FeedbackManager

    try:
        mgr = FeedbackManager(connection_string=f"sqlite:///{_get_db_path()}")
        similar = mgr.find_similar_queries(query, limit=limit, positive_only=True)
    except Exception:
        similar = []

    if not similar:
        return "No similar verified queries found in feedback history."

    lines = ["# Similar Verified Queries\n"]
    for idx, item in enumerate(similar, 1):
        q = item.get("query_text", "")
        sql = item.get("sql_query", "")
        similarity = item.get("similarity", 0.0)
        lines.append(f"### Example {idx} (Similarity: {similarity:.2f})\n")
        lines.append(f"**Question:** {q}\n")
        lines.append(f"```sql\n{sql}\n```\n")
    return "\n".join(lines)


@mcp.tool()
def execute_sql_query(sql_query: str) -> str:
    """Safely execute a SQL query against the database and return results as a Markdown table.

    Only SELECT, WITH, PRAGMA, and EXPLAIN queries are allowed. Destructive mutations are rejected.

    Args:
        sql_query: The SQL query to execute.

    Returns:
        Markdown table containing the query results along with row count and execution duration.
    """
    clean_sql = sql_query.strip().rstrip(";")
    first_token = re.split(r"\s+", clean_sql)[0].upper() if clean_sql else ""
    allowed_verbs = {"SELECT", "WITH", "PRAGMA", "EXPLAIN"}
    if first_token not in allowed_verbs:
        return f"Error: Only read-only queries ({', '.join(allowed_verbs)}) are permitted. Received: {first_token}"

    db_path = _get_db_path()
    start_time = time.time()
    conn = None
    try:
        conn = sqlite3.connect(db_path, timeout=15)
        conn.row_factory = sqlite3.Row
        # Read-only tool: wait for writers instead of failing, and forbid
        # accidental writes on this connection.
        conn.execute("PRAGMA busy_timeout=30000")
        conn.execute("PRAGMA query_only=ON")
        cursor = conn.cursor()
        cursor.execute(clean_sql)
        rows = cursor.fetchall()
        duration = time.time() - start_time
    except Exception as exc:
        return f"Database Error ({type(exc).__name__}): {exc}"
    finally:
        if conn is not None:
            conn.close()

    if not rows:
        return f"Query executed successfully in {duration:.3f}s. Result: 0 rows returned."

    headers = list(rows[0].keys())
    lines = [f"**Query Results** ({len(rows)} rows in {duration:.3f}s):\n"]
    lines.append("| " + " | ".join(headers) + " |")
    lines.append("| " + " | ".join(["---"] * len(headers)) + " |")
    for r in rows:
        row_vals = [str(r[h]) if r[h] is not None else "NULL" for h in headers]
        lines.append("| " + " | ".join(row_vals) + " |")

    return "\n".join(lines)


def main() -> None:
    logging.basicConfig(level=logging.ERROR, stream=sys.stderr)
    logging.getLogger().handlers = [logging.StreamHandler(sys.stderr)]
    mcp.run()


if __name__ == "__main__":
    main()
