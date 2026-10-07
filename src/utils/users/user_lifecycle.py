"""User lifecycle operations, CRUD, escalation guards, and asset transfers."""

import logging
from sqlalchemy import text
from src.models.user import Role, User

logger = logging.getLogger("text2sql.user_lifecycle")

ADMIN_ROLE_NAME = "admin"
BUILTIN_ADMIN_USERNAME = "admin"


class EscalationGuardError(ValueError):
    """A user change that would strip the system of administrators."""


_USER_PRIVATE_DELETES: tuple[tuple[str, str], ...] = (
    (
        "agent_messages",
        "DELETE FROM agent_messages WHERE conversation_id IN "
        "(SELECT id FROM agent_conversations WHERE user_id = :uid)",
    ),
    (
        "agent_sessions",
        "DELETE FROM agent_sessions WHERE conversation_id IN "
        "(SELECT id FROM agent_conversations WHERE user_id = :uid)",
    ),
    (
        "agui_threads",
        "DELETE FROM agui_threads WHERE conversation_id IN "
        "(SELECT id FROM agent_conversations WHERE user_id = :uid)",
    ),
    (
        "agent_attachments",
        "DELETE FROM agent_attachments WHERE conversation_id IN "
        "(SELECT id FROM agent_conversations WHERE user_id = :uid)",
    ),
    (
        "agent_runs",
        "DELETE FROM agent_runs WHERE conversation_id IN "
        "(SELECT id FROM agent_conversations WHERE user_id = :uid)",
    ),
    ("agent_attachments", "DELETE FROM agent_attachments WHERE user_id = :uid"),
    ("agent_runs", "DELETE FROM agent_runs WHERE user_id = :uid"),
    ("agent_conversations", "DELETE FROM agent_conversations WHERE user_id = :uid"),
    ("api_keys", "DELETE FROM api_keys WHERE user_id = :uid"),
    ("knowledge_queries", "DELETE FROM knowledge_queries WHERE user_id = :uid"),
    ("code_generation_history", "DELETE FROM code_generation_history WHERE user_id = :uid"),
    ("agent_definition_access", "DELETE FROM agent_definition_access WHERE user_id = :uid"),
)

_USER_PROVENANCE_NULLS: tuple[tuple[str, str], ...] = (
    ("audit_logs", "user_id"),
    ("resource_role_access", "granted_by"),
    ("agent_definition_role_access", "granted_by"),
    ("hosted_app_role_access", "granted_by"),
    ("agent_definition_access", "granted_by"),
    ("agent_definitions", "updated_by"),
)

_USER_OWNED_ASSETS: tuple[tuple[str, str], ...] = (
    ("agent_definitions", "created_by"),
    ("skills", "owner_id"),
    ("maf_skills", "created_by"),
    ("llm_connections", "created_by"),
    ("mcp_servers", "created_by"),
    ("hosted_apps", "created_by"),
    ("knowledge_documents", "owner_id"),
    ("eval_definitions", "created_by"),
    ("eval_results", "created_by"),
)

#: Legacy per-asset role grants folded into ``resource_role_access`` at startup.
#: ``roles.id`` is also a recycled rowid, so a deleted role's rows here would be
#: resurrected onto the next role by ``migrate_legacy_grants``.
_LEGACY_ROLE_GRANT_TABLES: tuple[str, ...] = (
    "agent_definition_role_access",
    "hosted_app_role_access",
)



def is_admin_account(user) -> bool:
    """Whether the user object currently holds the built-in admin role."""
    return any(
        (getattr(role, "name", "") or "").lower() == ADMIN_ROLE_NAME
        for role in (getattr(user, "roles", None) or [])
    )


def active_admin_count(manager, session, exclude_user_id=None) -> int:
    """Count active users holding the admin role (optionally excluding one)."""
    query = (
        session.query(User)
        .filter(User.is_active.is_(True))
        .filter(User.roles.any(Role.name == ADMIN_ROLE_NAME))
    )
    if exclude_user_id is not None:
        query = query.filter(User.id != exclude_user_id)
    return int(query.count())


def guard_builtin_admin(user, action: str) -> None:
    """Refuse delete/deactivate on the seeded admin account itself."""
    if getattr(user, "username", "") == BUILTIN_ADMIN_USERNAME:
        raise EscalationGuardError(f"The built-in admin user cannot be {action}.")


def guard_last_active_admin(manager, session, user) -> None:
    """Refuse a change that would leave no active administrator behind."""
    if not is_admin_account(user) or not getattr(user, "is_active", True):
        return
    if active_admin_count(manager, session, exclude_user_id=user.id) == 0:
        raise EscalationGuardError("Cannot remove the last active administrator.")


def assert_user_deletable(manager, user) -> None:
    """Raise if user is protected from deletion."""
    session = manager._get_session()
    guard_builtin_admin(user, "deleted")
    guard_last_active_admin(manager, session, user)


def assert_user_update_allowed(manager, user, *, is_active=None, role_ids=None) -> None:
    """Raise if this update would trip an escalation guard."""
    session = manager._get_session()
    if is_active is not None and not bool(is_active):
        guard_builtin_admin(user, "deactivated")
        guard_last_active_admin(manager, session, user)
    if role_ids is not None:
        admin_role = session.query(Role).filter(Role.name == ADMIN_ROLE_NAME).first()
        if admin_role is not None and admin_role.id not in role_ids:
            if is_admin_account(user):
                guard_last_active_admin(manager, session, user)


def existing_tables(session) -> set[str]:
    """Names of tables present in the bound database."""
    rows = session.execute(text("SELECT name FROM sqlite_master WHERE type = 'table'")).fetchall()
    return {str(row[0]) for row in rows}


def purge_user_private_data(session, user_id: int) -> None:
    """Delete every private row a recycled users.id could inherit."""
    tables = existing_tables(session)
    for table, statement in _USER_PRIVATE_DELETES:
        if table in tables:
            session.execute(text(statement), {"uid": user_id})
    for table, column in _USER_PROVENANCE_NULLS:
        if table in tables:
            session.execute(
                text(f"UPDATE {table} SET {column} = NULL WHERE {column} = :uid"),
                {"uid": user_id},
            )


def transfer_owned_assets(session, user_id: int) -> None:
    """Move shared assets owned by the departing user to the administrator."""
    successor = session.query(User).filter(User.username == BUILTIN_ADMIN_USERNAME).first()
    if successor is None:
        raise EscalationGuardError(
            "Cannot delete the user: no administrator account exists to receive its shared assets."
        )
    tables = existing_tables(session)
    for table, column in _USER_OWNED_ASSETS:
        if table in tables:
            session.execute(
                text(f"UPDATE {table} SET {column} = :sid WHERE {column} = :uid"),
                {"sid": successor.id, "uid": user_id},
            )


def create_user(manager, username, email, password):
    """Create a new user."""
    try:
        session = manager._get_session()
        existing_user = session.query(User).filter(
            (User.username == username) | (User.email == email)
        ).first()

        if existing_user:
            if existing_user.username == username:
                raise ValueError(f"Username '{username}' already exists")
            else:
                raise ValueError(f"Email '{email}' already exists")

        user = User(
            username=username,
            email=email,
            password_hash=manager._hash_password(password),
            is_active=True,
        )
        session.add(user)
        session.commit()
        return user.id
    except Exception as e:
        session.rollback()
        logger.error("Error creating user: %s", e)
        raise


def update_user(manager, user_id, username, email, password=None, is_active=None):
    """Update an existing user with admin protection guards."""
    try:
        session = manager._get_session()
        user = session.query(User).filter(User.id == user_id).first()
        if not user:
            raise ValueError("User not found")

        existing_user = session.query(User).filter(
            ((User.username == username) | (User.email == email)) & (User.id != user_id)
        ).first()
        if existing_user:
            if existing_user.username == username:
                raise ValueError(f"Username '{username}' already exists")
            else:
                raise ValueError(f"Email '{email}' already exists")

        user.username = username
        user.email = email
        if password:
            user.password_hash = manager._hash_password(password)

        if is_active is not None and bool(is_active) != bool(user.is_active):
            if not bool(is_active):
                guard_builtin_admin(user, "deactivated")
                guard_last_active_admin(manager, session, user)
            user.is_active = bool(is_active)

        session.commit()
        return True
    except EscalationGuardError:
        session = manager._get_session()
        session.rollback()
        raise
    except Exception as e:
        session.rollback()
        logger.error("Error updating user: %s", e)
        raise


def delete_user(manager, user_id):
    """Delete a user, enforcing admin guards, purging private data, transferring assets."""
    try:
        session = manager._get_session()
        user = session.query(User).filter(User.id == user_id).first()
        if not user:
            raise ValueError("User not found")

        assert_user_deletable(manager, user)
        user.roles = []
        purge_user_private_data(session, user_id)
        transfer_owned_assets(session, user_id)

        session.delete(user)
        session.commit()
        return True
    except EscalationGuardError:
        session = manager._get_session()
        session.rollback()
        raise
    except Exception as e:
        session.rollback()
        logger.error("Error deleting user: %s", e)
        raise


def get_user_by_id(manager, user_id):
    """Get a user by ID."""
    if not user_id:
        return None
    session = manager._get_session()
    return session.query(User).filter(User.id == user_id).first()


def get_user_by_username(manager, username):
    """Get a user by username."""
    session = manager._get_session()
    return session.query(User).filter(User.username == username).first()


def get_all_users(manager):
    """Get all users."""
    session = manager._get_session()
    return session.query(User).all()


def get_user_count(manager):
    """Get total user count."""
    session = manager._get_session()
    return session.query(User).count()


def get_username_by_id(manager, user_id):
    """Get username by ID."""
    if not user_id:
        return None
    user = manager.get_user_by_id(user_id)
    return user.username if user else None
