"""Conversations, messages, attachments, and serialized MAF sessions."""

from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import datetime, timezone
from typing import Any

from src.agent_platform import db
from src.agent_platform.paths import attachments_root


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(sep=" ", timespec="seconds")


class ConversationStore:
    # One-time DDL guard. Creating tables / ALTER on every store call was a
    # lock-heavy cost in the hot path; after the first successful run per
    # process we short-circuit.
    _tables_ready = False
    _ddl_lock = threading.Lock()

    @staticmethod
    def _reset_schema_guard() -> None:
        """Force DDL to run again (test isolation swaps the DB file)."""
        with ConversationStore._ddl_lock:
            ConversationStore._tables_ready = False

    @staticmethod
    def ensure_tables() -> None:
        if ConversationStore._tables_ready:
            return
        with ConversationStore._ddl_lock:
            if ConversationStore._tables_ready:
                return
            conn = db.get_db_connection()
            try:
                cur = conn.cursor()
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS agent_conversations (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        public_id TEXT NOT NULL UNIQUE,
                        title TEXT,
                        agent_slug TEXT,
                        user_id INTEGER,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                    """
                )
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS agent_messages (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        conversation_id INTEGER NOT NULL,
                        role TEXT NOT NULL,
                        content TEXT,
                        run_id INTEGER,
                        meta_json TEXT,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY(conversation_id) REFERENCES agent_conversations(id)
                    )
                    """
                )
                try:
                    cur.execute("ALTER TABLE agent_messages ADD COLUMN meta_json TEXT")
                except Exception:
                    pass
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS agent_attachments (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        public_id TEXT NOT NULL UNIQUE,
                        conversation_id INTEGER,
                        user_id INTEGER,
                        filename TEXT,
                        path TEXT NOT NULL,
                        content_type TEXT,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                    """
                )
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS agent_sessions (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        conversation_id INTEGER NOT NULL UNIQUE,
                        session_json TEXT NOT NULL,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY(conversation_id) REFERENCES agent_conversations(id)
                    )
                    """
                )
                conn.commit()
                ConversationStore._tables_ready = True
            finally:
                conn.close()

    @classmethod
    def create(cls, *, user_id: int | None, title: str | None = None, agent_slug: str | None = None) -> dict[str, Any]:
        cls.ensure_tables()
        public_id = str(uuid.uuid4())
        conn = db.get_db_connection()
        cur = conn.cursor()
        now = _now()
        cur.execute(
            """
            INSERT INTO agent_conversations (public_id, title, agent_slug, user_id, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (public_id, title or "New chat", agent_slug, user_id, now, now),
        )
        cid = cur.lastrowid
        conn.commit()
        conn.close()
        return cls.get(cid)  # type: ignore[return-value]

    @classmethod
    def get(cls, conversation_id: int | str) -> dict[str, Any] | None:
        cls.ensure_tables()
        conn = db.get_db_connection()
        cur = conn.cursor()
        if isinstance(conversation_id, str) and not conversation_id.isdigit():
            cur.execute("SELECT * FROM agent_conversations WHERE public_id = ?", (conversation_id,))
        else:
            cur.execute("SELECT * FROM agent_conversations WHERE id = ?", (int(conversation_id),))
        row = cur.fetchone()
        conn.close()
        return dict(row) if row else None

    @classmethod
    def list_for_user(
        cls,
        user_id: int,
        *,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        cls.ensure_tables()
        conn = db.get_db_connection()
        cur = conn.cursor()
        if limit is not None:
            cur.execute(
                "SELECT * FROM agent_conversations WHERE user_id = ? ORDER BY updated_at DESC LIMIT ? OFFSET ?",
                (user_id, limit, max(0, offset)),
            )
        else:
            cur.execute(
                "SELECT * FROM agent_conversations WHERE user_id = ? ORDER BY updated_at DESC",
                (user_id,),
            )
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()
        return rows

    @classmethod
    def count_for_user(cls, user_id: int) -> int:
        cls.ensure_tables()
        conn = db.get_db_connection()
        cur = conn.cursor()
        cur.execute(
            "SELECT COUNT(*) FROM agent_conversations WHERE user_id = ?",
            (user_id,),
        )
        row = cur.fetchone()
        conn.close()
        return int(row[0])

    @classmethod
    def search(
        cls,
        user_id: int,
        query: str,
        *,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        cls.ensure_tables()
        q = (query or "").strip()
        if not q:
            return cls.list_for_user(user_id, limit=limit, offset=offset)
        needle = f"%{q.lower()}%"
        conn = db.get_db_connection()
        cur = conn.cursor()
        if limit is not None:
            cur.execute(
                """
                SELECT DISTINCT c.*
                FROM agent_conversations c
                LEFT JOIN agent_messages m ON m.conversation_id = c.id
                WHERE c.user_id = ?
                  AND (
                    LOWER(COALESCE(c.title, '')) LIKE ?
                    OR LOWER(COALESCE(c.agent_slug, '')) LIKE ?
                    OR LOWER(COALESCE(m.content, '')) LIKE ?
                  )
                ORDER BY c.updated_at DESC
                LIMIT ? OFFSET ?
                """,
                (user_id, needle, needle, needle, limit, max(0, offset)),
            )
        else:
            cur.execute(
                """
                SELECT DISTINCT c.*
                FROM agent_conversations c
                LEFT JOIN agent_messages m ON m.conversation_id = c.id
                WHERE c.user_id = ?
                  AND (
                    LOWER(COALESCE(c.title, '')) LIKE ?
                    OR LOWER(COALESCE(c.agent_slug, '')) LIKE ?
                    OR LOWER(COALESCE(m.content, '')) LIKE ?
                  )
                ORDER BY c.updated_at DESC
                """,
                (user_id, needle, needle, needle),
            )
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()
        return rows

    @classmethod
    def count_search(cls, user_id: int, query: str) -> int:
        cls.ensure_tables()
        q = (query or "").strip()
        if not q:
            return cls.count_for_user(user_id)
        needle = f"%{q.lower()}%"
        conn = db.get_db_connection()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT COUNT(DISTINCT c.id)
            FROM agent_conversations c
            LEFT JOIN agent_messages m ON m.conversation_id = c.id
            WHERE c.user_id = ?
              AND (
                LOWER(COALESCE(c.title, '')) LIKE ?
                OR LOWER(COALESCE(c.agent_slug, '')) LIKE ?
                OR LOWER(COALESCE(m.content, '')) LIKE ?
              )
            """,
            (user_id, needle, needle, needle),
        )
        row = cur.fetchone()
        conn.close()
        return int(row[0])

    @classmethod
    def set_title(cls, conversation_id: int, title: str) -> None:
        cls.ensure_tables()
        conn = db.get_db_connection()
        cur = conn.cursor()
        cur.execute(
            "UPDATE agent_conversations SET title = ?, updated_at = ? WHERE id = ?",
            (title, _now(), conversation_id),
        )
        conn.commit()
        conn.close()

    @classmethod
    def set_agent_slug(cls, conversation_id: int, agent_slug: str | None) -> None:
        cls.ensure_tables()
        conn = db.get_db_connection()
        cur = conn.cursor()
        cur.execute(
            "UPDATE agent_conversations SET agent_slug = ?, updated_at = ? WHERE id = ?",
            (agent_slug, _now(), conversation_id),
        )
        conn.commit()
        conn.close()

    @classmethod
    def add_message(
        cls,
        conversation_id: int,
        role: str,
        content: str,
        run_id: int | None = None,
        meta: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        cls.ensure_tables()
        conn = db.get_db_connection()
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO agent_messages (conversation_id, role, content, run_id, meta_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (conversation_id, role, content, run_id, json.dumps(meta) if meta else None, _now()),
        )
        mid = cur.lastrowid
        cur.execute(
            "UPDATE agent_conversations SET updated_at = ? WHERE id = ?",
            (_now(), conversation_id),
        )
        conn.commit()
        conn.close()
        return {
            "id": mid,
            "conversation_id": conversation_id,
            "role": role,
            "content": content,
            "run_id": run_id,
            "meta": meta,
        }

    @classmethod
    def upsert_assistant_for_run(
        cls,
        conversation_id: int,
        run_id: int,
        content: str,
        meta: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        cls.ensure_tables()
        conn = db.get_db_connection()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id FROM agent_messages
            WHERE conversation_id = ? AND run_id = ? AND role = 'assistant'
            ORDER BY id DESC LIMIT 1
            """,
            (conversation_id, run_id),
        )
        row = cur.fetchone()
        payload = json.dumps(meta) if meta else None
        if row:
            mid = row[0] if not isinstance(row, dict) else row["id"]
            cur.execute(
                "UPDATE agent_messages SET content = ?, meta_json = ? WHERE id = ?",
                (content, payload, mid),
            )
        else:
            cur.execute(
                """
                INSERT INTO agent_messages (conversation_id, role, content, run_id, meta_json, created_at)
                VALUES (?, 'assistant', ?, ?, ?, ?)
                """,
                (conversation_id, content, run_id, payload, _now()),
            )
            mid = cur.lastrowid
        cur.execute(
            "UPDATE agent_conversations SET updated_at = ? WHERE id = ?",
            (_now(), conversation_id),
        )
        conn.commit()
        conn.close()
        return {
            "id": mid,
            "conversation_id": conversation_id,
            "role": "assistant",
            "content": content,
            "run_id": run_id,
            "meta": meta,
        }

    @classmethod
    def list_messages(cls, conversation_id: int) -> list[dict[str, Any]]:
        cls.ensure_tables()
        conn = db.get_db_connection()
        cur = conn.cursor()
        cur.execute(
            "SELECT * FROM agent_messages WHERE conversation_id = ? ORDER BY id ASC",
            (conversation_id,),
        )
        rows = []
        for row in cur.fetchall():
            data = dict(row)
            raw = data.pop("meta_json", None)
            if raw:
                try:
                    data["meta"] = json.loads(raw)
                except Exception:
                    data["meta"] = None
            else:
                data["meta"] = None
            rows.append(data)
        conn.close()
        return rows

    @classmethod
    def save_session(cls, conversation_id: int, session_dict: dict[str, Any]) -> None:
        cls.ensure_tables()
        conn = db.get_db_connection()
        cur = conn.cursor()
        payload = json.dumps(session_dict)
        cur.execute("SELECT id FROM agent_sessions WHERE conversation_id = ?", (conversation_id,))
        row = cur.fetchone()
        if row:
            cur.execute(
                "UPDATE agent_sessions SET session_json = ?, updated_at = ? WHERE conversation_id = ?",
                (payload, _now(), conversation_id),
            )
        else:
            cur.execute(
                "INSERT INTO agent_sessions (conversation_id, session_json, updated_at) VALUES (?, ?, ?)",
                (conversation_id, payload, _now()),
            )
        conn.commit()
        conn.close()

    @classmethod
    def load_session(cls, conversation_id: int) -> dict[str, Any] | None:
        cls.ensure_tables()
        conn = db.get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT session_json FROM agent_sessions WHERE conversation_id = ?", (conversation_id,))
        row = cur.fetchone()
        conn.close()
        if not row:
            return None
        raw = row[0] if not isinstance(row, dict) else row["session_json"]
        try:
            return json.loads(raw)
        except Exception:
            return None

    @classmethod
    def save_attachment(
        cls,
        *,
        filename: str,
        data: bytes,
        user_id: int | None,
        conversation_id: int | None = None,
        content_type: str | None = None,
    ) -> dict[str, Any]:
        cls.ensure_tables()
        public_id = str(uuid.uuid4())
        dest_dir = attachments_root() / public_id
        dest_dir.mkdir(parents=True, exist_ok=True)
        safe_name = os.path.basename(filename) or "upload.bin"
        dest = dest_dir / safe_name
        dest.write_bytes(data)
        conn = db.get_db_connection()
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO agent_attachments (public_id, conversation_id, user_id, filename, path, content_type, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (public_id, conversation_id, user_id, safe_name, str(dest), content_type, _now()),
        )
        aid = cur.lastrowid
        conn.commit()
        conn.close()
        return {
            "id": aid,
            "public_id": public_id,
            "filename": safe_name,
            "path": str(dest),
            "content_type": content_type,
        }

    @classmethod
    def delete(cls, conversation_id: int) -> bool:
        cls.ensure_tables()
        conn = db.get_db_connection()
        cur = conn.cursor()
        cid = int(conversation_id)
        cur.execute("DELETE FROM agent_messages WHERE conversation_id = ?", (cid,))
        cur.execute("DELETE FROM agent_sessions WHERE conversation_id = ?", (cid,))
        cur.execute("DELETE FROM agent_attachments WHERE conversation_id = ?", (cid,))
        cur.execute("DELETE FROM agent_conversations WHERE id = ?", (cid,))
        changed = cur.rowcount > 0
        conn.commit()
        conn.close()
        return changed

    @classmethod
    def get_attachment(cls, public_id: str) -> dict[str, Any] | None:
        cls.ensure_tables()
        conn = db.get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT * FROM agent_attachments WHERE public_id = ?", (public_id,))
        row = cur.fetchone()
        conn.close()
        return dict(row) if row else None
