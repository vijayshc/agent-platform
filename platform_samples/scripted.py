"""Deterministic published agents for UI e2e (scripted client, no live LLM)."""

from __future__ import annotations

from src.agent_platform.catalog.store import DefinitionStore


def _admin_user_id() -> int:
    try:
        from src.utils.user_manager import UserManager

        user = UserManager().get_user_by_username("admin")
        if user is None:
            return 1
        return int(getattr(user, "id", None) or user["id"])
    except Exception:
        return 1


def seed_scripted_definitions(*, force: bool = False) -> None:
    """Register the deterministic UI-e2e agents (existing ones kept unless forced)."""
    DefinitionStore.ensure_tables()

    def upsert(**kwargs) -> None:
        if not force and DefinitionStore.get_by_slug(kwargs["slug"]) is not None:
            return
        DefinitionStore.upsert(**kwargs)

    upsert(
        slug="echo",
        name="Echo",
        kind="agent",
        config={
            "kind": "agent",
            "runtime": "agent",
            "description": "Replies with a short ack. Scripted test agent.",
            "instructions": "Reply with pong.",
            "model": {"client": "scripted", "responses": ["pong from echo"]},
        },
        published=True,
    )
    upsert(
        slug="slow-echo",
        name="Slow Echo",
        kind="agent",
        config={
            "kind": "agent",
            "runtime": "agent",
            "description": "Echo with a delay so the run stays live while Chat streams.",
            "instructions": "Reply with pong after a pause.",
            "model": {
                "client": "scripted",
                "delay_s": 5,
                "responses": ["pong from slow-echo"],
            },
        },
        published=True,
    )
    upsert(
        slug="writer",
        name="Writer",
        kind="agent",
        config={
            "kind": "agent",
            "runtime": "agent",
            "description": "Calls a write tool that requires HITL approval.",
            "instructions": "Call dangerous_write when asked to write.",
            "function_tools": ["dangerous_write"],
            "model": {
                "client": "scripted",
                "reuse_id": "writer-hitl-ui",
                "responses": [
                    {
                        "type": "function_call",
                        "call_id": "call_1",
                        "name": "dangerous_write",
                        "arguments": {"path": "x.txt", "content": "secret"},
                    },
                    "wrote it",
                ],
            },
        },
        published=True,
    )
    upsert(
        slug="studio-scripted",
        name="Studio Scripted",
        kind="agent",
        config={
            "kind": "agent",
            "runtime": "agent",
            "description": "Scripted studio: skill load, tool call, multi-agent speaker, findings.",
            "instructions": "Load the design-review skill, then report findings.",
            "maf_skill_ids": ["design-review"],
            "model": {
                "client": "scripted",
                "reuse_id": "studio-scripted-ui",
                "responses": [
                    {
                        "type": "function_call",
                        "call_id": "skill_1",
                        "name": "load_skill",
                        "arguments": {"skill_name": "design-review"},
                        "author": "Design Reviewer",
                    },
                    {
                        "text": "Findings: sample-service has design defects. Inspected via skill.",
                        "author": "Design Reviewer",
                    },
                ],
            },
        },
        published=True,
    )
    upsert(
        slug="hitl-writer",
        name="HITL Writer",
        kind="agent",
        config={
            "kind": "agent",
            "runtime": "agent",
            "description": "Unpublished draft: write tool requires HITL. Scripted, no reuse queue.",
            "instructions": "Call dangerous_write when asked to write.",
            "function_tools": ["dangerous_write"],
            "model": {
                "client": "scripted",
                "reuse_id": "hitl-writer-ui",
                "responses": [
                    {
                        "type": "function_call",
                        "call_id": "call_1",
                        "name": "dangerous_write",
                        "arguments": {"path": "x.txt", "content": "secret"},
                    },
                    "wrote it",
                ],
            },
        },
        published=False,
        created_by=_admin_user_id(),
    )
    upsert(
        slug="studio-supervisor",
        name="Studio Supervisor",
        kind="workflow",
        config={
            "kind": "workflow",
            "pattern": "supervisor",
            "description": "Supervisor studio. Scripted, no live LLM.",
            "model": {
                "client": "scripted",
                "reuse_id": "studio-supervisor-ui",
                "responses": [
                    "Assign the review to Design Reviewer.",
                    "Findings: reviewed by Design Reviewer.",
                ],
            },
            "manager": {"name": "Tech Lead", "instructions": "Delegate to Design Reviewer."},
            "participants": [
                {"name": "Design Reviewer", "instructions": "Review design."},
            ],
        },
        published=True,
    )
