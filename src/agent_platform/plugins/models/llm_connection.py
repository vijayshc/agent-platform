"""Dynamic model plugin backed by admin-configurable LLM connections.

The agent definition's ``model.client`` holds a connection identifier
("default", a connection id, or a connection name). Compilation is delegated to
the runtime model selector so the plugin and the direct compile path can never
disagree about precedence: a pinned connection wins over the run-level client,
which wins over the operator's default connection.
"""

from __future__ import annotations

from typing import Any


class LLMConnectionModelPlugin:
    type_id = "llm_connection"
    kind = "model"
    label = "LLM Manager"
    icon = "cloud"
    schema = {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Model name override"},
        },
    }

    def compile(self, spec: dict[str, Any], ctx: Any) -> Any:
        from src.agent_platform.runtime.model_select import compile_model_client

        return compile_model_client(
            {"client": spec.get("client"), "name": spec.get("name")},
            ctx,
        )
