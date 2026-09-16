"""Eval endpoints: definitions, run, results. Studio-gated, agents:write scope."""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from src.agent_platform.api.auth import api_auth_required, current_user_id
from src.agent_platform.catalog.store import DefinitionStore
from src.agent_platform.eval.runner import normalize_items, run_evaluation
from src.agent_platform.eval.store import EvalStore


def _ser_definition(eval_def: dict) -> dict:
    return {
        "id": eval_def.get("id"),
        "definition_id": eval_def.get("definition_id"),
        "name": eval_def.get("name"),
        "items": eval_def.get("items") or [],
        "model": eval_def.get("model"),
        "created_at": eval_def.get("created_at"),
    }


def _ser_result(row: dict) -> dict:
    checks = (row.get("results") or {}).get("checks") if isinstance(row.get("results"), dict) else None
    return {
        "id": row.get("id"),
        "item_index": row.get("item_index"),
        "passed": bool(row.get("pass")),
        "score": row.get("score"),
        "status": row.get("status"),
        "output": row.get("output"),
        "error": row.get("error"),
        "checks": checks or [],
        "run_id": row.get("run_id"),
        "run_public_id": row.get("run_public_id"),
        "trace_id": row.get("trace_id"),
        "created_at": row.get("created_at"),
    }


def _require_studio_or_403():
    from src.agent_platform.api.api_helpers import can_studio
    if can_studio():
        return None
    return jsonify({"error": "forbidden", "message": "Agent Studio requires admin or agent_studio access"}), 403


def _resolve_eval_agent(agent_id: str):
    """Evals compile and ainvoke a single agent graph.

    They apply to catalog kind=agent (runtime agent *or* harness), not workflows.
    """
    row = DefinitionStore.resolve(agent_id, published_only=False)
    if row is None:
        return None, (jsonify({"error": "agent not found"}), 404)
    kind = str(row.get("kind") or "agent").lower()
    if kind != "agent":
        return None, (jsonify({"error": "evals are only supported on agent definitions"}), 400)
    return row, None


def create_eval_blueprint() -> Blueprint:
    bp = Blueprint("agent_evals_v1", __name__, url_prefix="/api/v1")
    return _register_routes(bp)


def register_eval_routes(bp: Blueprint) -> None:
    """Register eval routes on an already-prefixed /api/v1 blueprint."""
    _register_routes(bp)


def _register_routes(bp: Blueprint) -> Blueprint:

    @bp.get("/agents/<agent_id>/evals")
    @api_auth_required("agents:read")
    def list_evals(agent_id: str):
        denied = _require_studio_or_403()
        if denied:
            return denied
        row, err = _resolve_eval_agent(agent_id)
        if err:
            return err
        definitions = EvalStore.list_definitions(int(row["id"]))
        return jsonify({"evals": [_ser_definition(d) for d in definitions]})

    @bp.post("/agents/<agent_id>/evals")
    @api_auth_required("agents:write")
    def create_eval(agent_id: str):
        denied = _require_studio_or_403()
        if denied:
            return denied
        row, err = _resolve_eval_agent(agent_id)
        if err:
            return err
        data = request.get_json(silent=True) or {}
        name = str(data.get("name") or "").strip() or "Eval"
        try:
            items = normalize_items(data.get("items"))
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        model = data.get("model")
        if model is not None and not isinstance(model, dict):
            return jsonify({"error": "model must be an object"}), 400
        created = EvalStore.create_definition(
            int(row["id"]), name, items, created_by=current_user_id(), model=model
        )
        return jsonify(_ser_definition(created)), 201

    @bp.delete("/agents/<agent_id>/evals/<int:eval_id>")
    @api_auth_required("agents:write")
    def delete_eval(agent_id: str, eval_id: int):
        denied = _require_studio_or_403()
        if denied:
            return denied
        row, err = _resolve_eval_agent(agent_id)
        if err:
            return err
        existing = EvalStore.get_definition(eval_id)
        if existing is None or int(existing["definition_id"]) != int(row["id"]):
            return jsonify({"error": "eval definition not found"}), 404
        EvalStore.delete_definition(eval_id)
        return jsonify({"ok": True})

    @bp.post("/agents/<agent_id>/evals/<int:eval_id>/run")
    @api_auth_required("agents:write")
    def run_eval(agent_id: str, eval_id: int):
        denied = _require_studio_or_403()
        if denied:
            return denied
        row, err = _resolve_eval_agent(agent_id)
        if err:
            return err
        existing = EvalStore.get_definition(eval_id)
        if existing is None or int(existing["definition_id"]) != int(row["id"]):
            return jsonify({"error": "eval definition not found"}), 404
        try:
            outcome = run_evaluation(row, existing, user_id=current_user_id())
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception as exc:
            return jsonify({"error": f"eval run failed: {exc}"}), 500
        return jsonify(outcome)

    @bp.get("/agents/<agent_id>/evals/<int:eval_id>/results")
    @api_auth_required("agents:read")
    def eval_results(agent_id: str, eval_id: int):
        denied = _require_studio_or_403()
        if denied:
            return denied
        row, err = _resolve_eval_agent(agent_id)
        if err:
            return err
        existing = EvalStore.get_definition(eval_id)
        if existing is None or int(existing["definition_id"]) != int(row["id"]):
            return jsonify({"error": "eval definition not found"}), 404
        rows = EvalStore.list_results(eval_id)
        passed = sum(1 for r in rows if bool(r.get("pass")))
        return jsonify(
            {
                "eval": _ser_definition(existing),
                "results": [_ser_result(r) for r in rows],
                "summary": {
                    "passed": passed,
                    "failed": len(rows) - passed,
                    "total": len(rows),
                },
            }
        )

    return bp
