"""Sample eval definitions for the bundled database agents.

Registered by ``scripts/feed_samples.py`` after the sample MCP servers and
agents exist; every step no-ops when its agent is absent.
"""

from __future__ import annotations

import logging

from src.agent_platform.catalog.store import DefinitionStore
from src.agent_platform.eval.store import EvalStore

logger = logging.getLogger("text2sql.agent_platform")

# Keys: agent slug → (eval name, items). Items: prompt + keyword and/or
# expected_tool_calls (name/arguments checked by tool_call_args_match).
SAFE_ITEMS = [
    {
        "prompt": "List the files in the workspace root.",
        "expected_tool_calls": [{"name": "list_dir"}],
    },
    {
        "prompt": "Report whether the sample-service directory exists.",
        "keyword": "sample-service",
    },
    {
        "prompt": "Read the app.py entrypoint header.",
        "expected_tool_calls": [{"name": "read_file", "arguments": {"path": "sample-service/app.py"}}],
    },
]

def _upsert_definition(
    definition_id: int,
    name: str,
    items: list[dict],
    *,
    force: bool,
    model: dict | None = None,
) -> None:
    """Create the sample eval, or refresh it in place under ``force``.

    Refresh updates the definition row instead of deleting it: recorded results
    reference the eval id, and a sample refresh must not discard run history.
    """
    existing = EvalStore.list_definitions(definition_id)
    if not existing:
        EvalStore.create_definition(definition_id, name, items, model=model)
        return
    if not force:
        return
    for row in existing:
        EvalStore.update_definition(int(row["id"]), name=name, items=items, model=model)


def seed_eval_definitions(*, force: bool = False) -> None:
    """Create eval definitions for the sample agents that bind the Workspace MCP."""
    DefinitionStore.ensure_tables()
    EvalStore.ensure_tables()

    for slug, name, items in (
        ("developer", "Developer workspace evals", SAFE_ITEMS),
    ):
        row = DefinitionStore.get_by_slug(slug)
        if row is None:
            continue
        definition_id = int(row["id"])
        # Only seed when the definition actually binds the Workspace server.
        bindings = (row.get("config") or {}).get("mcp_bindings") or []
        if bindings and not any(
            str(b.get("server") or b.get("server_name") or "") == "Workspace" for b in bindings
        ):
            continue
        _upsert_definition(definition_id, name, items, force=force)


def seed_eval_definitions_e2e(*, force: bool = False) -> None:
    """Deterministic eval definitions for scripted E2E agents (no live LLM)."""
    EvalStore.ensure_tables()
    for slug, name, items, model in (
        (
            "echo",
            "Echo keyword evals",
            [{"prompt": "say pong", "keyword": "pong"}],
            {"client": "scripted", "responses": ["pong from echo"]},
        ),
        (
            "studio-scripted",
            "Studio scripted evals",
            [
                {"prompt": "review sample-service", "keyword": "Findings"},
                {
                    "prompt": "review sample-service",
                    "expected_tool_calls": [{"name": "load_skill", "arguments": {"skill_name": "design-review"}}],
                },
            ],
            {
                "client": "scripted",
                "responses": [
                    {
                        "type": "function_call",
                        "call_id": "skill_1",
                        "name": "load_skill",
                        "arguments": {"skill_name": "design-review"},
                    },
                    {"text": "Findings: reviewed via skill."},
                ],
            },
        ),
    ):
        row = DefinitionStore.get_by_slug(slug)
        if row is None:
            continue
        definition_id = int(row["id"])
        _upsert_definition(definition_id, name, items, force=force, model=model)
