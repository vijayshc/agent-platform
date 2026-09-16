"""Where a hosted app's files live.

Kept apart from the supervisor so the install pipeline can use the same paths
without importing the lifecycle code, and so there is exactly one definition of
each location.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from src.hosting import settings


def app_root(slug: str) -> Path:
    return settings.root() / slug


def code_dir(slug: str) -> Path:
    return app_root(slug) / "app"


def venv_dir(slug: str) -> Path:
    return app_root(slug) / "venv"


def run_dir(slug: str) -> Path:
    return app_root(slug) / "run"


def install_log_path(slug: str) -> Path:
    return app_root(slug) / "logs" / "install.log"


def log_path(slug: str) -> Path:
    return app_root(slug) / "logs" / "app.log"


def report_path(slug: str) -> Path:
    return app_root(slug) / "runtime.json"


def socket_path(slug: str) -> Path:
    """A short, fixed-length socket path.

    AF_UNIX limits a socket path to about 108 bytes, and a long slug under the
    default root would silently exceed it. The app never sees this path - it
    inherits the descriptor - so it can be derived from a hash instead.
    """
    digest = hashlib.sha1(slug.encode("utf-8")).hexdigest()[:16]
    return settings.root() / ".sockets" / f"{digest}.sock"


def interpreter(slug: str) -> str:
    """The venv interpreter when one exists, else the platform interpreter."""
    candidate = venv_dir(slug) / "bin" / "python"
    return str(candidate) if candidate.exists() else settings.python_executable()


