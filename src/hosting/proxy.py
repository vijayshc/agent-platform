"""Forwarding a request to a hosted app, and judging what comes back.

The platform owns the listening socket and the app inherits the descriptor, so
the only route into an app is this proxy. That makes it the one place where the
response can be judged before the browser sees it, which is exactly what the
per-application content policy needs.

Two controls live here, both owned by the app's policy:

* **what** it may send (content type, and ``Content-Disposition: attachment``);
* **how much** of it, per content type. A response whose type carries a cap is
  read up to that cap before anything is sent. If it fits, it is served
  normally; if it does not, the browser gets a plain ``413`` and no part of the
  dump. Reading it first is deliberate: the alternative - streaming and cutting
  the connection - would leak an incomplete body and answer with a lied-about
  ``200``. A type with no cap is streamed as before, so server-sent events and
  long polls are untouched.
"""

from __future__ import annotations

import re

import httpx
from flask import Response, g, jsonify, request, stream_with_context

from src.hosting import identity, origin, policy, supervisor

PROXY_TIMEOUT = httpx.Timeout(connect=10.0, read=None, write=60.0, pool=10.0)

#: Never forwarded in either direction.
HOP_BY_HOP = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade", "host", "content-length",
}
#: The platform's own cookie must not reach an app, and an app's cookie must not
#: be able to look like the platform's.
#: ``cookie`` is handled separately (an app sees its own cookies and only its
#: own); ``authorization`` is dropped because it carries a *platform* credential
#: - an app has no business holding the caller's API key or bearer token.
REQUEST_DROP = HOP_BY_HOP | {
    "cookie", "authorization", "x-forwarded-prefix", "x-forwarded-host", "x-forwarded-proto",
} | {name.lower() for name in identity.IDENTITY_HEADERS}
#: ``content-encoding`` is dropped because the body is decoded on the way
#: through (``iter_bytes``), so forwarding the header would describe bytes the
#: client never receives. ``content-length`` is recomputed from the body that is
#: actually sent - which matters once a response has been buffered to judge it.
RESPONSE_DROP = HOP_BY_HOP | {"content-encoding", "content-length", "set-cookie", "location"}

_COOKIE_NAME = re.compile(r"^\s*([^=;]+)=")
_COOKIE_PATH = re.compile(r"(;\s*[Pp]ath=)([^;]*)")


def forward(record, subpath: str, body: bytes | None, headers: dict):
    """Send one request to an app and return what the browser may be given."""
    slug = record.slug
    target = f"http://app/{subpath}"
    if request.query_string:
        target = f"{target}?{request.query_string.decode('utf-8', 'replace')}"

    client = httpx.Client(
        transport=httpx.HTTPTransport(uds=str(supervisor.socket_path(slug))),
        timeout=PROXY_TIMEOUT,
        follow_redirects=False,
    )
    try:
        upstream = client.send(
            client.build_request(request.method, target, headers=headers, content=body),
            stream=True,
        )
    except Exception as exc:
        client.close()
        return jsonify({"error": "unreachable", "message": str(exc)}), 502
    return _relay(client, upstream, record)


def _relay(client: httpx.Client, upstream: httpx.Response, record):
    """Judge the upstream response against the app's policy, then stream it."""
    slug = record.slug
    app_policy = policy.for_record(record)
    content_type = upstream.headers.get("content-type")
    status = upstream.status_code

    out_headers = [(k, v) for k, v in upstream.headers.multi_items() if k.lower() not in RESPONSE_DROP]
    out_headers.extend(rewritten_cookies(slug, upstream.headers.get_list("set-cookie")))
    location = upstream.headers.get("location")
    if location:
        out_headers.append(("Location", rewrite_location(slug, location)))

    # A 304 (or 204, or 1xx) carries no body: there is nothing to allow, cap or
    # hand to the browser, and the browser may fold its headers into the cache
    # entry it is revalidating. Judging one as content is what turned a cache
    # revalidation - which declares no content type at all - into a 403 and broke
    # every warm-cache page load. It passes through with its headers untouched.
    if not _carries_body(status):
        upstream.close()
        client.close()
        return Response(status=status, headers=out_headers)

    blocked = policy.check(content_type, upstream.headers.get("content-disposition"), app_policy)
    if blocked:
        upstream.close()
        client.close()
        return blocked_response("Blocked by the content policy", blocked, 403)

    payload = None
    # A HEAD carries no body to cap; buffering it would also rewrite the length
    # it reports, which is the one thing a HEAD is for.
    limit = policy.max_bytes_for(content_type, app_policy) if request.method != "HEAD" else None
    if limit is not None:
        over = policy.declared_exceeds(upstream.headers.get("content-length"), limit)
        if not over:
            try:
                payload, over = _read_within(upstream, limit)
            except Exception as exc:
                upstream.close()
                client.close()
                g.audit_reason = "the app's response body could not be read"
                return jsonify({"error": "bad_gateway", "message": str(exc)}), 502
        upstream.close()
        client.close()
        if over:
            reason = policy.limit_exceeded_reason(content_type, limit)
            return blocked_response("Blocked by the payload limit", reason, 413, "payload_too_large")

    if payload is not None:
        response = Response(payload, status=status, headers=out_headers)
    else:
        @stream_with_context
        def body():
            try:
                for chunk in upstream.iter_bytes():
                    yield chunk
            finally:
                upstream.close()
                client.close()

        response = Response(body(), status=status, headers=out_headers)
    # Server-sent events and long polls must not be buffered by an intermediary.
    response.headers.setdefault("X-Accel-Buffering", "no")
    return response


def _carries_body(status: int) -> bool:
    """Whether a response can carry a body the policy would govern.

    1xx is interim, 204 means "no content" and 304 means "use your cache": all
    three are bodyless by specification, so their empty shell must pass the
    policy rather than be judged as unidentified content.
    """
    return status >= 200 and status not in (204, 304)


def _read_within(upstream: httpx.Response, limit: int) -> tuple[bytes | None, bool]:
    """The decoded body, or ``(None, True)`` the moment it passes ``limit``.

    The body is decoded by httpx, so this counts what the browser would actually
    receive - a gzipped bulk export still trips its cap.
    """
    chunks: list[bytes] = []
    total = 0
    for chunk in upstream.iter_bytes():
        total += len(chunk)
        if total > limit:
            return None, True
        chunks.append(chunk)
    return b"".join(chunks), False


def blocked_response(title: str, reason: str, status: int = 403,
                     code: str = "blocked_by_content_policy", audit: str = "content policy"):
    """The refusal a policy block returns, as a JSON error or a readable page."""
    g.audit_reason = f"{audit}: {reason}"
    if "/api/" in request.path or request.is_json:
        return jsonify({"error": code, "message": reason}), status
    return origin.message_page(
        request, status, title,
        f"{reason}. An administrator can change this under Hosted Apps &rarr; this "
        "application &rarr; Content policy.",
    )


def forwarded_cookie_header(slug: str) -> str | None:
    """The Cookie header an app should see: only its own cookies, by their own names.

    The platform's session cookie must never cross the boundary, and neither must
    a sibling app's - but an app's *own* cookies must, or any Flask app using
    sessions, flash messages or CSRF tokens would be permanently signed out.
    """
    raw = request.headers.get("Cookie")
    if not raw:
        return None
    prefix = f"app_{slug}_"
    forwarded = []
    for part in raw.split(";"):
        part = part.strip()
        if "=" not in part:
            continue
        name, _, value = part.partition("=")
        name = name.strip()
        if name.startswith(prefix):
            forwarded.append(f"{name[len(prefix):]}={value}")
    return "; ".join(forwarded) if forwarded else None


def rewrite_location(slug: str, location: str) -> str:
    """Keep redirects inside the app's prefix."""
    prefix = f"/apps/{slug}"
    if location.startswith(prefix):
        return location
    if location.startswith("/"):
        return f"{prefix}{location}"
    return location  # absolute URL: the app is deliberately pointing elsewhere


def rewritten_cookies(slug: str, cookies: list[str]) -> list[tuple[str, str]]:
    """Namespace an app's cookies to its own path.

    Without this an app using Flask sessions would set a cookie called
    ``session`` at ``/`` on the platform's own origin - overwriting the platform
    session of every signed-in user.
    """
    rewritten = []
    for cookie in cookies:
        name_match = _COOKIE_NAME.match(cookie)
        if not name_match:
            continue
        name = name_match.group(1).strip()
        scoped = f"app_{slug}_{name}"
        value = cookie[name_match.end():]
        if _COOKIE_PATH.search(value):
            value = _COOKIE_PATH.sub(rf"\g<1>/apps/{slug}/", value)
        else:
            value = f"{value}; Path=/apps/{slug}/"
        rewritten.append(("Set-Cookie", f"{scoped}={value}"))
    return rewritten
