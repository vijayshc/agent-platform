"""Landlock applied from Python: no root, no helper binary, no package.

Landlock is a *self*-applied restriction - that is its design premise - so a
launcher we already control can impose it on the app it is about to exec using
nothing but libc syscalls. This is the same set of syscalls the `landrun` CLI
wraps; calling them directly removes a whole supply chain from the picture.

Guarantees depend on the kernel ABI, so the ABI is always reported:

    ABI 1 (5.13)  filesystem only
    ABI 4 (6.7)   + TCP bind/connect control
    ABI 6 (6.12)  + IPC scoping (signals, abstract unix sockets)
    ABI 9         + pathname unix-socket control

Not even the kernel can restrict ``chdir``/``stat``/``flock``/``chmod``/
``chown``/``setxattr``/``utime``/``fcntl``/``access`` through Landlock - that
list is documented in Documentation/userspace-api/landlock.rst.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import os

_SYS_CREATE_RULESET = 444
_SYS_ADD_RULE = 445
_SYS_RESTRICT_SELF = 446

_CREATE_RULESET_VERSION = 1
_RULE_PATH_BENEATH = 1
_PR_SET_NO_NEW_PRIVS = 38

# Filesystem rights, with the ABI that introduced each.
EXECUTE, WRITE_FILE, READ_FILE, READ_DIR = 1 << 0, 1 << 1, 1 << 2, 1 << 3
REMOVE_DIR, REMOVE_FILE, MAKE_CHAR, MAKE_DIR = 1 << 4, 1 << 5, 1 << 6, 1 << 7
MAKE_REG, MAKE_SOCK, MAKE_FIFO, MAKE_BLOCK = 1 << 8, 1 << 9, 1 << 10, 1 << 11
MAKE_SYM, REFER, TRUNCATE, IOCTL_DEV = 1 << 12, 1 << 13, 1 << 14, 1 << 15

_FS_RIGHTS = (
    (EXECUTE, 1), (WRITE_FILE, 1), (READ_FILE, 1), (READ_DIR, 1),
    (REMOVE_DIR, 1), (REMOVE_FILE, 1), (MAKE_CHAR, 1), (MAKE_DIR, 1),
    (MAKE_REG, 1), (MAKE_SOCK, 1), (MAKE_FIFO, 1), (MAKE_BLOCK, 1),
    (MAKE_SYM, 1), (REFER, 2), (TRUNCATE, 3), (IOCTL_DEV, 5),
)

# Network rights (ABI 4+). UDP is not covered by Landlock before ABI 10, which
# is why seccomp is always applied alongside this.
NET_BIND_TCP, NET_CONNECT_TCP = 1 << 0, 1 << 1
SCOPE_ABSTRACT_UNIX_SOCKET, SCOPE_SIGNAL = 1 << 0, 1 << 1
_RULE_NET_PORT = 2

READ_ONLY = EXECUTE | READ_FILE | READ_DIR
READ_WRITE = 0xFFFF  # masked down to the rights the running ABI supports

#: Rights that only apply to a directory. Granting these for a regular file or
#: device makes landlock_add_rule fail with EINVAL.
_DIR_ONLY = (READ_DIR | REMOVE_DIR | REMOVE_FILE | MAKE_CHAR | MAKE_DIR | MAKE_REG
             | MAKE_SOCK | MAKE_FIFO | MAKE_BLOCK | MAKE_SYM | REFER)

_libc = ctypes.CDLL(ctypes.util.find_library("c") or "libc.so.6", use_errno=True)


class _RulesetAttr(ctypes.Structure):
    _fields_ = [
        ("handled_access_fs", ctypes.c_uint64),
        ("handled_access_net", ctypes.c_uint64),
        ("scoped", ctypes.c_uint64),
    ]


class _PathBeneathAttr(ctypes.Structure):
    _pack_ = 1
    _fields_ = [("allowed_access", ctypes.c_uint64), ("parent_fd", ctypes.c_int32)]


class _NetPortAttr(ctypes.Structure):
    """struct landlock_net_port_attr: the port a rule grants access to."""

    _fields_ = [("allowed_access", ctypes.c_uint64), ("port", ctypes.c_uint64)]


def abi_version() -> int:
    """Landlock ABI version, or 0 when Landlock is unavailable."""
    result = _libc.syscall(
        _SYS_CREATE_RULESET, None, ctypes.c_size_t(0), ctypes.c_uint32(_CREATE_RULESET_VERSION)
    )
    return int(result) if result >= 0 else 0


def _handled_fs(abi: int) -> int:
    mask = 0
    for right, since in _FS_RIGHTS:
        if abi >= since:
            mask |= right
    return mask


def _add_path_rule(ruleset_fd: int, path: str, access: int) -> bool:
    try:
        fd = os.open(path, os.O_PATH | os.O_CLOEXEC)
    except OSError:
        return False  # optional path that does not exist on this host
    try:
        if not os.path.isdir(path):
            access &= ~_DIR_ONLY
        attr = _PathBeneathAttr(allowed_access=access, parent_fd=fd)
        result = _libc.syscall(
            _SYS_ADD_RULE, ctypes.c_int(ruleset_fd), ctypes.c_int(_RULE_PATH_BENEATH),
            ctypes.byref(attr), ctypes.c_uint32(0),
        )
        if result < 0:
            raise OSError(ctypes.get_errno(), f"landlock_add_rule({path})")
        return True
    finally:
        os.close(fd)


def _add_net_port_rule(ruleset_fd: int, port: int, access: int) -> bool:
    """Allow TCP connect to one port (ABI 4+); every other port stays denied."""
    if not access:
        return False
    attr = _NetPortAttr(allowed_access=access, port=port)
    result = _libc.syscall(
        _SYS_ADD_RULE, ctypes.c_int(ruleset_fd), ctypes.c_int(_RULE_NET_PORT),
        ctypes.byref(attr), ctypes.c_uint32(0),
    )
    if result < 0:
        raise OSError(ctypes.get_errno(), f"landlock_add_rule(port {port})")
    return True


def restrict(read_only=(), read_write=(), deny_network: bool = True, scope_ipc: bool = True,
             net_connect_ports: "tuple[int, ...] | None" = None) -> int:
    """Confine the calling process and its descendants; returns the ABI used.

    ``read_only`` paths may be read and executed, ``read_write`` paths get every
    right the kernel supports. Anything not listed is denied.

    ``net_connect_ports`` is the other shape of network policy: instead of
    denying the network outright (``deny_network``), handle TCP and then *allow*
    only those ports, so an install can reach the package index but not a service
    on loopback. Landlock rules are allow-rules, so anything not listed is denied.
    """
    abi = abi_version()
    if abi == 0:
        return 0

    handled_fs = _handled_fs(abi)
    wants_ports = bool(net_connect_ports) and abi >= 4
    handled_net = (NET_BIND_TCP | NET_CONNECT_TCP) if ((deny_network or wants_ports) and abi >= 4) else 0
    scoped = (SCOPE_ABSTRACT_UNIX_SOCKET | SCOPE_SIGNAL) if (scope_ipc and abi >= 6) else 0

    attr = _RulesetAttr(handled_access_fs=handled_fs, handled_access_net=handled_net, scoped=scoped)
    size = 24 if abi >= 6 else (16 if abi >= 4 else 8)
    ruleset_fd = _libc.syscall(
        _SYS_CREATE_RULESET, ctypes.byref(attr), ctypes.c_size_t(size), ctypes.c_uint32(0)
    )
    if ruleset_fd < 0:
        raise OSError(ctypes.get_errno(), "landlock_create_ruleset")

    try:
        for path in read_only:
            _add_path_rule(ruleset_fd, path, READ_ONLY & handled_fs)
        for path in read_write:
            _add_path_rule(ruleset_fd, path, READ_WRITE & handled_fs)
        if wants_ports:
            for port in net_connect_ports:
                _add_net_port_rule(ruleset_fd, int(port), NET_CONNECT_TCP & handled_net)

        # Landlock refuses to confine a process that could still gain privileges.
        if _libc.prctl(_PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0:
            raise OSError(ctypes.get_errno(), "prctl(PR_SET_NO_NEW_PRIVS)")
        if _libc.syscall(_SYS_RESTRICT_SELF, ctypes.c_int(ruleset_fd), ctypes.c_uint32(0)) < 0:
            raise OSError(ctypes.get_errno(), "landlock_restrict_self")
    finally:
        os.close(ruleset_fd)
    return abi
