"""Reusable LIVE tenancy helpers for the module tester agents.

No ``test_`` prefix: pytest must not collect this file.  Every helper talks to
the real running Flask app over HTTP (``requests``), mirroring
``tests/livehelpers.py``: real admin API, real roles/users, real session
cookies.  Nothing here mocks the application.

Typical use::

    from tenant_helpers import TenantProvisioner, assert_cannot_run, assert_can_run

    provisioner = TenantProvisioner()
    try:
        tenant_a = provisioner.provision("tenant-a", {"agent_studio": "write"})
        tenant_b = provisioner.provision("tenant-b", {"agent_studio": "read"})
        agent = provisioner.create_agent(tenant_a)
        assert_can_run(tenant_a.client, agent["slug"], context="owner")
        assert_cannot_run(tenant_b.client, agent["slug"], context="before grant")
    finally:
        provisioner.cleanup()

``cleanup()`` deletes every asset, user and role the provisioner created, and is
safe to call even when earlier steps failed.
"""

from __future__ import annotations

import os
import uuid
from typing import Any, Iterable, Mapping

import requests

BASE_URL = os.environ.get("LIVE_AGENT_BASE_URL", "http://127.0.0.1:5000").rstrip("/")
ADMIN_USERNAME = os.environ.get("LIVE_AGENT_USER", "admin")
ADMIN_PASSWORD = os.environ.get("LIVE_AGENT_PASSWORD", "admin")
DEFAULT_TENANT_PASSWORD = os.environ.get("LIVE_TENANT_PASSWORD", "TenantPass123!")
TIMEOUT = int(os.environ.get("LIVE_AGENT_TIMEOUT", "30"))

#: A connection id that cannot exist, so a run stops at model resolution with a
#: 400 AFTER the access gate.  This lets us prove "may run" without an LLM call.
UNUSABLE_MODEL = {"client": "tenancy-nonexistent-connection", "name": None}


def unique_name(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


# --------------------------------------------------------------------------
# one live identity (session-cookie HTTP client)
# --------------------------------------------------------------------------

class LiveHttp:
    """A requests session bound to one live app identity."""

    def __init__(self, base: str = BASE_URL, label: str = "client"):
        self.base = (base or BASE_URL).rstrip("/")
        self.label = label
        self.session = requests.Session()
        self.user_id: int | None = None
        self.username: str | None = None

    def login(self, username: str, password: str) -> "LiveHttp":
        try:
            self.session.get(f"{self.base}/login", timeout=TIMEOUT).raise_for_status()
        except requests.RequestException as exc:
            raise AssertionError(f"[{self.label}] app not reachable at {self.base}: {exc}") from exc
        response = self.session.post(
            f"{self.base}/login",
            data={"username": username, "password": password},
            allow_redirects=True,
            timeout=TIMEOUT,
        )
        if response.status_code >= 400:
            raise AssertionError(
                f"[{self.label}] login as {username!r} failed: "
                f"{response.status_code} {response.text[:300]}"
            )
        probe = self.session.get(f"{self.base}/api/v1/agents", timeout=TIMEOUT)
        if probe.status_code == 401:
            raise AssertionError(f"[{self.label}] login did not establish a session")
        self.username = username
        return self

    def request(self, method: str, path: str, **kwargs) -> requests.Response:
        kwargs.setdefault("timeout", TIMEOUT)
        return self.session.request(method, f"{self.base}{path}", **kwargs)

    def get(self, path: str, **kwargs) -> requests.Response:
        return self.request("GET", path, **kwargs)

    def post(self, path: str, **kwargs) -> requests.Response:
        return self.request("POST", path, **kwargs)

    def put(self, path: str, **kwargs) -> requests.Response:
        return self.request("PUT", path, **kwargs)

    def delete(self, path: str, **kwargs) -> requests.Response:
        return self.request("DELETE", path, **kwargs)


class Tenant:
    """A provisioned live tenant: one role, one user, one authenticated client."""

    def __init__(self, name, role_id, role_name, user_id, username, client: LiveHttp):
        self.name = name
        self.role_id = int(role_id)
        self.role_name = role_name
        self.user_id = int(user_id)
        self.username = username
        self.client = client

    def __repr__(self) -> str:
        return f"<Tenant {self.name} role={self.role_id} user={self.user_id}>"


def _normalize_modules(modules: Any) -> list:
    """Accept ``{"key": "read"}``, ``["key", ...]`` or ``[{"key","access"}]``."""
    if not modules:
        return []
    if isinstance(modules, Mapping):
        return [{"key": str(key), "access": str(level)} for key, level in modules.items()]
    normalized = []
    for item in modules:
        if isinstance(item, str):
            normalized.append(item)
        elif isinstance(item, Mapping):
            normalized.append(dict(item))
    return normalized


# --------------------------------------------------------------------------
# provisioning
# --------------------------------------------------------------------------

class TenantProvisioner:
    """Provisions and tears down real tenants through the real admin HTTP API."""

    def __init__(self, base: str = BASE_URL, admin_username: str = ADMIN_USERNAME,
                 admin_password: str = ADMIN_PASSWORD):
        self.base = (base or BASE_URL).rstrip("/")
        self.admin = LiveHttp(self.base, "admin").login(admin_username, admin_password)
        self._role_ids: list[int] = []
        self._user_ids: list[int] = []
        self._agents: list[tuple[str, Tenant | None]] = []
        self._cleanup_calls: list = []

    # -- roles ----------------------------------------------------------

    def create_role(self, name: str, modules: Any = None) -> int:
        response = self.admin.post(
            "/admin/api/roles", json={"name": name, "description": "tenancy test role"}
        )
        if response.status_code not in (200, 201):
            raise AssertionError(
                f"create role {name!r} failed: {response.status_code} {response.text[:300]}"
            )
        role_id = int(response.json()["role_id"])
        self._role_ids.append(role_id)
        if modules is not None:
            self.set_role_modules(role_id, modules)
        return role_id

    def set_role_modules(self, role_id: int, modules: Any) -> None:
        response = self.admin.put(
            f"/admin/api/roles/{role_id}/permissions",
            json={"modules": _normalize_modules(modules)},
        )
        if response.status_code != 200:
            raise AssertionError(
                f"set modules on role {role_id} failed: "
                f"{response.status_code} {response.text[:300]}"
            )

    # -- users ----------------------------------------------------------

    def create_user(self, username: str, role_ids: Iterable[int] | None = None,
                    password: str = DEFAULT_TENANT_PASSWORD) -> int:
        response = self.admin.post(
            "/admin/api/users",
            json={
                "username": username,
                "email": f"{username}@tenancy.test",
                "password": password,
                "roles": [int(r) for r in (role_ids or [])],
            },
        )
        if response.status_code not in (200, 201):
            raise AssertionError(
                f"create user {username!r} failed: {response.status_code} {response.text[:300]}"
            )
        user_id = int(response.json()["user"]["id"])
        self._user_ids.append(user_id)
        return user_id

    def provision(self, name: str, modules: Any = None,
                  role_name: str | None = None,
                  password: str = DEFAULT_TENANT_PASSWORD) -> Tenant:
        role_name = role_name or unique_name(f"{name}-role")
        role_id = self.create_role(role_name, modules)
        username = unique_name(f"{name}-user")
        user_id = self.create_user(username, [role_id], password=password)
        client = LiveHttp(self.base, name).login(username, password)
        client.user_id = user_id
        return Tenant(name, role_id, role_name, user_id, username, client)

    # -- assets ---------------------------------------------------------

    def create_agent(self, tenant: Tenant, name: str | None = None,
                     config: dict | None = None, published: bool = False) -> dict:
        """Create an agent owned by ``tenant`` via the real /api/v1/agents API."""
        name = name or unique_name("tenancy-agent")
        body = {
            "name": name,
            "slug": unique_name(name.lower().replace(" ", "-")),
            "kind": "agent",
            "published": bool(published),
            "config": dict(config or {"instructions": "Tenancy probe agent."}),
        }
        response = tenant.client.post("/api/v1/agents", json=body)
        if response.status_code not in (200, 201):
            raise AssertionError(
                f"[{tenant.name}] create agent failed: {response.status_code} {response.text[:400]}"
            )
        row = response.json()
        self._agents.append((str(row["slug"]), tenant))
        return row

    def delete_agent(self, slug: str, client: LiveHttp | None = None) -> bool:
        """Delete an agent; a 404 counts as success (already gone)."""
        client = client or self.admin
        response = client.delete(f"/api/v1/agents/{slug}")
        return response.status_code < 400 or response.status_code == 404

    # -- teardown -------------------------------------------------------

    def cleanup(self) -> list[str]:
        """Delete every created asset/user/role; never raises. Returns errors."""
        errors: list[str] = []
        for slug, _tenant in list(self._agents):
            try:
                if not self.delete_agent(slug):
                    errors.append(f"agent {slug} not deleted")
            except Exception as exc:  # pragma: no cover - defensive
                errors.append(f"agent {slug}: {exc}")
        self._agents.clear()

        for user_id in list(self._user_ids):
            try:
                response = self.admin.delete(f"/admin/api/users/{user_id}")
                if response.status_code >= 400 and response.status_code != 404:
                    errors.append(f"user {user_id} not deleted: {response.status_code}")
            except Exception as exc:  # pragma: no cover - defensive
                errors.append(f"user {user_id}: {exc}")
        self._user_ids.clear()

        for role_id in list(self._role_ids):
            try:
                response = self.admin.delete(f"/admin/api/roles/{role_id}")
                if response.status_code >= 400 and response.status_code != 404:
                    errors.append(f"role {role_id} not deleted: {response.status_code}")
            except Exception as exc:  # pragma: no cover - defensive
                errors.append(f"role {role_id}: {exc}")
        self._role_ids.clear()
        return errors


# --------------------------------------------------------------------------
# assertions: generic access API
# --------------------------------------------------------------------------

def _access_path(resource_type: str, resource_id: int | str) -> str:
    return f"/api/v1/access/{resource_type}/{resource_id}"


def assert_access_granted(client: LiveHttp, resource_type: str, resource_id: int | str,
                          context: str = "") -> requests.Response:
    """Owner/admin can read a resource's grants through the generic API."""
    response = client.get(_access_path(resource_type, resource_id))
    if response.status_code != 200:
        raise AssertionError(
            f"{context or client.label}: expected to manage {resource_type} {resource_id} "
            f"(200), got {response.status_code}: {response.text[:300]}"
        )
    return response


def assert_access_denied(client: LiveHttp, resource_type: str, resource_id: int | str,
                         context: str = "") -> requests.Response:
    """A non-owner/non-admin gets 403 (or 404) from the generic access API."""
    response = client.get(_access_path(resource_type, resource_id))
    if response.status_code not in (403, 404):
        raise AssertionError(
            f"{context or client.label}: expected access management on {resource_type} "
            f"{resource_id} to be denied, got {response.status_code}: {response.text[:300]}"
        )
    return response


# --------------------------------------------------------------------------
# assertions: agent visibility through the real run gate
# --------------------------------------------------------------------------

def run_gate(client: LiveHttp, agent_id: str, model: dict | None = None) -> requests.Response:
    """POST /runs with an unusable model: 403 = no access, 400 = access allowed."""
    return client.post(
        "/api/v1/runs",
        json={"agent_id": agent_id, "input": "tenancy access probe", "model": model or UNUSABLE_MODEL},
    )


def assert_can_run(client: LiveHttp, agent_id: str, context: str = "") -> requests.Response:
    """The user passed the agent access gate (run stopped at model resolution)."""
    response = run_gate(client, agent_id)
    if response.status_code == 403:
        raise AssertionError(
            f"{context or client.label}: expected access to agent {agent_id}, but the run "
            f"gate denied it (403): {response.text[:300]}"
        )
    body = response.json() if response.headers.get("content-type", "").startswith("application/json") else {}
    if response.status_code != 400 or body.get("error") != "model_unavailable":
        raise AssertionError(
            f"{context or client.label}: expected agent {agent_id} to pass the access gate "
            f"(400 model_unavailable), got {response.status_code}: {response.text[:300]}"
        )
    return response


def assert_cannot_run(client: LiveHttp, agent_id: str, context: str = "") -> requests.Response:
    """The user was denied by the agent access gate (403)."""
    response = run_gate(client, agent_id)
    if response.status_code != 403:
        raise AssertionError(
            f"{context or client.label}: expected agent {agent_id} to be hidden, got "
            f"{response.status_code}: {response.text[:300]}"
        )
    return response


# --------------------------------------------------------------------------
# assertions: list visibility
# --------------------------------------------------------------------------

def list_ids(client: LiveHttp, path: str, list_key: str = "agents",
             id_key: str = "id") -> set:
    response = client.get(path)
    if response.status_code >= 400:
        raise AssertionError(
            f"{client.label}: GET {path} failed: {response.status_code} {response.text[:300]}"
        )
    rows = (response.json() or {}).get(list_key) or []
    return {row.get(id_key) for row in rows}


def assert_isolated(client: LiveHttp, path: str, asset_id, *, list_key: str = "agents",
                    id_key: str = "id", context: str = "") -> None:
    """The asset must NOT appear in the caller's list (tenant isolation)."""
    ids = list_ids(client, path, list_key, id_key)
    if asset_id in ids:
        raise AssertionError(
            f"{context or client.label}: asset {asset_id} leaked into {path} "
            f"({len(ids)} rows visible)"
        )


def assert_visible(client: LiveHttp, path: str, asset_id, *, list_key: str = "agents",
                   id_key: str = "id", context: str = "") -> None:
    """The asset MUST appear in the caller's list (grant/ownership works)."""
    ids = list_ids(client, path, list_key, id_key)
    if asset_id not in ids:
        raise AssertionError(
            f"{context or client.label}: asset {asset_id} missing from {path} "
            f"({len(ids)} rows visible)"
        )
