"""LLM connection manager.

Admin-configurable OpenAI-compatible provider connections. The operator creates
connections through the admin LLM Manager page; the agent builder and the main
LLM engine resolve a connection (or the default one) and build a chat client
from it at runtime.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from src.auth import resource_access
from src.models.llm_connection import LLMConnection
from src.models.secrets import MASK, mask_value, merge_masked, unwrap_dict, unwrap_value
from src.utils.provider_diagnostics import (
    ProviderResponseError,
    assistant_reply,
    describe_failure,
    redact,
)

logger = logging.getLogger("text2sql.llm_connection_manager")

# Sent with every provider request unless the operator overrides the header.
_BASE_HEADERS = {"Accept-Encoding": "gzip, deflate"}

# Identifiers that mean "whatever the operator marked as default".
DEFAULT_IDENTIFIERS = frozenset({"", "default"})


class LLMConnectionError(RuntimeError):
    """A configured connection cannot be used (missing, unknown, or disabled)."""


def _slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")


def ensure() -> None:
    LLMConnection.create_table()


def list_connections() -> list[dict[str, Any]]:
    """Every configured connection, ordered default-first.

    Unfiltered by design: tenancy filtering belongs to the callers that know the
    acting identity. ``visible_connection_ids`` is that filter and is what the
    admin REST list and the model pickers apply.
    """
    ensure()
    out = []
    for conn in LLMConnection.get_all():
        out.append(_to_dict(conn))
    return out


# --- tenancy --------------------------------------------------------------

def visible_connection_ids(user_id: int | None) -> set[int] | None:
    """Ids of connections ``user_id`` may use.

    ``None`` means *every* connection and is returned for administrators only.
    A user with no identity gets just the deployment-level (owner-less) rows:
    they are config/env-seeded configuration shared with everyone, not tenant
    data. Tenant-owned rows require the owner, an administrator, or a granted
    role.
    """
    ensure()
    rows = [{"id": conn.id, "created_by": conn.created_by} for conn in LLMConnection.get_all()]
    if user_id is not None and resource_access.is_admin(user_id):
        return None
    if user_id is None:
        return {row["id"] for row in rows if row["created_by"] is None}
    return {row["id"] for row in resource_access.filter_visible("llm_connection", rows, user_id)}


def can_access_connection(connection_id: int, user_id: int | None) -> bool:
    """Whether ``user_id`` may select the connection ``connection_id`` for a run.

    Fails closed: an unknown/deleted connection is never accessible, and a
    tenant-owned one needs the owner, an administrator, or a granted role. A
    deployment-level row (``created_by IS NULL``) is shared, so it stays usable
    from request-less contexts such as orchestration or the default-model path.
    """
    try:
        cid = int(connection_id)
    except (TypeError, ValueError):
        return False
    conn = LLMConnection.get_by_id(cid)
    if conn is None:
        return False
    if conn.created_by is None:
        return True  # deployment-level connection, shared with every caller
    return resource_access.can_access("llm_connection", conn.id, conn.created_by, user_id)


def can_manage_connection(connection_id: int, user_id: int | None) -> bool:
    """Whether ``user_id`` may edit/delete/test/set-default the connection.

    Only the owner or an administrator. A role grant confers *use* (choosing the
    model), never destructive control; an owner-less deployment row is managed by
    administrators only.
    """
    try:
        cid = int(connection_id)
    except (TypeError, ValueError):
        return False
    if user_id is None:
        return False
    if resource_access.is_admin(user_id):
        return True
    conn = LLMConnection.get_by_id(cid)
    if conn is None or conn.created_by is None:
        return False
    try:
        return int(conn.created_by) == int(user_id)
    except (TypeError, ValueError):
        return False


#: Why a non-administrator may never change the global default: the default is
#: deployment-wide state every other user's default-model path resolves through,
#: so a tenant pointing it at a private connection would deny model selection to
#: everyone else. Tenant owners keep full use of their own connection by
#: selecting it explicitly for a run.
GLOBAL_DEFAULT_ADMIN_ONLY = (
    "Only an administrator can change the global default connection. Select your "
    "own connection explicitly for a run instead."
)


def can_set_global_default(user_id: int | None) -> bool:
    """Only an administrator may change the deployment-wide default connection."""
    return user_id is not None and resource_access.is_admin(user_id)


def _to_dict(conn: LLMConnection) -> dict[str, Any]:
    return {
        "id": conn.id,
        "name": conn.name,
        "base_url": conn.base_url,
        "api_key": MASK if conn.api_key else "",
        "api_key_masked": bool(conn.api_key),
        "model_name": conn.model_name,
        "system_instruction": conn.system_instruction,
        "extra_body": conn.extra_body or {},
        "http_headers": mask_value(conn.http_headers),
        "verify_ssl": bool(conn.verify_ssl),
        "is_default": bool(conn.is_default),
        "enabled": bool(conn.enabled),
        "created_by": conn.created_by,
    }


def get_connection(identifier: Any) -> LLMConnection | None:
    """Resolve a connection by int id, exact name, or slugified name."""
    if identifier is None or str(identifier).strip() == "":
        return None
    text = str(identifier).strip()
    if text.isdigit():
        conn = LLMConnection.get_by_id(int(text))
        if conn is not None:
            return conn
    conn = LLMConnection.get_by_name(text)
    if conn is not None:
        return conn
    slug = _slugify(text)
    for candidate in LLMConnection.get_all():
        if _slugify(candidate.name) == slug:
            return candidate
    return None


def get_default() -> LLMConnection | None:
    """The operator's default connection, or ``None`` when none is marked.

    A default the operator disabled is a configuration error, not a missing
    default: the caller asked for "the default", so silently answering with a
    different connection (or with nothing) would hide the misconfiguration.
    """
    ensure()
    conn = LLMConnection.get_default()
    if conn is not None and not conn.enabled:
        raise LLMConnectionError(
            f"The default LLM connection '{conn.name}' is disabled. Enable it in the "
            "admin LLM Manager, or mark another connection as default."
        )
    return conn


def _usage_error(identifier: Any) -> LLMConnectionError:
    text = str(identifier).strip()
    return LLMConnectionError(
        f"Unknown LLM connection '{text}'. Pick an enabled connection in the admin "
        "LLM Manager."
    )


def resolve(identifier: Any = None) -> LLMConnection:
    """Resolve an identifier to a usable, enabled connection.

    ``None``/``"default"`` resolve to the operator's default connection. An
    explicitly referenced connection must exist and be enabled: silently
    substituting the default would run the agent on a model nobody chose.
    """
    text = str(identifier or "").strip()
    if text.lower() in DEFAULT_IDENTIFIERS:
        conn = get_default()
        if conn is None:
            raise LLMConnectionError(
                "No LLM connection configured. Add a default connection in the admin "
                "LLM Manager."
            )
        return conn
    conn = get_connection(text)
    if conn is None:
        raise _usage_error(text)
    if not conn.enabled:
        raise LLMConnectionError(
            f"LLM connection '{conn.name}' is disabled. Enable it in the admin LLM "
            "Manager, or pick another model."
        )
    return conn


def options(conn: LLMConnection | None) -> dict[str, Any]:
    """Build the per-call chat options for a connection.

    The connection's only request configuration is the operator's free-form
    ``extra_body``; it is forwarded verbatim, so any OpenAI-compatible provider
    argument (``temperature``, ``max_tokens``, ``top_p``, ``presence_penalty``,
    ``top_k``, ``chat_template_kwargs``, ...) works without a code change. The
    SDK deep-merges it into the request body, where these keys also override the
    engine's own call arguments of the same name.
    """
    if conn is None or not conn.extra_body:
        return {}
    return {"extra_body": conn.extra_body}


def _apply_payload(conn: LLMConnection, payload: dict[str, Any]) -> LLMConnection:
    """Coerce an API/form payload onto a connection (or a new one).

    Masked API keys are left untouched, so the stored key survives an edit that
    doesn't change it.
    """
    conn.name = (payload.get("name") or conn.name or "").strip()
    conn.base_url = (payload.get("base_url") or conn.base_url or "").strip()
    api_key = (payload.get("api_key") or "").strip()
    if api_key and api_key != MASK:
        conn.api_key = api_key
    elif conn.api_key is None:
        conn.api_key = ""
    conn.model_name = (payload.get("model_name") or conn.model_name or "").strip()
    conn.system_instruction = _coerce_str(payload.get("system_instruction"), conn.system_instruction)
    conn.extra_body = _coerce_extra_body(payload.get("extra_body"), conn.extra_body)
    conn.http_headers = merge_masked(
        conn.http_headers,
        _coerce_dict(payload.get("http_headers"), conn.http_headers),
    )
    if "verify_ssl" in payload:
        conn.verify_ssl = LLMConnection._flag(payload.get("verify_ssl"), default=1)
    # Flags are only changed when the payload carries them: a partial update
    # (an integration calling the save API with just a new model name) must not
    # silently re-enable a connection the operator disabled.
    if "is_default" in payload:
        conn.is_default = 1 if payload.get("is_default") else 0
    if "enabled" in payload:
        conn.enabled = 1 if payload.get("enabled") else 0
    return conn


def _coerce_str(value, current):
    if value is None:
        return current
    return str(value)


def _coerce_dict(value, current):
    if value is None:
        return current or {}
    if isinstance(value, dict):
        return value
    return current or {}


# Request-body keys the application owns per call. Allowing them in a
# connection's parameters would let one saved connection break every call
# (e.g. wiping ``messages``), so they are rejected with an explicit message.
# Agent generation options are checked against the same set.
RESERVED_BODY_KEYS = frozenset({"model", "messages", "stream", "extra_body"})


def _coerce_extra_body(value, current):
    """Model parameters must be a JSON object of provider arguments.

    A malformed value (e.g. an unparsed string) is an operator mistake that must
    surface as an error instead of silently dropping the connection's parameters.
    The object is forwarded as the request's ``extra_body``, which the SDK merges
    into the request body, so keys like ``temperature``/``top_k`` land exactly
    where ``chat.completions.create`` would put them.
    """
    if value is None:
        return current or {}
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return current or {}
        try:
            value = json.loads(text)
        except ValueError as exc:
            raise ValueError(f"model parameters are not valid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError("model parameters must be a JSON object")
    reserved = sorted(RESERVED_BODY_KEYS.intersection(value))
    if reserved:
        raise ValueError(
            "model parameters cannot set "
            + ", ".join(reserved)
            + " — the app manages them per call. Put the contents of extra_body directly "
            "in this object instead."
        )
    return value


def save_connection(payload: dict[str, Any], user_id: int | None = None) -> LLMConnection:
    """Create or update a connection from an API/form payload.

    ``user_id`` is stamped as ``created_by`` on creation only; the owner of an
    existing row is never rewritten by a later edit. ``user_id=None`` creates a
    deployment-level (owner-less, shared) row — the seed path.

    The global ``is_default`` flag is deployment-wide state: a request carrying
    an actor may only change it as an administrator (see
    :func:`can_set_global_default`). A tenant owner still creates and uses their
    own private connection; they simply cannot point the deployment default at it.
    """
    ensure()
    conn = None
    cid = payload.get("id")
    if cid and str(cid).strip():
        try:
            conn = LLMConnection.get_by_id(int(cid))
        except (TypeError, ValueError):
            conn = None
    if conn is None:
        conn = LLMConnection()
        conn.created_by = user_id
    previous_default = int(conn.is_default or 0)
    conn = _apply_payload(conn, payload)
    if (
        "is_default" in payload
        and int(conn.is_default or 0) != previous_default
        and user_id is not None
        and not can_set_global_default(user_id)
    ):
        raise LLMConnectionError(GLOBAL_DEFAULT_ADMIN_ONLY)
    if conn.is_default:
        conn.save()
        LLMConnection.set_default(conn.id)
    else:
        conn.save()
    return LLMConnection.get_by_id(conn.id)


def set_default(connection_id: int, user_id: int | None = None) -> LLMConnection | None:
    """Move the global default to ``connection_id`` (administrators only).

    ``user_id=None`` is the internal/seed caller. A request path always passes
    the actor, so a non-administrator is rejected at the root and cannot deny
    the default-model path to every other user.
    """
    ensure()
    if user_id is not None and not can_set_global_default(user_id):
        raise LLMConnectionError(GLOBAL_DEFAULT_ADMIN_ONLY)
    conn = LLMConnection.get_by_id(connection_id)
    if conn is None:
        return None
    LLMConnection.set_default(connection_id)
    return LLMConnection.get_by_id(connection_id)


def delete_connection(connection_id: int) -> bool:
    ensure()
    conn = LLMConnection.get_by_id(connection_id)
    if conn is None:
        return False
    return conn.delete()


def _request_headers(conn: LLMConnection | None) -> dict[str, str]:
    """Default headers plus the operator's free-form connection headers."""
    headers = dict(_BASE_HEADERS)
    if conn and conn.http_headers:
        headers.update(unwrap_dict(conn.http_headers))
    return headers


def _sdk_transport() -> Any:
    """The HTTP library the installed OpenAI SDK drives.

    ``openai`` 3.x is built on ``httpx2``; earlier releases drive ``httpx``. A
    TLS override has to use the SDK's own transport, otherwise the injected
    client is a foreign implementation the SDK has to adapt to (or reject).
    """
    from importlib import import_module

    from openai import DefaultHttpxClient

    base = DefaultHttpxClient.__mro__[1]
    return import_module(base.__module__.split(".")[0])


def _https_clients(conn: LLMConnection | None, *, include_async: bool = False) -> dict[str, Any]:
    """httpx clients that disable TLS verification when the operator asked for it.

    Certificate verification is on by default; when it is turned off the SDK gets
    explicit clients, since there is no per-request switch for it. The raw
    ``openai.OpenAI`` client takes only the sync client.
    """
    if conn is None or conn.verify_ssl:
        return {}
    transport = _sdk_transport()
    clients: dict[str, Any] = {"http_client": transport.Client(verify=False)}
    if include_async:
        clients["http_async_client"] = transport.AsyncClient(verify=False)
    return clients


def build_client(conn: LLMConnection | None, model_name: str | None = None) -> Any:
    from src.utils.reasoning_chat_openai import ReasoningChatOpenAI

    name = (model_name or (conn.model_name if conn else None)) or "gpt-4o"
    kwargs: dict[str, Any] = {
        "model": name,
        "request_timeout": 60.0,
        "max_retries": 2,
        "streaming": True,
        "default_headers": _request_headers(conn),
    }
    if conn and conn.api_key:
        kwargs["api_key"] = unwrap_value(conn.api_key)
    if conn and conn.base_url:
        kwargs["base_url"] = conn.base_url
    if conn is not None:
        if conn.extra_body:
            kwargs["extra_body"] = conn.extra_body
        kwargs.update(_https_clients(conn, include_async=True))
    client = ReasoningChatOpenAI(**kwargs)
    if conn is not None:
        client._llm_defaults = options(conn)
        client._llm_system_instruction = conn.system_instruction
        # The connection a client was built from, so later layers (per-agent
        # generation options) can derive a variant without resolving again.
        client._llm_connection = conn
    return client


def apply_model_options(client: Any, overrides: dict[str, Any] | None) -> Any:
    """Derive a client whose provider parameters are the agent's, on top of the connection's.

    Agent definitions may pin generation parameters (``temperature``,
    ``max_tokens``, ...). Those are more specific than the connection's Model
    Parameters, so they are merged into the request's ``extra_body`` — the SDK
    deep-merges it into the body, where a plain call argument of the same name
    would be overridden by the connection's value instead.

    Clients that are not OpenAI-compatible (the scripted test client) have no
    request body to merge into and are returned unchanged.
    """
    merged_overrides = {k: v for k, v in dict(overrides or {}).items() if v is not None}
    if not merged_overrides or not hasattr(client, "extra_body"):
        return client
    merged = {**(getattr(client, "extra_body", None) or {}), **merged_overrides}
    derived = client.model_copy(update={"extra_body": merged})
    if hasattr(derived, "_llm_defaults"):
        derived._llm_defaults = {"extra_body": merged}
    return derived


def build_openai_client(conn: LLMConnection | None, model_name: str | None = None) -> Any:
    """Build a raw ``openai.OpenAI`` client (LLMEngine path) for a connection."""
    from openai import OpenAI

    name = (model_name or (conn.model_name if conn else None)) or None
    kwargs: dict[str, Any] = {
        "timeout": 60.0,
        "max_retries": 2,
        "default_headers": _request_headers(conn),
    }
    if conn and conn.api_key:
        kwargs["api_key"] = unwrap_value(conn.api_key)
    if conn and conn.base_url:
        kwargs["base_url"] = conn.base_url
    if conn is not None:
        kwargs.update(_https_clients(conn))
    client = OpenAI(**kwargs)
    client.model_name = name
    client._llm_defaults = options(conn) if conn is not None else {}
    client._llm_system_instruction = conn.system_instruction if conn is not None else None
    return client


def _payload_connection(payload: dict[str, Any]) -> LLMConnection:
    """The stored connection the payload refers to, with the payload applied."""
    conn = None
    cid = payload.get("id")
    if cid and str(cid).strip():
        try:
            conn = LLMConnection.get_by_id(int(cid))
        except (TypeError, ValueError):
            conn = None
    if conn is None:
        conn = LLMConnection()
    return _apply_payload(conn, payload)


def _connection_secrets(conn: LLMConnection) -> list[Any]:
    """Credential values that must never be echoed back in a test result."""
    values: list[Any] = [conn.api_key]
    values.extend(unwrap_dict(conn.http_headers).values())
    return values


def test_connection(payload: dict[str, Any]) -> dict[str, Any]:
    """Run one real chat completion against the payload's connection.

    ``ok`` is reported only when the provider answered with assistant text, so a
    URL that merely returns HTTP 200 (an HTML page, an error envelope, an empty
    completion) is a failed test rather than a false success. Every failure
    carries the provider's own error and the underlying transport reason instead
    of the SDK's generic "Connection error.".
    """
    ensure()
    conn = _payload_connection(payload)
    model_name = (conn.model_name or "").strip()
    if not model_name:
        return {"ok": False, "error": "model name is required to test a connection"}
    if not conn.api_key:
        return {"ok": False, "error": "api key is required to test a connection"}
    endpoint = (conn.base_url or "").strip() or "https://api.openai.com/v1"
    secrets = _connection_secrets(conn)
    result: dict[str, Any] = {"endpoint": endpoint, "model": model_name}

    def failure(message: str) -> dict[str, Any]:
        return {**result, "ok": False, "error": redact(message, secrets)}

    try:
        client = build_openai_client(conn, model_name=model_name)
        request: dict[str, Any] = {
            "model": model_name,
            "messages": [{"role": "user", "content": "ping"}],
            "max_tokens": 5,
        }
        if conn.extra_body:
            request["extra_body"] = conn.extra_body
        raw = client.chat.completions.with_raw_response.create(**request)
    except Exception as exc:  # noqa: BLE001 - every provider failure is reported
        return failure(describe_failure(exc, endpoint))
    try:
        reply = assistant_reply(raw, endpoint, model_name)
    except ProviderResponseError as exc:
        return failure(str(exc))
    except Exception as exc:  # noqa: BLE001 - a malformed 2xx must not be a 500
        return failure(describe_failure(exc, endpoint))
    return {**result, "ok": True, "reply": redact(reply, secrets)}


def seed_default_from_config() -> None:
    """If no default connection exists, create one from ``config.config``."""
    ensure()
    if LLMConnection.get_default() is not None:
        return
    try:
        from config.config import (
            MAX_TOKENS,
            OPENROUTER_API_KEY,
            OPENROUTER_BASE_URL,
            OPENROUTER_MODEL,
            TEMPERATURE,
        )
    except Exception:
        return
    conn = LLMConnection(
        name="Default",
        base_url=OPENROUTER_BASE_URL,
        api_key=OPENROUTER_API_KEY,
        model_name=OPENROUTER_MODEL,
        extra_body={"temperature": TEMPERATURE, "max_tokens": MAX_TOKENS},
        is_default=1,
        enabled=1,
    )
    conn.save()
