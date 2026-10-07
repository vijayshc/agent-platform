#!/usr/bin/env python3
"""Serve an MCP tool module over streamable HTTP -- the HTTP MCP service runner.

An HTTP MCP server is an endpoint, not a subprocess: the app only ever connects
to a URL. This script is the endpoint side, kept out of the framework so the
platform itself starts nothing.

Every request is authenticated by ``src.mcp_server_auth``: the caller must
present the app's own access token (``Authorization: Bearer <token>``), minted
for the user whose run the call belongs to. The gate is applied to the whole
application, so ``initialize``, ``tools/list`` and ``tools/call`` are all
covered, and a tool reads the verified caller with
``src.mcp_server_auth.current_caller()``.

Serve one module in the foreground::

    python scripts/mcp_http_service.py \\
        --module platform_samples.mcp_servers.text2sql --port 8765

Bring up every registered server that declares ``config.service`` (a one-shot
pass; children are detached and log to ``logs/mcp-http-<name>.log``)::

    python scripts/mcp_http_service.py --autostart
    python scripts/mcp_http_service.py --autostart --dry-run

Servers without a ``service`` block are somebody else's endpoint and are never
touched -- an external server answers to its own credential, not to this host.

A row's ``config`` carries the endpoint and, when this host serves it, the
service block::

    {
      "url": "http://127.0.0.1:8765/mcp",
      "service": {
        "module": "platform_samples.mcp_servers.text2sql",
        "port": 8765,
        "python": "/path/to/.venv/bin/python",
        "pythonpath": "/path/to/that/sample"
      }
    }

``url`` names the endpoint (it is what the app connects to), and the service
block names the module to serve. ``host``/``port``/``path`` default from ``url``;
``python`` and ``pythonpath`` are only needed by a module that runs in its own
environment.
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
from urllib.parse import urlparse, urlunparse

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

import requests  # noqa: E402
from mcp.server.fastmcp import FastMCP  # noqa: E402

from src.mcp_server_auth import HEALTH_PATH, require_bearer  # noqa: E402

logger = logging.getLogger("mcp_http_service")

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PATH = "/mcp"
READY_TIMEOUT_S = 20.0
PROBE_TIMEOUT_S = 3.0


# --------------------------------------------------------------------- serving


def load_server(module_name: str) -> FastMCP:
    """The ``mcp`` FastMCP instance exposed by a platform MCP module."""
    module = importlib.import_module(module_name)
    server = getattr(module, "mcp", None)
    if not isinstance(server, FastMCP):
        raise SystemExit(f"{module_name} does not expose a FastMCP() instance named 'mcp'")
    return server


def build_app(
    module_name: str, *, path: str = DEFAULT_PATH, stateless: bool = True
) -> Any:
    """ASGI app serving ``module_name``'s tools over streamable HTTP, gated."""
    server = load_server(module_name)
    server.settings.streamable_http_path = path
    # Stateless: a request may land on a fresh session, so an agent run that
    # reconnects mid-conversation never depends on which worker holds state.
    server.settings.stateless_http = stateless
    return require_bearer(server.streamable_http_app())


def health_url(url: str) -> str:
    """The ``/healthz`` URL of the host serving ``url`` (no token needed)."""
    parsed = urlparse(url)
    return urlunparse((parsed.scheme, parsed.netloc, HEALTH_PATH, "", "", ""))


def is_reachable(url: str, timeout: float = PROBE_TIMEOUT_S) -> bool:
    """True when the service at ``url`` answers its health endpoint."""
    if not url:
        return False
    try:
        response = requests.get(health_url(url), timeout=timeout)
    except requests.RequestException:
        return False
    return response.status_code == 200


def serve(args: argparse.Namespace) -> None:
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    app = build_app(args.module, path=args.path)
    logger.info(
        "Serving %s on http://%s:%s%s (auth=app access token)",
        args.module,
        args.host,
        args.port,
        args.path,
    )
    import uvicorn

    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


# -------------------------------------------------------------------- autostart


def service_spec(server: Any) -> dict[str, Any]:
    """The ``config.service`` block of a catalog row ({} when it has none)."""
    config = getattr(server, "config", None) or {}
    spec = config.get("service")
    if not isinstance(spec, dict) or not str(spec.get("module") or "").strip():
        return {}
    return spec


def start_server(
    server: Any, *, wait_s: float = READY_TIMEOUT_S, dry_run: bool = False
) -> tuple[bool, str]:
    """Probe ``server``'s endpoint and launch its service when it is down."""
    config = getattr(server, "config", None) or {}
    url = str(config.get("url") or "").strip()
    if is_reachable(url):
        return True, f"already serving {url}"

    spec = service_spec(server)
    command = _command(spec, url)
    if dry_run:
        return False, "would start: " + " ".join(command)

    log_path = _log_path(server.name)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logger.info("Starting HTTP MCP service for '%s': %s", server.name, " ".join(command))
    with log_path.open("ab") as log:
        subprocess.Popen(  # noqa: S603 - module path comes from an admin-registered row
            command,
            cwd=str(APP_ROOT),
            env=_child_env(spec),
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )

    deadline = time.monotonic() + max(1.0, wait_s)
    while time.monotonic() < deadline:
        if is_reachable(url):
            return True, f"started {url}"
        time.sleep(0.4)
    return False, f"{url} did not answer within {int(wait_s)}s (see {log_path})"


def _command(spec: dict[str, Any], url: str) -> list[str]:
    """The child argv. No credential appears here: a served module authenticates
    its callers with the app's own tokens, so there is nothing to pass.

    ``spec.python`` names the interpreter when the module needs its own
    environment (a sample server whose dbt/duckdb stack must not touch the
    platform's); it defaults to this interpreter.
    """
    parsed = urlparse(url)
    return [
        str(spec.get("python") or sys.executable),
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


def _child_env(spec: dict[str, Any]) -> dict[str, str]:
    """The child's environment. ``spec.pythonpath`` adds import roots the module
    needs beyond the platform tree (e.g. the sample package it lives in)."""
    from src.agent_platform.paths import pythonpath_env

    env = dict(os.environ)
    parts = [pythonpath_env()]
    extra = str(spec.get("pythonpath") or "").strip()
    if extra:
        parts.append(extra)
    env["PYTHONPATH"] = os.pathsep.join(parts)
    env["APP_ROOT"] = str(APP_ROOT)
    env.setdefault("PYTHONUNBUFFERED", "1")
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
