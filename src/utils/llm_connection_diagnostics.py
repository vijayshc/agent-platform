"""Live diagnostic probes and connectivity tests for LLM provider connections.

Extracted from llm_connection_manager to keep connection lifecycle and client
factories cleanly decoupled from network diagnostics.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from src.models.llm_connection import LLMConnection
from src.models.secrets import unwrap_dict
from src.utils.provider_diagnostics import (
    ProviderResponseError,
    assistant_reply,
    describe_failure,
    redact,
)

if TYPE_CHECKING:
    pass


def _payload_connection(payload: dict[str, Any]) -> LLMConnection:
    """The stored connection the payload refers to, with the payload applied."""
    from src.utils.llm_connection_manager import _apply_payload

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
    from src.utils.llm_connection_manager import build_openai_client, ensure

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
