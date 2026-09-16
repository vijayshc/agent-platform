"""Validate Studio graphs and definition configs before save / publish / test-run."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from src.agent_platform.catalog.validate_flow import validate_capabilities
from src.agent_platform.paths import SKILLS_DIR, user_skills_dir

_SCRIPTED_CLIENT = "scripted"


def scripted_client_allowed() -> bool:
    """ScriptedChatClient is an E2E/test engine, not a production model."""
    return os.environ.get("AGENT_PLATFORM_E2E") == "1"


def definition_uses_scripted_client(definition: dict[str, Any] | None) -> bool:
    """True if any model.client (or studio modelClient) is scripted."""
    if not isinstance(definition, dict):
        return False
    config = definition.get("config") if isinstance(definition.get("config"), dict) else definition
    if not isinstance(config, dict):
        return False
    for spec in _iter_model_specs(config):
        client = spec.get("client") or spec.get("modelClient")
        if str(client or "").strip().lower() == _SCRIPTED_CLIENT:
            return True
    return False


def _iter_model_specs(config: dict[str, Any]) -> list[dict[str, Any]]:
    specs: list[dict[str, Any]] = []

    def take(obj: Any) -> None:
        if not isinstance(obj, dict):
            return
        model = obj.get("model")
        if isinstance(model, dict):
            specs.append(model)
        elif obj.get("client") or obj.get("modelClient"):
            specs.append(obj)

    take(config)
    for key in ("manager", "aggregator", "manager_agent"):
        take(config.get(key))
    for spec in list(config.get("participants") or []) + list(config.get("nodes") or []):
        take(spec)
    studio = config.get("studio") or {}
    if isinstance(studio, dict):
        for node in studio.get("nodes") or []:
            if not isinstance(node, dict):
                continue
            data = node.get("data") or {}
            if isinstance(data, dict):
                take(data)
    return specs


def validate_definition(definition: dict[str, Any]) -> dict[str, Any]:
    errors: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    config = dict(definition.get("config") or definition)
    kind = (definition.get("kind") or config.get("kind") or "agent").lower()

    _validate_scripted_client(definition, config, errors)
    _validate_model_connections(config, errors)
    _validate_default_options(config, errors, str(definition.get("name") or config.get("name") or "agent"))

    if kind == "agent":
        if not str(config.get("instructions") or "").strip():
            errors.append({"code": "missing_instructions", "message": "Agent instructions are required."})

    _validate_mcp(config, errors)
    _validate_skills(config, errors)
    _validate_harness_options(config, errors)
    _validate_hitl(config, errors)
    validate_capabilities(config, kind, errors, warnings)

    return {"ok": not errors, "errors": errors, "warnings": warnings}


def _validate_scripted_client(
    definition: dict[str, Any],
    config: dict[str, Any],
    errors: list[dict[str, str]],
) -> None:
    if scripted_client_allowed():
        return
    if definition_uses_scripted_client(definition) or definition_uses_scripted_client(config):
        errors.append(
            {
                "code": "scripted_client_not_allowed",
                "message": (
                    "model.client=scripted is not allowed in production. "
                    "Set AGENT_PLATFORM_E2E=1 to use the scripted test engine."
                ),
            }
        )


def _validate_model_connections(config: dict[str, Any], errors: list[dict[str, str]]) -> None:
    """Every pinned connection must exist and be enabled.

    "default" is not a pin: it follows whichever connection the operator marks
    as default, so it is validated at run time instead. A pin that points at a
    deleted or disabled connection would otherwise be reported only when the
    agent runs.
    """
    from src.utils.llm_connection_manager import DEFAULT_IDENTIFIERS, resolve

    checked: set[str] = set()
    for spec in _iter_model_specs(config):
        client = str(spec.get("client") or spec.get("modelClient") or "").strip()
        if not client or client.lower() in DEFAULT_IDENTIFIERS or client.lower() == _SCRIPTED_CLIENT:
            continue
        if client in checked:
            continue
        checked.add(client)
        try:
            resolve(client)
        except Exception as exc:  # noqa: BLE001 - reported verbatim to the author
            errors.append({"code": "bad_model_connection", "message": str(exc)})


_COMPACTION_STRATEGIES = {
    "context_window",
    "sliding_window",
    "token_budget",
    "summarization",
    "tool_result",
    "selective_tool_call",
}
_MEMORY_STORES = {"memory_file_store", "none"}
_INOPERABLE_MEMORY_STORES = {"mem0", "redis"}
_MODES = {"plan", "execute"}
_HISTORY_PROVIDERS = {"in_memory", "file"}


def _agent_configs(config: dict[str, Any]) -> list[dict[str, Any]]:
    """The config and every agent-shaped config nested inside it."""
    holders = [config]
    for key in ("manager", "aggregator", "manager_agent"):
        value = config.get(key)
        if isinstance(value, dict):
            holders.append(value)
    for spec in list(config.get("participants") or []) + list(config.get("nodes") or []):
        if isinstance(spec, dict):
            holders.append(spec)
    return holders


def _validate_default_options(
    config: dict[str, Any], errors: list[dict[str, str]], default_name: str = "agent"
) -> None:
    """Agent generation options must not take over app-owned request keys.

    A connection's Model Parameters already refuse ``model``/``messages``/
    ``stream``/``extra_body``; an agent's ``default_options`` are merged into the
    same request body, so they are held to the same contract.
    """
    from src.utils.llm_connection_manager import RESERVED_BODY_KEYS

    for holder in _agent_configs(config):
        options = holder.get("default_options")
        if not isinstance(options, dict):
            continue
        name = str(holder.get("name") or holder.get("id") or default_name)
        reserved = sorted(RESERVED_BODY_KEYS.intersection(options))
        if reserved:
            errors.append(
                {
                    "code": "bad_default_options",
                    "message": (
                        f"default_options for '{name}' cannot set "
                        + ", ".join(reserved)
                        + " — the app manages them per call."
                    ),
                }
            )
        max_tokens = options.get("max_tokens")
        if max_tokens is not None and (
            not isinstance(max_tokens, int) or isinstance(max_tokens, bool) or max_tokens <= 0
        ):
            # A non-positive cap is forwarded verbatim, so the provider would
            # reject every call for this agent; refuse it at authoring time.
            errors.append(
                {
                    "code": "bad_default_options",
                    "message": f"default_options for '{name}' must use a positive integer max_tokens.",
                }
            )


def _validate_harness_options(config: dict[str, Any], errors: list[dict[str, str]]) -> None:
    """Validate harness capability fields (enums, token bounds)."""
    runtime = str(config.get("runtime") or "agent").lower()
    # The studio writes camelCase ("Context window") and the runtime prefers it;
    # the snake_case API spelling is accepted too. Every spelling that is
    # present must be a valid bound, and the output cap is compared against the
    # one the runtime actually applies (camelCase first, matching
    # ``harness_config.context_window_tokens``).
    context_keys = [
        key
        for key in ("maxContextWindowTokens", "max_context_window_tokens")
        if config.get(key) is not None
    ]
    for key in context_keys:
        value = config[key]
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            errors.append(
                {
                    "code": "bad_max_context_window_tokens",
                    "message": f"{key} must be a positive integer.",
                }
            )
    effective_context_key = context_keys[0] if context_keys else "maxContextWindowTokens"
    max_context = config.get(effective_context_key)
    max_output = config.get("max_output_tokens")
    if max_output is not None:
        if not isinstance(max_output, int) or isinstance(max_output, bool) or max_output <= 0:
            errors.append(
                {
                    "code": "bad_max_output_tokens",
                    "message": "max_output_tokens must be a positive integer.",
                }
            )
    if (
        isinstance(max_context, int)
        and not isinstance(max_context, bool)
        and isinstance(max_output, int)
        and not isinstance(max_output, bool)
        and 0 < max_context <= max_output
    ):
        errors.append(
            {
                "code": "bad_token_limits",
                "message": f"max_output_tokens must be less than {effective_context_key}.",
            }
        )

    for key, allowed, label in (
        ("compaction_strategy", _COMPACTION_STRATEGIES, "compaction_strategy"),
        ("mode", _MODES, "mode"),
        ("history_provider", _HISTORY_PROVIDERS, "history_provider"),
    ):
        value = config.get(key)
        if value is None:
            continue
        if not isinstance(value, str) or value.lower() not in allowed:
            errors.append(
                {
                    "code": f"bad_{key}",
                    "message": f"{label} must be one of: {', '.join(sorted(allowed))}.",
                }
            )

    memory_store = config.get("memory_store")
    if memory_store is not None:
        kind = memory_store.lower() if isinstance(memory_store, str) else ""
        if kind in _INOPERABLE_MEMORY_STORES:
            errors.append(
                {
                    "code": "bad_memory_store",
                    "message": (
                        f"memory_store {kind!r} is not operable: connection credentials "
                        "are not configured. Use memory_file_store or none."
                    ),
                }
            )
        elif kind not in _MEMORY_STORES:
            errors.append(
                {
                    "code": "bad_memory_store",
                    "message": f"memory_store must be one of: {', '.join(sorted(_MEMORY_STORES))}.",
                }
            )

    if runtime != "harness" and config.get("mode"):
        errors.append(
            {
                "code": "mode_requires_harness",
                "message": "mode is only supported on runtime: harness agents.",
            }
        )


def _validate_mcp(config: dict[str, Any], errors: list[dict[str, str]]) -> None:
    bindings = list(config.get("mcp_bindings") or [])
    for spec in config.get("participants") or []:
        if isinstance(spec, dict):
            bindings.extend(spec.get("mcp_bindings") or [])
    studio = config.get("studio") or {}
    for node in studio.get("nodes") or []:
        if not isinstance(node, dict):
            continue
        data = node.get("data") or {}
        ptype = str(data.get("paletteType") or "")
        if ptype.startswith("mcp"):
            bindings.append(
                {
                    "server_id": data.get("serverId") or data.get("server_id"),
                    "server": data.get("serverName") or data.get("server"),
                }
            )
    seen: set[tuple[Any, Any]] = set()
    for binding in bindings:
        if not isinstance(binding, dict):
            continue
        key = (binding.get("server_id"), binding.get("server") or binding.get("server_name"))
        if key in seen:
            continue
        seen.add(key)
        try:
            from src.models.mcp_server import MCPServer

            server = None
            if binding.get("server_id") is not None:
                server = MCPServer.get_by_id(int(binding["server_id"]))
            if server is None and (binding.get("server") or binding.get("server_name")):
                server = MCPServer.get_by_name(binding.get("server") or binding.get("server_name"))
            if server is None:
                errors.append(
                    {
                        "code": "mcp_down",
                        "message": f"MCP server not found: {binding.get('server') or binding.get('server_id')}.",
                    }
                )
        except Exception as exc:
            errors.append({"code": "mcp_down", "message": f"MCP lookup failed: {exc}"})


def _validate_skills(config: dict[str, Any], errors: list[dict[str, str]]) -> None:
    skill_ids = list(config.get("maf_skill_ids") or config.get("skill_ids") or [])
    for spec in config.get("participants") or []:
        if isinstance(spec, dict):
            skill_ids.extend(spec.get("maf_skill_ids") or [])
    for sid in skill_ids:
        if _skill_md_path(str(sid)) is None:
            errors.append({"code": "missing_skill_md", "message": f"SKILL.md not found for '{sid}'."})
    for raw in config.get("skill_paths") or []:
        path = Path(str(raw))
        if not (path / "SKILL.md").is_file() and not (path.name == "SKILL.md" and path.is_file()):
            errors.append({"code": "missing_skill_md", "message": f"SKILL.md not found at {raw}."})


def _skill_md_path(sid: str) -> Path | None:
    candidate = Path(sid)
    if candidate.is_dir() and (candidate / "SKILL.md").is_file():
        return candidate / "SKILL.md"
    if candidate.is_file() and candidate.name == "SKILL.md":
        return candidate
    user = user_skills_dir() / sid / "SKILL.md"
    if user.is_file():
        return user
    seeded = SKILLS_DIR / sid / "SKILL.md"
    if seeded.is_file():
        return seeded
    try:
        from src.agent_platform.catalog.skills_store import MafSkillStore

        row = MafSkillStore.get_by_name(sid)
        if row and row.get("path"):
            path = Path(row["path"])
            md = path / "SKILL.md" if path.is_dir() else path
            if md.is_file():
                return md
    except Exception:
        pass
    return None


def _validate_hitl(config: dict[str, Any], errors: list[dict[str, str]]) -> None:
    hitl = config.get("hitl")
    if hitl is None:
        return
    if not isinstance(hitl, dict):
        errors.append({"code": "bad_hitl", "message": "hitl must be an object."})
        return
    approval = hitl.get("approval")
    if approval is not None:
        if not isinstance(approval, list) or not all(isinstance(x, str) for x in approval):
            errors.append({"code": "bad_hitl_approval", "message": "hitl.approval must be an array of strings."})
    for spec in config.get("participants") or []:
        if isinstance(spec, dict) and spec.get("hitl") is not None:
            _validate_hitl(spec, errors)
