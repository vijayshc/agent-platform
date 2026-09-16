"""Build an app's environment: its virtualenv, its dependencies, its build.

This is the only place third-party code executes, so every command runs through
the launcher's *install profile*: the filesystem is confined to the app's own
directory and the system libraries, while the network stays open so the package
index is reachable. Where the kernel cannot confine it, the install is refused
unless an administrator has explicitly accepted that risk.
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from src.hosting import archive, settings
from src.hosting.errors import HostingError
from src.hosting.paths import app_root, install_log_path, venv_dir
from src.models.hosted_app import HostedApp


def prepare(slug: str, source_dir: Path, progress=None) -> list[str]:
    """Build the app's virtualenv and install everything it declares.

    Runs as a background job, reporting each step as it starts so the admin UI
    can show what is happening - an install can take minutes, and a spinner with
    no explanation is indistinguishable from a hang.
    """
    performed: list[str] = []

    def step(message: str) -> None:
        performed.append(message)
        _append_install_log(slug, f"[{time.strftime('%H:%M:%S')}] {message}")
        if progress:
            try:
                progress(message)
            except Exception:
                pass

    if not settings.build_venv():
        step("virtualenv disabled by configuration: skipping dependency install")
        return performed
    step("creating the virtualenv")
    _make_venv(slug)
    step("installing Python requirements")
    _pip_install(slug, source_dir)
    step("installing Node packages")
    _npm_install(slug, source_dir)
    step("running the build command")
    _node_build(slug, source_dir)
    step("environment ready")
    return performed


def _make_venv(slug: str) -> None:
    """One virtualenv per app, on top of the platform's Python.

    ``--system-site-packages`` keeps an app runnable on a node with no package
    index; the app's own requirements are installed on top of it. It runs under
    the same install profile as pip and npm: creating a virtualenv executes
    ``ensurepip``, and no third-party code should run outside the profile.
    """
    target = venv_dir(slug)
    _run_installer(
        slug,
        [settings.python_executable(), "-m", "venv", "--system-site-packages", str(target)],
        "virtualenv creation",
        app_root(slug),
    )


def _node_tool_dirs() -> list[str]:
    """Directories holding node/npm/npx, when they are installed somewhere else."""
    directories: list[str] = []
    for name in ("node", "npm", "npx"):
        found = shutil.which(name)
        if not found:
            continue
        directory = str(Path(found).resolve().parent)
        if directory not in directories:
            directories.append(directory)
    return directories


def _install_environment(slug: str) -> dict:
    """Environment for an installer: all of its writes stay inside the app.

    Deliberately **not** ``os.environ``. pip, npm and a manifest's build command
    are third-party code running with network access, and the platform's own
    process holds credentials (provider API keys, tokens, ``SECRET_KEY``).
    Inheriting its environment handed every one of them to any uploaded archive;
    the run profile never did, and now neither does this one.
    """
    root = app_root(slug)
    # logs/ too: the installer's output is written there, and it is the only
    # record of why a failed install failed.
    for name in ("logs", ".tmp", ".pip-cache", ".npm-cache"):
        (root / name).mkdir(parents=True, exist_ok=True)
    path_parts = [str(venv_dir(slug) / "bin"), *_node_tool_dirs(), "/usr/local/bin", "/usr/bin", "/bin"]
    return {
        "PATH": ":".join(dict.fromkeys(path_parts)),
        "HOME": str(root),
        "TMPDIR": str(root / ".tmp"),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PYTHONUNBUFFERED": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        # Required so the launcher module itself can be imported *before* it
        # applies the confinement; it is popped again before the installer runs.
        "PYTHONPATH": str(settings.platform_root()),
        "PIP_CACHE_DIR": str(root / ".pip-cache"),
        "PIP_DISABLE_PIP_VERSION_CHECK": "1",
        "PIP_NO_INPUT": "1",
        "npm_config_cache": str(root / ".npm-cache"),
        "npm_config_audit": "false",
        "npm_config_fund": "false",
        "npm_config_update_notifier": "false",
    }


def _stream(process: subprocess.Popen, slug: str, timeout: int) -> tuple[int, str, bool]:
    """Stream the installer's output into the log, killing it if it overruns.

    The output is streamed rather than captured and written at the end: an npm
    install can take a minute, and a log that stays empty until it finishes is
    no use for watching - or for finding out where it hung. The timeout is what
    stops a build that never finishes from pinning a thread and a process for
    the life of the platform.
    """
    tail: list[str] = []
    overran = threading.Event()

    def kill_group() -> None:
        overran.set()
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        except OSError:
            pass
        # setsid/setpgid are denied to the installer, but closing the pipe is what
        # guarantees this thread wakes even if some process still holds it open.
        try:
            process.stdout.close()
        except Exception:
            pass

    timer = threading.Timer(timeout, kill_group)
    timer.daemon = True
    timer.start()
    assert process.stdout is not None
    try:
        for line in process.stdout:
            _append_install_log(slug, line.rstrip("\n"))
            tail.append(line)
            if len(tail) > 60:
                tail.pop(0)
    except ValueError:
        pass  # the watchdog closed the pipe to unblock this read
    finally:
        timer.cancel()
    try:
        code = process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        code = process.wait(timeout=5)
    return code, "".join(tail), overran.is_set()


def _run_installer(slug: str, argv: list[str], label: str, cwd: Path) -> None:
    """Run an installer, confined, streaming everything it prints into the log."""
    app_dir = str(app_root(slug))
    _append_install_log(slug, f"$ {' '.join(argv)}")
    # The marker lives outside everything the install profile can write, so the
    # installer cannot create it: exit 69 alone must not be trusted as "the
    # launcher refused", or a build command could ask for an unconfined re-run.
    marker_dir = settings.platform_root() / "temp"
    try:
        marker_dir.mkdir(parents=True, exist_ok=True)
        marker = Path(tempfile.mkdtemp(prefix=".install-refusal-", dir=str(marker_dir)))
    except OSError:
        # Last resort: still better than no install, and only reached when the
        # platform tree is not writable at all.
        marker = Path(tempfile.mkdtemp(prefix=".install-refusal-"))
    refusal_marker = marker / "refused"
    confined = [
        settings.python_executable(), "-m", "src.hosting.launcher",
        "--app-dir", app_dir, "--install-profile",
        "--refuse-marker", str(refusal_marker), "--", *argv,
    ]
    timeout = max(5, settings.install_timeout())

    def spawn(command: list[str]) -> subprocess.Popen:
        return subprocess.Popen(
            command, cwd=str(cwd), env=_install_environment(slug),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
            stdin=subprocess.DEVNULL,
            start_new_session=True,  # so a timeout can stop the whole tree
        )

    try:
        process = spawn(confined)
    except OSError as exc:
        raise HostingError(f"{label} could not start: {exc}") from exc

    try:
        code, tail, overran = _stream(process, slug, timeout)
    except Exception:
        process.kill()
        raise

    if overran:
        raise HostingError(
            f"{label} was stopped after {timeout}s "
            "(HOSTED_APPS_INSTALL_TIMEOUT); see the install log for how far it got"
        )

    refused = refusal_marker.exists()
    shutil.rmtree(marker, ignore_errors=True)

    if code == 69 and refused:
        # The kernel cannot confine an install on this host - confirmed by the
        # launcher's own marker, not by the exit status the installer controls.
        if not settings.allow_unconfined_install():
            raise HostingError(
                f"{label} was not run: this kernel cannot confine a dependency install, so "
                "third-party build code would run with the platform's privileges. Set "
                "HOSTED_APPS_ALLOW_UNCONFINED_INSTALL=true to accept that, or build the "
                "virtualenv on another host and ship it."
            )
        _append_install_log(slug, f"$ {' '.join(argv)}  (unconfined: Landlock unavailable)")
        try:
            fallback = spawn(argv)
        except OSError as exc:
            raise HostingError(f"{label} could not start: {exc}") from exc
        code, tail, overran = _stream(fallback, slug, timeout)
        if overran:
            raise HostingError(f"{label} was stopped after {timeout}s (HOSTED_APPS_INSTALL_TIMEOUT)")

    if code != 0:
        raise HostingError(f"{label} failed: {tail.strip()[-600:]}")
def _append_install_log(slug: str, text: str) -> None:
    if not text:
        return
    try:
        with install_log_path(slug).open("a", encoding="utf-8") as handle:
            handle.write(text if text.endswith("\n") else text + "\n")
    except OSError:
        pass


def _pip_install(slug: str, source_dir: Path) -> None:
    requirements = source_dir / requirements_name()
    if not requirements.exists():
        return
    if not requirements.read_text(errors="replace").strip():
        _append_install_log(slug, "requirements.txt is empty: nothing to install.\n")
        return
    _run_installer(
        slug,
        [str(venv_dir(slug) / "bin" / "pip"), "install", "--disable-pip-version-check",
         "-r", str(requirements)],
        "pip install",
        source_dir,
    )


def _npm_install(slug: str, source_dir: Path) -> None:
    package_json = source_dir / "package.json"
    if not package_json.exists() or not settings.npm_install():
        return
    npm = shutil.which("npm")
    if not npm:
        raise HostingError(
            "This archive has a package.json but npm is not installed on this host, so its "
            "Node dependencies cannot be installed."
        )
    _run_installer(
        slug, [npm, "install", "--no-audit", "--no-fund", "--loglevel=error"], "npm install", source_dir
    )


def _node_build(slug: str, source_dir: Path) -> None:
    """Run the manifest's build command, if it declares one.

    A React app is compiled here, once, so the running app only serves static
    files: no toolchain and no Node needed at run time.
    """
    command = str(_manifest_for(slug).get("build") or "").strip()
    if not command or not settings.node_build():
        return
    _run_installer(slug, ["/bin/sh", "-c", command], "build", source_dir)


def _manifest_for(slug: str) -> dict:
    record = HostedApp.get_by_slug(slug)
    return (record.manifest if record else {}) or {}


def requirements_name() -> str:
    return archive.REQUIREMENTS_NAME

