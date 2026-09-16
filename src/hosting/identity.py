"""Identity handed from the platform to a hosted app.

The app never authenticates anyone. The parent verifies the session (or API key,
or bearer token), then tells the app who is calling. Two properties matter:

* **The platform's session cookie never crosses the boundary.** The app cannot
  replay the user's session against the platform's own APIs.
* **Client-supplied identity headers are stripped before the parent's are set**,
  so a browser cannot pretend to be someone else.

Requests reach an app over a private unix socket owned by the parent, which is
the real trust boundary; the HMAC is defense in depth for the day that socket is
reachable by something else.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from typing import Mapping

from src.hosting import settings

#: Headers the parent owns. Anything with these names arriving from a client is
#: discarded rather than forwarded.
IDENTITY_HEADERS = (
    "X-Auth-User-Id",
    "X-Auth-Username",
    "X-Auth-Roles",
    "X-Auth-Ts",
    "X-Auth-Sig",
)

#: Minutes a signature stays valid, so a captured request cannot be replayed
#: indefinitely by a process that can reach the socket.
SIGNATURE_TTL_SECONDS = 300


def _canonical(user_id: str, username: str, roles: str, issued_at: str) -> bytes:
    return "|".join((user_id, username, roles, issued_at)).encode()


def sign(user_id: str, username: str, roles: str, issued_at: str) -> str:
    return hmac.new(settings.identity_secret(), _canonical(user_id, username, roles, issued_at), hashlib.sha256).hexdigest()


def build_headers(user_id, username: str, roles: str) -> dict[str, str]:
    """The identity headers for one request."""
    issued_at = str(int(time.time()))
    user_id = str(user_id or "")
    username = username or ""
    roles = roles or ""
    return {
        "X-Auth-User-Id": user_id,
        "X-Auth-Username": username,
        "X-Auth-Roles": roles,
        "X-Auth-Ts": issued_at,
        "X-Auth-Sig": sign(user_id, username, roles, issued_at),
    }


def verify(headers: Mapping[str, str]) -> dict | None:
    """Verify a signed identity, for an app that chooses to check it.

    Returns the identity, or ``None`` when the signature is missing, wrong or
    stale. Apps that simply read the headers still work; this exists so an app
    that cares can be certain.
    """
    user_id = headers.get("X-Auth-User-Id", "")
    username = headers.get("X-Auth-Username", "")
    roles = headers.get("X-Auth-Roles", "")
    issued_at = headers.get("X-Auth-Ts", "")
    signature = headers.get("X-Auth-Sig", "")
    if not user_id or not issued_at or not signature:
        return None
    try:
        if abs(time.time() - int(issued_at)) > SIGNATURE_TTL_SECONDS:
            return None
    except ValueError:
        return None
    expected = sign(user_id, username, roles, issued_at)
    if not hmac.compare_digest(signature, expected):
        return None
    return {"user_id": user_id, "username": username, "roles": roles, "issued_at": issued_at}
