"""ADMIN-CONFIGURABLE LLM CONNECTION MODEL

This model stores one or more OpenAI-compatible provider connections that the
operator manages through the admin LLM Manager page. It uses the same raw
SQLite pattern as :mod:`src.models.mcp_server` so it works without extra
SQLAlchemy metadata wiring.
"""

from __future__ import annotations

import json
from datetime import datetime

from src.auth import resource_access
from src.models.secrets import as_secret, mask_value, wrap_headers
from src.utils.database import get_db_connection

#: Canonical resource type for the generic per-asset role-grant API.
LLM_CONNECTION_RESOURCE_TYPE = "llm_connection"

# Explicit column list: ``SELECT *`` would break on databases that still carry
# columns from an older release (temperature / max_tokens / top_k / extra_options).
_COLUMNS = (
    "id, name, base_url, api_key, model_name, system_instruction, extra_body, "
    "http_headers, verify_ssl, is_default, enabled, created_by, created_at, updated_at"
)

# Columns an older release wrote per-connection request arguments into. They are
# folded into extra_body once, so an upgraded install keeps its model settings.
_LEGACY_ARGUMENT_COLUMNS = ("extra_options", "top_k", "temperature", "max_tokens")


class LLMConnection:
    def __init__(
        self,
        id=None,
        name=None,
        base_url=None,
        api_key=None,
        model_name=None,
        system_instruction=None,
        extra_body=None,
        http_headers=None,
        verify_ssl=1,
        is_default=0,
        enabled=1,
        created_by=None,
        created_at=None,
        updated_at=None,
    ):
        if isinstance(created_at, str):
            created_at = datetime.fromisoformat(created_at)
        if isinstance(updated_at, str):
            updated_at = datetime.fromisoformat(updated_at)
        self.id = id
        self.name = name
        self.base_url = base_url
        # api_key lives as SecretString so repr()/logging redacts it. str(),
        # sqlite, and JSON still carry the raw value (build_client transparently
        # passes it to the OpenAI SDK which unwraps SecretString); API responses
        # must use api_key_masked_get() instead of the raw attribute.
        self.api_key = api_key
        self.model_name = model_name
        self.system_instruction = system_instruction
        # Every provider argument beyond base_url/api_key/model: the operator
        # writes them as one free-form JSON object (temperature, max_tokens,
        # top_p, top_k, chat_template_kwargs, ...). Forwarded verbatim as the
        # request's extra_body, so nesting and key count are unconstrained.
        self.extra_body = extra_body if isinstance(extra_body, dict) else self._parse_json(extra_body)
        self.http_headers = http_headers
        # TLS certificate verification for the provider connection. Operator
        # controlled: only an explicit False disables verification.
        self.verify_ssl = self._flag(verify_ssl, default=1)
        self.is_default = 1 if is_default else 0
        self.enabled = 1 if enabled else 0
        # Owning user. ``None`` marks a deployment-level (config/env-seeded)
        # connection: it is shared with every authenticated user, matching the
        # generic resource-access grandfather rule for owner-less rows.
        self.created_by = created_by
        self.created_at = created_at or datetime.now()
        self.updated_at = updated_at or datetime.now()

    @property
    def api_key(self) -> Any:
        return self._api_key

    @api_key.setter
    def api_key(self, value: Any) -> None:
        self._api_key = as_secret(value)

    @property
    def http_headers(self) -> dict:
        return self._http_headers

    @http_headers.setter
    def http_headers(self, value: Any) -> None:
        raw = value if isinstance(value, dict) else self._parse_json(value)
        self._http_headers = wrap_headers(raw)

    @staticmethod
    def _parse_json(value):
        if value is None:
            return {}
        if isinstance(value, (dict, list)):
            return value
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except (ValueError, TypeError):
            return {}

    @staticmethod
    def _flag(value, default: int = 0) -> int:
        """Coerce a payload/DB flag (bool, int, or "false"/"0"/"off") to 0/1."""
        if value is None:
            return default
        if isinstance(value, str):
            return 0 if value.strip().lower() in {"", "0", "false", "no", "off"} else 1
        return 1 if value else 0

    @classmethod
    def create_table(cls):
        """Create the llm_connections table if it doesn't exist."""
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS llm_connections (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                base_url TEXT,
                api_key TEXT,
                model_name TEXT,
                system_instruction TEXT,
                extra_body TEXT,
                http_headers TEXT,
                verify_ssl INTEGER DEFAULT 1,
                is_default INTEGER DEFAULT 0,
                enabled INTEGER DEFAULT 1,
                created_by INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        cls._ensure_columns(cursor)
        conn.commit()
        conn.close()

    @classmethod
    def _ensure_columns(cls, cursor) -> None:
        """Upgrade a database written by an older release of this model."""
        columns = {row[1] for row in cursor.execute("PRAGMA table_info(llm_connections)")}
        if "extra_body" not in columns:
            cursor.execute("ALTER TABLE llm_connections ADD COLUMN extra_body TEXT")
        if "verify_ssl" not in columns:
            cursor.execute("ALTER TABLE llm_connections ADD COLUMN verify_ssl INTEGER DEFAULT 1")
        if "created_by" not in columns:
            # Existing rows keep NULL, which the shared access rules treat as
            # deployment-level (visible to every authenticated user).
            cursor.execute("ALTER TABLE llm_connections ADD COLUMN created_by INTEGER")
        cls._fold_legacy_arguments(cursor, columns)

    @classmethod
    def _fold_legacy_arguments(cls, cursor, columns: set[str]) -> None:
        """Move legacy per-connection request arguments into extra_body.

        Older releases stored temperature / max_tokens / top_k / extra_options in
        their own columns. Those settings must keep applying, so they are folded
        into the free-form object — but only for connections whose extra_body is
        still empty, so an operator's dynamic parameters always win. The call is
        idempotent and becomes a no-op once the fold has run.
        """
        legacy = [name for name in _LEGACY_ARGUMENT_COLUMNS if name in columns]
        if not legacy:
            return
        rows = cursor.execute(
            f"SELECT id, extra_body, {', '.join(legacy)} FROM llm_connections "
            "WHERE extra_body IS NULL OR extra_body = '' OR extra_body = '{}'"
        ).fetchall()
        for row in rows:
            extra = cls._parse_json(row[1])
            values = dict(zip(legacy, row[2:]))
            for key, item in cls._parse_json(values.get("extra_options")).items():
                extra.setdefault(key, item)
            for name in ("temperature", "max_tokens", "top_k"):
                value = values.get(name)
                if value in (None, ""):
                    continue
                if name == "top_k" and not value:
                    continue  # 0 meant "unset" in the old fixed Top K field
                extra.setdefault(name, value)
            if not extra:
                continue
            # Clear the legacy columns in the same statement: otherwise a later
            # save that empties extra_body would resurrect the stale values.
            cleared = ", ".join(f"{name} = NULL" for name in legacy)
            cursor.execute(
                f"UPDATE llm_connections SET extra_body = ?, {cleared} WHERE id = ?",
                (json.dumps(extra), row[0]),
            )

    @classmethod
    def get_all(cls):
        """Return all connections ordered by default flag then name."""
        cls.create_table()
        conn = get_db_connection()
        conn.row_factory = cls._dict_factory
        cursor = conn.cursor()
        cursor.execute(f"SELECT {_COLUMNS} FROM llm_connections ORDER BY is_default DESC, name")
        rows = [cls(**row) for row in cursor.fetchall()]
        conn.close()
        return rows

    @classmethod
    def get_by_id(cls, connection_id):
        cls.create_table()
        conn = get_db_connection()
        conn.row_factory = cls._dict_factory
        cursor = conn.cursor()
        cursor.execute(f"SELECT {_COLUMNS} FROM llm_connections WHERE id = ?", (connection_id,))
        row = cursor.fetchone()
        conn.close()
        return cls(**row) if row else None

    @classmethod
    def get_by_name(cls, name):
        cls.create_table()
        conn = get_db_connection()
        conn.row_factory = cls._dict_factory
        cursor = conn.cursor()
        cursor.execute(f"SELECT {_COLUMNS} FROM llm_connections WHERE name = ?", (name,))
        row = cursor.fetchone()
        conn.close()
        return cls(**row) if row else None

    @classmethod
    def get_default(cls):
        cls.create_table()
        conn = get_db_connection()
        conn.row_factory = cls._dict_factory
        cursor = conn.cursor()
        cursor.execute(f"SELECT {_COLUMNS} FROM llm_connections WHERE is_default = 1 LIMIT 1")
        row = cursor.fetchone()
        conn.close()
        return cls(**row) if row else None

    def save(self):
        """Insert or update this connection. Returns self (with id set)."""
        max_retries = 3
        base_delay = 0.1
        for attempt in range(max_retries):
            try:
                conn = get_db_connection()
                try:
                    conn.execute("BEGIN IMMEDIATE")
                    cursor = conn.cursor()
                    self.updated_at = datetime.now()
                    base = (
                        self.name,
                        self.base_url,
                        self.api_key,
                        self.model_name,
                        self.system_instruction,
                        json.dumps(self.extra_body or {}),
                        json.dumps(self.http_headers or {}),
                        self.verify_ssl,
                        self.is_default,
                        self.enabled,
                    )
                    if self.id:
                        # ``created_by`` is deliberately not in the UPDATE: the
                        # owner is stamped once at creation and never rewritten.
                        cursor.execute(
                            """
                            UPDATE llm_connections
                            SET name = ?, base_url = ?, api_key = ?, model_name = ?,
                                system_instruction = ?, extra_body = ?, http_headers = ?,
                                verify_ssl = ?, is_default = ?, enabled = ?, updated_at = ?
                            WHERE id = ?
                            """,
                            (*base, self.updated_at, self.id),
                        )
                    else:
                        cursor.execute(
                            """
                            INSERT INTO llm_connections
                                (name, base_url, api_key, model_name, system_instruction,
                                 extra_body, http_headers, verify_ssl,
                                 is_default, enabled, created_by, updated_at)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (*base, self.created_by, self.updated_at),
                        )
                        self.id = cursor.lastrowid
                    conn.commit()
                    return self
                except Exception:
                    conn.rollback()
                    raise
                finally:
                    conn.close()
            except Exception as exc:
                if "database is locked" in str(exc).lower() and attempt < max_retries - 1:
                    import time

                    time.sleep(base_delay * (2 ** attempt))
                    continue
                raise

    def delete(self):
        if not self.id:
            return False
        max_retries = 3
        base_delay = 0.1
        for attempt in range(max_retries):
            try:
                conn = get_db_connection()
                try:
                    conn.execute("BEGIN IMMEDIATE")
                    cursor = conn.cursor()
                    cursor.execute("DELETE FROM llm_connections WHERE id = ?", (self.id,))
                    conn.commit()
                    return True
                except Exception:
                    conn.rollback()
                    raise
                finally:
                    conn.close()
            except Exception as exc:
                if "database is locked" in str(exc).lower() and attempt < max_retries - 1:
                    import time

                    time.sleep(base_delay * (2 ** attempt))
                    continue
                raise
        return False

    @classmethod
    def set_default(cls, connection_id):
        """Mark one connection default and clear the others."""
        cls.create_table()
        max_retries = 3
        base_delay = 0.1
        for attempt in range(max_retries):
            try:
                conn = get_db_connection()
                try:
                    conn.execute("BEGIN IMMEDIATE")
                    cursor = conn.cursor()
                    cursor.execute("UPDATE llm_connections SET is_default = 0")
                    cursor.execute("UPDATE llm_connections SET is_default = 1, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (connection_id,))
                    conn.commit()
                    return True
                except Exception:
                    conn.rollback()
                    raise
                finally:
                    conn.close()
            except Exception as exc:
                if "database is locked" in str(exc).lower() and attempt < max_retries - 1:
                    import time

                    time.sleep(base_delay * (2 ** attempt))
                    continue
                raise
        return False

    def api_key_masked_get(self) -> str:
        """API key for responses: ``********`` if a key is stored, else ``""``."""
        return "********" if self.api_key else ""

    def config_dict_masked(self) -> dict:
        """Serializable copy of the connection (api key and http_headers masked).

        Used for API responses and for building client kwargs when the key must
        stay out of any persisted artifact.
        """
        data = {
            "id": self.id,
            "name": self.name,
            "base_url": self.base_url,
            "api_key": self.api_key_masked_get(),
            "api_key_masked": bool(self.api_key),
            "model_name": self.model_name,
            "system_instruction": self.system_instruction,
            "extra_body": self.extra_body,
            "http_headers": mask_value(self.http_headers),
            "verify_ssl": bool(self.verify_ssl),
            "is_default": bool(self.is_default),
            "enabled": bool(self.enabled),
            "created_by": self.created_by,
        }
        return data

    @staticmethod
    def _dict_factory(cursor, row):
        return {col[0]: row[idx] for idx, col in enumerate(cursor.description)}


def _llm_connection_owner(connection_id: int) -> int | None:
    """Owner resolver for the generic access API (``None`` when deleted/shared)."""
    conn = LLMConnection.get_by_id(int(connection_id))
    return conn.created_by if conn is not None else None


def _llm_connection_exists(connection_id: int) -> bool:
    """Existence resolver so an owner-less shared row is still manageable by admin."""
    return LLMConnection.get_by_id(int(connection_id)) is not None


# Register owner/existence resolution for the generic access API. Idempotent;
# runs at import time so ``resource_access.owner_of("llm_connection", id)`` works
# as soon as this model is loaded.
resource_access.register_resource(
    LLM_CONNECTION_RESOURCE_TYPE, _llm_connection_owner, _llm_connection_exists
)
