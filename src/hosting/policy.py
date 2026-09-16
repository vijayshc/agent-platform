"""What a hosted app is allowed to send back to the browser.

This is a **policy control, not a security boundary**, and the admin UI says so.
The proxy can only judge what the app declares: an app that wants to hand the
browser a file can label it ``text/html`` and the user can still save it. What
the allowlist genuinely buys is that an app cannot surprise a user with a
download dialog, and that a whole class of accidental responses (a stray ZIP, a
database dump, a PDF) never reaches the browser at all.

A second control sits beside it: a **per-content-type payload cap**. An app that
is allowed to answer ``application/json`` is still not allowed to answer with an
unbounded one, so a bulk export cannot be pulled through the app by a user who
may open it. The cap is per type because the honest sizes differ by an order of
magnitude - a page or an icon is kilobytes, a data endpoint may be megabytes -
and one number for everything would either break the page or fail to stop the
dump.

The real containment for data is the isolation tier - what the app can *read* -
together with per-application access control, which together decide what data an
app has in the first place.
"""

from __future__ import annotations

import ipaddress
import re
from typing import Iterable

from src.hosting import settings

#: Offered to the administrator as a starting point: everything a normal web
#: application needs to render, and nothing that prompts a download.
RECOMMENDED_CONTENT_TYPES = (
    "text/html",
    "text/css",
    "text/plain",
    "text/javascript",
    "application/javascript",
    "application/json",
    "image/png",
    "image/jpeg",
    "image/gif",
    "image/svg+xml",
    "image/webp",
    "image/x-icon",
    "font/woff",
    "font/woff2",
    "application/font-woff",
)

#: The largest cap an administrator may set. A response over its cap is buffered
#: (bounded by the cap) so it can be refused with a real status rather than
#: truncated mid-flight; this ceiling keeps that buffer in the same order as the
#: request body the platform already holds.
MAX_LIMIT_BYTES = settings.max_body_bytes()

DEFAULTS = {
    "allowed_content_types": [],
    "block_attachments": False,
    "content_type_limits": {},
    "source_ip_allowlist": [],
}

#: A source-IP rule is a network range (``10.0.0.0/24``), a single address, or
#: an IPv4 wildcard (``192.168.1.*``). The wildcard stands for a whole octet, so
#: it reads as a subnet without needing a prefix to be worked out.
_IPV4_WILDCARD = re.compile(r"^(\*|\d{1,3})(\.(\*|\d{1,3})){3}$")


def for_record(record) -> dict:
    """The policy that belongs to one app."""
    if record is None:
        return dict(DEFAULTS)
    raw_limits = getattr(record, "content_type_limits", {}) or {}
    if not isinstance(raw_limits, dict):
        raw_limits = {}
    return {
        "allowed_content_types": list(getattr(record, "allowed_content_types", []) or []),
        "block_attachments": bool(getattr(record, "block_attachments", False)),
        "content_type_limits": {str(key).lower(): value for key, value in raw_limits.items()},
        "source_ip_allowlist": normalize_ip_rules(getattr(record, "source_ip_allowlist", []) or []),
    }


def save_for_slug(slug: str, policy: dict) -> dict:
    """Persist one app's policy, normalising the content types as it goes."""
    from src.models.hosted_app import HostedApp

    allowed = normalize(policy.get("allowed_content_types") or [])
    cleaned = {
        "allowed_content_types": allowed,
        "block_attachments": bool(policy.get("block_attachments", False)),
        "content_type_limits": normalize_limits(policy.get("content_type_limits"), allowed),
        "source_ip_allowlist": normalize_ip_rules(policy.get("source_ip_allowlist") or []),
    }
    HostedApp.set_content_policy(
        slug, cleaned["allowed_content_types"], cleaned["block_attachments"],
        cleaned["content_type_limits"], cleaned["source_ip_allowlist"],
    )
    return cleaned


def normalize(content_types: Iterable[str]) -> list[str]:
    """Lower-case, strip parameters and de-duplicate, keeping the given order."""
    seen: list[str] = []
    for item in content_types:
        value = str(item or "").split(";")[0].strip().lower()
        if value and value not in seen:
            seen.append(value)
    return seen


def normalize_limits(raw, allowed: Iterable[str]) -> dict[str, int]:
    """A positive byte cap per content type the app may actually send.

    With a non-empty allowlist that means the types on it, so removing a type
    removes its cap with it; an empty allowlist permits everything, so every cap
    is meaningful and kept. Values that are not a positive whole number are
    dropped rather than stored as nonsense, and each is clamped to the ceiling.
    """
    permitted = normalize(allowed)
    if not isinstance(raw, dict):
        return {}
    cleaned: dict[str, int] = {}
    for key, value in raw.items():
        content_type = str(key or "").split(";")[0].strip().lower()
        if not content_type or (permitted and content_type not in permitted):
            continue
        try:
            size = int(value)
        except (TypeError, ValueError):
            continue
        if size > 0:
            cleaned[content_type] = min(size, MAX_LIMIT_BYTES)
    return cleaned


def max_bytes_for(content_type: str | None, policy: dict | None = None) -> int | None:
    """The byte cap that applies to a response of this type, or ``None``.

    Judged on the declared type, parameters stripped (``application/json;
    charset=utf-8`` is still ``application/json``).
    """
    policy = policy or DEFAULTS
    limits = policy.get("content_type_limits") or {}
    if not limits:
        return None
    declared = (content_type or "").split(";")[0].strip().lower()
    if not declared:
        return None
    return limits.get(declared)


def declared_exceeds(content_length: str | None, limit: int) -> bool:
    """Whether a declared length is already over the cap.

    A length the app declares is the wire size; for a compressed response that is
    smaller than what the browser receives, so this is only ever a fast refusal,
    never the whole check.
    """
    try:
        size = int(content_length)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return False
    return size > limit


def limit_exceeded_reason(content_type: str | None, limit: int) -> str:
    declared = (content_type or "unknown").split(";")[0].strip().lower() or "unknown"
    return f"the response body of type '{declared}' exceeds the {human_bytes(limit)} payload limit"


def human_bytes(size: int) -> str:
    """A size an administrator wrote, said back the way they wrote it."""
    size = int(size)
    for unit, step in (("GB", 1024 ** 3), ("MB", 1024 ** 2), ("KB", 1024)):
        if size >= step and size % step == 0:
            return f"{size // step} {unit}"
    return f"{size} bytes"


def is_ip_rule(value: str) -> bool:
    """Whether one entry is a source-address rule this policy can enforce."""
    value = str(value or "").strip()
    if not value:
        return False
    if "*" in value:
        if not _IPV4_WILDCARD.match(value):
            return False
        return all(part == "*" or int(part) <= 255 for part in value.split("."))
    try:
        if "/" in value:
            ipaddress.ip_network(value, strict=False)
        else:
            ipaddress.ip_address(value)
    except ValueError:
        return False
    return True


def invalid_ip_rules(items) -> list[str]:
    """The entries an administrator offered that cannot be enforced."""
    return [
        str(item).strip()
        for item in (items or [])
        if str(item or "").strip() and not is_ip_rule(str(item))
    ]


def normalize_ip_rules(items) -> list[str]:
    """Trim, drop anything unenforceable and de-duplicate, keeping the order."""
    seen: list[str] = []
    for item in items or []:
        value = str(item or "").strip()
        if value and value not in seen and is_ip_rule(value):
            seen.append(value)
    return seen


def _matches_wildcard(rule: str, client_ip: str) -> bool:
    pattern = "^" + re.escape(rule).replace(r"\*", r"\d{1,3}") + "$"
    return re.match(pattern, client_ip) is not None


def ip_matches(client_ip: str, rules: Iterable[str]) -> bool:
    """Whether an address satisfies any rule: exact, ``a.b.c.d/n`` or ``a.b.*``."""
    try:
        address = ipaddress.ip_address(client_ip)
    except ValueError:
        address = None
    for rule in rules:
        if "*" in rule:
            if _matches_wildcard(rule, client_ip):
                return True
        elif "/" in rule:
            try:
                if address is not None and address in ipaddress.ip_network(rule, strict=False):
                    return True
            except ValueError:
                continue
        elif address is not None and address == ipaddress.ip_address(rule):
            return True
    return False


def check_source_ip(client_ip: str | None, policy: dict | None = None) -> str | None:
    """Return the reason a request from this address must be blocked, or ``None``.

    An empty list means "no restriction", matching the content allowlist. This is
    judged on the platform's own view of the peer (``request.remote_addr``), not
    on a client-supplied forwarding header.
    """
    policy = policy or DEFAULTS
    rules = policy.get("source_ip_allowlist") or []
    if not rules:
        return None
    if not client_ip:
        return "the request carries no source address, and the policy allows only a fixed list"
    if ip_matches(client_ip, rules):
        return None
    return f"the request came from {client_ip}, which is not in the allowed source list"


def check(content_type: str | None, content_disposition: str | None, policy: dict | None = None) -> str | None:
    """Return the reason a response must be blocked, or ``None`` to allow it.

    ``policy`` is the *app's* policy; without one nothing is restricted. Payload
    caps are judged separately (they need the body), see :func:`max_bytes_for`.
    """
    policy = policy or DEFAULTS

    disposition = (content_disposition or "").lower()
    if policy.get("block_attachments") and "attachment" in disposition:
        return "downloads are blocked by the content policy"

    allowed = policy.get("allowed_content_types") or []
    if not allowed:
        return None  # an empty allowlist means "no restriction"

    declared = (content_type or "").split(";")[0].strip().lower()
    if not declared:
        return "the response declares no content type, and the policy allows only a fixed list"
    if declared not in allowed:
        return f"content type '{declared}' is not in the allowed list"
    return None
