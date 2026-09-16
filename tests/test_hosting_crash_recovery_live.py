"""Live test: a hosted app survives a platform crash, and the next boot reaps it.

Started apps are deliberately placed in their own session so a platform restart
does not take them down. The cost is that an unclean exit can leave one running:
unreachable, because its socket is recreated on the next start, but still
consuming resources. This proves the next boot finds it - and that the reaper
refuses to touch a process that is not ours.

Run:  ~/anaconda3/bin/python3 tests/test_hosting_crash_recovery_live.py
"""

import io
import json
import os
import re
import shutil
import signal
import sqlite3
import subprocess
import sys
import time
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

BASE = "http://127.0.0.1:5056"
# "tiera" exercises the default tier, where the recorded pid is the `unshare`
# supervisor rather than gunicorn - a different identification problem.
POLICY = os.environ.get("ORPHAN_TEST_POLICY", "bplus")
ALLOW_USERNS = "true" if POLICY == "tiera" else "false"
LOG = REPO / "logs" / "orphan-test.log"
SLUG = "hosted-app-demo-bplus"
SAMPLE = REPO / "samples" / "hosted-app-demo"
def _database_path() -> Path:
    """The database the platform under test uses, not a hardcoded one."""
    uri = os.environ.get("DATABASE_URI", "sqlite:///text2sql.db")
    name = uri.split("sqlite:///")[-1]
    return Path(name) if os.path.isabs(name) else REPO / name


DB = _database_path()


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


def build_archive() -> bytes:
    """The sample app under its own slug, built here so the test is self-contained."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(SAMPLE.rglob("*")):
            if not path.is_file() or "__pycache__" in path.parts:
                continue
            relative = path.relative_to(SAMPLE).as_posix()
            if relative == "app.json":
                manifest = json.loads(path.read_text())
                manifest["slug"] = SLUG
                manifest["name"] = "Hosted App Demo (crash test)"
                archive.writestr(relative, json.dumps(manifest, indent=2))
            else:
                archive.write(path, relative)
    return buffer.getvalue()


#: The apps origin this suite uses (app content is never served from the
#: platform's own origin; see src/hosting/origin.py).
APPS_PORT = os.environ.get("ORPHAN_TEST_APPS_PORT", "5058")


def start_platform(log_handle):
    return subprocess.Popen(
        [sys.executable, "-c",
         "import app; from src.hosting import origin; origin.serve(app.app); "
         "app.app.run(host='127.0.0.1', port=5056, threaded=True, use_reloader=False)"],
        cwd=str(REPO), stdout=log_handle, stderr=subprocess.STDOUT,
        env=dict(os.environ, HOSTED_APPS_ALLOW_USERNS=ALLOW_USERNS,
                 HOSTED_APPS_ORIGIN_PORT=APPS_PORT), start_new_session=True,
    )


def wait_up(timeout=90):
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


def session_with_token():
    import requests

    session = requests.Session()
    session.get(f"{BASE}/login", timeout=15)
    session.post(f"{BASE}/login", data={"username": "admin", "password": os.environ.get("LIVE_AGENT_PASSWORD", "admin")}, timeout=15)
    page = session.get(f"{BASE}/admin/hosted-apps", timeout=15).text
    match = re.search(r'name="csrf-token" content="([^"]+)"', page)
    session.headers["X-CSRF-Token"] = match.group(1) if match else ""
    return session


def clean(session):
    """Remove any previous copy so the run starts from a known state."""
    response = session.delete(f"{BASE}/admin/api/hosted-apps/{SLUG}", timeout=60)
    print(f"   pre-clean delete : {response.status_code}")
    leftover = REPO / "uploads" / "hosted-apps" / SLUG
    if leftover.exists():
        shutil.rmtree(leftover, ignore_errors=True)


def deploy(session):
    """Import, wait for the background install, and let the platform start it."""
    response = session.post(
        f"{BASE}/admin/api/hosted-apps/import",
        files={"file": (f"{SLUG}.zip", build_archive(), "application/zip")},
        data={"start": "1"}, timeout=60,
    )
    print(f"   import           : {response.status_code}")
    for _ in range(120):
        state = session.get(f"{BASE}/admin/api/hosted-apps/{SLUG}/install?lines=20", timeout=20).json()
        if state.get("status") != "installing":
            print(f"   install          : {state.get('status')} ({state.get('step')})")
            break
        time.sleep(2)
    # start=1 is honoured by the installer; nudge it in case it is still stopped.
    session.post(f"{BASE}/admin/api/hosted-apps/{SLUG}/start", timeout=60)
    return response


def recorded(slug):
    conn = sqlite3.connect(str(DB))
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute("SELECT status, pid, owner, tier FROM hosted_apps WHERE slug = ?", (slug,)).fetchone()
        return dict(row) if row else {}
    finally:
        conn.close()


def alive(pid):
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


_refuse_shared_state()

print(f"--- policy: {POLICY} (allow_user_namespaces={ALLOW_USERNS}) ---")
checks = []
with LOG.open("wb") as log:
    platform = start_platform(log)
    assert wait_up(), "platform did not come up"
    session = session_with_token()
    clean(session)
    response = deploy(session)
    assert response.status_code == 202, f"deploy failed: {response.text[:300]}"

    state = recorded(SLUG)
    app_pid, platform_pid = state["pid"], platform.pid
    served = session.get(f"{BASE}/apps/{SLUG}/api/selfcheck", timeout=20)
    print(f"1. running         : pid={app_pid} owner={state['owner']!r} tier={state['tier']} http={served.status_code}")
    checks.append(("app started and serves", state["status"] == "running" and served.status_code == 200))
    checks.append(("owner recorded", state["owner"].split(":")[1] == str(platform_pid)))
    expected_tier = "A" if POLICY == "tiera" else "B+"
    checks.append((f"tier is {expected_tier} under the policy knob", state["tier"] == expected_tier))

    # Crash the platform with SIGKILL: no graceful shutdown, so no supervisor cleanup.
    os.killpg(os.getpgid(platform_pid), signal.SIGKILL)
    platform.wait(timeout=15)
    time.sleep(1.5)
    survived = alive(app_pid)
    print(f"2. platform SIGKILL: hosted app pid {app_pid} still alive = {survived}")
    checks.append(("app survives an unclean platform exit", survived))

    # The next boot must notice the orphan and stop it before anything restarts.
    platform2 = start_platform(log)
    assert wait_up(), "platform did not come up the second time"
    time.sleep(2.5)
    reaped = not alive(app_pid)
    log_text = LOG.read_text(errors="replace")
    reap_lines = [line for line in log_text.splitlines() if "reaped orphaned process" in line]
    print(f"3. after restart   : orphan alive = {alive(app_pid)} | reap logged = {bool(reap_lines)}")
    if reap_lines:
        print(f"   {reap_lines[-1].split('text2sql:')[-1].strip()}")
    checks.append(("orphan reaped on next boot", reaped))
    checks.append(("reap recorded in the log", bool(reap_lines)))

    # Safety: a pid we do not own must never be treated as ours.
    victim = subprocess.Popen(["sleep", "120"])
    time.sleep(0.3)
    from src.hosting import supervisor

    owned = supervisor._looks_like_our_app(victim.pid, SLUG)
    print(f"4. safety          : unrelated pid {victim.pid} considered ours = {owned}")
    checks.append(("unrelated process is not claimed", owned is False))
    victim.kill()
    victim.wait()

    os.killpg(os.getpgid(platform2.pid), signal.SIGKILL)
    platform2.wait(timeout=15)

print()
for label, ok in checks:
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
print(f"\n{sum(1 for _, ok in checks if ok)}/{len(checks)} expectations met")
