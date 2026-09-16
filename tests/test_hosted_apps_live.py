"""Live end-to-end test: import, run, serve and govern a hosted application.

Runs against a REAL app instance (start.sh; override with LIVE_AGENT_BASE_URL)
and a REAL hosted Flask app process confined by ``src.hosting.launcher``. Every
assertion is on observed behaviour - HTML actually served, the identity the
platform injected, the isolation the app reports about itself, the RBAC decision
and the audit trail.

    LIVE_AGENT_BASE_URL=http://127.0.0.1:5055 \
    PHOENIX_WORKING_DIR=$PWD/.phoenix \
    ~/anaconda3/bin/python3 -m pytest tests/test_hosted_apps_live.py -q
"""

from __future__ import annotations

import io
import json
import os
import pathlib
import time
import uuid
import zipfile
from urllib.parse import urlsplit

import pytest
import requests

BASE_URL = os.environ.get("LIVE_AGENT_BASE_URL", "http://127.0.0.1:5000").rstrip("/")
USERNAME = os.environ.get("LIVE_AGENT_USER", "admin")
PASSWORD = os.environ.get("LIVE_AGENT_PASSWORD", "admin")
SLUG = "hosted-app-demo"
SAMPLES = pathlib.Path(__file__).resolve().parent.parent / "samples"
SAMPLE_DIR = SAMPLES / "hosted-app-demo"
REACT_SAMPLE_DIR = SAMPLES / "react-hosted-demo"
AUDIT_DIR = pathlib.Path(__file__).resolve().parent.parent / "logs" / "audit"


def _archive(*, requirements: bool = True, slug: str | None = None,
             extra: dict[str, str] | None = None,
             sample: pathlib.Path | None = None) -> bytes:
    """A sample app as an upload; ``requirements`` off proves it is required."""
    source = sample or SAMPLE_DIR
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(source.rglob("*")):
            if not path.is_file() or "__pycache__" in path.parts:
                continue
            name = path.relative_to(source).as_posix()
            if not requirements and name == "requirements.txt":
                continue
            if name.startswith("node_modules/") or "/node_modules/" in name:
                continue
            if slug and name == "app.json":
                manifest = json.loads(path.read_text())
                manifest["slug"] = slug
                manifest["name"] = f"{manifest['name']} ({slug})"
                archive.writestr(name, json.dumps(manifest, indent=2))
                continue
            if name == "app.json" and sample is not None and slug is None:
                continue
            archive.write(path, name)
        if sample is not None and slug:
            manifest = json.loads((source / "app.json").read_text())
            manifest["slug"] = slug
            manifest["name"] = f"{manifest['name']} ({slug})"
            archive.writestr("app.json", json.dumps(manifest, indent=2))
        for name, data in (extra or {}).items():
            archive.writestr(name, data)
    return buffer.getvalue()


#: Where app content is served. Apps live at /apps/<slug>/ on their own origin
#: (see src/hosting/origin.py); the platform's own path 308-redirects there. The
#: tests discover it from that redirect unless it is given explicitly.
APPS: dict[str, str] = {"base": os.environ.get("LIVE_AGENT_APPS_URL", "").rstrip("/")}


def apps(path: str) -> str:
    """An absolute URL on the apps origin."""
    return f"{APPS['base']}{path}"


@pytest.fixture(scope="module", autouse=True)
def _resolve_apps_origin(admin):
    if not APPS["base"]:
        # Discovery starts from the platform, whose /apps/<slug>/ 308-redirects to
        # the apps origin; ``apps()`` cannot be used before the base is known.
        response = admin.get(f"{BASE_URL}/apps/{SLUG}/", allow_redirects=False, timeout=20)
        location = response.headers.get("Location", "")
        if response.status_code in (301, 302, 307, 308) and location.startswith("http"):
            parts = urlsplit(location)
            APPS["base"] = f"{parts.scheme}://{parts.netloc}"
        else:
            APPS["base"] = BASE_URL  # same-origin mode
    print(f"\n  platform origin: {BASE_URL} | apps origin: {APPS['base']}")
    yield


def _wait_for_install(admin, slug: str, timeout: int = 240) -> dict:
    """Poll the install job until it is no longer running."""
    deadline = time.time() + timeout
    state = {}
    while time.time() < deadline:
        state = admin.get(f"{BASE_URL}/admin/api/hosted-apps/{slug}/install?lines=200", timeout=20).json()
        if state.get("status") != "installing":
            return state
        time.sleep(2)
    raise AssertionError(f"install of {slug} did not finish within {timeout}s: {state.get('step')}")


def _login(username: str = USERNAME, password: str = PASSWORD) -> requests.Session:
    session = requests.Session()
    session.get(f"{BASE_URL}/login", timeout=15)
    response = session.post(
        f"{BASE_URL}/login", data={"username": username, "password": password},
        allow_redirects=True, timeout=15,
    )
    assert response.status_code < 400, f"login failed for {username}: {response.status_code}"
    return session


@pytest.fixture(scope="module")
def admin():
    try:
        requests.get(f"{BASE_URL}/login", timeout=5)
    except requests.RequestException as exc:
        pytest.fail(f"App is not running at {BASE_URL}: {exc}")
    return _login()


@pytest.fixture(scope="module")
def deployed(admin):
    """Import the sample app, wait for its environment, start it, then clean up."""
    admin.delete(f"{BASE_URL}/admin/api/hosted-apps/{SLUG}", timeout=60)
    response = admin.post(
        f"{BASE_URL}/admin/api/hosted-apps/import",
        files={"file": (f"{SLUG}.zip", _archive(), "application/zip")},
        data={"start": "1"}, timeout=60,
    )
    assert response.status_code == 202, f"import failed: {response.status_code} {response.text[:400]}"

    state = _wait_for_install(admin, SLUG)
    assert state["status"] != "error", f"install failed: {state.get('error')}"
    started = admin.post(f"{BASE_URL}/admin/api/hosted-apps/{SLUG}/start", timeout=120)
    assert started.status_code == 200, f"could not start: {started.text[:300]}"

    listed = admin.get(f"{BASE_URL}/admin/api/hosted-apps", timeout=20).json()["apps"]
    app = next(a for a in listed if a["slug"] == SLUG)

    yield app

    admin.post(f"{BASE_URL}/admin/api/hosted-apps/{SLUG}/stop", timeout=60)
    admin.delete(f"{BASE_URL}/admin/api/hosted-apps/{SLUG}", timeout=60)


def test_capabilities_are_reported(admin):
    response = admin.get(f"{BASE_URL}/admin/api/hosted-apps", timeout=20)
    assert response.status_code == 200
    body = response.json()
    caps = body["capabilities"]
    assert caps["tier"] in ("A", "B+", "B")
    assert body["capabilities_text"]
    assert "landlock" in caps["landlock_status"] or caps["landlock_status"]
    print(f"\n  tier={caps['tier']} ({caps['tier_reason']})")
    print(f"  {body['capabilities_text']}")


def test_imported_app_is_listed_with_its_tier(admin, deployed):
    response = admin.get(f"{BASE_URL}/admin/api/hosted-apps", timeout=20)
    entry = next((a for a in response.json()["apps"] if a["slug"] == SLUG), None)
    assert entry is not None, "the imported app is missing from the list"
    assert entry["status"] == "running"
    assert entry["url"] == f"/apps/{SLUG}/"
    assert entry["tier"] in ("A", "B+", "B")
    print(f"\n  {entry['name']} -> {entry['url']} | tier={entry['tier']} | "
          f"filesystem_isolated={entry['filesystem_isolated']}")


def test_page_is_served_under_the_apps_origin(admin, deployed):
    response = admin.get(apps(f"/apps/{SLUG}/"), timeout=20)
    assert response.status_code == 200
    assert "Hosted App Demo" in response.text
    # The app generated its own asset URLs through the prefix the platform gave it.
    assert f"/apps/{SLUG}/static/app.css" in response.text
    assert f"/apps/{SLUG}/static/react.production.min.js" in response.text


def test_platform_injects_the_signed_in_identity(admin, deployed):
    """The app never authenticates; it is told who is calling."""
    body = admin.get(apps(f"/apps/{SLUG}/api/whoami"), timeout=20).json()
    assert body["username"] == USERNAME, f"identity not injected: {body}"
    assert body["user_id"], "user id was not injected"
    assert body["prefix"] == f"/apps/{SLUG}"
    print(f"\n  app sees: {body}")


def test_app_reports_its_own_confinement(admin, deployed):
    """Confinement is observable from inside the app, not just asserted by us."""
    body = admin.get(apps(f"/apps/{SLUG}/api/selfcheck"), timeout=20).json()
    checks = body["checks"]
    assert checks["platform_file"].startswith("DENIED"), f"platform file was readable: {checks}"
    assert checks["outbound_network"].startswith("DENIED"), f"network was reachable: {checks}"
    assert checks["new_socket"].startswith("DENIED"), f"a socket was created: {checks}"
    assert checks["write_own_dir"] == "OK", f"the app cannot write its own directory: {checks}"
    print(f"\n  {checks}")


def test_static_assets_and_redirects_resolve_through_the_prefix(admin, deployed):
    css = admin.get(apps(f"/apps/{SLUG}/static/app.css"), timeout=20)
    assert css.status_code == 200 and "text/css" in css.headers.get("content-type", "")
    react = admin.get(apps(f"/apps/{SLUG}/static/react.production.min.js"), timeout=20)
    assert react.status_code == 200 and len(react.content) > 5000

    # A bare path without the trailing slash must redirect, or relative links break.
    bare = admin.get(apps(f"/apps/{SLUG}"), allow_redirects=False, timeout=20)
    assert bare.status_code in (301, 302, 307, 308)
    assert bare.headers["Location"].endswith(f"/apps/{SLUG}/")


def test_platform_origin_redirects_app_requests(admin, deployed):
    """Links and bookmarks on the platform's URL still work - they redirect.

    App content is never served on the platform's own origin, because a document
    there is same-origin with the platform's JavaScript and could act as the
    signed-in user.
    """
    response = admin.get(f"{BASE_URL}/apps/{SLUG}/", allow_redirects=False, timeout=20)
    assert response.status_code in (301, 302, 307, 308), response.status_code
    location = response.headers["Location"]
    assert location.startswith(f"{APPS['base']}/apps/{SLUG}/"), location
    print(f"\n  {BASE_URL}/apps/{SLUG}/ -> {location}")


def test_platform_paths_are_not_served_on_the_apps_origin(admin, deployed):
    """The apps origin serves /apps/* and nothing else.

    If a platform path were reachable there, an app's own JavaScript would be
    same-origin with it and the separation would be worth nothing.
    """
    for path in ("/", "/login", "/admin/api/hosted-apps", "/admin/api/users",
                 "/static/css/app.css", "/api/workspaces"):
        response = admin.get(apps(path), timeout=20)
        assert response.status_code == 404, f"{path} is served on the apps origin: {response.status_code}"
    print("\n  platform paths on the apps origin: all 404")


def test_cross_origin_write_from_the_apps_origin_is_refused(admin, deployed):
    """A form post the browser would deliver from an app page is refused.

    CSP connect-src already stops fetch/XHR, but a plain form submission is not
    covered by it: the browser names the initiating origin, and the platform
    refuses anything that is not its own.
    """
    response = admin.post(
        f"{BASE_URL}/admin/api/hosted-apps/{SLUG}/start",
        headers={"Origin": APPS["base"]}, timeout=20,
    )
    assert response.status_code == 403, f"cross-origin write was accepted: {response.status_code}"
    print(f"\n  POST from {APPS['base']} -> {response.status_code}")

    # And the same request from the platform's own origin still works.
    own = admin.post(
        f"{BASE_URL}/admin/api/hosted-apps/{SLUG}/start",
        headers={"Origin": BASE_URL}, timeout=20,
    )
    assert own.status_code == 200, f"same-origin write was refused: {own.status_code}"


def test_the_app_has_no_port_of_its_own(admin, deployed):
    """The only route in is the platform's URL; nothing listens on a TCP port."""
    import socket

    from src.hosting import supervisor

    sock = supervisor.socket_path(SLUG)
    assert sock.exists() and str(sock).endswith(".sock"), "the app should be reachable over a unix socket"
    # The app's own port range is never bound: scanning the loopback for a second
    # listener for this app must find nothing.
    for port in range(8000, 8010):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.settimeout(0.2)
            assert probe.connect_ex(("127.0.0.1", port)) != 0, f"unexpected listener on {port}"


def test_access_requires_the_hosted_apps_module(admin, deployed):
    """A user without the module is refused, on the page and on the API."""
    suffix = uuid.uuid4().hex[:8]
    role = admin.post(
        f"{BASE_URL}/admin/api/roles",
        json={"name": f"hosted-nomod-{suffix}", "description": "no module access"},
        timeout=20,
    )
    assert role.status_code in (200, 201), role.text[:300]
    body = role.json()
    role_id = body.get("role_id") or (body.get("role") or {}).get("id") or body.get("id")
    assert role_id, f"could not determine role id: {role.text[:300]}"
    # Explicitly grant nothing.
    admin.put(f"{BASE_URL}/admin/api/roles/{role_id}/permissions", json={"modules": []}, timeout=20)

    username = f"hosted_user_{suffix}"
    created = admin.post(
        f"{BASE_URL}/admin/api/users",
        json={"username": username, "email": f"{username}@example.com",
              "password": "Passw0rd!23", "roles": [role_id]},
        timeout=20,
    )
    assert created.status_code in (200, 201), created.text[:300]
    created_body = created.json()
    user_id = (created_body.get("user") or {}).get("id") or created_body.get("user_id")
    try:
        limited = _login(username, "Passw0rd!23")
        api = limited.get(f"{BASE_URL}/admin/api/hosted-apps", timeout=20)
        assert api.status_code == 403, f"API was not protected: {api.status_code}"

        page = limited.get(apps(f"/apps/{SLUG}/"), allow_redirects=False, timeout=20)
        assert page.status_code != 200, "a user without the module could open the app"
        assert page.status_code in (302, 303, 403), f"unexpected status {page.status_code}"
        print(f"\n  without the module: API={api.status_code}, page={page.status_code}")
    finally:
        if user_id:
            admin.delete(f"{BASE_URL}/admin/api/users/{user_id}", timeout=20)
        admin.delete(f"{BASE_URL}/admin/api/roles/{role_id}", timeout=20)


def test_install_is_a_background_job_with_a_step_by_step_log(admin):
    """The upload returns at once; the steps are visible while they run."""
    probe = f"{SLUG}-install-probe"
    admin.delete(f"{BASE_URL}/admin/api/hosted-apps/{probe}", timeout=60)
    started = time.time()
    response = admin.post(
        f"{BASE_URL}/admin/api/hosted-apps/import",
        files={"file": (f"{probe}.zip", _archive(slug=probe), "application/zip")},
        data={"start": "1"}, timeout=60,
    )
    elapsed = time.time() - started
    assert response.status_code == 202, f"expected 202 Accepted, got {response.status_code}"
    assert response.json().get("start_when_ready") is True
    assert elapsed < 20, f"the upload blocked for {elapsed:.1f}s: the install should not hold the request"

    # Watch it happen: the log is dropped once the environment is ready, so the
    # steps are collected while the install is running. This is exactly what the
    # import dialog does.
    steps_seen: list[str] = []
    deadline = time.time() + 240
    state = {}
    while time.time() < deadline:
        state = admin.get(f"{BASE_URL}/admin/api/hosted-apps/{probe}/install?lines=200", timeout=20).json()
        if state.get("step"):
            steps_seen.append(state["step"])
        if state.get("status") != "installing":
            break
        time.sleep(0.3)
    assert state.get("status") != "error", f"install failed: {state.get('error')}"
    assert steps_seen, "the install reported no progress at all while it ran"

    # The durable record is the app log line: the bulky install log is dropped
    # once the environment is ready, and this is what survives it.
    from pathlib import Path as _Path

    workspace = _Path(next(a for a in admin.get(f"{BASE_URL}/admin/api/hosted-apps", timeout=20).json()["apps"]
                           if a["slug"] == probe)["workspace"])
    recorded = [line for line in (workspace / "logs" / "app.log").read_text(errors="replace").splitlines()
                if "environment ready" in line]
    assert recorded, "the app log should point at the install record"
    print(f"\n  install finished in {time.time() - started:.1f}s; "
          f"live steps observed: {len(set(steps_seen))}; install log kept")

    # The install log is kept: it holds the full output of what ran, which is
    # what someone reads when the app later misbehaves.
    install_log = (workspace / "logs" / "install.log").read_text(errors="replace")
    assert "creating the virtualenv" in install_log, install_log[-300:]
    assert "pip install" in install_log, "the pip command and its output should be recorded"
    assert "environment ready" in install_log
    print("  install log kept, with the commands and their output")
    admin.delete(f"{BASE_URL}/admin/api/hosted-apps/{probe}", timeout=60)


def test_an_archive_without_requirements_is_rejected(admin):
    """Every app declares its dependencies; the file is what builds its venv."""
    response = admin.post(
        f"{BASE_URL}/admin/api/hosted-apps/import",
        files={"file": (f"{SLUG}.zip", _archive(requirements=False), "application/zip")},
        timeout=60,
    )
    assert response.status_code == 400, f"expected rejection, got {response.status_code}"
    assert "requirements.txt" in response.text, response.text[:200]

    print(f"\n  {response.json().get('error', '')[:110]}")


def test_the_workspace_path_is_reported(admin, deployed):
    """The Edit dialog shows where the app lives, so an operator can look."""
    assert deployed.get("workspace"), "the app payload carries no workspace path"
    from pathlib import Path as _Path

    workspace = _Path(deployed["workspace"])
    assert workspace.is_dir(), f"{workspace} is not a directory"
    assert (workspace / "app").is_dir(), f"the app code is not in {workspace}"
    assert (workspace / "venv").is_dir(), f"the virtualenv is not in {workspace}"
    assert (workspace / "logs" / "app.log").exists(), "the app log is missing from the workspace"
    print(f"\n  workspace: {workspace}")


def test_content_policy_is_per_application(admin, deployed):
    """A policy restricts the app it belongs to, and no other."""
    mine = f"{BASE_URL}/admin/api/hosted-apps/{SLUG}/policy"
    other = f"{BASE_URL}/admin/api/hosted-apps/{SLUG}-install-probe/policy"
    original = admin.get(mine, timeout=20).json()["policy"]
    try:
        admin.put(mine, json={"allowed_content_types": ["text/html"], "block_attachments": False}, timeout=20)

        blocked = admin.get(apps(f"/apps/{SLUG}/static/app.css"), timeout=20)
        assert blocked.status_code == 403, f"the stylesheet should be blocked, got {blocked.status_code}"
        assert "content policy" in blocked.text.lower()
        assert admin.get(apps(f"/apps/{SLUG}/"), timeout=20).status_code == 200, "html should still be served"

        # The stored policy is the app's own, and another app keeps its own.
        stored = admin.get(mine, timeout=20).json()["policy"]
        assert stored["allowed_content_types"] == ["text/html"], stored
        listed = {a["slug"]: a.get("content_policy") for a in
                  admin.get(f"{BASE_URL}/admin/api/hosted-apps", timeout=20).json()["apps"]}
        assert listed[SLUG]["allowed_content_types"] == ["text/html"]
        for slug, policy in listed.items():
            if slug != SLUG and policy:
                assert policy["allowed_content_types"] != ["text/html"], f"{slug} should be unaffected"
        print("\n  text/html allowed and text/css blocked for this app only")
    finally:
        admin.put(mine, json=original, timeout=20)


def test_payload_limits_cap_bulk_downloads_per_content_type(admin, deployed):
    """A per-type size cap stops a bulk export the allowlist would otherwise let out.

    The export is streamed (no ``Content-Length``), so the host cannot learn its
    size from a header - it has to enforce the cap while reading, which is the
    case a header-only check would miss. The cap belongs to the type: raising it
    releases the same endpoint, and a type with no cap is never affected.
    """
    mine = f"{BASE_URL}/admin/api/hosted-apps/{SLUG}/policy"
    original = admin.get(mine, timeout=20).json()["policy"]
    try:
        # 1. A streamed JSON export over its cap is refused, in full, with a 413.
        admin.put(mine, json={
            "allowed_content_types": ["text/html", "application/json"],
            "block_attachments": False,
            "content_type_limits": {"application/json": 64 * 1024},
        }, timeout=20)

        big = admin.get(apps(f"/apps/{SLUG}/api/export?rows=5000"), timeout=30)
        assert big.status_code == 413, f"the bulk export should be refused, got {big.status_code}"
        refusal = big.json()
        assert refusal["error"] == "payload_too_large", refusal
        assert "application/json" in refusal["message"] and "64 KB" in refusal["message"], refusal
        print(f"\n  export over cap -> 413: {refusal['message']}")

        # 2. Under the cap the same endpoint is served, and a type with no cap
        #    (the page) is untouched.
        rows = admin.get(apps(f"/apps/{SLUG}/api/export?rows=200"), timeout=30)
        assert rows.status_code == 200 and rows.json()[0]["id"] == 0, rows.text[:200]
        page = admin.get(apps(f"/apps/{SLUG}/"), timeout=20)
        assert page.status_code == 200 and "Hosted App Demo" in page.text

        # 3. A response that *declares* an over-cap length is refused too, without
        #    its body being read at all.
        admin.put(mine, json={
            "allowed_content_types": ["text/html", "application/json"],
            "block_attachments": False,
            "content_type_limits": {"application/json": 64},
        }, timeout=20)
        declared = admin.get(apps(f"/apps/{SLUG}/api/summary"), timeout=20)
        assert declared.status_code == 413, f"a declared over-cap body should be refused: {declared.status_code}"

        # 4. Raising the cap releases the export: the limit is the app's, not a
        #    one-way switch.
        admin.put(mine, json={
            "allowed_content_types": ["text/html", "application/json"],
            "block_attachments": False,
            "content_type_limits": {"application/json": 10 * 1024 * 1024},
        }, timeout=20)
        released = admin.get(apps(f"/apps/{SLUG}/api/export?rows=5000"), timeout=30)
        assert released.status_code == 200 and len(released.content) > 64 * 1024, \
            f"the export should be served under a 10 MB cap: {released.status_code}"

        # 5. The cap follows the allowlist: a type that is not allowed cannot keep
        #    a cap of its own.
        admin.put(mine, json={
            "allowed_content_types": ["text/html"],
            "block_attachments": False,
            "content_type_limits": {"application/json": 64},
        }, timeout=20)
        stored = admin.get(mine, timeout=20).json()["policy"]
        assert stored["content_type_limits"] == {}, stored
        print("  cap under the cap-per-type rules: per-type, releasable, allowlist-bound")
    finally:
        admin.put(mine, json=original, timeout=20)


def test_cache_revalidation_survives_a_content_policy(admin, deployed):
    """A cached asset must still revalidate under a policy; a 304 is not content.

    A 304 declares no content type at all, so judging it against the allowlist
    turned every warm-cache page load into a 403 and left the app unstyled. The
    policy governs bodies, and a 304 has none.
    """
    mine = f"{BASE_URL}/admin/api/hosted-apps/{SLUG}/policy"
    original = admin.get(mine, timeout=20).json()["policy"]
    try:
        admin.put(mine, json={
            "allowed_content_types": ["text/html", "text/css", "text/javascript",
                                      "application/javascript"],
            "block_attachments": False,
            "content_type_limits": {},
        }, timeout=20)

        first = admin.get(apps(f"/apps/{SLUG}/static/app.css"), timeout=20)
        assert first.status_code == 200, first.status_code
        validator = first.headers.get("ETag") or first.headers.get("Last-Modified")
        assert validator, "the asset is served without a validator, so it cannot revalidate"

        again = admin.get(apps(f"/apps/{SLUG}/static/app.css"),
                          headers={"If-None-Match": first.headers.get("ETag", "")}, timeout=20)
        assert again.status_code == 304, f"revalidation was refused: {again.status_code}"
        assert not again.content, "a 304 must not carry a body"
        assert admin.get(apps(f"/apps/{SLUG}/"), timeout=20).status_code == 200
        print("\n  cached asset revalidates 304 under a policy; the page still serves")
    finally:
        admin.put(mine, json=original, timeout=20)


def test_source_ip_allowlist_admits_only_named_addresses(admin, deployed):
    """Only the addresses an operator names may reach the app; empty means all.

    Judged on the address the platform actually saw, so this is a real network
    gate rather than something a client can spoof with a forwarding header.
    """
    mine = f"{BASE_URL}/admin/api/hosted-apps/{SLUG}/policy"
    original = admin.get(mine, timeout=20).json()["policy"]
    try:
        who = admin.get(apps(f"/apps/{SLUG}/api/whoami"), timeout=20).json()
        client_ip = (who.get("client_ip") or "").strip()
        assert client_ip, f"the app did not report the address it was called from: {who}"
        forms = [client_ip]
        if "." in client_ip:  # the IPv4 forms the editor offers
            forms += [f"{client_ip}/32", ".".join(client_ip.split(".")[:3]) + ".*"]

        # An empty list is no restriction.
        admin.put(mine, json={**original, "source_ip_allowlist": []}, timeout=20)
        assert admin.get(apps(f"/apps/{SLUG}/"), timeout=20).status_code == 200

        # The caller's own address is admitted, in each accepted form.
        for rule in forms:
            admin.put(mine, json={**original, "source_ip_allowlist": [rule]}, timeout=20)
            assert admin.get(apps(f"/apps/{SLUG}/"), timeout=20).status_code == 200, rule

        # A different network is refused, page and API alike.
        admin.put(mine, json={**original, "source_ip_allowlist": ["10.99.99.0/24"]}, timeout=20)
        page = admin.get(apps(f"/apps/{SLUG}/"), timeout=20)
        assert page.status_code == 403, page.status_code
        assert "source ip policy" in page.text.lower(), page.text[:200]
        api = admin.get(apps(f"/apps/{SLUG}/api/summary"), timeout=20)
        assert api.status_code == 403 and api.json()["error"] == "blocked_by_source_ip", api.text[:200]

        # An entry that cannot be enforced is rejected, not silently stored.
        bad = admin.put(mine, json={**original, "source_ip_allowlist": ["not-an-ip"]}, timeout=20)
        assert bad.status_code == 400 and "not-an-ip" in bad.text, bad.text[:200]

        # Several rules at once, stored in the order they were given.
        admin.put(mine, json={**original, "source_ip_allowlist": ["10.0.0.0/8", forms[-1]]}, timeout=20)
        stored = admin.get(mine, timeout=20).json()["policy"]["source_ip_allowlist"]
        assert stored == ["10.0.0.0/8", forms[-1]], stored
        print(f"\n  source IP allowlist: {client_ip} admitted exact/CIDR/wildcard; other subnet 403")
    finally:
        admin.put(mine, json=original, timeout=20)


def test_logs_are_useful_without_the_app_printing_anything(admin, deployed):
    """The logs view must explain the start, not show an empty box.

    A healthy app writes nothing of its own, and gunicorn is silent at error
    level, so the platform records the tier and the command it ran.
    """
    body = admin.get(f"{BASE_URL}/admin/api/hosted-apps/{SLUG}/logs?lines=100", timeout=20).json()
    log = body.get("log") or ""
    assert "isolation tier" in log, f"the log does not say which tier the app runs under: {log[:200]!r}"
    assert "gunicorn" in log.lower(), f"the log does not show the app starting: {log[:200]!r}"
    tier_line = next(line for line in log.splitlines() if "isolation tier" in line)
    print(f"\n  {tier_line.strip()}")


def test_per_application_access_is_enforced(admin, deployed):
    """Role grants on one application decide who, besides the owner, may open it.

    The app belongs to the administrator who imported it, so a module holder
    with no grant is refused and a grant to their role admits them. Both
    directions are asserted, because an ACL that never denies anything is worse
    than none at all.
    """
    import uuid as _uuid

    suffix = _uuid.uuid4().hex[:8]
    role_name = f"hosted-access-{suffix}"
    role = admin.post(
        f"{BASE_URL}/admin/api/roles", json={"name": role_name, "description": "hosted app access"},
        timeout=20,
    ).json()
    role_id = role.get("role_id") or (role.get("role") or {}).get("id")
    assert role_id, f"could not create the test role: {role}"
    # The role must pass the coarse module gate; the per-app grant is what varies.
    admin.put(f"{BASE_URL}/admin/api/roles/{role_id}/permissions", json={"modules": ["hosted_apps"]}, timeout=20)

    username = f"hosted_acl_{suffix}"
    created = admin.post(
        f"{BASE_URL}/admin/api/users",
        json={"username": username, "email": f"{username}@example.com",
              "password": "Passw0rd!23", "roles": [role_id]},
        timeout=20,
    ).json()
    user_id = (created.get("user") or {}).get("id") or created.get("user_id")

    try:
        user = _login(username, "Passw0rd!23")

        # 1. no grants -> only the owner (the importing admin) may open it
        admin.put(f"{BASE_URL}/admin/api/hosted-apps/{SLUG}/access", json={"roles": []}, timeout=20)
        denied = user.get(apps(f"/apps/{SLUG}/"), allow_redirects=False, timeout=20)
        assert denied.status_code == 403, \
            f"with no grants a module holder who is not the owner got {denied.status_code}"

        # 2. granted to some other role -> refused
        others = [r for r in admin.get(f"{BASE_URL}/admin/api/hosted-apps/{SLUG}/access", timeout=20)
                  .json()["roles"] if int(r["id"]) != int(role_id)]
        assert others, "expected at least one other role to restrict to"
        admin.put(f"{BASE_URL}/admin/api/hosted-apps/{SLUG}/access",
                  json={"roles": [int(others[0]["id"])]}, timeout=20)
        denied = user.get(apps(f"/apps/{SLUG}/"), allow_redirects=False, timeout=20)
        assert denied.status_code == 403, f"a user without a granted role got {denied.status_code}"

        # 3. granted to their role -> allowed again
        admin.put(f"{BASE_URL}/admin/api/hosted-apps/{SLUG}/access", json={"roles": [int(role_id)]}, timeout=20)
        assert user.get(apps(f"/apps/{SLUG}/"), timeout=20).status_code == 200, \
            "a user in a granted role should be allowed"
        print("\n  per-app access: open -> 403 when restricted -> 200 when granted")
    finally:
        admin.put(f"{BASE_URL}/admin/api/hosted-apps/{SLUG}/access", json={"roles": []}, timeout=20)
        if user_id:
            admin.delete(f"{BASE_URL}/admin/api/users/{user_id}", timeout=20)
        admin.delete(f"{BASE_URL}/admin/api/roles/{role_id}", timeout=20)


def test_audit_trail_records_hosted_app_access(admin, deployed):
    """Hosted-app traffic lands in the same audit trail as everything else."""
    before = time.time() - 5
    admin.get(apps(f"/apps/{SLUG}/"), timeout=20)
    admin.get(apps(f"/apps/{SLUG}/api/summary"), timeout=20)
    time.sleep(1.0)

    assert AUDIT_DIR.is_dir(), f"no audit directory at {AUDIT_DIR}"
    lines = []
    for path in sorted(AUDIT_DIR.glob("*"), key=lambda p: p.stat().st_mtime, reverse=True)[:2]:
        if path.is_file():
            lines.extend(path.read_text(errors="replace").splitlines()[-4000:])

    hosted = [line for line in lines if f"/apps/{SLUG}" in line]
    assert hosted, "no audit entries were written for hosted-app requests"
    modules = [line for line in hosted if "hosted_apps" in line]
    assert modules, f"audit entries do not record the module: {hosted[-3:]}"
    print(f"\n  {len(hosted)} audit entries for /apps/{SLUG}; latest:\n    {hosted[-1][:200]}")


def test_node_packages_are_installed_and_built(admin):
    """A React app's npm dependencies and build step run at install time.

    This is the whole point of the package.json support: the JavaScript does not
    exist in the uploaded archive. It is produced by esbuild during install, from
    the packages the app declared, and served as a static file afterwards.
    """
    slug = "react-hosted-demo-test"
    admin.delete(f"{BASE_URL}/admin/api/hosted-apps/{slug}", timeout=60)
    try:
        response = admin.post(
            f"{BASE_URL}/admin/api/hosted-apps/import",
            files={"file": (f"{slug}.zip", _archive(sample=REACT_SAMPLE_DIR, slug=slug), "application/zip")},
            data={"start": "1"}, timeout=60,
        )
        assert response.status_code == 202, f"import failed: {response.status_code} {response.text[:300]}"

        state = _wait_for_install(admin, slug, timeout=600)
        assert state["status"] != "error", f"the Node install or build failed: {state.get('error')}"

        # The build produced the bundle the app serves.
        row = next(a for a in admin.get(f"{BASE_URL}/admin/api/hosted-apps", timeout=20).json()["apps"]
                   if a["slug"] == slug)
        workspace = pathlib.Path(row["workspace"])
        bundle = workspace / "app" / "static" / "bundle.js"
        assert bundle.is_file(), "esbuild did not produce static/bundle.js"
        assert bundle.stat().st_size > 50_000, f"the bundle looks too small: {bundle.stat().st_size} bytes"
        assert (workspace / "app" / "node_modules" / "react").is_dir(), "react was not installed"

        assert admin.post(f"{BASE_URL}/admin/api/hosted-apps/{slug}/start", timeout=120).status_code == 200
        served = admin.get(apps(f"/apps/{slug}/static/bundle.js"), timeout=20)
        assert served.status_code == 200 and len(served.content) > 50_000, "the built bundle is not served"
        page = admin.get(apps(f"/apps/{slug}/"), timeout=20)
        assert page.status_code == 200 and "/static/bundle.js" in page.text
        print(f"\n  npm install + esbuild build ran at install time; bundle served: {len(served.content)} bytes")
    finally:
        admin.post(f"{BASE_URL}/admin/api/hosted-apps/{slug}/stop", timeout=60)
        admin.delete(f"{BASE_URL}/admin/api/hosted-apps/{slug}", timeout=60)


def test_starting_an_app_that_is_still_installing_is_refused(admin):
    """The lifecycle must not race the installer."""
    slug = "install-race-test"
    admin.delete(f"{BASE_URL}/admin/api/hosted-apps/{slug}", timeout=60)
    try:
        response = admin.post(
            f"{BASE_URL}/admin/api/hosted-apps/import",
            files={"file": (f"{slug}.zip", _archive(sample=REACT_SAMPLE_DIR, slug=slug), "application/zip")},
            timeout=60,
        )
        assert response.status_code == 202
        started = admin.post(f"{BASE_URL}/admin/api/hosted-apps/{slug}/start", timeout=30)
        if started.status_code == 409:
            assert "installing" in started.text.lower()
            print(f"\n  start while installing -> 409: {started.json().get('message', '')[:70]}")
        else:
            # The install won the race; that is fine, just not the case under test.
            assert started.status_code in (200, 409), started.text[:200]
    finally:
        _wait_for_install(admin, slug, timeout=300)
        admin.post(f"{BASE_URL}/admin/api/hosted-apps/{slug}/stop", timeout=60)
        admin.delete(f"{BASE_URL}/admin/api/hosted-apps/{slug}", timeout=60)


def test_an_interrupted_install_can_be_retried(admin):
    """A stopped or failed install can be run again without re-uploading.

    Retrying is the point: an install interrupted by a restart, or failed on a
    network blip, must not require deleting the app and starting over.
    """
    slug = "retry-probe"
    admin.delete(f"{BASE_URL}/admin/api/hosted-apps/{slug}", timeout=60)
    try:
        response = admin.post(
            f"{BASE_URL}/admin/api/hosted-apps/import",
            files={"file": (f"{slug}.zip", _archive(sample=REACT_SAMPLE_DIR, slug=slug), "application/zip")},
            timeout=60,
        )
        assert response.status_code == 202
        assert _wait_for_install(admin, slug, timeout=600)["status"] != "error"

        # Retry the install from the files already on disk.
        again = admin.post(f"{BASE_URL}/admin/api/hosted-apps/{slug}/reinstall", timeout=60)
        assert again.status_code == 200, again.text[:300]
        state = _wait_for_install(admin, slug, timeout=600)
        assert state["status"] != "error", f"the retry failed: {state.get('error')}"

        log = (pathlib.Path(next(a for a in admin.get(f"{BASE_URL}/admin/api/hosted-apps", timeout=20)
                                 .json()["apps"] if a["slug"] == slug)["workspace"])
               / "logs" / "install.log").read_text(errors="replace")
        assert "retrying the install" in log, "the retry was not recorded in the install log"
        print("\n  reinstall re-ran the environment setup from the files on disk")
    finally:
        admin.delete(f"{BASE_URL}/admin/api/hosted-apps/{slug}", timeout=60)


def test_an_app_whose_files_vanished_says_so(admin):
    """A row without a workspace must explain itself, not say 'not installed'."""
    slug = "vanished-probe"
    admin.delete(f"{BASE_URL}/admin/api/hosted-apps/{slug}", timeout=60)
    response = admin.post(
        f"{BASE_URL}/admin/api/hosted-apps/import",
        files={"file": (f"{slug}.zip", _archive(slug=slug), "application/zip")},
        timeout=60,
    )
    assert response.status_code == 202
    _wait_for_install(admin, slug, timeout=300)

    import shutil as _shutil

    workspace = pathlib.Path(next(a for a in admin.get(f"{BASE_URL}/admin/api/hosted-apps", timeout=20)
                                  .json()["apps"] if a["slug"] == slug)["workspace"])
    _shutil.rmtree(workspace, ignore_errors=True)
    try:
        started = admin.post(f"{BASE_URL}/admin/api/hosted-apps/{slug}/start", timeout=30)
        assert started.status_code in (409, 500), started.text[:200]
        message = (started.json().get("message") or "") if "json" in (started.headers.get("content-type") or "") else started.text
        assert "missing" in message.lower(), f"the message should say the files are missing: {message[:200]}"
        print(f"\n  {message[:110]}")
    finally:
        admin.delete(f"{BASE_URL}/admin/api/hosted-apps/{slug}", timeout=60)
