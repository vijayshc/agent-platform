"""Persisted record of a hosted application."""

from __future__ import annotations

import json
from datetime import datetime

from src.utils.database import get_db_connection
from src.auth import resource_access

HOSTED_APP_RESOURCE_TYPE = "hosted_app"


class HostedAppStatus:
    STOPPED = "stopped"
    RUNNING = "running"
    ERROR = "error"
    INSTALLING = "installing"


class HostedApp:
    """One imported application: its manifest, its runtime state, its tier."""

    def __init__(self, **fields):
        for key in ("created_at", "updated_at", "started_at"):
            value = fields.get(key)
            if isinstance(value, str):
                fields[key] = datetime.fromisoformat(value)
        self.id = fields.get("id")
        self.slug = fields.get("slug")
        self.name = fields.get("name")
        self.description = fields.get("description") or ""
        self.version = fields.get("version") or ""
        self.entry = fields.get("entry")
        self.manifest = fields.get("manifest") or {}
        if isinstance(self.manifest, str):
            try:
                self.manifest = json.loads(self.manifest)
            except json.JSONDecodeError:
                self.manifest = {}
        self.status = fields.get("status") or HostedAppStatus.STOPPED
        self.tier = fields.get("tier") or ""
        self.tier_reason = fields.get("tier_reason") or ""
        self.filesystem_isolated = bool(fields.get("filesystem_isolated") or False)
        self.last_error = fields.get("last_error") or ""
        self.pid = fields.get("pid")
        self.autostart = bool(fields.get("autostart")) if fields.get("autostart") is not None else True
        self.owner = fields.get("owner") or ""
        self.install_step = fields.get("install_step") or ""
        #: What the operator asked for: "running" until they stop it, so a
        #: platform restart brings back exactly what was running.
        self.desired_state = fields.get("desired_state") or ""
        raw_types = fields.get("allowed_content_types") or "[]"
        if isinstance(raw_types, str):
            try:
                raw_types = json.loads(raw_types)
            except json.JSONDecodeError:
                raw_types = []
        self.allowed_content_types = list(raw_types) if isinstance(raw_types, list) else []
        raw_limits = fields.get("content_type_limits") or "{}"
        if isinstance(raw_limits, str):
            try:
                raw_limits = json.loads(raw_limits)
            except json.JSONDecodeError:
                raw_limits = {}
        self.content_type_limits = dict(raw_limits) if isinstance(raw_limits, dict) else {}
        raw_ips = fields.get("source_ip_allowlist") or "[]"
        if isinstance(raw_ips, str):
            try:
                raw_ips = json.loads(raw_ips)
            except json.JSONDecodeError:
                raw_ips = []
        self.source_ip_allowlist = list(raw_ips) if isinstance(raw_ips, list) else []
        self.block_attachments = bool(fields.get("block_attachments") or False)
        self.created_by = fields.get("created_by")
        self.created_at = fields.get("created_at") or datetime.now()
        self.updated_at = fields.get("updated_at") or datetime.now()
        self.started_at = fields.get("started_at")

    # -- persistence -------------------------------------------------------

    @staticmethod
    def _dict_factory(cursor, row):
        return {column[0]: row[index] for index, column in enumerate(cursor.description)}

    @classmethod
    def create_table(cls) -> None:
        conn = get_db_connection()
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS hosted_apps (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    slug TEXT NOT NULL UNIQUE,
                    name TEXT NOT NULL,
                    description TEXT DEFAULT '',
                    version TEXT DEFAULT '',
                    entry TEXT NOT NULL,
                    manifest TEXT DEFAULT '{}',
                    status TEXT DEFAULT 'stopped',
                    tier TEXT DEFAULT '',
                    tier_reason TEXT DEFAULT '',
                    filesystem_isolated INTEGER DEFAULT 0,
                    last_error TEXT DEFAULT '',
                    pid INTEGER,
                    autostart INTEGER DEFAULT 1,
                    owner TEXT DEFAULT '',
                    install_step TEXT DEFAULT '',
                    desired_state TEXT DEFAULT '',
                    allowed_content_types TEXT DEFAULT '[]',
                    content_type_limits TEXT DEFAULT '{}',
                    source_ip_allowlist TEXT DEFAULT '[]',
                    block_attachments INTEGER DEFAULT 0,
                    created_by INTEGER,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    started_at TIMESTAMP
                )
                """
            )
            # Existing databases predate the owner column; add it in place.
            columns = {row[1] for row in conn.execute("PRAGMA table_info(hosted_apps)")}
            if "owner" not in columns:
                conn.execute("ALTER TABLE hosted_apps ADD COLUMN owner TEXT DEFAULT ''")
            if "install_step" not in columns:
                conn.execute("ALTER TABLE hosted_apps ADD COLUMN install_step TEXT DEFAULT ''")
            if "desired_state" not in columns:
                conn.execute("ALTER TABLE hosted_apps ADD COLUMN desired_state TEXT DEFAULT ''")
            if "allowed_content_types" not in columns:
                conn.execute("ALTER TABLE hosted_apps ADD COLUMN allowed_content_types TEXT DEFAULT '[]'")
            if "content_type_limits" not in columns:
                conn.execute("ALTER TABLE hosted_apps ADD COLUMN content_type_limits TEXT DEFAULT '{}'")
            if "source_ip_allowlist" not in columns:
                conn.execute("ALTER TABLE hosted_apps ADD COLUMN source_ip_allowlist TEXT DEFAULT '[]'")
            if "block_attachments" not in columns:
                conn.execute("ALTER TABLE hosted_apps ADD COLUMN block_attachments INTEGER DEFAULT 0")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS hosted_app_role_access (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    app_id INTEGER NOT NULL,
                    role_id INTEGER NOT NULL,
                    granted_by INTEGER,
                    granted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(app_id, role_id)
                )
                """
            )
            conn.commit()
        finally:
            conn.close()

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "slug": self.slug,
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "entry": self.entry,
            "status": self.status,
            "tier": self.tier,
            "tier_reason": self.tier_reason,
            "filesystem_isolated": self.filesystem_isolated,
            "last_error": self.last_error,
            "pid": self.pid,
            "autostart": self.autostart,
            "owner": self.owner,
            "install_step": self.install_step,
            "desired_state": self.desired_state,
            "content_policy": {
                "allowed_content_types": self.allowed_content_types,
                "block_attachments": self.block_attachments,
                "content_type_limits": self.content_type_limits,
                "source_ip_allowlist": self.source_ip_allowlist,
            },
            "created_by": self.created_by,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "url": f"/apps/{self.slug}/",
        }

    @classmethod
    def _query(cls, sql: str, params: tuple = ()) -> list["HostedApp"]:
        conn = get_db_connection()
        try:
            conn.row_factory = cls._dict_factory
            rows = conn.execute(sql, params).fetchall()
        finally:
            conn.close()
        return [cls(**row) for row in rows]

    @classmethod
    def get_all(cls) -> list["HostedApp"]:
        return cls._query("SELECT * FROM hosted_apps ORDER BY name COLLATE NOCASE")

    @classmethod
    def get_by_slug(cls, slug: str) -> "HostedApp | None":
        rows = cls._query("SELECT * FROM hosted_apps WHERE slug = ?", (slug,))
        return rows[0] if rows else None

    @classmethod
    def get_by_id(cls, app_id: int) -> "HostedApp | None":
        rows = cls._query("SELECT * FROM hosted_apps WHERE id = ?", (app_id,))
        return rows[0] if rows else None

    @classmethod
    def upsert(cls, manifest: dict, *, created_by: int | None = None, autostart: bool = True) -> "HostedApp":
        """Insert or update the record for an imported app, keeping its identity."""
        existing = cls.get_by_slug(manifest["slug"])
        conn = get_db_connection()
        try:
            payload = (
                manifest.get("description", ""),
                str(manifest.get("version") or ""),
                manifest["entry"],
                json.dumps(manifest),
                int(bool(autostart)),
            )
            if existing:
                conn.execute(
                    """UPDATE hosted_apps SET description = ?, version = ?, entry = ?, manifest = ?,
                       autostart = ?, updated_at = CURRENT_TIMESTAMP WHERE slug = ?""",
                    (*payload, manifest["slug"]),
                )
            else:
                conn.execute(
                    """INSERT INTO hosted_apps (slug, name, description, version, entry, manifest,
                       autostart, created_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        manifest["slug"], manifest["name"], manifest.get("description", ""),
                        str(manifest.get("version") or ""), manifest["entry"],
                        json.dumps(manifest), int(bool(autostart)), created_by,
                    ),
                )
            conn.commit()
        finally:
            conn.close()
        return cls.get_by_slug(manifest["slug"])

    @classmethod
    def set_runtime(
        cls,
        slug: str,
        *,
        status: str | None = None,
        pid: int | None = None,
        tier: str | None = None,
        tier_reason: str | None = None,
        filesystem_isolated: bool | None = None,
        last_error: str | None = None,
        owner: str | None = None,
        install_step: str | None = None,
        desired_state: str | None = None,
        mark_started: bool = False,
    ) -> None:
        sets = ["updated_at = CURRENT_TIMESTAMP"]
        params: list = []
        for column, value in (
            ("status", status), ("pid", pid), ("tier", tier), ("tier_reason", tier_reason),
            ("last_error", last_error), ("owner", owner), ("install_step", install_step),
            ("desired_state", desired_state),
        ):
            if value is not None:
                sets.append(f"{column} = ?")
                params.append(value)
        if filesystem_isolated is not None:
            sets.append("filesystem_isolated = ?")
            params.append(int(filesystem_isolated))
        if mark_started:
            sets.append("started_at = CURRENT_TIMESTAMP")
        if status == HostedAppStatus.STOPPED:
            sets.append("pid = NULL")
            sets.append("owner = ''")
        params.append(slug)
        conn = get_db_connection()
        try:
            conn.execute(f"UPDATE hosted_apps SET {', '.join(sets)} WHERE slug = ?", tuple(params))
            conn.commit()
        finally:
            conn.close()

    # -- per-application role access ---------------------------------------
    #
    # Grants live in the shared ``resource_role_access`` table (resource_type
    # "hosted_app", resource_id = hosted_apps.id).  The public API keeps taking
    # the slug, which is the app's stable URL identity, and resolves it to the
    # row id internally.

    @classmethod
    def list_access(cls, slug: str) -> list[dict]:
        record = cls.get_by_slug(slug)
        if record is None:
            return []
        return resource_access.list_access(HOSTED_APP_RESOURCE_TYPE, int(record.id))

    @classmethod
    def set_access(cls, slug: str, role_ids: list[int], granted_by: int | None = None) -> list[dict]:
        record = cls.get_by_slug(slug)
        if record is None:
            return []
        return resource_access.set_access(
            HOSTED_APP_RESOURCE_TYPE, int(record.id), role_ids, granted_by=granted_by
        )

    @classmethod
    def grant_access(cls, slug: str, role_id: int, granted_by: int | None = None) -> list[dict]:
        record = cls.get_by_slug(slug)
        if record is None:
            return []
        return resource_access.grant_access(
            HOSTED_APP_RESOURCE_TYPE, int(record.id), role_id, granted_by=granted_by
        )

    @classmethod
    def revoke_access(cls, slug: str, role_id: int) -> list[dict]:
        record = cls.get_by_slug(slug)
        if record is None:
            return []
        return resource_access.revoke_access(HOSTED_APP_RESOURCE_TYPE, int(record.id), role_id)

    @classmethod
    def set_content_policy(cls, slug: str, allowed: list[str], block_attachments: bool,
                           limits: dict | None = None, source_ips: list[str] | None = None) -> "HostedApp | None":
        """Each app carries its own policy: a data tool and a dashboard have no
        business sharing one allowlist, a cap on what one may download is equally
        its own, and so is the set of addresses allowed to reach it."""
        conn = get_db_connection()
        try:
            conn.execute(
                """UPDATE hosted_apps SET allowed_content_types = ?, block_attachments = ?,
                   content_type_limits = ?, source_ip_allowlist = ?, updated_at = CURRENT_TIMESTAMP
                   WHERE slug = ?""",
                (json.dumps(list(allowed)), int(bool(block_attachments)), json.dumps(dict(limits or {})),
                 json.dumps(list(source_ips or [])), slug),
            )
            conn.commit()
        finally:
            conn.close()
        return cls.get_by_slug(slug)

    @classmethod
    def update_details(cls, slug: str, *, name: str | None = None, description: str | None = None,
                       autostart: bool | None = None) -> "HostedApp | None":
        """Rename an application, change its description or its boot behaviour.

        The slug is the URL and never changes here, so links and audit history
        stay valid. Turning autostart on also clears an earlier explicit stop:
        the operator is asking for the app to be up, so the next platform boot
        must be allowed to honour that rather than replay the old "stopped".
        """
        sets, params = [], []
        if name is not None and name.strip():
            sets.append("name = ?")
            params.append(name.strip()[:120])
        if description is not None:
            sets.append("description = ?")
            params.append(description.strip()[:500])
        if autostart is not None:
            sets.append("autostart = ?")
            params.append(int(bool(autostart)))
            if autostart:
                sets.append("desired_state = ?")
                params.append("")
        if not sets:
            return cls.get_by_slug(slug)
        sets.append("updated_at = CURRENT_TIMESTAMP")
        params.append(slug)
        conn = get_db_connection()
        try:
            conn.execute(f"UPDATE hosted_apps SET {', '.join(sets)} WHERE slug = ?", tuple(params))
            conn.commit()
        finally:
            conn.close()
        return cls.get_by_slug(slug)

    @classmethod
    def delete(cls, slug: str) -> None:
        conn = get_db_connection()
        try:
            row = conn.execute("SELECT id FROM hosted_apps WHERE slug = ?", (slug,)).fetchone()
            if row is not None:
                resource_access.ensure_tables()
                conn.execute(
                    "DELETE FROM resource_role_access WHERE resource_type = ? AND resource_id = ?",
                    (HOSTED_APP_RESOURCE_TYPE, row[0]),
                )
                conn.execute("DELETE FROM hosted_app_role_access WHERE app_id = ?", (row[0],))
            conn.execute("DELETE FROM hosted_apps WHERE slug = ?", (slug,))
            conn.commit()
        finally:
            conn.close()


def _hosted_app_owner(app_id: int) -> int | None:
    record = HostedApp.get_by_id(int(app_id))
    return record.created_by if record else None


def _hosted_app_exists(app_id: int) -> bool:
    return HostedApp.get_by_id(int(app_id)) is not None


# Register owner resolution for the generic access API.  Idempotent; runs at
# import time so ``resource_access.owner_of("hosted_app", id)`` works as soon as
# this model is loaded.
resource_access.register_resource(
    HOSTED_APP_RESOURCE_TYPE, _hosted_app_owner, _hosted_app_exists
)
