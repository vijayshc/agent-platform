"""Best-effort import of agent_teams / agent_workflows into agent_definitions."""

from __future__ import annotations

import logging
import re

from src.agent_platform.catalog.store import DefinitionStore

logger = logging.getLogger("text2sql.agent_platform")


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")
    return slug or "imported-agent"


def import_legacy_definitions() -> int:
    imported = 0
    try:
        from src.models.agent_team import AgentTeam
        from src.models.agent_workflow import AgentWorkflow
    except Exception:
        return 0

    try:
        AgentTeam.create_table()
        AgentWorkflow.create_table()
    except Exception:
        return 0

    try:
        teams = AgentTeam.get_all()
    except Exception as exc:
        logger.warning("legacy team import skipped: %s", exc)
        return 0

    for team in teams:
        slug = _slugify(team.name or f"team-{team.id}")
        if DefinitionStore.get_by_slug(slug):
            continue
        agents = (team.config or {}).get("agents") or []
        settings = (team.config or {}).get("settings") or {}
        if len(agents) <= 1:
            spec = agents[0] if agents else {}
            config = {
                "kind": "agent",
                "runtime": "agent",
                "instructions": spec.get("system_prompt") or f"You are {team.name}.",
                "description": team.description or "",
                "mcp_bindings": _tools_to_bindings(spec.get("tools") or []),
                "model": {"client": "default"},
            }
            kind = "agent"
        else:
            mode = settings.get("execution_mode") or "graph"
            pattern = "supervisor" if mode == "selector" else "graph"
            config = {
                "kind": "workflow",
                "pattern": pattern,
                "description": team.description or "",
                "model": {"client": "default"},
                "participants": [
                    {
                        "name": a.get("name") or a.get("role") or "Agent",
                        "instructions": a.get("system_prompt") or "",
                        "description": a.get("description") or "",
                        "mcp_bindings": _tools_to_bindings(a.get("tools") or []),
                    }
                    for a in agents
                ],
            }
            if pattern == "supervisor":
                config["manager"] = {
                    "name": "Supervisor",
                    "instructions": f"You coordinate the {team.name} specialists.",
                }
            kind = "workflow"
        DefinitionStore.upsert(
            slug=slug,
            name=team.name or slug,
            kind=kind,
            config=config,
            published=False,
        )
        imported += 1

    try:
        workflows = AgentWorkflow.get_all()
    except Exception:
        workflows = []
    for wf in workflows:
        slug = _slugify(wf.name or f"workflow-{wf.id}")
        if DefinitionStore.get_by_slug(slug):
            continue
        graph = wf.graph or {}
        config = {
            "kind": "workflow",
            "pattern": "graph",
            "description": wf.description or "",
            "nodes": graph.get("nodes") or [],
            "edges": graph.get("edges") or [],
            "entry": (graph.get("config") or {}).get("entry_point"),
            "model": {"client": "default"},
        }
        DefinitionStore.upsert(
            slug=slug,
            name=wf.name or slug,
            kind="workflow",
            config=config,
            published=False,
        )
        imported += 1
    logger.info("Imported %s legacy agent definitions", imported)
    return imported


def _tools_to_bindings(tools: list) -> list[dict]:
    by_server: dict[int, list[str]] = {}
    for tool in tools or []:
        sid = tool.get("server_id")
        name = tool.get("tool_name")
        if sid and name:
            by_server.setdefault(int(sid), []).append(name)
    return [{"server_id": sid, "tools": names} for sid, names in by_server.items()]
