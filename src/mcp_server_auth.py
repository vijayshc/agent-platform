"""The gate every platform-served MCP server runs behind.

A platform-served MCP server is an ordinary streamable-HTTP endpoint on
``127.0.0.1`` (``scripts/mcp_http_service.py``, autostarted from a catalog row's
``config.service``). The app authenticates it with the same artifact it
authenticates a user with: its own access token, minted for the user the run
belongs to and presented as ``Authorization: Bearer <token>`` on every request
(``src.agent_platform.plugins.mcp.connection``).

This module is the whole server side of that contract:

* verify the token with the app's one verifier (:mod:`src.auth.access_tokens`);
* refuse anything else ``401`` *before* the MCP application runs, so
  ``initialize``, ``tools/list`` and ``tools/call`` are all covered and a tool
  cannot forget to authenticate;
* publish the verified caller for the tool body, so a tool can name the user
  whose run it is::

      from src.mcp_server_auth import current_caller

      @mcp.tool()
      def who_is_calling() -> str:
          return current_caller().username
"""

from __future__ import annotations

import contextvars
import logging
from dataclasses import dataclass
from typing import Any

from src.auth.access_tokens import read_claims

logger = logging.getLogger("mcp_server_auth")

#: Served without a token, so a supervisor can tell "listening" from "authorized".
HEALTH_PATH = "/healthz"

_BEARER_PREFIX = "bearer "

_caller: contextvars.ContextVar["Caller | None"] = contextvars.ContextVar(
    "mcp_caller", default=None
)


class MissingCaller(RuntimeError):
    """A tool asked for the caller outside a gated request."""


@dataclass(frozen=True)
class Caller:
    """The verified app user behind an MCP request."""

    user_id: int
    username: str


def current_caller() -> Caller:
    """The verified caller of the request being handled.

    Every request reaches a tool through the gate, which refuses a request it
    cannot verify, so a tool body always has a caller. Asking outside a request
    is a bug and raises rather than returning an empty identity.
    """
    caller = _caller.get()
    if caller is None:
        raise MissingCaller(
            "no verified caller: this tool runs only behind the MCP gate "
            "(serve the module with scripts/mcp_http_service.py)"
        )
    return caller


def bearer_token(header_value: Any) -> str:
    """The token inside an ``Authorization: Bearer <token>`` header (else ``''``)."""
    value = str(header_value or "").strip()
    if value.lower().startswith(_BEARER_PREFIX):
        return value[len(_BEARER_PREFIX):].strip()
    return ""


def caller_from_token(token: str | None) -> Caller | None:
    """The caller ``token`` names, or ``None`` when it is not a valid app token.

    A token without a username cannot name the run's user, and every token the
    app hands an MCP server carries one, so it is refused like any other
    credential that does not prove who is calling.
    """
    claims = read_claims(token)
    if not claims:
        return None
    try:
        return Caller(user_id=int(claims["sub"]), username=str(claims["username"]))
    except (KeyError, TypeError, ValueError):
        return None


def require_bearer(app: Any) -> Any:
    """Wrap an ASGI app so only a valid app token reaches it."""

    async def guarded(scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await app(scope, receive, send)
            return
        if scope.get("path") == HEALTH_PATH:
            await _plain(send, 200, b"ok")
            return
        headers = {key.lower(): value for key, value in scope.get("headers") or []}
        presented = headers.get(b"authorization", b"").decode("latin-1")
        caller = caller_from_token(bearer_token(presented))
        if caller is None:
            logger.warning("Refused unauthenticated MCP request to %s", scope.get("path"))
            await _plain(send, 401, b"unauthorized")
            return
        token = _caller.set(caller)
        try:
            await app(scope, receive, send)
        finally:
            _caller.reset(token)

    return guarded


async def _plain(send: Any, status: int, body: bytes) -> None:
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"text/plain"),
                (b"content-length", str(len(body)).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})
