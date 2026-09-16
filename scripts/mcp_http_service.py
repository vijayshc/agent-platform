#!/usr/bin/env python3
"""Serve an MCP tool module over streamable HTTP -- the HTTP MCP service runner.

An HTTP MCP server is an endpoint, not a subprocess: the app only ever connects
to a URL. This script is the endpoint side, kept out of the framework so the
platform itself starts nothing.

Serve one module in the foreground::

    python scripts/mcp_http_service.py \\
        --module platform_samples.mcp_servers.text2sql --port 8765 --token "$MCP_HTTP_TOKEN"

Bring up every registered server that declares ``config.service`` (a one-shot
pass; children are detached and log to ``logs/mcp-http-<name>.log``)::

    python scripts/mcp_http_service.py --autostart
    python scripts/mcp_http_service.py --autostart --dry-run

The bearer token of an autostarted server is taken from the row's own
``Authorization`` header, so the service enforces exactly the credential the
catalog sends. Servers without a ``service`` block are probes only and are
never touched -- they are somebody else's endpoint.
"""

from __future__ import annotations

import argparse
import importlib
import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

import requests  # noqa: E402
from mcp.server.fastmcp import FastMCP  # noqa: E402

logger = logging.getLogger("mcp_http_service")

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PATH = "/mcp"
HEALTH_PATH = "/healthz"
BEARER_PREFIX = "bearer "
READY_TIMEOUT_S = 20.0
PROBE_TIMEOUT_S = 3.0

_INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2024-11-05",
        "capabilities": {},
        "clientInfo": {"name": "mcp-http-service", "version": "1"},
    },
}


# --------------------------------------------------------------------- serving


def load_server(module_name: str) -> FastMCP:
    """The ``mcp`` FastMCP instance exposed by a platform MCP module."""
    module = importlib.import_module(module_name)
    server = getattr(module, "mcp", None)
    if not isinstance(server, FastMCP):
        raise SystemExit(f"{module_name} does not expose a FastMCP() instance named 'mcp'")
    return server


def bearer_token(authorization: Any) -> str:
    """The token inside an ``Authorization: Bearer <token>`` header (else '')."""
    value = str(authorization or "").strip()
    if value.lower().startswith(BEARER_PREFIX):
        return value[len(BEARER_PREFIX):].strip()
    return value


def build_app(
    module_name: str, *, path: str = DEFAULT_PATH, token: str | None = None, stateless: bool = True
) -> Any:
    """ASGI app serving ``module_name``'s tools over streamable HTTP."""
    server = load_server(module_name)
    server.settings.streamable_http_path = path
    # Stateless: a request may land on a fresh session, so an agent run that
    # reconnects mid-conversation never depends on which worker holds state.
    server.settings.stateless_http = stateless
    app = server.streamable_http_app()
    return require_bearer(app, token) if token else _with_health(app)


def require_bearer(app: Any, token: str) -> Any:
    """Wrap an ASGI app so only ``Authorization: Bearer <token>`` gets through."""
    expected = f"Bearer {token}"

    async def guarded(scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await app(scope, receive, send)
            return
        if scope.get("path") == HEALTH_PATH:
            await _plain(send, 200, b"ok")
            return
        headers = {k.lower(): v for k, v in scope.get("headers") or []}
        presented = headers.get(b"authorization", b"").decode("latin-1").strip()
        if presented != expected:
            logger.warning("Rejected unauthenticated %s", scope.get("path"))
            await _plain(send, 401, b"unauthorized")
            return
        await app(scope, receive, send)

    return guarded


def _with_health(app: Any) -> Any:
    async def healthy(scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] == "http" and scope.get("path") == HEALTH_PATH:
            await _plain(send, 200, b"ok")
            return
        await app(scope, receive, send)

    return healthy


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


def serve(args: argparse.Namespace) -> None:
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    app = build_app(args.module, path=args.path, token=args.token)
    logger.info(
        "Serving %s on http://%s:%s%s (auth=%s)",
        args.module,
        args.host,
        args.port,
        args.path,
        "bearer" if args.token else "none",
    )
    import uvicorn

    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


# ------------------------------------------------------------------ autostart


def is_reachable(url: str, token: str | None = None, timeout: float = PROBE_TIMEOUT_S) -> bool:
    """True when ``url`` answers a real MCP initialize request."""
    if not url:
        return False
    headers = {"Accept": "application/json, text/event-stream"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        response = requests.post(url, json=_INITIALIZE, headers=headers, timeout=timeout)
    except requests.RequestException:
        return False
    return response.status_code == 200


def service_spec(server: Any) -> dict[str, Any]:
    """The ``config.service`` block of a catalog row ({} when it has none)."""
    config = getattr(server, "config", None) or {}
    spec = config.get("service")
    if not isinstance(spec, dict) or not str(spec.get("module") or "").strip():
        return {}
    return spec


def start_server(server: Any, *, wait_s: float = READY_TIMEOUT_S, dry_run: bool = False) -> tuple[bool, str]:
    """Probe ``server``'s endpoint and launch its service when it is down."""
    config = getattr(server, "config", None) or {}
    url = str(config.get("url") or "").strip()
    token = bearer_token((config.get("headers") or {}).get("Authorization")) or None
    if is_reachable(url, token):
        return True, f"already serving {url}"

    command = _command(service_spec(server), url)
    if dry_run:
        return False, "would start: " + " ".join(command)

    log_path = _log_path(server.name)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logger.info("Starting HTTP MCP service for '%s': %s", server.name, " ".join(command))
    with log_path.open("ab") as log:
        subprocess.Popen(  # noqa: S603 - module path comes from an admin-registered row
            command,
            cwd=str(APP_ROOT),
            env=_child_env(token),
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )

    deadline = time.monotonic() + max(1.0, wait_s)
    while time.monotonic() < deadline:
        if is_reachable(url, token):
            return True, f"started {url}"
        time.sleep(0.4)
    return False, f"{url} did not answer within {int(wait_s)}s (see {log_path})"


def _command(spec: dict[str, Any], url: str) -> list[str]:
    """The child argv. The bearer token is NOT here: argv is world-readable via
    ``ps`` (and would be echoed by --dry-run), so it is passed in the environment
    instead -- see ``_child_env``."""
    parsed = urlparse(url)
    return [
        sys.executable,
        str(Path(__file__).resolve()),
        "--module",
        str(spec["module"]),
        "--host",
        str(spec.get("host") or parsed.hostname or DEFAULT_HOST),
        "--port",
        str(spec.get("port") or parsed.port or 8765),
        "--path",
        str(spec.get("path") or parsed.path or DEFAULT_PATH),
    ]


def _child_env(token: str | None) -> dict[str, str]:
    from src.agent_platform.paths import pythonpath_env

    env = dict(os.environ)
    env["PYTHONPATH"] = pythonpath_env()
    env["APP_ROOT"] = str(APP_ROOT)
    env.setdefault("PYTHONUNBUFFERED", "1")
    if token:
        env["MCP_HTTP_TOKEN"] = token
    return env


def _log_path(name: str) -> Path:
    slug = "".join(c if c.isalnum() or c in "-_" else "-" for c in str(name)) or "service"
    return APP_ROOT / "logs" / f"mcp-http-{slug}.log"


def autostart(*, dry_run: bool = False) -> list[dict[str, Any]]:
    """One pass over catalog rows that declare a local service."""
    from src.models.mcp_server import MCPServer

    report: list[dict[str, Any]] = []
    for server in MCPServer.get_all():
        if not service_spec(server):
            continue
        try:
            ok, message = start_server(server, dry_run=dry_run)
        except Exception as exc:  # one broken row must not stop the others
            logger.exception("Autostart failed for '%s'", server.name)
            ok, message = False, str(exc)
        (logger.info if ok else logger.warning)("HTTP MCP '%s': %s", server.name, message)
        report.append({"name": server.name, "ok": ok, "message": message})
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--module", default=os.environ.get("MCP_HTTP_MODULE"), help="MCP module to serve")
    parser.add_argument("--host", default=os.environ.get("MCP_HTTP_HOST", DEFAULT_HOST))
    parser.add_argument("--port", type=int, default=int(os.environ.get("MCP_HTTP_PORT", "8765")))
    parser.add_argument("--path", default=os.environ.get("MCP_HTTP_PATH", DEFAULT_PATH))
    parser.add_argument(
        "--token",
        default=os.environ.get("MCP_HTTP_TOKEN"),
        help="require this bearer token (omit to serve unauthenticated)",
    )
    parser.add_argument(
        "--autostart",
        action="store_true",
        help="start every registered server with a config.service block, then exit",
    )
    parser.add_argument("--dry-run", action="store_true", help="with --autostart: print what would start")
    args = parser.parse_args(argv)

    if args.autostart:
        logging.basicConfig(level=logging.INFO, stream=sys.stderr)
        report = autostart(dry_run=args.dry_run)
        print(json.dumps(report, indent=1) if report else "No catalog server declares a local service.")
        return 0 if all(row["ok"] or args.dry_run for row in report) else 1

    if not args.module:
        parser.error("--module is required (or use --autostart)")
    serve(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
