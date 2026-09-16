"""The per-application log file: rotation, the start banner, and tailing.

Split out of the supervisor because it is self-contained file handling, and
because a hosted app's log needs two things the supervisor should not have to
think about: it must never be mysteriously empty (gunicorn only writes at info
level), and it must never grow without bound.
"""

from __future__ import annotations

import time
from pathlib import Path

#: Rotate once the file passes this, keeping one previous file beside it.
MAX_LOG_BYTES = 5 * 1024 * 1024

#: How much of the tail is read when serving the logs view.
TAIL_WINDOW_BYTES = 256 * 1024


def rotate(path: Path) -> None:
    """Move an oversized log aside so the next start begins cleanly."""
    try:
        if path.exists() and path.stat().st_size > MAX_LOG_BYTES:
            previous = path.with_suffix(path.suffix + ".1")
            previous.unlink(missing_ok=True)
            path.rename(previous)
    except OSError:
        pass  # housekeeping must never block a start


def banner(path: Path, *, slug: str, tier: str, reason: str, isolated: bool, command: list[str]) -> None:
    """Record what was started, under which tier, and with which command.

    A healthy app would otherwise produce no output at all - gunicorn only
    speaks at info level - so an operator opening the logs would see an empty
    box and no explanation.
    """
    lines = [
        "",
        "=" * 78,
        f"[hosting] starting '{slug}' at {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"[hosting] isolation tier : {tier} ({reason})",
        "[hosting] filesystem     : "
        + ("isolated - the platform's files are hidden or denied to this app"
           if isolated else
           "NOT isolated - this app can read the platform's files"),
        f"[hosting] command        : {' '.join(command)}",
        "=" * 78,
    ]
    try:
        with path.open("a", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")
    except OSError:
        pass


def append(path: Path, text: str) -> None:
    """Append a line to a log, never raising: logging must not break an install."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(text if text.endswith("\n") else text + "\n")
    except OSError:
        pass


def tail(path: Path, lines: int = 200) -> str:
    """The last ``lines`` of the log, reading only the end of a possibly huge file."""
    try:
        size = path.stat().st_size
        with path.open("rb") as handle:
            handle.seek(max(0, size - TAIL_WINDOW_BYTES))
            content = handle.read().decode("utf-8", "replace")
    except OSError:
        return ""
    return "\n".join(content.splitlines()[-lines:])
