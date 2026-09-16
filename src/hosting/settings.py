"""Configuration for hosted apps.

The values live in :mod:`config.config` - with the settings this deployment
recommends - rather than in ``.env``. A ``.env`` file does not travel with the
code, so a deployment that never had one would start with hosting switched off
and the isolation tier unconfigured. Every value can still be overridden from the
environment; the overrides are read in ``config/config.py``.

This module is the single place the rest of the feature asks for configuration,
so nothing else needs to know how a value was supplied.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from config import config as platform_config


def enabled() -> bool:
    """Hosting is on unless a deployment switches it off."""
    return bool(platform_config.HOSTED_APPS_ENABLED)


def configured_tier() -> str:
    """``auto`` | ``A`` | ``B+`` | ``B``.

    ``auto`` adopts the strongest mechanism the kernel actually offers. Pinning a
    tier turns a silent downgrade into a startup failure, which is what you want
    on a node that is supposed to be isolated.
    """
    raw = str(platform_config.HOSTED_APPS_TIER or "auto").strip().lower()
    return {"a": "A", "b+": "B+", "b": "B", "auto": "auto"}.get(raw, "auto")


def autostart() -> bool:
    return bool(platform_config.HOSTED_APPS_AUTOSTART)


def allow_userns() -> bool:
    """Whether the platform may use unprivileged user namespaces.

    False on a site that forbids them by policy; the probe then selects Tier B+
    (Landlock) or Tier B.
    """
    return bool(platform_config.HOSTED_APPS_ALLOW_USERNS)


def allow_signals() -> bool:
    """Whether a hosted app may signal processes (default: no).

    Denying signals closes the sibling-process attack, at a real cost: gunicorn
    cannot reap a hung or timed-out worker. The platform stops apps by killing
    the whole process group, which does not need the app's cooperation.
    """
    return bool(platform_config.HOSTED_APPS_ALLOW_SIGNALS)


def allow_unconfined_install() -> bool:
    """Whether dependency installation may run without filesystem confinement.

    Installing a package executes third-party build code. Where the kernel can
    confine that (Landlock), we do. Where it cannot, the script would run with
    the platform's own privileges, so it is refused unless an administrator
    explicitly accepts the risk here.
    """
    return bool(platform_config.HOSTED_APPS_ALLOW_UNCONFINED_INSTALL)


def require_requirements() -> bool:
    """Whether an uploaded archive must carry requirements.txt.

    On by default: it makes the dependency set explicit and reviewable, and it is
    what builds the per-app virtualenv. An app that needs nothing beyond what the
    platform provides ships a file containing only comments.
    """
    return bool(platform_config.HOSTED_APPS_REQUIRE_REQUIREMENTS)


def build_venv() -> bool:
    """Whether to create a per-app virtualenv at install time."""
    return bool(platform_config.HOSTED_APPS_VENV)


def npm_install() -> bool:
    """Whether a package.json in the archive triggers an npm install."""
    return bool(platform_config.HOSTED_APPS_NPM_INSTALL)


def node_build() -> bool:
    """Whether a manifest ``build`` command is run after installing."""
    return bool(platform_config.HOSTED_APPS_NODE_BUILD)


def install_timeout() -> int:
    """Seconds allowed for a single installer command."""
    try:
        return int(platform_config.HOSTED_APPS_INSTALL_TIMEOUT)
    except (TypeError, ValueError):
        return 900


def install_net_ports() -> tuple[int, ...]:
    """TCP ports an install may connect to (empty tuple = no port narrowing)."""
    raw = str(getattr(platform_config, "HOSTED_APPS_INSTALL_NET_PORTS", "80,443") or "")
    ports: list[int] = []
    for item in raw.replace(";", ",").split(","):
        item = item.strip()
        if item.isdigit() and 0 < int(item) < 65536 and int(item) not in ports:
            ports.append(int(item))
    return tuple(ports)


def max_body_bytes() -> int:
    """Largest request body forwarded to a hosted app."""
    try:
        return int(platform_config.HOSTED_APPS_MAX_BODY_BYTES)
    except (TypeError, ValueError):
        return 32 * 1024 * 1024


def landlock_proc() -> bool:
    """Whether a Landlock-confined app may read /proc (withheld by default)."""
    return bool(platform_config.HOSTED_APPS_LANDLOCK_PROC)


def app_env() -> dict:
    """Extra environment variables handed to every hosted app."""
    import json

    raw = str(platform_config.HOSTED_APPS_APP_ENV or "").strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def root() -> Path:
    """Where hosted apps live.

    Kept outside the platform tree on purpose. Tier A hides the platform root and
    ``$HOME``; an apps root inside either of them still works (it is bound back),
    but a separate directory keeps the mount plan simple and obvious.
    """
    configured = str(platform_config.HOSTED_APPS_ROOT or "").strip()
    if configured:
        path = Path(configured).expanduser()
    else:
        from src.agent_platform.paths import uploads_dir

        path = uploads_dir() / "hosted-apps"
    path.mkdir(parents=True, exist_ok=True)
    return path


def apps_origin_url() -> str:
    """An operator-provided apps origin (``https://apps.example.com``), or "".

    When set, the platform does not bind a second listener: the deployment is
    expected to serve it (a reverse proxy, or a second WSGI instance started with
    ``APP_ORIGIN_ONLY=1``).
    """
    return str(getattr(platform_config, "HOSTED_APPS_ORIGIN", "") or "").strip()


def apps_origin_host() -> str:
    return str(getattr(platform_config, "HOSTED_APPS_ORIGIN_HOST", "") or "0.0.0.0").strip()


def apps_origin_port() -> int:
    """Port the apps origin listens on. 0 means same-origin (unsafe, migration only)."""
    try:
        return int(getattr(platform_config, "HOSTED_APPS_ORIGIN_PORT", 5001))
    except (TypeError, ValueError):
        return 5001


def same_origin_mode() -> bool:
    """Whether apps are (still) served from the platform's own origin.

    Only true when the port is explicitly set to 0. It is a migration setting:
    same-origin hosting lets any hosted app act as the signed-in user through the
    browser, which no process sandbox can prevent.
    """
    return apps_origin_port() <= 0 and not apps_origin_url()


def platform_origin_url() -> str:
    """The platform's public origin, when it differs from the request's own."""
    return str(getattr(platform_config, "HOSTED_APPS_PLATFORM_ORIGIN", "") or "").strip()


def platform_port() -> int:
    """Port the platform itself is served on.

    The configured platform origin wins when there is one: in the two-process
    deployment the apps-origin process is started with ``PORT`` set to the *apps*
    port, so ``PORT`` alone would answer with the wrong number there.
    """
    configured = platform_origin_url()
    if configured:
        from urllib.parse import urlsplit

        parts = urlsplit(configured)
        if parts.port:
            return parts.port
        if parts.scheme:
            return 443 if parts.scheme == "https" else 80
    try:
        return int(os.getenv("PORT", "5000"))
    except ValueError:
        return 5000


def platform_root() -> Path:
    """The Text2SQL application root - the tree a hosted app must never see."""
    configured = str(platform_config.HOSTED_APPS_PLATFORM_ROOT or "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    from src.agent_platform.paths import APP_ROOT

    return APP_ROOT


def python_executable() -> str:
    """Interpreter a hosted app runs under (per-app venvs build on top of it)."""
    configured = str(platform_config.HOSTED_APPS_PYTHON or "").strip()
    return configured or sys.executable


def identity_secret() -> bytes:
    """Key for signing the identity headers handed to a hosted app.

    Derived from the platform secret so no extra configuration is required, and
    so a hosted app cannot compute it from anything it is given.
    """
    import hashlib
    import hmac

    base = os.getenv("SECRET_KEY", DEFAULT_SECRET_KEY).encode()
    return hmac.new(base, b"hosted-apps-identity-v1", hashlib.sha256).digest()


#: The placeholder ``config.SECRET_KEY`` falls back to. Anything signed with it
#: is forgeable by anyone who can read the source, which is why the boot log
#: says so (see ``capabilities.exposure_warnings``).
DEFAULT_SECRET_KEY = "default-dev-key-change-in-production"


def secret_key_is_default() -> bool:
    """Whether the platform secret is still the built-in placeholder."""
    return os.getenv("SECRET_KEY", DEFAULT_SECRET_KEY) == DEFAULT_SECRET_KEY


def tier_requires_filesystem_isolation() -> bool:
    """Whether the operator demanded a tier that hides the platform's files."""
    return configured_tier() in ("A", "B+")
