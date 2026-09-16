"""Hashed API keys with scopes (runs:write, agents:read)."""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timezone
from typing import Any

from src.models.secrets import SecretString
from src.agent_platform import db
VALID_SCOPES = {"runs:write", "agents:read", "runs:read", "agents:write"}


def _hash_key(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class ApiKeyStore:
    @staticmethod
    def ensure_tables() -> None:
        conn = db.get_db_connection()
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS api_keys (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT,
                key_hash TEXT NOT NULL UNIQUE,
                prefix TEXT,
                user_id INTEGER,
                scopes TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_used_at TIMESTAMP,
                revoked INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        conn.commit()
        conn.close()

    @classmethod
    def create(cls, *, user_id: int = 1, name: str, scopes: list[str] | None = None) -> dict[str, Any]:
        cls.ensure_tables()
        raw = "apk_" + secrets.token_urlsafe(32)
        key_hash = _hash_key(raw)
        prefix = raw[:10]
        scope_list = [s for s in (scopes or ["runs:write", "agents:read"]) if s in VALID_SCOPES]
        conn = db.get_db_connection()
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO api_keys (name, key_hash, prefix, user_id, scopes, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                name,
                key_hash,
                prefix,
                user_id,
                ",".join(scope_list),
                datetime.now(timezone.utc).isoformat(sep=" ", timespec="seconds"),
            ),
        )
        kid = cur.lastrowid
        conn.commit()
        conn.close()
        # The plaintext ``key`` is returned exactly once at creation. It is never
        # stored: the table holds only the SHA-256 hash and a 10-char prefix, and
        # list_for_user()/verify() never re-read it. Wrapping it in SecretString
        # keeps repr()/logging masked for any caller that must echo the response.
        return {
            "id": kid,
            "name": name,
            "key": SecretString(raw),
            "prefix": prefix,
            "scopes": scope_list,
            "user_id": user_id,
        }

    @classmethod
    def verify(cls, raw: str) -> dict[str, Any] | None:
        cls.ensure_tables()
        if not raw:
            return None
        key_hash = _hash_key(raw.strip())
        conn = db.get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT * FROM api_keys WHERE key_hash = ? AND revoked = 0", (key_hash,))
        row = cur.fetchone()
        if row:
            cur.execute(
                "UPDATE api_keys SET last_used_at = ? WHERE id = ?",
                (datetime.now(timezone.utc).isoformat(sep=" ", timespec="seconds"), dict(row)["id"]),
            )
            conn.commit()
        conn.close()
        if not row:
            return None
        data = dict(row)
        data["scopes"] = [s for s in (data.get("scopes") or "").split(",") if s]
        return data

    @classmethod
    def list_for_user(cls, user_id: int) -> list[dict[str, Any]]:
        cls.ensure_tables()
        conn = db.get_db_connection()
        cur = conn.cursor()
        cur.execute(
            "SELECT id, name, prefix, scopes, created_at, last_used_at, revoked FROM api_keys WHERE user_id = ? ORDER BY id DESC",
            (user_id,),
        )
        rows = []
        for row in cur.fetchall():
            data = dict(row)
            data["scopes"] = [s for s in (data.get("scopes") or "").split(",") if s]
            rows.append(data)
        conn.close()
        return rows

    @classmethod
    def revoke(cls, key_id: int, user_id: int) -> bool:
        conn = db.get_db_connection()
        cur = conn.cursor()
        cur.execute("UPDATE api_keys SET revoked = 1 WHERE id = ? AND user_id = ?", (key_id, user_id))
        changed = cur.rowcount > 0
        conn.commit()
        conn.close()
        return changed

    @classmethod
    def list_keys(cls) -> list[dict[str, Any]]:
        cls.ensure_tables()
        conn = db.get_db_connection()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, name, prefix, scopes, created_at, last_used_at, revoked, user_id
            FROM api_keys
            ORDER BY id DESC
            """
        )
        rows = []
        for row in cur.fetchall():
            data = dict(row)
            data["scopes"] = [s for s in (data.get("scopes") or "").split(",") if s]
            rows.append(data)
        conn.close()
        return rows

    @classmethod
    def delete(cls, key_id: int) -> bool:
        cls.ensure_tables()
        conn = db.get_db_connection()
        cur = conn.cursor()
        cur.execute("DELETE FROM api_keys WHERE id = ?", (int(key_id),))
        changed = cur.rowcount > 0
        conn.commit()
        conn.close()
        return changed
