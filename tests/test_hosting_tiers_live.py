"""Live test: every isolation tier this host supports, through the real launcher.

No mocks. For each tier a real Flask app is launched under
``src.hosting.launcher``, inheriting a listening socket the test owns, and is
then interrogated over that socket exactly as the parent application will.

Tier A and B+ must not be able to read the platform's files. Tier B is the
documented floor: it has no filesystem isolation, and this test asserts that
fact rather than pretending otherwise, so a regression in the *other* tiers is
never masked by it.

Run:  ~/anaconda3/bin/python3 -m pytest tests/test_hosting_tiers_live.py -s
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
WORK = REPO / "temp" / "hosting_tiers_test"

APP_SOURCE = '''\
import os, resource, socket
from flask import Flask

app = Flask(__name__)
PLATFORM = {platform!r}
APP_DIR = {app!r}

def attempt(fn):
    try:
        return fn()
    except Exception as exc:
        return f"DENIED {{type(exc).__name__}}"

@app.get("/probe")
def probe():
    return {{
        "served": "YES",
        "read platform .env": attempt(lambda: "ALLOWED" if open(os.path.join(PLATFORM, ".env")).read() else "EMPTY"),
        "read platform db": attempt(lambda: "ALLOWED" if open(os.path.join(PLATFORM, "text2sql.db"), "rb").read(16) else "EMPTY"),
        "see platform source tree": attempt(lambda: "EXISTS" if os.path.isdir(os.path.join(PLATFORM, "src")) else "ABSENT"),
        "write own app dir": attempt(lambda: (open(os.path.join(APP_DIR, "written.txt"), "w").write("ok"), "ok")[1]),
        "outbound tcp": attempt(lambda: (socket.create_connection(("1.1.1.1", 443), timeout=3).close(), "ALLOWED")[1]),
        "create socket": attempt(lambda: (socket.socket(socket.AF_INET, socket.SOCK_STREAM).close(), "ALLOWED")[1]),
        "rlimit_as": resource.getrlimit(resource.RLIMIT_AS)[0],
    }}
'''


def _build_app(name: str) -> Path:
    app_dir = WORK / name
    if app_dir.exists():
        shutil.rmtree(app_dir)
    (app_dir / "run").mkdir(parents=True)
    (app_dir / "app.py").write_text(APP_SOURCE.format(platform=str(REPO), app=str(app_dir)))
    return app_dir


def _launch(app_dir: Path, tier: str, fd: int):
    report = app_dir / "runtime.json"
    env = dict(os.environ)
    env.setdefault("HOSTED_APPS_TIER", tier)
    return subprocess.Popen(
        [
            sys.executable, "-m", "src.hosting.launcher",
            "--app-dir", str(app_dir),
            "--tier", tier,
            "--socket-fd", str(fd),
            "--report", str(report),
            "--",
            sys.executable, "-m", "gunicorn",
            "--bind", f"fd://{fd}",
            "--no-control-socket",          # gunicorn 25 otherwise creates its own
            "--workers", "1", "--log-level", "error", "app:app",
        ],
        cwd=str(REPO), env=env, pass_fds=(fd,), start_new_session=True,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )


def _ask(socket_path: str, proc) -> dict | None:
    import httpx

    client = httpx.Client(transport=httpx.HTTPTransport(uds=socket_path), base_url="http://app", timeout=6)
    for _ in range(20):
        if proc.poll() is not None:
            return None
        try:
            return client.get("/probe").json()
        except Exception:
            time.sleep(0.4)
    return None


def _stop(proc) -> None:
    """Always escalate to SIGKILL: the app may be denied the right to signal
    itself, so a graceful stop cannot be assumed."""
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except OSError:
        pass
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()


def run_tier(tier: str) -> tuple[dict | None, str]:
    app_dir = _build_app(f"app_{tier.replace('+', 'p')}")
    socket_path = str(app_dir / "run" / "app.sock")
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(socket_path)
    listener.listen(32)
    fd = listener.fileno()

    proc = _launch(app_dir, tier, fd)
    try:
        data = _ask(socket_path, proc)
        output = ""
        if data is None and proc.stdout is not None:
            _stop(proc)
            output = proc.stdout.read() or ""
        report = {}
        report_file = app_dir / "runtime.json"
        if report_file.exists():
            report = json.loads(report_file.read_text())
        return data, output or json.dumps(report)
    finally:
        _stop(proc)
        listener.close()


@pytest.fixture(scope="module")
def caps():
    from src.hosting import capabilities

    return capabilities.detect()


def test_tier_a_isolates_filesystem(caps):
    if not caps["userns"]:
        pytest.skip("user namespaces unavailable on this host")
    data, err = run_tier("A")
    assert data is not None, f"tier A app did not start: {err}"
    assert data["served"] == "YES"
    assert data["read platform .env"].startswith("DENIED")
    assert data["read platform db"].startswith("DENIED")
    assert data["see platform source tree"] == "ABSENT"
    assert data["write own app dir"] == "ok"
    assert data["outbound tcp"].startswith("DENIED")
    # Socket creation must be denied in tier A as well: the app inherits its
    # listener, so nothing may create one. This also catches a silent failure to
    # apply seccomp inside the namespace (e.g. an import attempted after the
    # platform tree has been hidden).
    assert data["create socket"].startswith("DENIED"), data["create socket"]
    assert data["rlimit_as"] > 0


def test_tier_bplus_isolates_filesystem(caps):
    if not caps["landlock_abi"]:
        pytest.skip("Landlock unavailable on this host")
    data, err = run_tier("B+")
    assert data is not None, f"tier B+ app did not start: {err}"
    assert data["served"] == "YES"
    assert data["read platform .env"].startswith("DENIED")
    assert data["read platform db"].startswith("DENIED")
    assert data["write own app dir"] == "ok"
    assert data["outbound tcp"].startswith("DENIED")
    assert data["create socket"].startswith("DENIED"), data["create socket"]


def test_tier_b_serves_without_network_but_reads_platform_files():
    """Tier B is the floor. Its limits are asserted so they stay visible."""
    data, err = run_tier("B")
    assert data is not None, f"tier B app did not start: {err}"
    assert data["served"] == "YES"
    assert data["outbound tcp"].startswith("DENIED")
    assert data["create socket"].startswith("DENIED")
    # Documented limitation: no filesystem isolation without namespaces/Landlock.
    assert data["read platform .env"] == "ALLOWED"


def test_pinned_tier_fails_closed_when_unavailable(monkeypatch):
    """A node promised isolation must refuse to run rather than silently degrade."""
    from src.hosting import capabilities

    fake = {"userns": False, "seccomp": True, "landlock_abi": 0, "landlock_status": "unsupported (kernel has no Landlock)"}
    tier, reason = capabilities.resolve_tier(fake, "A")
    assert tier is None and "user namespaces" in reason
    tier, reason = capabilities.resolve_tier(fake, "B+")
    assert tier is None and "Landlock" in reason
    tier, _ = capabilities.resolve_tier(fake, "auto")
    assert tier == "B"
    tier, _ = capabilities.resolve_tier(fake, "B")
    assert tier == "B"
