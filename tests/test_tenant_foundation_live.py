"""LIVE end-to-end test of the resource-tenancy foundation (no mocks).

Provisions two tenant users plus a third unrelated tenant through the real admin
HTTP API, has tenant A create a real agent, grants tenant B's role access to it
through the generic ``/api/v1/access/agent/<id>`` API, proves B can reach the
agent's run gate while C cannot, then revokes and proves B loses access again.
Everything created is deleted in ``cleanup()`` even if an assertion fails.
"""

from __future__ import annotations

import pytest

from tenant_helpers import (
    TenantProvisioner,
    assert_access_denied,
    assert_access_granted,
    assert_can_run,
    assert_cannot_run,
)


@pytest.fixture(scope="module")
def provisioner():
    live = TenantProvisioner()
    try:
        yield live
    finally:
        errors = live.cleanup()
        assert not errors, f"tenant cleanup left residue: {errors}"


def test_tenant_visibility_round_trip(provisioner):
    tenant_a = provisioner.provision("tenancy-a", {"agent_studio": "write"})
    tenant_b = provisioner.provision("tenancy-b", {"agent_studio": "read"})
    tenant_c = provisioner.provision("tenancy-c", {"agent_studio": "read"})

    agent = provisioner.create_agent(tenant_a, name="Tenancy Asset")
    agent_id = int(agent["id"])
    slug = str(agent["slug"])

    try:
        # The owner can always reach their own asset.
        assert_can_run(tenant_a.client, slug, context="owner A")

        # Before any grant, neither unrelated tenant may reach it.
        assert_cannot_run(tenant_b.client, slug, context="B before grant")
        assert_cannot_run(tenant_c.client, slug, context="C before grant")

        # Owner (and admin) may manage the asset's grants through the generic API.
        before = assert_access_granted(tenant_a.client, "agent", agent_id, context="owner A")
        assert before.json()["access"] == []

        # An administrator grants tenant B's role on tenant A's agent.
        granted = provisioner.admin.put(
            f"/api/v1/access/agent/{agent_id}", json={"role_ids": [tenant_b.role_id]}
        )
        assert granted.status_code == 200, granted.text
        body = granted.json()
        assert {"role_id": tenant_b.role_id, "role_name": tenant_b.role_name} in body["access"]
        role_ids = {role["id"] for role in body["roles"]}
        assert tenant_b.role_id in role_ids

        # B now sees the asset; C still does not.
        assert_can_run(tenant_b.client, slug, context="B after grant")
        assert_cannot_run(tenant_c.client, slug, context="C after grant")

        # A granted tenant may *use* the asset but not manage its grants.
        assert_access_denied(tenant_b.client, "agent", agent_id, context="B manage attempt")

        # Revoke and prove B loses access immediately.
        revoked = provisioner.admin.put(
            f"/api/v1/access/agent/{agent_id}", json={"role_ids": []}
        )
        assert revoked.status_code == 200, revoked.text
        assert revoked.json()["access"] == []
        assert_cannot_run(tenant_b.client, slug, context="B after revoke")

        # Unknown resource types are rejected as 404, not silently accepted.
        unknown = provisioner.admin.get("/api/v1/access/not_a_real_type/1")
        assert unknown.status_code == 404, unknown.text
    finally:
        provisioner.delete_agent(slug, client=tenant_a.client)
