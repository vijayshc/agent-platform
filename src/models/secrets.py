"""SecretString helpers for provider-credential fields.

``agent_framework.SecretString`` is a str subclass whose ``repr()`` is masked
but whose ``str()`` and JSON serialization reveal the raw value (verified
against agent-framework 1.7.0).  These helpers therefore (a) wrap
credential-bearing values in SecretString at the source of truth so repr/$
logging redacts them, and (b) provide ``mask_value()`` for the serialization
boundaries (API responses) that must never emit the plaintext.
"""

from __future__ import annotations

from typing import Any

class SecretString(str):
    def __repr__(self) -> str:
        return "SecretString('********')"

MASK = "********"

# Persist-time attribute keys that are always credentials, regardless of value.
# ``http.request.header.*`` covers the OTEL HTTP header namespace.
_CREDENTIAL_LEAVES = frozenset({"authorization", "api_key", "cookie", "token"})

# Raw secret strings registered at wrap time so persist-time redaction can
# replace them even after OTEL has copied them into a plain str.
_REGISTERED_SECRETS: set[str] = set()
_SORTED_SECRETS: tuple[str, ...] = ()


def is_secret_key(key: Any) -> bool:
    """True when a telemetry/header key is always treated as a credential."""
    k = str(key).lower()
    if k.startswith("http.request.header."):
        return True
    leaf = k.rsplit(".", 1)[-1].replace("-", "_")
    return leaf in _CREDENTIAL_LEAVES


def register_secret(value: Any) -> None:
    """Remember a secret string for substring redaction at persist time."""
    global _SORTED_SECRETS
    if not isinstance(value, str):
        return
    raw = str(value)
    if not raw or raw == MASK or raw in _REGISTERED_SECRETS:
        return
    _REGISTERED_SECRETS.add(raw)
    _SORTED_SECRETS = tuple(sorted(_REGISTERED_SECRETS, key=len, reverse=True))


def as_secret(value: Any) -> Any:
    """Wrap a plain string in SecretString (non-strings pass through)."""
    if value is None:
        return value
    if isinstance(value, SecretString):
        register_secret(str(value))
        return value
    if isinstance(value, str):
        register_secret(value)
        return SecretString(value)
    return value


def wrap_headers(headers: Any) -> Any:
    """Wrap every string value in a credential bag (env / headers / http_headers).

    These dicts *are* credential bags: names like ``OPENAI_KEY`` / ``X-Key`` do
    not match a TOKEN|SECRET|PASSWORD heuristic, so wrapping is by container,
    not by key name. ``command`` / ``args`` / ``url`` live outside these dicts.
    """
    if not isinstance(headers, dict):
        return headers
    return {key: as_secret(value) if isinstance(value, str) else value for key, value in headers.items()}


def wrap_credentials(config: dict[str, Any]) -> dict[str, Any]:
    """Wrap every string value in ``config.env`` / ``config.headers``.

    Returns a shallow copy; the input dict is not mutated. Other keys
    (``command``, ``args``, ``url``) stay plaintext.
    """
    out = dict(config or {})
    for section in ("env", "headers"):
        raw = out.get(section)
        if isinstance(raw, dict):
            out[section] = wrap_headers(raw)
    return out


def mask_value(value: Any) -> Any:
    """Deep copy with every SecretString replaced by ``MASK``."""
    if isinstance(value, SecretString):
        return MASK
    if isinstance(value, dict):
        return {key: mask_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [mask_value(item) for item in value]
    return value


def redact_substrings(text: str) -> str:
    """Replace every registered secret substring with ``MASK``."""
    if not text or not _SORTED_SECRETS:
        return text
    for secret in _SORTED_SECRETS:
        if secret in text:
            text = text.replace(secret, MASK)
    return text


def redact_value(value: Any) -> Any:
    """Deep redact for persist-time payloads.

    (a) SecretString -> MASK, (b) registered secret substrings -> MASK,
    (c) credential-named keys -> MASK.
    """
    if isinstance(value, SecretString) or type(value).__name__ == "SecretString":
        return MASK
    if isinstance(value, dict):
        out: dict[Any, Any] = {}
        for key, item in value.items():
            if is_secret_key(key):
                out[key] = MASK
            else:
                out[key] = redact_value(item)
        return out
    if isinstance(value, list):
        return [redact_value(item) for item in value]
    if isinstance(value, bytes):
        try:
            return redact_substrings(value.decode("utf-8"))
        except Exception:
            return value
    if isinstance(value, str):
        return redact_substrings(value)
    return value


def unwrap_value(val: Any) -> Any:
    if val is None:
        return None
    if isinstance(val, SecretString):
        return str(val)
    if isinstance(val, dict):
        return {k: unwrap_value(v) for k, v in val.items()}
    if isinstance(val, list):
        return [unwrap_value(v) for v in val]
    return val


def unwrap_dict(d: Any) -> dict[str, Any]:
    if not isinstance(d, dict):
        return {}
    return {str(k): str(unwrap_value(v)) for k, v in d.items()}


def merge_masked(previous: Any, incoming: Any) -> Any:
    """Replace mask-placeholder values in ``incoming`` with the stored ones.

    A client that edits a config object it previously fetched sees masked
    credential values (e.g. ``********``). Saving that object back verbatim must
    not overwrite the stored credential, so any dict value equal to the mask is
    restored from ``previous`` at that key. Nested dicts are walked so both the
    MCP ``env``/``headers`` shape and a flat LLM ``http_headers`` dict work.
    """
    if not isinstance(incoming, dict) or not isinstance(previous, dict):
        return incoming
    out = dict(incoming)
    for key, value in incoming.items():
        if value == MASK and key in previous:
            out[key] = previous[key]
        elif isinstance(value, dict) and isinstance(previous.get(key), dict):
            out[key] = merge_masked(previous[key], value)
    return out
