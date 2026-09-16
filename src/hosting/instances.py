"""Which platform instance owns a hosted app, and which process is which.

Two platform processes can share one database - a second development instance,
or an overlapping rolling restart - and each must only manage the apps it
actually started. That needs two answers the operating system does not give
directly:

* is the instance recorded in a row still running?
* is the pid recorded in a row really that app, and not a recycled pid?

Pids are reused, so neither question can be answered by ``kill(pid, 0)`` alone.
The start time from ``/proc/<pid>/stat`` is stable for the life of a process and
is what distinguishes "the process I recorded" from "a different process that
happens to have the same pid".
"""

from __future__ import annotations

import os
import socket
from pathlib import Path


def proc_starttime(pid: int) -> str | None:
    """Field 22 of ``/proc/<pid>/stat``: the process start time."""
    try:
        data = Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        return None
    try:
        # The second field is the command name in parentheses and may contain
        # spaces, so fields are counted from after the final closing paren.
        return data[data.rindex(")") + 2:].split()[19]
    except (ValueError, IndexError):
        return None


#: Identifies *this* platform run for as long as it lives.
INSTANCE_TOKEN = f"{socket.gethostname()}:{os.getpid()}:{proc_starttime(os.getpid()) or 0}"


def instance_alive(owner: str) -> bool:
    """Whether the platform instance named in ``owner`` is still running."""
    host, _, rest = owner.partition(":")
    pid_text, _, starttime = rest.partition(":")
    if host != socket.gethostname() or not pid_text.isdigit():
        return False
    pid = int(pid_text)
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    if not starttime or starttime == "0":
        return True  # legacy two-part token: fall back to plain liveness
    return proc_starttime(pid) == starttime


def process_is_app(pid: int, app_directory: str) -> bool:
    """Whether ``pid`` really is the hosted app rooted at ``app_directory``.

    The command line is no help: gunicorn rewrites its process title to
    ``gunicorn: master [_host_wsgi:application]``, and on tier A the recorded pid
    is the ``unshare`` supervisor, which only ever names the app in the plan it
    carries. Two things do survive and are specific to this app:

    * the environment the launcher gave it - ``HOME`` for tiers B/B+, or the
      ``APPHOST_PLAN`` for tier A;
    * its working directory, which is inside the app directory.
    """
    try:
        environ = Path(f"/proc/{pid}/environ").read_bytes().decode("utf-8", "replace")
    except OSError:
        environ = ""
    for entry in environ.split("\0") if environ else ():
        if entry == f"HOME={app_directory}":
            return True
        if entry.startswith("APPHOST_PLAN=") and app_directory in entry:
            return True
    try:
        cwd = os.readlink(f"/proc/{pid}/cwd")
    except OSError:
        return False
    return cwd == app_directory or cwd.startswith(app_directory + os.sep)
