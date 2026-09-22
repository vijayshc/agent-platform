"""Text-to-SQL FastMCP server (stdio). Exposes schema discovery and SQL execution tools."""

from __future__ import annotations

import logging
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

logging.basicConfig(stream=sys.stderr, level=logging.INFO, force=True)
for _h in logging.root.handlers:
    _h.setStream(sys.stderr)

from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult, TextContent
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.exc import SQLAlchemyError

from src.utils.tool_data_contract import (
    ERROR_KIND,
    ToolDataContractError,
    apply_declared_types,
    build_table,
    render_markdown,
)

mcp = FastMCP("Text2SQL")

logger = logging.getLogger("text2sql.mcp")

DEFAULT_DB_PATH = Path(os.environ.get("TEXT2SQL_DB_PATH") or "text2sql.db").resolve()
SCHEMA_FILE = Path(os.environ.get("SCHEMA_PATH") or "config/data/schema.json").resolve()
CONDITION_FILE = Path(os.environ.get("CONDITION_PATH") or "config/data/condition.json").resolve()

#: One SQLAlchemy URL for every database this server can reach. SQLAlchemy picks
#: the dialect, the driver and the value types, so nothing here is written for a
#: particular database::
#:
#:     DATABASE_URL=postgresql+psycopg://user:pass@host/db
#:     DATABASE_URL=mysql+pymysql://user:pass@host/db
#:     DATABASE_URL=mssql+pyodbc://...
#:     DATABASE_URL=sqlite:////abs/path/to/text2sql.db   (the default)
#:
#: Point it at a **read-only account** in production: the verb guard and the
#: read-only transaction below stop the obvious mutations, but only the database
#: can enforce read-only against a stored function.
_ENGINE: Engine | None = None

#: Statements that put a transaction in read-only mode, per dialect. SQLAlchemy
#: does not abstract read-only transactions, so this capability map is the one
#: dialect-aware thing in this server — a security guard, not type handling. A
#: dialect with no entry relies on the verb guard and a read-only database role.
_READ_ONLY_STATEMENTS = {
    "postgresql": "SET TRANSACTION READ ONLY",
    "mysql": "SET TRANSACTION READ ONLY",
    "mariadb": "SET TRANSACTION READ ONLY",
    "oracle": "SET TRANSACTION READ ONLY",
    "sqlite": "PRAGMA query_only = ON",
}


def _database_url() -> str:
    return os.environ.get("DATABASE_URL") or f"sqlite:///{DEFAULT_DB_PATH}"


def _engine() -> Engine:
    """The process-wide engine, created once from ``DATABASE_URL``."""
    global _ENGINE
    if _ENGINE is None:
        _ENGINE = create_engine(_database_url(), pool_pre_ping=True, future=True)
    return _ENGINE


def _begin_read_only(conn: Any) -> None:
    """Put this connection's transaction in read-only mode where it can be."""
    statement = _READ_ONLY_STATEMENTS.get(conn.dialect.name)
    if statement is None:
        logger.warning(
            "no read-only transaction statement for dialect %r; relying on the verb "
            "guard and a read-only database role",
            conn.dialect.name,
        )
        return
    conn.exec_driver_sql(statement)


def _get_db_path() -> str:
    """The SQLite file the app-local feedback store lives in.

    The query tool itself goes through the engine and knows nothing about the
    database behind ``DATABASE_URL``; this path exists only because the feedback
    store is a separate, app-owned SQLite database.
    """
    return str(DEFAULT_DB_PATH)


_DECLARED_TYPES: dict[str, str] | None = None


def _contract_type(sa_type: Any) -> str | None:
    """The contract type a SQLAlchemy column type declares, or ``None``."""
    from sqlalchemy import types as sat

    # Order matters: Float subclasses Numeric, and DateTime is checked before Date.
    if isinstance(sa_type, sat.Boolean):
        return "boolean"
    if isinstance(sa_type, sat.Integer):
        return "integer"
    if isinstance(sa_type, sat.Float):
        return "number"
    if isinstance(sa_type, sat.Numeric):
        return "decimal"
    if isinstance(sa_type, sat.DateTime):
        return "datetime"
    if isinstance(sa_type, sat.Date):
        return "date"
    if isinstance(sa_type, sat.Time):
        return "time"
    if isinstance(sa_type, (sat.String, sat.Text, sat.Unicode, sat.Enum, sat.Uuid)):
        return "string"
    return None


def _declared_types() -> dict[str, str]:
    """Column name -> contract type, for names that are unambiguous in the schema.

    Reflected once per process through SQLAlchemy's inspector, so it is generic
    across dialects. A name that means different types in different tables is
    dropped: guessing which table a result column came from is exactly the kind
    of inference this contract avoids.
    """
    global _DECLARED_TYPES
    if _DECLARED_TYPES is not None:
        return _DECLARED_TYPES
    from sqlalchemy import inspect as sa_inspect

    declared: dict[str, str] = {}
    ambiguous: set[str] = set()
    try:
        inspector = sa_inspect(_engine())
        for table_name in inspector.get_table_names():
            for column in inspector.get_columns(table_name):
                ctype = _contract_type(column["type"])
                if ctype is None:
                    continue
                name = str(column["name"])
                if name in declared and declared[name] != ctype:
                    ambiguous.add(name)
                else:
                    declared[name] = ctype
    except Exception:
        # Not cached: a transient reflection failure must not permanently cost
        # every later query its declared types.
        logger.warning("could not reflect the schema; untyped columns stay unknown", exc_info=True)
        return {}
    for name in ambiguous:
        declared.pop(name, None)
    _DECLARED_TYPES = declared
    return declared


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
def execute_sql_query(sql_query: str) -> CallToolResult:
    """Safely execute a SQL query against the database and return the result set.

    Runs through SQLAlchemy, so the same tool serves any database its URL names.
    Only SELECT, WITH and EXPLAIN are accepted; the statement must be a single
    read-only statement.

    Args:
        sql_query: The SQL query to execute.

    Returns:
        The result set as a typed table (``structuredContent``) plus a readable
        markdown rendering. Each column's declared type is the type of the values
        the database driver returned — a date column of a database that has dates
        comes back as a date, a numeric column as a number, and a missing value
        stays null instead of becoming the text "NULL".
    """
    clean_sql = sql_query.strip().rstrip(";")
    if ";" in clean_sql:
        return _error("Error: Only one statement may be executed at a time.")
    first_token = re.split(r"\s+", clean_sql)[0].upper() if clean_sql else ""
    allowed_verbs = {"SELECT", "WITH", "EXPLAIN"}
    if first_token not in allowed_verbs:
        return _error(
            f"Error: Only read-only queries ({', '.join(sorted(allowed_verbs))}) are permitted. "
            f"Received: {first_token}"
        )

    start_time = time.time()
    try:
        with _engine().connect() as conn:
            _begin_read_only(conn)
            result = conn.execute(text(clean_sql))
            headers = list(result.keys())
            rows = [list(row) for row in result.fetchall()]
            duration = time.time() - start_time
    except SQLAlchemyError as exc:
        return _error(f"Database Error ({type(exc).__name__}): {exc}")

    try:
        table = build_table(headers, rows)
        # A column the values cannot type (empty result, all null) takes the type
        # the schema declares for its name; a column whose values are consistent
        # with the declared type takes it too (a SQLite boolean is 0/1).
        table = apply_declared_types(table, _declared_types())
    except ToolDataContractError as exc:
        return _error(f"Query Result Error: {exc}")
    summary = f"**Query Results** ({table.total_rows} rows in {duration:.3f}s):"
    body = f"{summary}\n\n{render_markdown(table.columns, table.rows)}"
    return CallToolResult(
        content=[TextContent(type="text", text=body)],
        structuredContent=table.to_contract(),
    )


def _error(message: str) -> CallToolResult:
    """A failed query: readable text plus an explicit error envelope.

    The envelope tells the runtime this is the tool's own failure rather than a
    missing typed table, so the message is passed through instead of being
    reported as a producer bug.
    """
    return CallToolResult(
        content=[TextContent(type="text", text=message)],
        structuredContent={"kind": ERROR_KIND, "version": 1, "message": message},
    )


def main() -> None:
    logging.basicConfig(level=logging.ERROR, stream=sys.stderr)
    logging.getLogger().handlers = [logging.StreamHandler(sys.stderr)]
    mcp.run()


if __name__ == "__main__":
    main()
