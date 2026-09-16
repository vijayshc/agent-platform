"""The confinement mechanisms applied to a hosted app.

Three layers, each covering the others' blind spots:

* **Landlock** (tier B+ only; tier A does not need it, because the paths are
  simply not there) - path-based filesystem rules plus TCP control. Cannot
  restrict ``stat``/``chmod``/``utime``.
* **seccomp** (always) - syscall surface. Because the parent owns the listening
  socket and hands it over as an inherited descriptor, the app never needs to
  create one, so ``socket``/``socketpair``/``bind``/``connect`` can all be
  denied. That closes local service reachability on every kernel, including
  RHEL 8 where Landlock does not exist. ``listen`` must stay allowed because
  gunicorn calls it on the inherited socket to set the backlog.
* **rlimits** (always) - bounds on address space, processes, CPU, file size and
  descriptors. Namespaces and Landlock bound what an app can *reach*, not how
  much it can *consume*.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import os
import resource
import sys

# --- seccomp --------------------------------------------------------------

SCMP_ACT_ALLOW = 0x7FFF0000
SCMP_ACT_ERRNO = 0x00050000
SCMP_CMP_EQ = 4
SCMP_CMP_MASKED_EQ = 7
EPERM, ENOSYS = 1, 38

AF_INET, AF_INET6, AF_NETLINK, AF_PACKET = 2, 10, 16, 17


class _ScmpArgCmp(ctypes.Structure):
    """struct scmp_arg_cmp from <seccomp.h>."""

    _fields_ = [
        ("arg", ctypes.c_uint),
        ("op", ctypes.c_uint),
        ("datum_a", ctypes.c_uint64),
        ("datum_b", ctypes.c_uint64),
    ]


#: Denied outright. ``socket`` is denied for every family, including AF_UNIX:
#: the app inherits its listening socket from the platform and never needs to
#: create one, so there is no exception to make.
#:
#: Two families deserve a note because leaving them out silently voids controls
#: that are otherwise advertised:
#:
#: * ``io_uring_*`` - io_uring operations are **not syscalls**, so seccomp never
#:   sees them. ``IORING_OP_SOCKET``/``IORING_OP_CONNECT`` (Linux 5.19+) create
#:   and connect sockets while ``socket``/``connect`` are denied, and
#:   ``sendto`` - which an app legitimately never needs - then carries the data
#:   out. Measured: a tier B+ app sent a UDP datagram past the seccomp ban.
#:   Docker, ChromeOS and Cilium deny io_uring for the same reason.
#: * ``rt_sigqueueinfo``/``rt_tgsigqueueinfo`` - the queued-signal form of
#:   ``kill``. Denying ``kill``/``tkill``/``tgkill`` alone left the sibling-kill
#:   attack open on every kernel without Landlock IPC scoping (ABI < 6), i.e.
#:   exactly the tier B floor. The ``pidfd_*`` family is denied for the same
#:   reason.
DENIED_SYSCALLS = (
    "socket", "socketpair", "bind", "connect",
    "kill", "tkill", "tgkill",                      # stops sibling-process attacks
    "rt_sigqueueinfo", "rt_tgsigqueueinfo",         # ...including the queued form
    "ptrace", "process_vm_readv", "process_vm_writev", "kcmp",
    "process_madvise", "process_mrelease",
    # pidfd_getfd would let an app take a copy of a file descriptor it was
    # never given, including a socket - it needs pidfd_open first, so both go.
    "pidfd_open", "pidfd_getfd", "pidfd_send_signal",
    "mount", "umount2", "pivot_root", "chroot", "setns", "unshare",
    # The new mount API reaches the same mount table as mount(2) without
    # calling it, so denying mount(2) alone is not a mount policy.
    "fsopen", "fsconfig", "fsmount", "fspick", "move_mount", "open_tree",
    "mount_setattr",
    # io_uring: not a syscall once the ring exists, so the ring can never exist.
    "io_uring_setup", "io_uring_enter", "io_uring_register",
    "kexec_load", "init_module", "finit_module", "delete_module", "bpf",
    "perf_event_open", "userfaultfd", "open_by_handle_at", "name_to_handle_at",
    "add_key", "keyctl", "request_key", "reboot", "swapon", "swapoff", "acct",
    "quotactl", "settimeofday", "clock_settime", "adjtimex",
)

#: ``clone3`` takes its flags inside a struct the filter cannot read, so it is
#: answered with ENOSYS rather than EPERM: glibc treats ENOSYS as "not
#: implemented" and falls back to ``clone(2)``, whose flags *can* be filtered.
#: (An EPERM would make thread creation fail outright on glibc 2.34+.)
ENOSYS_SYSCALLS = ("clone3",)

#: Namespace-creating clone flags. ``unshare``/``setns`` are denied, but a
#: namespace can also be created by ``clone``/``clone3``; without this an app can
#: still give itself CAP_SYS_ADMIN in a fresh user namespace (measured) and reach
#: the new mount API, which is a large kernel attack surface.
#:
#: One rule is added per flag, deliberately. ``SCMP_CMP_MASKED_EQ`` compares
#: ``(flags & datum_a) == datum_b``, so a single rule carrying the *combined*
#: mask only matches a clone that sets every namespace flag at once and misses
#: the realistic ``CLONE_NEWUSER`` on its own - measured, and the reason the
#: first version of this rule did nothing.
CLONE_NEWNS = 0x00020000
CLONE_NEWTIME = 0x00000080
CLONE_NEWCGROUP = 0x02000000
CLONE_NEWUTS = 0x04000000
CLONE_NEWIPC = 0x08000000
CLONE_NEWUSER = 0x10000000
CLONE_NEWPID = 0x20000000
CLONE_NEWNET = 0x40000000
NAMESPACE_CLONE_FLAGS = (
    CLONE_NEWTIME, CLONE_NEWNS, CLONE_NEWCGROUP, CLONE_NEWUTS, CLONE_NEWIPC,
    CLONE_NEWUSER, CLONE_NEWPID, CLONE_NEWNET,
)

#: The install profile needs the network (the package index) and its own
#: children, so the deny-list differs from the run profile: no socket/connect
#: denial, but listeners, the mount/namespace surface and the kernel-attack
#: surface stay closed. ``kill`` is only denied when the installer could not be
#: given its own pid namespace - inside one, its signals cannot leave.
INSTALL_ALWAYS_DENIED = tuple(
    name for name in DENIED_SYSCALLS
    if name not in ("socket", "socketpair", "connect", "bind",
                    "kill", "tkill", "tgkill",
                    "rt_sigqueueinfo", "rt_tgsigqueueinfo")
)
INSTALL_LISTENER_DENIED = ("bind", "listen", "accept", "accept4")
INSTALL_SIGNAL_DENIED = ("kill", "tkill", "tgkill", "rt_sigqueueinfo", "rt_tgsigqueueinfo")
#: ``setsid``/``setpgid`` move a process out of the group the install timeout
#: kills, so a build command could leave a detached process behind and hold the
#: output pipe open forever. Measured before this rule existed.
INSTALL_DETACH_DENIED = ("setsid", "setpgid")


def install_denied_syscalls(pid_namespace: bool = True) -> tuple[str, ...]:
    """The deny-list for a dependency install (network open, listeners shut).

    The pid namespace is the primary defence - inside it the installer cannot
    reach the platform's processes at all - and the rules below are what remains
    when the kernel cannot give one.
    """
    denied = list(INSTALL_ALWAYS_DENIED) + list(INSTALL_LISTENER_DENIED) + list(INSTALL_DETACH_DENIED)
    if not pid_namespace:
        denied += list(INSTALL_SIGNAL_DENIED)
    return tuple(dict.fromkeys(denied))

#: The app should not allocate its way through the host. RLIMIT_AS is per
#: process, so a multi-worker app multiplies these; without cgroup delegation
#: (unavailable to an unprivileged user) there is no aggregate cap.
DEFAULT_LIMITS = {
    "as": 2 * 1024 ** 3,
    "nproc": 256,
    "cpu": 600,
    "fsize": 512 * 1024 ** 2,
    "nofile": 4096,
    "core": 0,
}

#: An install legitimately compiles and links, so it gets more room than a
#: running app - but bounded, because it is also the one moment third-party
#: build code executes. Before this existed the installer ran with every limit
#: unlimited (measured: RLIMIT_AS -1, RLIMIT_CPU -1, 63 427 processes).
INSTALL_LIMITS = {
    "as": 4 * 1024 ** 3,
    "nproc": 256,
    "cpu": 900,
    "fsize": 2 * 1024 ** 3,
    "nofile": 4096,
    "core": 0,
}


#: Signals are the one policy choice in the list: denying them closes the
#: sibling-process attack but also prevents gunicorn from reaping a hung worker.
SIGNAL_SYSCALLS = ("kill", "tkill", "tgkill")


def denied_syscalls(allow_signals: bool = False) -> tuple[str, ...]:
    if not allow_signals:
        return DENIED_SYSCALLS
    return tuple(name for name in DENIED_SYSCALLS if name not in SIGNAL_SYSCALLS)


def apply_seccomp(denied=DENIED_SYSCALLS, *, enosys=ENOSYS_SYSCALLS,
                  namespace_flags: int | None = NAMESPACE_CLONE_FLAGS,
                  deny_internet_families: bool | None = None) -> int:
    """Install the filter on the calling process. Unprivileged; sets no_new_privs.

    ``deny_internet_families`` defaults to "only when ``socket`` itself is
    denied" - the internet address families must stay reachable for an installer
    that has to fetch packages, and the rule is redundant wherever socket
    creation is denied outright.
    """
    lib = ctypes.CDLL(ctypes.util.find_library("seccomp") or "libseccomp.so.2", use_errno=True)
    lib.seccomp_init.restype = ctypes.c_void_p
    lib.seccomp_init.argtypes = [ctypes.c_uint32]
    lib.seccomp_load.argtypes = [ctypes.c_void_p]
    lib.seccomp_release.argtypes = [ctypes.c_void_p]
    lib.seccomp_syscall_resolve_name.restype = ctypes.c_int
    lib.seccomp_syscall_resolve_name.argtypes = [ctypes.c_char_p]
    lib.seccomp_rule_add.argtypes = [
        ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int, ctypes.c_uint
    ]
    lib.seccomp_rule_add_array.argtypes = [
        ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int, ctypes.c_uint, ctypes.c_void_p
    ]

    ctx = lib.seccomp_init(SCMP_ACT_ALLOW)
    if not ctx:
        raise RuntimeError("seccomp_init failed")
    applied = 0

    def deny(number: int, errno: int) -> None:
        nonlocal applied
        rc = lib.seccomp_rule_add(ctx, ctypes.c_uint32(SCMP_ACT_ERRNO | errno),
                                  ctypes.c_int(number), ctypes.c_uint(0))
        if rc != 0:
            raise RuntimeError(f"seccomp_rule_add({number}) rc={rc}")
        applied += 1

    def deny_when(number: int, comparison: "_ScmpArgCmp", errno: int = EPERM) -> None:
        nonlocal applied
        rc = lib.seccomp_rule_add_array(
            ctx, ctypes.c_uint32(SCMP_ACT_ERRNO | errno), ctypes.c_int(number),
            ctypes.c_uint(1), ctypes.byref(comparison),
        )
        if rc != 0:
            raise RuntimeError(f"seccomp_rule_add_array({number}) rc={rc}")
        applied += 1

    try:
        for name in dict.fromkeys(denied):
            number = lib.seccomp_syscall_resolve_name(name.encode())
            if number < 0:
                continue  # not present on this architecture
            deny(number, EPERM)

        # ENOSYS rather than EPERM: glibc falls back to clone(2), whose flags the
        # rule below can actually filter.
        for name in enosys:
            number = lib.seccomp_syscall_resolve_name(name.encode())
            if number >= 0:
                deny(number, ENOSYS)

        if namespace_flags:
            clone_number = lib.seccomp_syscall_resolve_name(b"clone")
            if clone_number >= 0:
                for flag in namespace_flags:
                    deny_when(clone_number, _ScmpArgCmp(
                        arg=0, op=SCMP_CMP_MASKED_EQ, datum_a=flag, datum_b=flag,
                    ))

        if deny_internet_families is None:
            deny_internet_families = "socket" in denied
        if deny_internet_families:
            # Belt and braces for kernels where Landlock cannot restrict network:
            # deny internet address families, keep AF_UNIX for the inherited socket.
            socket_number = lib.seccomp_syscall_resolve_name(b"socket")
            if socket_number >= 0:
                for family in (AF_INET, AF_INET6, AF_NETLINK, AF_PACKET):
                    deny_when(socket_number, _ScmpArgCmp(
                        arg=0, op=SCMP_CMP_EQ, datum_a=family, datum_b=0,
                    ))

        rc = lib.seccomp_load(ctx)
        if rc != 0:
            raise RuntimeError(f"seccomp_load rc={rc}")
    finally:
        lib.seccomp_release(ctx)
    return applied


# --- rlimits --------------------------------------------------------------

def apply_rlimits(limits: dict | None = None) -> None:
    merged = {**DEFAULT_LIMITS, **(limits or {})}
    pairs = (
        (resource.RLIMIT_AS, merged["as"]),
        (resource.RLIMIT_NPROC, merged["nproc"]),
        (resource.RLIMIT_CPU, merged["cpu"]),
        (resource.RLIMIT_FSIZE, merged["fsize"]),
        (resource.RLIMIT_NOFILE, merged["nofile"]),
        (resource.RLIMIT_CORE, merged["core"]),
    )
    for what, value in pairs:
        try:
            _soft, hard = resource.getrlimit(what)
            ceiling = value if hard == resource.RLIM_INFINITY else min(value, hard)
            resource.setrlimit(what, (ceiling, ceiling))
        except (ValueError, OSError):
            continue  # a limit the platform forbids us to lower is not fatal


# --- Landlock policy ------------------------------------------------------

#: Readable and executable so the interpreter, the venv and normal libraries
#: load. ``/proc`` is deliberately absent: withholding it hides the host's
#: process table and its ``environ`` files, which Landlock can otherwise do
#: nothing about. Set HOSTED_APPS_LANDLOCK_PROC=1 for apps that need it.
LANDLOCK_READ_ONLY = ("/usr", "/lib", "/lib64", "/bin", "/sbin", "/etc")
LANDLOCK_READ_WRITE_EXTRA = ("/tmp", "/dev/null", "/dev/urandom")


def landlock_paths(app_dir: str, venv_dir: str | None = None) -> tuple[list[str], list[str]]:
    read_only = list(LANDLOCK_READ_ONLY)
    from src.hosting import settings

    if settings.landlock_proc():
        read_only.append("/proc")
    if sys.prefix not in read_only:
        read_only.append(sys.prefix)  # the base interpreter the venv is built on
    if venv_dir and os.path.isdir(venv_dir) and venv_dir not in read_only:
        read_only.append(venv_dir)    # the app's own environment, read+execute
    return read_only, [app_dir, *LANDLOCK_READ_WRITE_EXTRA]


#: The install profile reads /proc as well: package managers want to know how
#: many CPUs there are, and with a pid namespace of its own the table it reads
#: contains nothing but the installer. Without that namespace the grant is a way
#: back to the platform's secrets - ``/proc/<pid>/environ`` is readable as the
#: same user wherever Yama's ``ptrace_scope`` is 0 - so the caller only passes it
#: when the namespace is available.
LANDLOCK_INSTALL_READ_ONLY = ("/proc",)


def node_prefixes() -> list[str]:
    """Wherever node/npm actually live, so the install profile can grant them.

    A Node toolchain is often installed under the user's home (nvm and friends),
    which the install profile does not otherwise expose - and without this the
    confinement denies ``exec`` on npm itself, which looks like a permissions
    bug rather than a policy one.
    """
    import shutil as _shutil
    from pathlib import Path as _Path

    prefixes: list[str] = []
    for name in ("node", "npm", "npx"):
        found = _shutil.which(name)
        if not found:
            continue
        real = _Path(found).resolve()
        prefix = real.parent.parent if real.parent.name == "bin" else real.parent
        if str(prefix) not in prefixes:
            prefixes.append(str(prefix))
    return prefixes


def resolver_paths() -> list[str]:
    """The real location of the name-resolution files.

    ``/etc/resolv.conf`` is frequently a symlink - to ``/run/systemd/resolve/``
    on a systemd host, to ``/mnt/wsl/`` under WSL. Landlock resolves the link
    before applying the rule, so granting only ``/etc`` leaves the resolver
    unable to read its own configuration and every lookup fails with
    ``EAI_AGAIN`` - which looks like a network fault rather than a policy one.
    """
    from pathlib import Path as _Path

    resolved: list[str] = []
    for candidate in ("/etc/resolv.conf", "/etc/hosts", "/etc/nsswitch.conf", "/etc/gai.conf"):
        path = _Path(candidate)
        if not path.exists():
            continue
        real = path.resolve()
        if str(real) != candidate and str(real) not in resolved:
            resolved.append(str(real))
    return resolved


def install_profile_paths(app_dir: str, include_proc: bool = True) -> tuple[list[str], list[str]]:
    """Filesystem policy for running pip or npm at install time.

    Installation is the one moment third-party build code executes, so it gets
    its own profile: it may write inside the app's own directory and read the
    system libraries, and that is all. The network is deliberately left open -
    the installer has to reach PyPI or the npm registry - which is why this
    profile is never used to *run* an app.
    """
    read_only = [*LANDLOCK_READ_ONLY, *node_prefixes(), *resolver_paths()]
    if include_proc:
        read_only += list(LANDLOCK_INSTALL_READ_ONLY)
    if sys.prefix not in read_only:
        read_only.append(sys.prefix)
    read_write = [app_dir, "/tmp", "/dev/null", "/dev/urandom"]
    return read_only, read_write


def apply_install_profile(app_dir: str, *, include_proc: bool = True,
                          net_ports: tuple[int, ...] | None = None) -> int:
    """Confine this process for a dependency install; returns the Landlock ABI (0 = unavailable).

    ``net_ports`` narrows egress to those TCP ports (the package index). Landlock
    can do that from ABI 4, and it is what keeps an install away from loopback
    services - the platform's own API, sibling app sockets - while leaving the
    index reachable.
    """
    from src.hosting import landlock

    read_only, read_write = install_profile_paths(app_dir, include_proc=include_proc)
    return landlock.restrict(
        read_only=read_only, read_write=read_write, deny_network=False,
        scope_ipc=False, net_connect_ports=net_ports,
    )


def apply_landlock(app_dir: str, venv_dir: str | None = None) -> int:
    """Confine the calling process's filesystem + TCP + IPC view."""
    from src.hosting import landlock

    read_only, read_write = landlock_paths(app_dir, venv_dir)
    return landlock.restrict(
        read_only=read_only, read_write=read_write, deny_network=True, scope_ipc=True
    )
