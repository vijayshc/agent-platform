"""Hosted apps are served from their own origin.

**Why this exists.** A document served from the platform's own origin is
same-origin with the platform's own JavaScript. Whatever the proxy does to
cookies and identity headers, a hosted app's script can call every platform API
as the signed-in user *through the browser* and read the answers - measured: an
app page read the user list, created an administrator and posted the result to
another host. No process sandbox changes that, because it is a browser boundary,
not a kernel one.

Serving app content from a second origin puts the browser's same-origin policy
back in charge:

* the app cannot read platform responses (no CORS headers are sent);
* the app cannot script or frame a platform page (``X-Frame-Options: SAMEORIGIN``
  is cross-origin from there);
* the app's own cookies, XHR and server-side sessions keep working, because
  inside the apps origin the app *is* same-origin with itself.

The path is unchanged - apps still live at ``/apps/<slug>/``. Only the authority
differs: the platform listens on its own port, and the hosted-apps origin is a
second listener on the same host (or a hostname the operator provides, e.g.
``https://apps.example.com`` behind a reverse proxy). A request to the platform's
own ``/apps/<slug>/`` is redirected to the apps origin rather than proxied, so
bookmarks and links keep working.

Two modes:

* **Second listener in this process** (default, ``HOSTED_APPS_ORIGIN_PORT``):
  used by the development server, which is how this deployment runs.
* **Operator-provided origin** (``HOSTED_APPS_ORIGIN``, e.g. behind nginx or a
  second WSGI instance started with ``APP_ORIGIN_ONLY=1``): the platform does not
  bind anything and trusts the configured origin.

Setting ``HOSTED_APPS_ORIGIN_PORT=0`` restores same-origin serving. That is the
configuration this module exists to remove; it is allowed only so an existing
deployment can be migrated, and every start logs a warning.
"""

from __future__ import annotations

import logging
import os
import re
import threading
from urllib.parse import urlsplit

from src.hosting import settings

logger = logging.getLogger("text2sql.hosting.origin")

#: The only paths this origin serves. Everything else is a platform path and must
#: not be reachable from a document the app controls. A slug is required: the bare
#: ``/apps`` has no route, and reaching the platform's 404 handler would render the
#: platform shell (with the session's CSRF token) on the app-controlled origin.
SERVED_PREFIX = "/apps"
_SERVED_PATH = re.compile(r"^/apps/[^/]")
ENV_ORIGIN_ONLY = "APP_ORIGIN_ONLY"

_state = {"available": False, "reason": "not started", "server": None}


# --- origins -----------------------------------------------------------------

def _default_port(scheme: str) -> int:
    return 443 if scheme == "https" else 80


def _split_host(host: str, scheme: str = "http") -> tuple[str, int]:
    """``host[:port]`` from a Host header or netloc, with the port filled in.

    Userinfo is dropped: ``Host: attacker@real-host`` must not turn into a
    redirect whose authority carries attacker-controlled credentials.
    """
    host = (host or "").strip()
    if "@" in host:
        host = host.rsplit("@", 1)[-1]
    if not host:
        return "", _default_port(scheme)
    if host.startswith("["):  # IPv6 literal
        name, _, rest = host.partition("]")
        name += "]"
        port = int(rest[1:]) if rest.startswith(":") and rest[1:].isdigit() else _default_port(scheme)
        return name, port
    if ":" in host:
        name, _, tail = host.rpartition(":")
        if tail.isdigit():
            return name, int(tail)
    return host, _default_port(scheme)


def _origin(scheme: str, host: str, port: int, *, with_port: bool = True) -> str:
    name, _ = _split_host(host, scheme)
    if not name:
        return ""
    if not with_port or port == _default_port(scheme):
        return f"{scheme}://{name}"
    return f"{scheme}://{name}:{port}"


def _configured_origin() -> str:
    """The configured apps origin reduced to ``scheme://host[:port]``.

    A path in the configured value is meaningless - every app is addressed by its
    own ``/apps/<slug>/`` path - and keeping it would produce doubled prefixes.
    """
    configured = settings.apps_origin_url()
    if not configured:
        return ""
    parts = urlsplit(configured)
    if not parts.netloc:
        return ""
    return f"{parts.scheme or 'https'}://{parts.netloc}"


def apps_origin_for(request) -> str:
    """The absolute origin a hosted app should be served from, for this request."""
    configured = _configured_origin()
    if configured:
        return configured.rstrip("/")
    return _origin(request.scheme, request.host, settings.apps_origin_port())


def platform_origin_for(request) -> str:
    """The platform's own origin, as the browser should reach it."""
    configured = settings.platform_origin_url()
    if configured:
        return configured.rstrip("/")
    return _origin(request.scheme, request.host, settings.platform_port())


def is_apps_origin(request) -> bool:
    """Whether this request arrived on the apps origin rather than the platform's.

    Deliberately layered, because every single-signal version had a hole:

    1. Same-origin migration mode: everything is the platform origin.
    2. ``APP_ORIGIN_ONLY=1``: this process exists to serve the apps origin.
    3. The port the request *actually arrived on* (``SERVER_PORT``) is this
       process's apps listener. A ``Host`` header cannot fake it, which is what
       stops a portless Host on 80/443 from being mistaken for the apps origin
       and putting app content back on the platform's origin.
    4. A configured origin, matched by hostname - needed where a proxy terminates
       TLS and the WSGI app sees ``http`` - but the platform's own port still
       wins, or a deployment whose two origins share a hostname (differing only
       by port) would call every platform request an apps request.
    """
    if settings.same_origin_mode():
        return True
    if os.getenv(ENV_ORIGIN_ONLY, "").strip() == "1":
        return True

    listener_port = None
    raw_port = request.environ.get("SERVER_PORT")
    if raw_port is not None and str(raw_port).strip().isdigit():
        listener_port = int(str(raw_port).strip())
    if listener_port is not None and listener_port == settings.apps_origin_port():
        return True

    configured = _configured_origin()
    if not configured:
        return False
    configured_host = _split_host(urlsplit(configured).netloc, "https")[0]
    request_host = _split_host(request.host, request.scheme)[0]
    if not configured_host or request_host.lower() != configured_host.lower():
        return False
    if listener_port is not None and listener_port == settings.platform_port():
        return False  # the platform's own listener, reached by its own hostname
    return True


def available() -> bool:
    """Whether app content can be served on a separate origin right now.

    False means the second listener failed to start and no external origin is
    configured: the platform-origin route then refuses to serve rather than
    silently falling back to same-origin content, which is the whole point.
    """
    if settings.same_origin_mode() or settings.apps_origin_url():
        return True
    return bool(_state["available"])


def describe() -> str:
    if settings.same_origin_mode():
        return "same origin (HOSTED_APPS_ORIGIN_PORT=0) - apps share the platform's origin"
    configured = settings.apps_origin_url()
    if configured:
        return f"separate origin (configured): {configured}"
    if _state["available"]:
        return f"separate origin: port {settings.apps_origin_port()} ({_state['reason']})"
    return f"unavailable: {_state['reason']}"


# --- redirecting platform-origin requests ------------------------------------

def redirect_for(request, path: str | None = None):
    """A 308 to the apps origin, or ``None`` when already there.

    308 preserves the method and body, so an API call posted to the platform's
    path lands on the apps origin intact instead of degrading to a GET.
    ``path`` overrides the target path (used to add a trailing slash in one hop).
    """
    if is_apps_origin(request):
        return None
    candidate = path or request.full_path.rstrip("?")
    if _safe_target_path(candidate) is None:
        # A decoded newline in the path would otherwise raise inside Werkzeug and
        # turn a crafted URL into a 500.
        from flask import make_response

        return make_response(("<!doctype html><meta charset='utf-8'><title>Bad request</title>"
                              "<h1>Bad request</h1>"), 400, {"Content-Type": "text/html; charset=utf-8"})
    from flask import redirect

    return redirect(f"{apps_origin_for(request)}{candidate}", code=308)


def app_url(request, path: str) -> str:
    """Absolute apps-origin URL for a path such as ``/apps/demo/``."""
    suffix = path if path.startswith("/") else f"/{path}"
    return f"{apps_origin_for(request)}{suffix}"


# --- the apps-origin WSGI application ----------------------------------------

def plain_page(status: int, title: str, detail: str = ""):
    """A plain HTML response for platform error handlers running on this origin.

    The platform's own 403/404/500 pages are the SPA shell, which carries the
    session's CSRF token; rendering one here would hand it to the app's
    JavaScript. Error pages on the apps origin are therefore always plain.
    """
    from flask import make_response

    body = (f"<!doctype html><meta charset='utf-8'><title>{title}</title>"
            f"<h1>{title}</h1>" + (f"<p>{detail}</p>" if detail else ""))
    return make_response(body, status, {"Content-Type": "text/html; charset=utf-8"})


#: Deliberately small and theme-free: this page is served *on the apps origin*,
#: where the platform's stylesheet lives on the other origin.
_MESSAGE_PAGE = """<!doctype html><meta charset="utf-8"><title>{title}</title>
<style>body{{font:15px/1.5 system-ui,sans-serif;margin:4rem auto;max-width:34rem;padding:0 1rem;color:#222}}
a{{color:#0b62d0}}</style>
<h1>{title}</h1><p>{detail}</p><p><a href="{platform}/admin/hosted-apps">Back to Hosted Apps</a>
&middot; <a href="{platform}/">Platform home</a></p>"""


def message_page(request, status: int, title: str, detail: str):
    """A readable page for a browser that asked for an app and cannot have it.

    On the apps origin a bare JSON body is what a person sees when they mistype a
    slug or open an app that is stopped, so those cases answer with a page that
    says what happened and links back to the platform.
    """
    from flask import make_response

    platform = platform_origin_for(request)
    body = _MESSAGE_PAGE.format(title=title, detail=detail, platform=platform)
    return make_response(body, status, {"Content-Type": "text/html; charset=utf-8"})


def wants_html(request) -> bool:
    """Whether this is a browser navigation rather than an API call."""
    if (request.method or "").upper() not in ("GET", "HEAD"):
        return False
    accept = (request.headers.get("Accept") or "").lower()
    return "text/html" in accept


def _plain(start_response, status: int, title: str, detail: str):
    body = (
        f"<!doctype html><meta charset='utf-8'><title>{title}</title>"
        f"<h1>{title}</h1><p>{detail}</p>"
    ).encode("utf-8")
    start_response(
        f"{status} {'Not Found' if status == 404 else 'Forbidden' if status == 403 else 'Error'}",
        [("Content-Type", "text/html; charset=utf-8"), ("Content-Length", str(len(body))),
         ("X-Content-Type-Options", "nosniff"), ("Cache-Control", "no-store")],
    )
    return [body]


def _is_served_path(path: str) -> bool:
    """True for ``/apps/<slug>`` and below - never for the bare ``/apps`` or ``/apps/``.

    Those two have no blueprint route, so letting them through meant the
    platform's own 404 handler rendered the SPA shell *on the apps origin* -
    including a meta tag carrying the signed-in user's CSRF token, readable by the
    app's own JavaScript. Anything without a slug is now answered here instead.
    """
    return bool(_SERVED_PATH.match(path or ""))


def _safe_target_path(path: str) -> str | None:
    """Reject a redirect target carrying control characters (a decoded %0a)."""
    for char in path:
        if ord(char) < 0x20 or ord(char) == 0x7F:
            return None
    return path


def make_wsgi(inner):
    """Wrap a WSGI app so it serves *only* ``/apps/…``.

    Nothing else may be reachable here: an app's JavaScript is same-origin on this
    origin, so a platform path served here (``/admin/api/…``, ``/static/…``,
    ``/login``) would put the app back in the position this module removes.
    Platform redirects are rewritten to absolute URLs on the platform origin, so
    "please log in" still works from the apps origin.
    """

    def application(environ, start_response):
        path = environ.get("PATH_INFO") or "/"
        if not _is_served_path(path):
            return _plain(
                start_response, 404, "Not found",
                "This origin serves hosted applications only, under /apps/&lt;name&gt;/.",
            )

        platform = _platform_origin_from_environ(environ)

        def intercept(status, headers, exc_info=None):
            if platform:
                headers = [
                    (name, _rewrite_location(platform, value))
                    if name.lower() == "location" else (name, value)
                    for name, value in headers
                ]
            return start_response(status, headers, exc_info)

        return inner(environ, intercept)

    return application


def _platform_origin_from_environ(environ) -> str:
    configured = settings.platform_origin_url()
    if configured:
        return configured.rstrip("/")
    scheme = environ.get("wsgi.url_scheme", "http")
    host = environ.get("HTTP_HOST") or environ.get("SERVER_NAME", "")
    return _origin(scheme, host, settings.platform_port())


def _rewrite_location(platform: str, value: str) -> str:
    """Make a platform-relative redirect absolute; leave app paths relative."""
    if not value.startswith("/") or _is_served_path(value):
        return value
    return f"{platform}{value}"


# --- starting the second listener --------------------------------------------

def serve(flask_app, *, host: str | None = None, port: int | None = None) -> bool:
    """Start the apps origin in this process. Returns whether it is listening."""
    if not settings.enabled():
        _state.update(available=False, reason="hosting disabled")
        return False
    if settings.same_origin_mode():
        _state.update(available=False, reason="same-origin mode requested")
        logger.warning(
            "Hosted apps: HOSTED_APPS_ORIGIN_PORT=0 - apps are served from the "
            "platform's own origin, so a hosted app can call platform APIs as the "
            "signed-in user. Set a port (default 5001) or HOSTED_APPS_ORIGIN to fix it."
        )
        return False
    if settings.apps_origin_url():
        _state.update(available=True, reason=f"provided by configuration: {settings.apps_origin_url()}")
        logger.info("Hosted apps origin: %s", describe())
        return True

    host = host or settings.apps_origin_host()
    port = port or settings.apps_origin_port()
    from werkzeug.serving import make_server

    try:
        server = make_server(host, port, make_wsgi(flask_app), threaded=True)
    except (OSError, SystemExit) as exc:
        # Werkzeug answers a bind conflict with sys.exit(), which would take the
        # whole platform down over a port used by an unrelated service. The
        # platform must boot; only *app content* is refused (503) until the apps
        # origin has a port of its own.
        _state.update(available=False, reason=f"could not bind {host}:{port}: {exc}")
        logger.error(
            "Hosted apps: the apps origin could not listen on %s:%s (%s). Apps will "
            "not be served until it can: serving them from the platform origin instead "
            "would let any hosted app act as the signed-in user.", host, port, exc,
        )
        return False

    thread = threading.Thread(target=server.serve_forever, name="hosted-apps-origin", daemon=True)
    thread.start()
    _state.update(available=True, reason=f"listening on {host}:{port}", server=server)
    logger.info("Hosted apps origin: %s", describe())
    return True


def stop() -> None:
    server = _state.get("server")
    if server is not None:
        try:
            server.shutdown()
        except Exception:  # pragma: no cover - shutdown must not raise
            pass
