from datetime import datetime
from enum import Enum
import json

from src.auth import resource_access
from src.models.secrets import mask_value, wrap_credentials
from src.utils.database import get_db_connection

#: Resource type registered with the generic role-grant store.
MCP_SERVER_RESOURCE_TYPE = "mcp_server"


class MCPServerType(Enum):
    STDIO = "stdio"
    HTTP = "http"


# Explicit projection: the model never depends on SELECT * ordering or on
# columns a previous schema revision left behind.
_COLUMNS = "id, name, description, server_type, config, created_at, updated_at, created_by"


class MCPServer:
    """A registered MCP server.

    A row is configuration only: there is no lifecycle to manage. Every
    consumer (a run, a studio inspection, an admin tool check) connects on
    demand through ``plugins.mcp.connection.build_connection`` and closes the
    session when it is done, so no long-lived process or stale status exists.
    """

    def __init__(self, id=None, name=None, description=None, server_type=None,
                 config=None, created_at=None, updated_at=None, created_by=None):
        # Convert string timestamps from DB to datetime
        if isinstance(created_at, str):
            created_at = datetime.fromisoformat(created_at)
        if isinstance(updated_at, str):
            updated_at = datetime.fromisoformat(updated_at)

        self.id = id
        self.name = name
        self.description = description
        self.server_type = server_type
        # Every string in config.env / config.headers is a SecretString (those
        # dicts are credential bags). str()/sqlite/JSON still carry the raw value
        # (needed by the connection builder and DB persistence); API responses
        # must use config_masked() instead of the raw dict.
        self.config = config
        self.created_at = created_at or datetime.now()
        self.updated_at = updated_at or datetime.now()
        # Owning user id; ``None`` marks a legacy/grandfathered row visible to
        # every authenticated user (see ``src.auth.resource_access``).
        self.created_by = created_by

    @property
    def config(self) -> dict:
        """Server config; every string in env/headers is SecretString."""
        return self._config

    @config.setter
    def config(self, value: dict | str | None) -> None:
        if isinstance(value, str):
            value = json.loads(value) if value else {}
        config = value if isinstance(value, dict) else {}
        # One canonical HTTP key. ``base_url``/``endpoint`` were the legacy
        # manager's spelling and ``url`` is what the run-time binding reads, so
        # a server holding only the legacy key would silently connect nowhere.
        url = config.get("url") or config.get("base_url") or config.get("endpoint")
        if url:
            config = {
                key: val for key, val in config.items() if key not in {"base_url", "endpoint"}
            }
            config["url"] = url
        self._config = wrap_credentials(config)

    @classmethod
    def create_table(cls):
        """Create the mcp_servers table and reconcile older schema revisions."""
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute('''
        CREATE TABLE IF NOT EXISTS mcp_servers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            description TEXT,
            server_type TEXT NOT NULL,
            config TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            created_by INTEGER
        )
        ''')
        conn.commit()
        conn.close()

        cls._migrate()

    @classmethod
    def _migrate(cls) -> None:
        """Add the tenancy column and reconcile older schema revisions."""
        conn = get_db_connection()
        try:
            cursor = conn.cursor()
            columns = {row[1] for row in cursor.execute("PRAGMA table_info(mcp_servers)")}
            if "created_by" not in columns:
                # Existing rows keep NULL -> grandfathered visible to every
                # authenticated user; only new rows are stamped with an owner.
                cursor.execute("ALTER TABLE mcp_servers ADD COLUMN created_by INTEGER")
                conn.commit()
            if "status" in columns:
                # Servers used to be started/stopped and carried that flag.
                # Nothing reads it now: reachability is observed per connection.
                cursor.execute("ALTER TABLE mcp_servers DROP COLUMN status")
                conn.commit()
            cls._normalize_http_keys(cursor)
            conn.commit()
        finally:
            conn.close()

    @staticmethod
    def _normalize_http_keys(cursor) -> None:
        """Collapse legacy ``base_url``/``endpoint`` onto the canonical ``url``."""
        rows = cursor.execute(
            "SELECT id, config FROM mcp_servers WHERE server_type = ?",
            (MCPServerType.HTTP.value,),
        ).fetchall()
        for row in rows:
            try:
                config = json.loads(row[1]) if row[1] else {}
            except (TypeError, ValueError):
                continue
            if not isinstance(config, dict):
                continue
            url = config.get("url") or config.get("base_url") or config.get("endpoint")
            if not url:
                continue
            if config.get("url") == url and "base_url" not in config and "endpoint" not in config:
                continue
            config["url"] = url
            config.pop("base_url", None)
            config.pop("endpoint", None)
            cursor.execute(
                "UPDATE mcp_servers SET config = ? WHERE id = ?",
                (json.dumps(config), row[0]),
            )

    @classmethod
    def _fetch_rows(cls) -> list[dict]:
        """Every server as a plain dict, ordered by name (access-filterable)."""
        conn = get_db_connection()
        conn.row_factory = cls._dict_factory
        try:
            cursor = conn.cursor()
            cursor.execute(f'SELECT {_COLUMNS} FROM mcp_servers ORDER BY name')
            return [dict(row) for row in cursor.fetchall()]
        finally:
            conn.close()

    @classmethod
    def get_all(cls):
        """Get all MCP servers from the database (unfiltered registry view)."""
        return [cls(**row) for row in cls._fetch_rows()]

    @classmethod
    def get_visible(cls, user_id):
        """Servers ``user_id`` may see.

        Administrator -> every server; owner -> own servers; otherwise only
        servers whose granted roles intersect the user's roles (plus
        owner-less legacy rows). No user -> ``[]`` (deny).
        """
        if user_id is None:
            return []
        rows = resource_access.filter_visible(
            MCP_SERVER_RESOURCE_TYPE, cls._fetch_rows(), user_id
        )
        return [cls(**row) for row in rows]

    @classmethod
    def get_by_id(cls, server_id):
        """Get a server by ID."""
        conn = get_db_connection()
        conn.row_factory = cls._dict_factory
        cursor = conn.cursor()

        cursor.execute(f'SELECT {_COLUMNS} FROM mcp_servers WHERE id = ?', (server_id,))
        server_data = cursor.fetchone()

        conn.close()
        return cls(**server_data) if server_data else None

    @classmethod
    def get_by_name(cls, name):
        """Get a server by name."""
        conn = get_db_connection()
        conn.row_factory = cls._dict_factory
        cursor = conn.cursor()

        cursor.execute(f'SELECT {_COLUMNS} FROM mcp_servers WHERE name = ?', (name,))
        server_data = cursor.fetchone()

        conn.close()
        return cls(**server_data) if server_data else None

    def save(self):
        """Save this MCP server to the database with retry logic for lock errors."""
        max_retries = 3
        base_delay = 0.1

        for attempt in range(max_retries):
            try:
                conn = get_db_connection()
                try:
                    # Begin immediate transaction to acquire lock quickly
                    conn.execute("BEGIN IMMEDIATE")

                    cursor = conn.cursor()

                    if self.id:
                        # Update existing record. Ownership is immutable here:
                        # an edit must never let a non-owner claim the server.
                        self.updated_at = datetime.now()
                        cursor.execute('''
                        UPDATE mcp_servers
                        SET name = ?, description = ?, server_type = ?, config = ?,
                            updated_at = ?
                        WHERE id = ?
                        ''', (
                            self.name,
                            self.description,
                            self.server_type,
                            json.dumps(self.config),
                            self.updated_at,
                            self.id
                        ))
                    else:
                        # Insert new record, stamped with the creating user.
                        cursor.execute('''
                        INSERT INTO mcp_servers
                            (name, description, server_type, config, created_by)
                        VALUES (?, ?, ?, ?, ?)
                        ''', (
                            self.name,
                            self.description,
                            self.server_type,
                            json.dumps(self.config),
                            self.created_by
                        ))
                        self.id = cursor.lastrowid

                    conn.commit()
                    return self

                except Exception as e:
                    conn.rollback()
                    raise
                finally:
                    conn.close()

            except Exception as e:
                if "database is locked" in str(e).lower() and attempt < max_retries - 1:
                    # Wait with exponential backoff before retrying
                    import time
                    delay = base_delay * (2 ** attempt)
                    time.sleep(delay)
                    continue
                else:
                    # Re-raise if not a lock error or max retries exceeded
                    raise

    def delete(self):
        """Delete this MCP server from the database with retry logic for lock errors."""
        if not self.id:
            return False

        max_retries = 3
        base_delay = 0.1

        for attempt in range(max_retries):
            try:
                conn = get_db_connection()
                try:
                    # Begin immediate transaction to acquire lock quickly
                    conn.execute("BEGIN IMMEDIATE")

                    cursor = conn.cursor()
                    cursor.execute('DELETE FROM mcp_servers WHERE id = ?', (self.id,))

                    conn.commit()
                    return True

                except Exception as e:
                    conn.rollback()
                    raise
                finally:
                    conn.close()

            except Exception as e:
                if "database is locked" in str(e).lower() and attempt < max_retries - 1:
                    # Wait with exponential backoff before retrying
                    import time
                    delay = base_delay * (2 ** attempt)
                    time.sleep(delay)
                    continue
                else:
                    # Re-raise if not a lock error or max retries exceeded
                    raise

        return False

    def config_masked(self):
        """Config for API responses: credential values replaced with ``********``.

        Every string in ``config.env``/``config.headers`` is stored (and
        returned by ``self.config``) as SecretString so repr/logging stays
        masked; JSON serialization would reveal them, so API boundaries must
        use this. ``command``/``args``/``url`` stay plaintext.
        """
        return mask_value(self.config)

    @staticmethod
    def _dict_factory(cursor, row):
        """Convert row to dictionary for SQLite row factory."""
        d = {}
        for idx, col in enumerate(cursor.description):
            d[col[0]] = row[idx]
        return d


# --- frozen tenancy helpers (the Studio agent calls these) -----------------

def can_access_server(server_id: int, user_id: int | None) -> bool:
    """Whether ``user_id`` may *use* (connect to / list tools of) the server.

    ``None`` user always denies; a missing server denies. Administrators and
    the owner always pass; an owner-less legacy row is grandfathered; otherwise
    the user's roles must intersect the server's granted roles.
    """
    server = MCPServer.get_by_id(server_id)
    if server is None:
        return False
    return resource_access.can_access(
        MCP_SERVER_RESOURCE_TYPE, server.id, server.created_by, user_id
    )


def visible_server_ids(user_id: int | None) -> set[int] | None:
    """Server ids ``user_id`` may see; ``None`` means *all* (administrator).

    ``user_id is None`` returns the empty set: no identity, no visibility.
    """
    if user_id is None:
        return set()
    if resource_access.is_admin(user_id):
        return None
    return {int(server.id) for server in MCPServer.get_visible(user_id)}


def _mcp_server_owner(server_id: int) -> int | None:
    server = MCPServer.get_by_id(int(server_id))
    return server.created_by if server is not None else None


def _mcp_server_exists(server_id: int) -> bool:
    return MCPServer.get_by_id(int(server_id)) is not None


# Register owner/existence resolution for the generic access API. Idempotent;
# runs at import time so ``resource_access.owner_of("mcp_server", id)`` works as
# soon as this model is loaded.
resource_access.register_resource(
    MCP_SERVER_RESOURCE_TYPE, _mcp_server_owner, _mcp_server_exists
)
