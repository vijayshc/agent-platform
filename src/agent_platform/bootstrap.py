"""Framework startup for the agent platform.

This module is the whole boot contract: plugin registration, run-store hygiene,
and a check that the database has been initialized by
``scripts/setup_platform.py``. It defines no schema, creates no roles, no MCP
servers, and no agents; sample content is owned by ``scripts/feed_samples.py``.

(The stores it touches -- run reconciliation, published definitions -- lazily
ensure their own tables, which is why this runs only after the schema check.)

Consequence: this package never imports sample or setup code, so deleting
``platform_samples/`` (or never running the feed) cannot stop the platform from
booting against an initialized database.
"""

from __future__ import annotations

import logging
import os
import sqlite3

from src.agent_platform.paths import APP_ROOT

logger = logging.getLogger("text2sql.agent_platform")

SETUP_HINT = (
    "Database is not initialized. Run `python scripts/setup_platform.py` "
    "(one-time schema + roles + admin user), then restart."
)

#: Tables the platform cannot serve without. Everything else is optional
#: functionality whose own store creates what it needs on first use.
CORE_TABLES: tuple[str, ...] = (
    "users",
    "roles",
    "permissions",
    "user_roles",
    "role_permissions",
    "agent_definitions",
    "agent_runs",
    "agent_conversations",
    "agent_messages",
    "api_keys",
    "mcp_servers",
    "maf_skills",
    "llm_connections",
)


def existing_tables() -> set[str]:
    """Table names in the configured database (empty when it does not exist)."""
    from src.utils.database import get_db_connection

    conn = get_db_connection()
    try:
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        return {str(row[0]) for row in rows}
    except sqlite3.DatabaseError:
        return set()
    finally:
        conn.close()


def missing_tables() -> list[str]:
    """Core tables absent from the database, sorted."""
    return sorted(set(CORE_TABLES) - existing_tables())


def assert_database_ready() -> None:
    """Fail loudly (and actionably) when the setup script has not been run."""
    missing = missing_tables()
    if not missing:
        logger.info("Database schema present (%d core tables)", len(CORE_TABLES))
        return
    logger.error(
        "Missing %d core table(s): %s. %s%s",
        len(missing),
        ", ".join(missing),
        SETUP_HINT,
        f" (app root: {APP_ROOT})",
    )
    raise RuntimeError(f"{SETUP_HINT} Missing tables: {', '.join(missing)}")


def initialize_runtime() -> None:
    """Boot the framework. Requires an initialized database; seeds nothing."""
    from src.agent_platform.plugins import register_builtin_plugins

    register_builtin_plugins()
    assert_database_ready()
    ensure_module_schema()
    migrate_resource_grants()
    _reconcile_runs()
    unpublish_scripted_definitions()


#: Tenancy columns every module store's ensure entry point must guarantee, per
#: table.  Only tables that exist are checked: optional modules stay optional.
MODULE_TENANCY_COLUMNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("mcp_servers", ("created_by",)),
    ("llm_connections", ("created_by",)),
    ("maf_skills", ("created_by",)),
    ("skills", ("owner_id",)),
    ("knowledge_documents", ("owner_id", "access_id")),
    ("hosted_apps", ("created_by",)),
    ("agent_definitions", ("created_by",)),
)


def ensure_module_schema() -> None:
    """Run each module store's idempotent schema/migration entry point.

    Module authors added per-asset ownership columns to independent stores
    (MCP servers, LLM connections, both skill tables, hosted apps, knowledge
    documents, agent definitions) and migrated the live database by hand. Each
    store already owns an idempotent ensure/migrate entry point; this is the one
    boot choke point that runs them all, so a new column can never reach a live
    app as ``no such column`` again.

    Runs after ``assert_database_ready()`` and before any request handling.
    Idempotent (safe on every startup) and fail-fast: a migration that raises,
    or a tenancy column still missing afterwards, aborts the boot.
    """
    from src.agent_platform.catalog.skills_store import MafSkillStore
    from src.agent_platform.catalog.store import DefinitionStore
    from src.models.hosted_app import HostedApp
    from src.models.llm_connection import LLMConnection
    from src.models.mcp_server import MCPServer
    from src.models.skill import Skill
    from src.utils import knowledge_access

    migrations: tuple[tuple[str, object], ...] = (
        ("mcp_servers", MCPServer.create_table),
        ("llm_connections", LLMConnection.create_table),
        ("maf_skills", MafSkillStore.ensure_tables),
        ("skills", Skill.create_table),
        ("knowledge_documents", knowledge_access.ensure_schema),
        ("hosted_apps", HostedApp.create_table),
        ("agent_definitions", DefinitionStore.ensure_tables),
    )
    for label, migrate in migrations:
        try:
            migrate()
        except Exception as exc:
            logger.exception("Module schema migration failed for %s", label)
            raise RuntimeError(f"Module schema migration failed for {label}: {exc}") from exc

    missing = missing_tenancy_columns()
    if missing:
        raise RuntimeError(
            "Tenancy columns still missing after module schema migrations: "
            + ", ".join(f"{table}.{column}" for table, column in missing)
        )


def missing_tenancy_columns() -> list[tuple[str, str]]:
    """``(table, column)`` tenancy columns absent from tables that exist."""
    from src.utils.database import get_db_connection

    conn = get_db_connection()
    try:
        existing = {
            str(row[0])
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        missing: list[tuple[str, str]] = []
        for table, columns in MODULE_TENANCY_COLUMNS:
            if table not in existing:
                continue
            present = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
            missing.extend((table, column) for column in columns if column not in present)
        return missing
    finally:
        conn.close()


def migrate_resource_grants() -> dict:
    """Create the generic grant table and fold legacy per-asset grants into it.

    Idempotent and safe on every startup: existing agent/hosted-app grants keep
    working because their rows are copied once into ``resource_role_access``.
    """
    from src.auth.resource_access import ensure_tables, migrate_legacy_grants

    ensure_tables()
    summary = migrate_legacy_grants()
    if any(summary.values()):
        logger.info("Migrated legacy resource grants: %s", summary)
    return summary


def _reconcile_runs() -> None:
    """No run survives a restart: anything non-terminal is orphaned."""
    from src.agent_platform.execution.run_store import RunStore

    try:
        orphaned = RunStore.reconcile_orphans()
        if orphaned:
            logger.info("Marked %s interrupted run(s) as failed", orphaned)
    except Exception:
        logger.exception("Run reconciliation failed")


def unpublish_scripted_definitions() -> int:
    """Keep leftover scripted test agents unpublished outside the E2E server.

    A definition using the scripted chat client is test scaffolding; if one is
    left published in a real deployment it answers every question with canned
    text, so the platform unpublishes it at boot. The definition itself is
    created by ``scripts/feed_samples.py --scripted``. Returns how many were
    unpublished (0 when the E2E server is running, or on error).
    """
    if os.environ.get("AGENT_PLATFORM_E2E") == "1":
        return 0
    from src.agent_platform.catalog.store import DefinitionStore
    from src.agent_platform.catalog.validate import definition_uses_scripted_client

    count = 0
    try:
        for row in DefinitionStore.list_published():
            if not definition_uses_scripted_client(row):
                continue
            DefinitionStore.set_published(int(row["id"]), False)
            count += 1
            logger.info("Unpublished leftover scripted definition %s", row.get("slug"))
    except Exception:
        logger.exception("Unpublishing leftover scripted agents failed")
    return count
