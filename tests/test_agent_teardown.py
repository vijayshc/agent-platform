"""Created catalog agents are removed by the live helper teardown path."""

from __future__ import annotations

from livehelpers import flask_live_client


def test_create_agent_teardown_removes_slug(app_client):
    """POST /api/v1/agents via LiveClient.create_agent, then DELETE via teardown.

    Drives the shipped catalog create/delete APIs — the same helpers live tests use.
    """
    api = flask_live_client(app_client)
    row = api.create_agent(
        "Teardown Probe",
        {"kind": "agent", "instructions": "Reply briefly."},
    )
    slug = row["slug"]
    assert slug.startswith("live-")

    listed = app_client.get("/api/v1/agents?include_drafts=1")
    assert listed.status_code == 200
    slugs = {a["slug"] for a in listed.get_json()["agents"]}
    assert slug in slugs

    api.teardown()

    after = app_client.get("/api/v1/agents?include_drafts=1")
    assert after.status_code == 200
    remaining = {a["slug"] for a in after.get_json()["agents"]}
    assert slug not in remaining


def test_teardown_also_drops_tracked_conversation_and_key(app_client):
    api = flask_live_client(app_client)
    api.create_api_key()
    agent = api.create_agent(
        "Teardown Conv",
        {"kind": "agent", "instructions": "Reply briefly."},
    )
    conv = api.create_conversation(agent["slug"], "teardown-conv")
    cid = conv.get("public_id") or conv.get("id")
    assert cid

    got = app_client.get(f"/api/v1/conversations/{cid}")
    assert got.status_code == 200

    slug = agent["slug"]
    api.teardown()

    missing_agent = app_client.get(f"/api/v1/agents/{slug}?include_drafts=1")
    # After delete the row is gone; 404 from the shipped GET.
    assert missing_agent.status_code == 404

    missing_conv = app_client.get(f"/api/v1/conversations/{cid}")
    assert missing_conv.status_code == 404


def test_api_keys_list_and_delete_shipped_route(app_client):
    listed = app_client.get("/api/v1/api-keys")
    assert listed.status_code == 200
    assert isinstance(listed.get_json()["api_keys"], list)

    created = app_client.post(
        "/api/v1/api-keys",
        json={"name": "drop-me", "scopes": ["agents:read"]},
    )
    assert created.status_code == 201
    kid = created.get_json()["id"]
    assert kid

    deleted = app_client.delete(f"/api/v1/api-keys/{kid}")
    assert deleted.status_code == 200

    after = app_client.get("/api/v1/api-keys")
    ids = {k["id"] for k in after.get_json()["api_keys"]}
    assert kid not in ids
