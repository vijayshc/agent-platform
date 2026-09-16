"""Truthful diagnostics for real provider calls.

Two SDK behaviours hide the truth from the LLM Manager's connection test:

* every transport problem is reported with the same generic text
  ("Connection error."), with the real reason only in the exception's cause;
* any 2xx JSON body is accepted as a ``ChatCompletion`` — even one that carries
  no choices at all — so a URL that is not a chat endpoint looks like a success.

This module extracts the provider's own error text and validates that a 2xx body
really is a chat completion with assistant text, so the LLM Manager can only
report success for a connection that genuinely answered.
"""

from __future__ import annotations

import json
import re
from typing import Any, Iterable

from src.models.secrets import MASK, unwrap_value

# Provider bodies can be whole HTML error pages; keep the reported reason short.
_MAX_DETAIL = 300

_HTML_TAG = re.compile(r"<[^>]+>")
_HTML_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_WHITESPACE = re.compile(r"\s+")


class ProviderResponseError(RuntimeError):
    """The provider answered 2xx, but not with a usable chat completion."""


def detail(text: Any, limit: int = _MAX_DETAIL) -> str:
    """Collapse a provider body into one short, readable reason."""
    collapsed = _WHITESPACE.sub(" ", _HTML_TAG.sub(" ", str(text or ""))).strip()
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: limit - 1].rstrip() + "…"


def html_summary(text: str) -> str | None:
    """The meaningful line of an HTML error page (its title), not its markup."""
    if "<html" not in text.lower() and "<!doctype" not in text.lower():
        return None
    match = _HTML_TITLE.search(text)
    summary = detail(match.group(1)) if match else ""
    return summary or None


def error_message_from_body(body: Any) -> str | None:
    """Pull a provider's own human-readable error out of a decoded body."""
    if isinstance(body, str):
        return html_summary(body) or detail(body) or None
    if not isinstance(body, dict):
        return None
    error = body.get("error")
    if isinstance(error, dict):
        for key in ("message", "detail", "description", "error"):
            value = error.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    if isinstance(error, str) and error.strip():
        return error.strip()
    for key in ("message", "detail", "error_description"):
        value = body.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def redact(text: str, secrets: Iterable[Any]) -> str:
    """Never echo a credential back inside a diagnostics message.

    Provider bodies can quote the request back (echo endpoints, gateway logs),
    which would otherwise put the connection's API key or header secrets in
    front of whoever reads the test result.
    """
    for value in secrets:
        secret = unwrap_value(value)
        if isinstance(secret, str) and len(secret) >= 4 and secret != MASK:
            text = text.replace(secret, MASK)
    return text


def describe_failure(exc: BaseException, endpoint: str) -> str:
    """Name the endpoint and the real reason a provider call failed.

    The SDK's own ``str(exc)`` ("Connection error.") is deliberately replaced by
    the root cause of the failure — the refused connection, the unknown host,
    the rejected certificate, the timeout — so the operator can act on it.
    """
    status = getattr(exc, "status_code", None)
    if isinstance(status, int):
        return f"{endpoint} returned HTTP {status}: {provider_error_text(exc)}"
    if "timeout" in type(exc).__name__.lower():
        return f"{endpoint} did not answer in time: {detail(str(exc))}"
    cause = deepest_cause(exc)
    reason = detail(f"{type(cause).__name__}: {cause}") if cause is not None else detail(str(exc))
    return f"Cannot reach {endpoint}: {reason}"


def provider_error_text(exc: BaseException) -> str:
    """The provider's reply body, preferring its structured error message."""
    message = error_message_from_body(getattr(exc, "body", None))
    if message:
        return detail(message)
    response = getattr(exc, "response", None)
    if response is not None:
        message = error_message_from_body(as_json(response))
        if message:
            return detail(message)
        raw = getattr(response, "text", "") or ""
        summary = html_summary(raw)
        if summary:
            return summary
        text = detail(raw)
        if text:
            return text
    return detail(str(exc))


def deepest_cause(exc: BaseException) -> BaseException | None:
    """The last link of the ``raise ... from`` chain, when there is one."""
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if current.__cause__ is None:
            break
        current = current.__cause__
    return None if current is exc else current


def as_json(value: Any) -> Any:
    """Decode a response or body into JSON, whatever shape it arrives in."""
    if value is None or isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        return _loads(value)
    parser = getattr(value, "json", None)
    if callable(parser):
        try:
            return parser()
        except Exception:  # noqa: BLE001 - a non-JSON body is expected here
            return None
    text = getattr(value, "text", None)
    return _loads(text) if isinstance(text, str) else None


def assistant_reply(raw: Any, endpoint: str, model: str) -> str:
    """The assistant text of a 2xx chat completion, or a precise error.

    Raises ``ProviderResponseError`` when the body is not JSON, carries no
    choices, or holds no assistant text: each of those means the connection
    cannot be used for chat even though the HTTP call succeeded.
    """
    status = getattr(raw, "status_code", 200)
    body = as_json(raw)
    if body is None:
        text = getattr(raw, "text", "") or ""
        content_type = (getattr(raw, "headers", None) or {}).get("content-type", "")
        raise ProviderResponseError(
            f"{endpoint} answered HTTP {status} but not with JSON "
            f"({content_type or 'unknown content type'}): {html_summary(text) or detail(text)}"
        )
    choices = body.get("choices") if isinstance(body, dict) else None
    if not choices:
        reason = error_message_from_body(body) or detail(json.dumps(body))
        raise ProviderResponseError(
            f"{endpoint} answered HTTP {status} without a chat completion"
            + (f": {reason}" if reason else "")
            + ". Check the base URL — it must serve an OpenAI-compatible /chat/completions."
        )
    first = choices[0] if isinstance(choices[0], dict) else {}
    text = message_text(first.get("message"))
    if not text:
        raise ProviderResponseError(
            f"{endpoint} answered HTTP {status} but model {model!r} returned no text "
            f"(finish_reason={first.get('finish_reason')!r}). Check the model name and the "
            "connection's model parameters — a tiny max_tokens or thinking mode can "
            "swallow the reply."
        )
    return text


def message_text(message: Any) -> str:
    """The text of an assistant message, including list-style content parts."""
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    if isinstance(content, list):
        content = "".join(
            part.get("text") or "" for part in content if isinstance(part, dict)
        )
    return content.strip() if isinstance(content, str) else ""


def _loads(text: str) -> Any:
    try:
        return json.loads(text)
    except ValueError:
        return None
