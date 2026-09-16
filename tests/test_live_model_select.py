"""Live tests: chat model list is LLM Manager, and chat/run use that connection.

Hits the running Flask app and a real LLM.
  python -m pytest tests/test_live_model_select.py -q
"""

from __future__ import annotations

from livehelpers import (
    SHORT,
    LiveClient,
    _refetch,
    _reply,
)


LEGACY_CLIENTS = {"ollama", "foundry", "anthropic", "bedrock", "default", "llm_connection"}


def _models(api: LiveClient) -> list[dict]:
    r = api.http.get(f"{api.base}/api/v1/models", timeout=15)
    assert r.status_code == 200, r.text[:500]
    body = r.json()
    models = body.get("models") or []
    assert isinstance(models, list)
    return models


def test_live_models_list_is_llm_manager_only(api: LiveClient):
    models = _models(api)
    assert models, "LLM Manager has no enabled connections — configure one before chatting"
    ids = {str(m.get("id")) for m in models}
    leaked = ids & LEGACY_CLIENTS
    assert not leaked, f"legacy model clients still listed: {leaked}"
    for row in models:
        assert str(row.get("id") or "").isdigit(), row
        assert row.get("name"), row
        assert "api_key" not in row
        assert "base_url" not in row

    admin = api.http.get(f"{api.base}/admin/config/llm/api/list", timeout=15)
    assert admin.status_code == 200, admin.text[:500]
    conns = admin.json().get("data") or []
    enabled = {str(c["id"]) for c in conns if c.get("enabled", True)}
    assert ids == enabled, f"/api/v1/models {ids} != enabled LLM Manager {enabled}"


def test_live_studio_resources_model_clients_match_llm_manager(api: LiveClient):
    models = _models(api)
    res = api.http.get(f"{api.base}/api/v1/studio/resources", timeout=15)
    assert res.status_code == 200, res.text[:500]
    clients = res.json().get("model_clients") or []
    ids = {str(c.get("id")) for c in clients}
    leaked = ids & LEGACY_CLIENTS
    assert not leaked, f"studio still lists legacy model clients: {leaked}"
    manager_ids = {str(m["id"]) for m in models}
    extra = ids - manager_ids - {"scripted"}
    assert not extra, f"studio model_clients not in LLM Manager: {extra}"
    missing = manager_ids - ids
    assert not missing, f"studio missing LLM Manager connections: {missing}"


def test_live_chat_uses_selected_llm_connection(api: LiveClient):
    models = _models(api)
    chosen = next((m for m in models if m.get("is_default")), models[0])
    agent = api.create_agent(
        "Live Chat Model Pick",
        {
            "kind": "agent",
            "instructions": "Reply with one short sentence. Do not use tools.",
            "default_options": SHORT,
        },
    )
    conv = api.create_conversation(agent["slug"], "live-model-pick")
    conv_id = conv.get("public_id") or conv.get("id")
    assert conv_id, conv

    turn = api.http.post(
        f"{api.base}/api/v1/conversations/{conv_id}/messages",
        json={
            "input": "Say hello in five words or fewer.",
            "stream": False,
            "model": {"client": chosen["id"]},
        },
        timeout=(10, 120),
    )
    assert turn.status_code < 400, turn.text[:800]
    body = turn.json()
    run_id = (body.get("run") or {}).get("public_id") or (body.get("run") or {}).get("id")
    assert run_id, body
    run = api.wait_for_run(str(run_id))
    run = _refetch(api, str(run_id))
    reply = _reply(body, run)
    assert run["status"] == "success", run.get("error") or reply
    assert reply.strip(), "chat reply is empty — selected LLM connection did not answer"
    stored = (run.get("input_json") or {}).get("model") or {}
    assert str(stored.get("client")) == str(chosen["id"]), stored


def test_live_run_override_hits_each_enabled_connection(api: LiveClient):
    models = _models(api)
    agent = api.create_agent(
        "Live Run Model Override",
        {
            "kind": "agent",
            "instructions": "Reply with one short sentence. Do not use tools.",
            "default_options": SHORT,
        },
    )
    # Cap at two connections so the live suite stays focused.
    for row in models[:2]:
        payload = api.invoke(
            agent["slug"],
            "Reply with the single word ok.",
            model={"client": row["id"]},
        )
        run_id = (payload.get("run") or {}).get("public_id") or (payload.get("run") or {}).get("id")
        assert run_id, payload
        run = api.wait_for_run(str(run_id))
        run = _refetch(api, str(run_id))
        reply = _reply(payload, run)
        assert run["status"] == "success", f"{row['name']}: {run.get('error') or reply}"
        assert reply.strip(), f"{row['name']} returned an empty reply"
        stored = (run.get("input_json") or {}).get("model") or {}
        assert str(stored.get("client")) == str(row["id"]), stored
