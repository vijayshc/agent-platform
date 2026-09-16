"""Live catalog create + teardown must drop agent, conversation, and API key."""

from __future__ import annotations

from livehelpers import LiveClient


def _assert_deletes_ok(results: dict) -> None:
    for kind, rows in results.items():
        for ident, status, body in rows:
            assert "AttributeError" not in (body or ""), f"{kind} {ident} DELETE body: {body}"
            assert status == 200, f"{kind} {ident} DELETE {status} {body}"


def test_live_teardown_drops_agent_conversation_and_key(api: LiveClient):
    assert api._created_key_ids, "require_live_app must create a test API key"
    kid = api._created_key_ids[0]

    agent = api.create_agent(
        "Cleanup Probe",
        {"kind": "agent", "instructions": "Reply briefly. Do not use tools."},
    )
    slug = agent["slug"]
    assert slug.startswith("live-")
    conv = api.create_conversation(slug, "live-teardown-conv")
    cid = conv.get("public_id") or conv.get("id")
    assert cid

    assert slug in api.list_agent_slugs()
    listed_conv = api.http.get(f"{api.base}/api/v1/conversations/{cid}", **api._timeout(15))
    assert listed_conv.status_code == 200, listed_conv.text[:500]
    listed_keys = api.http.get(f"{api.base}/api/v1/api-keys", **api._timeout(15))
    assert listed_keys.status_code == 200, listed_keys.text[:500]
    assert kid in {int(k["id"]) for k in listed_keys.json()["api_keys"]}

    results = api.teardown()
    _assert_deletes_ok(results)

    # Key is gone; fall back to the login session for post-delete GETs.
    api.http.headers.pop("X-API-Key", None)

    assert slug not in api.list_agent_slugs()
    missing_conv = api.http.get(f"{api.base}/api/v1/conversations/{cid}", **api._timeout(15))
    assert missing_conv.status_code == 404, missing_conv.text[:500]
    keys_after = api.http.get(f"{api.base}/api/v1/api-keys", **api._timeout(15))
    assert keys_after.status_code == 200, keys_after.text[:500]
    assert kid not in {int(k["id"]) for k in keys_after.json()["api_keys"]}
