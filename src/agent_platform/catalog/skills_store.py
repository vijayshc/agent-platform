"""maf_skills table — filesystem SKILL.md packages.

Every package carries the id of the user who created/imported it
(``created_by``) so the generic resource-access store can tell an owner from a
granted role.  Rows created before this column existed keep ``created_by IS
NULL`` and stay grandfathered-visible, exactly like a seeded package.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from src.agent_platform import db


class MafSkillStore:
    @staticmethod
    def _ensure_columns(cur) -> None:
        """Add ``created_by`` to a table created before ownership existed."""
        cols = {r["name"] for r in cur.execute("PRAGMA table_info(maf_skills)").fetchall()}
        if "created_by" not in cols:
            cur.execute("ALTER TABLE maf_skills ADD COLUMN created_by INTEGER")

    @staticmethod
    def ensure_tables() -> None:
        conn = db.get_db_connection()
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS maf_skills (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                description TEXT,
                path TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1,
                created_by INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        MafSkillStore._ensure_columns(cur)
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_maf_skills_owner ON maf_skills(created_by)"
        )
        conn.commit()
        conn.close()

    @classmethod
    def upsert(
        cls,
        name: str,
        description: str,
        path: str,
        enabled: bool = True,
        created_by: int | None = None,
    ) -> dict[str, Any]:
        """Insert or update a package row, preserving the original owner.

        ``created_by`` is only written on insert, or to fill a previously
        owner-less row; an existing owner is never overwritten.
        """
        cls.ensure_tables()
        owner = int(created_by) if created_by is not None else None
        conn = db.get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT id FROM maf_skills WHERE name = ?", (name,))
        row = cur.fetchone()
        if row:
            existing_id = row["id"] if not isinstance(row, tuple) else row[0]
            cur.execute(
                "UPDATE maf_skills SET description = ?, path = ?, enabled = ?, "
                "created_by = COALESCE(created_by, ?) WHERE id = ?",
                (description, path, 1 if enabled else 0, owner, existing_id),
            )
        else:
            cur.execute(
                "INSERT INTO maf_skills (name, description, path, enabled, created_by, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    name,
                    description,
                    path,
                    1 if enabled else 0,
                    owner,
                    datetime.now(timezone.utc).isoformat(sep=" ", timespec="seconds"),
                ),
            )
        conn.commit()
        conn.close()
        return cls.get_by_name(name)  # type: ignore[return-value]

    @classmethod
    def claim_owner(cls, name: str, user_id: int) -> bool:
        """Set ``created_by`` when the row has no owner yet; never reassign.

        Returns whether the row exists (an already-owned row is left intact but
        still reported as found, so callers can distinguish "not a package"
        from "owned by someone else").
        """
        cls.ensure_tables()
        conn = db.get_db_connection()
        cur = conn.cursor()
        cur.execute(
            "UPDATE maf_skills SET created_by = ? WHERE name = ? AND created_by IS NULL",
            (int(user_id), name),
        )
        conn.commit()
        cur.execute("SELECT 1 FROM maf_skills WHERE name = ?", (name,))
        found = cur.fetchone() is not None
        conn.close()
        return found

    @classmethod
    def get_by_name(cls, name: str) -> dict[str, Any] | None:
        cls.ensure_tables()
        conn = db.get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT * FROM maf_skills WHERE name = ?", (name,))
        row = cur.fetchone()
        conn.close()
        return dict(row) if row else None

    @classmethod
    def get_by_id(cls, package_id: int) -> dict[str, Any] | None:
        cls.ensure_tables()
        conn = db.get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT * FROM maf_skills WHERE id = ?", (int(package_id),))
        row = cur.fetchone()
        conn.close()
        return dict(row) if row else None

    @classmethod
    def list_enabled(cls) -> list[dict[str, Any]]:
        cls.ensure_tables()
        conn = db.get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT * FROM maf_skills WHERE enabled = 1 ORDER BY name")
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()
        return rows

    @classmethod
    def list_all(cls) -> list[dict[str, Any]]:
        cls.ensure_tables()
        conn = db.get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT * FROM maf_skills ORDER BY name")
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()
        return rows

    @classmethod
    def delete_by_name(cls, name: str) -> bool:
        cls.ensure_tables()
        conn = db.get_db_connection()
        cur = conn.cursor()
        cur.execute("DELETE FROM maf_skills WHERE name = ?", (name,))
        deleted = cur.rowcount > 0
        conn.commit()
        conn.close()
        return deleted
