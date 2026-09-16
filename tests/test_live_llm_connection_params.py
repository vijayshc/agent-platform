"""Live tests for the LLM Manager's free-form provider configuration.

Everything here runs against the REAL app, the REAL network and the REAL
provider — nothing is mocked:

* custom HTTP headers and the free-form ``extra_body`` are asserted from the
  JSON that a live echo endpoint received over the wire;
* the SSL-verification toggle is asserted against a live host whose certificate
  cannot be verified;
* the operator's ``extra_body`` is sent to the connection's real provider
  through both the raw OpenAI client and a real agent run;
* the admin Test endpoint reports success only for a real completion, and
  reports the provider's own error (unreachable endpoint, wrong model,
  HTTP 200 that is not a chat completion) otherwise.

  python -m pytest \\
      tests/test_live_llm_connection_params.py -q
"""

from __future__ import annotations

import json
import uuid

import pytest
import requests

from livehelpers import BASE_URL, PASSWORD, USERNAME


REAL_EXTRA_BODY = {
    "temperature": 0.7,
    "max_tokens": 256,
    "top_p": 0.8,
    "presence_penalty": 1.5,
    "top_k": 20,
    "chat_template_kwargs": {"enable_thinking": False},
}


class AdminSession(requests.Session):
    """Logged-in admin session for the LLM Manager API."""

    def __init__(self) -> None:
        super().__init__()
        self.get(f"{BASE_URL}/login", timeout=15).raise_for_status()
        r = self.post(
            f"{BASE_URL}/login",
            data={"username": USERNAME, "password": PASSWORD},
            timeout=20,
        )
        if r.status_code >= 400:
            pytest.fail(f"admin login failed: {r.status_code} {r.text[:300]}")

    def save(self, payload: dict) -> int:
        r = self.post(f"{BASE_URL}/admin/config/llm/api/save", json=payload, timeout=30)
        if r.status_code >= 400:
            pytest.fail(f"save connection failed: {r.status_code} {r.text[:400]}")
        return int(r.json()["id"])

    def remove(self, connection_id: int) -> None:
        """DELETE a connection through the LLM Manager API."""
        r = self.delete(f"{BASE_URL}/admin/config/llm/api/delete/{connection_id}", timeout=20)
        if r.status_code >= 400:
            pytest.fail(f"delete connection failed: {r.status_code} {r.text[:300]}")


@pytest.fixture(scope="module")
def admin() -> AdminSession:
    return AdminSession()


@pytest.fixture()
def make_connection(admin: AdminSession):
    """Create connections through the live API and always delete them after."""
    created: list[int] = []

    def _make(**overrides) -> int:
        payload = {
            "name": f"live-params-{uuid.uuid4().hex[:10]}",
            "base_url": "https://httpbin.org/anything",
            "api_key": "sk-live-echo",
            "model_name": "echo-model",
            "enabled": True,
        }
        payload.update(overrides)
        cid = admin.save(payload)
        created.append(cid)
        return cid

    yield _make

    for cid in created:
        admin.remove(cid)


def _real_provider() -> tuple[str, str, str]:
    """base_url / api_key / model of the live default connection (read-only)."""
    from src.utils.database import get_db_connection

    conn = get_db_connection()
    try:
        row = conn.execute(
            "SELECT base_url, api_key, model_name FROM llm_connections "
            "WHERE is_default = 1 LIMIT 1"
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        pytest.fail("no default LLM connection is configured in the live app")
    return str(row["base_url"]), str(row["api_key"]), str(row["model_name"])


def _client_for(connection_id: int):
    """Build the provider client from the connection as it is stored right now."""
    from src.models.llm_connection import LLMConnection
    from src.utils import llm_connection_manager as mgr

    conn = LLMConnection.get_by_id(connection_id)
    assert conn is not None, f"connection {connection_id} disappeared"
    return conn, mgr.build_openai_client(conn, model_name=conn.model_name)


def test_headers_and_model_parameters_reach_the_provider_wire(admin, make_connection):
    """The saved headers/parameters are what the provider actually receives."""
    tenant = f"acme-{uuid.uuid4().hex[:8]}"
    cid = make_connection(
        http_headers={"X-Tenant": tenant, "X-Trace": "live-params"},
        extra_body={
            "temperature": 0.9,
            "max_tokens": 4321,
            "top_k": 20,
            "chat_template_kwargs": {"enable_thinking": False},
        },
    )

    conn, client = _client_for(cid)
    raw = client.chat.completions.with_raw_response.create(
        model=conn.model_name,
        messages=[{"role": "user", "content": "ping"}],
        # Call arguments are deliberately different: operator parameters win.
        max_tokens=5,
        temperature=0.1,
        extra_body=conn.extra_body,
    )
    echo = json.loads(raw.text)
    headers = {k.lower(): v for k, v in echo["headers"].items()}
    body = echo["json"]

    assert headers.get("x-tenant") == tenant
    assert headers.get("x-trace") == "live-params"
    assert body["model"] == "echo-model"
    assert body["max_tokens"] == 4321
    assert body["temperature"] == 0.9
    assert body["top_k"] == 20
    assert body["chat_template_kwargs"] == {"enable_thinking": False}
    assert body["messages"][0]["content"] == "ping"


def test_app_owned_body_keys_are_rejected_by_the_live_api(admin):
    """A connection cannot take over model/messages/stream/extra_body."""
    for key in ("model", "messages", "stream", "extra_body"):
        r = admin.post(
            f"{BASE_URL}/admin/config/llm/api/save",
            json={
                "name": f"live-bad-{key}-{uuid.uuid4().hex[:6]}",
                "base_url": "https://httpbin.org/anything",
                "model_name": "echo-model",
                "extra_body": {key: "x"},
            },
            timeout=30,
        )
        assert r.status_code == 400, f"{key}: {r.status_code} {r.text[:300]}"
        assert key in r.text


def test_masked_headers_survive_an_edit_round_trip(admin, make_connection):
    """The masked value the UI sees must not overwrite the stored secret."""
    tenant = f"acme-{uuid.uuid4().hex[:8]}"
    cid = make_connection(http_headers={"Authorization": f"Bearer {tenant}"})

    listed = admin.get(f"{BASE_URL}/admin/config/llm/api/list", timeout=20).json()["data"]
    row = next(item for item in listed if item["id"] == cid)
    assert row["http_headers"]["Authorization"] == "********"

    # Save the payload exactly as the UI round-trips it (still masked).
    admin.save({**row, "http_headers": dict(row["http_headers"])})

    conn, client = _client_for(cid)
    assert conn.http_headers["Authorization"] == f"Bearer {tenant}"
    raw = client.chat.completions.with_raw_response.create(
        model=conn.model_name, messages=[{"role": "user", "content": "ping"}], max_tokens=5
    )
    echoed = {k.lower(): v for k, v in json.loads(raw.text)["headers"].items()}
    assert echoed.get("authorization") == f"Bearer {tenant}"


def test_verify_ssl_toggle_controls_certificate_checks(admin, make_connection):
    """Disabled verification must bypass a live invalid certificate."""
    cid = make_connection(
        base_url="https://self-signed.badssl.com/anything",
        model_name="tls-model",
        verify_ssl=True,
    )

    conn, client = _client_for(cid)
    assert conn.verify_ssl == 1
    with pytest.raises(Exception) as refused:
        client.chat.completions.create(
            model="tls-model", messages=[{"role": "user", "content": "ping"}], max_tokens=5
        )
    # Verification on: the TLS handshake never completes, so there is no HTTP
    # response at all (the SDK reports a connection error).
    assert "connection error" in str(refused.value).lower()
    assert "404" not in str(refused.value)

    # Flip the operator toggle through the live API and retry: the TLS
    # handshake now succeeds, so the failure is a plain HTTP 404 instead.
    listed = admin.get(f"{BASE_URL}/admin/config/llm/api/list", timeout=20).json()["data"]
    row = next(item for item in listed if item["id"] == cid)
    admin.save({**row, "verify_ssl": False})

    conn, client = _client_for(cid)
    assert conn.verify_ssl == 0
    with pytest.raises(Exception) as reached:
        client.chat.completions.create(
            model="tls-model", messages=[{"role": "user", "content": "ping"}], max_tokens=5
        )
    message = str(reached.value)
    assert "404" in message or "Not Found" in message


def test_real_provider_accepts_the_operators_model_parameters(admin, make_connection):
    """The user's example parameters are accepted by the real model endpoint."""
    base_url, api_key, model = _real_provider()
    cid = make_connection(
        base_url=base_url,
        api_key=api_key,
        model_name=model,
        extra_body=REAL_EXTRA_BODY,
        http_headers={"X-Tenant": "live-params"},
    )

    conn, client = _client_for(cid)
    assert conn.extra_body == REAL_EXTRA_BODY
    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": "Reply with exactly: PARAMS OK"}],
        extra_body=conn.extra_body,
    )
    assert "PARAMS OK" in (response.choices[0].message.content or "")

    # The admin Test button issues the same real call through the live app.
    r = admin.post(f"{BASE_URL}/admin/config/llm/api/test", json={"id": cid}, timeout=120)
    assert r.status_code == 200
    body = r.json()
    assert body.get("status") == "success", r.text[:400]
    assert body.get("reply"), "a real provider must answer the test ping with text"
    assert body.get("endpoint") == base_url
    assert body.get("model") == model


def _closed_port() -> int:
    """A local port nothing listens on: the connection is refused for real."""
    import socket

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def test_admin_test_names_an_unreachable_endpoint(admin, make_connection):
    """The Test button reports the real transport failure, not "Connection error."."""
    port = _closed_port()
    base_url = f"http://127.0.0.1:{port}/v1"
    cid = make_connection(base_url=base_url, api_key="sk-live", model_name="llama3.2:3b")

    body = admin.post(
        f"{BASE_URL}/admin/config/llm/api/test", json={"id": cid}, timeout=60
    ).json()

    assert body["status"] == "error", body
    assert body["endpoint"] == base_url
    assert body["model"] == "llama3.2:3b"
    assert body["message"] != "Connection error."
    assert base_url in body["message"]
    assert "refused" in body["message"].lower()


def test_admin_test_rejects_a_200_that_is_not_a_chat_completion(admin, make_connection):
    """An endpoint that answers HTTP 200 with something else is a failed test."""
    cid = make_connection(
        base_url="https://httpbin.org/anything", api_key="sk-live", model_name="echo-model"
    )

    body = admin.post(
        f"{BASE_URL}/admin/config/llm/api/test", json={"id": cid}, timeout=120
    ).json()

    assert body["status"] == "error", body
    assert "without a chat completion" in body["message"]
    # httpbin echoes the request headers back, so the API key travels in the
    # body: the reported reason must mask it instead of publishing the secret.
    assert "sk-live" not in body["message"]
    assert "********" in body["message"]


def test_admin_test_reports_the_real_providers_error_for_a_wrong_model(admin, make_connection):
    """A real provider's own error text reaches the LLM Manager verbatim."""
    base_url, api_key, _ = _real_provider()
    model = f"no-such-model-{uuid.uuid4().hex[:8]}"
    cid = make_connection(base_url=base_url, api_key=api_key, model_name=model)

    body = admin.post(
        f"{BASE_URL}/admin/config/llm/api/test", json={"id": cid}, timeout=120
    ).json()

    assert body["status"] == "error", body
    assert body["endpoint"] == base_url
    assert "returned HTTP" in body["message"]
    assert model in body["message"]


def test_live_agent_run_applies_connection_model_parameters(api, admin, make_connection):
    """An agent run through the real provider keeps working with parameters set."""
    base_url, api_key, model = _real_provider()
    cid = make_connection(
        base_url=base_url,
        api_key=api_key,
        model_name=model,
        extra_body={**REAL_EXTRA_BODY, "max_tokens": 500},
        http_headers={"X-Tenant": "live-agent-params"},
    )

    agent = api.create_agent(
        "Live Params Agent",
        {
            "kind": "agent",
            "runtime": "agent",
            "instructions": "Answer in one short sentence. Do not use tools.",
            "model": {"client": str(cid)},
        },
    )
    slug = agent.get("slug") or agent.get("id")
    result = api.invoke(slug, "Reply with exactly: AGENT PARAMS OK", model={"client": str(cid)})

    assert result["status"] == "success", result.get("run", {}).get("error") or result.get("reply")
    reply = (result.get("reply") or "").upper()
    assert "AGENT PARAMS OK" in reply, reply[:400]


def test_agent_run_still_works_without_model_parameters(api):
    """Baseline: the default connection keeps driving agents after the change."""
    agent = api.create_agent(
        "Live Default Agent",
        {
            "kind": "agent",
            "runtime": "agent",
            "instructions": "Answer in one short sentence. Do not use tools.",
            "model": {"client": "default"},
        },
    )
    slug = agent.get("slug") or agent.get("id")
    result = api.invoke(slug, "Reply with exactly: BASELINE OK")

    assert result["status"] == "success", result.get("run", {}).get("error") or result.get("reply")
    assert "BASELINE OK" in (result.get("reply") or "").upper()


def _wire_client(connection_id: int):
    """The LangChain client an agent gets for a connection, as stored right now."""
    from src.models.llm_connection import LLMConnection
    from src.utils import llm_connection_manager as mgr

    conn = LLMConnection.get_by_id(connection_id)
    assert conn is not None, f"connection {connection_id} disappeared"
    return conn, mgr.build_client(conn)


def test_agent_parameters_are_merged_over_the_connection_on_the_wire(admin, make_connection):
    """An agent's own generation parameters win key by key, and reach a real server."""
    cid = make_connection(
        extra_body={"temperature": 0.9, "max_tokens": 4321, "top_k": 20},
    )
    conn, base = _wire_client(cid)
    from src.utils import llm_connection_manager as mgr

    derived = mgr.apply_model_options(base, {"temperature": 0.05, "max_tokens": 77})
    assert derived.extra_body == {"temperature": 0.05, "max_tokens": 77, "top_k": 20}
    assert base.extra_body == {"temperature": 0.9, "max_tokens": 4321, "top_k": 20}, "base mutated"

    # The merged object is what the provider receives: httpbin echoes the body.
    raw = derived.root_client.chat.completions.with_raw_response.create(
        model=conn.model_name,
        messages=[{"role": "user", "content": "ping"}],
        extra_body=derived.extra_body,
    )
    body = json.loads(raw.text)["json"]
    assert body["temperature"] == 0.05
    assert body["max_tokens"] == 77
    assert body["top_k"] == 20


def test_agent_run_applies_its_own_output_cap(api, admin, make_connection):
    """The agent's default_options cap its own calls; the connection keeps its own."""
    base_url, api_key, model = _real_provider()
    cid = make_connection(
        base_url=base_url,
        api_key=api_key,
        model_name=model,
        extra_body={"temperature": 0.2, "max_tokens": 4000},
    )

    def _run(name: str, config: dict) -> str:
        agent = api.create_agent(name, config)
        slug = agent.get("slug") or agent.get("id")
        result = api.invoke(slug, "List the numbers 1 to 100 separated by commas, with no other text.")
        assert result["status"] == "success", result.get("run", {}).get("error") or result.get("reply")
        return (result.get("reply") or "").strip()

    capped = _run(
        "Live Capped Agent",
        {
            "kind": "agent",
            "runtime": "agent",
            "instructions": "Answer directly. Do not use tools.",
            "model": {"client": str(cid)},
            "default_options": {"temperature": 0.1, "max_tokens": 8},
        },
    )
    uncapped = _run(
        "Live Uncapped Agent",
        {
            "kind": "agent",
            "runtime": "agent",
            "instructions": "Answer directly. Do not use tools.",
            "model": {"client": str(cid)},
        },
    )

    # Control first: a terse control reply would make the comparison meaningless.
    assert len(uncapped) > 80, f"control reply was too short to compare against: {uncapped[:80]!r}"
    assert len(capped) < 80, f"the agent's 8-token cap did not reach the provider: {capped[:120]!r}"
    assert len(capped) < len(uncapped)


def test_pinned_connection_outranks_the_run_model(api, admin, make_connection):
    """An agent pinned to a connection runs on it even when the caller picks another."""
    base_url, api_key, model = _real_provider()
    pinned = make_connection(base_url=base_url, api_key=api_key, model_name=model)
    unreachable = make_connection(
        base_url=f"http://127.0.0.1:{_closed_port()}/v1", api_key="sk-live", model_name="nope"
    )

    agent = api.create_agent(
        "Live Pinned Model",
        {
            "kind": "agent",
            "runtime": "agent",
            "instructions": "Answer in one short sentence. Do not use tools.",
            "model": {"client": str(pinned)},
        },
    )
    slug = agent.get("slug") or agent.get("id")
    result = api.invoke(slug, "Reply with exactly: PINNED OK", model={"client": str(unreachable)})

    assert result["status"] == "success", result.get("run", {}).get("error") or result.get("reply")
    assert "PINNED OK" in (result.get("reply") or "").upper()
    stored = (result.get("run", {}).get("input_json") or {}).get("model") or {}
    assert str(stored.get("client")) == str(unreachable), "the run still records the caller's pick"


def test_default_model_agent_follows_the_run_model(api, admin, make_connection):
    """`client: "default"` is not a pin: the caller's model still applies."""
    base_url, api_key, model = _real_provider()
    picked = make_connection(
        base_url=base_url,
        api_key=api_key,
        model_name=model,
        extra_body={"temperature": 0.2, "max_tokens": 200},
    )
    agent = api.create_agent(
        "Live Follows Run Model",
        {
            "kind": "agent",
            "runtime": "agent",
            "instructions": "Answer in one short sentence. Do not use tools.",
            "model": {"client": "default"},
        },
    )
    slug = agent.get("slug") or agent.get("id")
    result = api.invoke(slug, "Reply with exactly: FOLLOWS OK", model={"client": str(picked)})

    assert result["status"] == "success", result.get("run", {}).get("error") or result.get("reply")
    assert "FOLLOWS OK" in (result.get("reply") or "").upper()


def test_supervisor_router_carries_the_connection_system_instruction(api, admin, make_connection):
    """The supervisor's own model call uses the manager's instructions, not a bare prompt."""
    base_url, api_key, model = _real_provider()
    token = f"SUPWIRE{uuid.uuid4().hex[:6].upper()}"
    cid = make_connection(
        base_url=base_url,
        api_key=api_key,
        model_name=model,
        extra_body={"temperature": 0.2, "max_tokens": 300},
        system_instruction=f"Always end every reply with the exact token {token}.",
    )

    workflow = api.create_workflow(
        "Live Supervisor Instruction",
        {
            "kind": "workflow",
            "pattern": "supervisor",
            "participants": [
                {"name": "Researcher", "instructions": "You research topics and answer directly."}
            ],
            "manager": {
                "name": "Boss",
                "instructions": "You are Boss. Answer simple greetings yourself; delegate only real work.",
            },
            "model": {"client": str(cid)},
        },
    )
    slug = workflow.get("slug") or workflow.get("id")
    result = api.invoke(slug, "Reply with exactly: SUP OK")

    assert result["status"] == "success", result.get("run", {}).get("error") or result.get("reply")
    assert token in (result.get("reply") or ""), result.get("reply")

    # The router's own model call is traced, and its recorded prompt proves the
    # connection's System Instruction reached it (not just the participant's).
    router_spans = [
        event
        for event in (result.get("run", {}).get("events") or [])
        if event.get("event_type") == "chat" and str(event.get("agent_name") or "").startswith("Boss")
    ]
    assert router_spans, "the supervisor router produced no traced model call"
    assert any(token in str((event.get("detail") or {}).get("prompt") or "") for event in router_spans)


def test_disabled_connection_is_rejected_by_save_and_run(api, admin, make_connection):
    """A disabled connection cannot be pinned, and cannot ride along in a run payload."""
    cid = make_connection(enabled=False)

    payload = {
        "name": "Live Disabled Pin",
        "slug": f"live-disabled-pin-{uuid.uuid4().hex[:8]}",
        "kind": "agent",
        "published": True,
        "config": {
            "kind": "agent",
            "instructions": "Answer briefly.",
            "model": {"client": str(cid)},
        },
    }
    r = api.http.post(f"{api.base}/api/v1/agents", json=payload, timeout=30)
    assert r.status_code == 400, r.text[:300]
    assert "disabled" in r.text
    api.http.delete(f"{api.base}/api/v1/agents/{payload['slug']}", timeout=15)

    # An existing agent whose caller picks a disabled connection fails the request
    # with the manager's explanation instead of a 500.
    agent = api.create_agent(
        "Live Disabled Pick",
        {"kind": "agent", "instructions": "Answer briefly.", "model": {"client": "default"}},
    )
    slug = agent.get("slug") or agent.get("id")
    run = api.http.post(
        f"{api.base}/api/v1/runs",
        json={"agent_id": slug, "input": "hello", "stream": True, "model": {"client": str(cid)}},
        timeout=(10, 30),
    )
    assert run.status_code == 400, run.text[:300]
    assert "disabled" in run.text


def test_unknown_connection_is_rejected_by_save_and_run(api):
    """A stale model pick is reported, never silently replaced by the default."""
    ghost = f"ghost-{uuid.uuid4().hex[:8]}"
    payload = {
        "name": "Live Ghost Pin",
        "slug": f"live-ghost-pin-{uuid.uuid4().hex[:8]}",
        "kind": "agent",
        "published": True,
        "config": {"kind": "agent", "instructions": "Answer briefly.", "model": {"client": ghost}},
    }
    r = api.http.post(f"{api.base}/api/v1/agents", json=payload, timeout=30)
    assert r.status_code == 400, r.text[:300]
    assert ghost in r.text
    api.http.delete(f"{api.base}/api/v1/agents/{payload['slug']}", timeout=15)

    agent = api.create_agent(
        "Live Ghost Pick",
        {"kind": "agent", "instructions": "Answer briefly.", "model": {"client": "default"}},
    )
    slug = agent.get("slug") or agent.get("id")
    run = api.http.post(
        f"{api.base}/api/v1/runs",
        json={"agent_id": slug, "input": "hello", "stream": True, "model": {"client": ghost}},
        timeout=(10, 30),
    )
    assert run.status_code == 400, run.text[:300]
    assert ghost in run.text
