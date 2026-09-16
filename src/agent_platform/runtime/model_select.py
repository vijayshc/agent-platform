"""LLM Manager is the only source of selectable chat models.

Legacy provider plugins (ollama/foundry/anthropic/bedrock) are not listed or
compiled. Scripted stays E2E-gated.
"""

from __future__ import annotations

from typing import Any

from src.utils.llm_connection_manager import DEFAULT_IDENTIFIERS


def _effective_user_id(user_id: int | None = None) -> int | None:
    """Resolve the acting identity for a tenancy decision.

    An explicit ``user_id`` always wins. ``None`` means "the current request's
    identity": outside a request context (orchestration, evals, the CLI) there is
    no identity, which fails closed for tenant-owned connections while leaving
    deployment-level (owner-less) ones usable.
    """
    if user_id is not None:
        return user_id
    try:
        from src.agent_platform.api.auth import current_user_id

        return current_user_id()
    except RuntimeError:
        return None


def list_public_models(
    user_id: int | None = None, *, include_scripted: bool = False
) -> list[dict[str, Any]]:
    from src.utils.llm_connection_manager import list_connections, visible_connection_ids

    visible = visible_connection_ids(_effective_user_id(user_id))
    models: list[dict[str, Any]] = []
    for conn in list_connections():
        if visible is not None and conn.get("id") not in visible:
            continue
        if not conn.get("enabled", True):
            continue
        models.append({
            "id": str(conn.get("id")),
            "name": conn.get("name") or f"Connection {conn.get('id')}",
            "model_name": conn.get("model_name"),
            "is_default": bool(conn.get("is_default")),
        })
    if include_scripted:
        from src.agent_platform.catalog.validate import scripted_client_allowed

        if scripted_client_allowed():
            models.append({
                "id": "scripted",
                "name": "Scripted (tests)",
                "model_name": None,
                "is_default": False,
            })
    return models


def studio_model_clients(user_id: int | None = None) -> list[dict[str, Any]]:
    return [
        {
            "id": m["id"],
            "label": m["name"],
            "model_name": m.get("model_name"),
            "is_default": bool(m.get("is_default")),
        }
        for m in list_public_models(user_id, include_scripted=True)
    ]


def parse_model_payload(data: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(data, dict):
        return None
    model = data.get("model")
    if model is None:
        return None
    if isinstance(model, str):
        client = model.strip()
        return {"client": client} if client else None
    if not isinstance(model, dict):
        return None
    client = str(model.get("client") or model.get("id") or "").strip()
    if not client:
        return None
    spec: dict[str, Any] = {"client": client}
    name = model.get("name")
    if name:
        spec["name"] = name
    return spec


def pinned_model_client(model_spec: dict[str, Any] | None) -> str | None:
    """The connection an agent explicitly pins, or ``None`` when it has no preference.

    ``client: "default"`` (or a missing client) means "whoever the operator made
    default", so a run-level model choice still applies; any other identifier is
    an explicit pin and wins over the caller's selection.
    """
    client_id = str((model_spec or {}).get("client") or "").strip()
    if client_id.lower() in DEFAULT_IDENTIFIERS:
        return None
    return client_id


def compile_model_client(
    model_spec: dict[str, Any] | None,
    ctx: Any = None,
    user_id: int | None = None,
) -> Any:
    """Build the chat client an agent's model spec asks for.

    Precedence: a connection the agent pins > the run-level client (the model
    the caller selected for this run) > the operator's default connection.

    Tenancy: the resolved connection must be usable by the acting user (the
    explicit ``user_id``, else ``ctx.user_id``, else the current request's
    identity). A connection owned by another tenant fails closed with an
    ``LLMConnectionError``; the deployment-level default is shared.
    """
    spec = dict(model_spec or {})
    pinned = pinned_model_client(spec)
    if pinned is None and ctx is not None and getattr(ctx, "client", None) is not None:
        return ctx.client
    if pinned == "scripted":
        from src.agent_platform.plugins import register_builtin_plugins
        from src.agent_platform.plugins.registry import get_registry

        register_builtin_plugins()
        plugin = get_registry().get(pinned)
        if plugin is not None and plugin.kind == "model":
            return plugin.compile(spec, ctx)
    from src.utils.llm_connection_manager import (
        LLMConnectionError,
        build_client,
        can_access_connection,
        resolve,
    )

    effective_user_id = _effective_user_id(
        user_id if user_id is not None else getattr(ctx, "user_id", None)
    )
    conn = resolve(pinned)
    if not can_access_connection(conn.id, effective_user_id):
        raise LLMConnectionError(
            f"LLM connection '{conn.name}' is not available to this user. Pick a "
            "connection you created or were granted access to."
        )
    return build_client(conn, model_name=spec.get("name"))


def generation_options(config: dict[str, Any] | None) -> dict[str, Any]:
    """Provider parameters an agent pins for its own model calls.

    ``default_options`` is the agent author's generation configuration
    (temperature, max_tokens, ...) and ``max_output_tokens`` is the explicit
    output cap, which wins when both are present. The connection's Model
    Parameters stay the base and are overridden key by key.
    """
    options = {
        key: value
        for key, value in dict((config or {}).get("default_options") or {}).items()
        if value is not None
    }
    max_output = (config or {}).get("max_output_tokens")
    if isinstance(max_output, int) and not isinstance(max_output, bool) and max_output > 0:
        options["max_tokens"] = max_output
    from src.utils.llm_connection_manager import RESERVED_BODY_KEYS, LLMConnectionError

    reserved = sorted(RESERVED_BODY_KEYS.intersection(options))
    if reserved:
        # Same contract as a connection's Model Parameters: these keys drive the
        # request itself, so an agent must not be able to set them.
        raise LLMConnectionError(
            "default_options cannot set " + ", ".join(reserved) + " — the app manages them per call."
        )
    return options


def client_from_payload(
    payload: dict[str, Any] | None, user_id: int | None = None
) -> Any | None:
    """Build the run-level client a request payload selects.

    ``user_id`` defaults to the current request's identity; when it is unknown
    (no request context) tenant-owned connections fail closed, while the
    deployment-level default connection stays usable.
    """
    spec = parse_model_payload(payload)
    if spec is None:
        return None
    return compile_model_client(spec, user_id=user_id)
