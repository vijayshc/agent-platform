"""
User management utility for Text2SQL application.
Handles user authentication and role management.

User activity is audited by the authorization layer (src/auth/audit.py) into
the daily audit file; this module no longer owns an audit table.
"""

from src.utils.database import get_db_session
from src.models.user import User, Role, Permission
from sqlalchemy import text
import datetime
import uuid
import bcrypt  # Replacing hashlib with bcrypt
import secrets
import logging
import hashlib  # Keep for backward compatibility
from config.config import AUTH_PROVIDER

try:
    if AUTH_PROVIDER == 'ldap':
        from src.utils.ldap_auth import LDAPAuthenticator
    else:
        LDAPAuthenticator = None
except Exception:
    LDAPAuthenticator = None

logger = logging.getLogger('text2sql')

#: Name of the sole role that grants administrator access everywhere.
ADMIN_ROLE_NAME = "admin"
#: Username of the seeded account every deployment relies on.
BUILTIN_ADMIN_USERNAME = "admin"


class EscalationGuardError(ValueError):
    """A user change that would strip the system of administrators.

    Deleting/deactivating the built-in ``admin`` account or the final active
    administrator, and removing the ``admin`` role from that final
    administrator, all raise this so the API can answer 400/403 rather than
    leaking a 500.
    """


#: ``(table, statement)`` pairs that remove rows belonging to exactly one user.
#: ``users.id`` is a plain SQLite rowid (no ``AUTOINCREMENT``), so deleting the
#: highest-numbered account lets the next insert reuse its id; every private row
#: keyed by that id would silently become the new account's data.  Child rows of
#: a conversation are removed before the conversation itself so the declared
#: foreign keys stay satisfied.  ``:uid`` is the deleted user's id.
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

#: Append-only audit and grant-provenance columns that are retained but
#: de-identified: a recycled ``users.id`` must not inherit the deleted account's
#: history as though it were its own.  ``audit_logs`` is a legacy table (the live
#: trail is file-based in ``src.auth.audit``) whose ``user_id`` foreign key also
#: blocks the user delete while rows remain.
_USER_PROVENANCE_NULLS: tuple[tuple[str, str], ...] = (
    ("audit_logs", "user_id"),
    ("resource_role_access", "granted_by"),
    ("agent_definition_role_access", "granted_by"),
    ("hosted_app_role_access", "granted_by"),
    ("agent_definition_access", "granted_by"),
    ("agent_definitions", "updated_by"),
)

#: Shared tenant assets owned by the departing user.  They are retained because
#: other roles may hold grants on them and some are platform configuration, but
#: ownership moves to the built-in administrator: a recycled id must not become
#: their owner, and ``NULL`` ownership is not an option because
#: ``resource_access`` grandfathers owner-less assets to every authenticated user.
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


class UserManager:
    """Manages users, roles, permissions, and audit logs"""
    
    def __init__(self):
        """Initialize the user manager"""
        # Don't store session as instance variable to avoid thread conflicts
        pass
    
    def _get_session(self):
        """Get a thread-safe database session"""
        return get_db_session()

    @staticmethod
    def _is_admin_account(user) -> bool:
        """Whether the user object currently holds the built-in admin role."""
        return any(
            (getattr(role, "name", "") or "").lower() == ADMIN_ROLE_NAME
            for role in (getattr(user, "roles", None) or [])
        )

    def _active_admin_count(self, session, exclude_user_id=None) -> int:
        """Count active users holding the admin role (optionally excluding one).

        Administrators only grant access while ``is_active`` is true, so a
        deactivated account does not count towards keeping the system
        manageable.
        """
        query = (
            session.query(User)
            .filter(User.is_active.is_(True))
            .filter(User.roles.any(Role.name == ADMIN_ROLE_NAME))
        )
        if exclude_user_id is not None:
            query = query.filter(User.id != exclude_user_id)
        return int(query.count())

    def _guard_builtin_admin(self, user, action: str) -> None:
        """Refuse delete/deactivate on the seeded ``admin`` account itself."""
        if user.username == BUILTIN_ADMIN_USERNAME:
            raise EscalationGuardError(
                f"The built-in admin user cannot be {action}."
            )

    def _guard_last_active_admin(self, session, user) -> None:
        """Refuse a change that would leave no active administrator behind."""
        if not self._is_admin_account(user) or not user.is_active:
            return
        if self._active_admin_count(session, exclude_user_id=user.id) == 0:
            raise EscalationGuardError(
                "Cannot remove the last active administrator."
            )

    def assert_user_deletable(self, user) -> None:
        """Raise if ``user`` is protected from deletion.

        Public so an API can refuse a protected target with 403 *before* any
        other check, and used by :meth:`delete_user` so the route contract and
        the write path can never disagree.
        """
        session = self._get_session()
        self._guard_builtin_admin(user, "deleted")
        self._guard_last_active_admin(session, user)

    def assert_user_update_allowed(self, user, *, is_active=None, role_ids=None) -> None:
        """Raise if this update would trip an escalation guard.

        Mirrors the checks :meth:`update_user` and :meth:`remove_user_from_role`
        actually enforce, so an API can answer 403 for a forbidden change ahead
        of generic field validation without weakening the write path.
        """
        session = self._get_session()
        if is_active is not None and not bool(is_active):
            self._guard_builtin_admin(user, "deactivated")
            self._guard_last_active_admin(session, user)
        if role_ids is not None:
            admin_role = (
                session.query(Role).filter(Role.name == ADMIN_ROLE_NAME).first()
            )
            if admin_role is not None and admin_role.id not in role_ids:
                holds_admin = any(
                    (getattr(role, "name", "") or "") == ADMIN_ROLE_NAME
                    for role in user.roles
                )
                if holds_admin:
                    self._guard_last_active_admin(session, user)

    @staticmethod
    def _existing_tables(session) -> set[str]:
        """Names of tables present in the bound database.

        Legacy tables (``audit_logs``, ``code_generation_history``,
        ``agent_definition_access`` …) are optional: a database created fresh by
        the current code does not necessarily hold them, so the cleanup below
        only touches tables that exist instead of failing the whole delete.
        """
        rows = session.execute(
            text("SELECT name FROM sqlite_master WHERE type = 'table'")
        ).fetchall()
        return {str(row[0]) for row in rows}

    def _purge_user_private_data(self, session, user_id: int) -> None:
        """Delete every private row a recycled ``users.id`` could inherit.

        Chat threads and their children, runs, API keys, knowledge queries,
        code-generation history and per-user agent grants are all scoped to one
        account, so account deletion removes them.  Audit and grant-provenance
        columns are retained but de-identified.  Every statement runs on the
        caller's session, inside the same transaction as the user deletion, so
        the account and its data disappear together or not at all.
        """
        tables = self._existing_tables(session)
        for table, statement in _USER_PRIVATE_DELETES:
            if table in tables:
                session.execute(text(statement), {"uid": user_id})
        for table, column in _USER_PROVENANCE_NULLS:
            if table in tables:
                session.execute(
                    text(f"UPDATE {table} SET {column} = NULL WHERE {column} = :uid"),
                    {"uid": user_id},
                )

    def _transfer_owned_assets(self, session, user_id: int) -> None:
        """Move shared assets owned by the departing user to the administrator.

        These rows are deliberately not deleted: other roles may hold grants on
        them and some are platform configuration (LLM/MCP connections).  They
        cannot keep their owner's id either — a recycled id would make the new
        account their owner — and they cannot be nulled, because
        ``resource_access`` treats an owner-less asset as visible to every
        authenticated user.  Handing them to the built-in administrator retains
        the assets without transferring them to a future account.
        """
        successor = (
            session.query(User)
            .filter(User.username == BUILTIN_ADMIN_USERNAME)
            .first()
        )
        if successor is None:
            raise EscalationGuardError(
                "Cannot delete the user: no administrator account exists to "
                "receive its shared assets."
            )
        tables = self._existing_tables(session)
        for table, column in _USER_OWNED_ASSETS:
            if table in tables:
                session.execute(
                    text(f"UPDATE {table} SET {column} = :sid WHERE {column} = :uid"),
                    {"sid": successor.id, "uid": user_id},
                )
    
    def _hash_password(self, password):
        """Hash a password using bcrypt"""
        # Generate a salt and hash the password
        password_bytes = password.encode('utf-8')
        salt = bcrypt.gensalt(rounds=12)  # Work factor of 12 (higher is more secure but slower)
        password_hash = bcrypt.hashpw(password_bytes, salt)
        return password_hash.decode('utf-8')
    
    def _verify_password(self, password_hash, password):
        """Verify a password against stored hash"""
        if not password_hash or not password:
            return False
            
        try:
            # Check if this is an old SHA-256 hash (contains a $ separator)
            if '$' in password_hash and not password_hash.startswith('$2b$'):
                # Old hash format - verify using old method and upgrade if correct
                salt, stored_hash = password_hash.split('$')
                h = hashlib.sha256()
                h.update((password + salt).encode('utf-8'))
                is_valid = h.hexdigest() == stored_hash
                
                # If valid, update to new hash format
                if is_valid:
                    logger.info("Upgrading password hash from SHA-256 to bcrypt")
                    session = self._get_session()
                    user = session.query(User).filter(User.password_hash == password_hash).first()
                    if user:
                        user.password_hash = self._hash_password(password)
                        session.commit()
                
                return is_valid
            
            # New bcrypt hash format
            password_bytes = password.encode('utf-8')
            stored_hash_bytes = password_hash.encode('utf-8')
            return bcrypt.checkpw(password_bytes, stored_hash_bytes)
            
        except Exception as e:
            logger.error(f"Password verification error: {str(e)}")
            return False
    
    def authenticate(self, username, password):
        """Authenticate a user by username and password.
        If AUTH_PROVIDER is 'ldap', authenticate via LDAP and provision user if needed.
        """
        try:
            if AUTH_PROVIDER == 'ldap':
                return self._authenticate_ldap(username, password)
            else:
                session = self._get_session()
                user = session.query(User).filter(User.username == username).first()
                if not user:
                    return None
                if self._verify_password(user.password_hash, password):
                    return user.id
                return None
        except Exception as e:
            logger.info(f"Authentication error: {str(e)}")
            return None

    def _authenticate_ldap(self, username: str, password: str):
        """Authenticate via LDAP and upsert a local user record.
        Returns user.id on success else None.
        """
        if LDAPAuthenticator is None:
            logger.error("LDAPAuthenticator not available; check configuration and dependencies.")
            return None

        ldap = LDAPAuthenticator()
        profile = ldap.authenticate(username, password)
        if not profile:
            return None

        # Provision or update local user without storing the external password
        session = self._get_session()
        user = session.query(User).filter(User.username == username).first()
        if not user:
            # Ensure 'user' role exists
            role = session.query(Role).filter(Role.name == "user").first()
            if not role:
                role = Role(name="user", description="Standard user")
                session.add(role)
                session.flush()

            # Create a strong random local password hash placeholder
            placeholder = uuid.uuid4().hex + uuid.uuid4().hex
            user = User(
                username=username,
                email=profile.get('email') or f"{username}@example.com",
                password_hash=self._hash_password(placeholder),
                is_active=True
            )
            session.add(user)
            session.flush()
            user.roles.append(role)
            session.commit()
        else:
            # Optionally update email
            updated = False
            new_email = profile.get('email')
            if new_email and new_email != user.email:
                user.email = new_email
                updated = True
            if not user.is_active:
                # Deny login if inactive locally
                return None
            if updated:
                session.commit()
        return user.id
    
    def create_user(self, username, email, password):
        """Create a new user"""
        try:
            session = self._get_session()
            # Check if username or email already exists
            existing_user = session.query(User).filter(
                (User.username == username) | (User.email == email)
            ).first()
            
            if existing_user:
                if existing_user.username == username:
                    raise ValueError(f"Username '{username}' already exists")
                else:
                    raise ValueError(f"Email '{email}' already exists")
            
            # Create the user
            user = User(
                username=username,
                email=email,
                password_hash=self._hash_password(password),
                is_active=True
            )
            
            session.add(user)
            session.commit()
            
            return user.id
        except Exception as e:
            session.rollback()
            logger.error(f"Error creating user: {str(e)}")
            raise
    
    def update_user(self, user_id, username, email, password=None, is_active=None):
        """Update an existing user.

        ``is_active=False`` deactivates the account.  The same escalation guards
        as deletion apply: the built-in ``admin`` account can never be
        deactivated and the last active administrator cannot be locked out.
        """
        try:
            session = self._get_session()
            user = session.query(User).filter(User.id == user_id).first()
            
            if not user:
                raise ValueError("User not found")
            
            # Check if username or email already exists for another user
            existing_user = session.query(User).filter(
                ((User.username == username) | (User.email == email)) &
                (User.id != user_id)
            ).first()
            
            if existing_user:
                if existing_user.username == username:
                    raise ValueError(f"Username '{username}' already exists")
                else:
                    raise ValueError(f"Email '{email}' already exists")
            
            # Update user properties
            user.username = username
            user.email = email
            
            # Update password if provided
            if password:
                user.password_hash = self._hash_password(password)

            if is_active is not None and bool(is_active) != bool(user.is_active):
                if not bool(is_active):
                    self._guard_builtin_admin(user, "deactivated")
                    self._guard_last_active_admin(session, user)
                user.is_active = bool(is_active)
            
            session.commit()
            return True
        except EscalationGuardError:
            session = self._get_session()
            session.rollback()
            raise
        except Exception as e:
            session.rollback()
            logger.error(f"Error updating user: {str(e)}")
            raise
    
    def delete_user(self, user_id):
        """Delete a user.

        Two escalation guards protect administrator identity: the built-in
        ``admin`` account can never be deleted, and the final active
        administrator cannot be deleted (otherwise nobody could administer the
        system afterwards).
        """
        try:
            session = self._get_session()
            user = session.query(User).filter(User.id == user_id).first()
            
            if not user:
                raise ValueError("User not found")
            
            self.assert_user_deletable(user)
            
            # Remove user from roles
            user.roles = []

            # Purge the account's private data, de-identify retained audit
            # rows, and hand its shared assets to the administrator — all in
            # this transaction, before the users row disappears.
            self._purge_user_private_data(session, user_id)
            self._transfer_owned_assets(session, user_id)
            
            # Delete the user
            session.delete(user)
            session.commit()
            
            return True
        except EscalationGuardError:
            session = self._get_session()
            session.rollback()
            raise
        except Exception as e:
            session.rollback()
            logger.error(f"Error deleting user: {str(e)}")
            raise
    
    def get_user_by_id(self, user_id):
        """Get a user by ID"""
        if not user_id:
            return None
            
        session = self._get_session()
        return session.query(User).filter(User.id == user_id).first()
    
    def get_user_by_username(self, username):
        """Get a user by username"""
        session = self._get_session()
        return session.query(User).filter(User.username == username).first()
    
    def get_all_users(self):
        """Get all users"""
        session = self._get_session()
        return session.query(User).all()
    
    def get_user_count(self):
        """Get total user count"""
        session = self._get_session()
        return session.query(User).count()
    
    def get_username_by_id(self, user_id):
        """Get username by ID"""
        if not user_id:
            return None
            
        user = self.get_user_by_id(user_id)
        return user.username if user else None
    
    def change_password(self, user_id, current_password, new_password):
        """Change a user's password"""
        try:
            user = self.get_user_by_id(user_id)
            
            if not user:
                return False
                
            # Verify current password
            if not self._verify_password(user.password_hash, current_password):
                return False
            
            # Update password
            session = self._get_session()
            user.password_hash = self._hash_password(new_password)
            session.commit()
            
            return True
        except Exception as e:
            session = self._get_session()
            session.rollback()
            logger.error(f"Error changing password: {str(e)}")
            return False
    
    def generate_reset_token(self, username):
        """Generate a password reset token"""
        try:
            user = self.get_user_by_username(username)
            
            if not user:
                return False
            
            # Generate token
            token = secrets.token_urlsafe(32)
            
            # Store token and expiry
            session = self._get_session()
            user.reset_token = token
            user.reset_token_expiry = datetime.datetime.utcnow() + datetime.timedelta(hours=24)
            
            session.commit()
            
            return True
        except Exception as e:
            session = self._get_session()
            session.rollback()
            logger.error(f"Error generating reset token: {str(e)}")
            return False
    
    def verify_reset_token(self, token):
        """Verify a password reset token"""
        try:
            session = self._get_session()
            user = session.query(User).filter(
                (User.reset_token == token) &
                (User.reset_token_expiry > datetime.datetime.utcnow())
            ).first()
            
            if not user:
                return None
                
            return user.id
        except Exception as e:
            logger.error(f"Error verifying reset token: {str(e)}")
            return None
    
    def reset_password(self, user_id, new_password):
        """Reset a user's password using a token"""
        try:
            user = self.get_user_by_id(user_id)
            
            if not user:
                return False
            
            # Update password
            session = self._get_session()
            user.password_hash = self._hash_password(new_password)
            
            # Clear reset token
            user.reset_token = None
            user.reset_token_expiry = None
            
            session.commit()
            
            return True
        except Exception as e:
            session = self._get_session()
            session.rollback()
            logger.error(f"Error resetting password: {str(e)}")
            return False
    
    # Role management
    
    def get_all_roles(self):
        """Get all roles"""
        session = self._get_session()
        return session.query(Role).all()
    
    def get_role_count(self):
        """Get total role count"""
        session = self._get_session()
        return session.query(Role).count()
    
    def has_role(self, user_id, role_name):
        """Check if a user has a specific role"""
        try:
            user = self.get_user_by_id(user_id)
            
            if not user:
                return False
                
            for role in user.roles:
                if role.name == role_name:
                    return True
                    
            return False
        except Exception as e:
            logger.error(f"Error checking role: {str(e)}")
            return False
    
    def add_user_to_role(self, user_id, role_id):
        """Add a user to a role"""
        try:
            user = self.get_user_by_id(user_id)
            session = self._get_session()
            role = session.query(Role).filter(Role.id == role_id).first()
            
            if not user or not role:
                return False
            
            # Check if user already has this role
            for existing_role in user.roles:
                if existing_role.id == role.id:
                    return True
            
            # Add role to user
            user.roles.append(role)
            session.commit()
            
            return True
        except Exception as e:
            session = self._get_session()
            session.rollback()
            logger.error(f"Error adding user to role: {str(e)}")
            return False
    
    def remove_user_from_role(self, user_id, role_id):
        """Remove a user from a role.

        Stripping the ``admin`` role from the final active administrator is
        refused: that user would lose every administrative capability at once.
        """
        try:
            user = self.get_user_by_id(user_id)
            session = self._get_session()
            role = session.query(Role).filter(Role.id == role_id).first()
            
            if not user or not role:
                return False
            
            # Check if user has this role
            has_role = False
            for existing_role in user.roles:
                if existing_role.id == role.id:
                    has_role = True
                    break
            
            if not has_role:
                return True

            if role.name == ADMIN_ROLE_NAME:
                self._guard_last_active_admin(session, user)
            
            # Remove role from user
            user.roles.remove(role)
            session.commit()
            
            return True
        except EscalationGuardError:
            session = self._get_session()
            session.rollback()
            raise
        except Exception as e:
            session = self._get_session()
            session.rollback()
            logger.error(f"Error removing user from role: {str(e)}")
            return False
    
    def get_role_by_id(self, role_id):
        """Get a role by its ID"""
        try:
            session = self._get_session()
            return session.query(Role).filter(Role.id == role_id).first()
        except Exception as e:
            logger.error(f"Error getting role by ID: {str(e)}")
            return None
    
    def create_role(self, name, description=""):
        """Create a new role"""
        try:
            session = self._get_session()
            # Check if role already exists
            existing_role = session.query(Role).filter(Role.name == name).first()
            
            if existing_role:
                raise ValueError(f"Role '{name}' already exists")
            
            # Create the role
            role = Role(name=name, description=description)
            
            session.add(role)
            session.commit()
            
            return role.id
        except Exception as e:
            session = self._get_session()
            session.rollback()
            logger.error(f"Error creating role: {str(e)}")
            raise
    
    def update_role(self, role_id, name, description):
        """Update an existing role.

        The built-in ``admin`` role may have its description changed but never
        its name: every administrator check keys off ``has_role(user_id,
        "admin")``, so a rename would silently revoke administrator access
        across the whole application.
        """
        try:
            role = self.get_role_by_id(role_id)
            
            if not role:
                raise ValueError("Role not found")
            
            if role.name == "admin" and name != "admin":
                raise ValueError("Cannot rename the built-in admin role")
            
            session = self._get_session()
            # Check if name already exists for another role
            existing_role = session.query(Role).filter(
                (Role.name == name) & (Role.id != role_id)
            ).first()
            
            if existing_role:
                raise ValueError(f"Role name '{name}' already exists")
            
            # Update role properties
            role.name = name
            role.description = description
            
            session.commit()
            return True
        except Exception as e:
            session = self._get_session()
            session.rollback()
            logger.error(f"Error updating role: {str(e)}")
            raise
    
    def delete_role(self, role_id):
        """Delete a role.

        The built-in ``admin`` role is protected: it is the only role that
        grants administrator access (``has_role(user_id, "admin")``), so
        deleting it could leave the system with no administrator at all.

        Per-asset grants in ``resource_role_access`` are removed in the same
        transaction as the role row.  Otherwise a deleted role id could be
        reused by SQLite and the stale grant would silently hand the new role
        access to every asset the old one could reach.
        """
        try:
            role = self.get_role_by_id(role_id)
            
            if not role:
                raise ValueError("Role not found")
            
            if role.name == "admin":
                raise ValueError("Cannot delete the built-in admin role")
            
            session = self._get_session()
            # Remove users from role
            for user in role.users:
                user.roles.remove(role)
            
            # Purge this role's resource grants before the role row disappears,
            # in the same transaction so the delete is all-or-nothing.
            session.execute(
                text("DELETE FROM resource_role_access WHERE role_id = :role_id"),
                {"role_id": role_id},
            )

            # Legacy per-asset grant tables are copied into
            # ``resource_role_access`` by ``migrate_legacy_grants`` on every
            # startup, so rows left for a recycled role id would resurrect this
            # role's access on the next boot.
            tables = self._existing_tables(session)
            for table in _LEGACY_ROLE_GRANT_TABLES:
                if table in tables:
                    session.execute(
                        text(f"DELETE FROM {table} WHERE role_id = :role_id"),
                        {"role_id": role_id},
                    )
            
            # Delete the role
            session.delete(role)
            session.commit()
            
            return True
        except Exception as e:
            session = self._get_session()
            session.rollback()
            logger.error(f"Error deleting role: {str(e)}")
            raise
    
    def get_role_modules(self, role_id):
        """Return module keys granted to a role (any level), admin => all keys."""
        return list(self.get_role_module_levels(role_id).keys())

    def get_role_module_levels(self, role_id) -> dict[str, str]:
        """Return ``{module_key: "read"|"write"}`` for a role."""
        from src.auth.modules import ACCESS_WRITE, MODULE_KEYS

        role = self.get_role_by_id(role_id)
        if not role:
            raise ValueError("Role not found")
        if role.name == "admin":
            return {key: ACCESS_WRITE for key in MODULE_KEYS}
        return self._module_levels_from_permissions(role.permissions)

    def get_role_permissions(self, role_id):
        """Get permission rows for a role (kept for API compatibility)."""
        role = self.get_role_by_id(role_id)
        if not role:
            raise ValueError("Role not found")
        return role.permissions

    def update_role_permissions(self, role_id, permission_ids):
        """Legacy permission-id update.

        Module levels are the supported model; callers should use
        :meth:`update_role_modules` instead.  This shim remains for older
        integrations and resolves IDs to module permissions where possible.
        """
        from src.auth.modules import MODULE_PERMISSION_NAMES

        session = self._get_session()
        role = session.query(Role).filter(Role.id == role_id).first()
        if not role:
            raise ValueError("Role not found")
        if role.name == "admin":
            raise ValueError("Cannot modify admin role permissions")

        from src.auth.modules import MODULE_BY_KEY, split_module_permission

        selected: dict[str, Permission] = {}
        for raw_id in permission_ids or []:
            try:
                perm = session.query(Permission).filter(Permission.id == int(raw_id)).first()
            except (TypeError, ValueError):
                continue
            if not perm or perm.name not in MODULE_PERMISSION_NAMES:
                continue
            parsed = split_module_permission(perm.name)
            if parsed is None:
                continue
            key, level = parsed
            # If both read and write IDs arrive, write wins.
            if key not in selected or level == "write":
                selected[key] = perm
        role.permissions = [
            selected[m.key] for m in MODULE_BY_KEY.values() if m.key in selected
        ]
        try:
            session.commit()
            return True
        except Exception:
            session.rollback()
            raise

    def update_role_modules(self, role_id, module_assignments):
        """Replace a role's permissions with explicit module/access assignments.

        ``module_assignments`` accepts the same shapes as
        ``normalize_module_assignments``:
          - ``["knowledge", "users"]`` (legacy => write)
          - ``{"knowledge": "read"}``
          - ``[{"key": "knowledge", "access": "read"}]``
        """
        from src.auth.modules import ACCESS_READ, normalize_module_assignments

        role = self.get_role_by_id(role_id)
        if not role:
            raise ValueError("Role not found")
        if role.name == "admin":
            raise ValueError("Cannot modify admin role permissions")

        requested = normalize_module_assignments(module_assignments)
        session = self._get_session()
        role = session.query(Role).filter(Role.id == role_id).first()

        # Admin permission rows may already exist from a previous sync; read
        # rows are created lazily on first assignment.
        wanted_names = []
        for key, level in requested.items():
            module = session.query(Permission).filter(
                Permission.name.in_([
                    f"module:{key}",
                    f"module:{key}:{ACCESS_READ}",
                ])
            ).all()
            by_name = {p.name: p for p in module}
            write_perm = by_name.get(f"module:{key}")
            if write_perm is None:
                from src.auth.modules import get_module

                definition = get_module(key)
                write_perm = Permission(
                    name=f"module:{key}",
                    description=(definition.description if definition else f"Full access to {key}"),
                )
                session.add(write_perm)
                session.flush()
            if level == ACCESS_READ:
                read_perm = by_name.get(f"module:{key}:{ACCESS_READ}")
                if read_perm is None:
                    from src.auth.modules import get_module

                    definition = get_module(key)
                    read_perm = Permission(
                        name=f"module:{key}:{ACCESS_READ}",
                        description=(
                            f"Read-only access to {definition.label}"
                            if definition else f"Read-only access to {key}"
                        ),
                    )
                    session.add(read_perm)
                    session.flush()
                wanted_names.append(read_perm.name)
            else:
                wanted_names.append(write_perm.name)

        perms = session.query(Permission).filter(Permission.name.in_(wanted_names)).all()
        role.permissions = perms
        try:
            session.commit()
            return True
        except Exception:
            session.rollback()
            raise

    def get_all_permissions(self):
        """Get all persisted permissions (legacy compatibility endpoint)."""
        session = self._get_session()
        return session.query(Permission).all()

    def has_permission(self, user_id, permission_name):
        """Return whether a user directly or indirectly holds a permission name.

        Admin always has full access.  For module permissions prefer
        :meth:`has_module_access`; this method stays as a low-level primitive
        used by older code paths.
        """
        try:
            if not user_id:
                return False
            user = self.get_user_by_id(user_id)
            if not user:
                return False
            for role in user.roles:
                if role.name == "admin":
                    return True
                for permission in role.permissions:
                    if permission.name == permission_name:
                        return True
            return False
        except Exception as e:
            logger.error(f"Error checking permission: {str(e)}")
            return False
    
    # Database initialization
    
    def initialize_roles_permissions(self):
        """Initialize the default admin/user roles and the module catalog."""
        try:
            from src.auth.modules import MODULES

            session = self._get_session()

            module_perms = []
            for module in MODULES:
                write_perm = session.query(Permission).filter(
                    Permission.name == module.write_permission_name
                ).first()
                if not write_perm:
                    write_perm = Permission(
                        name=module.write_permission_name,
                        description=module.description,
                    )
                    session.add(write_perm)
                    session.flush()
                read_perm = session.query(Permission).filter(
                    Permission.name == module.read_permission_name
                ).first()
                if not read_perm:
                    read_perm = Permission(
                        name=module.read_permission_name,
                        description=f"Read-only access to {module.label}",
                    )
                    session.add(read_perm)
                    session.flush()
                module_perms.append(write_perm)

            # Admin always keeps every module.
            admin_role = session.query(Role).filter(Role.name == "admin").first()
            if not admin_role:
                admin_role = Role(name="admin", description="Administrator with full access")
                session.add(admin_role)
                session.flush()
            admin_role.permissions = list(module_perms)

            # Standard users can always use the main agent chat.  Admin/feature
            # modules are granted explicitly by an administrator.
            user_role = session.query(Role).filter(Role.name == "user").first()
            if not user_role:
                user_role = Role(name="user", description="Standard user")
                session.add(user_role)
                session.flush()
            user_role.permissions = []

            # Create default admin user if it does not exist.
            admin_user = session.query(User).filter(User.username == "admin").first()
            if not admin_user:
                admin_user = User(
                    username="admin",
                    email="admin@example.com",
                    password_hash=self._hash_password("admin123"),
                    is_active=True,
                )
                session.add(admin_user)
                session.flush()
            if admin_role not in admin_user.roles:
                admin_user.roles.append(admin_role)

            session.commit()
            return True
        except Exception as e:
            try:
                session = self._get_session()
                session.rollback()
            except Exception:
                pass
            logger.error(f"Error initializing roles and permissions: {str(e)}")
            return False

    # Module access helpers

    @staticmethod
    def _module_levels_from_permissions(permissions) -> dict[str, str]:
        from src.auth.modules import (
            ACCESS_READ,
            ACCESS_WRITE,
            MODULE_BY_KEY,
            module_level_from_permission,
            module_key_from_permission,
        )

        found: dict[str, str] = {}
        for perm in permissions or []:
            key = module_key_from_permission(getattr(perm, "name", None))
            level = module_level_from_permission(getattr(perm, "name", None))
            if not key or level not in {ACCESS_READ, ACCESS_WRITE}:
                continue
            # Write must always win over read for the same module.
            if found.get(key) == ACCESS_WRITE:
                continue
            found[key] = level
        return {m.key: found[m.key] for m in MODULE_BY_KEY.values() if m.key in found}

    def get_user_module_levels(self, user_id) -> dict[str, str]:
        """Return ``{module_key: "read"|"write"}`` for a user."""
        from src.auth.modules import ACCESS_WRITE, MODULE_KEYS

        if not user_id:
            return {}
        try:
            user = self.get_user_by_id(user_id)
            if not user:
                return {}
            levels: dict[str, str] = {}
            for role in user.roles:
                if role.name == "admin":
                    return {key: ACCESS_WRITE for key in MODULE_KEYS}
                for key, level in self._module_levels_from_permissions(role.permissions).items():
                    if level == ACCESS_WRITE or levels.get(key) != ACCESS_WRITE:
                        levels[key] = level
            return levels
        except Exception as e:
            logger.error(f"Error resolving user module levels: {str(e)}")
            return {}

    def get_user_modules(self, user_id) -> set[str]:
        """Return every module key the user can access (read or write)."""
        return set(self.get_user_module_levels(user_id).keys())

    def has_module_access(self, user_id, module_key: str, min_level: str = "read") -> bool:
        """Whether a user meets ``min_level`` (read/write) for a module."""
        from src.auth.modules import level_allows

        if not user_id or not module_key:
            return False
        return level_allows(self.get_user_module_levels(user_id).get(str(module_key).strip().lower()), min_level)

    def has_any_module_access(self, user_id, module_keys, min_level: str = "read") -> bool:
        """Whether a user meets ``min_level`` for any of ``module_keys``."""
        if not user_id:
            return False
        requested = {str(k).strip().lower() for k in (module_keys or ()) if str(k).strip()}
        if not requested:
            return False
        from src.auth.modules import level_allows

        levels = self.get_user_module_levels(user_id)
        return any(level_allows(levels.get(key), min_level) for key in requested)

    def sync_module_permissions(self):
        """Synchronise permission rows and role assignments with the module catalog.

        This replaces the old endpoint-permission sync.  It is idempotent and
        safe to call on every application startup.  Obsolete endpoint/core
        permission rows are removed so role management is always expressed in
        module terms.
        """
        try:
            from flask import current_app

            has_app_context = bool(current_app)
        except RuntimeError:
            has_app_context = False
        if not has_app_context:
            return
        from src.auth.modules import (
            MODULES,
            MODULE_PERMISSION_NAMES,
            split_module_permission,
        )
        from src.models.user import role_permission_association

        session = self._get_session()
        try:
            write_perms = {}
            for module in MODULES:
                for name, description in (
                    (module.write_permission_name, module.description),
                    (module.read_permission_name, f"Read-only access to {module.label}"),
                ):
                    perm = session.query(Permission).filter(Permission.name == name).first()
                    if not perm:
                        perm = Permission(name=name, description=description)
                        session.add(perm)
                        session.flush()
                write_perms[module.key] = session.query(Permission).filter(
                    Permission.name == module.write_permission_name
                ).first()

            module_perm_names = set(MODULE_PERMISSION_NAMES)

            # Keep only module permissions on roles. Write wins when a role has
            # both forms for the same module.
            for role in session.query(Role).all():
                if role.name == "admin":
                    role.permissions = list(write_perms.values())
                    continue
                keep = []
                seen = set()
                for perm in role.permissions:
                    name = getattr(perm, "name", None)
                    if name not in module_perm_names:
                        continue
                    parsed = split_module_permission(name)
                    if parsed is None:
                        continue
                    key, level = parsed
                    if key in seen:
                        # Replace a previously kept read permission with write.
                        existing_index = next(
                            i for i, p in enumerate(keep)
                            if split_module_permission(p.name)[0] == key
                        )
                        if level == "write":
                            keep[existing_index] = perm
                        continue
                    if level in {"read", "write"}:
                        # Prefer write if both accidentally present later.
                        keep.append(perm)
                        seen.add(key)
                role.permissions = keep
            session.flush()

            # Remove obsolete permission rows and any leftover association rows.
            legacy_ids = [
                perm_id for (perm_id,) in session.query(Permission.id)
                .filter(~Permission.name.in_(module_perm_names))
                .all()
            ]
            if legacy_ids:
                session.execute(
                    role_permission_association.delete().where(
                        role_permission_association.c.permission_id.in_(legacy_ids)
                    )
                )
                session.query(Permission).filter(Permission.id.in_(legacy_ids)).delete(
                    synchronize_session=False
                )
            session.commit()
        except Exception:
            session.rollback()
            raise
    def get_all_endpoints(self):
        """Get all registered endpoints from the Flask app"""
        from flask import current_app
        endpoints = []
        if current_app:
            for rule in current_app.url_map.iter_rules():
                if rule.endpoint not in endpoints:
                    endpoints.append(rule.endpoint)
        return sorted(endpoints)

    def check_endpoint_access(self, user_id, endpoint):
        """Check if a user has access to a specific endpoint"""
        # Public endpoints that don't require authentication
        public_endpoints = ['static', 'auth.login', 'auth.logout', 'auth.reset_password_request', 'auth.reset_password']
        if endpoint in public_endpoints:
            return True
            
        if not user_id:
            return False
            
        user = self.get_user_by_id(user_id)
        if not user:
            return False
            
        # Admin has access to everything
        if self.has_role(user_id, 'admin'):
            return True
            
        # Check permissions
        # We check for exact match or wildcard match
        # e.g. 'admin.users' matches 'admin.users' or 'admin.*' or '*'
        
        allowed_endpoints = set()
        for role in user.roles:
            for permission in role.permissions:
                allowed_endpoints.add(permission.name)
        
        if '*' in allowed_endpoints:
            return True
            
        if endpoint in allowed_endpoints:
            return True
            
        # Check wildcards
        parts = endpoint.split('.')
        if len(parts) > 1:
            # Check 'blueprint.*'
            if f"{parts[0]}.*" in allowed_endpoints:
                return True
                
        return False

    def sync_endpoint_permissions(self):
        """Deprecated compatibility alias for :meth:`sync_module_permissions`."""
        self.sync_module_permissions()
