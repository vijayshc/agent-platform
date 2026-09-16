#!/usr/bin/env python3
"""One-time platform setup: schema, roles/permissions, and the first admin user.

Run this against a fresh database *before* starting the app::

    python scripts/setup_platform.py                       # admin / admin123
    python scripts/setup_platform.py --admin-user ops --admin-password secret --admin-email ops@corp
    python scripts/setup_platform.py --check               # report only, change nothing

It is idempotent and never destructive: existing tables, users, roles, and
credentials are left exactly as they are, so re-running it after a code update
(for example when a module adds permissions) is safe and expected.

Sample content -- demo agents, bundled MCP servers, example skills, and demo
data -- is NOT created here. That is ``scripts/feed_samples.py``.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from src.utils.database import DATABASE_URI  # noqa: E402

DEFAULT_ADMIN_USER = "admin"
DEFAULT_ADMIN_PASSWORD = "admin123"
DEFAULT_ADMIN_EMAIL = "admin@example.com"

#: Every table ``create_schema()`` is responsible for. ``--check`` diffs this
#: list, so a database that is missing any of them is reported instead of
#: silently half-working.
EXPECTED_TABLES: tuple[str, ...] = (
    # auth / RBAC
    "users",
    "roles",
    "permissions",
    "user_roles",
    "role_permissions",
    # agent catalog + conversations + runs
    "agent_definitions",
    "agent_definition_role_access",
    "agent_conversations",
    "agent_messages",
    "agent_sessions",
    "agent_attachments",
    "agent_runs",
    "api_keys",
    "maf_skills",
    "skills",
    # evals, agui, teams/workflows, hosting, models
    "eval_definitions",
    "eval_results",
    "agui_threads",
    "agent_teams",
    "agent_workflows",
    "hosted_apps",
    "hosted_app_role_access",
    "llm_connections",
    "mcp_servers",
    # few-shot feedback for the Text-to-SQL UI (owned by src/utils/feedback_manager.py)
    "query_feedback",
)


def create_schema() -> list[str]:
    """Create every platform table. Idempotent; returns what was ensured."""
    from src.agent_platform.agui.thread_store import ensure_tables as ensure_agui
    from src.agent_platform.catalog.skills_store import MafSkillStore
    from src.agent_platform.catalog.store import DefinitionStore
    from src.agent_platform.conversations.store import ConversationStore
    from src.agent_platform.eval.store import EvalStore
    from src.agent_platform.execution.api_keys import ApiKeyStore
    from src.agent_platform.execution.run_store import RunStore
    from src.models.agent_team import AgentTeam
    from src.models.agent_workflow import AgentWorkflow
    from src.models.hosted_app import HostedApp
    from src.models.llm_connection import LLMConnection
    from src.models.mcp_server import MCPServer
    from src.models.skill import Skill
    from src.models.user import Base
    from src.utils.database import create_db_engine
    from src.utils.feedback_schema import ensure_feedback_table

    ensured: list[str] = []

    # Authentication/authorization models (users, roles, permissions, links).
    Base.metadata.create_all(create_db_engine(DATABASE_URI))
    ensured.append("auth (users/roles/permissions)")

    for label, ensure in (
        ("agent definitions", DefinitionStore.ensure_tables),
        ("maf skills", MafSkillStore.ensure_tables),
        ("conversations", ConversationStore.ensure_tables),
        ("runs", RunStore.ensure_tables),
        ("api keys", ApiKeyStore.ensure_tables),
        ("evals", EvalStore.ensure_tables),
        ("agui threads", ensure_agui),
        ("query feedback", lambda: ensure_feedback_table(DATABASE_URI)),
        ("mcp servers", MCPServer.create_table),
        ("skills (legacy)", Skill.create_table),
        ("llm connections", LLMConnection.create_table),
        ("agent teams", AgentTeam.create_table),
        ("agent workflows", AgentWorkflow.create_table),
        ("hosted apps", HostedApp.create_table),
    ):
        ensure()
        ensured.append(label)
    return ensured


def ensure_roles_and_admin(
    *,
    username: str = DEFAULT_ADMIN_USER,
    password: str = DEFAULT_ADMIN_PASSWORD,
    email: str = DEFAULT_ADMIN_EMAIL,
) -> tuple[str, bool]:
    """Create module permissions, the admin/user roles, and the first admin.

    ``initialize_roles_permissions()`` always bootstraps an ``admin`` account
    with the published default password. When the operator asks for a different
    first admin, that scaffold account is removed again -- a second, full-admin
    login on a known default must not survive setup.

    Returns ``(message, created)``; an existing user keeps its stored password.
    """
    from src.models.user import User
    from src.utils.user_manager import UserManager

    manager = UserManager()
    session = manager._get_session()
    target_before = session.query(User).filter(User.username == username).first() is not None
    scaffold_before = (
        session.query(User).filter(User.username == DEFAULT_ADMIN_USER).first() is not None
    )

    if not manager.initialize_roles_permissions():
        raise RuntimeError("initialize_roles_permissions() failed")
    session.expire_all()

    user = session.query(User).filter(User.username == username).first()
    if user is None:
        try:
            user_id = int(manager.create_user(username, email, password))
        except ValueError as exc:  # username/email already taken by another account
            raise RuntimeError(f"cannot create admin '{username}' <{email}>: {exc}") from exc
        created = True
    else:
        user_id = int(user.id)
        created = not target_before

    # initialize_roles_permissions() bootstraps 'admin' with the default
    # password; honour an explicit --admin-password for an account this run made.
    if created and username == DEFAULT_ADMIN_USER and password != DEFAULT_ADMIN_PASSWORD:
        manager.update_user(user_id, username, email, password)

    if not manager.has_role(user_id, "admin"):
        admin_role = next((r for r in manager.get_all_roles() if r.name == "admin"), None)
        if admin_role is None:
            raise RuntimeError("admin role missing after initialize_roles_permissions()")
        manager.add_user_to_role(user_id, int(admin_role.id))

    removed = ""
    if username != DEFAULT_ADMIN_USER:
        scaffold = session.query(User).filter(User.username == DEFAULT_ADMIN_USER).first()
        if scaffold is not None and int(scaffold.id) != user_id:
            if not scaffold_before and manager.has_role(user_id, "admin"):
                manager.delete_user(int(scaffold.id))
                removed = f"; removed scaffold '{DEFAULT_ADMIN_USER}' account"
            elif _still_has_default_password(manager, scaffold):
                # Non-destructive by design, but never silent about a second
                # full-admin account on the published default password.
                removed = (
                    f"; WARNING: '{DEFAULT_ADMIN_USER}' still exists with the default "
                    f"password -- change it or delete that account"
                )

    if created:
        return f"created admin user '{username}'{removed}", True
    return f"admin user '{username}' already exists (credentials untouched){removed}", False


def _still_has_default_password(manager, user) -> bool:
    """True when a local account still authenticates with the shipped default."""
    verifier = getattr(manager, "_verify_password", None)
    stored = getattr(user, "password_hash", None)
    if not callable(verifier) or not stored or not str(stored).startswith("$2"):
        return False
    try:
        return bool(verifier(str(stored), DEFAULT_ADMIN_PASSWORD))
    except Exception:
        return False


def sync_module_permissions() -> str:
    """Reconcile permission rows with the module catalog (needs app context)."""
    from flask import Flask

    from src.utils.user_manager import UserManager

    with Flask(__name__).app_context():
        UserManager().sync_module_permissions()
    return "module permissions synced with the module catalog"


def ensure_default_llm_connection() -> str:
    from src.utils.llm_connection_manager import seed_default_from_config

    seed_default_from_config()
    return "default LLM connection present"


def _short_error(exc: Exception) -> str:
    """``OperationalError: no such table: roles`` instead of a multi-line blob."""
    detail = str(exc).strip().splitlines()
    return f"{type(exc).__name__}: {detail[0] if detail else exc}"


def diagnose() -> list[str]:
    """Everything missing for the platform to serve: tables, roles, admin, LLM."""
    from src.agent_platform.bootstrap import existing_tables

    problems: list[str] = []
    missing = sorted(set(EXPECTED_TABLES) - existing_tables())
    if missing:
        problems.append(f"{len(missing)} missing table(s): {', '.join(missing)}")

    try:
        from src.models.llm_connection import LLMConnection

        if not LLMConnection.get_all():
            problems.append("no LLM connection configured")
    except Exception as exc:
        problems.append(f"LLM connections unreadable ({_short_error(exc)})")

    try:
        from src.models.user import User, Role
        from src.utils.user_manager import UserManager

        manager = UserManager()
        session = manager._get_session()
        admin_role = session.query(Role).filter(Role.name == "admin").first()
        if admin_role is None:
            problems.append("no 'admin' role")
        else:
            admins = [
                user.username
                for user in session.query(User).all()
                if manager.has_role(int(user.id), "admin")
            ]
            if not admins:
                problems.append("no user holds the 'admin' role")
        if session.query(User).count() == 0:
            problems.append("no users exist")
    except Exception as exc:
        problems.append(f"users/roles unreadable ({_short_error(exc)})")
    return problems


def report(*, apply: bool, admin: str, password: str, email: str) -> int:
    print(f"Database: {DATABASE_URI}")
    if not apply:
        problems = diagnose()
        if problems:
            for problem in problems:
                print(f"  - {problem}")
            print("Run without --check to create/fix the platform schema and roles.")
            return 1
        print(f"Schema ready ({len(EXPECTED_TABLES)} tables), roles, admin user, LLM connection: ok")
        return 0

    for step in create_schema():
        print(f"  schema: {step}")
    message, created = ensure_roles_and_admin(username=admin, password=password, email=email)
    print(f"  roles: {message}")
    print(f"  roles: {sync_module_permissions()}")
    print(f"  llm:   {ensure_default_llm_connection()}")

    problems = diagnose()
    if problems:
        for problem in problems:
            print(f"  ERROR: {problem}")
        return 1
    print("Setup complete." + (" Change the admin password before exposing this instance." if created else ""))
    print("Next: python scripts/feed_samples.py   (optional sample agents + MCP servers)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="report schema state and exit")
    parser.add_argument("--admin-user", default=os.environ.get("PLATFORM_ADMIN_USER", DEFAULT_ADMIN_USER))
    parser.add_argument(
        "--admin-password",
        default=os.environ.get("PLATFORM_ADMIN_PASSWORD", DEFAULT_ADMIN_PASSWORD),
    )
    parser.add_argument(
        "--admin-email",
        default=os.environ.get("PLATFORM_ADMIN_EMAIL"),
        help=f"defaults to <admin-user>@example.com (shipped default {DEFAULT_ADMIN_EMAIL})",
    )
    args = parser.parse_args(argv)
    return report(
        apply=not args.check,
        admin=args.admin_user,
        password=args.admin_password,
        email=args.admin_email or f"{args.admin_user}@example.com",
    )


if __name__ == "__main__":
    raise SystemExit(main())
