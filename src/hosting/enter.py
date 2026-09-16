"""Tier A: build a filesystem view inside the new namespaces, then exec the app.

Invoked by ``launcher.py`` as ``unshare ... python -m src.hosting.enter``; the
plan arrives in ``$APPHOST_PLAN``.

Two details make this work with no privileges at all:

1. Every bind source is opened as an ``O_PATH`` descriptor *before* anything is
   hidden, because the sources become unreachable the moment their parent tree
   is overmounted. This is the same technique bubblewrap uses.

2. Mounts go through the ``mount(2)`` syscall rather than the ``mount`` command.
   util-linux canonicalises the source path with ``realpath()`` in userspace,
   which rewrites ``/proc/<pid>/fd/N`` back into the original path - a path the
   tmpfs now covers - so it silently binds an *empty* directory with no error.
   The kernel resolves the magic link to the real dentry instead.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import json
import os
import sys

# Every import must happen here, before the platform tree is overmounted:
# importing afterwards fails, because the module files are no longer reachable.
from src.hosting import settings
from src.hosting.confine import apply_rlimits, denied_syscalls
from src.hosting.confine import apply_seccomp as _apply_seccomp

MS_RDONLY, MS_REMOUNT, MS_BIND = 1, 32, 4096
MOUNT_ATTR_RDONLY, AT_RECURSIVE, AT_FDCWD = 0x1, 0x8000, -100
SYS_MOUNT_SETATTR = 442

_libc = ctypes.CDLL(ctypes.util.find_library("c") or "libc.so.6", use_errno=True)


class _MountAttr(ctypes.Structure):
    _fields_ = [
        ("attr_set", ctypes.c_uint64),
        ("attr_clr", ctypes.c_uint64),
        ("propagation", ctypes.c_uint64),
        ("userns_fd", ctypes.c_uint64),
    ]


def _fail(what: str, errno: int) -> None:
    sys.stderr.write(f"hosting.enter: {what} failed: {os.strerror(errno)}\n")
    raise SystemExit(70)


def mount(source: str | None, target: str, fstype: str | None = None, flags: int = 0) -> None:
    """``mount(2)`` - the source is passed through untouched, magic links included."""
    result = _libc.mount(
        source.encode() if source else None,
        target.encode(),
        fstype.encode() if fstype else None,
        ctypes.c_ulong(flags),
        None,
    )
    if result != 0:
        _fail(f"mount({source or fstype} -> {target})", ctypes.get_errno())


def remount_read_only(target: str) -> bool:
    """Best effort: hardening, not the security boundary."""
    attr = _MountAttr(attr_set=MOUNT_ATTR_RDONLY, attr_clr=0, propagation=0, userns_fd=0)
    result = _libc.syscall(
        SYS_MOUNT_SETATTR, ctypes.c_int(AT_FDCWD), target.encode(),
        ctypes.c_uint(AT_RECURSIVE), ctypes.byref(attr), ctypes.c_size_t(ctypes.sizeof(attr)),
    )
    if result == 0:
        return True
    result = _libc.mount(None, target.encode(), None, ctypes.c_ulong(MS_REMOUNT | MS_BIND | MS_RDONLY), None)
    return result == 0


def confine_process(enabled: bool | None) -> None:
    """Deny socket creation and process attacks, exactly as in tiers B and B+.

    Everything here comes from modules imported before the platform tree was
    hidden. The imported function is aliased because a local name of the same
    name would shadow it and this call would silently recurse into itself.
    """
    if not enabled:
        return
    try:
        _apply_seccomp(denied=denied_syscalls(settings.allow_signals()))
    except Exception as exc:  # never let hardening prevent an app from starting
        sys.stderr.write(f"hosting.enter: seccomp not applied: {exc}\n")


def main() -> None:
    raw = os.environ.pop("APPHOST_PLAN", None)
    if not raw:
        sys.stderr.write("hosting.enter: APPHOST_PLAN is missing\n")
        raise SystemExit(70)
    plan = json.loads(raw)

    # 1. Stage bind sources as descriptors while their real paths still resolve.
    staged = []
    for source, target, mode in plan["binds"]:
        if os.path.exists(source):
            staged.append((os.open(source, os.O_PATH | os.O_CLOEXEC), target, mode))

    # 2. Hide the platform: an empty tmpfs over each sensitive tree.
    for hide in plan["hides"]:
        if os.path.isdir(hide):
            mount(None, hide, "tmpfs")

    # 3. Restore only what the app legitimately needs, through the staged
    #    descriptors. The path must name *our* pid: the `mount` command is a
    #    separate process, where /proc/self/fd/N would be its own table.
    for fd, target, mode in staged:
        os.makedirs(target, exist_ok=True)
        mount(f"/proc/{os.getpid()}/fd/{fd}", target, flags=MS_BIND)
        if mode == "ro":
            remount_read_only(target)
        os.close(fd)

    # 4. Private scratch space, so /tmp is not a channel to the platform.
    tmpdir = plan.get("tmp")
    if tmpdir:
        os.makedirs(tmpdir, exist_ok=True)
        mount(None, tmpdir, "tmpfs")

    # 5. Hand over to the app with a clean environment and working directory.
    for key in list(os.environ):
        # PYTHONPATH points at the platform root: useless to the app (it is
        # hidden) and a leak of the platform's layout into a process that has no
        # business knowing it.
        if key.startswith(("APPHOST_", "TEXT2SQL", "FLASK_", "SECRET")) or key == "PYTHONPATH":
            os.environ.pop(key, None)
    os.environ.update(plan.get("env") or {})
    apply_rlimits(plan.get("limits"))
    confine_process(plan.get("seccomp"))
    os.chdir(plan["cwd"])
    argv = plan["argv"]
    try:
        os.execv(argv[0], argv)
    except OSError as exc:
        # A missing dynamic loader or shebang interpreter also surfaces as
        # ENOENT, so report both facts rather than just the errno.
        sys.stderr.write(
            f"hosting.enter: cannot exec {argv[0]}: {exc} "
            f"(exists={bool(argv[0]) and os.path.exists(argv[0])}, cwd={os.getcwd()})\n"
        )
        raise


if __name__ == "__main__":
    main()
