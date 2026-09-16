"""Authenticated reverse proxy for Arize Phoenix AI Observability.

Tenancy decision (security-critical)
------------------------------------
Phoenix keeps a single, process-wide project store: it cannot be scoped to the
calling tenant per request, so this proxy cannot enforce "only the traces of
agents you may access".  Serving it to every ``observability`` user therefore
leaked every tenant's traces through the raw Phoenix UI.  The proxy is now
restricted to administrators (``@admin_required()``); the built-in runs API
(``/api/v1/runs``) -- which resolves each run's agent definition through
``resource_access`` -- is the tenant-scoped observability surface for everyone
else.  The ``/phoenix/status`` and ``/phoenix/projects`` area endpoints keep
their existing ``observability`` module gate (see ``run_routes``).

Access is still authenticated: an unauthenticated caller gets 401 before the
admin gate is consulted.  This is a deliberate, documented reduction in
surface, not a silent degradation.
"""

from __future__ import annotations

import logging
from typing import Any

import requests
from flask import Blueprint, Response, jsonify, request, session

from src.agent_platform.api.auth import api_auth_required, current_user_id
from src.auth.decorators import admin_required
from src.services.phoenix_service import get_phoenix_url

logger = logging.getLogger("text2sql.api.phoenix_proxy")

phoenix_proxy_bp = Blueprint("phoenix_proxy", __name__, url_prefix="/api/v1/phoenix/proxy")

_EXCLUDED_FORWARD_HEADERS = {
    "host",
    "content-length",
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
}


# Injected before Phoenix's own theme bootstrap so first paint matches the host app.
# Phoenix only supports light/dark; our lightColored theme maps to light.
_PHOENIX_THEME_SYNC_SCRIPT = """<script data-text2sql-phoenix-theme>
(function () {
  try {
    var app = localStorage.getItem("selectedTheme") || sessionStorage.getItem("selectedTheme") || "light";
    var phoenix = app === "dark" ? "dark" : "light";
    localStorage.setItem("arize-phoenix-theme", phoenix);
  } catch (e) {}
})();
</script>"""


def _rewrite_phoenix_html(html: str, proxy_prefix: str = "/api/v1/phoenix/proxy") -> str:
    """Rewrite static asset paths, router basename, and host-app theme in Phoenix index.html."""
    html = html.replace('href="/assets/', f'href="{proxy_prefix}/assets/')
    html = html.replace('src="/assets/', f'src="{proxy_prefix}/assets/')
    html = html.replace('src="/modernizr.js"', f'src="{proxy_prefix}/modernizr.js"')
    html = html.replace('href="/favicon.ico"', f'href="{proxy_prefix}/favicon.ico"')
    html = html.replace('basename: ""', f'basename: "{proxy_prefix}"')
    html = html.replace("basename: ''", f"basename: '{proxy_prefix}'")
    if "<head>" in html and f'<base href="{proxy_prefix}/">' not in html:
        html = html.replace("<head>", f'<head>\n    <base href="{proxy_prefix}/">', 1)
    if "data-text2sql-phoenix-theme" not in html and "<head>" in html:
        html = html.replace("<head>", f"<head>\n    {_PHOENIX_THEME_SYNC_SCRIPT}", 1)
    return html


@phoenix_proxy_bp.route("", defaults={"subpath": ""}, methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "HEAD"])
@phoenix_proxy_bp.route("/", defaults={"subpath": ""}, methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "HEAD"])
@phoenix_proxy_bp.route("/<path:subpath>", methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "HEAD"])
@api_auth_required("runs:read")
@admin_required()
def proxy_phoenix(subpath: str):
    """Administrator-only reverse proxy to the internal Phoenix server."""
    user_id = current_user_id()
    if not user_id:
        return jsonify({"error": "Authentication required"}), 401

    phoenix_base = get_phoenix_url()
    
    # If subpath is a client-side route like projects/..., fetch root index.html from Phoenix
    if not subpath or (not subpath.startswith("assets/") and subpath not in {"graphql", "modernizr.js", "favicon.ico"} and not subpath.startswith("v1/")):
        target_url = f"{phoenix_base}/"
    else:
        target_url = f"{phoenix_base}/{subpath}"

    # Forward headers and attach internal proxy secret
    headers = {
        key: value
        for key, value in request.headers.items()
        if key.lower() not in _EXCLUDED_FORWARD_HEADERS
    }
    headers["X-Internal-Phoenix-Auth"] = "agent_platform_phoenix_secret_2026"

    try:
        resp = requests.request(
            method=request.method,
            url=target_url,
            params=request.args,
            data=request.get_data(),
            headers=headers,
            allow_redirects=False,
            timeout=30.0,
        )
    except Exception as exc:
        logger.error("Failed to proxy request to Phoenix at %s: %s", target_url, exc)
        return jsonify({"error": "Phoenix service unavailable", "details": str(exc)}), 503

    content_type = resp.headers.get("Content-Type", "")
    
    # Rewrite HTML index page to support proxied asset URLs and router basename
    if "text/html" in content_type:
        body = _rewrite_phoenix_html(resp.text)
        response = Response(body, status=resp.status_code, mimetype="text/html")
    else:
        response = Response(resp.content, status=resp.status_code, content_type=content_type)

    if "Cache-Control" in resp.headers:
        response.headers["Cache-Control"] = resp.headers["Cache-Control"]

    return response

