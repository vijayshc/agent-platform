"""Deterministic chat client for tests and offline invoke."""

from __future__ import annotations

from typing import Any

from src.agent_platform.runtime.scripted_client import ScriptedChatClient

_SHARED: dict[str, ScriptedChatClient] = {}


class ScriptedModelPlugin:
    type_id = "scripted"
    kind = "model"
    label = "Scripted (tests)"
    icon = "flask"
    schema = {
        "type": "object",
        "properties": {
            "responses": {
                "type": "array",
                "items": {"type": "string"},
            }
        },
    }

    def compile(self, spec: dict[str, Any], ctx: Any) -> Any:
        if getattr(ctx, "client", None) is not None:
            return ctx.client
        delay_s = float(spec.get("delay_s") or 0)
        reuse_id = spec.get("reuse_id") or spec.get("queue_id")
        if reuse_id:
            key = str(reuse_id)
            run_id = getattr(ctx, "run_id", None)
            if run_id is not None:
                key = f"{key}:run:{run_id}"
            existing = _SHARED.get(key)
            if existing is None:
                existing = ScriptedChatClient(spec.get("responses") or ["ok"], delay_s=delay_s)
                _SHARED[key] = existing
            return existing
        return ScriptedChatClient(spec.get("responses") or ["ok"], delay_s=delay_s)


def reset_shared_clients() -> None:
    _SHARED.clear()
