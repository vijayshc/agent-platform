"""Schema for the few-shot query-feedback store.

``FeedbackManager`` reads and writes ``query_feedback`` with raw SQL (there is
no SQLAlchemy model), so its DDL lives here -- one small owner that
``scripts/setup_platform.py`` calls, keeping the 800-line manager free of schema
concerns.
"""

from __future__ import annotations

from sqlalchemy import text

from config.config import DATABASE_URI
from src.utils.database import create_db_engine

FEEDBACK_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS query_feedback (
    feedback_id INTEGER PRIMARY KEY,
    query_text TEXT NOT NULL,
    sql_query TEXT NOT NULL,
    results_summary TEXT,
    workspace TEXT,
    feedback_rating INTEGER NOT NULL,    -- 1 for thumbs up, 0 for thumbs down
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    embedding BLOB,                      -- vector embedding for similarity search
    tables_used TEXT,                    -- comma-separated tables the query touched
    is_manual_sample BOOLEAN DEFAULT 0   -- manually curated few-shot example
)
"""


def ensure_feedback_table(connection_string: str | None = None) -> None:
    """Create the ``query_feedback`` table when it is missing (idempotent)."""
    engine = create_db_engine(connection_string or DATABASE_URI)
    with engine.begin() as conn:
        conn.execute(text(FEEDBACK_TABLE_DDL))
