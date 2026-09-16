"""
Dashboard analytics aggregation for the admin dashboard.

Pulls metrics from across the app (users, roles, agents, runs, messages,
conversations, skills, mcp servers, hosted apps) and shapes them into a single
JSON payload the React dashboard renders. All queries go through
``get_db_connection`` (raw sqlite3) for speed and resilience; tables that
do not exist are gracefully treated as empty.

User activity and login auditing lives in the audit trail files
(``src/auth/audit.py``), not in the database, so it is deliberately not
part of this payload.
"""

from __future__ import annotations

import datetime
import logging
from typing import Any, Callable

from src.utils.database import get_db_connection

logger = logging.getLogger("text2sql.dashboard")


def _today() -> datetime.date:
    return datetime.date.today()


def _dt_start(days_ago: int = 0) -> datetime.datetime:
    day = _today() - datetime.timedelta(days=days_ago)
    return datetime.datetime.combine(day, datetime.time.min)


def _iso(v: Any) -> str | None:
    """Best-effort ISO string for a sqlite/str/datetime value."""
    if v is None:
        return None
    if isinstance(v, datetime.datetime):
        return v.isoformat(sep=" ", timespec="seconds")
    return str(v)


def _run_sql(conn, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
    """Run a query and return a list of dicts; swallow errors for missing tables."""
    try:
        cur = conn.cursor()
        cur.execute(sql, params)
        cols = [c[0] for c in cur.description] if cur.description else []
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception as exc:  # noqa: BLE001
        logger.debug("dashboard query skipped: %s", exc)
        return []


def _scalar(conn, sql: str, params: tuple = (), default: int = 0) -> int:
    rows = _run_sql(conn, sql, params)
    if not rows:
        return default
    first = next(iter(rows[0].values()), None)
    try:
        return int(first or default)
    except (TypeError, ValueError):
        return default


def _has_table(conn, name: str) -> bool:
    return bool(
        _run_sql(
            conn,
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (name,),
        )
    )


def _series(
    conn,
    expr: str,
    table: str,
    date_col: str,
    *,
    since: str,
    where: str = "",
    params: tuple = (),
) -> list[dict[str, Any]]:
    """Return ``[{date, count}]`` for each day from ``since`` to today."""
    start = _dt_start(0)
    if since:
        try:
            start = datetime.datetime.strptime(since, "%Y-%m-%d").date()
        except ValueError:
            start = start
    rows = _run_sql(
        conn,
        f"SELECT date({date_col}) AS day, COUNT({expr}) AS count "
        f"FROM {table} "
        f"WHERE date({date_col}) >= date(?) {where} "
        f"GROUP BY date({date_col}) ORDER BY day ASC",
        (str(start),) + params,
    )
    counts = {r["day"]: r["count"] for r in rows}
    days = []
    cur = start
    today = _today()
    while cur <= today:
        days.append({"date": cur.isoformat(), "count": int(counts.get(cur.isoformat(), 0))})
        cur += datetime.timedelta(days=1)
    return days


def build_dashboard_payload(days: int = 14) -> dict[str, Any]:
    """Return a full analytics payload for the React dashboard."""
    conn = get_db_connection()
    try:
        since = (_today() - datetime.timedelta(days=days - 1)).isoformat()

        # ---- Core count metrics -------------------------------------------
        users = _scalar(conn, "SELECT COUNT(*) FROM users") if _has_table(conn, "users") else 0
        roles = _scalar(conn, "SELECT COUNT(*) FROM roles") if _has_table(conn, "roles") else 0
        agents = _scalar(conn, "SELECT COUNT(*) FROM agent_definitions") if _has_table(conn, "agent_definitions") else 0
        published = (
            _scalar(conn, "SELECT COUNT(*) FROM agent_definitions WHERE published = 1")
            if _has_table(conn, "agent_definitions")
            else 0
        )
        skills = _scalar(conn, "SELECT COUNT(*) FROM skills") if _has_table(conn, "skills") else 0
        mcp_total = _scalar(conn, "SELECT COUNT(*) FROM mcp_servers") if _has_table(conn, "mcp_servers") else 0
        # MCP servers are configuration records now: they are connected on
        # demand (per run and per studio inspection), so there is no runtime
        # state to count. The transport split is the honest summary.
        mcp_stdio = (
            _scalar(conn, "SELECT COUNT(*) FROM mcp_servers WHERE server_type = 'stdio'")
            if _has_table(conn, "mcp_servers")
            else 0
        )
        hosted_total = _scalar(conn, "SELECT COUNT(*) FROM hosted_apps") if _has_table(conn, "hosted_apps") else 0
        hosted_running = (
            _scalar(conn, "SELECT COUNT(*) FROM hosted_apps WHERE status = 'running'")
            if _has_table(conn, "hosted_apps")
            else 0
        )
        runs = _scalar(conn, "SELECT COUNT(*) FROM agent_runs") if _has_table(conn, "agent_runs") else 0
        conversations = _scalar(conn, "SELECT COUNT(*) FROM agent_conversations") if _has_table(conn, "agent_conversations") else 0
        messages = _scalar(conn, "SELECT COUNT(*) FROM agent_messages") if _has_table(conn, "agent_messages") else 0

        runs_today = (
            _scalar(conn, "SELECT COUNT(*) FROM agent_runs WHERE date(started_at) = date(?)", (str(_today()),))
            if _has_table(conn, "agent_runs")
            else 0
        )
        messages_today = (
            _scalar(
                conn,
                "SELECT COUNT(*) FROM agent_messages WHERE date(created_at) = date(?)",
                (str(_today()),),
            )
            if _has_table(conn, "agent_messages")
            else 0
        )

        # ---- Time series ---------------------------------------------------
        runs_by_day = (
            _series(conn, "1", "agent_runs", "started_at", since=since)
            if _has_table(conn, "agent_runs")
            else []
        )
        messages_by_day = (
            _series(conn, "1", "agent_messages", "created_at", since=since)
            if _has_table(conn, "agent_messages")
            else []
        )

        # ---- Breakdowns ----------------------------------------------------
        runs_by_status = (
            _run_sql(conn, "SELECT status AS name, COUNT(*) AS value FROM agent_runs GROUP BY status ORDER BY value DESC")
            if _has_table(conn, "agent_runs")
            else []
        )
        runs_by_agent = (
            _run_sql(
                conn,
                "SELECT COALESCE(agent_slug, 'unknown') AS name, COUNT(*) AS value "
                "FROM agent_runs GROUP BY agent_slug ORDER BY value DESC LIMIT 8",
            )
            if _has_table(conn, "agent_runs")
            else []
        )
        messages_by_role = (
            _run_sql(conn, "SELECT role AS name, COUNT(*) AS value FROM agent_messages GROUP BY role")
            if _has_table(conn, "agent_messages")
            else []
        )
        top_users = (
            _run_sql(
                conn,
                "SELECT COALESCE(u.username, 'system') AS name, COUNT(*) AS value "
                "FROM agent_runs r LEFT JOIN users u ON u.id = r.user_id "
                "GROUP BY r.user_id ORDER BY value DESC LIMIT 6",
            )
            if _has_table(conn, "agent_runs") and _has_table(conn, "users")
            else []
        )

        # ---- Recently executed runs ----------------------------------------
        recent_runs = (
            _run_sql(
                conn,
                "SELECT id, agent_slug, task, status, started_at FROM agent_runs "
                "ORDER BY id DESC LIMIT 8",
            )
            if _has_table(conn, "agent_runs")
            else []
        )

        return {
            "metrics": {
                "users": users,
                "roles": roles,
                "agents": agents,
                "publishedAgents": published,
                "skills": skills,
                "mcpServers": mcp_total,
                "mcpServersStdio": mcp_stdio,
                "hostedApps": hosted_total,
                "hostedAppsRunning": hosted_running,
                "runs": runs,
                "runsToday": runs_today,
                "conversations": conversations,
                "interactions": messages,
                "interactionsToday": messages_today,
            },
            "series": {
                "runs": runs_by_day,
                "messages": messages_by_day,
            },
            "breakdowns": {
                "runsByStatus": runs_by_status,
                "runsByAgent": runs_by_agent,
                "messagesByRole": messages_by_role,
                "topUsers": top_users,
            },
            "recentRuns": recent_runs,
            "days": days,
        }
    finally:
        conn.close()
