"""Read-only run queries that share the run store's tenancy rule.

Kept out of :mod:`run_store` so the store stays within its size budget and
focused on lifecycle writes.  The count uses the *same* viewer clause as
``RunStore.list_runs``, so "N of M" in the UI is scoped exactly like the rows.
"""

from __future__ import annotations

from typing import Any

from src.agent_platform import db


def count_visible_runs(viewer_id: int | None, definition_ids: list[int] | None) -> int:
    """Total runs the viewer may list (``definition_ids=None`` means admin)."""
    args: list[Any] = []
    where = ""
    if viewer_id is not None and definition_ids is not None:
        clause = "(r.user_id = ?"
        args.append(int(viewer_id))
        if definition_ids:
            placeholders = ",".join("?" for _ in definition_ids)
            clause += f" OR r.definition_id IN ({placeholders})"
            args.extend(int(d) for d in definition_ids)
        where = " WHERE " + clause + ")"
    conn = db.get_db_connection()
    try:
        row = conn.execute(f"SELECT COUNT(*) FROM agent_runs r{where}", args).fetchone()
        return int(row[0]) if row else 0
    finally:
        conn.close()
