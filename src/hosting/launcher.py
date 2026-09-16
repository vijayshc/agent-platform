"""Launch one hosted app under the strongest confinement this host offers.

    python -m src.hosting.launcher --app-dir DIR --platform-root DIR \
        --tier auto --socket-fd 7 --report FILE -- CMD ...

Tier A re-execs through ``unshare`` so ``src.hosting.enter`` can build the
filesystem view. Tiers B and B+ confine *this* process (rlimits, then Landlock
when available, then seccomp) and then become the app.

The listening socket is created by the parent and inherited here, so the app
never calls ``socket()``/``bind()``/``connect()`` and those can be denied
outright - on every kernel, including ones with no Landlock at all.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path

if __package__ in (None, ""):  # allow `python src/hosting/launcher.py`
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
    from src.hosting import capabilities, confine, settings
else:
    from src.hosting import capabilities, confine, settings

EXIT_TIER_UNAVAILABLE = 69


def _app_environment(app_dir: str, extra: dict | None = None) -> dict:
    """A deliberately small environment.

    The platform's own environment is never inherited: SECRET_KEY and every
    configured credential stay with the parent.
    """
    env = {
        "PATH": f"{app_dir}/venv/bin:/usr/bin:/bin",
        "HOME": app_dir,
        "TMPDIR": "/tmp",
        "LANG": "C.UTF-8",
        "PYTHONUNBUFFERED": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        # Informational: the path a confined app must NOT be able to read. It
        # exists so an app can report its own confinement honestly (the demo app
        # does exactly that), and it reveals nothing the app could not guess.
        "HOSTED_APP_PLATFORM_PROBE": str(settings.platform_root() / "text2sql.db"),
    }
    env.update(settings.app_env())
    env.update(extra or {})
    return env


def _tier_a_plan(app_dir: str, command: list[str], venv_dir: str = "", env: dict | None = None) -> dict:
    home = str(Path.home())
    platform_root = str(settings.platform_root())
    # /dev/shm and /run/user/<uid> are shared, writable IPC locations: leaving
    # them visible let a tier A app still reach a neighbour's socket there.
    hides = []
    for candidate in (platform_root, home, "/dev/shm", f"/run/user/{os.getuid()}"):
        if candidate and os.path.isdir(candidate) and candidate not in hides:
            hides.append(candidate)

    binds = [(app_dir, app_dir, "rw")]
    if venv_dir and os.path.isdir(venv_dir):
        binds.append((venv_dir, venv_dir, "ro"))
    # If the interpreter the app runs on lives under a tree we are about to
    # hide, bind it back read-only at its original path so exec keeps working.
    prefix = sys.prefix
    if any(prefix == h or prefix.startswith(h.rstrip("/") + "/") for h in hides) and prefix != app_dir:
        binds.append((prefix, prefix, "ro"))

    return {
        "argv": command,
        "cwd": app_dir,
        "env": env if env is not None else _app_environment(app_dir),
        "hides": hides,
        "binds": binds,
        "tmp": "/tmp",
        "limits": confine.DEFAULT_LIMITS,
        "seccomp": capabilities.detect()["seccomp"],
    }


def _write_report(path: str, payload: dict) -> None:
    try:
        Path(path).write_text(json.dumps(payload, indent=2))
    except OSError:
        pass  # a missing report must never stop an app from starting


def launch(
    *,
    app_dir: str,
    command: list[str],
    tier: str = "auto",
    socket_fd: int | None = None,
    report_path: str = "",
    venv_dir: str = "",
    extra_env: dict | None = None,
) -> int:
    """Replace this process with the confined app. Returns an exit code on refusal."""
    caps = capabilities.detect()
    selected, reason = capabilities.resolve_tier(caps, tier)
    if selected is None:
        sys.stderr.write(f"hosting.launcher: {reason}\n")
        return EXIT_TIER_UNAVAILABLE

    # Everything that touches the platform tree is computed BEFORE confinement.
    # A lazy import after Landlock is applied is denied by the very rules we
    # just installed - settings.platform_root() imports src.agent_platform.paths
    # on first use, so ordering here is not cosmetic.
    app_env = _app_environment(app_dir, extra_env)

    report = {
        "tier": selected,
        "tier_reason": reason,
        "capabilities": {k: v for k, v in caps.items() if k != "tier_reason"},
        "app_dir": app_dir,
        "command": command,
        "socket_fd": socket_fd,
        "filesystem_isolated": selected in ("A", "B+"),
        "pid": os.getpid(),
        "started_at": time.time(),
    }
    if report_path:
        _write_report(report_path, report)

    if selected == "A":
        plan = _tier_a_plan(app_dir, command, venv_dir, app_env)
        module_root = str(Path(__file__).resolve().parent.parent.parent)
        env = {
            "APPHOST_PLAN": json.dumps(plan),
            "PYTHONPATH": module_root,
            "PATH": "/usr/bin:/bin",
        }
        os.execve(
            "/usr/bin/unshare",
            [
                "unshare", "--user", "--map-root-user",
                "--mount", "--net", "--pid", "--fork", "--mount-proc", "--propagation", "private",
                sys.executable, "-m", "src.hosting.enter",
            ],
            env,
        )

    # Tiers B and B+: confine this process, then become the app.
    try:
        os.setsid()  # harmless, and EPERM when we are already a session leader
    except OSError:
        pass
    confine.apply_rlimits()
    if selected == "B+" and caps["landlock_abi"]:
        confine.apply_landlock(app_dir, venv_dir or None)
    if caps["seccomp"]:
        confine.apply_seccomp(confine.denied_syscalls(settings.allow_signals()))

    os.environ.clear()
    os.environ.update(app_env)
    os.chdir(app_dir)
    os.execv(command[0], command)
    return 0


def run_install(*, app_dir: str, command: list[str], refuse_marker: str = "") -> int:
    """Exec an installer confined to the filesystem, with the network open.

    Installation is the one moment third-party build code runs, so it gets its
    own profile, applied in this order:

    1. **Its own pid namespace**, where the kernel offers one. Without it an
       install script could signal (or read ``/proc/<pid>/environ`` of) the
       platform's own processes; inside it, neither is possible. The namespace is
       entered *first*, because mapping the uid needs to write
       ``/proc/self/uid_map`` and the filesystem confinement below is read-only
       on ``/proc``.
    2. **Landlock** - the app's directory writable, the system libraries and the
       Python/Node toolchains readable, everything else denied. Where the kernel
       cannot confine this, the install is **refused** rather than run with the
       platform's privileges.
    3. **rlimits and seccomp** - bounded memory, processes, CPU and file size,
       and no listeners, no io_uring, no mount or namespace surface. ``socket``
       and ``connect`` stay allowed: the package index has to be reachable.
    """
    os.environ.pop("PYTHONPATH", None)
    caps = capabilities.detect()
    pid_namespace = bool(caps.get("userns")) and bool(shutil.which("unshare"))
    already_namespaced = os.environ.pop("APPHOST_INSTALL_NAMESPACED", "") == "1"

    if pid_namespace and not already_namespaced:
        env = dict(os.environ)
        env.update({
            "APPHOST_INSTALL_NAMESPACED": "1",
            # The inner stage re-imports this module; only for that.
            "PYTHONPATH": str(Path(__file__).resolve().parent.parent.parent),
        })
        os.execve(
            "/usr/bin/unshare",
            [
                "unshare", "--user", "--map-root-user",
                "--mount", "--pid", "--fork", "--mount-proc", "--propagation", "private",
                sys.executable, "-m", "src.hosting.launcher",
                "--app-dir", app_dir, "--install-profile", "--", *command,
            ],
            env,
        )

    abi = confine.apply_install_profile(
        app_dir, include_proc=pid_namespace, net_ports=settings.install_net_ports()
    )
    if not abi:
        sys.stderr.write(
            "hosting.launcher: this kernel cannot confine a dependency install "
            "(Landlock is unavailable). Refusing to run third-party build code with the "
            "platform's privileges; set HOSTED_APPS_ALLOW_UNCONFINED_INSTALL=true to accept that risk.\n"
        )
        # Out of band, because the exit status is the installer's once we exec:
        # a manifest's build command could otherwise `exit 69` and talk the
        # platform into re-running it with no confinement at all. The marker path
        # is chosen by the parent, outside every path the install profile allows.
        _write_refusal_marker(refuse_marker)
        return EXIT_TIER_UNAVAILABLE
    confine.apply_rlimits(confine.INSTALL_LIMITS)
    confine.apply_seccomp(
        confine.install_denied_syscalls(pid_namespace), deny_internet_families=False
    )
    os.execv(command[0], command)
    return 0


def _write_refusal_marker(path: str) -> None:
    if not path:
        return
    try:
        Path(path).write_text("refused\n")
    except OSError:
        pass  # the caller falls back to treating 69 as an ordinary failure


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="src.hosting.launcher")
    parser.add_argument("--app-dir", required=True)
    parser.add_argument("--tier", default=None, help="auto | A | B+ | B (default: configuration)")
    parser.add_argument("--socket-fd", type=int, default=None)
    parser.add_argument("--venv-dir", default="")
    parser.add_argument(
        "--install-profile", action="store_true",
        help="confine the filesystem for a dependency install, leaving the network open",
    )
    parser.add_argument(
        "--refuse-marker", default="",
        help="file to create when the install is refused (checked by the caller, "
             "because the exit status afterwards belongs to the installer)",
    )
    parser.add_argument("--report", default="")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)

    command = [part for part in args.command if part != "--"]
    if not command:
        parser.error("no command given")

    if args.install_profile:
        return run_install(
            app_dir=str(Path(args.app_dir).resolve()), command=command,
            refuse_marker=args.refuse_marker,
        )

    return launch(
        app_dir=str(Path(args.app_dir).resolve()),
        command=command,
        tier=args.tier or settings.configured_tier(),
        socket_fd=args.socket_fd,
        report_path=args.report,
        venv_dir=args.venv_dir,
    )


if __name__ == "__main__":
    raise SystemExit(main())
