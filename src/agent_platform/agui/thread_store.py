"""DB-backed mapping of AG-UI thread IDs to platform conversations.

AG-UI clients generate arbitrary ``thread_id`` values; the platform identifies
conversations by ``agent_conversations.public_id``. This store keeps the
``thread_id -> conversation_id`` mapping in its own table so multi-turn AG-UI
threads and HITL resumes resolve to the same conversation across requests and
restarts (process-local state would lose the mapping).
"""

from __future__ import annotations

from typing import Any

from src.agent_platform import db


def ensure_tables() -> None:
    conn = db.get_db_connection()
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS agui_threads (
            thread_id TEXT PRIMARY KEY,
            conversation_id INTEGER NOT NULL,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.commit()
    conn.close()


def lookup(thread_id: str) -> int | None:
    """Return the conversation pk for an AG-UI thread, or None."""
    if not thread_id:
        return None
    ensure_tables()
    conn = db.get_db_connection()
    try:
        row = conn.execute(
            "SELECT conversation_id FROM agui_threads WHERE thread_id = ?", (thread_id,)
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return None
    return int(row[0] if not isinstance(row, dict) else row["conversation_id"])


def bind(thread_id: str, conversation_id: int) -> None:
    """Record (or refresh) the thread -> conversation mapping."""
    if not thread_id or not conversation_id:
        return
    ensure_tables()
    conn = db.get_db_connection()
    try:
        conn.execute(
            """
            INSERT INTO agui_threads (thread_id, conversation_id, updated_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(thread_id) DO UPDATE SET
                conversation_id = excluded.conversation_id,
                updated_at = CURRENT_TIMESTAMP
            """,
            (thread_id, conversation_id),
        )
        conn.commit()
    finally:
        conn.close()


def conversation_for_thread(thread_id: str) -> dict[str, Any] | None:
    """Resolve the conversation row bound to a thread (delegates to the store)."""
    from src.agent_platform.conversations.store import ConversationStore

    conv_pk = lookup(thread_id)
    if conv_pk is None:
        return None
    conv = ConversationStore.get(conv_pk)
    if conv is not None:
        return conv
    # Conversation deleted: drop the stale mapping.
    ensure_tables()
    conn = db.get_db_connection()
    try:
        conn.execute("DELETE FROM agui_threads WHERE thread_id = ?", (thread_id,))
        conn.commit()
    finally:
        conn.close()
    return None
