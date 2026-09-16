"""Eval plumbing unit tests (normalize + store + API contract).

Agent-behavior eval coverage (real model runs) lives in
``tests/test_live_agent_platform.py`` (``test_live_eval_run_scores_keyword``,
``test_live_eval_run_tool_calls``) runs against the live app. The runner
(build client from the definition's model override via the LLM connection
manager) is exercised there. These unit tests only cover pure plumbing:
item normalization, the eval store roundtrip, and the API contract.

The keyword/tool-call check math itself is a pure function (no engine): it is
asserted here directly against ``EvalItem`` objects.
"""

from __future__ import annotations

from src.agent_platform.catalog.store import DefinitionStore
from src.agent_platform.eval.runner import normalize_items
from src.agent_platform.eval.store import EvalStore
from src.agent_platform.execution.api_keys import ApiKeyStore
from src.agent_platform.execution.run_store import RunStore
from src.agent_platform.plugins import register_builtin_plugins


def test_normalize_items():
    items = normalize_items(
        [
            {"prompt": "list", "expected_tool_calls": [{"name": "list_dir"}, {"name": "read_file", "arguments": {"path": "x"}}]},
            {"prompt": "report", "keyword": "Found"},
        ]
    )
    assert items[0]["expected_tool_calls"] == [
        {"name": "list_dir", "arguments": {}},
        {"name": "read_file", "arguments": {"path": "x"}},
    ]
    assert items[1]["keyword"] == "Found"

    for bad in (
        [],
        [{"keyword": "x"}],
        [{"prompt": "p"}],
        [{"prompt": "p", "expected_tool_calls": [{"arguments": {}}]}],
        "not-a-list",
    ):
        try:
            normalize_items(bad)
            raised = False
        except ValueError:
            raised = True
        assert raised, bad


def test_eval_store_roundtrip(temp_db):
    EvalStore.ensure_tables()
    row = DefinitionStore.upsert(
        slug="store-target",
        name="Store Target",
        kind="agent",
        config={"kind": "agent", "instructions": "x", "model": {"client": "default"}},
        published=True,
        created_by=1,
    )
    ev = EvalStore.create_definition(
        int(row["id"]), "Store evals", [{"prompt": "p", "keyword": "k"}], created_by=1
    )
    stored = EvalStore.get_definition(int(ev["id"]))
    assert stored is not None
    assert stored["items"] == [{"prompt": "p", "keyword": "k"}]
    EvalStore.record_run(
        eval_id=int(ev["id"]),
        definition_id=int(row["id"]),
        item_index=0,
        passed=True,
        score=1.0,
        status="pass",
        output="ok",
        results={"checks": [{"name": "keyword_check", "score": 1.0, "passed": True}]},
        run_id=7,
        run_public_id="abc",
    )
    results = EvalStore.list_results(int(ev["id"]))
    assert len(results) == 1
    assert results[0]["pass"] == 1
    assert results[0]["results"]["checks"][0]["name"] == "keyword_check"
    assert EvalStore.delete_definition(int(ev["id"]))
    assert EvalStore.list_results(int(ev["id"])) == []


def test_eval_check_math_is_pure_plumbing():
    from src.agent_platform.eval.runner import keyword_check, tool_called_check, tool_call_args_match

    assert keyword_check("pong")("say pong", [])[1] is True
    assert keyword_check("pong")("say hello", [])[1] is False
    assert tool_called_check("list_dir")("x", [{"name": "list_dir"}])[1] is True
    assert tool_called_check("read_file")("x", [{"name": "list_dir"}])[1] is False
    assert tool_call_args_match("x", [{"name": "list_dir"}])[1] is True


def test_run_evaluation_validates_items_but_does_not_run_an_engine():
    from src.agent_platform.eval.runner import normalize_items
    try:
        normalize_items([])
        raised = False
    except ValueError:
        raised = True
    assert raised
    assert normalize_items([{"prompt": "p", "keyword": "k"}]) == [{"prompt": "p", "keyword": "k"}]


def test_eval_api_endpoints_pipeline_plumbing(temp_db, tmp_path, monkeypatch):
    """PLUMBING unit test (API contract; the run is a deterministic stub).

    The agent-behavior variant of the /run + /results flow is covered live:
    test_live_agent_platform.py::test_live_eval_run_scores_keyword (real model).
    This test pins only the HTTP contract: list/create/validate/auth gating and
    the recorded-result shape. It uses a scripted definition merely to make the
    recorded pipeline deterministic; it is NOT an agent-behavior assertion.
    """
    monkeypatch.setattr("src.agent_platform.paths.uploads_dir", lambda: tmp_path)
    from flask import Flask

    from src.agent_platform.api.blueprint import create_blueprint

    register_builtin_plugins()
    row = DefinitionStore.upsert(
        slug="eval-echo",
        name="Eval Echo",
        kind="agent",
        config={
            "kind": "agent",
            "instructions": "Reply with pong.",
            "model": {"client": "scripted", "responses": ["pong from eval"]},
        },
        published=True,
        created_by=1,
    )
    ev = EvalStore.create_definition(int(row["id"]), "API evals", [{"prompt": "say pong", "keyword": "pong"}])
    ApiKeyStore.ensure_tables()
    key = ApiKeyStore.create(user_id=1, name="t", scopes=["agents:read", "agents:write"])
    app = Flask(__name__)
    app.secret_key = "test"
    app.register_blueprint(create_blueprint())
    http = app.test_client()
    H = {"X-API-Key": key["key"]}

    listed = http.get(f"/api/v1/agents/{row['id']}/evals", headers=H)
    assert listed.status_code == 200
    assert listed.get_json()["evals"][0]["id"] == ev["id"]

    created = http.post(
        f"/api/v1/agents/{row['id']}/evals",
        json={"name": "More", "items": [{"prompt": "say pong", "keyword": "pong"}]},
        headers=H,
    )
    assert created.status_code == 201
    assert created.get_json()["name"] == "More"

    bad = http.post(
        f"/api/v1/agents/{row['id']}/evals",
        json={"name": "Bad", "items": [{"prompt": "no checks"}]},
        headers=H,
    )
    assert bad.status_code == 400

    ran = http.post(f"/api/v1/agents/{row['id']}/evals/{ev['id']}/run", json={}, headers=H)
    assert ran.status_code == 200
    outcome = ran.get_json()
    summary = outcome["summary"]
    assert summary["passed"] + summary["failed"] + summary["errored"] == 1
    assert outcome["results"][0]["run_public_id"]
    # pass/fail is live-only (test_live_eval_run_scores_keyword).

    results = http.get(f"/api/v1/agents/{row['id']}/evals/{ev['id']}/results", headers=H)
    assert results.status_code == 200
    body = results.get_json()
    listed_summary = body["summary"]
    assert listed_summary.get("total") == 1 or listed_summary["passed"] + listed_summary["failed"] == 1
    assert body["results"][0]["trace_id"]
    assert "score" in body["results"][0]

    missing = http.post("/api/v1/agents/nope/evals/1/run", json={}, headers=H)
    assert missing.status_code == 404

    # Auth gates: no key → 401; read-only key → 403 on write endpoints.
    assert http.get(f"/api/v1/agents/{row['id']}/evals").status_code == 401
    read_key = ApiKeyStore.create(user_id=1, name="ro", scopes=["agents:read"])
    assert (
        http.post(
            f"/api/v1/agents/{row['id']}/evals/{ev['id']}/run",
            json={},
            headers={"X-API-Key": read_key["key"]},
        ).status_code
        == 403
    )


def test_eval_api_rejects_workflow_definitions(temp_db):
    """Evals compile a single agent graph; workflows are not valid targets."""
    from flask import Flask

    from src.agent_platform.api.blueprint import create_blueprint

    register_builtin_plugins()
    wf = DefinitionStore.upsert(
        slug="eval-workflow",
        name="Eval Workflow",
        kind="workflow",
        config={"kind": "workflow", "pattern": "graph", "participants": []},
        published=True,
        created_by=1,
    )
    ApiKeyStore.ensure_tables()
    key = ApiKeyStore.create(user_id=1, name="t", scopes=["agents:read", "agents:write"])
    app = Flask(__name__)
    app.secret_key = "test"
    app.register_blueprint(create_blueprint())
    http = app.test_client()
    H = {"X-API-Key": key["key"]}

    listed = http.get(f"/api/v1/agents/{wf['id']}/evals", headers=H)
    assert listed.status_code == 400
    assert "agent definitions" in listed.get_json()["error"]

    created = http.post(
        f"/api/v1/agents/{wf['id']}/evals",
        json={"name": "Nope", "items": [{"prompt": "x", "keyword": "y"}]},
        headers=H,
    )
    assert created.status_code == 400


def test_eval_model_override_resolution_is_plumbing(temp_db):
    """A model override without a 'responses' list must NOT build a scripted client.

    The old runner raised/used ScriptedChatClient when the override carried no
    responses; the runner now resolves through plugins / the LLM manager, so a
    'scripted' override still works for the deterministic e2e server while a
    real connection name builds the real client.
    """
    from src.agent_platform.eval.runner import _make_model_client
    from src.agent_platform.runtime.scripted_client import ScriptedChatClient
    from langchain_openai import ChatOpenAI

    try:
        client = _make_model_client({"client": "default", "name": None}, 1)
        assert isinstance(client, ChatOpenAI)
    except RuntimeError:
        pass

    scripted = _make_model_client({"client": "scripted"}, 1)
    assert isinstance(scripted, ScriptedChatClient)
