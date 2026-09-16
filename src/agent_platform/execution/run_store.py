"""Run persistence shared by the HTTP API and Agent Runs UI."""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from typing import Any

from src.agent_platform import db


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(sep=" ", timespec="seconds")


def _to_int(value: Any) -> int | None:
    """Coerce an id column / payload value to ``int``, rejecting booleans."""
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


class RunStore:
    # One-time DDL guard. Creating tables / ALTER / CREATE INDEX on every store
    # call was a large, lock-heavy cost in the hot path (every run status write,
    # every span event, every message). After the first successful run per
    # process we short-circuit so concurrent runs do not re-run DDL against a
    # shared SQLite file.
    _tables_ready = False
    _ddl_lock = threading.Lock()

    @staticmethod
    def _reset_schema_guard() -> None:
        """Force DDL to run again.

        Test isolation swaps ``get_db_connection`` to a fresh file; the cached
        ``_tables_ready`` must be cleared so the new file gets its schema.
        """
        with RunStore._ddl_lock:
            RunStore._tables_ready = False

    @staticmethod
    def ensure_tables() -> None:
        if RunStore._tables_ready:
            return
        with RunStore._ddl_lock:
            if RunStore._tables_ready:
                return
            conn = db.get_db_connection()
            try:
                cur = conn.cursor()
                cur.execute(
                    """
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
                )
                for col, decl in (
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
                    # Persisted at run start / root-span creation so the run's
                    # interactions can be re-fetched from Phoenix on demand long
                    # after the in-memory replay buffer is gone.
                    ("phoenix_project", "TEXT"),
                    ("session_id", "TEXT"),
                    ("trace_id", "TEXT"),
                    ("root_span_id", "TEXT"),
                ):
                    try:
                        cur.execute(f"ALTER TABLE agent_runs ADD COLUMN {col} {decl}")
                    except Exception:
                        pass
                try:
                    cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_agent_runs_public_id ON agent_runs(public_id)")
                except Exception:
                    pass
                conn.commit()
                RunStore._tables_ready = True
            finally:
                conn.close()

    @classmethod
    def create(
        cls,
        *,
        task: str,
        definition_id: int | None = None,
        conversation_id: int | None = None,
        user_id: int | None = None,
        agent_slug: str | None = None,
        entity_type: str = "agent",
        entity_id: int = 0,
        workspace_dir: str | None = None,
        input_payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        cls.ensure_tables()
        public_id = str(uuid.uuid4())
        conn = db.get_db_connection()
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO agent_runs (
                public_id, entity_type, entity_id, definition_id, conversation_id, user_id,
                agent_slug, task, status, started_at, input_json, workspace_dir
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'running', ?, ?, ?)
            """,
            (
                public_id,
                entity_type,
                entity_id or definition_id or 0,
                definition_id,
                conversation_id,
                user_id,
                agent_slug,
                task or "",
                _now(),
                json.dumps(input_payload or {}),
                workspace_dir,
            ),
        )
        run_id = cur.lastrowid
        conn.commit()
        conn.close()

        if conversation_id is None and run_id:
            try:
                import shutil
                from src.agent_platform.paths import checkpoint_dir_for

                cp_dir = checkpoint_dir_for(f"run-{public_id}")
                if cp_dir.exists():
                    shutil.rmtree(cp_dir, ignore_errors=True)
            except Exception:
                pass

        return cls.get(run_id)  # type: ignore[return-value]

    @classmethod
    def get(cls, run_id: int | str) -> dict[str, Any] | None:
        cls.ensure_tables()
        conn = db.get_db_connection()
        cur = conn.cursor()
        if isinstance(run_id, str) and not run_id.isdigit():
            cur.execute("SELECT * FROM agent_runs WHERE public_id = ?", (run_id,))
        else:
            cur.execute("SELECT * FROM agent_runs WHERE id = ?", (int(run_id),))
        row = cur.fetchone()
        if not row:
            conn.close()
            return None
        data = dict(row)
        data = cls._attach_usernames(conn, [data])[0]
        conn.close()
        for key in ("input_json", "pending_json", "session_json"):
            if data.get(key):
                try:
                    data[key] = json.loads(data[key])
                except Exception:
                    pass
        return data

    @staticmethod
    def can_view(run: dict[str, Any] | None, user_id: int | None) -> bool:
        """The canonical run-visibility rule, shared by every run endpoint.

        A caller may see a run when they own it (``user_id``), or when the run's
        agent definition is accessible to them through
        ``resource_access.can_access("agent", ...)``: administrator, definition
        owner, unowned (grandfathered) definition, or a role granted on the
        definition.  Runs with no definition (``definition_id`` NULL, or a
        definition that no longer exists) are owner-only (or admin).  Unknown
        callers are denied: this fails closed.
        """
        if not run:
            return False
        uid = _to_int(user_id)
        if uid is None:
            return False
        from src.auth import resource_access

        if resource_access.is_admin(uid):
            return True
        owner = _to_int(run.get("user_id"))
        if owner is not None and owner == uid:
            return True
        definition_id = _to_int(run.get("definition_id"))
        if definition_id is None:
            return False
        if not resource_access.resource_exists("agent", definition_id):
            return False
        return resource_access.can_access(
            "agent",
            definition_id,
            resource_access.owner_of("agent", definition_id),
            uid,
        )

    @staticmethod
    def visible_definition_ids(user_id: int | None) -> list[int] | None:
        """Agent-definition ids whose runs ``user_id`` may list.

        ``None`` means "no restriction" (administrator).  For everyone else the
        set is the caller's own definitions, unowned (grandfathered) definitions,
        and definitions granted to one of the caller's roles -- resolved with a
        bounded number of queries (never one per run) so the run list can push
        visibility into SQL and still honour ``limit``.
        """
        from src.auth import resource_access

        uid = _to_int(user_id)
        if uid is None:
            return []
        if resource_access.is_admin(uid):
            return None
        resource_access.ensure_tables()
        ids: set[int] = set()
        conn = db.get_db_connection()
        try:
            try:
                rows = conn.execute(
                    "SELECT id FROM agent_definitions WHERE created_by IS NULL OR created_by = ?",
                    (uid,),
                ).fetchall()
                ids.update(int(r["id"]) for r in rows if r["id"] is not None)
            except sqlite3.Error:
                pass
            role_ids = sorted(resource_access.user_role_ids(uid))
            if role_ids:
                placeholders = ",".join("?" for _ in role_ids)
                try:
                    rows = conn.execute(
                        "SELECT resource_id FROM resource_role_access "
                        f"WHERE resource_type = 'agent' AND role_id IN ({placeholders})",
                        role_ids,
                    ).fetchall()
                    ids.update(int(r["resource_id"]) for r in rows if r["resource_id"] is not None)
                except sqlite3.Error:
                    pass
        finally:
            conn.close()
        return sorted(ids)

    @staticmethod
    def _viewer_clause(viewer_id: int, definition_ids: list[int]) -> tuple[str, list[Any]]:
        """SQL restricting runs to a non-admin viewer's own or accessible runs."""
        clause = "(r.user_id = ?"
        args: list[Any] = [int(viewer_id)]
        if definition_ids:
            placeholders = ",".join("?" for _ in definition_ids)
            clause += f" OR r.definition_id IN ({placeholders})"
            args.extend(int(d) for d in definition_ids)
        return clause + ")", args

    @staticmethod
    def _run_filters(
        *,
        user_id: int | None,
        agent: str | None,
        error_only: bool,
        hitl_only: bool,
        since: str | None,
        until: str | None,
        q: str | None = None,
    ) -> tuple[list[str], list[Any]]:
        """Build the shared WHERE clause for listing/counting runs.

        Agent trace events are no longer persisted to SQLite (they live in
        Arize Phoenix), so filtering only uses columns on ``agent_runs``.
        """
        where: list[str] = []
        args: list[Any] = []
        if user_id is not None:
            where.append("r.user_id = ?")
            args.append(user_id)
        if agent:
            where.append("r.agent_slug = ?")
            args.append(agent)
        if q:
            needle = f"%{q}%"
            where.append("(r.agent_slug LIKE ? OR r.task LIKE ? OR r.status LIKE ? OR r.final_reply LIKE ?)")
            args.extend([needle, needle, needle, needle])
        if error_only:
            where.append("(r.status = 'error' OR (r.error IS NOT NULL AND TRIM(r.error) != ''))")
        if hitl_only:
            where.append("r.status = 'awaiting_approval'")
        if since:
            where.append("r.started_at >= ?")
            args.append(since)
        if until:
            where.append("r.started_at <= ?")
            args.append(until)
        return where, args

    @classmethod
    def _attach_usernames(cls, conn, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Populate each run row's ``username`` from the users table (best-effort).

        Runs created by API keys or system work may reference a user that no
        longer exists; those keep ``username=None`` so the UI can render a
        generic fallback. Resolution is skipped entirely when the ``users``
        table is not present (e.g. standalone agent-platform tests).
        """
        user_ids = {r.get("user_id") for r in rows if r.get("user_id") is not None}
        if not user_ids:
            return rows
        try:
            has_users = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'users'"
            ).fetchone()
            if not has_users:
                return rows
            placeholders = ",".join("?" for _ in user_ids)
            user_rows = conn.execute(
                f"SELECT id, username FROM users WHERE id IN ({placeholders})",
                list(user_ids),
            ).fetchall()
        except sqlite3.Error:
            return rows
        name_by_id = {r["id"]: r["username"] for r in user_rows}
        for row in rows:
            uid = row.get("user_id")
            if uid is not None:
                row["username"] = name_by_id.get(uid)
        return rows

    @classmethod
    def count_runs(
        cls,
        *,
        user_id: int | None = None,
        agent: str | None = None,
        error_only: bool = False,
        hitl_only: bool = False,
        since: str | None = None,
        until: str | None = None,
        q: str | None = None,
    ) -> int:
        """Return the number of runs matching the same filters as ``list_runs``."""
        cls.ensure_tables()
        where, args = cls._run_filters(
            user_id=user_id,
            agent=agent,
            error_only=error_only,
            hitl_only=hitl_only,
            since=since,
            until=until,
            q=q,
        )
        conn = db.get_db_connection()
        cur = conn.cursor()
        sql = "SELECT COUNT(*) FROM agent_runs r"
        if where:
            sql += " WHERE " + " AND ".join(where)
        cur.execute(sql, args)
        total = cur.fetchone()[0]
        conn.close()
        return int(total)

    @classmethod
    def list_runs(
        cls,
        limit: int = 50,
        offset: int = 0,
        user_id: int | None = None,
        agent: str | None = None,
        error_only: bool = False,
        hitl_only: bool = False,
        since: str | None = None,
        until: str | None = None,
        q: str | None = None,
        *,
        viewer_id: int | None = None,
        definition_ids: list[int] | None = None,
    ) -> list[dict[str, Any]]:
        cls.ensure_tables()
        try:
            limit = int(limit)
        except (TypeError, ValueError):
            limit = 50
        limit = max(1, min(limit, 500))
        try:
            offset = int(offset)
        except (TypeError, ValueError):
            offset = 0
        offset = max(0, offset)
        where, args = cls._run_filters(
            user_id=user_id,
            agent=agent,
            error_only=error_only,
            hitl_only=hitl_only,
            since=since,
            until=until,
            q=q,
        )
        # Tenant scoping for the observability surface: a non-admin viewer is
        # restricted in SQL to runs they own or whose definition they can access,
        # so ``limit`` counts visible runs rather than pre-filter candidates.
        # ``definition_ids is None`` is the administrator case (no restriction).
        if viewer_id is not None and definition_ids is not None:
            clause, viewer_args = cls._viewer_clause(viewer_id, definition_ids)
            where.append(clause)
            args.extend(viewer_args)
        conn = db.get_db_connection()
        cur = conn.cursor()
        sql = "SELECT r.* FROM agent_runs r"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY r.id DESC LIMIT ? OFFSET ?"
        args.extend([limit, offset])
        cur.execute(sql, args)
        rows = [dict(r) for r in cur.fetchall()]
        rows = cls._attach_usernames(conn, rows)
        conn.close()
        return rows

    @classmethod
    def list_for_conversation(cls, conversation_id: int) -> list[dict[str, Any]]:
        cls.ensure_tables()
        conn = db.get_db_connection()
        cur = conn.cursor()
        cur.execute(
            "SELECT * FROM agent_runs WHERE conversation_id = ? ORDER BY id DESC",
            (conversation_id,),
        )
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()
        return rows

    @classmethod
    def finish(
        cls,
        run_id: int,
        status: str,
        final_reply: str | None = None,
        error: str | None = None,
    ) -> None:
        current = cls.get(run_id)
        if current and current.get("status") == "cancelled" and status != "cancelled":
            return
        conn = db.get_db_connection()
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE agent_runs
            SET status = ?, finished_at = ?, final_reply = ?, error = ?, pending_json = NULL
            WHERE id = ?
            """,
            (status, _now(), final_reply, error, run_id),
        )
        conn.commit()
        conn.close()

    @classmethod
    def reconcile_orphans(cls) -> int:
        """Fail runs that no process can still be executing.

        Runs execute in the web process, so anything left in a non-terminal state
        at start-up was orphaned by a restart or a crash. Without this they stay
        "running" forever -- invisible work that never finishes and cannot be
        retried. Called once during platform initialisation.
        """
        cls.ensure_tables()
        conn = db.get_db_connection()
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE agent_runs
            SET status = 'error',
                finished_at = ?,
                error = 'Interrupted: the application restarted before this run finished.',
                pending_json = NULL
            WHERE status IN ('running', 'pending')
            """,
            (_now(),),
        )
        changed = cur.rowcount or 0
        conn.commit()
        conn.close()
        return int(changed)

    @classmethod
    def set_pending(cls, run_id: int, pending: dict[str, Any] | None) -> None:
        conn = db.get_db_connection()
        cur = conn.cursor()
        chk_id = f"chk-{run_id}" if pending else None
        cur.execute(
            "UPDATE agent_runs SET pending_json = ?, status = ?, checkpoint_id = ? WHERE id = ?",
            (json.dumps(pending) if pending else None, "awaiting_approval" if pending else "running", chk_id, run_id),
        )
        conn.commit()
        conn.close()

    @classmethod
    def set_status(cls, run_id: int, status: str) -> None:
        current = cls.get(run_id)
        if current and current.get("status") == "cancelled" and status not in {"cancelled"}:
            return
        conn = db.get_db_connection()
        cur = conn.cursor()
        cur.execute("UPDATE agent_runs SET status = ? WHERE id = ?", (status, run_id))
        conn.commit()
        conn.close()

    @classmethod
    def update_workspace(cls, run_id: int, workspace_dir: str) -> None:
        conn = db.get_db_connection()
        cur = conn.cursor()
        cur.execute("UPDATE agent_runs SET workspace_dir = ? WHERE id = ?", (workspace_dir, run_id))
        conn.commit()
        conn.close()

    @classmethod
    def save_session(cls, run_id: int, session_dict: dict[str, Any] | None) -> None:
        conn = db.get_db_connection()
        cur = conn.cursor()
        cur.execute(
            "UPDATE agent_runs SET session_json = ? WHERE id = ?",
            (json.dumps(session_dict) if session_dict else None, run_id),
        )
        conn.commit()
        conn.close()

    @classmethod
    def set_checkpoint(cls, run_id: int, checkpoint_id: str | None) -> None:
        conn = db.get_db_connection()
        cur = conn.cursor()
        cur.execute("UPDATE agent_runs SET checkpoint_id = ? WHERE id = ?", (checkpoint_id, run_id))
        conn.commit()
        conn.close()

    @classmethod
    def set_trace(
        cls,
        run_id: int,
        *,
        session_id: str | None = None,
        trace_id: str | None = None,
        root_span_id: str | None = None,
        project: str | None = None,
    ) -> None:
        """Record the Phoenix correlation ids for a run (see ``run_trace``)."""
        from src.agent_platform.execution.run_trace import set_run_trace

        set_run_trace(
            run_id,
            session_id=session_id,
            trace_id=trace_id,
            root_span_id=root_span_id,
            project=project,
        )
