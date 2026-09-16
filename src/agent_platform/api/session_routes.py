"""Session/profile endpoint for the React agent app.

Exposes the current user's identity, roles, and the administration navigation
items they are actually allowed to access.  Navigation is derived from the
canonical module catalog (``src.auth.modules``), so the React client never
duplicates authorization logic.
"""

from __future__ import annotations

from flask import Blueprint, jsonify

from src.agent_platform.api.auth import api_auth_required, current_user_id
from src.auth.modules import MODULES

session_bp = Blueprint("session_api", __name__)


@session_bp.get("/me")
@api_auth_required()
def get_me():
    user_id = current_user_id()
    from src.utils.user_manager import UserManager

    um = UserManager()
    user = um.get_user_by_id(user_id) if user_id else None
    is_admin = um.has_role(user_id, "admin") if user_id else False

    module_levels = um.get_user_module_levels(user_id) if user_id else {}
    # Administrator-only capabilities (vector db, database, file browser, users,
    # roles) are hidden from non-admins even when a role explicitly grants them:
    # the server denies them regardless, so advertising one would only render a
    # dead nav link and mislead the role editor.
    visible_modules = [m for m in MODULES if is_admin or not m.admin_only]
    visible_keys = {m.key for m in visible_modules}
    module_levels = {k: v for k, v in module_levels.items() if k in visible_keys}
    accessible_modules = [m for m in visible_modules if m.key in module_levels]
    admin_menu = [m.as_nav_item() for m in accessible_modules]

    roles = [r.name for r in user.roles] if user else []

    from config.config import (
        AUTH_PROVIDER,
        BROWSER_LLM_PROXY_ENABLED,
        BROWSER_LLM_PROXY_URL,
        BROWSER_LLM_PROXY_TIMEOUT_SECONDS,
    )

    return jsonify(
        {
            "user_id": user_id,
            "username": user.username if user else None,
            "email": user.email if user else None,
            "is_admin": is_admin,
            "roles": roles,
            "modules": [m.key for m in accessible_modules],
            "module_levels": module_levels,
            "admin_menu": admin_menu,
            "auth_provider": AUTH_PROVIDER,
            "is_ldap": AUTH_PROVIDER == "ldap",
            "browser_llm_proxy_enabled_default": BROWSER_LLM_PROXY_ENABLED,
            "browser_llm_proxy_url": BROWSER_LLM_PROXY_URL,
            "browser_llm_proxy_timeout": BROWSER_LLM_PROXY_TIMEOUT_SECONDS,
        }
    )
