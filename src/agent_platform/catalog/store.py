"""agent_definitions persistence."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from src.agent_platform import db
from src.auth import resource_access

AGENT_RESOURCE_TYPE = "agent"


def _agent_owner(definition_id: int) -> int | None:
    row = DefinitionStore.get_by_id(int(definition_id))
    return row.get("created_by") if row else None


def _agent_exists(definition_id: int) -> bool:
    return DefinitionStore.get_by_id(int(definition_id)) is not None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(sep=" ", timespec="seconds")


def _row(row) -> dict[str, Any] | None:
    if row is None:
        return None
    data = dict(row)
    if isinstance(data.get("config"), str):
        try:
            data["config"] = json.loads(data["config"])
        except Exception:
            data["config"] = {}
    data["published"] = bool(data.get("published"))
    return data


class DefinitionStore:
    @staticmethod
    def _ensure_columns(cur) -> None:
        """Add columns introduced after the initial migration to existing DBs."""
        cols = {r["name"] for r in cur.execute("PRAGMA table_info(agent_definitions)").fetchall()}
        if "updated_by" not in cols:
            cur.execute("ALTER TABLE agent_definitions ADD COLUMN updated_by INTEGER")
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS agent_definition_versions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                agent_id INTEGER NOT NULL,
                version INTEGER NOT NULL,
                name TEXT NOT NULL,
                kind TEXT NOT NULL,
                config TEXT NOT NULL,
                created_by INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(agent_id, version)
            )
            """
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_agent_versions_agent ON agent_definition_versions(agent_id, version)"
        )

    @staticmethod
    def ensure_tables() -> None:
        conn = db.get_db_connection()
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS agent_definitions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                slug TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL,
                kind TEXT NOT NULL,
                config TEXT NOT NULL,
                published INTEGER NOT NULL DEFAULT 0,
                version INTEGER NOT NULL DEFAULT 1,
                created_by INTEGER,
                updated_by INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        DefinitionStore._ensure_columns(cur)
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS agent_definition_versions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                agent_id INTEGER NOT NULL,
                version INTEGER NOT NULL,
                name TEXT NOT NULL,
                kind TEXT NOT NULL,
                config TEXT NOT NULL,
                created_by INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(agent_id, version)
            )
            """
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_agent_versions_agent ON agent_definition_versions(agent_id, version)"
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS agent_definition_role_access (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                agent_id INTEGER NOT NULL,
                role_id INTEGER NOT NULL,
                granted_by INTEGER,
                granted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(agent_id, role_id)
            )
            """
        )
        conn.commit()
        conn.close()

    @staticmethod
    def grant_role_access(definition_id: int, role_id: int, granted_by: int | None = None) -> list[dict]:
        """Grant one role on a definition (delegates to the generic store)."""
        return resource_access.grant_access(
            AGENT_RESOURCE_TYPE, definition_id, role_id, granted_by=granted_by
        )

    @staticmethod
    def revoke_role_access(definition_id: int, role_id: int) -> list[dict]:
        """Revoke one role from a definition (delegates to the generic store)."""
        return resource_access.revoke_access(AGENT_RESOURCE_TYPE, definition_id, role_id)

    @staticmethod
    def list_access(definition_id: int) -> list[dict]:
        """Granted roles for a definition: ``[{"role_id", "role_name"}]``."""
        return resource_access.list_access(AGENT_RESOURCE_TYPE, definition_id)

    @classmethod
    def upsert(
        cls,
        *,
        slug: str,
        name: str,
        kind: str,
        config: dict[str, Any],
        published: bool = False,
        created_by: int | None = None,
        updated_by: int | None = None,
    ) -> dict[str, Any]:
        cls.ensure_tables()
        existing = cls.get_by_slug(slug)
        conn = db.get_db_connection()
        cur = conn.cursor()
        payload = json.dumps(config)
        now = _now()
        if existing:
            # Publish-only versioning: edits never bump the version here.
            version = int(existing.get("version") or 1)
            owner = existing.get("created_by") if existing.get("created_by") is not None else created_by
            editor = existing.get("updated_by") if updated_by is None else updated_by
            cur.execute(
                """
                UPDATE agent_definitions
                SET name = ?, kind = ?, config = ?, published = ?, version = ?, created_by = ?, updated_by = ?, updated_at = ?
                WHERE id = ?
                """,
                (name, kind, payload, 1 if published else 0, version, owner, editor, now, existing["id"]),
            )
            conn.commit()
            conn.close()
            return cls.get_by_id(existing["id"])  # type: ignore[return-value]
        cur.execute(
            """
            INSERT INTO agent_definitions (slug, name, kind, config, published, version, created_by, updated_by, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?, ?)
            """,
            (slug, name, kind, payload, 1 if published else 0, created_by, updated_by, now, now),
        )
        new_id = cur.lastrowid
        conn.commit()
        conn.close()
        row = cls.get_by_id(new_id)
        # A row created already-published is its own first published snapshot
        # so has_draft_changes is False immediately after creation.
        if row is not None and published:
            try:
                cls.snapshot(row, actor=created_by)
            except Exception:
                pass
        return row  # type: ignore[return-value]

    @classmethod
    def get_by_id(cls, definition_id: int) -> dict[str, Any] | None:
        cls.ensure_tables()
        conn = db.get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT * FROM agent_definitions WHERE id = ?", (definition_id,))
        row = cur.fetchone()
        conn.close()
        return _row(row)

    @classmethod
    def get_by_slug(cls, slug: str) -> dict[str, Any] | None:
        cls.ensure_tables()
        conn = db.get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT * FROM agent_definitions WHERE slug = ?", (slug,))
        row = cur.fetchone()
        conn.close()
        return _row(row)

    @classmethod
    def resolve(cls, identifier: str | int, *, published_only: bool = False) -> dict[str, Any] | None:
        if isinstance(identifier, int) or (isinstance(identifier, str) and identifier.isdigit()):
            row = cls.get_by_id(int(identifier))
        else:
            row = cls.get_by_slug(str(identifier))
        if row is None:
            return None
        if published_only and not row.get("published"):
            return None
        return row

    get = resolve

    @classmethod
    def list_published(cls) -> list[dict[str, Any]]:
        cls.ensure_tables()
        conn = db.get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT * FROM agent_definitions WHERE published = 1 ORDER BY name")
        rows = [_row(r) for r in cur.fetchall()]
        conn.close()
        return [r for r in rows if r]

    @classmethod
    def list_all(cls) -> list[dict[str, Any]]:
        cls.ensure_tables()
        conn = db.get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT * FROM agent_definitions ORDER BY name")
        rows = [_row(r) for r in cur.fetchall()]
        conn.close()
        return [r for r in rows if r]

    @classmethod
    def set_published(cls, definition_id: int, published: bool, actor: int | None = None) -> dict[str, Any] | None:
        conn = db.get_db_connection()
        cur = conn.cursor()
        if actor is not None:
            cur.execute(
                "UPDATE agent_definitions SET published = ?, updated_by = ?, updated_at = ? WHERE id = ?",
                (1 if published else 0, actor, _now(), definition_id),
            )
        else:
            cur.execute(
                "UPDATE agent_definitions SET published = ?, updated_at = ? WHERE id = ?",
                (1 if published else 0, _now(), definition_id),
            )
        conn.commit()
        conn.close()
        return cls.get_by_id(definition_id)

    @classmethod
    def save(
        cls,
        *,
        definition_id: int | None = None,
        slug: str,
        name: str,
        kind: str,
        config: dict[str, Any],
        published: bool | None = None,
        created_by: int | None = None,
        updated_by: int | None = None,
        bump_version: bool = True,
    ) -> dict[str, Any]:
        # Publish-only versioning: save() NEVER bumps the version. The
        # bump_version arg is kept for backwards compatibility but ignored.
        _ = bump_version
        cls.ensure_tables()
        existing = None
        if definition_id is not None:
            existing = cls.get_by_id(int(definition_id))
        if existing is None:
            existing = cls.get_by_slug(slug)
        conn = db.get_db_connection()
        cur = conn.cursor()
        payload = json.dumps(config)
        now = _now()
        if existing:
            version = int(existing.get("version") or 1)
            pub = existing.get("published") if published is None else published
            editor = existing.get("updated_by") if updated_by is None else updated_by
            cur.execute(
                """
                UPDATE agent_definitions
                SET slug = ?, name = ?, kind = ?, config = ?, published = ?, version = ?, updated_by = ?, updated_at = ?
                WHERE id = ?
                """,
                (slug, name, kind, payload, 1 if pub else 0, version, editor, now, existing["id"]),
            )
            conn.commit()
            conn.close()
            return cls.get_by_id(existing["id"])  # type: ignore[return-value]
        cur.execute(
            """
            INSERT INTO agent_definitions (slug, name, kind, config, published, version, created_by, updated_by, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?, ?)
            """,
            (slug, name, kind, payload, 1 if published else 0, created_by, updated_by, now, now),
        )
        new_id = cur.lastrowid
        conn.commit()
        conn.close()
        row = cls.get_by_id(new_id)
        if row is not None and bool(published):
            try:
                cls.snapshot(row, actor=created_by)
            except Exception:
                pass
        return row  # type: ignore[return-value]

    @classmethod
    def delete(cls, definition_id: int) -> bool:
        cls.ensure_tables()
        resource_access.ensure_tables()
        conn = db.get_db_connection()
        cur = conn.cursor()
        cur.execute(
            "DELETE FROM resource_role_access WHERE resource_type = ? AND resource_id = ?",
            (AGENT_RESOURCE_TYPE, definition_id),
        )
        cur.execute("DELETE FROM agent_definition_role_access WHERE agent_id = ?", (definition_id,))
        cur.execute("DELETE FROM agent_definition_versions WHERE agent_id = ?", (definition_id,))
        cur.execute("DELETE FROM agent_definitions WHERE id = ?", (definition_id,))
        affected = cur.rowcount > 0
        conn.commit()
        conn.close()
        return affected

    # ------------------------------------------------------------------
    # Publish-only versioning
    # ------------------------------------------------------------------

    @staticmethod
    def _version_row(row) -> dict[str, Any] | None:
        if row is None:
            return None
        data = dict(row)
        if isinstance(data.get("config"), str):
            try:
                data["config"] = json.loads(data["config"])
            except Exception:
                data["config"] = {}
        return data

    @classmethod
    def list_versions(cls, agent_id: int) -> list[dict[str, Any]]:
        cls.ensure_tables()
        conn = db.get_db_connection()
        cur = conn.cursor()
        cur.execute(
            "SELECT * FROM agent_definition_versions WHERE agent_id = ? ORDER BY version ASC",
            (int(agent_id),),
        )
        rows = [cls._version_row(r) for r in cur.fetchall()]
        conn.close()
        return [r for r in rows if r]

    @classmethod
    def get_version(cls, agent_id: int, version: int) -> dict[str, Any] | None:
        cls.ensure_tables()
        conn = db.get_db_connection()
        cur = conn.cursor()
        cur.execute(
            "SELECT * FROM agent_definition_versions WHERE agent_id = ? AND version = ?",
            (int(agent_id), int(version)),
        )
        row = cur.fetchone()
        conn.close()
        return cls._version_row(row)

    @classmethod
    def _latest_snapshot(cls, agent_id: int) -> dict[str, Any] | None:
        cls.ensure_tables()
        conn = db.get_db_connection()
        cur = conn.cursor()
        cur.execute(
            "SELECT * FROM agent_definition_versions WHERE agent_id = ? ORDER BY version DESC LIMIT 1",
            (int(agent_id),),
        )
        row = cur.fetchone()
        conn.close()
        return cls._version_row(row)

    @classmethod
    def versions_count(cls, agent_id: int) -> int:
        cls.ensure_tables()
        conn = db.get_db_connection()
        cur = conn.cursor()
        cur.execute(
            "SELECT COUNT(*) AS n FROM agent_definition_versions WHERE agent_id = ?",
            (int(agent_id),),
        )
        row = cur.fetchone()
        conn.close()
        try:
            return int(dict(row).get("n") or 0)
        except Exception:
            return 0

    @classmethod
    def published_version(cls, agent_id: int) -> int | None:
        snap = cls._latest_snapshot(int(agent_id))
        if not snap:
            return None
        try:
            return int(snap.get("version"))
        except (TypeError, ValueError):
            return None

    @classmethod
    def snapshot(cls, agent_row: dict[str, Any], actor: int | None = None) -> dict[str, Any] | None:
        """Persist the NEW published config as an immutable version row."""
        if not agent_row or agent_row.get("id") is None:
            return None
        cls.ensure_tables()
        agent_id = int(agent_row["id"])
        version = int(agent_row.get("version") or 1)
        name = agent_row.get("name") or ""
        kind = agent_row.get("kind") or "agent"
        config = agent_row.get("config") or {}
        payload = json.dumps(config) if not isinstance(config, str) else config
        created_by = actor
        if created_by is None:
            created_by = agent_row.get("updated_by")
        if created_by is None:
            created_by = agent_row.get("created_by")
        conn = db.get_db_connection()
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO agent_definition_versions (agent_id, version, name, kind, config, created_by, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(agent_id, version) DO UPDATE SET name = excluded.name, kind = excluded.kind, config = excluded.config
            """,
            (agent_id, version, name, kind, payload, created_by, _now()),
        )
        conn.commit()
        conn.close()
        return cls.get_version(agent_id, version)

    @classmethod
    def publish(
        cls, definition_id: int, published: bool, actor: int | None = None
    ) -> dict[str, Any] | None:
        """Publish (bump + snapshot) or unpublish (flag flip only).

        The first publish keeps version 1 and snapshots it, so history
        starts at v1; every later publish bumps and snapshots the new one.
        """
        cls.ensure_tables()
        row = cls.get_by_id(int(definition_id))
        if row is None:
            return None
        if not published:
            return cls.set_published(int(definition_id), False, actor=actor)
        already = cls.versions_count(int(definition_id)) > 0
        new_version = int(row.get("version") or 1) + (1 if already else 0)
        conn = db.get_db_connection()
        cur = conn.cursor()
        editor = actor if actor is not None else row.get("updated_by")
        cur.execute(
            "UPDATE agent_definitions SET published = 1, version = ?, updated_by = ?, updated_at = ? WHERE id = ?",
            (new_version, editor, _now(), int(definition_id)),
        )
        conn.commit()
        conn.close()
        fresh = cls.get_by_id(int(definition_id))
        if fresh is not None:
            try:
                cls.snapshot(fresh, actor=actor)
            except Exception:
                pass
        return fresh

    @classmethod
    def rollback(
        cls, agent_id: int, version: int, actor: int | None = None
    ) -> dict[str, Any] | None:
        """Restore name/kind/config from a snapshot as a draft edit.

        The row's version and published flag are left untouched.
        """
        snap = cls.get_version(int(agent_id), int(version))
        if snap is None:
            return None
        row = cls.get_by_id(int(agent_id))
        if row is None:
            return None
        payload = json.dumps(snap.get("config") or {})
        editor = actor if actor is not None else row.get("updated_by")
        conn = db.get_db_connection()
        cur = conn.cursor()
        cur.execute(
            "UPDATE agent_definitions SET name = ?, kind = ?, config = ?, updated_by = ?, updated_at = ? WHERE id = ?",
            (snap.get("name"), snap.get("kind"), payload, editor, _now(), int(agent_id)),
        )
        conn.commit()
        conn.close()
        return cls.get_by_id(int(agent_id))

    @classmethod
    def has_draft_changes(cls, agent_id: int) -> bool:
        row = cls.get_by_id(int(agent_id))
        if row is None:
            return False
        snap = cls._latest_snapshot(int(agent_id))
        if snap is None:
            # Draft that was never published always counts as having changes.
            return True
        if (row.get("name") or "") != (snap.get("name") or ""):
            return True
        if (row.get("kind") or "") != (snap.get("kind") or ""):
            return True
        cur_cfg = row.get("config") or {}
        snap_cfg = snap.get("config") or {}
        try:
            return json.dumps(cur_cfg, sort_keys=True) != json.dumps(snap_cfg, sort_keys=True)
        except Exception:
            return cur_cfg != snap_cfg


# Register owner resolution for the generic access API.  Idempotent; runs at
# import time so ``resource_access.owner_of("agent", id)`` works as soon as this
# store is loaded (which the /api/v1 blueprint always does).
resource_access.register_resource(AGENT_RESOURCE_TYPE, _agent_owner, _agent_exists)

