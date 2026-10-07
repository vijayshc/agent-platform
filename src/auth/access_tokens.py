"""The app's signed access tokens (JWT) - mint one place, verify one place.

One artifact identifies a caller everywhere: ``POST /login`` hands one to API
clients, the MCP connection builder mints one for the user a run belongs to, and
both the platform API and a platform-served MCP server's gate
(:mod:`src.mcp_server_auth`) verify it with :func:`read_claims`.

It is a leaf module on purpose - only ``jwt`` and the app's ``SECRET_KEY`` - so
an MCP server process can verify a token without importing the platform.

Tokens are stateless and carry the user id and name, so role and access changes
take effect immediately (authorization is always re-resolved per request).
"""

from __future__ import annotations

import time

import jwt

TOKEN_TTL_SECONDS = 12 * 60 * 60
ALGORITHM = "HS256"
ISSUER = "agent-platform"
#: Claims a token must carry to be accepted at all.
REQUIRED_CLAIMS = ("exp", "sub")


def secret() -> str:
    """The signing secret: the app's ``SECRET_KEY``, in any process.

    ``config.config`` is the one place it is read from (the environment, or the
    documented development default), and the app config is seeded from that same
    value - so an MCP server verifying a token in its own process verifies with
    the key the app minted it with. Deliberately not ``current_app``: that would
    make verification depend on a Flask application context the server does not
    have.
    """
    from config.config import SECRET_KEY

    return str(SECRET_KEY or "")


def issue_access_token(
    user_id: int, ttl: int = TOKEN_TTL_SECONDS, *, username: str | None = None
) -> str:
    """A fresh token for ``user_id``, naming ``username`` when one is given."""
    now = int(time.time())
    payload: dict[str, object] = {
        "sub": str(int(user_id)),
        "iat": now,
        "exp": now + int(ttl),
        "iss": ISSUER,
    }
    if username:
        payload["username"] = str(username)
    return jwt.encode(payload, secret(), algorithm=ALGORITHM)


def read_claims(token: str | None) -> dict | None:
    """Every verified claim in ``token``, or ``None``.

    The single verifier for this token family: signature, issuer and expiry.
    ``None`` (never an exception) means "not a valid token", so no caller can
    mistake a rejected token for an accepted one.
    """
    if not token:
        return None
    try:
        return jwt.decode(
            token,
            secret(),
            algorithms=[ALGORITHM],
            issuer=ISSUER,
            options={"require": list(REQUIRED_CLAIMS)},
        )
    except jwt.PyJWTError:
        return None


def read_access_token(token: str | None) -> int | None:
    """The user id in a valid token, or ``None``."""
    payload = read_claims(token)
    if payload is None:
        return None
    try:
        return int(payload["sub"])
    except (KeyError, TypeError, ValueError):
        return None
