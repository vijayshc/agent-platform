"""Database schema DDL and migrations for agent runs.

Decoupled from run_store.py so table creation and column migrations do not clutter
the runtime persistence logic.
"""

from __future__ import annotations

import threading
from src.agent_platform import db

# One-time DDL guard. After the first successful run per process we short-circuit
# so concurrent runs do not re-run DDL against a shared SQLite file.
_tables_ready = False
_ddl_lock = threading.Lock()

RUN_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS agent_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    public_id TEXT UNIQUE,
    entity_type TEXT,
    entity_id INTEGER,
    definition_id INTEGER,
    conversation_id INTEGER,
    user_id INTEGER,
    agent_slug TEXT,
    task TEXT,
    status TEXT DEFAULT 'running',
    started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    finished_at TIMESTAMP,
    final_reply TEXT,
    error TEXT,
    input_json TEXT,
    pending_json TEXT,
    workspace_dir TEXT,
    session_json TEXT,
    checkpoint_id TEXT,
    phoenix_project TEXT,
    session_id TEXT,
    trace_id TEXT,
    root_span_id TEXT
)
"""

MIGRATION_COLUMNS: list[tuple[str, str]] = [
    ("public_id", "TEXT"),
    ("definition_id", "INTEGER"),
    ("conversation_id", "INTEGER"),
    ("user_id", "INTEGER"),
    ("agent_slug", "TEXT"),
    ("input_json", "TEXT"),
    ("pending_json", "TEXT"),
    ("workspace_dir", "TEXT"),
    ("session_json", "TEXT"),
    ("checkpoint_id", "TEXT"),
    # Phoenix correlation: which trace/session/root span this run
    # produced, and the Phoenix project (agent name) it landed in.
    ("phoenix_project", "TEXT"),
    ("session_id", "TEXT"),
    ("trace_id", "TEXT"),
    ("root_span_id", "TEXT"),
]


def reset_schema_guard() -> None:
    """Force DDL to run again.

    Test isolation swaps ``get_db_connection`` to a fresh file; the cached
    ``_tables_ready`` must be cleared so the new file gets its schema.
    """
    global _tables_ready
    with _ddl_lock:
        _tables_ready = False


def ensure_run_tables() -> None:
    """Initialize agent_runs table and apply any missing column migrations."""
    global _tables_ready
    if _tables_ready:
        return
    with _ddl_lock:
        if _tables_ready:
            return
        conn = db.get_db_connection()
        try:
            cur = conn.cursor()
            cur.execute(RUN_TABLE_DDL)
            for col, decl in MIGRATION_COLUMNS:
                try:
                    cur.execute(f"ALTER TABLE agent_runs ADD COLUMN {col} {decl}")
                except Exception:
                    pass
            try:
                cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_agent_runs_public_id ON agent_runs(public_id)")
            except Exception:
                pass
            conn.commit()
            _tables_ready = True
        finally:
            conn.close()
