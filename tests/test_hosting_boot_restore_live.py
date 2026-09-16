"""Live test: hosted apps come back when the platform restarts.

The distinction that matters is intent. An app the operator left running should
return after a restart - that is what makes a platform restart invisible - while
an app they explicitly stopped must stay stopped. Both directions are checked
here, against a real platform instance started and restarted by this test.

Run:  ~/anaconda3/bin/python3 tests/test_hosting_boot_restore_live.py
"""

from __future__ import annotations

import io
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

BASE = os.environ.get("BOOT_RESTORE_BASE_URL", "http://127.0.0.1:5057")
LOG = REPO / "logs" / "boot-restore-test.log"
SAMPLE = REPO / "samples" / "hosted-app-demo"
KEPT = "restore-kept"
STOPPED = "restore-stopped"


def _refuse_shared_state() -> None:
    """Never run against the deployment's own database or app workspaces.

    A test instance that shares them adopts the running apps, rebinds their
    sockets and leaves the real deployment answering 502 - which happened once.
    Isolation is a precondition, not a convenience.
    """
    import os
    from pathlib import Path

    repo = Path(__file__).resolve().parent.parent
    shared_db = os.environ.get("DATABASE_URI", "sqlite:///text2sql.db") in (
        "", "sqlite:///text2sql.db", f"sqlite:///{repo / 'text2sql.db'}",
    )
    shared_root = not os.environ.get("HOSTED_APPS_ROOT") or Path(
        os.environ["HOSTED_APPS_ROOT"]
    ).resolve() == (repo / "uploads" / "hosted-apps").resolve()
    if (shared_db or shared_root) and os.environ.get("ALLOW_SHARED_TEST_STATE") != "1":
        raise SystemExit(
            "Refusing to run against the deployment's own state.\n"
            f"  DATABASE_URI      = {os.environ.get('DATABASE_URI', '(default text2sql.db)')}\n"
            f"  HOSTED_APPS_ROOT  = {os.environ.get('HOSTED_APPS_ROOT', '(default uploads/hosted-apps)')}\n"
            "Point both at a scratch location (see docs/hosted-apps-isolation.md 10.3), or set "
            "ALLOW_SHARED_TEST_STATE=1 if you really mean to."
        )


def archive(slug: str) -> bytes:
    buffer = io.BytesIO()
    manifest = json.loads((SAMPLE / "app.json").read_text())
    manifest["slug"] = slug
    manifest["name"] = slug
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as z:
        for path in sorted(SAMPLE.rglob("*")):
            if not path.is_file() or "__pycache__" in path.parts:
                continue
            name = path.relative_to(SAMPLE).as_posix()
            if name == "app.json":
                z.writestr(name, json.dumps(manifest, indent=2))
            else:
                z.write(path, name)
    return buffer.getvalue()


APPS_PORT = os.environ.get("BOOT_RESTORE_APPS_PORT", "5059")

_refuse_shared_state()


def start_platform(log_handle):
    return subprocess.Popen(
        [sys.executable, "-c",
         "import app; from src.hosting import origin; origin.serve(app.app); "
         f"app.app.run(host='127.0.0.1', port={BASE.rsplit(':', 1)[1]}, threaded=True, use_reloader=False)"],
        cwd=str(REPO), stdout=log_handle, stderr=subprocess.STDOUT,
        env=dict(os.environ, HOSTED_APPS_ORIGIN_PORT=APPS_PORT), start_new_session=True,
    )


def wait_up(timeout=90) -> bool:
    import requests

    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if requests.get(f"{BASE}/login", timeout=3).status_code == 200:
                return True
        except requests.RequestException:
            pass
        time.sleep(1)
    return False


def session():
    import requests

    s = requests.Session()
    s.get(f"{BASE}/login", timeout=15)
    s.post(f"{BASE}/login", data={"username": "admin", "password": os.environ.get("LIVE_AGENT_PASSWORD", "admin")}, timeout=15)
    page = s.get(f"{BASE}/admin/hosted-apps", timeout=15).text
    match = re.search(r'name="csrf-token" content="([^"]+)"', page)
    s.headers["X-CSRF-Token"] = match.group(1) if match else ""
    return s


def deploy(s, slug: str) -> None:
    s.delete(f"{BASE}/admin/api/hosted-apps/{slug}", timeout=30)
    shutil.rmtree(REPO / "uploads" / "hosted-apps" / slug, ignore_errors=True)
    response = s.post(
        f"{BASE}/admin/api/hosted-apps/import",
        files={"file": (f"{slug}.zip", archive(slug), "application/zip")},
        data={"start": "1"}, timeout=60,
    )
    assert response.status_code == 202, response.text[:200]
    deadline = time.time() + 240
    while time.time() < deadline:
        state = s.get(f"{BASE}/admin/api/hosted-apps/{slug}/install?lines=20", timeout=20).json()
        if state.get("status") != "installing":
            assert state["status"] != "error", f"{slug}: {state.get('error')}"
            break
        time.sleep(2)
    else:
        raise AssertionError(f"{slug} did not finish installing")
    # start=1 is honoured by the installer in the background; wait for it so the
    # test's next instruction is not racing it.
    assert wait_for_status(s, slug, "running") == "running", f"{slug} never started"


def statuses(s) -> dict:
    return {a["slug"]: a for a in s.get(f"{BASE}/admin/api/hosted-apps", timeout=20).json()["apps"]}


def wait_for_status(s, slug: str, want: str, timeout: int = 90) -> str:
    """The starts happen in the background, so poll rather than assume."""
    deadline = time.time() + timeout
    last = ""
    while time.time() < deadline:
        last = statuses(s).get(slug, {}).get("status", "missing")
        if last == want:
            return last
        time.sleep(2)
    return last


checks: list[tuple[str, bool]] = []
with LOG.open("wb") as log:
    platform = start_platform(log)
    assert wait_up(), "the platform did not come up"
    s = session()

    # One app left running, one explicitly stopped.
    deploy(s, KEPT)
    deploy(s, STOPPED)
    print(f"  setup: {KEPT}=running, {STOPPED}=stopped")
    s.post(f"{BASE}/admin/api/hosted-apps/{STOPPED}/stop", timeout=60)
    time.sleep(1)
    before = statuses(s)
    checks.append(("both apps reached their intended state",
                   before[KEPT]["status"] == "running" and before[STOPPED]["status"] == "stopped"))

    # Restart the platform the way an operator would.
    os.killpg(os.getpgid(platform.pid), signal.SIGTERM)
    platform.wait(timeout=30)
    time.sleep(1)

    platform2 = start_platform(log)
    assert wait_up(), "the platform did not come up the second time"
    s2 = session()

    kept = wait_for_status(s2, KEPT, "running")
    stopped = wait_for_status(s2, STOPPED, "stopped", timeout=20)
    print(f"  after restart: {KEPT}={kept}, {STOPPED}={stopped}")

    checks.append(("an app that was running came back by itself", kept == "running"))
    checks.append(("an app that was stopped stayed stopped", stopped == "stopped"))
    checks.append(("the restored app serves",
                   s2.get(f"{BASE}/apps/{KEPT}/", timeout=20).status_code == 200))
    checks.append(("the stopped app is not served",
                   s2.get(f"{BASE}/apps/{STOPPED}/", timeout=20).status_code == 503))

    boot = LOG.read_text(errors="replace")
    # The line lists every app it is bringing back, so match within it rather
    # than looking for one slug in isolation.
    starting_lines = [line for line in boot.splitlines() if "Hosted apps: starting" in line]
    checks.append(("the boot log says what it is bringing back",
                   any(KEPT in line for line in starting_lines)))
    checks.append(("the stopped app is not started at boot",
                   not any(STOPPED in line for line in starting_lines)))

    for slug in (KEPT, STOPPED):
        s2.delete(f"{BASE}/admin/api/hosted-apps/{slug}", timeout=30)
        shutil.rmtree(REPO / "uploads" / "hosted-apps" / slug, ignore_errors=True)
    os.killpg(os.getpgid(platform2.pid), signal.SIGKILL)
    platform2.wait(timeout=15)

print()
for label, ok in checks:
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
print(f"\n{sum(1 for _, ok in checks if ok)}/{len(checks)} expectations met")
sys.exit(0 if all(ok for _, ok in checks) else 1)
