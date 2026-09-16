"""What the sample feed registers, independent of how it is invoked.

``scripts/feed_samples.py`` is the operator-facing CLI; tests call these
functions directly. Everything is idempotent (upsert by name/slug), so running
the feed twice changes nothing, and running it against an existing database is
also how rows pick up moved module paths.
"""

from __future__ import annotations

import os
import secrets
import sys
from typing import Any

from src.agent_platform.paths import APP_ROOT, SAMPLES_DIR, pythonpath_env

MCP_MODULES = {
    "Workspace": "platform_samples.mcp_servers.workspace",
    "Text2SQL": "platform_samples.mcp_servers.text2sql",
    "Knowledge": "platform_samples.mcp_servers.knowledge",
}

MCP_DESCRIPTIONS = {
    "Workspace": "Agent workspace filesystem tools (list/read/search/write/command)",
    "Text2SQL": "Text-to-SQL database inspection and safe SQL query execution tools",
    "Knowledge": (
        "Knowledge base retrieval-augmented generation tools "
        "(search_knowledge, list_knowledge_documents, get_document_info, list_knowledge_tags)"
    ),
}

HTTP_NAME = "Text2SQL (HTTP)"
HTTP_DESCRIPTION = (
    "The Text2SQL tools served over streamable HTTP on this host, so remote and "
    "hosted agents reach them without a local process launch"
)
HTTP_DEFAULT_PORT = 8765

SKILLS = {
    "design-review": "Staff-architect design review",
    "backend-review": "Staff backend review",
    "implementation": "Principal implementer",
    "text2sql": "Expert Text-to-SQL data analyst",
}

#: Server modules that moved out of the framework package (row migration).
LEGACY_MODULES = {
    "src.agent_platform.seeds.workspace_mcp_server": MCP_MODULES["Workspace"],
    "src.agent_platform.seeds.text2sql_mcp_server": MCP_MODULES["Text2SQL"],
    "src.agent_platform.seeds.knowledge_mcp_server": MCP_MODULES["Knowledge"],
}

AGENT_SLUGS = (
    "text2sql",
    "developer",
    "deep-agent",
    "research-supervisor",
    "support-swarm",
    "review-graph",
)


def _stdio_config(module: str) -> dict[str, Any]:
    return {
        "command": sys.executable,
        "args": ["-m", module],
        "env": {"PYTHONPATH": pythonpath_env(), "APP_ROOT": str(APP_ROOT)},
    }


def feed_mcp_servers(*, http_port: int | None = None) -> list[str]:
    """Register (or refresh) the bundled MCP servers: three stdio, one HTTP."""
    from src.models.mcp_server import MCPServer, MCPServerType

    MCPServer.create_table()
    names: list[str] = []
    for name, module in MCP_MODULES.items():
        row = MCPServer.get_by_name(name)
        config = _stdio_config(module)
        if row is None:
            MCPServer(
                name=name,
                description=MCP_DESCRIPTIONS[name],
                server_type=MCPServerType.STDIO.value,
                config=config,
            ).save()
        else:
            row.description = MCP_DESCRIPTIONS[name]
            row.server_type = MCPServerType.STDIO.value
            row.config = config
            row.save()
        names.append(name)

    existing = MCPServer.get_by_name(HTTP_NAME)
    # An existing row keeps its url/port (an operator may have moved it) unless a
    # port is requested explicitly; the token is always preserved.
    port = http_port or os.environ.get("MCP_HTTP_PORT") or _existing_http_port(existing) or HTTP_DEFAULT_PORT
    port = int(port)
    token = _existing_http_token(existing) or os.environ.get("MCP_HTTP_TOKEN") or secrets.token_urlsafe(24)
    config = {
        "url": f"http://127.0.0.1:{port}/mcp",
        "headers": {"Authorization": f"Bearer {token}"},
        # `service` tells scripts/mcp_http_service.py what to launch. The app
        # itself never starts anything; it only connects to the URL.
        "service": {"module": MCP_MODULES["Text2SQL"], "host": "127.0.0.1", "port": port},
    }
    if existing is None:
        MCPServer(
            name=HTTP_NAME,
            description=HTTP_DESCRIPTION,
            server_type=MCPServerType.HTTP.value,
            config=config,
        ).save()
    else:
        existing.description = HTTP_DESCRIPTION
        existing.server_type = MCPServerType.HTTP.value
        existing.config = config
        existing.save()
    names.append(HTTP_NAME)
    return names


def _existing_http_token(server: Any) -> str:
    """Keep the stored bearer token so a reseed never invalidates agents."""
    if server is None:
        return ""
    header = str(((server.config or {}).get("headers") or {}).get("Authorization") or "")
    return header.removeprefix("Bearer ").strip()


def _existing_http_port(server: Any) -> int | None:
    """The port an existing HTTP row is served on, so a feed does not move it."""
    from urllib.parse import urlparse

    if server is None:
        return None
    config = getattr(server, "config", None) or {}
    service = config.get("service") if isinstance(config.get("service"), dict) else {}
    for candidate in (service.get("port"), urlparse(str(config.get("url") or "")).port):
        try:
            if candidate:
                return int(candidate)
        except (TypeError, ValueError):
            continue
    return None


def migrate_mcp_module_paths() -> list[str]:
    """Repoint rows that reference the retired ``src.agent_platform.seeds`` servers.

    The bundled tool servers moved to ``platform_samples.mcp_servers``; rows an
    operator created before the move (or duplicated from a bundled one) would
    otherwise stay permanently unreachable.
    """
    from src.models.mcp_server import MCPServer

    moved: list[str] = []
    for server in MCPServer.get_all():
        config = dict(getattr(server, "config", None) or {})
        args = list(config.get("args") or [])
        if not args:
            continue
        replaced = [LEGACY_MODULES.get(str(arg), str(arg)) for arg in args]
        if replaced == [str(arg) for arg in args]:
            continue
        config["args"] = replaced
        server.config = config
        server.save()
        moved.append(server.name)
    return moved


def feed_skills() -> list[str]:
    """Register the bundled skill packages (content lives in platform_samples)."""
    from src.agent_platform.catalog.skills_store import MafSkillStore

    MafSkillStore.ensure_tables()
    registered: list[str] = []
    for slug, description in SKILLS.items():
        path = SAMPLES_DIR / "skills" / slug
        if not (path / "SKILL.md").is_file():
            continue
        MafSkillStore.upsert(slug, description, str(path), enabled=True)
        registered.append(slug)
    return registered


def feed_agents(*, force: bool = False) -> list[str]:
    """Register the sample agents; returns the slugs written (kept ones omitted)."""
    from platform_samples.agents import seed_definitions

    return seed_definitions(force=force)


def feed_evals(*, force: bool = False) -> str:
    from platform_samples.evals import seed_eval_definitions

    seed_eval_definitions(force=force)
    return "eval definitions for sample agents"


def feed_scripted(*, force: bool = False) -> str:
    from platform_samples.evals import seed_eval_definitions_e2e
    from platform_samples.scripted import seed_scripted_definitions

    seed_scripted_definitions(force=force)
    seed_eval_definitions_e2e(force=force)
    return "scripted e2e agents + evals"


def feed_legacy() -> str:
    from platform_samples.legacy_import import import_legacy_definitions

    return f"imported {import_legacy_definitions()} legacy definition(s)"


def feed_demo_data() -> str:
    from platform_samples.demo_dataset import ensure_demo_dataset
    from src.utils.database import DATABASE_URI

    if not DATABASE_URI.startswith("sqlite:///"):
        return "skipped (demo dataset needs a sqlite database)"
    db_path = DATABASE_URI[len("sqlite:///"):] or "text2sql.db"
    inserted = ensure_demo_dataset(db_path)
    added = {name: n for name, n in inserted.items() if n}
    return f"demo business tables ready{f' (inserted {added})' if added else ' (already populated)'}"
