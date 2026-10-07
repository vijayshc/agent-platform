"""User authentication, password hashing, and token reset logic."""

import datetime
import hashlib
import logging
import secrets
import uuid
from typing import Optional
import bcrypt
from config.config import AUTH_PROVIDER
from src.models.user import Role, User

logger = logging.getLogger("text2sql.user_auth")

try:
    if AUTH_PROVIDER == "ldap":
        from src.utils.ldap_auth import LDAPAuthenticator
    else:
        LDAPAuthenticator = None
except Exception:
    LDAPAuthenticator = None


def hash_password(manager, password: str) -> str:
    """Hash a password using bcrypt."""
    password_bytes = password.encode("utf-8")
    salt = bcrypt.gensalt(rounds=12)
    password_hash = bcrypt.hashpw(password_bytes, salt)
    return password_hash.decode("utf-8")


def verify_password(manager, password_hash: str, password: str) -> bool:
    """Verify a password against stored hash, upgrading SHA-256 hashes if valid."""
    if not password_hash or not password:
        return False

    try:
        if "$" in password_hash and not password_hash.startswith("$2b$"):
            salt, stored_hash = password_hash.split("$")
            h = hashlib.sha256()
            h.update((password + salt).encode("utf-8"))
            is_valid = h.hexdigest() == stored_hash

            if is_valid:
                logger.info("Upgrading password hash from SHA-256 to bcrypt")
                session = manager._get_session()
                user = session.query(User).filter(User.password_hash == password_hash).first()
                if user:
                    user.password_hash = manager._hash_password(password)
                    session.commit()

            return is_valid

        password_bytes = password.encode("utf-8")
        stored_hash_bytes = password_hash.encode("utf-8")
        return bcrypt.checkpw(password_bytes, stored_hash_bytes)

    except Exception as e:
        logger.error("Password verification error: %s", e)
        return False


def authenticate(manager, username: str, password: str):
    """Authenticate a user by username and password."""
    try:
        if AUTH_PROVIDER == "ldap":
            return authenticate_ldap(manager, username, password)
        else:
            session = manager._get_session()
            user = session.query(User).filter(User.username == username).first()
            if not user:
                return None
            if manager._verify_password(user.password_hash, password):
                return user.id
            return None
    except Exception as e:
        logger.info("Authentication error: %s", e)
        return None


def authenticate_ldap(manager, username: str, password: str):
    """Authenticate via LDAP and upsert a local user record."""
    if LDAPAuthenticator is None:
        logger.error("LDAPAuthenticator not available; check configuration and dependencies.")
        return None

    ldap = LDAPAuthenticator()
    profile = ldap.authenticate(username, password)
    if not profile:
        return None

    session = manager._get_session()
    user = session.query(User).filter(User.username == username).first()
    if not user:
        role = session.query(Role).filter(Role.name == "user").first()
        if not role:
            role = Role(name="user", description="Standard user")
            session.add(role)
            session.flush()

        placeholder = uuid.uuid4().hex + uuid.uuid4().hex
        user = User(
            username=username,
            email=profile.get("email") or f"{username}@example.com",
            password_hash=manager._hash_password(placeholder),
            is_active=True,
        )
        session.add(user)
        session.flush()
        user.roles.append(role)
        session.commit()
    else:
        updated = False
        new_email = profile.get("email")
        if new_email and new_email != user.email:
            user.email = new_email
            updated = True
        if not user.is_active:
            return None
        if updated:
            session.commit()
    return user.id


def change_password(manager, user_id: int, current_password: str, new_password: str) -> bool:
    """Change a user's password."""
    try:
        user = manager.get_user_by_id(user_id)
        if not user:
            return False

        if not manager._verify_password(user.password_hash, current_password):
            return False

        session = manager._get_session()
        user.password_hash = manager._hash_password(new_password)
        session.commit()
        return True
    except Exception as e:
        session = manager._get_session()
        session.rollback()
        logger.error("Error changing password: %s", e)
        return False


def generate_reset_token(manager, username: str) -> bool:
    """Generate a password reset token."""
    try:
        user = manager.get_user_by_username(username)
        if not user:
            return False

        token = secrets.token_urlsafe(32)
        session = manager._get_session()
        user.reset_token = token
        user.reset_token_expiry = datetime.datetime.utcnow() + datetime.timedelta(hours=24)
        session.commit()
        return True
    except Exception as e:
        session = manager._get_session()
        session.rollback()
        logger.error("Error generating reset token: %s", e)
        return False


def verify_reset_token(manager, token: str) -> Optional[int]:
    """Verify a password reset token."""
    try:
        session = manager._get_session()
        user = session.query(User).filter(
            (User.reset_token == token) & (User.reset_token_expiry > datetime.datetime.utcnow())
        ).first()
        if not user:
            return None
        return user.id
    except Exception as e:
        logger.error("Error verifying reset token: %s", e)
        return None


def reset_password(manager, user_id: int, new_password: str) -> bool:
    """Reset a user's password using a token."""
    try:
        user = manager.get_user_by_id(user_id)
        if not user:
            return False

        session = manager._get_session()
        user.password_hash = manager._hash_password(new_password)
        user.reset_token = None
        user.reset_token_expiry = None
        session.commit()
        return True
    except Exception as e:
        session = manager._get_session()
        session.rollback()
        logger.error("Error resetting password: %s", e)
        return False
