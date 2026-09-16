"""eval_definitions / eval_results persistence."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from src.agent_platform import db


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(sep=" ", timespec="seconds")


def _json_row(row) -> dict[str, Any] | None:
    if row is None:
        return None
    data = dict(row)
    for key in ("items", "results", "scores", "expected_tool_calls", "model"):
        if isinstance(data.get(key), str):
            try:
                data[key] = json.loads(data[key])
            except Exception:
                data[key] = [] if key != "model" else None
    return data


class EvalStore:
    @staticmethod
    def ensure_tables() -> None:
        conn = db.get_db_connection()
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS eval_definitions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                definition_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                items TEXT NOT NULL,
                model TEXT,
                created_by INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        for col, decl in (("model", "TEXT"),):
            try:
                cur.execute(f"ALTER TABLE eval_definitions ADD COLUMN {col} {decl}")
            except Exception:
                pass
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS eval_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                eval_id INTEGER NOT NULL,
                definition_id INTEGER NOT NULL,
                item_index INTEGER NOT NULL,
                pass INTEGER NOT NULL DEFAULT 0,
                score REAL NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'pending',
                error TEXT,
                output TEXT,
                results TEXT,
                run_id INTEGER,
                run_public_id TEXT,
                trace_id TEXT,
                created_by INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_eval_results_eval ON eval_results(eval_id)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_eval_results_def ON eval_results(definition_id)"
        )
        conn.commit()
        conn.close()

    @classmethod
    def create_definition(
        cls,
        definition_id: int,
        name: str,
        items: list[dict[str, Any]],
        created_by: int | None = None,
        model: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        cls.ensure_tables()
        conn = db.get_db_connection()
        cur = conn.cursor()
        now = _now()
        cur.execute(
            """
            INSERT INTO eval_definitions (definition_id, name, items, model, created_by, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (int(definition_id), name, json.dumps(items), json.dumps(model) if model else None, created_by, now, now),
        )
        new_id = cur.lastrowid
        conn.commit()
        conn.close()
        return cls.get_definition(new_id)  # type: ignore[return-value]

    @classmethod
    def list_definitions(cls, definition_id: int) -> list[dict[str, Any]]:
        cls.ensure_tables()
        conn = db.get_db_connection()
        cur = conn.cursor()
        cur.execute(
            "SELECT * FROM eval_definitions WHERE definition_id = ? ORDER BY id",
            (int(definition_id),),
        )
        rows = [_json_row(r) for r in cur.fetchall()]
        conn.close()
        return [r for r in rows if r]

    @classmethod
    def get_definition(cls, eval_id: int) -> dict[str, Any] | None:
        cls.ensure_tables()
        conn = db.get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT * FROM eval_definitions WHERE id = ?", (int(eval_id),))
        row = _json_row(cur.fetchone())
        conn.close()
        return row

    @classmethod
    def update_definition(
        cls,
        eval_id: int,
        *,
        name: str | None = None,
        items: list[dict[str, Any]] | None = None,
        model: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Refresh a definition in place.

        Recorded results reference ``eval_id``; rewriting the row keeps that
        history (unlike delete + recreate, which drops it and churns ids).
        """
        cls.ensure_tables()
        current = cls.get_definition(eval_id)
        if current is None:
            return None
        conn = db.get_db_connection()
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE eval_definitions
            SET name = ?, items = ?, model = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                name if name is not None else current.get("name"),
                json.dumps(items if items is not None else current.get("items") or []),
                json.dumps(model) if model else current.get("model_json"),
                _now(),
                int(eval_id),
            ),
        )
        conn.commit()
        conn.close()
        return cls.get_definition(int(eval_id))

    @classmethod
    def delete_definition(cls, eval_id: int) -> bool:
        cls.ensure_tables()
        conn = db.get_db_connection()
        cur = conn.cursor()
        cur.execute("DELETE FROM eval_results WHERE eval_id = ?", (int(eval_id),))
        cur.execute("DELETE FROM eval_definitions WHERE id = ?", (int(eval_id),))
        conn.commit()
        conn.close()
        return True

    @classmethod
    def record_run(
        cls,
        *,
        eval_id: int,
        definition_id: int,
        item_index: int,
        passed: bool,
        score: float,
        status: str,
        output: str,
        results: dict[str, Any],
        error: str | None = None,
        run_id: int | None = None,
        run_public_id: str | None = None,
        trace_id: str | None = None,
    ) -> dict[str, Any]:
        cls.ensure_tables()
        conn = db.get_db_connection()
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO eval_results (
                eval_id, definition_id, item_index, pass, score, status,
                error, output, results, run_id, run_public_id, trace_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(eval_id),
                int(definition_id),
                int(item_index),
                1 if passed else 0,
                float(score),
                status,
                error,
                output,
                json.dumps(results),
                run_id,
                run_public_id,
                trace_id,
                _now(),
            ),
        )
        result_id = cur.lastrowid
        conn.commit()
        conn.close()
        row = cls.get_result(result_id)
        assert row is not None
        return row

    @classmethod
    def list_results(cls, eval_id: int) -> list[dict[str, Any]]:
        cls.ensure_tables()
        conn = db.get_db_connection()
        cur = conn.cursor()
        cur.execute(
            "SELECT * FROM eval_results WHERE eval_id = ? ORDER BY item_index, id",
            (int(eval_id),),
        )
        rows = [_json_row(r) for r in cur.fetchall()]
        conn.close()
        return [r for r in rows if r]

    @classmethod
    def list_results_for_definition(cls, definition_id: int) -> list[dict[str, Any]]:
        cls.ensure_tables()
        conn = db.get_db_connection()
        cur = conn.cursor()
        cur.execute(
            "SELECT * FROM eval_results WHERE definition_id = ? ORDER BY eval_id, item_index, id",
            (int(definition_id),),
        )
        rows = [_json_row(r) for r in cur.fetchall()]
        conn.close()
        return [r for r in rows if r]

    @classmethod
    def get_result(cls, result_id: int) -> dict[str, Any] | None:
        cls.ensure_tables()
        conn = db.get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT * FROM eval_results WHERE id = ?", (int(result_id),))
        row = _json_row(cur.fetchone())
        conn.close()
        return row
