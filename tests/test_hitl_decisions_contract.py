"""PLUMBING — the app's one resume contract.

``HumanInTheLoopMiddleware`` interrupts with
``{"action_requests": [...], "review_configs": [...]}`` and resumes with
``{"decisions": [...]}``. These tests pin the single normalizer and the
``/runs/<id>/approvals`` route's refusal of anything else; agent behavior with a
real model lives in the live suites.
"""

from __future__ import annotations

import pytest

from src.agent_platform.execution.api_keys import ApiKeyStore
from src.agent_platform.execution.run_store import RunStore
from src.agent_platform.runtime.hitl import (
    HitlPayloadError,
    normalize_decisions,
    pending_action_requests,
)

_PENDING = {
    "action_requests": [
        {"name": "dangerous_write", "args": {"path": "out.txt"}, "description": "write out.txt"}
    ],
    "review_configs": [{"action_name": "dangerous_write", "allowed_decisions": ["approve", "reject"]}],
    "agent": "Writer",
}


def test_normalize_decisions_keeps_only_the_library_fields():
    assert normalize_decisions({"decisions": [{"type": "approve"}]}) == {"decisions": [{"type": "approve"}]}
    assert normalize_decisions({"decisions": [{"type": "reject", "message": "no"}]}) == {
        "decisions": [{"type": "reject", "message": "no"}]
    }
    assert normalize_decisions({"decisions": [{"type": "reject"}]}) == {"decisions": [{"type": "reject"}]}
    assert normalize_decisions(
        {"decisions": [{"type": "edit", "edited_action": {"name": "dangerous_write", "args": {"path": "b"}}}]}
    ) == {"decisions": [{"type": "edit", "edited_action": {"name": "dangerous_write", "args": {"path": "b"}}}]}
    assert normalize_decisions({"decisions": [{"type": "respond", "message": "42"}]}) == {
        "decisions": [{"type": "respond", "message": "42"}]
    }


@pytest.mark.parametrize(
    "payload",
    [
        {"approved": True},
        {"responses": [{"approved": True}]},
        {"decisions": []},
        {"decisions": [{"approved": True}]},
        {"decisions": [{"type": "maybe"}]},
        {"decisions": [{"type": "edit"}]},
        {"decisions": [{"type": "edit", "edited_action": {"name": "x", "args": "nope"}}]},
        {"decisions": "approve"},
        {},
    ],
)
def test_normalize_decisions_rejects_everything_else(payload):
    with pytest.raises(HitlPayloadError):
        normalize_decisions(payload)


def test_pending_action_requests_counts_the_pending_interrupt():
    assert len(pending_action_requests(_PENDING)) == 1
    assert pending_action_requests(None) == []
    assert pending_action_requests({"type": "approval_request"}) == []


def _app_client():
    from flask import Flask

    from src.agent_platform.api.blueprint import create_blueprint

    RunStore.ensure_tables()
    ApiKeyStore.ensure_tables()
    app = Flask(__name__)
    app.secret_key = "test"
    app.register_blueprint(create_blueprint())
    return app.test_client()


def _awaiting_run():
    run = RunStore.create(task="write", definition_id=None, user_id=1, agent_slug="writer")
    RunStore.set_pending(int(run["id"]), _PENDING)
    key = ApiKeyStore.create(user_id=1, name="test", scopes=["runs:write", "runs:read"])
    return str(run["public_id"]), {"X-API-Key": key["key"]}


def test_approvals_route_rejects_the_old_body(temp_db):
    http = _app_client()
    run_id, headers = _awaiting_run()
    resp = http.post(f"/api/v1/runs/{run_id}/approvals", json={"approved": True, "stream": False}, headers=headers)
    assert resp.status_code == 400, resp.get_data(as_text=True)
    body = resp.get_json()
    assert body["error"] == "bad_decision"
    assert "decisions" in body["message"]
    # The interrupt is still pending: a rejected body must not consume it.
    assert RunStore.get(run_id)["status"] == "awaiting_approval"


def test_approvals_route_rejects_a_decision_count_mismatch(temp_db):
    http = _app_client()
    run_id, headers = _awaiting_run()
    resp = http.post(
        f"/api/v1/runs/{run_id}/approvals",
        json={"decisions": [{"type": "approve"}, {"type": "approve"}], "stream": False},
        headers=headers,
    )
    assert resp.status_code == 400, resp.get_data(as_text=True)
    body = resp.get_json()
    assert body["error"] == "bad_decision"
    assert "1 decision(s)" in body["message"]


def test_approvals_route_rejects_unknown_fields(temp_db):
    http = _app_client()
    run_id, headers = _awaiting_run()
    resp = http.post(
        f"/api/v1/runs/{run_id}/approvals",
        json={"decisions": [{"type": "approve"}], "feedback": "go"},
        headers=headers,
    )
    assert resp.status_code == 400, resp.get_data(as_text=True)
    assert resp.get_json()["error"] == "bad_decision"
