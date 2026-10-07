"""User management utility facade for Text2SQL application.

Delegates responsibilities to focused modules under ``src.utils.users``:
- Authentication & passwords: ``src.utils.users.user_auth``
- User CRUD & escalation guards: ``src.utils.users.user_lifecycle``
- Roles & associations: ``src.utils.users.role_manager``
- Module permissions: ``src.utils.users.module_permissions``
"""

from src.utils.database import get_db_session
from src.utils.users.module_permissions import (
    check_endpoint_access as _check_endpoint_access,
    get_all_endpoints as _get_all_endpoints,
    get_role_module_levels as _get_role_module_levels,
    get_role_modules as _get_role_modules,
    get_user_module_levels as _get_user_module_levels,
    get_user_modules as _get_user_modules,
    has_any_module_access as _has_any_module_access,
    has_module_access as _has_module_access,
    module_levels_from_permissions as _module_levels_from_permissions,
    sync_endpoint_permissions as _sync_endpoint_permissions,
    sync_module_permissions as _sync_module_permissions,
)
from src.utils.users.role_manager import (
    add_user_to_role as _add_user_to_role,
    create_role as _create_role,
    delete_role as _delete_role,
    get_all_permissions as _get_all_permissions,
    get_all_roles as _get_all_roles,
    get_role_by_id as _get_role_by_id,
    get_role_count as _get_role_count,
    get_role_permissions as _get_role_permissions,
    has_permission as _has_permission,
    has_role as _has_role,
    initialize_roles_permissions as _initialize_roles_permissions,
    remove_user_from_role as _remove_user_from_role,
    update_role as _update_role,
    update_role_modules as _update_role_modules,
    update_role_permissions as _update_role_permissions,
)
from src.utils.users.user_auth import (
    authenticate as _authenticate,
    authenticate_ldap as _authenticate_ldap,
    change_password as _change_password,
    generate_reset_token as _generate_reset_token,
    hash_password as _hash_password_fn,
    reset_password as _reset_password,
    verify_password as _verify_password_fn,
    verify_reset_token as _verify_reset_token,
)
from src.utils.users.user_lifecycle import (
    ADMIN_ROLE_NAME,
    BUILTIN_ADMIN_USERNAME,
    EscalationGuardError,
    _LEGACY_ROLE_GRANT_TABLES,
    _USER_OWNED_ASSETS,
    _USER_PRIVATE_DELETES,
    _USER_PROVENANCE_NULLS,
    active_admin_count as _active_admin_count_fn,
    assert_user_deletable as _assert_user_deletable_fn,
    assert_user_update_allowed as _assert_user_update_allowed_fn,
    create_user as _create_user,
    delete_user as _delete_user,
    existing_tables as _existing_tables_fn,
    get_all_users as _get_all_users,
    get_user_by_id as _get_user_by_id,
    get_user_by_username as _get_user_by_username,
    get_user_count as _get_user_count,
    get_username_by_id as _get_username_by_id,
    guard_builtin_admin as _guard_builtin_admin_fn,
    guard_last_active_admin as _guard_last_active_admin_fn,
    is_admin_account as _is_admin_account_fn,
    purge_user_private_data as _purge_user_private_data_fn,
    transfer_owned_assets as _transfer_owned_assets_fn,
    update_user as _update_user,
)


class UserManager:
    """Manages users, roles, permissions, and audit logs."""

    def __init__(self):
        pass

    def _get_session(self):
        return get_db_session()

    @staticmethod
    def _is_admin_account(user) -> bool:
        return _is_admin_account_fn(user)

    def _active_admin_count(self, session, exclude_user_id=None) -> int:
        return _active_admin_count_fn(self, session, exclude_user_id)

    def _guard_builtin_admin(self, user, action: str) -> None:
        _guard_builtin_admin_fn(user, action)

    def _guard_last_active_admin(self, session, user) -> None:
        _guard_last_active_admin_fn(self, session, user)

    def assert_user_deletable(self, user) -> None:
        _assert_user_deletable_fn(self, user)

    def assert_user_update_allowed(self, user, *, is_active=None, role_ids=None) -> None:
        _assert_user_update_allowed_fn(self, user, is_active=is_active, role_ids=role_ids)

    @staticmethod
    def _existing_tables(session) -> set[str]:
        return _existing_tables_fn(session)

    def _purge_user_private_data(self, session, user_id: int) -> None:
        _purge_user_private_data_fn(session, user_id)

    def _transfer_owned_assets(self, session, user_id: int) -> None:
        _transfer_owned_assets_fn(session, user_id)

    def _hash_password(self, password):
        return _hash_password_fn(self, password)

    def _verify_password(self, password_hash, password):
        return _verify_password_fn(self, password_hash, password)

    def authenticate(self, username, password):
        return _authenticate(self, username, password)

    def _authenticate_ldap(self, username: str, password: str):
        return _authenticate_ldap(self, username, password)

    def create_user(self, username, email, password):
        return _create_user(self, username, email, password)

    def update_user(self, user_id, username, email, password=None, is_active=None):
        return _update_user(self, user_id, username, email, password=password, is_active=is_active)

    def delete_user(self, user_id):
        return _delete_user(self, user_id)

    def get_user_by_id(self, user_id):
        return _get_user_by_id(self, user_id)

    def get_user_by_username(self, username):
        return _get_user_by_username(self, username)

    def get_all_users(self):
        return _get_all_users(self)

    def get_user_count(self):
        return _get_user_count(self)

    def get_username_by_id(self, user_id):
        return _get_username_by_id(self, user_id)

    def change_password(self, user_id, current_password, new_password):
        return _change_password(self, user_id, current_password, new_password)

    def generate_reset_token(self, username):
        return _generate_reset_token(self, username)

    def verify_reset_token(self, token):
        return _verify_reset_token(self, token)

    def reset_password(self, user_id, new_password):
        return _reset_password(self, user_id, new_password)

    def get_all_roles(self):
        return _get_all_roles(self)

    def get_role_count(self):
        return _get_role_count(self)

    def has_role(self, user_id, role_name):
        return _has_role(self, user_id, role_name)

    def add_user_to_role(self, user_id, role_id):
        return _add_user_to_role(self, user_id, role_id)

    def remove_user_from_role(self, user_id, role_id):
        return _remove_user_from_role(self, user_id, role_id)

    def get_role_by_id(self, role_id):
        return _get_role_by_id(self, role_id)

    def create_role(self, name, description=""):
        return _create_role(self, name, description=description)

    def update_role(self, role_id, name, description):
        return _update_role(self, role_id, name, description)

    def delete_role(self, role_id):
        return _delete_role(self, role_id)

    def get_role_modules(self, role_id):
        return _get_role_modules(self, role_id)

    def get_role_module_levels(self, role_id) -> dict[str, str]:
        return _get_role_module_levels(self, role_id)

    def get_role_permissions(self, role_id):
        return _get_role_permissions(self, role_id)

    def update_role_permissions(self, role_id, permission_ids):
        return _update_role_permissions(self, role_id, permission_ids)

    def update_role_modules(self, role_id, module_assignments):
        return _update_role_modules(self, role_id, module_assignments)

    def get_all_permissions(self):
        return _get_all_permissions(self)

    def has_permission(self, user_id, permission_name):
        return _has_permission(self, user_id, permission_name)

    def initialize_roles_permissions(self):
        return _initialize_roles_permissions(self)

    @staticmethod
    def _module_levels_from_permissions(permissions) -> dict[str, str]:
        return _module_levels_from_permissions(permissions)

    def get_user_module_levels(self, user_id) -> dict[str, str]:
        return _get_user_module_levels(self, user_id)

    def get_user_modules(self, user_id) -> set[str]:
        return _get_user_modules(self, user_id)

    def has_module_access(self, user_id, module_key: str, min_level: str = "read") -> bool:
        return _has_module_access(self, user_id, module_key, min_level=min_level)

    def has_any_module_access(self, user_id, module_keys, min_level: str = "read") -> bool:
        return _has_any_module_access(self, user_id, module_keys, min_level=min_level)

    def sync_module_permissions(self):
        return _sync_module_permissions(self)

    def get_all_endpoints(self):
        return _get_all_endpoints(self)

    def check_endpoint_access(self, user_id, endpoint):
        return _check_endpoint_access(self, user_id, endpoint)

    def sync_endpoint_permissions(self):
        return _sync_endpoint_permissions(self)


__all__ = [
    "UserManager",
    "ADMIN_ROLE_NAME",
    "BUILTIN_ADMIN_USERNAME",
    "EscalationGuardError",
    "_USER_PRIVATE_DELETES",
    "_USER_PROVENANCE_NULLS",
    "_USER_OWNED_ASSETS",
    "_LEGACY_ROLE_GRANT_TABLES",
]
