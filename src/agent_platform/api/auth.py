"""Session cookie, API key, or signed access-token auth — one user identity.

Credentials are read from ``X-API-Key`` or ``Authorization: Bearer <value>``.
A ``Bearer`` value may be either an API key (``apk_...``) or a JWT access
token minted by ``POST /login``. All three paths resolve to the same user id
so RBAC, agent role access and audit behave identically.
"""

from __future__ import annotations

from functools import wraps
from typing import Callable

from flask import g, jsonify, request, session

from src.agent_platform.api.tokens import read_access_token
from src.agent_platform.execution.api_keys import ApiKeyStore


def _extract_credential() -> str | None:
    header = request.headers.get("X-API-Key") or request.headers.get("Authorization") or ""
    if header.lower().startswith("bearer "):
        header = header[7:]
    header = header.strip()
    return header or None


def current_user_id() -> int | None:
    return getattr(g, "user_id", None) or session.get("user_id")


def api_auth_required(*required_scopes: str):
    def decorator(fn: Callable):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            raw = _extract_credential()
            if raw:
                key = ApiKeyStore.verify(raw)
                if key is not None:
                    # Identify the caller before any rejection so denied API
                    # calls are still attributed in the audit trail.
                    g.user_id = key.get("user_id")
                    g.auth_type = "api_key"
                    scopes = set(key.get("scopes") or [])
                    if required_scopes and not set(required_scopes).issubset(scopes):
                        g.audit_reason = "api key missing required scope"
                        return jsonify({"error": "missing scope"}), 403
                    g.api_key_id = key.get("id")
                    g.api_key_scopes = list(scopes)
                    return fn(*args, **kwargs)

                token_user = read_access_token(raw)
                if token_user is not None:
                    g.user_id = token_user
                    g.auth_type = "token"
                    return fn(*args, **kwargs)

                g.audit_reason = "invalid api key or token"
                return jsonify({"error": "invalid api key or token"}), 401

            user_id = session.get("user_id")
            if user_id:
                g.user_id = user_id
                g.auth_type = "session"
                return fn(*args, **kwargs)
            g.audit_reason = "authentication required"
            return jsonify({"error": "unauthorized"}), 401

        return wrapper

    return decorator
