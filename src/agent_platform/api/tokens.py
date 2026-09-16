"""Signed access tokens (JWT) for API clients.

Issued by ``POST /login`` (JSON) and accepted as
``Authorization: Bearer <token>`` alongside the session cookie. Tokens are
stateless and carry only the user id, so role and agent-access changes take
effect immediately (authorization is always re-resolved per request).
"""

from __future__ import annotations

import time

import jwt
from flask import current_app, has_app_context

TOKEN_TTL_SECONDS = 12 * 60 * 60
_ALGORITHM = "HS256"
_ISSUER = "agent-platform"


def _secret() -> str:
    if has_app_context():
        return str(current_app.config.get("SECRET_KEY") or "")
    return ""


def issue_access_token(user_id: int, ttl: int = TOKEN_TTL_SECONDS) -> str:
    now = int(time.time())
    payload = {
        "sub": str(int(user_id)),
        "iat": now,
        "exp": now + int(ttl),
        "iss": _ISSUER,
    }
    return jwt.encode(payload, _secret(), algorithm=_ALGORITHM)


def read_access_token(token: str | None) -> int | None:
    if not token:
        return None
    try:
        payload = jwt.decode(
            token,
            _secret(),
            algorithms=[_ALGORITHM],
            issuer=_ISSUER,
            options={"require": ["exp", "sub"]},
        )
        return int(payload["sub"])
    except Exception:
        return None
