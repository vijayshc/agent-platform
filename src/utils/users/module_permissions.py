"""Module and endpoint access control evaluation and synchronization."""

import logging
from src.models.user import Permission, Role

logger = logging.getLogger("text2sql.module_permissions")


def module_levels_from_permissions(permissions) -> dict[str, str]:
    """Extract module key to level mapping from a collection of permissions."""
    from src.auth.modules import (
        ACCESS_READ,
        ACCESS_WRITE,
        MODULE_BY_KEY,
        module_key_from_permission,
        module_level_from_permission,
    )

    found: dict[str, str] = {}
    for perm in permissions or []:
        key = module_key_from_permission(getattr(perm, "name", None))
        level = module_level_from_permission(getattr(perm, "name", None))
        if not key or level not in {ACCESS_READ, ACCESS_WRITE}:
            continue
        if found.get(key) == ACCESS_WRITE:
            continue
        found[key] = level
    return {m.key: found[m.key] for m in MODULE_BY_KEY.values() if m.key in found}


def get_role_module_levels(manager, role_id) -> dict[str, str]:
    """Return ``{module_key: "read"|"write"}`` for a role."""
    role = manager.get_role_by_id(role_id)
    if not role:
        return {}
    if role.name == "admin":
        from src.auth.modules import ACCESS_WRITE, MODULE_KEYS
        return {key: ACCESS_WRITE for key in MODULE_KEYS}
    return module_levels_from_permissions(role.permissions)


def get_role_modules(manager, role_id) -> set[str]:
    """Set of module keys a role has access to."""
    return set(get_role_module_levels(manager, role_id).keys())


def get_user_module_levels(manager, user_id) -> dict[str, str]:
    """Return ``{module_key: "read"|"write"}`` for a user."""
    from src.auth.modules import ACCESS_WRITE, MODULE_KEYS

    if not user_id:
        return {}
    try:
        user = manager.get_user_by_id(user_id)
        if not user:
            return {}
        levels: dict[str, str] = {}
        for role in user.roles:
            if role.name == "admin":
                return {key: ACCESS_WRITE for key in MODULE_KEYS}
            for key, level in module_levels_from_permissions(role.permissions).items():
                if level == ACCESS_WRITE or levels.get(key) != ACCESS_WRITE:
                    levels[key] = level
        return levels
    except Exception as e:
        logger.error("Error resolving user module levels: %s", e)
        return {}


def get_user_modules(manager, user_id) -> set[str]:
    """Return every module key the user can access."""
    return set(get_user_module_levels(manager, user_id).keys())


def has_module_access(manager, user_id, module_key: str, min_level: str = "read") -> bool:
    """Whether a user meets min_level (read/write) for a module."""
    from src.auth.modules import level_allows

    if not user_id or not module_key:
        return False
    return level_allows(get_user_module_levels(manager, user_id).get(str(module_key).strip().lower()), min_level)


def has_any_module_access(manager, user_id, module_keys, min_level: str = "read") -> bool:
    """Whether a user meets min_level for any of module_keys."""
    if not user_id:
        return False
    requested = {str(k).strip().lower() for k in (module_keys or ()) if str(k).strip()}
    if not requested:
        return False
    from src.auth.modules import level_allows

    levels = get_user_module_levels(manager, user_id)
    return any(level_allows(levels.get(key), min_level) for key in requested)


def sync_module_permissions(manager):
    """Synchronise permission rows and role assignments with the module catalog."""
    try:
        from flask import current_app
        has_app_context = bool(current_app)
    except RuntimeError:
        has_app_context = False
    if not has_app_context:
        return

    from src.auth.modules import MODULES, MODULE_PERMISSION_NAMES, split_module_permission
    from src.models.user import role_permission_association

    session = manager._get_session()
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
                    existing_index = next(
                        i for i, p in enumerate(keep)
                        if split_module_permission(p.name)[0] == key
                    )
                    if level == "write":
                        keep[existing_index] = perm
                    continue
                if level in {"read", "write"}:
                    keep.append(perm)
                    seen.add(key)
            role.permissions = keep
        session.flush()

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


def get_all_endpoints(manager):
    """Get all registered endpoints from the Flask app."""
    from flask import current_app
    endpoints = []
    if current_app:
        for rule in current_app.url_map.iter_rules():
            if rule.endpoint not in endpoints:
                endpoints.append(rule.endpoint)
    return sorted(endpoints)


def check_endpoint_access(manager, user_id, endpoint):
    """Check if a user has access to a specific endpoint."""
    public_endpoints = ["static", "auth.login", "auth.logout", "auth.reset_password_request", "auth.reset_password"]
    if endpoint in public_endpoints:
        return True

    if not user_id:
        return False

    user = manager.get_user_by_id(user_id)
    if not user:
        return False

    if manager.has_role(user_id, "admin"):
        return True

    allowed_endpoints = set()
    for role in user.roles:
        for permission in role.permissions:
            allowed_endpoints.add(permission.name)

    if "*" in allowed_endpoints:
        return True

    if endpoint in allowed_endpoints:
        return True

    parts = endpoint.split(".")
    if len(parts) > 1 and f"{parts[0]}.*" in allowed_endpoints:
        return True

    return False


def sync_endpoint_permissions(manager):
    """Deprecated compatibility alias for sync_module_permissions."""
    sync_module_permissions(manager)
