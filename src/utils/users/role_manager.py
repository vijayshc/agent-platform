"""Role and permission management operations."""

import logging
from sqlalchemy import text
from src.models.user import Permission, Role, User
from src.utils.users.user_lifecycle import (
    ADMIN_ROLE_NAME,
    EscalationGuardError,
    _LEGACY_ROLE_GRANT_TABLES,
    existing_tables,
    guard_last_active_admin,
)

logger = logging.getLogger("text2sql.role_manager")


def get_all_roles(manager):
    """Get all roles."""
    session = manager._get_session()
    return session.query(Role).all()


def get_role_count(manager):
    """Get total role count."""
    session = manager._get_session()
    return session.query(Role).count()


def has_role(manager, user_id, role_name):
    """Check if a user has a specific role."""
    try:
        user = manager.get_user_by_id(user_id)
        if not user:
            return False
        for role in user.roles:
            if role.name == role_name:
                return True
        return False
    except Exception as e:
        logger.error("Error checking role: %s", e)
        return False


def add_user_to_role(manager, user_id, role_id):
    """Add a user to a role."""
    try:
        user = manager.get_user_by_id(user_id)
        session = manager._get_session()
        role = session.query(Role).filter(Role.id == role_id).first()
        if not user or not role:
            return False

        for existing_role in user.roles:
            if existing_role.id == role.id:
                return True

        user.roles.append(role)
        session.commit()
        return True
    except Exception as e:
        session = manager._get_session()
        session.rollback()
        logger.error("Error adding user to role: %s", e)
        return False


def remove_user_from_role(manager, user_id, role_id):
    """Remove a user from a role with admin preservation guard."""
    try:
        user = manager.get_user_by_id(user_id)
        session = manager._get_session()
        role = session.query(Role).filter(Role.id == role_id).first()
        if not user or not role:
            return False

        has_target_role = any(existing.id == role.id for existing in user.roles)
        if not has_target_role:
            return True

        if role.name == ADMIN_ROLE_NAME:
            guard_last_active_admin(manager, session, user)

        user.roles.remove(role)
        session.commit()
        return True
    except EscalationGuardError:
        session = manager._get_session()
        session.rollback()
        raise
    except Exception as e:
        session = manager._get_session()
        session.rollback()
        logger.error("Error removing user from role: %s", e)
        return False


def get_role_by_id(manager, role_id):
    """Get a role by its ID."""
    try:
        session = manager._get_session()
        return session.query(Role).filter(Role.id == role_id).first()
    except Exception as e:
        logger.error("Error getting role by ID: %s", e)
        return None


def create_role(manager, name, description=""):
    """Create a new role."""
    try:
        session = manager._get_session()
        existing_role = session.query(Role).filter(Role.name == name).first()
        if existing_role:
            raise ValueError(f"Role '{name}' already exists")

        role = Role(name=name, description=description)
        session.add(role)
        session.commit()
        return role.id
    except Exception as e:
        session = manager._get_session()
        session.rollback()
        logger.error("Error creating role: %s", e)
        raise


def update_role(manager, role_id, name, description):
    """Update an existing role."""
    try:
        role = manager.get_role_by_id(role_id)
        if not role:
            raise ValueError("Role not found")
        if role.name == "admin" and name != "admin":
            raise ValueError("Cannot rename the built-in admin role")

        session = manager._get_session()
        existing_role = session.query(Role).filter(
            (Role.name == name) & (Role.id != role_id)
        ).first()
        if existing_role:
            raise ValueError(f"Role name '{name}' already exists")

        role.name = name
        role.description = description
        session.commit()
        return True
    except Exception as e:
        session = manager._get_session()
        session.rollback()
        logger.error("Error updating role: %s", e)
        raise


def delete_role(manager, role_id):
    """Delete a role, preventing deletion of admin and purging role access grants."""
    try:
        role = manager.get_role_by_id(role_id)
        if not role:
            raise ValueError("Role not found")
        if role.name == "admin":
            raise ValueError("Cannot delete the built-in admin role")

        session = manager._get_session()
        for user in role.users:
            user.roles.remove(role)

        session.execute(
            text("DELETE FROM resource_role_access WHERE role_id = :role_id"),
            {"role_id": role_id},
        )

        tables = existing_tables(session)
        for table in _LEGACY_ROLE_GRANT_TABLES:
            if table in tables:
                session.execute(
                    text(f"DELETE FROM {table} WHERE role_id = :role_id"),
                    {"role_id": role_id},
                )

        session.delete(role)
        session.commit()
        return True
    except Exception as e:
        session = manager._get_session()
        session.rollback()
        logger.error("Error deleting role: %s", e)
        raise


def get_role_permissions(manager, role_id):
    """Get permission rows for a role."""
    role = manager.get_role_by_id(role_id)
    if not role:
        raise ValueError("Role not found")
    return role.permissions


def update_role_permissions(manager, role_id, permission_ids):
    """Legacy permission-id update."""
    from src.auth.modules import MODULE_BY_KEY, MODULE_PERMISSION_NAMES, split_module_permission

    session = manager._get_session()
    role = session.query(Role).filter(Role.id == role_id).first()
    if not role:
        raise ValueError("Role not found")
    if role.name == "admin":
        raise ValueError("Cannot modify admin role permissions")

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
        if key not in selected or level == "write":
            selected[key] = perm

    role.permissions = [selected[m.key] for m in MODULE_BY_KEY.values() if m.key in selected]
    try:
        session.commit()
        return True
    except Exception:
        session.rollback()
        raise


def update_role_modules(manager, role_id, module_assignments):
    """Replace a role's permissions with explicit module/access assignments."""
    from src.auth.modules import ACCESS_READ, get_module, normalize_module_assignments

    role = manager.get_role_by_id(role_id)
    if not role:
        raise ValueError("Role not found")
    if role.name == "admin":
        raise ValueError("Cannot modify admin role permissions")

    requested = normalize_module_assignments(module_assignments)
    session = manager._get_session()
    role = session.query(Role).filter(Role.id == role_id).first()

    wanted_names = []
    for key, level in requested.items():
        module = session.query(Permission).filter(
            Permission.name.in_([f"module:{key}", f"module:{key}:{ACCESS_READ}"])
        ).all()
        by_name = {p.name: p for p in module}
        write_perm = by_name.get(f"module:{key}")
        if write_perm is None:
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


def get_all_permissions(manager):
    """Get all persisted permissions."""
    session = manager._get_session()
    return session.query(Permission).all()


def has_permission(manager, user_id, permission_name):
    """Return whether a user directly or indirectly holds a permission name."""
    try:
        if not user_id:
            return False
        user = manager.get_user_by_id(user_id)
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
        logger.error("Error checking permission: %s", e)
        return False


def initialize_roles_permissions(manager):
    """Initialize default admin/user roles and module permissions."""
    try:
        from src.auth.modules import MODULES

        session = manager._get_session()
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

        admin_role = session.query(Role).filter(Role.name == "admin").first()
        if not admin_role:
            admin_role = Role(name="admin", description="Administrator with full access")
            session.add(admin_role)
            session.flush()
        admin_role.permissions = list(module_perms)

        user_role = session.query(Role).filter(Role.name == "user").first()
        if not user_role:
            user_role = Role(name="user", description="Standard user")
            session.add(user_role)
            session.flush()
        user_role.permissions = []

        admin_user = session.query(User).filter(User.username == "admin").first()
        if not admin_user:
            admin_user = User(
                username="admin",
                email="admin@example.com",
                password_hash=manager._hash_password("admin123"),
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
            session = manager._get_session()
            session.rollback()
        except Exception:
            pass
        logger.error("Error initializing roles and permissions: %s", e)
        return False
