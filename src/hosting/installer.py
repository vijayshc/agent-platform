"""Importing an application and building its environment.

Split from the supervisor because an install is a job with its own lifecycle:
it runs in the background, reports progress, can fail, can be retried, and
cannot outlive the process that started it.
"""

from __future__ import annotations

import shutil
import threading
import time
from pathlib import Path

from src.hosting import applog, archive, environment
from src.hosting.errors import HostingError
from src.hosting.paths import app_root, code_dir, install_log_path, log_path, run_dir, venv_dir
from src.models.hosted_app import HostedApp, HostedAppStatus

#: Slugs with an install in flight, so two uploads cannot race in one directory.
_INSTALLING: set[str] = set()


def begin_install(data: bytes, created_by: int | None = None,
                  start_when_ready: bool = False) -> HostedApp:
    """Validate an upload, extract it, and start building its environment.

    Returns as soon as the files are on disk: the dependency install runs as a
    background job so the upload request does not sit there for minutes with no
    way to see what it is doing. The UI polls ``install_status`` for the steps.
    """
    manifest = archive.read_manifest(data)
    slug = manifest["slug"]
    if slug in _INSTALLING:
        raise HostingError(f"'{slug}' is being installed right now.")

    existing = HostedApp.get_by_slug(slug)
    if existing is not None and existing.status != HostedAppStatus.ERROR:
        raise HostingError(f"'{slug}' is already installed. Remove it first, or bump the slug.")

    # A previous failed attempt is replaced. Its log is what explains the
    # failure, so it is kept before the directory goes.
    _keep_failed_install_log(slug)
    shutil.rmtree(app_root(slug), ignore_errors=True)

    extracted = archive.extract(data, manifest, code_dir(slug))
    run_dir(slug).mkdir(parents=True, exist_ok=True)
    log_path(slug).parent.mkdir(parents=True, exist_ok=True)
    manifest["files"] = extracted["file_count"]
    HostedApp.upsert(manifest, created_by=created_by, autostart=bool(manifest.get("autostart", True)))
    HostedApp.set_runtime(slug, status=HostedAppStatus.INSTALLING, last_error="", install_step="queued")

    _INSTALLING.add(slug)
    threading.Thread(
        target=_install_worker, args=(slug, start_when_ready), name=f"install-{slug}", daemon=True
    ).start()
    return HostedApp.get_by_slug(slug)



def _install_worker(slug: str, start_when_ready: bool = False) -> None:
    """Build the app's environment off the request path, reporting each step."""
    try:
        performed = environment.prepare(
            slug, code_dir(slug),
            progress=lambda message: HostedApp.set_runtime(slug, install_step=message[:120]),
        )
        # The install log is kept: it holds the full output of what was run, and
        # that is what someone reads when the app later misbehaves. The app log
        # simply points at it.
        applog.append(
            log_path(slug),
            f"[hosting] environment ready at {time.strftime('%Y-%m-%d %H:%M:%S')} "
            f"after {len(performed)} steps - full output in logs/install.log",
        )
        HostedApp.set_runtime(slug, status=HostedAppStatus.STOPPED, install_step="ready", last_error="")
        if start_when_ready and HostedApp.get_by_slug(slug).desired_state != "stopped":
            # Honoured here rather than only in the browser, so an API or script
            # asking for "import and start" gets exactly that. An explicit stop
            # arriving while the install was finishing cancels it: the operator's
            # most recent instruction wins.
            from src.hosting import supervisor

            supervisor.start(slug)
    except Exception as exc:
        # The directory is kept on failure: the log in it is how an
        # administrator finds out what went wrong. Re-importing the same slug
        # replaces it, so nothing is blocked by leaving it in place.
        applog.append(install_log_path(slug), f"FAILED: {exc}")
        HostedApp.set_runtime(
            slug, status=HostedAppStatus.ERROR, install_step="failed",
            last_error=f"Environment setup failed: {exc}"[:500],
        )
    finally:
        _INSTALLING.discard(slug)



def reinstall(slug: str) -> tuple[bool, str]:
    """Re-run the environment setup from the files already extracted.

    Needed whenever an install did not finish: interrupted by a restart, failed
    on a network blip, or left half-built. No upload is required - the archive
    is already on disk - so this is the "try again" an administrator reaches for.
    """
    record = HostedApp.get_by_slug(slug)
    if record is None:
        return False, f"'{slug}' is not installed"
    if slug in _INSTALLING:
        return False, f"'{slug}' is being installed right now."
    if not code_dir(slug).is_dir():
        return False, (
            f"The files for '{slug}' are missing, so there is nothing to install from. "
            "Import the application again."
        )

    # A retry starts from a clean environment: a half-created virtualenv or a
    # partly-written node_modules is exactly what a retry is meant to replace.
    shutil.rmtree(venv_dir(slug), ignore_errors=True)
    shutil.rmtree(code_dir(slug) / "node_modules", ignore_errors=True)
    applog.append(install_log_path(slug), f"\n--- retrying the install at {time.strftime('%Y-%m-%d %H:%M:%S')} ---")

    HostedApp.set_runtime(slug, status=HostedAppStatus.INSTALLING, last_error="", install_step="queued")
    _INSTALLING.add(slug)
    threading.Thread(target=_install_worker, args=(slug,), name=f"install-{slug}", daemon=True).start()
    return True, "installing again"



def recover_interrupted_installs() -> list[str]:
    """An install cannot outlive the process that ran it.

    A platform restart in the middle of one leaves a row that claims to be
    installing forever, with no way to start or retry it. At boot, any such row
    becomes an error that can be retried.
    """
    recovered: list[str] = []
    for record in HostedApp.get_all():
        if record.status == HostedAppStatus.INSTALLING:
            HostedApp.set_runtime(
                slug=record.slug, status=HostedAppStatus.ERROR, install_step="interrupted",
                last_error="The install was interrupted (the platform restarted). Retry the install.",
            )
            recovered.append(record.slug)
    return recovered



def install_status(slug: str, lines: int = 400) -> dict:
    """How an install is going, including the log so far."""
    record = HostedApp.get_by_slug(slug)
    if record is None:
        return {"status": "missing", "step": "", "log": ""}
    return {
        "status": record.status,
        "step": record.install_step,
        "error": record.last_error,
        "log": applog.tail(install_log_path(slug), lines),
    }



def _keep_failed_install_log(slug: str) -> None:
    """Preserve an install log before the failed attempt is cleaned up.

    The app directory is removed so a rejected import never blocks a later one,
    but the reason it failed is exactly what an administrator needs to read.
    """
    source = install_log_path(slug)
    if not source.exists():
        return
    try:
        kept = settings.root() / ".failed-installs"
        kept.mkdir(parents=True, exist_ok=True)
        target = kept / f"{slug}-{time.strftime('%Y%m%d-%H%M%S')}.log"
        shutil.copyfile(source, target)
    except OSError:
        pass

