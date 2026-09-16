"""Phoenix correlation ids for a run.

Kept separate from :mod:`run_store` so the store stays focused on the run's own
lifecycle.  ``agent_runs`` carries four correlation columns
(``phoenix_project``/``session_id``/``trace_id``/``root_span_id``) that together
let the Observability UI re-fetch a run's interactions from Phoenix long after
the in-memory replay buffer is gone.
"""

from __future__ import annotations

from typing import Any

from src.agent_platform import db


def set_run_trace(
    run_id: int,
    *,
    session_id: str | None = None,
    trace_id: str | None = None,
    root_span_id: str | None = None,
    project: str | None = None,
) -> None:
    """Write the non-``None`` correlation fields for ``run_id``.

    Called twice per run: at start with the deterministic ``session_id`` and
    ``project``, then when the tracing instrumentation hands over the real
    ``trace_id``/``root_span_id``.  Writing only non-``None`` fields is what lets
    the two calls happen without clobbering each other.
    """
    fields: list[str] = []
    args: list[Any] = []
    for column, value in (
        ("session_id", session_id),
        ("trace_id", trace_id),
        ("root_span_id", root_span_id),
        ("phoenix_project", project),
    ):
        if value is not None:
            fields.append(f"{column} = ?")
            args.append(value)
    if not fields:
        return
    args.append(run_id)
    conn = db.get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute(f"UPDATE agent_runs SET {', '.join(fields)} WHERE id = ?", args)
        conn.commit()
    finally:
        conn.close()
